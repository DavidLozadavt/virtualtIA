"""api/routers/freeswitch.py — Gateway FreeSWITCH ↔ Lyra Voice V2.

Un único camino de voz: WebSocket full-duplex con mod_audio_stream
(WS /freeswitch/audio). El record-loop de V1 (inbound-call + audio-turn +
archivos WAV por turno) fue reemplazado por completo por el runtime
streaming (services/voice).

Contratos preservados (bucket A):
  - GET /freeswitch/recording/{call_uuid}.wav — grabación de la llamada para
    el panel del operador (ahora la escribe el propio runtime, mezclada
    server-side; ya no la sube FreeSWITCH).
  - POST /freeswitch/test-create-service — herramienta de operación que llama
    al backend IntelliTaxi con el contrato intacto.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import settings
from services.telephony.backend_client import TelephonyBackendClient
from services.telephony.phone_utils import limpiar_numero
from services.telephony.session_store import get_session_store
from services.voice.nlu import TurnNLU
from services.voice.orchestrator import GREETING, TurnOrchestrator
from services.voice.recorder import recording_path
from services.voice.runtime import VoiceCallRuntime
from services.voice.tts_stream import StreamingTTS

logger = logging.getLogger("lyra.freeswitch")

freeswitch_router = APIRouter(prefix="/freeswitch", tags=["FreeSWITCH"])

# Compartidos entre llamadas: la caché de frases TTS y las tareas NLU
# anticipadas viven a nivel de proceso, no por llamada.
_orchestrator = TurnOrchestrator()
_tts = StreamingTTS()
_nlu = TurnNLU()
_backend = TelephonyBackendClient()

_FIXED_PHRASES = [
    GREETING,
    "Un momento por favor...",
    "¡Hola! Con mucho gusto te ayudo. Cuéntame, ¿en dónde te recogemos?",
    "¿Sigues ahí? Dime dónde te recojo.",
    "¿Dónde estás en Popayán?",
    "No te escucho. Llámanos cuando puedas. ¡Hasta luego!",
    "Entendido. ¿Dónde queda exactamente? Puedes darme el barrio o la dirección.",
    (
        "Te enviaremos los datos del conductor por WhatsApp "
        "y en un momento él se comunica contigo. "
        "¡Que tengas un excelente viaje!"
    ),
]
_prewarm_started = False


def _ensure_tts_prewarm() -> None:
    """Sintetiza las frases fijas una sola vez (caché → latencia ~0)."""
    global _prewarm_started
    if _prewarm_started:
        return
    _prewarm_started = True
    asyncio.create_task(_tts.prewarm(_FIXED_PHRASES))


class TestCreateServiceRequest(BaseModel):
    telefono: str
    origen: str
    destino: Optional[str] = None
    call_uuid: Optional[str] = None


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
        "service": "lyra-voice-v2",
        "backend_api": settings.INTELLITAXI_API_BASE,
        "session_store": settings.VOICE_SESSION_STORE,
        "redis_ok": redis_ok,
        "stt_provider": "openai-realtime",
        "stt_model": settings.VOICE_STT_MODEL,
        "stt_language": settings.VOICE_STT_LANGUAGE,
        "stt_available": bool(settings.openai_stt_key()),
        "nlu_model": settings.VOICE_NLU_MODEL,
        "tts_voice": settings.LYRA_TTS_VOICE,
        "active_sessions": store.active_count(),
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


@freeswitch_router.get("/recording/{call_uuid}.wav")
async def serve_recording(call_uuid: str):
    """Sirve la grabación de llamada completa por call_uuid (para el frontend)."""
    path = recording_path(call_uuid)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(
        path=str(path),
        media_type="audio/wav",
        filename=path.name,
    )


@freeswitch_router.websocket("/audio")
async def audio_stream(websocket: WebSocket):
    """WebSocket mod_audio_stream ↔ Lyra Voice V2 (full-duplex).

    Entrada: frames binarios PCM16 8k mono (pata del llamante) + JSON de
    protocolo. Salida: mensajes streamAudio con el TTS por chunks.
    """
    await websocket.accept()
    _ensure_tts_prewarm()
    logger.info(
        "[freeswitch/ws] websocket accepted query=%s",
        dict(websocket.query_params),
    )
    app = websocket.scope.get("app")
    http_client = getattr(app.state, "http_client", None) if app else None
    runtime = VoiceCallRuntime(
        websocket,
        orchestrator=_orchestrator,
        tts=_tts,
        nlu=_nlu,
        http_client=http_client,
    )
    await runtime.run()
