"""Backchannel Manager — señales de escucha que nunca pisan al usuario.

Un "mmm", un "ajá" o un "listo" antes de responder es lo que hace que una
operadora suene como alguien que estaba escuchando. Pero en una llamada
telefónica cualquier sonido propio entra por el mismo canal que la voz del
usuario, así que la señal solo puede salir cuando emitirla no le quita nada al
reconocimiento.

La ventana segura es una sola: el canal de captura está cerrado — el turno del
usuario ya terminó y Lyra tiene la palabra — y no hay audio reproduciéndose.
Fuera de esa ventana la señal simplemente no se emite: jamás interrumpe, jamás
impide capturar al usuario. La frecuencia la deciden el Behavior Engine (que
amortigua rellenos consecutivos) y el Phrase Manager (que no repite).
"""

from __future__ import annotations

import random
from typing import Optional

from services.voice.conversation.memory import ConversationMemory


class BackchannelManager:
    """Decide si cabe una señal de escucha, y solo dentro de la ventana segura."""

    def __init__(
        self,
        memory: ConversationMemory,
        rng: Optional[random.Random] = None,
    ):
        self._memory = memory
        self._rng = rng or random.Random()
        # Estado del canal, publicado por el runtime. Arranca abierto: mientras
        # no se demuestre que Lyra tiene la palabra, no se emite nada.
        self.capture_open = True
        self.playing = False

    def capture_closed(self) -> None:
        self.capture_open = False

    def capture_reopened(self) -> None:
        self.capture_open = True

    def playback_started(self) -> None:
        self.playing = True

    def playback_finished(self) -> None:
        self.playing = False

    @property
    def is_safe(self) -> bool:
        """Ventana segura: nadie está siendo transcrito ni escuchando audio."""
        return not self.capture_open and not self.playing

    def should_emit(self, *, after_user_turn: bool, probability: float) -> bool:
        if not self.is_safe or not after_user_turn:
            return False
        if probability <= 0.0:
            return False
        if probability >= 1.0:
            return True
        return self._rng.random() < probability
