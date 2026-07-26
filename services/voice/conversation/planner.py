"""Speech Planner — convierte una intención lógica en una conversación.

Es la capa que impide que un resultado interno llegue tal cual al sintetizador.
Recibe QUÉ se quiere comunicar y devuelve CÓMO se dice: en etapas (acuse →
transición → resultado → confirmación), con pausas entre ideas, con sonido
contextual cuando hay una acción real detrás, y sin repetir formulaciones.

El contenido de negocio (dirección, barrio, mensaje del backend) viaja siempre
literal: el planner lo trocea y lo rodea, jamás lo reescribe.
"""

from __future__ import annotations

import re
from typing import Optional

from services.voice.conversation.behavior import BehaviorDecision, BehaviorEngine
from services.voice.conversation.memory import ConversationMemory
from services.voice.conversation.pauses import PauseLength, PauseManager
from services.voice.conversation.phrases import PhraseManager
from services.voice.conversation.plan import (
    SegmentKind,
    SpeechIntent,
    SpeechPlan,
    SpeechRequest,
    SpeechSegment,
)
from services.voice.conversation.states import StateProfile
from services.voice.conversation.timing import ConversationTimingEngine

# Presupuesto máximo de silencio por respuesta. Por encima de esto la
# naturalidad se convierte en demora: las pausas se comprimen proporcionalmente.
_MAX_SILENCE_BUDGET = 1.8

# Fondo contextual por matiz de narración.
_AMBIENT_BY_KIND = {
    "address": "search",
    "place": "search",
    "geo_context": "lookup",
    "service": "note",
    "generic": "lookup",
}

_CLAUSE_SPLIT = re.compile(r",\s+")
_HAS_DIGIT = re.compile(r"\d")


def _split_clause(sentence: str) -> list[str]:
    """Trocea una frase larga en ideas por comas seguras.

    NUNCA parte un fragmento con números: una dirección ("Calle 5 número 3 45,
    barrio Pubenza") tiene que salir de una pieza o deja de entenderse.
    """
    text = (sentence or "").strip()
    if not text or len(text.split()) <= 8:
        return [text] if text else []
    parts = _CLAUSE_SPLIT.split(text)
    if len(parts) < 2 or any(_HAS_DIGIT.search(p) for p in parts):
        return [text]
    out = []
    for i, part in enumerate(parts):
        part = part.strip().rstrip(",")
        if not part:
            continue
        if i < len(parts) - 1 and not part.endswith((".", "?", "!")):
            part = f"{part}."
        out.append(part)
    return out or [text]


def split_ideas(text: str) -> list[str]:
    """Contenido de negocio troceado en ideas hablables, sin alterar palabras."""
    from services.voice.text_normalize import split_sentences

    ideas: list[str] = []
    for sentence in split_sentences(text or ""):
        ideas.extend(_split_clause(sentence))
    return [i for i in ideas if i]


