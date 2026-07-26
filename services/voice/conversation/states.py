"""Conversation State Manager — estados conversacionales de Lyra.

Estos estados NO son los del FSM de negocio (waiting_origin, confirming_origin,
…): describen qué está haciendo la *operadora* en este instante — escuchar,
entender, procesar, buscar, confirmar, esperar al usuario, cerrar.

Cada estado tiene un comportamiento propio: ritmo, escala de pausas, cuánto
tarda en reaccionar, cuántas expresiones de transición se permite y si el
sonido contextual tiene sentido. No es solo un catálogo de frases distintas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from services.voice.conversation.plan import SpeechIntent

logger = logging.getLogger("lyra.voice.conversation.states")


class ConversationState(str, Enum):
    LISTENING = "listening"          # el usuario tiene la palabra
    UNDERSTANDING = "understanding"  # llegó el turno, se interpreta
    PROCESSING = "processing"        # se ejecuta una operación interna
    SEARCHING = "searching"          # se consulta/resuelve una ubicación
    CONFIRMING = "confirming"        # se devuelve un resultado a validar
    WAITING_USER = "waiting_user"    # se hizo una pregunta y se espera
    CLOSING = "closing"              # cierre de la llamada


@dataclass(frozen=True)
class StateProfile:
    """Ritmo y licencias expresivas de un estado.

    `reaction_range` es el tiempo humano de reacción antes de empezar a hablar;
    `pause_scale` estira o comprime TODAS las pausas del estado; las
    probabilidades gobiernan cuánta expresividad se permite. Los rangos son
    cortos a propósito: la naturalidad no puede costar latencia perceptible.
    """

    state: ConversationState
    reaction_range: tuple[float, float]
    pause_scale: float
    ack_probability: float
    transition_probability: float
    narration_probability: float
    ambient_allowed: bool
    max_stages: int

    def __post_init__(self) -> None:  # pragma: no cover - invariantes de datos
        lo, hi = self.reaction_range
        assert 0.0 <= lo <= hi, "rango de reacción inválido"


# Perfiles: cada estado suena distinto. Confirmar es pausado y cuidadoso;
# buscar es más ágil y admite narración; cerrar es rápido y sin adornos.
_PROFILES: dict[ConversationState, StateProfile] = {
    ConversationState.LISTENING: StateProfile(
        state=ConversationState.LISTENING,
        reaction_range=(0.0, 0.0),
        pause_scale=1.0,
        ack_probability=0.0,
        transition_probability=0.0,
        narration_probability=0.0,
        ambient_allowed=False,
        max_stages=1,
    ),
    ConversationState.UNDERSTANDING: StateProfile(
        state=ConversationState.UNDERSTANDING,
        reaction_range=(0.10, 0.30),
        pause_scale=1.0,
        ack_probability=0.55,
        transition_probability=0.25,
        narration_probability=0.0,
        ambient_allowed=False,
        max_stages=3,
    ),
    ConversationState.PROCESSING: StateProfile(
        state=ConversationState.PROCESSING,
        reaction_range=(0.05, 0.20),
        pause_scale=1.15,
        ack_probability=0.60,
        transition_probability=0.55,
        narration_probability=0.85,
        ambient_allowed=True,
        max_stages=4,
    ),
    ConversationState.SEARCHING: StateProfile(
        state=ConversationState.SEARCHING,
        reaction_range=(0.05, 0.18),
        pause_scale=1.25,
        ack_probability=0.65,
        transition_probability=0.60,
        narration_probability=0.90,
        ambient_allowed=True,
        max_stages=4,
    ),
    ConversationState.CONFIRMING: StateProfile(
        state=ConversationState.CONFIRMING,
        reaction_range=(0.12, 0.35),
        pause_scale=1.30,
        ack_probability=0.45,
        transition_probability=0.20,
        narration_probability=0.0,
        ambient_allowed=False,
        max_stages=4,
    ),
    ConversationState.WAITING_USER: StateProfile(
        state=ConversationState.WAITING_USER,
        reaction_range=(0.08, 0.25),
        pause_scale=1.10,
        ack_probability=0.30,
        transition_probability=0.15,
        narration_probability=0.0,
        ambient_allowed=False,
        max_stages=3,
    ),
    ConversationState.CLOSING: StateProfile(
        state=ConversationState.CLOSING,
        reaction_range=(0.05, 0.15),
        pause_scale=0.85,
        ack_probability=0.35,
        transition_probability=0.05,
        narration_probability=0.0,
        ambient_allowed=False,
        max_stages=3,
    ),
}


# Estado conversacional que corresponde a cada intención de habla.
_INTENT_STATE: dict[SpeechIntent, ConversationState] = {
    SpeechIntent.GREETING: ConversationState.WAITING_USER,
    SpeechIntent.ASK_PICKUP: ConversationState.WAITING_USER,
    SpeechIntent.REPROMPT: ConversationState.WAITING_USER,
    SpeechIntent.CONFIRM_PICKUP: ConversationState.CONFIRMING,
    SpeechIntent.CONFIRM_CORRECTION: ConversationState.CONFIRMING,
    SpeechIntent.DISAMBIGUATE: ConversationState.CONFIRMING,
    SpeechIntent.ASK_GEO_CONTEXT: ConversationState.WAITING_USER,
    SpeechIntent.NARRATE: ConversationState.SEARCHING,
    SpeechIntent.WAIT_MORE: ConversationState.PROCESSING,
    SpeechIntent.ACK_CREATE: ConversationState.PROCESSING,
    SpeechIntent.HANDOFF: ConversationState.CLOSING,
    SpeechIntent.SERVICE_CREATED: ConversationState.CLOSING,
    SpeechIntent.SILENCE_PROMPT: ConversationState.WAITING_USER,
    SpeechIntent.REPEAT: ConversationState.WAITING_USER,
    SpeechIntent.CLOSING: ConversationState.CLOSING,
    SpeechIntent.ERROR: ConversationState.CLOSING,
}


def profile_for(state: ConversationState) -> StateProfile:
    return _PROFILES[state]


class ConversationStateManager:
    """Sigue en qué estado conversacional está la llamada.

    Responsabilidad única: mantener el estado actual, su historia y el perfil
    de comportamiento asociado. No decide qué se dice ni cuándo.
    """

    _HISTORY_MAX = 40

    def __init__(self, call_uuid: str = ""):
        self.call_uuid = call_uuid
        self._state = ConversationState.LISTENING
        self._history: list[ConversationState] = [self._state]

    @property
    def state(self) -> ConversationState:
        return self._state

    @property
    def history(self) -> tuple[ConversationState, ...]:
        return tuple(self._history)

    @property
    def profile(self) -> StateProfile:
        return _PROFILES[self._state]

    def enter(self, state: ConversationState) -> ConversationState:
        if state is not self._state:
            logger.debug(
                "[conversation] %s → %s call_uuid=%s",
                self._state.value,
                state.value,
                self.call_uuid,
            )
        self._state = state
        self._history.append(state)
        if len(self._history) > self._HISTORY_MAX:
            del self._history[: len(self._history) - self._HISTORY_MAX]
        return state

    # ── transiciones nombradas (las usa el runtime, en orden real de turno) ──

    def user_speaking(self) -> ConversationState:
        return self.enter(ConversationState.LISTENING)

    def user_turn_ended(self) -> ConversationState:
        return self.enter(ConversationState.UNDERSTANDING)

    def working(self, *, searching: bool = False) -> ConversationState:
        return self.enter(
            ConversationState.SEARCHING if searching else ConversationState.PROCESSING
        )

    def for_intent(self, intent: SpeechIntent) -> ConversationState:
        """Estado que corresponde a lo que se va a decir a continuación."""
        return self.enter(_INTENT_STATE.get(intent, ConversationState.WAITING_USER))

    def awaiting_user(self) -> ConversationState:
        return self.enter(ConversationState.WAITING_USER)

    def closing(self) -> ConversationState:
        return self.enter(ConversationState.CLOSING)

    def profile_of(self, state: Optional[ConversationState] = None) -> StateProfile:
        return _PROFILES[state or self._state]
