"""
api/routers/freeswitch.py — Integración directa FreeSWITCH ↔ Lyra (sin Twilio).
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from core.config import settings
from services.telephony.audio_vad import detect_end_of_utterance
from services.telephony.backend_client import TelephonyBackendClient
from services.telephony.call_handler import process_stt_turn, process_text_turn
from services.telephony.phone_utils import limpiar_numero, resolve_caller_phone
from services.telephony.session_store import get_session_store
from services.telephony.stt_service import TelephonySTTService
from services.telephony.tts_service import TelephonyTTSService
from services.telephony.voice_call_engine import VoiceCallEngine

logger = logging.getLogger("lyra.freeswitch")

freeswitch_router = APIRouter(prefix="/freeswitch", tags=["FreeSWITCH"])

_engine = VoiceCallEngine()
_stt = TelephonySTTService()
_tts = TelephonyTTSService()
_backend = TelephonyBackendClient()

_audio_buffers: Dict[str, bytearray] = {}
_chunk_counters: Dict[str, int] = {}


class TestCreateServiceRequest(BaseModel):
    telefono: str
    origen: str
    destino: Optional[str] = None
    call_uuid: Optional[str] = None


class InboundCallRequest(BaseModel):
    call_uuid: str
    caller_number: Optional[str] = None
    destination_number: Optional[str] = None
    sip_headers: Optional[Dict[str, Any]] = None


class ProcessTextRequest(BaseModel):
    call_uuid: str
    text: str
    confidence: float = 1.0
    digits: str = ""


async def _parse_inbound_body(request: Request) -> dict:
    """Acepta JSON o form-urlencoded (curl desde dialplan FreeSWITCH)."""
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        return await request.json()
    form = await request.form()
    body = dict(form)
    sip_raw = body.get("sip_headers")
    if isinstance(sip_raw, str) and sip_raw.strip().startswith("{"):
        try:
            body["sip_headers"] = json.loads(sip_raw)
        except json.JSONDecodeError:
            pass
    return body


@freeswitch_router.get("/health")
async def freeswitch_health():
    store = get_session_store()
    redis_ok = None
    if settings.VOICE_SESSION_STORE == "redis" and settings.REDIS_URL:
        try:
            import redis

            redis.from_url(settings.REDIS_URL).ping()
            redis_ok = True
        except Exception as e:
            redis_ok = False
            logger.warning("[freeswitch/health] redis ping failed: %s", e)

    return {
        "ok": True,
        "service": "lyra-freeswitch-gateway",
        "backend_api": settings.INTELLITAXI_API_BASE,
        "session_store": settings.VOICE_SESSION_STORE,
        "redis_ok": redis_ok,
        "stt_provider": settings.TELEPHONY_STT_PROVIDER,
        "stt_available": _stt.available,
        "tts_voice": settings.LYRA_TTS_VOICE,
        "audio_codec": settings.TELEPHONY_AUDIO_CODEC,
        "ws_audio_url": settings.FREESWITCH_WS_AUDIO_URL,
        "active_sessions": store.active_count(),
        "twilio_fallback_active": True,
    }


@freeswitch_router.post("/test-create-service")
async def test_create_service(req: TestCreateServiceRequest, request: Request):
    telefono = limpiar_numero(req.telefono)
    http_client = getattr(request.app.state, "http_client", None)

    ok, msg = await _backend.create_service_from_geocoded(
        celular=telefono,
        origen=req.origen,
        destino=req.destino,
        call_uuid=req.call_uuid or "test-manual",
        use_freeswitch_channel=True,
        http_client=http_client,
    )

    logger.info(
        "[freeswitch] test-create-service ok=%s call_uuid=%s telefono=%s origen=%r",
        ok,
        req.call_uuid,
        telefono,
        req.origen,
    )

    return {
        "success": ok,
        "message": msg,
        "telefono": telefono,
        "origen": req.origen,
        "destino": req.destino,
        "canal_origen": TelephonyBackendClient.FREESWITCH_CHANNEL,
        "call_uuid": req.call_uuid or "test-manual",
    }


@freeswitch_router.post("/inbound-call")
async def inbound_call(request: Request):
    raw = await _parse_inbound_body(request)
    req = InboundCallRequest(
        call_uuid=str(raw.get("call_uuid") or raw.get("uuid") or ""),
        caller_number=raw.get("caller_number") or raw.get("caller_id_number"),
        destination_number=raw.get("destination_number") or raw.get("destination_number"),
        sip_headers=raw.get("sip_headers") if isinstance(raw.get("sip_headers"), dict) else None,
    )
    if not req.call_uuid:
        return {"success": False, "error": "call_uuid required"}

    store = get_session_store()
    caller, source = resolve_caller_phone(req.caller_number, req.sip_headers)

    session = store.get_or_create(
        call_uuid=req.call_uuid,
        caller_phone=caller,
        destination_number=req.destination_number,
        sip_metadata=req.sip_headers or {},
    )

    logger.info(
        "[freeswitch] inbound-call call_uuid=%s caller=%s source=%s dest=%s",
        req.call_uuid,
        caller,
        source,
        req.destination_number,
    )

    turn = _engine.handle_inbound(session)
    store.save(turn.session or session)

    tts_result = await _tts.synthesize_for_telephony(turn.speak_text)
    audio_b64 = ""
    if tts_result.get("mulaw"):
        audio_b64 = base64.b64encode(tts_result["mulaw"]).decode("ascii")
    elif tts_result.get("mp3"):
        audio_b64 = base64.b64encode(tts_result["mp3"]).decode("ascii")

    return {
        "success": True,
        "call_uuid": req.call_uuid,
        "caller_phone": caller,
        "caller_source": source,
        "speak_text": turn.speak_text,
        "action": turn.action.value,
        "state": session.state,
        "audio_base64": audio_b64,
        "audio_format": tts_result.get("format", "mp3"),
        "ws_audio_url": settings.FREESWITCH_WS_AUDIO_URL,
    }


@freeswitch_router.post("/process-text")
async def process_text(req: ProcessTextRequest, request: Request):
    store = get_session_store()
    http_client = getattr(request.app.state, "http_client", None)

    result = await process_text_turn(
        store,
        req.call_uuid,
        user_text=req.text,
        confidence=req.confidence,
        digits=req.digits,
        http_client=http_client,
        create_session_if_missing=True,
    )
    return result


@freeswitch_router.websocket("/audio")
async def audio_stream(websocket: WebSocket):
    """
    WebSocket mod_audio_stream (FreeSWITCH) ↔ Lyra.

    Eventos: connected | start | media | stop
    Respuesta: JSON con speak_text, action, audio_base64 (µ-law o mp3)
    """
    await websocket.accept()
    call_uuid: Optional[str] = None

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")

            if event == "connected":
                logger.info("[freeswitch/ws] connected")

            elif event == "start":
                start = data.get("start", {})
                call_uuid = (
                    start.get("callId")
                    or start.get("call_uuid")
                    or start.get("customParameters", {}).get("call_uuid")
                )
                if call_uuid:
                    _audio_buffers[call_uuid] = bytearray()
                    _chunk_counters[call_uuid] = 0
                logger.info("[freeswitch/ws] start call_uuid=%s", call_uuid)

            elif event == "media" and call_uuid:
                payload = data.get("media", {}).get("payload", "")
                if not payload:
                    continue
                chunk = base64.b64decode(payload)
                buf = _audio_buffers.setdefault(call_uuid, bytearray())
                buf.extend(chunk)
                _chunk_counters[call_uuid] = _chunk_counters.get(call_uuid, 0) + 1

                end, _ = detect_end_of_utterance(bytes(buf))
                if end and len(buf) > 3200:
                    await _flush_audio_turn(websocket, call_uuid, buf)
                    _audio_buffers[call_uuid] = bytearray()
                    _chunk_counters[call_uuid] = 0

            elif event == "stop":
                logger.info("[freeswitch/ws] stop call_uuid=%s", call_uuid)
                if call_uuid and call_uuid in _audio_buffers:
                    buf = _audio_buffers.pop(call_uuid)
                    if len(buf) > 1600:
                        await _flush_audio_turn(websocket, call_uuid, buf)
                break

    except WebSocketDisconnect:
        logger.info("[freeswitch/ws] disconnected call_uuid=%s", call_uuid)
    except Exception as e:
        logger.error("[freeswitch/ws] error call_uuid=%s err=%s", call_uuid, e)
    finally:
        if call_uuid:
            _audio_buffers.pop(call_uuid, None)
            _chunk_counters.pop(call_uuid, None)


async def _flush_audio_turn(
    websocket: WebSocket,
    call_uuid: str,
    audio_buf: bytearray,
) -> None:
    stt = await _stt.transcribe_mulaw_chunk(bytes(audio_buf), call_uuid=call_uuid)
    if not stt.get("success") or not stt.get("text"):
        logger.info("[freeswitch/ws] no speech call_uuid=%s", call_uuid)
        return

    app = websocket.scope.get("app")
    http_client = getattr(app.state, "http_client", None) if app else None
    store = get_session_store()

    response = await process_stt_turn(
        store,
        call_uuid,
        recognized_text=stt["text"],
        confidence=stt.get("confidence", 0.0),
        http_client=http_client,
    )

    await websocket.send_text(json.dumps(response, ensure_ascii=False))
    logger.info(
        "[freeswitch/ws] turn call_uuid=%s action=%s text=%r hangup=%s",
        call_uuid,
        response.get("action"),
        stt["text"][:80],
        response.get("hangup"),
    )
