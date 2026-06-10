"""Gestión de sesiones de llamada (re-export desde conversation_engine)."""

from services.telephony.conversation_engine import (
    CallSession,
    get_session,
    reset_session,
    get_active_session_count,
)

__all__ = ["CallSession", "get_session", "reset_session", "get_active_session_count"]
