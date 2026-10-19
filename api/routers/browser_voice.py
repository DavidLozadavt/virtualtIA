"""
gateway/browser_voice_router.py — REST endpoints for browser-based voice (STT + TTS).

Provides:
  POST /voice/transcribe   — Audio file → transcribed text (gpt-4o-mini-transcribe)
  POST /voice/synthesize   — Text → MP3 audio (edge-tts)

STT model is fixed to gpt-4o-mini-transcribe en core/voice_engine (no override
por proyecto). No configures `stt_model` en el YAML: se ignora a propósito.

Per-project activation: set `voice.enabled: true` in the project's YAML config.

How to enable for a new project:
  1. Add to your project's YAML:
       voice:
         enabled: true
         language: es
         tts_model: edge-tts
         tts_voice: es-CO-SalomeNeural
  2. That's it. These endpoints handle the rest.
"""

from core.logger import setup_logger
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional

from orchestrator.context_builder import load_project_config
from core.voice_engine import get_voice_engine
from core.pusher import trigger_pusher_event

logger = setup_logger("lyra.api.browser_voice")

browser_voice_router = APIRouter(prefix="/voice", tags=["Voice"])


# ── Request / Response Schemas ──────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    project_id: str
    text: str
    voice: Optional[str] = None   # Override project default
    speed: Optional[float] = 1.0


class TranscribeResponse(BaseModel):
    success: bool
    text: str
    language: str = ""
    confidence: float = 1.0
    error: str = ""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _get_voice_config(project_id: str, personality: str = None) -> dict:
    """Load and validate voice config from project YAML."""
    config = load_project_config(project_id, personality)
    if not config:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    voice_cfg = config.get("voice", {})
    if not voice_cfg.get("enabled", False):
        raise HTTPException(
            status_code=403,
            detail=f"Voice is not enabled for project '{project_id}'. "
                   f"Add 'voice.enabled: true' to the project YAML to activate it."
        )
    return voice_cfg


# ── Endpoints ───────────────────────────────────────────────────────────────

@browser_voice_router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    project_id: str = Form(...),
    audio: UploadFile = File(...),
):
    """
    Transcribe browser audio to text using gpt-4o-mini-transcribe.

    Accepts: WebM, WAV, MP3, OGG, M4A (recorded by MediaRecorder in browser)
    Returns: { "success": true, "text": "transcribed text", "language": "es" }

    Usage from any frontend:
        const formData = new FormData();
        formData.append('project_id', 'my_project');
        formData.append('audio', audioBlob, 'voice.webm');
        const res = await fetch('/voice/transcribe', { method: 'POST', body: formData });
        const { text } = await res.json();
    """
    voice_cfg = _get_voice_config(project_id)

    audio_bytes = await audio.read()

    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Audio file is empty.")

    if len(audio_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio exceeds 10MB limit.")

    # Pasar el content-type real del archivo para que el STT lo decodifique correctamente
    content_type = audio.content_type or "audio/webm"

    engine = get_voice_engine()
    result = await engine.transcribe(
        audio_bytes=audio_bytes,
        language=voice_cfg.get("language", "es"),
        content_type=content_type,
    )

    if not result["success"]:
        logger.warning(f"Transcription failed for project '{project_id}': {result.get('error')}")
        return TranscribeResponse(success=False, text="", error=result.get("error", "Unknown error"))

    logger.info(f"[{project_id}] Transcribed (conf={result.get('confidence', 1.0):.2f}): '{result['text'][:60]}'")
    return TranscribeResponse(
        success=True,
        text=result["text"],
        language=result.get("language", voice_cfg.get("language", "es")),
        confidence=result.get("confidence", 1.0),
    )


@browser_voice_router.post("/synthesize")
async def synthesize_speech(req: SynthesizeRequest):
    """
    Convert text to spoken audio using OpenAI TTS.

    Returns: MP3 audio binary (Content-Type: audio/mpeg)

    Usage from any frontend:
        const res = await fetch('/voice/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: 'my_project', text: 'Hola!' })
        });
        const blob = await res.blob();
        const audio = new Audio(URL.createObjectURL(blob));
        audio.play();
    """
    voice_cfg = _get_voice_config(req.project_id)

    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    # Trigger stop signal to frontend to prevent overlapping audio
    trigger_pusher_event("lyra-channel", "stop_audio", {"project_id": req.project_id})

    engine = get_voice_engine()
    result = await engine.synthesize(
        text=req.text,
        voice=req.voice or voice_cfg.get("tts_voice", "nova"),
        tts_model=voice_cfg.get("tts_model", "tts-1-hd"),  # HD = más humano
        speed=req.speed or 1.0,
    )

    if not result["success"]:
        raise HTTPException(status_code=500, detail=f"TTS failed: {result.get('error')}")

    logger.info(f"[{req.project_id}] Synthesized {len(result['audio_bytes'])} bytes.")
    return Response(
        content=result["audio_bytes"],
        media_type="audio/mpeg",
        headers={"Content-Disposition": "inline; filename=lyra_response.mp3"},
    )


from fastapi.responses import StreamingResponse

@browser_voice_router.get("/synthesize_stream")
async def synthesize_speech_stream(
    text: str, 
    project_id: str = "nexiservice", 
    speed: float = 1.0,
    personality: str = None
):
    """
    Stream audio directly to an <audio src="..."> tag for near-zero latency TTS.
    """
    voice_cfg = _get_voice_config(project_id, personality)
    
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    # Trigger stop signal to frontend to prevent overlapping audio
    trigger_pusher_event("lyra-channel", "stop_audio", {"project_id": project_id})

    engine = get_voice_engine()
    generator = engine.synthesize_stream(
        text=text,
        voice=voice_cfg.get("tts_voice", "es-ES-AlvaroNeural"),
        speed=speed,
    )
    
    return StreamingResponse(generator, media_type="audio/mpeg")


@browser_voice_router.get("/config/{project_id}")
async def get_voice_config_public(project_id: str):
    """
    Returns public voice config for a project (no secrets).
    Frontend uses this to know if voice is enabled, and what language to use.

    Usage: GET /voice/config/nexiservice
    Returns: { "enabled": true, "language": "es", "tts_voice": "nova" }
    """
    config = load_project_config(project_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    voice_cfg = config.get("voice", {})
    bot_name = config.get("assistant_name", "Lyra")
    return {
        "enabled": voice_cfg.get("enabled", False),
        "language": voice_cfg.get("language", "es"),
        "tts_voice": voice_cfg.get("tts_voice", "nova"),
        "bot_name": bot_name,
        "bot_greeting": config.get("greeting") or f"¡Hola! Bienvenido al portal de NexiService Colombia. Soy {bot_name}, tu guía para descubrir negocios y gestionar tus servicios. ¿Qué estás buscando hoy?",
    }
