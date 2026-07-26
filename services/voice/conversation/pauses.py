"""Pause Manager — silencios naturales entre ideas.

Las pausas nunca son constantes: cada una se calcula con jitter sobre una base,
escalada por el estado conversacional y contrastada con la anterior para que
dos pausas seguidas jamás midan lo mismo. Se insertan ENTRE ideas, no solo al
final de las frases (de eso se encarga el planner al trocear el contenido).
"""

from __future__ import annotations

import random
from enum import Enum
from typing import Optional

from services.voice.conversation.memory import ConversationMemory


class PauseLength(str, Enum):
    MICRO = "micro"     # respiración entre dos partes de una misma idea
    SHORT = "short"     # entre ideas encadenadas
    MEDIUM = "medium"   # antes de un resultado
    LONG = "long"       # "estoy mirando" real, antes de responder algo pesado


# Bases en segundos. El techo total por respuesta lo acota el planner: la
# naturalidad no puede convertirse en latencia perceptible.
_BASE: dict[PauseLength, float] = {
    PauseLength.MICRO: 0.12,
    PauseLength.SHORT: 0.26,
    PauseLength.MEDIUM: 0.46,
    PauseLength.LONG: 0.80,
}

_JITTER = 0.32          # ±32 % sobre la base
_MIN_DELTA = 0.035      # diferencia mínima frente a la pausa anterior
_FLOOR = 0.06
_CEIL = 1.20


class PauseManager:
    """Calcula duraciones de pausa; nunca devuelve dos veces la misma."""

    def __init__(
        self,
        memory: ConversationMemory,
        rng: Optional[random.Random] = None,
    ):
        self._memory = memory
        self._rng = rng or random.Random()

    def duration(self, length: PauseLength, *, scale: float = 1.0) -> float:
        base = _BASE[length] * max(0.1, scale)
        for _ in range(6):
            value = base * (1.0 + self._rng.uniform(-_JITTER, _JITTER))
            value = round(min(_CEIL, max(_FLOOR, value)), 3)
            last = self._memory.last_pause
            if last is None or abs(value - last) >= _MIN_DELTA:
                self._memory.remember_pause(value)
                return value
        # Agotados los intentos (rango muy estrecho): se desplaza a mano para
        # no devolver nunca un silencio idéntico al anterior.
        last = self._memory.last_pause or base
        value = round(min(_CEIL, max(_FLOOR, last + _MIN_DELTA)), 3)
        self._memory.remember_pause(value)
        return value

    def between_ideas(self, text: str, *, scale: float = 1.0) -> float:
        """Pausa proporcional al peso de la idea que viene a continuación."""
        words = len((text or "").split())
        if words <= 3:
            length = PauseLength.MICRO
        elif words <= 8:
            length = PauseLength.SHORT
        else:
            length = PauseLength.MEDIUM
        return self.duration(length, scale=scale)
