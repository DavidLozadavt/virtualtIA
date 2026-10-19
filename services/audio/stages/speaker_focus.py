"""Foco en el hablante principal — rechazar voces de fondo sin enrolamiento.

Ni el mejor detector de voz resuelve el caso de la televisión encendida o de una
conversación a dos metros: **eso es voz humana** y cualquier VAD la acepta con
razón. Lo que las distingue de la persona que llama no es el timbre, es la
posición: quien habla al teléfono está a centímetros del micrófono y domina el
nivel; la televisión, los niños en la otra habitación y los compañeros de oficina
llegan sistemáticamente más abajo.

Esta etapa explota exactamente eso, con el método que la industria usa cuando no
puede enrolar la voz del usuario (imposible en una llamada entrante: nunca antes
se oyó a esa persona). Se mantiene una ventana deslizante de niveles, se toma un
percentil alto como nivel del hablante principal, y se marca como voz de fondo lo
que quede muchos dB por debajo. Es análisis de señal puro, cuesta microsegundos y
no añade latencia.

Detalle que decide si funciona o estorba: **el nivel se integra sobre una ventana
del orden de una sílaba** (~200 ms), no sobre tramas de 20 ms. El habla normal
tiene 15-20 dB de rango dinámico *dentro* de una misma palabra (una vocal tónica
frente a una fricativa final), así que comparar tramas sueltas contra el percentil
marcaría como "fondo" media conversación del propio usuario. Integrando por
sílaba, la comparación mide lo que se pretende medir: la distancia del hablante,
no el fonema.

La etapa **no modifica el audio**: solo publica `ctx.background_voice`. La
decisión de silenciar es de la puerta de voz, que es quien tiene el pre-roll y la
histéresis. Así el criterio se desactiva sin tocar el resto del pipeline, y una
futura sustitución por extracción de hablante objetivo (modelos TSE) o por
identificación con *embeddings* entra en el mismo punto.

Durante el arranque, y hasta que la ventana tenga muestras suficientes, nunca
marca fondo: equivocarse aquí cuesta las primeras palabras del usuario, que es el
error más caro del sistema.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from services.audio.frames import FrameSlicer
from services.audio.pipeline import BaseStage, FrameContext

_POWER_FLOOR = 1e-12


class SpeakerFocusStage(BaseStage):
    """Marca como fondo el habla muy por debajo del nivel del hablante dominante."""

    name = "speaker_focus"

    def __init__(
        self,
        *,
        rate: int,
        frame_ms: float,
        integration_ms: float,
        window_sec: float,
        percentile: float,
        margin_db: float,
        silence_db: float,
        min_frames: int,
    ):
        self.rate = int(rate)
        frame_size = max(1, int(rate * frame_ms / 1000.0))
        self._slicer = FrameSlicer(frame_size)
        self.percentile = float(percentile)
        self.margin_db = float(margin_db)
        self.silence_db = float(silence_db)
        self.min_frames = max(1, int(min_frames))
        integration_frames = max(1, round(integration_ms / max(frame_ms, 1e-6)))
        self._integration: deque[float] = deque(maxlen=integration_frames)
        capacity = max(self.min_frames, int(window_sec * 1000.0 / max(frame_ms, 1e-6)))
        self._levels: deque[float] = deque(maxlen=capacity)
        self.frames_background = 0
        self.last_baseline_db = 0.0

    def reset(self) -> None:
        self._slicer.reset()
        self._integration.clear()
        self._levels.clear()

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block
        for frame in self._slicer.push(block):
            self._analyze(frame, ctx)
        return block

    def _analyze(self, frame: np.ndarray, ctx: FrameContext) -> None:
        self._integration.append(
            float(np.mean(np.square(frame, dtype=np.float64))) + _POWER_FLOOR
        )
        # Nivel integrado sobre la ventana silábica, en dBFS.
        level = 10.0 * float(np.log10(sum(self._integration) / len(self._integration)))
        if level <= self.silence_db:
            return  # silencio: no informa del nivel del hablante ni es fondo
        self._levels.append(level)
        if len(self._levels) < self.min_frames:
            return
        baseline = float(
            np.percentile(
                np.fromiter(self._levels, dtype=np.float64, count=len(self._levels)),
                self.percentile,
            )
        )
        self.last_baseline_db = round(baseline, 1)
        if level < baseline - self.margin_db:
            ctx.background_voice = True
            self.frames_background += 1

    def stats(self) -> dict:
        return {
            "baseline_db": self.last_baseline_db,
            "frames_background": self.frames_background,
        }
