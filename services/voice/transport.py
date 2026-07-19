"""Transporte FreeSWITCH ↔ Lyra vía mod_audio_stream (spec §3.1).

Entrada: frames del WS de mod_audio_stream — binarios (PCM16 8k mono, pata
del llamante) o texto (JSON de protocolo: connected/start/media/stop).
Salida: playback full-duplex con mensajes `streamAudio` (formato documentado
del módulo):

    {"type": "streamAudio",
     "data": {"audioDataType": "raw", "sampleRate": 8000,
              "audioData": "<base64 pcm16>"}}

No hay comando de kill de playback en el módulo open-source: la cancelación
de barge-in se logra empujando chunks pequeños con pacing casi-real-time
(runtime), de modo que "dejar de enviar" corta el audio en ≤~400 ms.

Sin gate de reproducción: el audio del usuario NUNCA se descarta aquí
(el anti-patrón half-duplex de V1 queda eliminado; el eco lo maneja el AEC).
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from fastapi import WebSocket, WebSocketDisconnect

from services.telephony.phone_utils import limpiar_numero

logger = logging.getLogger("lyra.voice.transport")

SAMPLE_RATE = 8000


@dataclass
class StreamStart:
    metadata: dict


@dataclass
class AudioFrame:
    pcm: bytes


@dataclass
class StreamStop:
    pass


TransportEvent = StreamStart | AudioFrame | StreamStop


def resolve_call_uuid(query_params: Any, headers: Any, data: Optional[dict] = None) -> Optional[str]:
    """call_uuid desde query string, headers o metadata JSON del protocolo."""
    for key in ("call_uuid", "uuid", "callId", "call_id"):
        val = query_params.get(key)
        if val:
            return str(val)
    for header in ("x-call-uuid", "call-uuid", "x-freeswitch-uuid"):
        val = headers.get(header)
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


def resolve_caller_number(query_params: Any, headers: Any, data: Optional[dict] = None) -> Optional[str]:
    """Número del llamante desde query string, headers o metadata JSON."""
    for key in ("caller_number", "caller_id_number", "caller", "from"):
        val = query_params.get(key)
        if val:
            cleaned = limpiar_numero(str(val))
            if cleaned:
                return cleaned
    for header in ("x-caller-number", "caller-number", "x-caller-id"):
        val = headers.get(header)
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
        custom = start.get("customParameters") or {}
        if isinstance(custom, dict):
            for key in ("caller_number", "caller"):
                val = custom.get(key)
                if val:
                    cleaned = limpiar_numero(str(val))
                    if cleaned:
                        return cleaned
    return None


def build_stream_audio_message(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
    """Mensaje de playback de mod_audio_stream (audio crudo base64)."""
    return json.dumps(
        {
            "type": "streamAudio",
            "data": {
                "audioDataType": "raw",
                "sampleRate": sample_rate,
                "audioData": base64.b64encode(pcm).decode("ascii"),
            },
        }
    )


@dataclass
class FreeSwitchTransport:
    """Sesión WS con mod_audio_stream para una llamada."""

    websocket: WebSocket
    call_uuid: Optional[str] = None
    caller_number: Optional[str] = None
    _closed: bool = field(default=False, init=False)

    def resolve_identity(self, data: Optional[dict] = None) -> None:
        if not self.call_uuid:
            self.call_uuid = resolve_call_uuid(
                self.websocket.query_params, self.websocket.headers, data
            )
        if not self.caller_number:
            self.caller_number = resolve_caller_number(
                self.websocket.query_params, self.websocket.headers, data
            )

    async def events(self) -> AsyncIterator[TransportEvent]:
        """Itera eventos del WS hasta stop/desconexión."""
        while True:
            try:
                msg = await self.websocket.receive()
            except (WebSocketDisconnect, RuntimeError):
                return

            if msg.get("type") == "websocket.disconnect":
                return

            if msg.get("bytes") is not None:
                pcm = msg["bytes"]
                if pcm:
                    yield AudioFrame(pcm=pcm)
                continue

            text = msg.get("text")
            if text is None:
                continue
            stripped = text.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                logger.debug("[transport] non-json text ignored len=%d", len(stripped))
                continue
            if not isinstance(data, dict):
                continue

            event = data.get("event") or data.get("type")
            self.resolve_identity(data)

            if event == "start":
                logger.info(
                    "[transport] stream start call_uuid=%s caller=%s",
                    self.call_uuid,
                    self.caller_number,
                )
                yield StreamStart(metadata=data)
            elif event == "media":
                payload = (data.get("media") or {}).get("payload", "")
                if payload:
                    try:
                        yield AudioFrame(pcm=base64.b64decode(payload))
                    except Exception as e:
                        logger.warning("[transport] media b64 decode failed: %s", e)
            elif event == "stop":
                logger.info("[transport] stream stop call_uuid=%s", self.call_uuid)
                yield StreamStop()
                return
            elif event == "connected":
                logger.info("[transport] protocol connected call_uuid=%s", self.call_uuid)

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        try:
            await self.websocket.send_text(build_stream_audio_message(pcm))
        except Exception as e:
            if not self._closed:
                logger.warning(
                    "[transport] send_audio failed call_uuid=%s err=%s",
                    self.call_uuid,
                    e,
                )
                self._closed = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.websocket.close()
        except Exception:
            pass

    @property
    def closed(self) -> bool:
        return self._closed
