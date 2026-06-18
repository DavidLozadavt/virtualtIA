"""
api/routers/freeswitch.py — Integración directa FreeSWITCH ↔ Lyra (sin Twilio).
"""

from __future__ import annotations

import base64
import json
import logging
import traceback
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from core.config import settings
from services.telephony.backend_client import TelephonyBackendClient
from services.telephony.call_handler import process_text_turn, _restore_terminal_session
from services.telephony.esl_client import get_esl_client
from services.telephony.phone_utils import limpiar_numero, resolve_caller_phone
from services.telephony.session_store import STATE_FINISHED, get_session_store
from services.telephony.stt_service import TelephonySTTService
from services.telephony.tts_file_store import (
    build_audio_url,
    get_tts_file_store,
    sanitize_audio_id,
)
from services.telephony.tts_service import TelephonyTTSService
from services.telephony.voice_call_engine import VoiceCallEngine
from services.telephony.ws_audio_buffer import WsAudioBuffer, resolve_ws_encoding

logger = logging.getLogger("lyra.freeswitch")

freeswitch_router = APIRouter(prefix="/freeswitch", tags=["FreeSWITCH"])

_engine = VoiceCallEngine()
_stt = TelephonySTTService()
_tts = TelephonyTTSService()
_backend = TelephonyBackendClient()

_ws_audio: Dict[str, WsAudioBuffer] = {}


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
    source: Optional[str] = None  # entel | freeswitch | manual


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
        "stt_provider": _stt.provider,
        "stt_model": _stt.model,
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


def _inbound_use_file_mode(channel_source: Optional[str]) -> bool:
    """FreeSWITCH/Entel: respuesta liviana con audio_url, sin base64."""
    if not channel_source:
        return True
    return channel_source.strip().lower() in ("entel", "freeswitch", "fs", "dialplan")


def _resolve_audio_file_path(audio_id: str):
    clean_id = sanitize_audio_id(audio_id)
    store = get_tts_file_store()
    path = store.get_path(clean_id)
    if not path:
        raise HTTPException(status_code=404, detail="Audio file not found or expired")
    return clean_id, path


@freeswitch_router.get("/audio-file/{audio_id}")
async def serve_audio_file(audio_id: str):
    """
    Sirve WAV 8 kHz mono generado para playback en FreeSWITCH.
    URL ejemplo: /freeswitch/audio-file/abc123.wav
    """
    clean_id, path = _resolve_audio_file_path(audio_id)
    return FileResponse(
        path=str(path),
        media_type="audio/wav",
        filename=f"{clean_id}.wav",
        headers={"Cache-Control": "no-store"},
    )


@freeswitch_router.head("/audio-file/{audio_id}")
async def head_audio_file(audio_id: str):
    """HEAD para que FreeSWITCH valide existencia del WAV sin descargar cuerpo."""
    clean_id, path = _resolve_audio_file_path(audio_id)
    return Response(
        status_code=200,
        headers={
            "Content-Type": "audio/wav",
            "Content-Length": str(path.stat().st_size),
            "Cache-Control": "no-store",
            "Accept-Ranges": "bytes",
        },
    )


