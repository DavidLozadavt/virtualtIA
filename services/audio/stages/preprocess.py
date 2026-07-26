"""Preacondicionamiento — lo que debe ocurrir antes de cualquier modelo.

Objetivo: entregar a las etapas siguientes una señal sin componentes que las
degraden aunque sean inaudibles. Tres problemas concretos del audio telefónico:

1. **Offset DC y retumbe (<80 Hz)**: viento sobre el micrófono, manipulación del
   teléfono y ruido de motor concentran energía por debajo de la banda de voz.
   No aportan inteligibilidad (la banda telefónica útil arranca ~300 Hz) pero sí
   inflan el RMS, lo que descalibra el VAD por energía, el control de ganancia y
   la adaptación del cancelador de eco.
2. **Zumbido de red (50/60 Hz y armónicos bajos)**: mismo efecto, y en el 8 kHz
   telefónico cae dentro de la zona que el filtro paso-alto ya elimina.
3. **Impulsos** (clics de red, paquetes corruptos): un pico aislado a fondo de
   escala hace que el modelo neuronal reaccione a un evento que no es voz y
   dispara falsos positivos de actividad.

Se implementa con biquads IIR con estado (continuidad entre bloques) y un
limitador suave de picos. Es DSP clásico a propósito: aquí no hay nada que un
modelo neuronal haga mejor, y todo lo que se limpia aquí mejora el rendimiento
de las etapas que sí son neuronales.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from services.audio.pipeline import BaseStage, FrameContext


class PreprocessStage(BaseStage):
    """Paso-alto + bloqueo DC + limitador de impulsos, con estado continuo."""

    name = "preprocess"

    def __init__(
        self,
        *,
        rate: int,
        highpass_hz: float,
        peak_limit: float,
        order: int = 2,
    ):
        self.rate = rate
        self.highpass_hz = float(highpass_hz)
        self.peak_limit = float(peak_limit)
        self._order = int(order)
        self._sos: Optional[np.ndarray] = None
        self._zi: Optional[np.ndarray] = None

    def _ensure_filter(self) -> None:
        if self._sos is not None:
            return
        from scipy.signal import butter, sosfilt_zi

        nyquist = self.rate / 2.0
        cutoff = min(max(self.highpass_hz, 1.0), nyquist * 0.9) / nyquist
        self._sos = butter(self._order, cutoff, btype="highpass", output="sos")
        self._zi = sosfilt_zi(self._sos) * 0.0

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block
        from scipy.signal import sosfilt

        self._ensure_filter()
        out, self._zi = sosfilt(self._sos, block.astype(np.float64), zi=self._zi)
        out = out.astype(np.float32, copy=False)

        if self.peak_limit > 0.0:
            # Limitador suave: comprime solo lo que excede el umbral, sin recortar
            # en duro (el recorte duro genera armónicos que el modelo interpreta
            # como fricativas).
            excess = np.abs(out) > self.peak_limit
            if bool(excess.any()):
                sign = np.sign(out[excess])
                magnitude = np.abs(out[excess])
                compressed = self.peak_limit + np.tanh(
                    (magnitude - self.peak_limit) / max(1e-6, 1.0 - self.peak_limit)
                ) * (1.0 - self.peak_limit)
                out[excess] = sign * compressed
                ctx.notes["peaks_limited"] = int(excess.sum())
        return out

    def reset(self) -> None:
        if self._zi is not None:
            self._zi = self._zi * 0.0
