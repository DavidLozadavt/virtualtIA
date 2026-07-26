"""Tipos del plan de habla — contrato entre la lógica y la voz.

La lógica de negocio (orquestador) NUNCA produce el texto final que se
reproduce: produce una `SpeechRequest` (qué se quiere comunicar y con qué
datos). El `SpeechPlanner` la convierte en un `SpeechPlan` — una secuencia de
segmentos de habla, pausas y sonido contextual — y el `SpeechRenderer` lo
convierte en audio.

Un plan es datos puros: se puede inspeccionar y probar sin sintetizar nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SpeechIntent(str, Enum):
    """Qué quiere comunicar el turno. NO cómo se dice."""

    GREETING = "greeting"                    # apertura de la llamada
    ASK_PICKUP = "ask_pickup"                # pedir el punto de recogida
    REPROMPT = "reprompt"                    # reparación / reintento
    CONFIRM_PICKUP = "confirm_pickup"        # confirmar origen (+barrio)
    CONFIRM_CORRECTION = "confirm_correction"  # confirmar una corrección
    DISAMBIGUATE = "disambiguate"            # elegir entre opciones
    ASK_GEO_CONTEXT = "ask_geo_context"      # pedir barrio/referencia
    NARRATE = "narrate"                      # narrar trabajo en curso
    WAIT_MORE = "wait_more"                  # la operación se alargó
    ACK_CREATE = "ack_create"                # aceptar y crear el servicio
    HANDOFF = "handoff"                      # se entrega al conductor
    SERVICE_CREATED = "service_created"      # resultado del backend
    SILENCE_PROMPT = "silence_prompt"        # el usuario no habló
    REPEAT = "repeat"                        # repetir lo último
    CLOSING = "closing"                      # despedida
    ERROR = "error"                          # falla técnica


@dataclass(frozen=True)
class SpeechRequest:
    """Intención + datos. El texto de negocio viaja literal en `text`/`slots`.

    `did_work` es la garantía de honestidad de la narración: solo cuando el
    turno ejecutó realmente una operación (geocodificar, resolver, crear) se
    permite narrar que se está trabajando o decir que "ya se encontró".
    """

    intent: SpeechIntent
    text: str = ""                       # payload literal (nunca se reescribe)
    slots: dict = field(default_factory=dict)
    did_work: bool = False
    after_user_turn: bool = True         # el usuario acaba de hablar
    kind: str = ""                       # matiz para NARRATE (address/place/…)

    def slot(self, name: str) -> Optional[str]:
        value = self.slots.get(name)
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class SegmentKind(str, Enum):
    SPEECH = "speech"
    PAUSE = "pause"
    AMBIENT = "ambient"


@dataclass(frozen=True)
class SpeechSegment:
    kind: SegmentKind
    text: str = ""
    duration: float = 0.0
    ambient: str = ""

    @classmethod
    def speech(cls, text: str) -> "SpeechSegment":
        return cls(kind=SegmentKind.SPEECH, text=text.strip())

    @classmethod
    def pause(cls, duration: float) -> "SpeechSegment":
        return cls(kind=SegmentKind.PAUSE, duration=max(0.0, duration))

    @classmethod
    def ambient_bed(cls, kind: str, duration: float) -> "SpeechSegment":
        return cls(
            kind=SegmentKind.AMBIENT, ambient=kind, duration=max(0.0, duration)
        )


@dataclass(frozen=True)
class SpeechPlan:
    """Secuencia ejecutable de habla, pausas y sonido contextual."""

    request: SpeechRequest
    segments: tuple[SpeechSegment, ...]
    state: str = ""

    @property
    def text(self) -> str:
        """Todo lo que se va a decir, en orden (para historial y anti-eco)."""
        return " ".join(
            s.text for s in self.segments if s.kind is SegmentKind.SPEECH and s.text
        ).strip()

    @property
    def speech_segments(self) -> tuple[SpeechSegment, ...]:
        return tuple(s for s in self.segments if s.kind is SegmentKind.SPEECH)

    @property
    def silence_seconds(self) -> float:
        return sum(
            s.duration
            for s in self.segments
            if s.kind in (SegmentKind.PAUSE, SegmentKind.AMBIENT)
        )
