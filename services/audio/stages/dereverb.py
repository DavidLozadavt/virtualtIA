"""Dereverberación — quitar la cola de sala, no el ataque de la palabra.

Con el teléfono en altavoz o sobre una mesa, cada sílaba llega dos veces: el
sonido directo y su reflejo tardío en paredes y techo. Esa cola tardía se
solapa con la sílaba siguiente y es una de las causas más frecuentes de que un
reconocedor confunda finales de palabra ("quince"/"trece", plurales que pierden
la s).

Método: supresión estadística de reverberación tardía (Lebart) en el dominio
STFT. La energía de la cola tardía en la trama actual se modela como una versión
atenuada de la energía de tramas anteriores, con una atenuación derivada del
tiempo de reverberación asumido (T60). Restarla con una ganancia tipo Wiener
elimina la cola sin tocar el sonido directo, que es lo que lleva la información.

Es causal, cuesta una FFT por trama y no necesita estimar la respuesta impulsiva
de la sala. La alternativa moderna (WPE en línea, o un modelo neuronal de
dereverberación) requiere invertir matrices por banda o un segundo modelo: no se
justifica cuando la banda telefónica ya recorta todo por encima de 3.4 kHz, que
es donde la reverberación es más audible. La etapa queda aislada para poder
sustituir el método sin tocar el resto del pipeline.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from services.audio.dsp import EPSILON, SpectralStream, smooth
from services.audio.pipeline import BaseStage, FrameContext


class DereverbStage(BaseStage):
    """Supresión de reverberación tardía por decaimiento exponencial estimado."""

    name = "dereverb"

    def __init__(
        self,
        *,
        rate: int,
        frame_size: int,
        hop_size: int,
        rt60_sec: float,
        direct_frames: int,
        strength: float,
        floor: float,
    ):
        self.rate = int(rate)
        self._stream = SpectralStream(frame_size, hop_size, channels=1)
        self._bins = frame_size // 2 + 1
        self.strength = float(strength)
        self.floor = float(floor)
        self._direct_frames = max(1, int(direct_frames))
        hop_sec = hop_size / float(rate)
        # Decaimiento de energía por trama para el T60 supuesto: la energía cae
        # 60 dB en rt60 segundos, es decir 10^(-6 * hop/rt60) por salto.
        self._decay = float(10.0 ** (-6.0 * hop_sec / max(rt60_sec, 1e-3)))
        self._late_power: Optional[np.ndarray] = None
        self._history: list[np.ndarray] = []
        self._gain_state: Optional[np.ndarray] = None

    @property
    def latency_ms(self) -> float:
        return round(self._stream.latency_ms(self.rate), 1)

    def reset(self) -> None:
        self._stream.reset()
        self._late_power = None
        self._history = []
        self._gain_state = None

    def process(self, block: np.ndarray, _ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block
        return self._stream.process([block], self._filter_frame)

    def _filter_frame(self, spectra: list[np.ndarray]) -> np.ndarray:
        spectrum = spectra[0]
        power = np.abs(spectrum) ** 2

        self._history.append(power)
        if len(self._history) > self._direct_frames:
            self._history.pop(0)

        if len(self._history) < self._direct_frames or self._late_power is None:
            self._late_power = np.zeros(self._bins, dtype=np.float64)
            late = self._late_power
        else:
            # La cola tardía de esta trama proviene de la energía de la trama
            # directa anterior, atenuada por el decaimiento de la sala.
            delayed = self._history[0]
            self._late_power = self._decay * (self._late_power + delayed)
            late = self._late_power

        gain = (power - self.strength * late) / (power + EPSILON)
        gain = np.clip(gain, self.floor, 1.0)
        self._gain_state = smooth(self._gain_state, gain.astype(np.float32), 0.5)
        return spectrum * self._gain_state