@freeswitch_router.post("/inbound-call")
async def inbound_call(request: Request):
    raw = await _parse_inbound_body(request)
    channel_source = str(raw.get("source") or raw.get("channel") or "freeswitch")
    req = InboundCallRequest(
        call_uuid=str(raw.get("call_uuid") or raw.get("uuid") or ""),
        caller_number=raw.get("caller_number") or raw.get("caller_id_number"),
        destination_number=raw.get("destination_number") or raw.get("destination_number"),
        sip_headers=raw.get("sip_headers") if isinstance(raw.get("sip_headers"), dict) else None,
        source=channel_source,
    )
    if not req.call_uuid:
        return {"success": False, "error": "call_uuid required"}

    store = get_session_store()
    caller, phone_source = resolve_caller_phone(req.caller_number, req.sip_headers)

    session = store.get_or_create(
        call_uuid=req.call_uuid,
        caller_phone=caller,
        destination_number=req.destination_number,
        sip_metadata=req.sip_headers or {},
    )

    logger.info(
        "[freeswitch] inbound-call created call_uuid=%s caller=%s phone_source=%s "
        "channel_source=%s dest=%s",
        req.call_uuid,
        caller,
        phone_source,
        channel_source,
        req.destination_number,
    )

    turn = _engine.handle_inbound(session)
    store.save(turn.session or session)

    tts_result = await _tts.synthesize_for_telephony(turn.speak_text)
    use_file = _inbound_use_file_mode(channel_source)

    if use_file:
        try:
            file_store = get_tts_file_store()
            audio_id, file_path = file_store.save_telephony_audio(
                tts_result,
                call_uuid=req.call_uuid,
                audio_id=sanitize_audio_id(req.call_uuid),
            )
            audio_url = build_audio_url(audio_id, request)
            response = {
                "success": True,
                "call_uuid": req.call_uuid,
                "caller_phone": caller,
                "caller_source": phone_source,
                "speak_text": turn.speak_text,
                "action": "play_then_listen",
                "state": session.state,
                "audio_id": audio_id,
                "audio_url": audio_url,
                "audio_format": "wav",
                "ws_audio_url": settings.FREESWITCH_WS_AUDIO_URL,
            }
            payload = json.dumps(response, ensure_ascii=False)
            logger.info(
                "[freeswitch] inbound-call response call_uuid=%s audio_url=%s "
                "file=%s response_bytes=%d",
                req.call_uuid,
                audio_url,
                file_path,
                len(payload.encode("utf-8")),
            )
            return Response(
                content=payload,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(
                "[freeswitch] inbound-call tts file failed call_uuid=%s err=%s",
                req.call_uuid,
                e,
            )
            return {
                "success": True,
                "call_uuid": req.call_uuid,
                "speak_text": turn.speak_text,
                "action": "play_then_listen",
                "audio_url": None,
                "error": "tts_file_generation_failed",
                "ws_audio_url": settings.FREESWITCH_WS_AUDIO_URL,
            }

    # Modo manual / pruebas: incluye base64 (no usar desde mod_curl en producción)
    audio_b64 = ""
    if tts_result.get("mulaw"):
        audio_b64 = base64.b64encode(tts_result["mulaw"]).decode("ascii")
    elif tts_result.get("mp3"):
        audio_b64 = base64.b64encode(tts_result["mp3"]).decode("ascii")

    response = {
        "success": True,
        "call_uuid": req.call_uuid,
        "caller_phone": caller,
        "caller_source": phone_source,
        "speak_text": turn.speak_text,
        "action": turn.action.value,
        "state": session.state,
        "audio_base64": audio_b64,
        "audio_format": tts_result.get("format", "mp3"),
        "ws_audio_url": settings.FREESWITCH_WS_AUDIO_URL,
    }
    logger.info(
        "[freeswitch] inbound-call manual mode response_bytes=%d",
        len(json.dumps(response).encode("utf-8")),
    )
    return response


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

    if new_uuid:
        if new_uuid != call_uuid:
            logger.info("[freeswitch/ws] call_uuid resolved=%s", new_uuid)
        call_uuid = new_uuid
        _ws_audio.setdefault(
            call_uuid,
            WsAudioBuffer(call_uuid=call_uuid, encoding=resolve_ws_encoding()),
        )

    if call_uuid:
        _ws_ensure_session(call_uuid, new_caller)
        if new_caller:
            caller_number = new_caller

    if event == "connected":
        logger.info("[freeswitch/ws] protocol connected call_uuid=%s", call_uuid)

    elif event == "start":
        enc_hint = None
        start = data.get("start") if isinstance(data.get("start"), dict) else {}
        media = start.get("mediaFormat") or data.get("mediaFormat") or {}
        if isinstance(media, dict):
            enc_hint = media.get("encoding") or media.get("codec")
        if call_uuid and call_uuid in _ws_audio:
            _ws_audio[call_uuid].encoding = resolve_ws_encoding(enc_hint)
        logger.info(
            "[freeswitch/ws] stream start call_uuid=%s caller=%s encoding=%s",
            call_uuid,
            caller_number,
            _ws_audio.get(call_uuid).encoding if call_uuid in _ws_audio else resolve_ws_encoding(enc_hint),
        )

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


def _get_ws_buffer(call_uuid: str) -> WsAudioBuffer:
    if call_uuid not in _ws_audio:
        _ws_audio[call_uuid] = WsAudioBuffer(
            call_uuid=call_uuid,
            encoding=resolve_ws_encoding(),
        )
    return _ws_audio[call_uuid]


async def _ws_append_audio(
    call_uuid: str,
    chunk: bytes,
    *,
    websocket: WebSocket,
) -> None:
    """Acumula audio y dispara STT al alcanzar duración mínima + silencio o máximo."""
    if not chunk:
        return

    acc = _get_ws_buffer(call_uuid)
    if not acc.first_chunk_logged:
        acc.first_chunk_logged = True
        logger.info(
            "[freeswitch/ws] audio bytes received first time call_uuid=%s len=%d encoding=%s",
            call_uuid,
            len(chunk),
            acc.encoding,
        )

    acc.append(chunk)
    should_flush, reason = acc.should_flush()
    if should_flush:
        audio_data = acc.take_and_reset()
        await _flush_audio_turn(websocket, call_uuid, audio_data, encoding=acc.encoding, reason=reason)


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
        logger.info("[freeswitch/ws] call_uuid resolved=%s", call_uuid)
        _get_ws_buffer(call_uuid)
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
                    if call_uuid and call_uuid in _ws_audio:
                        acc = _ws_audio[call_uuid]
                        if len(acc.buffer) >= acc.min_bytes // 2:
                            audio_data = acc.take_and_reset()
                            await _flush_audio_turn(
                                websocket,
                                call_uuid,
                                audio_data,
                                encoding=acc.encoding,
                                reason="stream_stop",
                            )
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
            _ws_audio.pop(call_uuid, None)
        logger.info("[freeswitch/ws] websocket closed call_uuid=%s", call_uuid)


async def _playback_tts_on_call(call_uuid: str, audio_url: str) -> bool:
    """Reproduce TTS en la llamada vía ESL uuid_broadcast usando URL HTTP."""
    if not settings.FREESWITCH_ESL_ENABLED:
        logger.warning("[freeswitch/ws] ESL disabled — skip playback call_uuid=%s", call_uuid)
        return False

    if not audio_url:
        logger.warning("[freeswitch/ws] playback skipped — no audio_url call_uuid=%s", call_uuid)
        return False

    esl = get_esl_client()
    ok = await esl.uuid_broadcast(call_uuid, audio_url, leg="aleg")
    if ok:
        logger.info("[freeswitch/ws] playback sent call_uuid=%s audio_url=%s", call_uuid, audio_url)
    else:
        logger.warning(
            "[freeswitch/ws] playback failed call_uuid=%s audio_url=%s",
            call_uuid,
            audio_url,
        )
    return ok


async def _flush_audio_turn(
    websocket: WebSocket,
    call_uuid: str,
    audio_buf: bytes,
    *,
    encoding: str,
    reason: str = "",
) -> None:
    if not audio_buf:
        return

    store = get_session_store()
    session = store.get(call_uuid) or _restore_terminal_session(store, call_uuid)
    if session and (session.service_created or session.state == STATE_FINISHED):
        logger.info(
            "[freeswitch/ws] skip stt — call already finished call_uuid=%s state=%s",
            call_uuid,
            session.state,
        )
        return

    logger.info(
        "[freeswitch/ws] stt start call_uuid=%s bytes=%d encoding=%s reason=%s",
        call_uuid,
        len(audio_buf),
        encoding,
        reason,
    )

    try:
        stt = await _stt.transcribe_telephony_chunk(
            audio_buf,
            encoding=encoding,
            call_uuid=call_uuid,
        )
    except Exception:
        logger.error(
            "[freeswitch/ws] stt error call_uuid=%s traceback=%s",
            call_uuid,
            traceback.format_exc(),
        )
        return

    transcript = (stt.get("text") or "").strip()
    if not stt.get("success") or not transcript:
        logger.info(
            "[freeswitch/ws] no speech call_uuid=%s err=%s",
            call_uuid,
            stt.get("error", "empty transcript"),
        )
        return

    logger.info(
        '[freeswitch/ws] transcript call_uuid=%s text="%s"',
        call_uuid,
        transcript[:200],
    )

    app = websocket.scope.get("app")
    http_client = getattr(app.state, "http_client", None) if app else None
    store = get_session_store()

    try:
        response = await process_text_turn(
            store,
            call_uuid,
            user_text=transcript,
            confidence=stt.get("confidence", 0.0),
            http_client=http_client,
            create_session_if_missing=True,
            file_playback=True,
            request=websocket,
        )
    except Exception:
        logger.error(
            "[freeswitch/ws] process error call_uuid=%s traceback=%s",
            call_uuid,
            traceback.format_exc(),
        )
        return

    logger.info(
        "[freeswitch/ws] process result state=%s action=%s backend_ok=%s",
        response.get("state"),
        response.get("action"),
        response.get("backend_ok"),
    )

    if response.get("backend_ok") is True:
        logger.info("[freeswitch/ws] backend_ok=true call_uuid=%s", call_uuid)

    audio_url = response.get("audio_url") or ""
    if audio_url:
        logger.info("[freeswitch/ws] tts generated audio_url=%s", audio_url)
        await _playback_tts_on_call(call_uuid, audio_url)

    if response.get("hangup"):
        if settings.FREESWITCH_ESL_ENABLED:
            await get_esl_client().uuid_kill(call_uuid)

    ws_payload = {
        k: v
        for k, v in response.items()
        if k not in ("file_path", "audio_base64")
    }
    try:
        await websocket.send_text(json.dumps(ws_payload, ensure_ascii=False))
    except Exception:
        logger.debug("[freeswitch/ws] ws notify failed call_uuid=%s", call_uuid)
