"""
api/routers/freeswitch.py — Integración directa FreeSWITCH ↔ Lyra (sin Twilio).
"""

from __future__ import annotations

import base64
import json
import logging
import traceback
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


def _ws_resolve_call_uuid(
    current: Optional[str],
    *,
    query_params: Any,
    data: Optional[dict] = None,
) -> Optional[str]:
    """Resuelve call_uuid desde query string o metadata JSON."""
    if current:
        return current

    for key in ("call_uuid", "uuid", "callId", "call_id"):
        val = query_params.get(key)
        if val:
            return str(val)

    if not data:
        return None

    for key in ("call_uuid", "uuid", "callId", "call_id"):
        val = data.get(key)
        if val:
            return str(val)

    start = data.get("start") or {}
    if isinstance(start, dict):
        for key in ("callId", "call_uuid", "uuid"):
            val = start.get(key)
            if val:
                return str(val)
        custom = start.get("customParameters") or {}
        if isinstance(custom, dict):
            val = custom.get("call_uuid") or custom.get("uuid")
            if val:
                return str(val)

    return None


def _ws_resolve_caller_number(
    current: Optional[str],
    *,
    query_params: Any,
    data: Optional[dict] = None,
) -> Optional[str]:
    """Resuelve caller_number desde query string o metadata JSON."""
    if current:
        return current

    for key in ("caller_number", "caller_id_number", "caller", "from"):
        val = query_params.get(key)
        if val:
            cleaned = limpiar_numero(str(val))
            if cleaned:
                return cleaned

    if not data:
        return None

    for key in ("caller_number", "caller_id_number", "caller", "from"):
        val = data.get(key)
        if val:
            cleaned = limpiar_numero(str(val))
            if cleaned:
                return cleaned

    start = data.get("start") or {}
    if isinstance(start, dict):
        for key in ("caller_number", "caller_id_number", "from"):
            val = start.get(key)
            if val:
                cleaned = limpiar_numero(str(val))
                if cleaned:
                    return cleaned

    return None


def _ws_ensure_session(call_uuid: str, caller_number: Optional[str]) -> None:
    """Vincula sesión si inbound-call ya la creó o la crea aquí."""
    store = get_session_store()
    session = store.get_or_create(call_uuid, caller_phone=caller_number)
    if caller_number and not session.caller_phone:
        session.caller_phone = caller_number
        store.save(session)


async def _ws_handle_text_metadata(
    text: str,
    *,
    call_uuid: Optional[str],
    caller_number: Optional[str],
    websocket: WebSocket,
) -> tuple[Optional[str], Optional[str], bool]:
    """
    Procesa frame text (JSON metadata o control).
    Returns: (call_uuid, caller_number, should_stop)
    """
    stripped = text.strip()
    if not stripped:
        return call_uuid, caller_number, False

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        logger.debug("[freeswitch/ws] non-json text ignored len=%d", len(stripped))
        return call_uuid, caller_number, False

    logger.info(
        "[freeswitch/ws] metadata text received keys=%s",
        list(data.keys()) if isinstance(data, dict) else type(data).__name__,
    )

    if not isinstance(data, dict):
        return call_uuid, caller_number, False

    event = data.get("event")
    qp = {}  # query already applied; metadata may refine ids

    new_uuid = _ws_resolve_call_uuid(call_uuid, query_params=qp, data=data)
    new_caller = _ws_resolve_caller_number(caller_number, query_params=qp, data=data)

    if new_uuid and new_uuid != call_uuid:
        logger.info("[freeswitch/ws] call_uuid resolved=%s (event=%s)", new_uuid, event)
        call_uuid = new_uuid
        _audio_buffers.setdefault(call_uuid, bytearray())
        _chunk_counters.setdefault(call_uuid, 0)

    if call_uuid:
        _ws_ensure_session(call_uuid, new_caller)
        if new_caller:
            caller_number = new_caller

    if event == "connected":
        logger.info("[freeswitch/ws] protocol connected call_uuid=%s", call_uuid)

    elif event == "start":
        logger.info("[freeswitch/ws] stream start call_uuid=%s caller=%s", call_uuid, caller_number)

    elif event == "media" and call_uuid:
        payload = (data.get("media") or {}).get("payload", "")
        if payload:
            try:
                chunk = base64.b64decode(payload)
                await _ws_append_audio(call_uuid, chunk, websocket=websocket)
            except Exception as e:
                logger.warning("[freeswitch/ws] media b64 decode failed: %s", e)

    elif event == "stop":
        logger.info("[freeswitch/ws] stream stop call_uuid=%s", call_uuid)
        return call_uuid, caller_number, True

    return call_uuid, caller_number, False


