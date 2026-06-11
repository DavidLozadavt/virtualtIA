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
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "openai/gpt-4o-mini"
    
    # STT Settings (because OpenRouter does not support audio)
    GROQ_API_KEY: str = ""
    OPENAI_WHISPER_KEY: str = ""

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
    FREESWITCH_WS_AUDIO_URL: str = "ws://127.0.0.1:8000/freeswitch/audio"
    FREESWITCH_HTTP_BASE_URL: str = ""  # ej. http://127.0.0.1:8098 (para audio_url en inbound-call)
    FREESWITCH_TTS_CACHE_DIR: str = "data/freeswitch_tts"

    TELEPHONY_STT_PROVIDER: str = "groq"  # groq | openai | deepgram
    TELEPHONY_STT_MODEL: str = "whisper-large-v3"
    TELEPHONY_STT_LANGUAGE: str = "es"
    TELEPHONY_AUDIO_CODEC: str = "PCMU"
    TELEPHONY_SAMPLE_RATE: int = 8000

    LYRA_TTS_VOICE: str = "es-BO-SofiaNeural"

    VOICE_SESSION_STORE: str = "memory"  # memory | redis
    REDIS_URL: str = ""
    CALL_SESSION_TTL_SEC: int = 7200

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def get_absolute_model_path(self) -> str:
        """Resolve model path relative to project root."""
        p = Path(self.MODEL_PATH)
        if p.is_absolute():
            return str(p)
        return str(Path(__file__).parent.parent / p)


settings = Settings()
