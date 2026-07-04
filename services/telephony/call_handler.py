"""
Orquestación de turnos telefónicos — lógica compartida entre HTTP y WebSocket.
"""

from __future__ import annotations

import base64
import logging
import uuid
from typing import Any, Dict, Optional

import httpx

from services.telephony.session_store import CallSession, SessionStore, STATE_FINISHED
from services.telephony.tts_file_store import build_audio_url, get_tts_file_store, sanitize_audio_id
from services.telephony.tts_service import TelephonyTTSService
from services.telephony.voice_call_engine import VoiceAction, VoiceCallEngine, VoiceTurnResult

logger = logging.getLogger("lyra.telephony.handler")

_engine = VoiceCallEngine()
_tts = TelephonyTTSService()


async def run_conversation_turn(
    store: SessionStore,
    session: CallSession,
    *,
    user_text: str = "",
    confidence: Optional[float] = 0.0,
    digits: str = "",
    http_client: Optional[httpx.AsyncClient] = None,
) -> VoiceTurnResult:
    """Ejecuta un turno y persiste sesión."""
    turn = await _engine.process_turn(
        session,
        user_text=user_text,
        confidence=confidence,
        digits=digits,
        http_client=http_client,
    )

    if turn.action == VoiceAction.CREATE_SERVICE:
        turn = await _engine.process_turn(
            turn.session or session,
            user_text="",
            http_client=http_client,
        )

    if turn.session:
        store.save(turn.session)
        # Mantener sesión tras crear servicio para que audio residual del WS
        # no reinicie el flujo en waiting_origin / confirming_origin.
        if turn.action == VoiceAction.HANGUP and not turn.session.service_created:
            store.delete(turn.session.call_uuid)

    return turn


def _restore_terminal_session(store: SessionStore, call_uuid: str) -> Optional[CallSession]:
    """Si el servicio ya se envió al backend, devolver sesión terminal."""
    from services.telephony.idempotency import get_submission_guard

    if not get_submission_guard().already_submitted(call_uuid):
        return None

    session = store.get(call_uuid)
    if session and (session.service_created or session.state == STATE_FINISHED):
        return session

    session = CallSession(
        call_uuid=call_uuid,
        service_created=True,
        state=STATE_FINISHED,
        last_message=(
            "Tu solicitud ya fue registrada. "
            "En un momento el conductor se comunica contigo."
        ),
    )
    store.save(session)
    return session


def build_audio_response(turn: VoiceTurnResult, tts_result: dict) -> Dict[str, Any]:
    """Empaqueta respuesta HTTP/WS con audio TTS."""
    audio_b64 = ""
    fmt = tts_result.get("format", "mp3")
    key = "mulaw" if fmt == "mulaw" else "mp3"
    if tts_result.get(key):
        audio_b64 = base64.b64encode(tts_result[key]).decode("ascii")

    return {
        "speak_text": turn.speak_text,
        "action": turn.action.value,
        "short_answer": turn.short_answer,
        "backend_ok": turn.backend_ok,
        "state": turn.session.state if turn.session else None,
        "audio_base64": audio_b64,
        "audio_format": fmt,
        "hangup": turn.action == VoiceAction.HANGUP,
    }


async def process_text_turn(
    store: SessionStore,
    call_uuid: str,
    *,
    user_text: str,
    confidence: Optional[float] = 1.0,
    digits: str = "",
    http_client: Optional[httpx.AsyncClient] = None,
    create_session_if_missing: bool = False,
    file_playback: bool = False,
    request: Optional[Any] = None,
    tts_result: Optional[dict] = None,
) -> Dict[str, Any]:
    session = store.get(call_uuid)
    if not session:
        session = _restore_terminal_session(store, call_uuid)
    if not session and create_session_if_missing:
        session = store.get_or_create(call_uuid)
    if not session:
        return {"success": False, "error": f"Session not found: {call_uuid}"}

    logger.info(
        "[handler] process_text call_uuid=%s text=%r conf=%s",
        call_uuid,
        user_text[:100],
        f"{confidence:.2f}" if confidence is not None else "n/a",
    )

    turn = await run_conversation_turn(
        store,
        session,
        user_text=user_text,
        confidence=confidence,
        digits=digits,
        http_client=http_client,
    )

    if tts_result is None:
        tts_result = await _tts.synthesize_for_telephony(turn.speak_text)
    payload = build_audio_response(turn, tts_result)
    payload.update({"success": True, "call_uuid": call_uuid})

    if file_playback:
        file_store = get_tts_file_store()
        # ID único por turno: si se reusa el call_uuid como nombre de archivo,
        # la URL de playback es idéntica cada turno y FreeSWITCH (mod_http_cache)
        # sirve el audio cacheado del turno anterior (ej. "¿Me confirmas?") en
        # vez del mensaje nuevo. Sufijo aleatorio → URL única → sin cache stale.
        audio_id = f"{sanitize_audio_id(call_uuid)}-{uuid.uuid4().hex[:8]}"
        _, file_path = file_store.save_telephony_audio(
            tts_result,
            call_uuid=call_uuid,
            audio_id=audio_id,
        )
        payload["audio_id"] = audio_id
        payload["audio_url"] = build_audio_url(audio_id, request)
        payload["audio_format"] = "wav"
        payload["action"] = turn.action.value
        payload["file_path"] = str(file_path)

    if turn.backend_ok is not None:
        logger.info(
            "[handler] backend result call_uuid=%s ok=%s",
            call_uuid,
            turn.backend_ok,
        )

    return payload


async def process_stt_turn(
    store: SessionStore,
    call_uuid: str,
    *,
    recognized_text: str,
    confidence: Optional[float],
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Turno proveniente de STT (WebSocket audio)."""
    session = store.get(call_uuid)
    if not session:
        session = _restore_terminal_session(store, call_uuid)
    if not session:
        session = store.get_or_create(call_uuid)

    logger.info(
        "[handler] stt_turn call_uuid=%s caller=%s text=%r conf=%s",
        call_uuid,
        session.caller_phone,
        recognized_text[:100],
        f"{confidence:.2f}" if confidence is not None else "n/a",
    )

    turn = await run_conversation_turn(
        store,
        session,
        user_text=recognized_text,
        confidence=confidence,
        http_client=http_client,
    )

    tts_result = await _tts.synthesize_for_telephony(turn.speak_text)
    response = build_audio_response(turn, tts_result)
    response.update({
        "event": "response",
        "call_uuid": call_uuid,
        "text": recognized_text,
    })
    return response
