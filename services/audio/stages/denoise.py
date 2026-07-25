"""Supresión de ruido neuronal — la etapa que decide la calidad final.

Modelo elegido: **DPDFNet** (Ceva, Apache-2.0). Razones técnicas, no de
popularidad:

* **Modelo nativo de 8 kHz** (`dpdfnet2_8khz`, `dpdfnet8_8khz`). Es la diferencia
  decisiva para telefonía: el resto de la familia moderna (DeepFilterNet,
  GTCRN, FastEnhancer) solo existe a 16 o 48 kHz, así que obliga a remuestrear
  8 kHz → 48 kHz → 8 kHz y a que el modelo trabaje sobre una banda que nunca vio
  en entrenamiento — más latencia, más CPU y peor resultado.
* Es el sucesor mantenido de DeepFilterNet2 (le añade bloques *dual-path* en el
  encoder). DeepFilterNet está abandonado desde 2024, solo a 48 kHz y sin API de
  streaming oficial en Python.
* Inferencia **solo CPU sobre ONNX Runtime**, causal, con estado RNN conservado
  entre llamadas: ~20 ms hasta la primera salida y ~10 ms por bloque.
* Entrenado con una pérdida explícita contra la sobre-atenuación, que es
  precisamente el fallo que arruina el reconocimiento.

Sobre-atenuación y reconocimiento
---------------------------------
La evidencia publicada en 2025-2026 es contundente en un punto: un supresor
optimizado para que el audio *suene* limpio suele **empeorar** la transcripción,
porque al borrar lo que considera ruido borra también fricativas y consonantes
sordas (la "s" de los plurales, la diferencia entre "quince" y "trece"). El
mecanismo medido es el artefacto de supresión, no el ruido residual.

Por eso la supresión aquí está **acotada**: se mezcla la señal limpia con la
original según un límite de atenuación en dB (`attn_limit_db`), exactamente la
semántica que DPDFNet aplica en su modo offline y que su API de streaming no
expone. `alpha = 10^(-límite/20)`; la salida es `alpha·original + (1-alpha)·limpia`.
Un límite de 0 dB deja pasar la señal original (sin supresión), y un límite alto
deja actuar al modelo sin freno. El valor por defecto es deliberadamente
conservador: elimina el ruido de fondo sin poder borrar un fonema completo.

La mezcla se hace muestra a muestra sobre la señal original retardada la misma
cantidad que el modelo, no sobre una aproximación: la mezcla lineal en el
espectro y en el tiempo son equivalentes porque la STFT es lineal.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

from services.audio.dsp import EPSILON, SpectralStream, smooth, vorbis_window
from services.audio.pipeline import BaseStage, FrameContext

logger = logging.getLogger("lyra.audio.denoise")


class SpeechEnhancer(Protocol):
    """Supresor de ruido intercambiable, con estado por llamada."""

    def enhance(self, block: np.ndarray) -> np.ndarray:
        """Procesa audio y devuelve el limpio disponible (puede ser más corto)."""

    def reset(self) -> None:
        """Olvida el estado recurrente."""


_DELAY_CACHE: dict[tuple[str, int], int] = {}


def _measure_output_delay(enhancer: "OnnxStreamEnhancer", key: str, rate: int) -> int:
    """Retardo entrada→salida real del modelo, medido con una señal de sondeo.

    No basta con `win_len - hop`: estos modelos aplican un filtro profundo sobre
    varias tramas, así que su salida llega decenas de milisegundos después de la
    entrada correspondiente (con DPDFNet 8 kHz son ~40 ms, no 10 ms). Ese número
    hace falta con exactitud para mezclar la señal original con la limpia sin
    crear un pre-eco.

    Se mide una sola vez por modelo y proceso (una llamada no paga la medición
    del resto). Un barrido de frecuencia es la señal ideal: su autocorrelación
    tiene un pico único, así que el desplazamiento se resuelve sin ambigüedad.
    """
    cached = _DELAY_CACHE.get((key, rate))
    if cached is not None:
        return cached
    length = rate // 2
    time_axis = np.arange(length, dtype=np.float64) / rate
    sweep_end = min(rate * 0.4, 3200.0)
    probe = (
        0.4
        * np.sin(
            2.0 * np.pi * (200.0 + (sweep_end - 200.0) * time_axis / (length / rate))
            * time_axis
        )
    ).astype(np.float32)
    produced = [
        enhancer.enhance(probe[start : start + enhancer.hop_size])
        for start in range(0, length, enhancer.hop_size)
    ]
    enhancer.reset()
    output = np.concatenate([chunk for chunk in produced if chunk.size]) if produced else np.zeros(0, dtype=np.float32)
    best_lag, best_score = enhancer.latency_samples, -2.0
    limit = min(output.size, rate // 4)
    for lag in range(0, limit):
        usable = min(output.size - lag, probe.size)
        if usable < rate // 8:
            break
        score = float(
            np.dot(probe[:usable], output[lag : lag + usable])
            / (
                np.linalg.norm(probe[:usable]) * np.linalg.norm(output[lag : lag + usable])
                + EPSILON
            )
        )
        if score > best_score:
            best_lag, best_score = lag, score
    if best_score < 0.1:
        # Sondeo poco concluyente (modelo muy agresivo con tonos puros): se usa
        # el retardo estructural, que al menos no desalinea en el sentido opuesto.
        best_lag = enhancer.latency_samples
    logger.info(
        "[audio] retardo del supresor medido: %d muestras (%.1f ms, correlación %.2f)",
        best_lag,
        best_lag / rate * 1000.0,
        best_score,
    )
    _DELAY_CACHE[(key, rate)] = best_lag
    return best_lag


class OnnxStreamEnhancer:
    """Supresor causal en streaming sobre un ONNX de espectro + estado recurrente.

    Contrato del modelo (el de DPDFNet, y el que debería cumplir cualquier
    reemplazo): entradas `(1, 1, bins, 2)` con la parte real e imaginaria del
    espectro de una trama, y un vector de estado plano que se realimenta; salidas
    del mismo par. El estado inicial no es cero: viene en los metadatos del propio
    ONNX (normalizadores ERB y de espectro), y arrancar en cero degrada las
    primeras décimas de segundo — justo la primera palabra.

    Se ejecuta directamente sobre ONNX Runtime, sin la librería de referencia del
    modelo: esa arrastra `librosa`, `numba`, `llvmlite` y `scikit-learn`, que en un
    servicio de telefonía son ~200 MB de dependencias compiladas para reimplementar
    treinta líneas de STFT. Aquí solo se requieren `onnxruntime` y `numpy`.

    La STFT es causal (sin relleno reflejado, ventana Vorbis que cumple
    Princen-Bradley al 50 % de solape), así que la latencia estructural es una
    trama menos un salto: 10 ms con el modelo de 8 kHz.
    """

    def __init__(
        self,
        model_path: str,
        rate: int,
        threads: int = 1,
        output_delay_samples: Optional[int] = None,
    ):
        import onnxruntime

        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"modelo de supresión no encontrado: {path}")

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = threads
        options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        self._session = onnxruntime.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs = self._session.get_inputs()
        outputs = self._session.get_outputs()
        if len(inputs) < 2 or len(outputs) < 2:
            raise ValueError(
                f"{path.name} no tiene la firma de streaming esperada "
                "(spec, state) → (spec, state)"
            )
        self._in_spec, self._in_state = inputs[0].name, inputs[1].name
        self._out_spec, self._out_state = outputs[0].name, outputs[1].name

        bins = inputs[0].shape[-2]
        if not isinstance(bins, int) or bins < 2:
            raise ValueError(f"{path.name} no declara el número de bandas")
        self.win_len = (bins - 1) * 2
        self.hop_size = self.win_len // 2
        self.rate = int(rate)
        expected_ms = self.win_len / self.rate * 1000.0
        if not 10.0 <= expected_ms <= 40.0:
            raise ValueError(
                f"{path.name} espera tramas de {self.win_len} muestras: a {self.rate} Hz "
                f"serían {expected_ms:.1f} ms. Usa el modelo de la tasa correcta "
                "o ajusta AUDIO_DENOISE_RATE."
            )
        self._window = vorbis_window(self.win_len)
        self._init_state = self._initial_state()
        self.reset()
        self.output_delay_samples = (
            int(output_delay_samples)
            if output_delay_samples is not None and output_delay_samples >= 0
            else _measure_output_delay(self, str(path), self.rate)
        )

    def _initial_state(self) -> np.ndarray:
        """Estado inicial declarado en los metadatos del ONNX."""
        meta = self._session.get_modelmeta().custom_metadata_map or {}
        size_declared = self._session.get_inputs()[1].shape[0]
        size = int(meta.get("state_size") or (size_declared if isinstance(size_declared, int) else 0))
        if size <= 0:
            raise ValueError("el ONNX no declara `state_size` ni una forma de estado fija")
        state = np.zeros(size, dtype=np.float32)
        erb_size = int(meta.get("erb_norm_state_size") or 0)
        spec_size = int(meta.get("spec_norm_state_size") or 0)
        erb_init = meta.get("erb_norm_init")
        spec_init = meta.get("spec_norm_init")
        if erb_size and erb_init:
            state[:erb_size] = np.array(
                [float(x) for x in erb_init.split(",")], dtype=np.float32
            )
        if spec_size and spec_init:
            state[erb_size : erb_size + spec_size] = np.array(
                [float(x) for x in spec_init.split(",")], dtype=np.float32
            )
        return np.ascontiguousarray(state)

    @property
    def latency_samples(self) -> int:
        return self.win_len - self.hop_size

    def reset(self) -> None:
        self._state = self._init_state.copy()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._ola = np.zeros(self.win_len, dtype=np.float32)

    def enhance(self, block: np.ndarray) -> np.ndarray:
        if block.size == 0:
            return np.zeros(0, dtype=np.float32)
        self._buffer = np.concatenate(
            (self._buffer, block.astype(np.float32, copy=False))
        )
        produced: list[np.ndarray] = []
        while self._buffer.size >= self.win_len:
            spectrum = np.fft.rfft(self._buffer[: self.win_len] * self._window)
            spec_in = np.stack(
                (
                    spectrum.real.astype(np.float32),
                    spectrum.imag.astype(np.float32),
                ),
                axis=-1,
            )[np.newaxis, np.newaxis, :, :]
            spec_out, self._state = self._session.run(
                [self._out_spec, self._out_state],
                {self._in_spec: spec_in, self._in_state: self._state},
            )
            pair = spec_out[0, 0]
            frame = np.fft.irfft(pair[:, 0] + 1j * pair[:, 1], n=self.win_len)
            self._ola += (frame * self._window).astype(np.float32)
            produced.append(self._ola[: self.hop_size].copy())
            self._ola = np.roll(self._ola, -self.hop_size)
            self._ola[-self.hop_size :] = 0.0
            self._buffer = self._buffer[self.hop_size :]
        return (
            np.concatenate(produced) if produced else np.zeros(0, dtype=np.float32)
        )


class SpectralGateEnhancer:
    """Respaldo sin modelo: sustracción espectral con piso de ruido por mínimos.

    Existe únicamente para que la ausencia del modelo neuronal degrade el
    sistema en vez de romperlo. Un método puramente espectral genera artefactos
    que perjudican al reconocedor, así que se aplica con mano muy ligera y su uso
    se registra como advertencia.
    """

    def __init__(
        self,
        *,
        rate: int,
        frame_size: int,
        hop_size: int,
        over_subtraction: float = 1.4,
        floor: float = 0.25,
        noise_adapt: float = 0.95,
    ):
        self.rate = int(rate)
        self._stream = SpectralStream(frame_size, hop_size, channels=1)
        self._bins = frame_size // 2 + 1
        self._over = float(over_subtraction)
        self._floor = float(floor)
        self._adapt = float(noise_adapt)
        self._noise: Optional[np.ndarray] = None
        self._gain_state: Optional[np.ndarray] = None

    def enhance(self, block: np.ndarray) -> np.ndarray:
        return self._stream.process([block], self._filter_frame)

    def _filter_frame(self, spectra: list[np.ndarray]) -> np.ndarray:
        spectrum = spectra[0]
        power = np.abs(spectrum) ** 2
        if self._noise is None:
            self._noise = power.copy()
        # Seguimiento de mínimos: el piso baja rápido y sube despacio, así el
        # ruido estacionario se aprende y la voz no lo arrastra hacia arriba.
        rising = power > self._noise
        self._noise = np.where(
            rising,
            self._adapt * self._noise + (1.0 - self._adapt) * power,
            0.5 * self._noise + 0.5 * power,
        )
        gain = (power - self._over * self._noise) / (power + EPSILON)
        gain = np.clip(gain, self._floor, 1.0)
        self._gain_state = smooth(self._gain_state, gain.astype(np.float32), 0.5)
        return spectrum * self._gain_state

    def reset(self) -> None:
        self._stream.reset()
        self._noise = None
        self._gain_state = None


class DenoiseStage(BaseStage):
    """Aplica el supresor con límite de atenuación para no dañar el habla."""

    name = "denoise"

    def __init__(
        self,
        enhancer: SpeechEnhancer,
        *,
        rate: int,
        attn_limit_db: Optional[float],
        bypass_on_speech_absent: bool = False,
    ):
        self.rate = int(rate)
        self.enhancer = enhancer
        self.attn_limit_db = attn_limit_db
        self._alpha = (
            0.0
            if attn_limit_db is None
            else float(10.0 ** (-float(attn_limit_db) / 20.0))
        )
        self.bypass_on_speech_absent = bool(bypass_on_speech_absent)
        # Retardo propio del supresor: la señal original se retiene otro tanto
        # para que la mezcla quede muestra a muestra en fase. Sin esto la mezcla
        # sumaría una copia adelantada 40 ms — un pre-eco audible y perjudicial.
        self._delay = int(getattr(enhancer, "output_delay_samples", 0) or 0)
        self._dry = np.zeros(0, dtype=np.float32)
        self.samples_processed = 0
        self.reset()

    @property
    def latency_ms(self) -> float:
        return round(self._delay / float(self.rate) * 1000.0, 1)

    def reset(self) -> None:
        self.enhancer.reset()
        # El retardo del modelo se representa como silencio previo en la señal
        # original: así el primer bloque limpio se mezcla con lo que le toca.
        self._dry = np.zeros(self._delay, dtype=np.float32) if self._alpha > 0.0 else np.zeros(0, dtype=np.float32)

    def process(self, block: np.ndarray, _ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block
        wet = self.enhancer.enhance(block)
        if self._alpha <= 0.0:
            self.samples_processed += int(wet.size)
            return wet
        # La original se retiene y se consume en la misma cantidad que produce el
        # modelo: así la mezcla queda alineada muestra a muestra sin suposiciones
        # sobre el retardo interno del supresor.
        self._dry = np.concatenate((self._dry, block))
        # Con el silencio inicial ya en la cola, consumir tantas muestras como
        # produjo el modelo entrega exactamente la original que le corresponde.
        take = min(wet.size, self._dry.size)
        if take == 0:
            return np.zeros(0, dtype=np.float32)
        dry = self._dry[:take]
        self._dry = self._dry[take:]
        self.samples_processed += take
        return (self._alpha * dry + (1.0 - self._alpha) * wet[:take]).astype(
            np.float32, copy=False
        )
