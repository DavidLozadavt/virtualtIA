"""Endpointing híbrido acústico + semántico (spec §3.2, §3.7).

Capa acústica: Deepgram emite `speech_final` (pausa detectada, ~300 ms) y
`UtteranceEnd` (gap en los tiempos de palabra). Capa semántica propia: si el
texto acumulado termina en continuación evidente ("calle", "en", "número",
un número colgado), la pausa NO cierra el turno de inmediato — se retiene
hasta `VOICE_ENDPOINT_HOLD_MS` (con techo `VOICE_ENDPOINT_HOLD_MAX_MS`) por
si el usuario está dictando una dirección despacio. Esto reemplaza el
`UTT_SIL_SECS=3` fijo de V1: el caso normal cierra en ~300 ms y el caso
"dirección dictada con pausas" espera solo lo necesario.

Máquina de estados síncrona con reloj inyectado (testeable sin dormir).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from services.voice.stt_stream import (
    SpeechStartedEvent,
    TranscriptEvent,
    UtteranceEndEvent,
)
from services.voice.text_normalize import _TENS, _UNITS

# Palabras que dejan la frase evidentemente incompleta si son la última.
_CONTINUATION_WORDS = frozenset({
    "en", "de", "del", "la", "el", "los", "las", "con", "por", "al", "a",
    "hacia", "para", "y", "numero", "cerca", "frente", "junto", "diagonal",
    "esquina", "entre", "sobre", "detras", "detrás", "antes", "despues",
    "después", "mi", "su", "una", "un",
    "calle", "carrera", "cra", "cl", "kr", "kra", "transversal", "avenida",
    "manzana", "casa", "torre", "bloque", "apartamento", "etapa", "sector",
}) | frozenset(_UNITS) | frozenset(_TENS.values())


def ends_in_continuation(text: str) -> bool:
    """True si el final del texto sugiere que la frase sigue."""
    t = (text or "").strip().rstrip(".,;:")
    if not t:
        return False
    last = t.split()[-1].lower()
    if re.fullmatch(r"\d+[a-z]?", last):
        return True  # número colgado: "calle dieciséis... 41"
    from core.stt_enhancer import strip_accents

    return strip_accents(last) in {
        s for w in _CONTINUATION_WORDS for s in (strip_accents(w),)
    }


@dataclass
class StablePartial:
    """Parcial estable: apto para NLU anticipado / geocoding especulativo."""

    text: str
    confidence: float


@dataclass
class TurnReady:
    """Fin de turno decidido: el texto completo entra al pipeline de turno."""

    text: str
    confidence: float


@dataclass
class HybridEndpointer:
    hold_ms: int
    hold_max_ms: int

    _segments: list[str] = field(default_factory=list)
    _confidences: list[float] = field(default_factory=list)
    _interim: str = ""
    _interim_conf: float = 0.0
    _last_interim: str = ""
    _interim_repeats: int = 0
    _hold_deadline: Optional[float] = None
    _hold_started: Optional[float] = None

    def _full_text(self) -> str:
        parts = [s for s in self._segments if s]
        if self._interim:
            parts.append(self._interim)
        return " ".join(parts).strip()

    def _avg_confidence(self) -> float:
        vals = list(self._confidences)
        if self._interim and self._interim_conf:
            vals.append(self._interim_conf)
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def pending_deadline(self) -> Optional[float]:
        """Instante (reloj monotónico) en que la retención semántica expira."""
        return self._hold_deadline

    def has_speech(self) -> bool:
        return bool(self._full_text())

    def _commit(self) -> list[object]:
        text = self._full_text()
        confidence = self._avg_confidence()
        self.reset()
        if not text:
            return []
        return [TurnReady(text=text, confidence=confidence)]

    def reset(self) -> None:
        self._segments.clear()
        self._confidences.clear()
        self._interim = ""
        self._interim_conf = 0.0
        self._last_interim = ""
        self._interim_repeats = 0
        self._hold_deadline = None
        self._hold_started = None

    def _arm_hold(self, now: float) -> None:
        if self._hold_started is None:
            self._hold_started = now
        cap = self._hold_started + self.hold_max_ms / 1000.0
        self._hold_deadline = min(now + self.hold_ms / 1000.0, cap)

    def on_event(self, event: object, now: float) -> list[object]:
        """Procesa un evento STT; devuelve señales (StablePartial / TurnReady)."""
        if isinstance(event, TranscriptEvent):
            return self._on_transcript(event, now)
        if isinstance(event, UtteranceEndEvent):
            # Sin más palabras según los tiempos del ASR: cierre definitivo,
            # anula cualquier retención semántica pendiente.
            if self.has_speech():
                return self._commit()
            return []
        if isinstance(event, SpeechStartedEvent):
            return []
        return []

    def _on_transcript(self, ev: TranscriptEvent, now: float) -> list[object]:
        signals: list[object] = []

        if ev.is_final:
            if ev.text:
                self._segments.append(ev.text)
                self._confidences.append(ev.confidence)
            self._interim = ""
            self._interim_conf = 0.0
            self._last_interim = ""
            self._interim_repeats = 0

            full = self._full_text()
            if full:
                signals.append(
                    StablePartial(text=full, confidence=self._avg_confidence())
                )

            if ev.speech_final and full:
                if ends_in_continuation(full):
                    self._arm_hold(now)
                else:
                    self._hold_deadline = None
                    self._hold_started = None
                    signals.extend(self._commit())
            return signals

        # Interim: el usuario sigue hablando → cancela la retención pendiente.
        if ev.text:
            if self._hold_deadline is not None:
                self._hold_deadline = None
            self._interim = ev.text
            self._interim_conf = ev.confidence
            if ev.text == self._last_interim:
                self._interim_repeats += 1
                if self._interim_repeats == 1:
                    signals.append(
                        StablePartial(
                            text=self._full_text(),
                            confidence=self._avg_confidence(),
                        )
                    )
            else:
                self._last_interim = ev.text
                self._interim_repeats = 0
        return signals

    def on_timer(self, now: float) -> list[object]:
        """El runtime llama esto cuando expira `pending_deadline`."""
        if self._hold_deadline is not None and now >= self._hold_deadline:
            self._hold_deadline = None
            self._hold_started = None
            return self._commit()
        return []
