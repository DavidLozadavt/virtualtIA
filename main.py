"""
main.py — Lyra Microservice Entry Point

... [TRUNCATED FOR BREVITY - ANALISIS COMMENTS] ...
"""

import logging
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()  # Must be before any os.getenv() calls in other modules

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.config import settings
from api.routers.main import router
from api.routers.admin import admin_router
from api.routers.twilio import voice_router
from api.routers.freeswitch import freeswitch_router
from api.routers.whatsapp import whatsapp_router
from api.routers.browser_voice import browser_voice_router
from api.routers.tts import router as tts_router
from api.middleware import RateLimitMiddleware

# ── Logging ──────────────────────────────────────────────────────
from core.logger import setup_logger

logger = setup_logger("lyra.main")


# ── Lifespan: load model once at startup ─────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan event.
    Loads the GGUF model into memory at startup.
    """
    model_path = settings.get_absolute_model_path()
    model_file = Path(model_path)

    from core.llm_engine import LLMEngine

    if not model_file.exists():
        logger.warning(f"Model file not found: {model_path}. Lyra will run in API-only mode (OpenRouter/OpenAI).")

    engine = LLMEngine(
        model_path=str(model_file),
        n_ctx=settings.MODEL_N_CTX,
        n_gpu_layers=settings.MODEL_N_GPU_LAYERS,
        n_threads=settings.MODEL_N_THREADS,
    )
    app.state.llm_engine = engine

    # ── Tool Registries: initialize once for each project ──
    from orchestrator.tool_registry import ToolRegistry
    app.state.tool_registries = {
        "nexiservice": ToolRegistry.for_project("nexiservice"),
        "intellitaxi": ToolRegistry.for_project("intellitaxi"),
        "rentus": ToolRegistry.for_project("rentus"),
        "schoolsena": ToolRegistry.for_project("schoolsena"),
    }
    logger.info(f"Tool Registries initialized: {list(app.state.tool_registries.keys())}")

    logger.info(f"LLM Provider: {settings.LLM_PROVIDER} | Model: {settings.OPENAI_MODEL}")

    # Fail-fast: detectar credencial LLM ausente al arrancar, no en la primera
    # llamada real de un usuario (causa del 401 "Missing Authentication header").
    if not settings.llm_api_key():
        expected = "OPENROUTER_API_KEY" if settings.LLM_PROVIDER == "openrouter" else "OPENAI_API_KEY"
        logger.error(
            "LLM_PROVIDER=%s pero %s esta vacio/ausente. Las llamadas al LLM "
            "fallaran con 401. Configura %s en el .env del servidor.",
            settings.LLM_PROVIDER, expected, expected,
        )
    elif settings.LLM_PROVIDER == "openrouter" and not settings.OPENROUTER_API_KEY.startswith("sk-or"):
        logger.error(
            "LLM_PROVIDER=openrouter pero OPENROUTER_API_KEY no parece una key de "
            "OpenRouter (esperado prefijo 'sk-or-'). Revisa que no sea una key de OpenAI."
        )

    logger.info(f"Lyra running on http://{settings.HOST}:{settings.PORT}")
    
    app.state.is_running = True

    # Initialize TTS cache for generated audio files
    app.state.tts_cache = {}

    # ── Shared httpx client for backend calls (reuses TCP connections) ──
    app.state.http_client = httpx.AsyncClient(timeout=60.0)
    
    # ── PUSHER: TRANSMITIR LA PERSONALIDAD EN TIEMPO REAL ──
    try:
        import asyncio
        from orchestrator.context_builder import load_project_config
        from core.voice_engine import get_voice_engine
        from core.pusher import get_pusher_client
        
        pusher_client = get_pusher_client()
        
        # ── PRE-WARM VOICES (Lyra & Nexo) ──
        async def warm_voices():
            engine = get_voice_engine()
            # Inicializamos ambas voces (Lyra: es-BO-SofiaNeural, Nexo: es-CL-LorenzoNeural)
            # Simplemente hacemos un stream corto vacío para "despertar" la conexión con edge-tts
            voices_to_warm = ["es-BO-SofiaNeural", "es-CL-LorenzoNeural"]
            for v in voices_to_warm:
                try:
                    async for _ in engine.synthesize_stream(" ", voice=v):
                        break
                    logger.info(f"[VoiceEngine] Voz pre-calentada: {v}")
                except Exception:
                    pass

        asyncio.create_task(warm_voices())

        async def watch_yaml_changes():
            yaml_path = Path(__file__).parent / "projects" / "nexiservice.yaml"
            last_mtime = yaml_path.stat().st_mtime if yaml_path.exists() else 0
            
            # Emitir al arrancar el servidor
            cfg = load_project_config("nexiservice") or {}
            try:
                if pusher_client:
                    pusher_client.trigger('lyra-channel', 'personality_updated', {
                        'bot_name': cfg.get("assistant_name", "Nexo"),
                        'bot_greeting': cfg.get("greeting", "¡Hola!")
                    })
            except Exception:
                pass
                
            # Loop infinito (hasta reiniciar) monitoreando el archivo YAML
            while getattr(app.state, "is_running", True):
                await asyncio.sleep(1)
                if not yaml_path.exists():
                    continue
                current_mtime = yaml_path.stat().st_mtime
                if current_mtime != last_mtime:
                    last_mtime = current_mtime
                    cfg = load_project_config("nexiservice") or {}
                    try:
                        if pusher_client:
                            pusher_client.trigger('lyra-channel', 'personality_updated', {
                                'bot_name': cfg.get("assistant_name", "Nexo"),
                                'bot_greeting': cfg.get("greeting", "¡Hola!")
                            })
                            logger.info(f"[Pusher] YAML editado. Sync rápido al Frontend -> {cfg.get('assistant_name')}")
                    except Exception as e:
                        logger.error(f"[Pusher] Error emitiendo nuevo update: {e}")

        asyncio.create_task(watch_yaml_changes())
        logger.info("[Pusher] Watcher en vivo iniciado. NUNCA MÁS necesitas reiniciar python tras editar YAML.")

        # ── INITIAL POWER STATUS BROADCAST ──
        try:
            from api.routers.admin.config import _get_config_value
            is_maintenance = _get_config_value("maintenance_mode", False)
            if pusher_client:
                pusher_client.trigger('lyra-channel', 'power_status_updated', {
                    'isPoweredOn': not is_maintenance,
                    'status': 'maintenance' if is_maintenance else 'online'
                })
                logger.info(f"[Pusher] Estado inicial emitido: {'OFF' if is_maintenance else 'ON'}")
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"[Pusher] Error al configurar websocket: {e}")

    yield

    # Cleanup
    logger.info("Shutting down Lyra...")
    
    # ── SHUTDOWN BROADCAST ──
    try:
        pusher_client = get_pusher_client()
        if pusher_client:
            pusher_client.trigger('lyra-channel', 'power_status_updated', {
                'isPoweredOn': False,
                'status': 'offline'
            })
    except Exception:
        pass

    app.state.is_running = False
    app.state.llm_engine = None
    if hasattr(app.state, "http_client"):
        await app.state.http_client.aclose()


# ── FastAPI app ──────────────────────────────────────────────────
app = FastAPI(
    title="Lyra — AI Microservice",
    description="Autonomous AI assistant with built-in admin, LLM engine, and tool calling",
    version="1.0.0",
    lifespan=lifespan,
)

# Rate limiting (inner middleware)
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)

# CORS — outermost middleware to ensure headers are present even on errors
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(router)
app.include_router(admin_router)
app.include_router(voice_router)           # Twilio telephony (/voice, /process_speech) — fallback
app.include_router(freeswitch_router)      # FreeSWITCH direct (/freeswitch/*)
app.include_router(whatsapp_router)        # Meta WhatsApp webhooks (/wh/whatsapp)
app.include_router(browser_voice_router)   # Browser voice STT/TTS (/voice/transcribe, /voice/synthesize)
app.include_router(tts_router)             # Serve generated MP3 files (/tts/{audio_id})
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    import json
    body = await request.body()
    try:
        body_str = body.decode()
    except:
        body_str = str(body)
    
    logger.error(f"❌ 422 VALIDATION ERROR: {exc.errors()}")
    logger.error(f"👉 REQUEST BODY: {body_str}")
    
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body_str},
    )


# ── Run with uvicorn ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        log_level="info",
    )
