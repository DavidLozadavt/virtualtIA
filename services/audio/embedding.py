"""Huella de voz — banco de filtros mel al estilo Kaldi y extractor de embeddings.

Qué resuelve
------------
Distinguir *quién* habla, no *si* alguien habla. Un detector de voz acepta a la
persona que llama, al televisor y al compañero de oficina por igual, porque los
tres son voz humana. Un embedding de hablante proyecta un fragmento de audio a un
vector donde la distancia mide identidad: dos fragmentos de la misma persona caen
cerca aunque digan cosas distintas, y dos personas distintas caen lejos aunque
digan lo mismo. Esa es la única señal que separa al usuario del resto de voces sin
depender del volumen.

Modelo
------
`wespeaker-voxceleb-resnet34-LM` exportado a ONNX (26 MB, 256 dimensiones). Se
eligió tras medirlo contra las alternativas del zoo de sherpa-onnx sobre las
grabaciones de referencia de tres hablantes que publica el propio proyecto:

* ResNet34-LM: misma persona 0.69, personas distintas 0.18. **Separa.**
* `wespeaker_en_voxceleb_CAM++.onnx`: misma persona 0.50, distintas 0.42, con
  pares de personas distintas por encima de 0.85. Ese export **no discrimina**
  con las mismas características de entrada, así que se descartó pese a ser el
  más barato en papel.
* `nemo_en_titanet_small.onnx`: igual de indistinguible (0.34 frente a 0.31).

Banda telefónica
----------------
El modelo se entrenó a 16 kHz y aquí se le da audio de 8 kHz remuestreado. Medido
sobre las mismas grabaciones, el margen entre "misma persona" y "otra persona"
baja de 0.266 a 0.244: **la identidad sobrevive al canal telefónico casi intacta**
porque lo que la lleva vive por debajo de 3.4 kHz. Lo que sí importa mucho es la
duración de la ventana (medido, margen entre mínimo propio y máximo ajeno):

    ventana 1.0 s → −0.34   (inservible)
    ventana 1.5 s → −0.16   (dudoso)
    ventana 3.0 s → +0.25   (separación limpia)

Por eso la ventana de trabajo es de segundos, no de tramas, y la decisión rápida
trama a trama la toman otras evidencias (ver `stages/speaker_lock.py`).

El remuestreo debe ser de banda limitada (polifásico). Con interpolación simple
la banda 4-8 kHz se llena con una imagen especular de la voz, que es un desajuste
peor que dejarla vacía.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from services.audio.frames import resample
from services.audio.runtime_pool import get_session

logger = logging.getLogger("lyra.audio.embedding")

# Parámetros exactos del extractor de wespeaker (su `preprocessor_config.json`):
# ventana 25 ms, salto 10 ms, 80 bandas mel, ventana de Hamming, sin dither,
# muestras en escala entera de 16 bits y media restada por dimensión.
EMBED_RATE = 16000
FRAME_LENGTH_MS = 25.0
FRAME_SHIFT_MS = 10.0
NUM_MEL_BINS = 80
PREEMPHASIS = 0.97
PCM_SCALE = 32768.0
MEL_LOW_HZ = 20.0
LOG_FLOOR = 1.1920928955078125e-07  # FLT_EPSILON, el suelo que usa Kaldi
MIN_FRAMES = 9  # `min_num_frames` del extractor de referencia


def _mel(frequency: np.ndarray | float) -> np.ndarray | float:
    return 1127.0 * np.log(1.0 + np.asarray(frequency, dtype=np.float64) / 700.0)


def mel_filterbank(
    num_bins: int, rate: int, fft_size: int, low_hz: float = MEL_LOW_HZ
) -> np.ndarray:
    """Banco triangular de Kaldi: triángulos equiespaciados **en escala mel**.

    Dos detalles que no son cosméticos y que cambian el resultado si se omiten:
    los triángulos son lineales en mel (no en Hz), y la banda de Nyquist se
    descarta — Kaldi recorre `fft_size / 2` bandas, no `fft_size / 2 + 1`.
    """
    usable = fft_size // 2
    high_hz = 0.5 * rate
    low_mel, high_mel = float(_mel(low_hz)), float(_mel(high_hz))
    delta = (high_mel - low_mel) / (num_bins + 1)
    mels = _mel(np.arange(usable) * (rate / fft_size))

    bank = np.zeros((num_bins, fft_size // 2 + 1), dtype=np.float64)
    for index in range(num_bins):
        left = low_mel + index * delta
        center, right = left + delta, left + 2.0 * delta
        rising = (mels - left) / delta
        falling = (right - mels) / delta
        weights = np.where(mels <= center, rising, falling)
        bank[index, :usable] = np.where((mels > left) & (mels < right), weights, 0.0)
    return bank


def hamming_window(size: int) -> np.ndarray:
    return 0.54 - 0.46 * np.cos(2.0 * np.pi * np.arange(size) / (size - 1))


class FbankExtractor:
    """Log-mel al estilo Kaldi, en numpy, sin torch ni torchaudio."""

    def __init__(self, rate: int = EMBED_RATE, num_bins: int = NUM_MEL_BINS):
        self.rate = int(rate)
        self.frame_size = int(rate * FRAME_LENGTH_MS / 1000.0)
        self.hop_size = int(rate * FRAME_SHIFT_MS / 1000.0)
        self.fft_size = 1
        while self.fft_size < self.frame_size:
            self.fft_size <<= 1
        self._window = hamming_window(self.frame_size)
        self._bank = mel_filterbank(num_bins, self.rate, self.fft_size).T

    def __call__(self, samples: np.ndarray) -> Optional[np.ndarray]:
        """Devuelve (tramas, 80) o None si el fragmento es demasiado corto."""
        if samples.size < self.frame_size:
            return None
        scaled = samples.astype(np.float64) * PCM_SCALE
        frames = np.lib.stride_tricks.sliding_window_view(scaled, self.frame_size)[
            :: self.hop_size
        ]
        if frames.shape[0] < MIN_FRAMES:
            return None
        frames = frames - frames.mean(axis=1, keepdims=True)  # quitar continua
        emphasised = np.empty_like(frames)
        emphasised[:, 0] = frames[:, 0] * (1.0 - PREEMPHASIS)
        emphasised[:, 1:] = frames[:, 1:] - PREEMPHASIS * frames[:, :-1]
        emphasised *= self._window
        power = np.abs(np.fft.rfft(emphasised, n=self.fft_size, axis=1)) ** 2
        features = np.log(np.maximum(power @ self._bank, LOG_FLOOR)).astype(np.float32)
        # Media por dimensión restada sobre el fragmento: cancela la respuesta del
        # canal (micrófono, códec, línea), que si no domina sobre la identidad.
        return features - features.mean(axis=0, keepdims=True)


class SpeakerEmbedder:
    """Fragmento de audio → vector unitario de identidad de hablante."""

    def __init__(self, model_path: str, *, rate: int = 8000, threads: int = 1):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"modelo de embeddings no encontrado: {path}")
        self._session = get_session(path, threads)
        self._input = self._session.get_inputs()[0].name
        self.rate = int(rate)
        self._fbank = FbankExtractor()
        self.dimension = int(self._session.get_outputs()[0].shape[-1] or 0)

    def __call__(self, samples: np.ndarray) -> Optional[np.ndarray]:
        """Embedding unitario, o None si el fragmento no da para calcularlo."""
        if samples.size == 0:
            return None
        wide = (
            resample(samples.astype(np.float32, copy=False), self.rate, EMBED_RATE)
            if self.rate != EMBED_RATE
            else samples.astype(np.float32, copy=False)
        )
        features = self._fbank(wide)
        if features is None:
            return None
        output = self._session.run(None, {self._input: features[None, :, :]})[0]
        vector = np.asarray(output, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-9:
            return None
        return vector / norm


def cosine(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    """Similitud coseno entre vectores ya unitarios (0.0 si falta alguno)."""
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))
