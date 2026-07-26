"""Conversation Timing Engine — tiempos de reacción humanos.

Una persona no responde en el instante exacto en que el otro calla: hay una
fracción de segundo entre oír y hablar, y ese retardo cambia según lo que está
haciendo. Aquí se calcula ese tiempo, siempre variable y siempre corto.

Regla dura de rendimiento: el retardo se OMITE cuando el turno ya emitió audio
(narración de espera) — no se acumulan silencios, y ninguna respuesta se
retrasa dos veces.
"""

from __future__ import annotations

import random
from typing import Optional

from services.voice.conversation.states import StateProfile

# Techo absoluto de reacción. Por encima de esto el usuario percibe demora, no
# humanidad.
_MAX_REACTION = 0.40


class ConversationTimingEngine:
    """Cuánto tarda Lyra en empezar a hablar, y con qué variación."""

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random()
        self._last: Optional[float] = None

    def reaction_delay(
        self,
        profile: StateProfile,
        *,
        already_speaking: bool = False,
        user_turn: bool = True,
    ) -> float:
        """Retardo antes del primer sonido de la respuesta.

        `already_speaking` (una narración de espera ya sonó en este turno) o un
        turno que no responde al usuario ⇒ cero: el silencio ya se llenó.
        """
        if already_speaking or not user_turn:
            return 0.0
        lo, hi = profile.reaction_range
        if hi <= 0.0:
            return 0.0
        for _ in range(4):
            value = round(min(_MAX_REACTION, self._rng.uniform(lo, hi)), 3)
            if self._last is None or abs(value - self._last) >= 0.02:
                self._last = value
                return value
        self._last = round(min(_MAX_REACTION, hi), 3)
        return self._last

    def wait_check_interval(self, attempt: int) -> float:
        """Cada cuánto revisar si una operación larga sigue corriendo.

        Crece con los intentos para no encimar frases de espera; siempre con
        jitter, para que la cadencia no se vuelva un metrónomo.
        """
        base = 1.6 + 0.9 * max(0, attempt)
        return round(base * (1.0 + self._rng.uniform(-0.15, 0.15)), 3)
