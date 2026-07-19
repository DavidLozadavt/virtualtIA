"""
core/config.py — Configuración centralizada desde .env con pydantic-settings.
"""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # MySQL
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASS: str = ""
    DB_NAME: str = "lyra_db"

    # LLM Settings
    LLM_PROVIDER: str = "openrouter"
    OPENAI_API_KEY: str = ""          # OpenAI directo (sk-... / sk-proj-...). Tambien STT.
    OPENROUTER_API_KEY: str = ""      # OpenRouter (sk-or-...). Credencial distinta a OpenAI.
    OPENAI_MODEL: str = "openai/gpt-4o-mini"
    
    # STT del canal de voz de navegador (core/voice_engine): OpenAI
    # gpt-4o-mini-transcribe de forma exclusiva (OpenRouter no soporta audio).
    # El STT telefónico es Deepgram streaming aparte (ver Voice V2).
    OPENAI_WHISPER_KEY: str = ""  # key OpenAI dedicada para STT (audio). Nombre legacy.
    GROQ_API_KEY: str = ""        # ya NO se usa para STT; sólo lo lee el script de diagnóstico

    MODEL_PATH: str = "models/Phi-3-mini-4k-instruct-q4.gguf"
    MODEL_N_CTX: int = 4096
    MODEL_N_GPU_LAYERS: int = 0
    MODEL_N_THREADS: int = 4

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Rentus backend
    RENTUS_API_BASE: str = "http://localhost/backend-rentus/public/api"

    # IntelliTaxi backend
    INTELLITAXI_API_BASE: str = "http://127.0.0.1:8000/api"

    # WhatsApp Meta Cloud API
    WHATSAPP_VERIFY_TOKEN: str = "token_de_verificacion_123"
    WHATSAPP_API_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""

    # Google Maps API (para geocodificación)
    GOOGLE_MAPS_API_KEY: str = ""

    # ── Telephony / FreeSWITCH ──
    FREESWITCH_ESL_HOST: str = "127.0.0.1"
    FREESWITCH_ESL_PORT: int = 8021
    FREESWITCH_ESL_PASSWORD: str = "ClueCon"
    # Grabaciones de llamada completa (mezcladas server-side por el runtime V2).
    # Se sirven por call_uuid para reproducirlas en el frontend del servicio.
    FREESWITCH_RECORDINGS_DIR: str = "data/freeswitch_recordings"

    # Playback vía ESL uuid_broadcast (pivote 2026-07-19, ver audio_file_store.py):
    # directorio compartido entre el proceso Python (host) y el contenedor
    # FreeSWITCH (bind mount). Mismo contenido, dos rutas distintas.
    FREESWITCH_TTS_SHARED_DIR: str = "data/freeswitch_tts_shared"  # ruta del host
    FREESWITCH_TTS_CONTAINER_DIR: str = "/tmp/lyra-tts"  # ruta dentro del contenedor

    TELEPHONY_SAMPLE_RATE: int = 8000

    FREESWITCH_ESL_ENABLED: bool = True

    # ── Lyra Voice V2 — motor conversacional streaming ──
    # STT streaming vía OpenAI Realtime transcription (WebSocket,
    # gpt-4o-mini-transcribe): parciales en vivo (input_audio_transcription.delta),
    # eventos de voz (speech_started) y cierre de enunciado por server_vad. El
    # audio telefónico 8k se envía como g711_ulaw nativo (sin resample). Requiere
    # una OPENAI_API_KEY real (no OpenRouter, que no soporta audio); ver
    # openai_stt_key(). El sesgo de barrios de Popayán va en el prompt de sesión.
    VOICE_STT_MODEL: str = "gpt-4o-mini-transcribe"
    VOICE_STT_LANGUAGE: str = "es"
    VOICE_STT_SILENCE_MS: int = 600          # server_vad: silencio que cierra el enunciado
    VOICE_STT_VAD_THRESHOLD: float = 0.5     # server_vad: sensibilidad de detección de voz
    VOICE_STT_PREFIX_PADDING_MS: int = 300   # server_vad: audio previo al inicio de voz

    # Endpointing híbrido: retención semántica cuando el parcial termina en
    # continuación ("calle", "en", número colgado) antes de cerrar el turno.
    VOICE_ENDPOINT_HOLD_MS: int = 900
    VOICE_ENDPOINT_HOLD_MAX_MS: int = 2200   # techo total de retención semántica

    # NLU structured-output (extracción de spans; nunca resuelve direcciones).
    VOICE_NLU_MODEL: str = "gpt-4o-mini"
    VOICE_NLU_API_KEY: str = ""              # si vacío usa OPENAI_API_KEY (no sk-or)
    VOICE_NLU_TIMEOUT_SEC: float = 3.5

    # TTS streaming por oración (edge-tts incremental → PCM 8k vía ffmpeg pipe).
    VOICE_TTS_TIMEOUT_SEC: float = 10.0

    # Barge-in (clasificador de interrupción real vs. backchannel).
    VOICE_BARGE_MIN_MS: int = 250            # habla sostenida mínima para interrumpir
    VOICE_SILENCE_PROMPT_SEC: float = 6.0    # silencio tras prompt → re-pregunta
    VOICE_MAX_TURNS: int = 40                # tope duro de turnos por llamada

    LYRA_TTS_VOICE: str = "es-BO-SofiaNeural"

    FFMPEG_BIN: str = "/usr/bin/ffmpeg"

    VOICE_SESSION_STORE: str = "memory"  # memory | redis
    REDIS_URL: str = ""
    CALL_SESSION_TTL_SEC: int = 7200

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def openai_stt_key(self) -> str:
        """Credencial OpenAI real para STT de voz (Realtime transcription).

        OpenRouter (sk-or...) no soporta audio, así que se rechaza. Se prefiere
        la key dedicada OPENAI_WHISPER_KEY; si no, se usa OPENAI_API_KEY salvo
        que sea de OpenRouter. Misma política que core/voice_engine (STT navegador).
        """
        key = (self.OPENAI_WHISPER_KEY or "").strip()
        if not key and not self.OPENAI_API_KEY.startswith("sk-or"):
            key = self.OPENAI_API_KEY.strip()
        return key

    def llm_api_key(self) -> str:
        """Credencial para llamadas de chat al LLM.

        OpenRouter y OpenAI usan sistemas de keys distintos, por eso se eligen
        por proveedor. Las keys de STT/NLU de voz se resuelven aparte
        (voice_engine / services.voice) porque OpenRouter no soporta audio.
        """
        if self.LLM_PROVIDER == "openrouter":
            return self.OPENROUTER_API_KEY
        return self.OPENAI_API_KEY

    def llm_base_url(self) -> str:
        """Base URL del endpoint LLM segun proveedor."""
        if self.LLM_PROVIDER == "openrouter":
            return "https://openrouter.ai/api/v1"
        return "https://api.openai.com/v1"

    def get_absolute_model_path(self) -> str:
        """Resolve model path relative to project root."""
        p = Path(self.MODEL_PATH)
        if p.is_absolute():
            return str(p)
        return str(Path(__file__).parent.parent / p)


settings = Settings()
