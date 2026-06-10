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
    APP_ENV: str = "production"

    # Telefonía (FreeSWITCH — sin Twilio en esta rama)
    TELEPHONY_PROVIDER: str = "freeswitch"
    FREESWITCH_WS_HOST: str = "0.0.0.0"
    FREESWITCH_WS_PORT: int = 8081
    FREESWITCH_AUDIO_WS_PATH: str = "/freeswitch/audio"
    FREESWITCH_ESL_HOST: str = ""
    FREESWITCH_ESL_PORT: int = 8021
    FREESWITCH_ESL_PASSWORD: str = ""
    LYRA_TTS_VOICE: str = "es-BO-SofiaNeural"

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
