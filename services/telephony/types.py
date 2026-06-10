"""Tipos compartidos para telefonía FreeSWITCH."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TelephonyProvider(str, Enum):
    FREESWITCH = "freeswitch"


@dataclass
class TurnResult:
    """Resultado de un turno conversacional (texto a hablar + control de llamada)."""

    speak: str = ""
    listen: bool = True
    hangup: bool = False
    short_answer: bool = False
    dtmf_mode: bool = False
    reset_session: bool = False
    processing_message: str = ""  # ej. "Un momento por favor..."


@dataclass
class CallStartInfo:
    call_id: str
    caller_id: Optional[str] = None
    caller_source: str = "not_found"
    sample_rate: int = 8000


@dataclass
class TurnInput:
    call_id: str
    text: str = ""
    confidence: float = 1.0
    digits: str = ""
    is_silence: bool = False
