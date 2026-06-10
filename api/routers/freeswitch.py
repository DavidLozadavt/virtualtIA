"""
Router FreeSWITCH — telefonía sin Twilio.

Endpoints:
  GET  /freeswitch/health
  WS   /freeswitch/audio
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, WebSocket

from core.config import settings
from services.telephony.conversation_engine import get_active_session_count
from services.telephony.providers.freeswitch_provider import FreeswitchProvider

logger = logging.getLogger("lyra.telephony.freeswitch.router")

freeswitch_router = APIRouter(prefix="/freeswitch", tags=["FreeSWITCH"])


@freeswitch_router.get("/health")
async def freeswitch_health():
    return {
        "ok": True,
        "service": "lyra-intellitaxi-freeswitch",
        "telephony_provider": os.getenv("TELEPHONY_PROVIDER", "freeswitch"),
        "backend_api": settings.INTELLITAXI_API_BASE,
        "ws_path": os.getenv("FREESWITCH_AUDIO_WS_PATH", "/freeswitch/audio"),
        "active_sessions": get_active_session_count(),
        "stt": "groq_or_whisper",
        "tts": "edge-tts",
    }


@freeswitch_router.websocket("/audio")
async def freeswitch_audio(websocket: WebSocket):
    """WebSocket para mod_audio_stream de FreeSWITCH."""
    http_client = getattr(websocket.app.state, "http_client", None)
    provider = FreeswitchProvider(websocket, http_client=http_client)
    await provider.run()
