"""Behavior Engine — decide qué se permite en este turno.

No escribe texto ni mide silencios: decide. Si hay acuse de escucha, si hay
transición, si se narra el trabajo, si suena el fondo contextual, cuánto se
tarda en arrancar y qué peso tienen las pausas del turno.

Reglas invariantes:
  · La narración exige trabajo real (`did_work`). Nunca se dice que se está
    buscando algo si no se está buscando nada.
  · Nunca dos rellenos consecutivos: ni pegados dentro de una respuesta, ni en
    dos turnos seguidos con la misma probabilidad de siempre.
  · La frecuencia es probabilística, nunca periódica.
  · La señal de escucha solo sale dentro de la ventana segura del
    `BackchannelManager`: nunca compite con la captura del usuario.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from services.voice.conversation.backchannel import BackchannelManager
from services.voice.conversation.memory import ConversationMemory
from services.voice.conversation.plan import SpeechIntent, SpeechRequest
from services.voice.conversation.states import StateProfile


@dataclass(frozen=True)
class BehaviorDecision:
    use_ack: bool
    use_transition: bool
    use_narration: bool
    use_found: bool
    use_ambient: bool
    reaction: float
    pause_scale: float

    @property
    def uses_filler(self) -> bool:
        return self.use_ack or self.use_transition


# Intenciones que jamás llevan adorno: son cierres o disculpas donde cualquier
# muletilla suena fuera de lugar.
_NO_FILLER_INTENTS = frozenset({SpeechIntent.ERROR, SpeechIntent.REPEAT})

# Intenciones en las que un "ya la encontré" es literalmente cierto cuando hubo
# trabajo previo: se acaba de resolver una ubicación y se devuelve el resultado.
_RESULT_INTENTS = frozenset(
    {SpeechIntent.CONFIRM_PICKUP, SpeechIntent.HANDOFF, SpeechIntent.DISAMBIGUATE}
)


class BehaviorEngine:
    """Aplica las reglas de comportamiento sobre el perfil del estado."""

    def __init__(
        self,
        memory: ConversationMemory,
        rng: Optional[random.Random] = None,
        backchannel: Optional[BackchannelManager] = None,
    ):
        self._memory = memory
        self._rng = rng or random.Random()
        self.backchannel = backchannel or BackchannelManager(memory, self._rng)

    def _roll(self, probability: float) -> bool:
        if probability <= 0.0:
            return False
        if probability >= 1.0:
            return True
        return self._rng.random() < probability

    def decide(
        self,
        request: SpeechRequest,
        profile: StateProfile,
        *,
        reaction: float,
    ) -> BehaviorDecision:
        no_filler = request.intent in _NO_FILLER_INTENTS

        # Dos turnos seguidos con relleno cansan: la probabilidad se desploma.
        damping = 1.0
        if self._memory.filler_last_turn:
            damping = 0.35
        if self._memory.filler_streak >= 2:
            damping = 0.0

        # Señal de escucha: la ventana segura la decide el BackchannelManager.
        use_ack = not no_filler and self.backchannel.should_emit(
            after_user_turn=request.after_user_turn,
            probability=profile.ack_probability * damping,
        )
        use_transition = (
            not no_filler
            and request.did_work
            and self._roll(profile.transition_probability * damping)
        )

        use_narration = request.did_work and self._roll(profile.narration_probability)
        if request.intent is SpeechIntent.NARRATE:
            use_narration = request.did_work  # el turno existe para narrar
        if request.intent is SpeechIntent.WAIT_MORE:
            use_narration = False

        # Nunca dos rellenos pegados: sin narración de por medio, acuse y
        # transición quedarían uno tras otro. Se conserva uno solo.
        if use_ack and use_transition and not use_narration:
            if self._rng.random() < 0.5:
                use_transition = False
            else:
                use_ack = False

        use_found = (
            request.did_work
            and request.intent in _RESULT_INTENTS
            and not use_narration
            and self._roll(0.45)
        )

        use_ambient = (
            profile.ambient_allowed
            and request.did_work
            and (use_narration or use_transition)
            and self._roll(0.55)
        )

        # El ritmo del turno también varía: el mismo estado no suena idéntico
        # dos veces seguidas.
        pause_scale = round(
            profile.pause_scale * (1.0 + self._rng.uniform(-0.12, 0.12)), 3
        )

        return BehaviorDecision(
            use_ack=use_ack,
            use_transition=use_transition,
            use_narration=use_narration,
            use_found=use_found,
            use_ambient=use_ambient,
            reaction=reaction,
            pause_scale=pause_scale,
        )
