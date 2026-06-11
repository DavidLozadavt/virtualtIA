"""
Acumulador de audio WebSocket — segmentación por duración + VAD.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple

from core.config import settings
from services.telephony.audio_vad import detect_end_of_utterance, detect_end_of_utterance_pcm16

AudioEncoding = Literal["pcm16", "mulaw"]

MIN_SEC = 1.5
MAX_SEC = 3.0
VAD_CHECK_EVERY_CHUNKS = 10


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

    @property
    def bytes_per_second(self) -> int:
        return (settings.TELEPHONY_SAMPLE_RATE or 8000) * (2 if self.encoding == "pcm16" else 1)

    @property
    def min_bytes(self) -> int:
        return int(self.bytes_per_second * MIN_SEC)

    @property
    def max_bytes(self) -> int:
        return int(self.bytes_per_second * MAX_SEC)

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
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