class SpeechPlanner:
    """Planifica la respuesta hablada. Responsabilidad única: estructurarla."""

    def __init__(
        self,
        phrases: PhraseManager,
        pauses: PauseManager,
        timing: ConversationTimingEngine,
        behavior: BehaviorEngine,
        memory: ConversationMemory,
    ):
        self._phrases = phrases
        self._pauses = pauses
        self._timing = timing
        self._behavior = behavior
        self._memory = memory

    # ── contenido central por intención ──

    def _core_stages(self, request: SpeechRequest) -> list[str]:
        intent = request.intent
        place = request.slot("place")
        barrio = request.slot("barrio")

        if intent is SpeechIntent.GREETING:
            return list(self._phrases.pick("greeting")) or split_ideas(request.text)

        if intent is SpeechIntent.ASK_PICKUP:
            return list(self._phrases.pick("ask_pickup")) or split_ideas(request.text)

        if intent is SpeechIntent.CONFIRM_PICKUP and place:
            category = "confirm_pickup_barrio" if barrio else "confirm_pickup"
            stages = self._phrases.pick(category, place=place, barrio=barrio or "")
            if stages:
                return list(stages)

        if intent is SpeechIntent.CONFIRM_CORRECTION and place:
            stages = self._phrases.pick("confirm_correction", place=place)
            if stages:
                return list(stages)

        if intent is SpeechIntent.NARRATE:
            kind = request.kind or "generic"
            stages = self._phrases.pick(f"narrate_{kind}") or self._phrases.pick(
                "narrate_generic"
            )
            return list(stages)

        if intent is SpeechIntent.WAIT_MORE:
            return list(self._phrases.pick("wait_more"))

        if intent is SpeechIntent.ACK_CREATE:
            stages = self._phrases.pick("ack_create")
            if stages:
                return list(stages)

        if intent is SpeechIntent.HANDOFF and barrio:
            stages = self._phrases.pick("handoff", barrio=barrio)
            if stages:
                return list(stages)

        # Resto (reparación, desambiguación, pregunta de barrio, mensaje del
        # backend, disculpa): el texto es de negocio y se dice literal, troceado
        # en ideas para que no salga como un bloque.
        return split_ideas(request.text)

    # ── decoraciones conversacionales ──

    def _prefix_stages(
        self, request: SpeechRequest, decision: BehaviorDecision, budget: int
    ) -> list[str]:
        stages: list[str] = []
        if budget <= 0:
            return stages
        if decision.use_ack:
            stages.extend(self._phrases.pick("ack"))
        if len(stages) < budget and decision.use_transition:
            stages.extend(self._phrases.pick("transition"))
        if len(stages) < budget and decision.use_found:
            stages.extend(self._phrases.pick("found"))
        return stages[:budget]

    # ── plan completo ──

    def plan(
        self,
        request: SpeechRequest,
        profile: StateProfile,
        *,
        state: str = "",
        already_speaking: bool = False,
    ) -> SpeechPlan:
        reaction = self._timing.reaction_delay(
            profile,
            already_speaking=already_speaking,
            user_turn=request.after_user_turn,
        )
        decision = self._behavior.decide(request, profile, reaction=reaction)

        core = self._core_stages(request)
        if not core:
            self._memory.note_turn(used_filler=False)
            return SpeechPlan(request=request, segments=(), state=state)

        decor_budget = max(0, profile.max_stages - len(core))
        prefix = self._prefix_stages(request, decision, decor_budget)
        used_filler = bool(prefix) and decision.uses_filler

        scale = decision.pause_scale
        segments: list[SpeechSegment] = []
        if reaction > 0:
            segments.append(SpeechSegment.pause(reaction))

        # Prefijos: cada uno es una idea suelta, separada por su propia pausa.
        for stage in prefix:
            segments.append(SpeechSegment.speech(stage))
            segments.append(
                SpeechSegment.pause(
                    self._pauses.duration(PauseLength.MICRO, scale=scale)
                )
            )

        # Bisagra entre "estoy mirando" y el resultado: la pausa más larga del
        # turno, y el único lugar donde el fondo contextual tiene sentido.
        if prefix:
            hinge_length = PauseLength.LONG if decision.use_ambient else PauseLength.MEDIUM
            hinge = self._pauses.duration(hinge_length, scale=scale)
            segments.pop()  # sustituye la micro-pausa del último prefijo
            if decision.use_ambient:
                ambient_kind = _AMBIENT_BY_KIND.get(request.kind or "generic", "lookup")
                segments.append(SpeechSegment.ambient_bed(ambient_kind, hinge))
            else:
                segments.append(SpeechSegment.pause(hinge))

        # Núcleo: una etapa por idea, con pausas ENTRE ideas (no solo al final).
        for i, stage in enumerate(core):
            segments.append(SpeechSegment.speech(stage))
            if i < len(core) - 1:
                segments.append(
                    SpeechSegment.pause(
                        self._pauses.between_ideas(core[i + 1], scale=scale)
                    )
                )

        segments = _fit_silence_budget(segments)
        self._memory.note_turn(used_filler=used_filler)
        return SpeechPlan(request=request, segments=tuple(segments), state=state)


def _fit_silence_budget(segments: list[SpeechSegment]) -> list[SpeechSegment]:
    """Comprime las pausas si el silencio total excede el presupuesto."""
    total = sum(
        s.duration
        for s in segments
        if s.kind in (SegmentKind.PAUSE, SegmentKind.AMBIENT)
    )
    if total <= _MAX_SILENCE_BUDGET or total <= 0:
        return segments
    factor = _MAX_SILENCE_BUDGET / total
    out: list[SpeechSegment] = []
    for s in segments:
        if s.kind is SegmentKind.PAUSE:
            out.append(SpeechSegment.pause(round(s.duration * factor, 3)))
        elif s.kind is SegmentKind.AMBIENT:
            out.append(
                SpeechSegment.ambient_bed(s.ambient, round(s.duration * factor, 3))
            )
        else:
            out.append(s)
    return out
