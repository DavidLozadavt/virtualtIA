"""Transporte FreeSWITCH ↔ Lyra vía mod_audio_stream — captura del usuario.

Entrada: frames del WS de mod_audio_stream — binarios (PCM16 8k mono, pata
del llamante) o texto (JSON de protocolo: connected/start/media/stop). Este
WS se usa SOLO para captura (full-duplex real: el audio del usuario nunca se
descarta ni se gatea, a diferencia del anti-patrón half-duplex de V1).

Playback: NO vía este WS. `mod_audio_stream` v1.0.3 (binario oficial,
licencia gratuita <10 canales) documenta reproducción bidireccional vía
mensajes `streamAudio`, pero en pruebas reales (2026-07-19, logs de
FreeSWITCH + evento `mod_audio_stream::play` confirmando recepción de cada
chunk) nunca inyecta audio en el canal — sin `chunk_played`/`queue_completed`
ni audio audible, pese a seguir la documentación al pie de la letra
(`STREAM_PLAYBACK`, formato JSON, chunking a 20ms). Pendiente de soporte del
vendor. Mientras tanto el playback usa el mecanismo probado de V1: WAV local
+ ESL `uuid_broadcast` (ver `services/voice/audio_file_store.py` y
`runtime.py`).
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
