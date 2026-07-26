"""Conversation Engine — fachada de la capa conversacional.

Ata los componentes (estado, comportamiento, frases, pausas, tiempos, memoria,
ambiente, planner, renderer) y expone lo mínimo que el runtime necesita:
observar el turno del usuario, anunciar trabajo, planificar una intención y
renderizar el plan a audio.

Una instancia por llamada: la memoria de expresiones y el estado conversacional
son de esa conversación y de ninguna otra.
"""

from __future__ import annotations

import logging
import random
from typing import Optional

from services.voice.conversation.ambient import AmbientSoundManager
from services.voice.conversation.backchannel import BackchannelManager
from services.voice.conversation.behavior import BehaviorEngine
from services.voice.conversation.memory import ConversationMemory
from services.voice.conversation.pauses import PauseManager
from services.voice.conversation.phrases import PhraseManager
from services.voice.conversation.plan import (
    SpeechIntent,
    SpeechPlan,
    SpeechRequest,
)
from services.voice.conversation.planner import SpeechPlanner
from services.voice.conversation.renderer import (
    RenderedSpeech,
    SpeechRenderer,
    SynthFn,
)
from services.voice.conversation.states import (
    ConversationState,
    ConversationStateManager,
)
from services.voice.conversation.timing import ConversationTimingEngine

logger = logging.getLogger("lyra.voice.conversation")


class ConversationEngine:
    """Capa conversacional completa de una llamada."""

    def __init__(
        self,
        call_uuid: str = "",
        *,
        rng: Optional[random.Random] = None,
        sample_rate: int = 8000,
    ):
        self.call_uuid = call_uuid
        self._rng = rng or random.Random()

        self.states = ConversationStateManager(call_uuid)
        self.memory = ConversationMemory()
        self.phrases = PhraseManager(self.memory, self._rng)
        self.pauses = PauseManager(self.memory, self._rng)
        self.timing = ConversationTimingEngine(self._rng)
        self.backchannel = BackchannelManager(self.memory, self._rng)
        self.behavior = BehaviorEngine(self.memory, self._rng, self.backchannel)
        self.ambient = AmbientSoundManager(self._rng, sample_rate=sample_rate)
        self.planner = SpeechPlanner(
            self.phrases, self.pauses, self.timing, self.behavior, self.memory
        )
        self.renderer = SpeechRenderer(self.ambient, sample_rate=sample_rate)

        # Audio ya emitido en el turno actual: evita encadenar dos retardos de
        # reacción cuando el turno ya rompió el silencio con una narración.
        self._spoke_this_turn = False

    # ── seguimiento del turno ──

    @property
    def state(self) -> ConversationState:
        return self.states.state

    def user_speaking(self) -> None:
        """El canal de captura vuelve a estar abierto: la palabra es del usuario.

        Mientras dure, ninguna señal de escucha puede emitirse — competiría con
        el reconocimiento de la voz que se está capturando.
        """
        self.backchannel.capture_reopened()
        self.states.user_speaking()

    def begin_turn(self) -> None:
        """El usuario terminó de hablar: empieza el turno de Lyra."""
        self._spoke_this_turn = False
        self.backchannel.capture_closed()
        self.states.user_turn_ended()

    def working(self, *, searching: bool = False) -> None:
        self.states.working(searching=searching)

    def end_turn(self) -> None:
        self._spoke_this_turn = False
        self.states.awaiting_user()

    # ── planificación ──

    def plan(self, request: SpeechRequest) -> SpeechPlan:
        state = self.states.for_intent(request.intent)
        plan = self.planner.plan(
            request,
            self.states.profile_of(state),
            state=state.value,
            already_speaking=self._spoke_this_turn,
        )
        if plan.segments:
            self._spoke_this_turn = True
        logger.debug(
            "[conversation] plan call_uuid=%s intent=%s state=%s stages=%d silence=%.2fs",
            self.call_uuid,
            request.intent.value,
            state.value,
            len(plan.speech_segments),
            plan.silence_seconds,
        )
        return plan

    async def render(self, plan: SpeechPlan, synth: SynthFn) -> RenderedSpeech:
        return await self.renderer.render(plan, synth)

    def playback_started(self) -> None:
        """Empieza a sonar audio de Lyra (lo publica el runtime)."""
        self.backchannel.playback_started()

    def playback_finished(self) -> None:
        self.backchannel.playback_finished()

    # ── atajos de intención usados por el runtime ──

    def narration(self, kind: str) -> SpeechRequest:
        """Narración de un proceso REALMENTE en ejecución."""
        self.working(searching=kind in ("address", "place", "geo_context"))
        return SpeechRequest(
            intent=SpeechIntent.NARRATE, did_work=True, kind=kind or "generic"
        )

    def wait_more(self, kind: str = "") -> SpeechRequest:
        """La operación se alargó: mantener viva la conversación."""
        return SpeechRequest(
            intent=SpeechIntent.WAIT_MORE,
            did_work=True,
            kind=kind or "generic",
            after_user_turn=False,
        )

    def wait_check_interval(self, attempt: int) -> float:
        return self.timing.wait_check_interval(attempt)

    def fallback(self, text: str) -> SpeechRequest:
        """Texto suelto sin intención declarada (disculpas técnicas)."""
        return SpeechRequest(
            intent=SpeechIntent.ERROR, text=text, after_user_turn=False
        )
