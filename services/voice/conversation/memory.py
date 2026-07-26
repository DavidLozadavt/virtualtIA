"""Conversation Memory — memoria de expresiones de la llamada en curso.

Registra qué formulaciones ya se usaron para que la conversación no se repita:
ninguna frase reciente vuelve a salir, ninguna construcción sintáctica se usa
dos veces seguidas y nunca hay dos expresiones de relleno consecutivas.

Es memoria conversacional, no memoria de negocio: aquí no se guardan
direcciones, barrios ni estado del servicio.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional

# Cuántas formulaciones recientes se bloquean por categoría. Se acota además al
# tamaño real del banco para no dejar una categoría sin opciones disponibles.
_RECENT_WINDOW = 4
_FORM_WINDOW = 2


class ConversationMemory:
    """Qué dijo Lyra hace poco, por categoría y por forma sintáctica."""

    def __init__(self) -> None:
        self._recent: dict[str, Deque[str]] = {}
        self._forms: dict[str, Deque[str]] = {}
        self._turns = 0
        self._filler_last_turn = False
        self._filler_streak = 0
        self._last_pause: Optional[float] = None

    # ── frases ──

    def _bucket(self, store: dict[str, Deque[str]], category: str, window: int) -> Deque[str]:
        bucket = store.get(category)
        if bucket is None:
            bucket = deque(maxlen=window)
            store[category] = bucket
        return bucket

    def remember_phrase(self, category: str, key: str, form: str = "") -> None:
        self._bucket(self._recent, category, _RECENT_WINDOW).append(key)
        if form:
            self._bucket(self._forms, category, _FORM_WINDOW).append(form)

    def recent_phrases(self, category: str) -> tuple[str, ...]:
        return tuple(self._recent.get(category, ()))

    def recent_forms(self, category: str) -> tuple[str, ...]:
        return tuple(self._forms.get(category, ()))

    def is_recent(self, category: str, key: str) -> bool:
        return key in self._recent.get(category, ())

    def is_recent_form(self, category: str, form: str) -> bool:
        return bool(form) and form in self._forms.get(category, ())

    # ── rellenos (fillers) ──

    @property
    def filler_last_turn(self) -> bool:
        return self._filler_last_turn

    @property
    def filler_streak(self) -> int:
        return self._filler_streak

    def note_turn(self, *, used_filler: bool) -> None:
        """Cierra el turno: deja constancia de si hubo relleno.

        Dos turnos seguidos con relleno ya suenan a muletilla; el Behavior
        Engine consulta esto para bajar (o anular) la probabilidad.
        """
        self._turns += 1
        if used_filler:
            self._filler_streak += 1
        else:
            self._filler_streak = 0
        self._filler_last_turn = used_filler

    @property
    def turns(self) -> int:
        return self._turns

    # ── pausas ──

    @property
    def last_pause(self) -> Optional[float]:
        return self._last_pause

    def remember_pause(self, duration: float) -> None:
        self._last_pause = duration
