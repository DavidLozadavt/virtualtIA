"""Utilidades de trama y tasa de muestreo — base común del pipeline de audio.

El transporte entrega bytes PCM16 de tamaño arbitrario (mod_audio_stream manda
~20 ms, pero ni el tamaño ni la alineación están garantizados), mientras que
cada etapa neuronal exige una trama exacta (Silero VAD: 256 muestras a 8 kHz /
512 a 16 kHz; GTCRN: 256 muestras a 16 kHz). `FrameSlicer` desacopla ambos
mundos sin copiar más de lo necesario y sin perder muestras entre llamadas.

Todo el pipeline trabaja en `float32` normalizado a [-1, 1] — es el dominio que
esperan los modelos ONNX y evita saturaciones intermedias al encadenar etapas.
La conversión a PCM16 ocurre una sola vez, en la salida.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

PCM16_SCALE = 32768.0
PCM16_MAX = 32767
PCM16_MIN = -32768
BYTES_PER_SAMPLE = 2


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    """PCM16 little-endian → float32 en [-1, 1)."""
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    # Un número impar de bytes (trama partida por el transporte) se trunca a la
    # última muestra completa; el byte suelto se descartaría igual al decodificar.
    usable = len(pcm) - (len(pcm) % BYTES_PER_SAMPLE)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    raw = np.frombuffer(pcm, dtype="<i2", count=usable // BYTES_PER_SAMPLE)
    return (raw.astype(np.float32) / PCM16_SCALE).copy()


def float_to_pcm16(samples: np.ndarray) -> bytes:
    """float32 en [-1, 1] → PCM16 little-endian, con recorte duro."""
    if samples.size == 0:
        return b""
    scaled = np.clip(samples * PCM16_SCALE, PCM16_MIN, PCM16_MAX)
    return scaled.astype("<i2").tobytes()


def rms(samples: np.ndarray) -> float:
    """RMS lineal de una trama float32 (0.0 si está vacía)."""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def dbfs(samples: np.ndarray, floor_db: float = -120.0) -> float:
    """Nivel RMS en dBFS; `floor_db` para silencio absoluto."""
    value = rms(samples)
    if value <= 0.0:
        return floor_db
    return max(floor_db, 20.0 * float(np.log10(value)))


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Remuestrea con filtro polifásico (scipy) preservando la fase lineal.

    8 kHz ↔ 16 kHz es un factor entero 2, el caso barato de `resample_poly`.
    Sin scipy disponible se cae a interpolación lineal: peor respuesta en
    frecuencia, pero el pipeline nunca se detiene por una dependencia.
    """
    if src_rate == dst_rate or samples.size == 0:
        return samples
    try:
        from math import gcd

        from scipy.signal import resample_poly

        factor = gcd(src_rate, dst_rate)
        up = dst_rate // factor
        down = src_rate // factor
        return resample_poly(samples, up, down).astype(np.float32, copy=False)
    except ImportError:  # pragma: no cover - scipy es dependencia del proyecto
        target = int(round(samples.size * dst_rate / src_rate))
        if target <= 0:
            return np.zeros(0, dtype=np.float32)
        src_idx = np.linspace(0.0, samples.size - 1, target, dtype=np.float64)
        return np.interp(src_idx, np.arange(samples.size), samples).astype(np.float32)


@dataclass
class StreamResampler:
    """Remuestreador polifásico con estado — apto para flujo continuo.

    `resample_poly` sobre tramas independientes introduce discontinuidades en
    cada frontera (el filtro arranca de cero cada vez), y esos clics degradan
    tanto al modelo de supresión como al reconocedor. Aquí el estado del filtro
    FIR y la fase de decimación se conservan entre bloques, de modo que la
    salida es idéntica a remuestrear la señal completa de una vez.
    """

    src_rate: int
    dst_rate: int
    half_taps: int = 16  # semilongitud del FIR por fase (calidad vs. retardo)

    def __post_init__(self) -> None:
        from math import gcd

        factor = gcd(self.src_rate, self.dst_rate)
        self._up = self.dst_rate // factor
        self._down = self.src_rate // factor
        self._passthrough = self._up == 1 and self._down == 1
        self._taps = None
        self._zi = None
        self._phase = 0

    def _ensure_filter(self) -> None:
        if self._taps is not None or self._passthrough:
            return
        from scipy.signal import firwin

        # Filtro anti-imagen/anti-aliasing con la banda de paso del menor de los
        # dos ritmos; ganancia `_up` para compensar la inserción de ceros.
        max_rate = max(self._up, self._down)
        length = 2 * self.half_taps * max_rate + 1
        cutoff = 1.0 / max_rate
        self._taps = (firwin(length, cutoff, window=("kaiser", 5.0)) * self._up).astype(
            np.float64
        )
        self._zi = np.zeros(self._taps.size - 1, dtype=np.float64)

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self._passthrough or samples.size == 0:
            return samples
        from scipy.signal import lfilter

        self._ensure_filter()
        if self._up > 1:
            stuffed = np.zeros(samples.size * self._up, dtype=np.float64)
            stuffed[:: self._up] = samples
        else:
            stuffed = samples.astype(np.float64, copy=False)
        filtered, self._zi = lfilter(self._taps, 1.0, stuffed, zi=self._zi)
        if self._down > 1:
            out = filtered[self._phase :: self._down]
            consumed = filtered.size - self._phase
            self._phase = (-consumed) % self._down
        else:
            out = filtered
        return out.astype(np.float32, copy=False)

    def reset(self) -> None:
        if self._zi is not None:
            self._zi = np.zeros(self._zi.size, dtype=np.float64)
        self._phase = 0

    @property
    def passthrough(self) -> bool:
        return self._passthrough


@dataclass
class FrameSlicer:
    """Reagrupa un flujo continuo en tramas de exactamente `frame_size`.

    Las muestras que no completan una trama quedan retenidas hasta la llamada
    siguiente (no se descartan ni se rellenan con ceros: rellenar inventaría
    silencio dentro de una palabra y degradaría el reconocimiento).
    """

    frame_size: int
    _buffer: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32), init=False
    )

    def push(self, samples: np.ndarray) -> Iterator[np.ndarray]:
        """Acumula y emite todas las tramas completas disponibles."""
        if samples.size:
            self._buffer = (
                samples.astype(np.float32, copy=False)
                if self._buffer.size == 0
                else np.concatenate((self._buffer, samples))
            )
        size = self.frame_size
        total = self._buffer.size
        emitted = 0
        while total - emitted >= size:
            yield self._buffer[emitted : emitted + size]
            emitted += size
        self._buffer = (
            self._buffer[emitted:] if emitted else self._buffer
        )

    def flush(self) -> np.ndarray:
        """Devuelve el resto sin completar y vacía el buffer."""
        rest = self._buffer
        self._buffer = np.zeros(0, dtype=np.float32)
        return rest

    @property
    def pending(self) -> int:
        return int(self._buffer.size)

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