_first_audio_logged: set[str] = set()


async def _ws_append_audio(
    call_uuid: str,
    chunk: bytes,
    *,
    websocket: Optional[WebSocket],
) -> None:
    """Acumula audio y dispara STT al detectar fin de turno (VAD)."""
    if not chunk:
        return

    if call_uuid not in _first_audio_logged:
        _first_audio_logged.add(call_uuid)
        logger.info(
            "[freeswitch/ws] audio bytes received first time call_uuid=%s len=%d",
            call_uuid,
            len(chunk),
        )

    buf = _audio_buffers.setdefault(call_uuid, bytearray())
    buf.extend(chunk)
    _chunk_counters[call_uuid] = _chunk_counters.get(call_uuid, 0) + 1

    # VAD cada ~50 chunks para no saturar CPU/logs
    if _chunk_counters[call_uuid] % 50 != 0:
        return

    end, _ = detect_end_of_utterance(bytes(buf))
    if end and len(buf) > 3200 and websocket is not None:
        await _flush_audio_turn(websocket, call_uuid, buf)
        _audio_buffers[call_uuid] = bytearray()
        _chunk_counters[call_uuid] = 0


@freeswitch_router.websocket("/audio")
async def audio_stream(websocket: WebSocket):
    """
    WebSocket mod_audio_stream (FreeSWITCH) ↔ Lyra.

    Acepta frames text (JSON metadata) y bytes (audio µ-law raw).
    Respuesta: JSON con speak_text, action, audio_base64 (µ-law o mp3)
    """
    await websocket.accept()
    logger.info("[freeswitch/ws] websocket accepted")

    call_uuid = _ws_resolve_call_uuid(
        None, query_params=websocket.query_params, data=None
    )
    caller_number = _ws_resolve_caller_number(
        None, query_params=websocket.query_params, data=None
    )

    if not call_uuid:
        for header in ("x-call-uuid", "call-uuid", "x-freeswitch-uuid"):
            val = websocket.headers.get(header)
            if val:
                call_uuid = str(val)
                logger.info("[freeswitch/ws] call_uuid from header %s", header)
                break

    if not caller_number:
        for header in ("x-caller-number", "caller-number", "x-caller-id"):
            val = websocket.headers.get(header)
            if val:
                caller_number = limpiar_numero(str(val))
                if caller_number:
                    break

    if call_uuid:
        logger.info(
            "[freeswitch/ws] call_uuid from query=%s caller=%s",
            call_uuid,
            caller_number,
        )
        _audio_buffers.setdefault(call_uuid, bytearray())
        _chunk_counters.setdefault(call_uuid, 0)
        _ws_ensure_session(call_uuid, caller_number)

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("text") is not None:
                call_uuid, caller_number, should_stop = await _ws_handle_text_metadata(
                    msg["text"],
                    call_uuid=call_uuid,
                    caller_number=caller_number,
                    websocket=websocket,
                )
                if should_stop:
                    if call_uuid and call_uuid in _audio_buffers:
                        buf = _audio_buffers.pop(call_uuid)
                        if len(buf) > 1600:
                            await _flush_audio_turn(websocket, call_uuid, buf)
                    break
                continue

            if msg.get("bytes") is not None:
                chunk = msg["bytes"]
                if not call_uuid:
                    logger.warning(
                        "[freeswitch/ws] audio bytes before call_uuid resolved len=%d — ignored",
                        len(chunk),
                    )
                    continue
                await _ws_append_audio(call_uuid, chunk, websocket=websocket)
                continue

    except WebSocketDisconnect:
        logger.info("[freeswitch/ws] websocket disconnected call_uuid=%s", call_uuid)
    except Exception:
        logger.error(
            "[freeswitch/ws] error call_uuid=%s traceback=%s",
            call_uuid,
            traceback.format_exc(),
        )
    finally:
        if call_uuid:
            _audio_buffers.pop(call_uuid, None)
            _chunk_counters.pop(call_uuid, None)
            _first_audio_logged.discard(call_uuid)
        logger.info("[freeswitch/ws] websocket closed call_uuid=%s", call_uuid)


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
