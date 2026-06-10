"""
Proveedor FreeSWITCH vía WebSocket (mod_audio_stream).

Recibe audio µ-law 8 kHz, STT propio, motor conversacional, TTS edge-tts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from core.voice_engine import get_voice_engine
from services.telephony.audio_codec import SAMPLE_WIDTH, mp3_to_ulaw, pcm_rms, ulaw_to_wav_bytes

try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # type: ignore
from services.telephony.conversation_engine import (
    STATE_CREATING_SERVICE,
    get_session,
    handle_call_start,
    process_turn,
    reset_session,
)
from services.telephony.log_utils import mask_phone
from services.telephony.types import TurnResult

logger = logging.getLogger("lyra.telephony.freeswitch")

FS_LOG = "[FREESWITCH]"


def _cfg(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _lyra_tts_voice() -> str:
    return _cfg("LYRA_TTS_VOICE", "es-BO-SofiaNeural")


def _parse_caller_id(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    match = re.search(r"\+?\d{7,15}", str(raw))
    if not match:
        return None
    numero = match.group(0)
    if numero.startswith("+57") and len(numero) == 13:
        return numero
    if len(numero) == 10 and numero.startswith("3"):
        return "+57" + numero
    if len(numero) == 12 and numero.startswith("57"):
        return "+" + numero
    return numero if numero.startswith("+") else numero


@dataclass
class FreeswitchCallSession:
    call_id: str
    caller_id: Optional[str] = None
    destination_number: Optional[str] = None
    did_number: Optional[str] = None
    sample_rate: int = 8000
    started_at: float = field(default_factory=time.time)
    audio_buffer: bytearray = field(default_factory=bytearray)
    is_speaking: bool = False
    silence_frames: int = 0
    speech_frames: int = 0
    is_playing_tts: bool = False
    barge_in_enabled: bool = True
    http_client: Any = None
    ended: bool = False

    # Umbrales VAD (configurables por env)
    silence_threshold: int = field(default_factory=lambda: _cfg_int("FS_VAD_SILENCE_RMS", 400))
    silence_frames_end: int = field(default_factory=lambda: _cfg_int("FS_VAD_SILENCE_FRAMES", 25))
    min_speech_frames: int = field(default_factory=lambda: _cfg_int("FS_VAD_MIN_SPEECH_FRAMES", 8))
    max_buffer_sec: float = field(default_factory=lambda: _cfg_float("FS_MAX_UTTERANCE_SEC", 12.0))


class FreeswitchProvider:
    """Maneja una sesión WebSocket mod_audio_stream ↔ Lyra."""

    def __init__(self, websocket: WebSocket, http_client=None):
        self.ws = websocket
        self.http_client = http_client
        self.call: Optional[FreeswitchCallSession] = None

    def _log(self, event: str, **kwargs):
        session_state = None
        if self.call:
            try:
                session_state = get_session(self.call.call_id).state
            except Exception:
                pass
        safe_kwargs = dict(kwargs)
        for phone_key in ("caller_id", "destination_number", "did_number", "from", "to"):
            if phone_key in safe_kwargs and safe_kwargs[phone_key]:
                safe_kwargs[phone_key] = mask_phone(str(safe_kwargs[phone_key]))
        payload = {
            "event": event,
            "call_id": self.call.call_id if self.call else None,
            "caller_id": mask_phone(self.call.caller_id) if self.call and self.call.caller_id else None,
            "destination_number": mask_phone(self.call.destination_number) if self.call and self.call.destination_number else None,
            "did_number": mask_phone(self.call.did_number) if self.call and self.call.did_number else None,
            "session_state": session_state,
            "ts": time.time(),
            **safe_kwargs,
        }
        logger.info(f"{FS_LOG} {event} {json.dumps({k: v for k, v in payload.items() if v is not None}, default=str)}")

    async def run(self) -> None:
        await self.ws.accept()
        self._log("ws_connected")
        try:
            while True:
                raw = await self.ws.receive_text()
                await self._handle_message(raw)
                if self.call and self.call.ended:
                    break
        except WebSocketDisconnect:
            self._log("ws_disconnected")
        except Exception as e:
            self._log("error", error=str(e))
            logger.exception(f"{FS_LOG} session error")
        finally:
            await self._end_call("session_closed")

    async def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._log("error", error="invalid_json")
            return

        event = data.get("event") or data.get("type", "")

        if event == "connected":
            self._log("audio_stream_connected")
            return

        if event == "start":
            await self._on_start(data)
            return

        if event == "media":
            await self._on_media(data)
            return

        if event == "stop":
            await self._end_call("stop_event")
            return

        # Algunos mod_audio_stream envían callId en el root
        if event == "dtmf" or data.get("dtmf"):
            digit = str(data.get("dtmf", {}).get("digit", "") or data.get("digit", ""))
            if digit and self.call:
                await self._process_dtmf(digit)
            return

    def _extract_custom_params(self, start: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        custom = start.get("customParameters") or start.get("custom_parameters") or {}
        if not isinstance(custom, dict):
            custom = {}
        # Algunos bridges envían metadata en el root del evento start
        for key in (
            "caller_id_number",
            "destination_number",
            "variable_sip_from_user",
            "variable_sip_to_user",
            "variable_sip_req_user",
            "variable_destination_number",
        ):
            if key in start and start[key] and key not in custom:
                custom[key] = start[key]
        return custom

    async def _on_start(self, data: Dict[str, Any]) -> None:
        start = data.get("start") or data
        custom = self._extract_custom_params(start, data)
        call_id = (
            start.get("callId")
            or start.get("uuid")
            or start.get("call_id")
            or data.get("callId")
            or data.get("uuid")
            or f"fs-{int(time.time())}"
        )
        caller_raw = (
            start.get("from")
            or start.get("caller")
            or start.get("callerId")
            or start.get("caller_id_number")
            or custom.get("caller_id_number")
            or custom.get("variable_sip_from_user")
            or data.get("caller")
            or data.get("from")
        )
        destination_raw = (
            start.get("to")
            or start.get("destination")
            or start.get("destination_number")
            or custom.get("destination_number")
            or custom.get("variable_destination_number")
            or custom.get("variable_sip_to_user")
            or custom.get("variable_sip_req_user")
            or data.get("to")
            or data.get("destination_number")
        )
        did_raw = _cfg("SIP_DID_NUMBER") or _cfg("SIP_EXTENSION") or destination_raw
        sample_rate = int(
            (start.get("mediaFormat") or {}).get("sampleRate")
            or start.get("sampleRate")
            or 8000
        )

        self.call = FreeswitchCallSession(
            call_id=str(call_id),
            caller_id=_parse_caller_id(caller_raw),
            destination_number=_parse_caller_id(destination_raw),
            did_number=_parse_caller_id(did_raw),
            sample_rate=sample_rate,
            http_client=self.http_client,
        )
        self._log("call_started", sample_rate=sample_rate)
        if custom:
            self._log(
                "sip_headers_detected",
                keys=list(custom.keys()),
                caller_id_number=mask_phone(str(custom.get("caller_id_number", ""))),
                destination_number=mask_phone(str(custom.get("destination_number", ""))),
            )
        if self.call.caller_id:
            self._log("caller_id_detected", caller_id=self.call.caller_id)
        if self.call.destination_number:
            self._log("destination_number_detected", destination_number=self.call.destination_number)
        if self.call.did_number:
            self._log("did_number_detected", did_number=self.call.did_number)

        turn = await handle_call_start(self.call.call_id, self.call.caller_id)
        await self._speak_turn(turn)

    async def _on_media(self, data: Dict[str, Any]) -> None:
        if not self.call or self.call.ended:
            return

        if self.call.is_playing_tts and self.call.barge_in_enabled:
            # Barge-in: si hay voz del usuario durante TTS, cortar reproducción
            payload_b64 = (data.get("media") or {}).get("payload", "")
            if payload_b64:
                ulaw = base64.b64decode(payload_b64)
                pcm = audioop.ulaw2lin(ulaw, SAMPLE_WIDTH)
                if pcm_rms(pcm) > self.call.silence_threshold * 2:
                    self.call.is_playing_tts = False
                    self._log("barge_in_detected")

        if self.call.is_playing_tts:
            return

        payload_b64 = (data.get("media") or {}).get("payload", "")
        if not payload_b64:
            return

        ulaw = base64.b64decode(payload_b64)
        self.call.audio_buffer.extend(ulaw)
        self._log("audio_received", bytes=len(ulaw), buffer=len(self.call.audio_buffer))

        pcm = audioop.ulaw2lin(ulaw, SAMPLE_WIDTH)
        rms = pcm_rms(pcm)

        if rms > self.call.silence_threshold:
            self.call.is_speaking = True
            self.call.speech_frames += 1
            self.call.silence_frames = 0
        else:
            if self.call.is_speaking:
                self.call.silence_frames += 1

        max_bytes = int(self.call.sample_rate * self.call.max_buffer_sec)
        if len(self.call.audio_buffer) >= max_bytes:
            await self._flush_stt("max_buffer")

        elif (
            self.call.is_speaking
            and self.call.speech_frames >= self.call.min_speech_frames
            and self.call.silence_frames >= self.call.silence_frames_end
        ):
            await self._flush_stt("end_of_utterance")

    async def _flush_stt(self, reason: str) -> None:
        if not self.call or len(self.call.audio_buffer) < 160:
            return

        buffer = bytes(self.call.audio_buffer)
        self.call.audio_buffer.clear()
        self.call.is_speaking = False
        self.call.silence_frames = 0
        self.call.speech_frames = 0

        self._log("stt_started", reason=reason, bytes=len(buffer))

        wav = ulaw_to_wav_bytes(buffer, self.call.sample_rate)
        engine = get_voice_engine()
        if not engine.stt_available:
            self._log("error", error="stt_not_configured")
            await self._speak_text("No puedo escucharte en este momento. Configura el servicio de voz.")
            return

        result = await engine.transcribe(
            audio_bytes=wav,
            language="es",
            content_type="audio/wav",
        )

        text = (result.get("text") or "").strip()
        confidence = float(result.get("confidence", 1.0))

        self._log("stt_result", text=text[:120], confidence=confidence, success=result.get("success"))

        if not result.get("success") or not text:
            turn = await process_turn(
                call_id=self.call.call_id,
                texto_usuario="",
                confidence=0.0,
                caller_id=self.call.caller_id,
                http_client=self.call.http_client,
            )
        else:
            turn = await process_turn(
                call_id=self.call.call_id,
                texto_usuario=text,
                confidence=confidence,
                caller_id=self.call.caller_id,
                http_client=self.call.http_client,
            )

        await self._speak_turn(turn)

    async def _process_dtmf(self, digit: str) -> None:
        self._log("dtmf_received", digit=digit)
        turn = await process_turn(
            call_id=self.call.call_id,
            digits=digit,
            caller_id=self.call.caller_id,
            http_client=self.call.http_client,
        )
        await self._speak_turn(turn)

    async def _speak_turn(self, turn: TurnResult) -> None:
        if turn.processing_message:
            await self._speak_text(turn.processing_message)

        if turn.speak:
            self._log("conversation_response", text=turn.speak[:120])
            await self._speak_text(turn.speak)

        # FreeSWITCH: encadenar turno automático tras mensaje de espera (creating_service).
        if self.call and not turn.hangup:
            sess = get_session(self.call.call_id)
            if sess.state == STATE_CREATING_SERVICE:
                self._log("creating_service_auto_turn")
                follow = await process_turn(
                    call_id=self.call.call_id,
                    texto_usuario="",
                    caller_id=self.call.caller_id,
                    http_client=self.call.http_client,
                )
                await self._speak_turn(follow)
                return

        if turn.reset_session:
            reset_session(self.call.call_id if self.call else "")

        if turn.hangup:
            await self._end_call("hangup")

    async def _speak_text(self, text: str) -> None:
        if not self.call or not text.strip():
            return

        self._log("tts_generated", chars=len(text))
        try:
            engine = get_voice_engine()
            mp3 = await engine.synthesize_to_bytes(text, voice=_lyra_tts_voice())
        except Exception as e:
            self._log("error", error=f"tts_failed:{e}")
            return

        if not mp3:
            self._log("error", error="tts_empty")
            return

        ulaw = mp3_to_ulaw(mp3)
        if not ulaw:
            self._log("error", error="audio_conversion_failed")
            return

        self.call.is_playing_tts = True
        await self._send_audio_ulaw(ulaw)
        self.call.is_playing_tts = False
        self._log("audio_sent", bytes=len(ulaw))

    async def _send_audio_ulaw(self, ulaw: bytes) -> None:
        """Envía audio µ-law a FreeSWITCH (chunks para no saturar WS)."""
        chunk_size = 320  # 20 ms @ 8kHz
        for i in range(0, len(ulaw), chunk_size):
            chunk = ulaw[i : i + chunk_size]
            payload = base64.b64encode(chunk).decode("ascii")
            msg = json.dumps({
                "type": "streamAudio",
                "data": {
                    "audioDataType": "raw",
                    "sampleRate": self.call.sample_rate if self.call else 8000,
                    "audioData": payload,
                },
            })
            await self.ws.send_text(msg)
            await asyncio.sleep(0.018)

    async def _end_call(self, reason: str) -> None:
        if not self.call or self.call.ended:
            return
        self.call.ended = True
        duration = round(time.time() - self.call.started_at, 1)
        self._log("call_ended", reason=reason, duration_sec=duration)
        reset_session(self.call.call_id)
        try:
            await self.ws.close()
        except Exception:
            pass
