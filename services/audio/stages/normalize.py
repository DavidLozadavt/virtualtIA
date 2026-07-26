"""Normalización de nivel — llegada consistente al reconocedor.

Un reconocedor no falla por volumen bajo en sí, falla por *variación*: la misma
palabra dicha lejos del micrófono y cerca produce transcripciones distintas. Esta
etapa lleva el habla a un nivel objetivo estable.

Dos decisiones deliberadas:

* **La ganancia se adapta solo sobre habla confirmada** (`ctx.speech_active`, que
  publica la puerta de voz). Adaptar durante el silencio amplificaría el ruido de
  fondo hasta el nivel objetivo, que es justo lo contrario de lo que busca el
  pipeline.
* **Adaptación lenta y ganancia acotada.** La red del operador ya aplica su
  propio control de ganancia; un segundo control rápido encima produce bombeo
  (la ganancia persigue a la del operador) y ese bombeo sí degrada la
  transcripción. El techo de ganancia evita además que un silencio mal clasificado
  amplifique ruido.

El limitador de salida es suave (tangente hiperbólica sobre el umbral): recortar
en duro genera armónicos que el reconocedor confunde con fricativas.
"""

from __future__ import annotations

import numpy as np

from services.audio.frames import rms
from services.audio.pipeline import BaseStage, FrameContext


class NormalizeStage(BaseStage):
    """Control de ganancia lento con objetivo en dBFS y limitador suave."""

    name = "normalize"

    def __init__(
        self,
        *,
        rate: int,
        target_dbfs: float,
        max_gain_db: float,
        min_gain_db: float,
        attack: float,
        release: float,
        limit: float,
        speech_only: bool = True,
        silence_dbfs: float = -55.0,
    ):
        self.rate = int(rate)
        self.speech_only = bool(speech_only)
        self.silence_level = float(10.0 ** (float(silence_dbfs) / 20.0))
        self.target = float(10.0 ** (float(target_dbfs) / 20.0))
        self.max_gain = float(10.0 ** (float(max_gain_db) / 20.0))
        self.min_gain = float(10.0 ** (float(min_gain_db) / 20.0))
        self.attack = float(attack)
        self.release = float(release)
        self.limit = float(limit)
        self._gain = 1.0

    def reset(self) -> None:
        self._gain = 1.0

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block
        level = rms(block)
        # Con `speech_only` la ganancia solo persigue al habla confirmada por la
        # puerta. Sin puerta en la cadena (o con el criterio desactivado) basta
        # con estar por encima del silencio, para que la etapa siga siendo útil
        # de forma independiente.
        adapt = ctx.speech_active if self.speech_only else level > self.silence_level
        # Una voz ajena ya atenuada no debe marcar el objetivo de ganancia: si lo
        # hiciera, esta etapa la subiría de vuelta al nivel objetivo y desharía
        # exactamente el trabajo del anclaje de hablante. Es el mismo argumento
        # que impide adaptar en silencio, aplicado a la voz que no es del usuario.
        if ctx.background_voice:
            adapt = False
        if adapt and level > 0.0:
            desired = float(np.clip(self.target / level, self.min_gain, self.max_gain))
            # Bajar la ganancia es más urgente que subirla: una saturación daña
            # más que un par de tramas algo bajas.
            coefficient = self.attack if desired < self._gain else self.release
            self._gain = coefficient * self._gain + (1.0 - coefficient) * desired
        out = block * self._gain
        peak = float(np.max(np.abs(out))) if out.size else 0.0
        if peak > self.limit:
            excess = np.abs(out) > self.limit
            headroom = max(1e-6, 1.0 - self.limit)
            out[excess] = np.sign(out[excess]) * (
                self.limit + np.tanh((np.abs(out[excess]) - self.limit) / headroom) * headroom
            )
        return out.astype(np.float32, copy=False)

    def stats(self) -> dict:
        return {"gain_db": round(20.0 * float(np.log10(max(self._gain, 1e-6))), 1)}
