"""
Acumulador de audio WebSocket — segmentación por duración + VAD.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple

from core.config import settings
from services.telephony.audio_vad import detect_end_of_utterance, detect_end_of_utterance_pcm16

logger = logging.getLogger("lyra.freeswitch.wsbuf")

AudioEncoding = Literal["pcm16", "mulaw"]

MIN_SEC = 1.5
# Fallback si FS_MAX_UTTERANCE_SEC no está en el entorno. NO 3.0: cortaba a la
# mitad las direcciones dictadas pausado por adultos mayores. El valor real se
# toma de settings.FS_MAX_UTTERANCE_SEC (12.0 por defecto en .env).
MAX_SEC_FALLBACK = 12.0
VAD_CHECK_EVERY_CHUNKS = 10


def _max_utterance_sec() -> float:
    """Duración máxima de locución antes de forzar flush (env-driven)."""
    val = getattr(settings, "FS_MAX_UTTERANCE_SEC", None)
    try:
        sec = float(val) if val is not None else MAX_SEC_FALLBACK
    except (TypeError, ValueError):
        sec = MAX_SEC_FALLBACK
    # Guardia: un valor absurdamente bajo reintroduce el bug de corte.
    return sec if sec >= MIN_SEC else MAX_SEC_FALLBACK


def resolve_ws_encoding(hint: Optional[str] = None) -> AudioEncoding:
    raw = (hint or settings.TELEPHONY_WS_AUDIO_ENCODING or "pcm16").lower()
    if raw in ("mulaw", "pcmu", "ulaw"):
        return "mulaw"
    return "pcm16"


@dataclass
class WsAudioBuffer:
    call_uuid: str
    encoding: AudioEncoding = "pcm16"
    buffer: bytearray = field(default_factory=bytearray)
    chunk_count: int = 0
    first_chunk_at: Optional[float] = None
    first_chunk_logged: bool = False
    # Instante (time.monotonic) hasta el cual se descarta el audio entrante.
    # Se activa mientras Lyra reproduce TTS: sin esto, mod_audio_stream devuelve
    # el eco de su propia voz, el buffer lo vacía como "habla del usuario", el STT
    # produce basura y el motor cae en el bucle "no logré entender / ¿me confirmas?".
    muted_until: float = 0.0
    # Diagnóstico: chunks descartados por mute en la ventana de gate actual.
    muted_drop_count: int = 0

    def is_muted(self) -> bool:
        return time.monotonic() < self.muted_until

    def gate_playback(self, seconds: float) -> None:
        """Silencia la captura durante un playback y descarta lo ya acumulado."""
        self.muted_until = time.monotonic() + max(0.0, seconds)
        self.buffer = bytearray()
        self.chunk_count = 0
        self.first_chunk_at = None

    @property
    def bytes_per_second(self) -> int:
        return (settings.TELEPHONY_SAMPLE_RATE or 8000) * (2 if self.encoding == "pcm16" else 1)

    @property
    def min_bytes(self) -> int:
        return int(self.bytes_per_second * MIN_SEC)

    @property
    def max_bytes(self) -> int:
        return int(self.bytes_per_second * _max_utterance_sec())

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self.is_muted():
            # Eco del TTS en curso: descartar para no auto-dispararse.
            self.muted_drop_count += 1
            # Log al primer descarte y luego cada ~2s de audio para no inundar.
            if self.muted_drop_count == 1 or self.muted_drop_count % 100 == 0:
                logger.info(
                    "[ws-buf] DROPPING audio (muted/playback gate) call_uuid=%s "
                    "remaining=%.2fs drops=%d",
                    self.call_uuid,
                    max(0.0, self.muted_until - time.monotonic()),
                    self.muted_drop_count,
                )
            return
        if self.muted_drop_count:
            logger.info(
                "[ws-buf] gate released call_uuid=%s (dropped %d chunks) — capturing again",
                self.call_uuid,
                self.muted_drop_count,
            )
            self.muted_drop_count = 0
        if self.first_chunk_at is None:
            self.first_chunk_at = time.monotonic()
        self.buffer.extend(chunk)
        self.chunk_count += 1

    def should_flush(self) -> Tuple[bool, str]:
        size = len(self.buffer)
        if size < self.min_bytes:
            return False, "below_min_duration"

        if size >= self.max_bytes:
            return True, "max_duration"

        if self.chunk_count % VAD_CHECK_EVERY_CHUNKS != 0:
            return False, "vad_skip"

        raw = bytes(self.buffer)
        if self.encoding == "pcm16":
            end, _ = detect_end_of_utterance_pcm16(raw)
        else:
            end, _ = detect_end_of_utterance(raw)

        if end:
            return True, "silence_detected"

        return False, "accumulating"

    def take_and_reset(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer = bytearray()
        self.chunk_count = 0
        self.first_chunk_at = None
        return data
