"""Telephony services — channel-agnostic voice call handling (FreeSWITCH, etc.)."""

from services.telephony.voice_call_engine import VoiceCallEngine, VoiceTurnResult, VoiceAction
from services.telephony.session_store import SessionStore, CallSession, get_session_store
from services.telephony.backend_client import TelephonyBackendClient
from services.telephony.call_handler import process_text_turn, process_stt_turn

__all__ = [
    "VoiceCallEngine",
    "VoiceTurnResult",
    "VoiceAction",
    "SessionStore",
    "CallSession",
    "get_session_store",
    "TelephonyBackendClient",
    "process_text_turn",
    "process_stt_turn",
]
