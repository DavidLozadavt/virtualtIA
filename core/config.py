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

    # ── Pipeline de mejora de audio de captura (services/audio) ──
    # Aislamiento de voz y supresión de ruido antes del STT. Etapas modulares,
    # cada una desactivable y sustituible; el orden lo fija AUDIO_PIPELINE_STAGES.
    # Ninguna constante vive en el código: todo se ajusta desde aquí / .env.
    AUDIO_PIPELINE_ENABLED: bool = True
    AUDIO_PIPELINE_STAGES: str = (
        "preprocess,echo_control,dereverb,denoise,speaker_focus,voice_gate,normalize"
    )
    # strict=True propaga el error de una etapa (solo para pruebas); en producción
    # la etapa se desactiva y la llamada continúa.
    AUDIO_PIPELINE_STRICT: bool = False

    # Preacondicionamiento: paso-alto contra viento/retumbe/motor y límite de picos.
    AUDIO_HIGHPASS_HZ: float = 90.0
    AUDIO_PEAK_LIMIT: float = 0.97

    # STFT compartida por eco, dereverberación y el respaldo espectral.
    # 256/128 a 8 kHz = tramas de 32 ms con salto de 16 ms.
    AUDIO_STFT_FRAME: int = 256
    AUDIO_STFT_HOP: int = 128

    # Cancelación de eco con la referencia del TTS (filtro MDF + supresión residual).
    AUDIO_ECHO_REFERENCE_WINDOW_SEC: float = 8.0
    AUDIO_ECHO_TAIL_MS: float = 128.0          # cola de eco que el filtro modela
    AUDIO_ECHO_STEP_SIZE: float = 0.3          # paso NLMS (mayor = converge antes, menos estable)
    AUDIO_ECHO_SEARCH_MS: float = 700.0        # rango de búsqueda del retardo de ida y vuelta
    AUDIO_ECHO_REALIGN_MS: float = 500.0       # cada cuánto se re-estima el retardo
    AUDIO_ECHO_ALIGN_CONFIDENCE: float = 0.35  # confianza mínima de GCC-PHAT para aceptar el retardo
    AUDIO_ECHO_ALIGN_MIN_DBFS: float = -45.0   # energía mínima para intentar alinear (evita picos espurios)
    AUDIO_ECHO_RESIDUAL_STRENGTH: float = 1.6
    AUDIO_ECHO_RESIDUAL_FLOOR: float = 0.05
    AUDIO_ECHO_DETECT_MARGIN_DB: float = 3.0   # margen para declarar la trama dominada por eco
    AUDIO_ECHO_TAIL_HOLD_MS: float = 1200.0    # vigilancia de eco tras terminar el playback

    # Dereverberación (supresión estadística de cola tardía). Deliberadamente
    # suave: en banda telefónica la reverberación audible ya está recortada y una
    # dereverberación fuerte introduce artefactos que el reconocedor confunde con
    # fonemas. Es la primera etapa a desactivar si una medición de WER no mejora.
    AUDIO_DEREVERB_RT60_SEC: float = 0.35
    AUDIO_DEREVERB_DIRECT_FRAMES: int = 2
    AUDIO_DEREVERB_STRENGTH: float = 0.5
    AUDIO_DEREVERB_FLOOR: float = 0.3

    # Supresión de ruido neuronal. `onnx` ejecuta el modelo de streaming causal
    # (por defecto DPDFNet 8 kHz nativo, Apache-2.0) sobre ONNX Runtime/CPU;
    # `spectral` es el respaldo sin modelo y `none` deja pasar la señal.
    # Cambiar de modelo es cambiar AUDIO_DENOISE_MODEL_PATH: mientras cumpla la
    # firma (spec, state) → (spec, state), no hay que tocar código.
    AUDIO_DENOISE_BACKEND: str = "onnx"        # onnx | spectral | none
    AUDIO_DENOISE_MODEL_PATH: str = "models/dpdfnet2_8khz.onnx"
    AUDIO_DENOISE_THREADS: int = 1
    # Tasa a la que trabaja el supresor; 0 = la del pipeline. Un modelo de 16 kHz
    # se conecta poniendo 16000: el pipeline remuestrea a su alrededor.
    AUDIO_DENOISE_RATE: int = 0
    # Límite de atenuación en dB: acota cuánto puede borrar el modelo para que no
    # se lleve fonemas (0 = sin supresión; negativo = sin límite).
    AUDIO_DENOISE_ATTN_LIMIT_DB: float = 12.0
    # Retardo entrada→salida del modelo, necesario para alinear la mezcla del
    # límite de atenuación. -1 = medirlo automáticamente al cargar el modelo.
    AUDIO_DENOISE_DELAY_SAMPLES: int = -1

    # Foco en el hablante principal (rechazo de TV, oficina y conversaciones cercanas).
    AUDIO_FOCUS_FRAME_MS: float = 20.0
    AUDIO_FOCUS_INTEGRATION_MS: float = 200.0  # ventana silábica: mide distancia, no fonema
    AUDIO_FOCUS_WINDOW_SEC: float = 3.0
    AUDIO_FOCUS_PERCENTILE: float = 85.0
    AUDIO_FOCUS_MARGIN_DB: float = 18.0       # dB por debajo del dominante = fondo
    AUDIO_FOCUS_SILENCE_DBFS: float = -55.0
    AUDIO_FOCUS_MIN_FRAMES: int = 40          # ventana mínima antes de marcar fondo

    # Puerta de voz (Silero VAD sobre ONNX, 8 kHz nativo).
    AUDIO_VAD_BACKEND: str = "silero"         # silero | energy
    AUDIO_VAD_MODEL_PATH: str = "models/silero_vad.onnx"
    AUDIO_VAD_THREADS: int = 1
    AUDIO_VAD_THRESHOLD: float = 0.6
    AUDIO_VAD_RELEASE_MARGIN: float = 0.15    # histéresis: umbral de cierre = umbral - margen
    AUDIO_VAD_MIN_SPEECH_MS: float = 96.0     # habla mínima para abrir (rechaza transitorios)
    AUDIO_VAD_HANGOVER_MS: float = 320.0      # colgado tras la última trama de voz
    AUDIO_GATE_PRE_ROLL_MS: float = 96.0      # apertura retroactiva; es la latencia del pipeline
    AUDIO_GATE_ATTENUATION: float = 0.0       # 0 = silencio digital para el no-habla
    # Evidencia extra exigida al detector cuando hay eco o voz de fondo (se suma
    # al umbral). 0 = sin exigencia extra; ≥ 1.0 = veto absoluto.
    AUDIO_GATE_ECHO_PENALTY: float = 0.3
    AUDIO_GATE_ECHO_HOLD_MS: float = 128.0    # la exigencia persiste tras el último eco
    AUDIO_GATE_BACKGROUND_PENALTY: float = 1.0

    # Normalización de nivel (lenta y acotada: el operador ya aplica su propio AGC).
    AUDIO_NORMALIZE_TARGET_DBFS: float = -20.0
    AUDIO_NORMALIZE_MAX_GAIN_DB: float = 12.0
    AUDIO_NORMALIZE_MIN_GAIN_DB: float = -6.0
    AUDIO_NORMALIZE_ATTACK: float = 0.7
    AUDIO_NORMALIZE_RELEASE: float = 0.98
    AUDIO_NORMALIZE_LIMIT: float = 0.95
    # True: la ganancia solo se adapta sobre habla confirmada por la puerta de voz
    # (nunca amplifica ruido de fondo). False: se adapta con cualquier señal sobre
    # el umbral de silencio, útil si la puerta no está en la cadena.
    AUDIO_NORMALIZE_SPEECH_ONLY: bool = True

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
