"""Clasificador de interrupción real vs. backchannel (spec §3.6).

Mientras el bot habla, el audio del usuario sigue fluyendo (full-duplex).
No toda voz entrante debe cortar el TTS: un "mm-hmm" o un "sí" de cortesía
mientras Lyra explica no es una interrupción. Este clasificador combina:

  1. Energía sostenida del residual post-AEC (≥ VOICE_BARGE_MIN_MS).
  2. Contenido del parcial STT: señales explícitas de interrupción
     (core.conversation_repair.BargeInHandler, rescatado de V1), o palabras
     con contenido real (no backchannel).
  3. Contexto del FSM: en confirming_origin un "sí"/"no" seco ES contenido
     (es la respuesta que se espera) y debe interrumpir para responder ágil.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from core.config import settings
from core.conversation_repair import BargeInHandler
from services.telephony.session_store import STATE_CONFIRMING_ORIGIN

logger = logging.getLogger("lyra.voice.barge")

SAMPLE_RATE = 8000

# Muletillas de acompañamiento que NO interrumpen (sin tildes, minúsculas).
_BACKCHANNEL_TOKENS = frozenset({
    "mm", "mmm", "aja", "ajam", "aha", "uhum", "eh", "ah", "oh",
    "ok", "okey", "ya", "bueno", "listo", "claro", "dale", "eso",
    "si", "señora", "señor", "gracias",
})

_ENERGY_THRESHOLD_RMS = 350.0


@dataclass
class InterruptionClassifier:
    """Decide si la voz entrante durante playback es interrupción real."""

    min_ms: int = 0
    _speech_ms: float = field(default=0.0, init=False)
    _has_text_signal: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not self.min_ms:
            self.min_ms = int(settings.VOICE_BARGE_MIN_MS or 250)

    def reset(self) -> None:
        self._speech_ms = 0.0
        self._has_text_signal = False

    def feed_audio(self, residual_pcm: bytes) -> None:
        """Acumula duración de habla sostenida sobre el residual post-AEC."""
        if not residual_pcm:
            return
        samples = np.frombuffer(residual_pcm, dtype=np.int16).astype(np.float64)
        if samples.size == 0:
            return
        rms = float(np.sqrt(np.mean(samples**2)))
        frame_ms = samples.size / SAMPLE_RATE * 1000.0
        if rms >= _ENERGY_THRESHOLD_RMS:
            self._speech_ms += frame_ms
        else:
            # Decaimiento suave: una micro-pausa no reinicia el contador a 0.
            self._speech_ms = max(0.0, self._speech_ms - frame_ms * 0.5)

    def feed_partial(self, partial_text: str, state: str) -> None:
        """Evalúa el contenido del parcial STT recibido durante el playback."""
        if not partial_text:
            return
        if BargeInHandler.is_interruption(partial_text):
            self._has_text_signal = True
            return
        if self.meaningful_tokens(partial_text, state):
            self._has_text_signal = True

    @staticmethod
    def meaningful_tokens(text: str, state: str) -> bool:
        from core.stt_enhancer import strip_accents

        tokens = [
            strip_accents(t.strip(".,;:!?¿¡").lower())
            for t in text.split()
            if t.strip(".,;:!?¿¡")
        ]
        if not tokens:
            return False
        if state == STATE_CONFIRMING_ORIGIN and any(
            t in ("si", "no") for t in tokens
        ):
            # La respuesta esperada llegó mientras el bot aún preguntaba.
            return True
        content = [t for t in tokens if t not in _BACKCHANNEL_TOKENS]
        return len(content) >= 2

    def should_interrupt(self) -> bool:
        return self._speech_ms >= self.min_ms and self._has_text_signal
