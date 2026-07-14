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
    
    # STT Settings (because OpenRouter does not support audio)
    STT_PROVIDER: str = ""  # openai | groq | deepgram (alias global; telefonía usa TELEPHONY_STT_PROVIDER)
    GROQ_API_KEY: str = ""
    OPENAI_WHISPER_KEY: str = ""  # legacy Whisper dedicado
    OPENAI_STT_API_KEY: str = ""  # STT telefonía; si vacío usa OPENAI_API_KEY
    OPENAI_STT_MODEL: str = ""  # ej. gpt-4o-mini-transcribe

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
    # Fallback SOLO cuando no hay request ni FREESWITCH_HTTP_BASE_URL: en runtime
    # build_ws_audio_url deriva el WS del mismo host:puerto que FreeSWITCH ya
    # alcanza para el WAV (172.17.0.1:8098 en contenedor). Puerto = PORT del app.
    FREESWITCH_WS_AUDIO_URL: str = "ws://127.0.0.1:8098/freeswitch/audio"
    FREESWITCH_HTTP_BASE_URL: str = ""  # ej. http://127.0.0.1:8098 (para audio_url en inbound-call)
    FREESWITCH_TTS_CACHE_DIR: str = "data/freeswitch_tts"
    # Grabaciones de llamada completa (record_session) subidas por FreeSWITCH al
    # final de la llamada. Se sirven por call_uuid para reproducirlas en el
    # frontend del servicio. Dir controlado por el app (host), no el contenedor.
    FREESWITCH_RECORDINGS_DIR: str = "data/freeswitch_recordings"

    TELEPHONY_STT_PROVIDER: str = ""  # openai | groq | deepgram (vacío = auto según API keys)
    TELEPHONY_STT_MODEL: str = ""  # openai: gpt-4o-mini-transcribe; groq: whisper-large-v3
    TELEPHONY_STT_LANGUAGE: str = "es"
    TELEPHONY_AUDIO_CODEC: str = "PCMU"
    TELEPHONY_SAMPLE_RATE: int = 8000
    TELEPHONY_WS_AUDIO_ENCODING: str = "pcm16"  # pcm16 | mulaw (mod_audio_stream mono 8k)

    # Segmentación de audio FreeSWITCH (mod_audio_stream). Duración máxima de una
    # locución antes de forzar el flush al STT. Adultos mayores dictan direcciones
    # pausado: 3s cortaba la dirección a la mitad → fallback amplio (12s).
    FS_MAX_UTTERANCE_SEC: float = 12.0

    # VAD por energía. Base/fallback; el detector calibra el umbral real contra el
    # piso de ruido medido al inicio de cada locución (voces bajas/temblorosas).
    FS_VAD_SILENCE_RMS: float = 400.0      # techo del umbral adaptativo
    FS_VAD_SILENCE_FRAMES: int = 25         # frames de silencio base (~500ms @20ms)
    FS_VAD_MIN_SPEECH_FRAMES: int = 8
    FS_VAD_NOISE_CALIB_MS: int = 300        # ventana inicial para medir ruido de fondo
    FS_VAD_NOISE_MULT: float = 1.8          # umbral = piso_ruido * mult (acotado)
    FS_VAD_HANGOVER_MS: int = 600           # padding de silencio antes de cerrar turno

    FREESWITCH_ESL_ENABLED: bool = True

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

    def llm_api_key(self) -> str:
        """Credencial para llamadas de chat al LLM.

        OpenRouter y OpenAI usan sistemas de keys distintos, por eso se eligen
        por proveedor. Las keys de STT se resuelven aparte (voice_engine /
        stt_service) porque OpenRouter no soporta audio.
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
