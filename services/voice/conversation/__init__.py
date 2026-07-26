"""Capa conversacional de Lyra — entre la lógica y el sintetizador.

    STT → Motor Conversacional (NLU/FSM de negocio)
        → Speech Planner → Conversation State Manager
        → Speech Renderer → TTS

La lógica de negocio expresa INTENCIONES (`SpeechRequest`); esta capa decide
qué se dice, cómo, con qué ritmo y con qué pausas, y entrega audio listo. El
texto final que escucha el usuario nunca sale directamente del orquestador ni
de un modelo.

Componentes, uno por responsabilidad:
  · `states`    — Conversation State Manager (estados y su comportamiento)
  · `behavior`  — Behavior Engine (qué se permite en este turno)
  · `phrases`   — Phrase Manager (formulaciones sin repetición)
  · `pauses`    — Pause Manager (silencios variables entre ideas)
  · `timing`    — Conversation Timing Engine (reacción humana)
  · `memory`    — Conversation Memory (expresiones ya usadas)
  · `ambient`   — Ambient Sound Manager (fondo contextual discreto)
  · `backchannel` — señales de escucha dentro de su ventana segura
  · `planner`   — Speech Planner (intención → plan multietapa)
  · `renderer`  — Speech Renderer (plan → PCM)
  · `engine`    — fachada por llamada
"""

from services.voice.conversation.ambient import AmbientSoundManager, ambient_for
from services.voice.conversation.backchannel import BackchannelManager
from services.voice.conversation.behavior import BehaviorDecision, BehaviorEngine
from services.voice.conversation.engine import ConversationEngine
from services.voice.conversation.memory import ConversationMemory
from services.voice.conversation.pauses import PauseLength, PauseManager
from services.voice.conversation.phrases import (
    PHRASE_BANK,
    Phrase,
    PhraseManager,
    fixed_phrases,
)
from services.voice.conversation.plan import (
    SegmentKind,
    SpeechIntent,
    SpeechPlan,
    SpeechRequest,
    SpeechSegment,
)
from services.voice.conversation.planner import SpeechPlanner, split_ideas
from services.voice.conversation.renderer import (
    RenderedSpeech,
    SpeechRenderer,
    silence,
)
from services.voice.conversation.states import (
    ConversationState,
    ConversationStateManager,
    StateProfile,
    profile_for,
)
from services.voice.conversation.timing import ConversationTimingEngine

__all__ = [
    "AmbientSoundManager",
    "ambient_for",
    "BackchannelManager",
    "BehaviorDecision",
    "BehaviorEngine",
    "ConversationEngine",
    "ConversationMemory",
    "ConversationState",
    "ConversationStateManager",
    "ConversationTimingEngine",
    "PHRASE_BANK",
    "PauseLength",
    "PauseManager",
    "Phrase",
    "PhraseManager",
    "RenderedSpeech",
    "SegmentKind",
    "SpeechIntent",
    "SpeechPlan",
    "SpeechPlanner",
    "SpeechRenderer",
    "SpeechRequest",
    "SpeechSegment",
    "StateProfile",
    "fixed_phrases",
    "profile_for",
    "silence",
    "split_ideas",
]
