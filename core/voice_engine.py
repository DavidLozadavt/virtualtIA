"""
core/voice_engine.py — Centralized Speech-to-Text and Text-to-Speech engine for Lyra.

STT: OpenAI gpt-4o-mini-transcribe — única ruta de transcripción soportada.
     Supera a whisper-1 en nombres propios/barrios (evidencia:
     scratch/audio_diagnostic.py, donde whisper-1 rompe "Valle del Ortigal").
     whisper-1 y el fallback Groq whisper-large-v3 fueron retirados a propósito.
TTS: OpenAI gpt-4o-mini-tts vía services/voice/tts_stream — el mismo motor y las
     mismas instrucciones de interpretación que el canal telefónico, para que la
     voz del navegador sea la misma persona. El texto se normaliza antes de
     sintetizar (números, nomenclatura vial, puntuación) y el audio se emite en
     streaming según lo genera el modelo.

Voces disponibles (timbre; la fonética española la fijan el texto y las
instrucciones, no el nombre de la voz):
  coral    → femenina, cálida y cercana ★ recomendada para atención
  sage     → femenina, serena
  nova     → femenina, clara
  ballad   → femenina, expresiva
  ash      → masculina, firme
  onyx     → masculina, grave
  verse    → masculina, conversacional

Usage in any project: enable via voice.enabled: true in the project's YAML config.
"""

import logging
import re as _re
from io import BytesIO
from typing import AsyncGenerator

from services.voice.text_normalize import prepare_for_speech
from services.voice.tts_stream import (
    DEFAULT_VOICE,
    TTSError,
    resolve_voice,
    stream_speech,
)

logger = logging.getLogger("lyra.voice")

# Único modelo STT del sistema. No hay fallback a whisper-1 ni a Groq
# whisper-large-v3: gpt-4o-mini-transcribe se usa de forma exclusiva.
STT_MODEL = "gpt-4o-mini-transcribe"


# ── Prompt hint para el STT ───────────────────────────────────────────────────
# Sesga el modelo hacia vocabulario cotidiano en español para mejorar precisión
# con hablantes rápidos, lentos o con poca vocalización.
_STT_PROMPT_ES = (
    "Sí. No. Ok. Vale. Dale. Claro. Listo. Bien. Perfecto. "
    "Hola, buenos días. Busco un restaurante, barbería, hotel. "
    "Quiero agendar una cita. Sí, me interesa. No, gracias. Ok, adelante. Dale, muéstrame."
)


def _is_gibberish(text: str) -> bool:
    """
    Detecta si el texto transcrito es basura/ruido (e.g. 'asdñlamdslkmasd').
    Criterios:
      1. Más del 60% consonantes seguidas sin vocal (bloques ilegibles).
      2. Sin ningún carácter de espacio y longitud > 6 (palabra irreconocible).
      3. Proporción de caracteres no-alfa > 40%.
    """
    if not text:
        return False
    t = text.strip().lower()
    if len(t) <= 2:
        return False
    consonant_blocks = _re.findall(r'[bcdfghjklmnñpqrstvwxyz]{5,}', t)
    if consonant_blocks:
        return True
    if ' ' not in t and len(t) > 8:
        alpha = [c for c in t if c.isalpha()]
        non_alpha = [c for c in t if not c.isalpha() and not c.isspace()]
        if len(non_alpha) / max(len(t), 1) > 0.35:
            return True
        vowels = set('aeiouáéíóúü')
        vowel_count = sum(1 for c in alpha if c in vowels)
        if alpha and vowel_count / len(alpha) < 0.15:
            return True
    return False


def _clean_for_tts(text: str) -> str:
    """
    Limpia el texto antes de enviarlo al motor TTS.
    Elimina markdown, anclas internas y caracteres que suenan raro al hablar.
    Los símbolos que sí tienen lectura en español ('#', '$', '%', '°') se
    conservan aquí: los convierte a palabras la normalización posterior.
    """
    # Eliminar tags internos de Lyra [TAG:XX], [BIZ:XX], [ID:XX]
    # Usamos [^\]]* para permitir IDs alfanuméricos
    clean = _re.sub(r'\[BIZ:[^\]]*\]', '', text)
    clean = _re.sub(r'\[ID:[^\]]*\]', '', clean)
    clean = _re.sub(r'\[TAG:[^\]]*\]', '', clean)
    
    # Convertir **negrita** e *itálica* a texto plano
    clean = _re.sub(r'\*{1,2}(.+?)\*{1,2}', r'\1', clean)
    
    # Eliminar headings markdown
    clean = _re.sub(r'#{1,6}\s*', '', clean)
    
    # Convertir viñetas al inicio de línea
    clean = _re.sub(r'^\s*[•·\-]\s*', '', clean, flags=_re.MULTILINE)
    
    # Colapsar saltos de línea en pausas
    clean = _re.sub(r'\n+', '. ', clean)
    
    # Limpieza final de caracteres extraños pero permitiendo lo básico pronunciable
    clean = _re.sub(r'[^\w\s\.,;:¿?¡!\[\]\-:#$%°º]', '', clean, flags=_re.UNICODE)

    result = _re.sub(r'\s+', ' ', clean).strip()
    return result if result else " "  # Nunca retornar vacío para no romper el stream Audio


def _speech_text(text: str, limit: int = 4000) -> str:
    """Texto tal como se envía al modelo: sin markdown, con pronunciación y
    puntuación de habla resueltas."""
    return prepare_for_speech(_clean_for_tts(text))[:limit]


class VoiceEngine:
    """
    Motor de voz reutilizable en cualquier proyecto Lyra.

    STT: OpenAI gpt-4o-mini-transcribe (requiere una key REAL de OpenAI —
         OpenRouter no soporta audio)
    TTS: OpenAI gpt-4o-mini-tts con instrucciones de operadora telefónica
         (misma key real de OpenAI)
    """

    SUPPORTED_AUDIO_FORMATS = {"audio/webm", "audio/wav", "audio/mp3", "audio/mpeg", "audio/ogg", "audio/m4a"}
    MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB

    # Mapeo de content-type → extensión para que el STT decodifique correctamente
    _MIME_TO_EXT = {
        "audio/webm": "webm",
        "audio/wav":  "wav",
        "audio/mp3":  "mp3",
        "audio/mpeg": "mp3",
        "audio/ogg":  "ogg",
        "audio/m4a":  "m4a",
        "audio/mp4":  "m4a",
    }

    def __init__(self, api_key: str):
        # STT: OpenAI gpt-4o-mini-transcribe, siempre. Sin fallback a whisper-1
        # ni a Groq whisper-large-v3 (ambos peores en nombres propios/barrios).
        self.stt_model = STT_MODEL
        try:
            from openai import AsyncOpenAI
            from core.config import settings

            # gpt-4o-mini-transcribe requiere una key REAL de OpenAI. OpenRouter
            # (sk-or...) no soporta audio. Se prefiere la key dedicada de STT
            # (OPENAI_WHISPER_KEY, ahora "OpenAI STT key"); si no existe, se usa
            # OPENAI_API_KEY salvo que sea de OpenRouter.
            stt_key = (settings.OPENAI_WHISPER_KEY or "").strip()
            if not stt_key and not settings.OPENAI_API_KEY.startswith("sk-or"):
                stt_key = settings.OPENAI_API_KEY.strip()

            if stt_key:
                self.openai_client = AsyncOpenAI(api_key=stt_key)
                self.stt_available = True
                logger.info("VoiceEngine STT (OpenAI %s) inicializado.", self.stt_model)
            else:
                self.openai_client = None
                self.stt_available = False
                logger.warning(
                    "STT deshabilitado: falta una key de OpenAI válida para audio. "
                    "Configura OPENAI_WHISPER_KEY, o una OPENAI_API_KEY que no sea de "
                    "OpenRouter (sk-or...). gpt-4o-mini-transcribe no acepta keys sk-or."
                )
        except ImportError:
            logger.warning("openai package no instalado. STT no disponible.")
            self.openai_client = None
            self.stt_available = False

        # El TTS usa la misma credencial real de OpenAI que el STT: sin ella no
        # hay síntesis posible, y decirlo al arrancar evita descubrirlo en la
        # primera respuesta hablada.
        from core.config import settings as _settings

        self.tts_available = bool(_settings.openai_audio_key())
        if self.tts_available:
            logger.info(
                "VoiceEngine TTS (OpenAI %s, voz %s) inicializado.",
                _settings.VOICE_TTS_MODEL, _settings.VOICE_TTS_VOICE,
            )
        else:
            logger.warning(
                "TTS deshabilitado: falta una key real de OpenAI para audio "
                "(OPENAI_WHISPER_KEY / OPENAI_API_KEY no-OpenRouter)."
            )

        # Por compatibilidad con código legacy
        self.available = self.stt_available or self.tts_available

    # ── STT ──────────────────────────────────────────────────────────────────

    async def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "es",
        content_type: str = "audio/webm",
    ) -> dict:
        """
        Convierte audio de voz a texto con OpenAI gpt-4o-mini-transcribe.

        El modelo es fijo (self.stt_model): no se acepta override por-llamada
        para que ninguna ruta pueda volver a seleccionar whisper-1.

        Notas del modelo:
        - response_format="json" es el ÚNICO formato soportado por
          gpt-4o-mini-transcribe (verbose_json/segments NO están disponibles,
          por eso no se calcula confianza por no_speech_prob).
        - prompt hint: sesga hacia vocabulario español colombiano.
        - Extensión de archivo correcta según MIME type.
        - Detección de transcripciones basura.

        Returns: { "success": bool, "text": str, "language": str, "confidence": float }
        """
        if not self.stt_available:
            return {"success": False, "text": "", "error": "STT no disponible. Instala el paquete 'openai'."}

        if len(audio_bytes) > self.MAX_AUDIO_BYTES:
            return {"success": False, "text": "", "error": "Audio excede el límite de 10MB."}

        if len(audio_bytes) < 100:
            return {"success": False, "text": "", "error": "Audio demasiado corto o vacío."}

        try:
            ext = self._MIME_TO_EXT.get(content_type, "webm")
            audio_file = BytesIO(audio_bytes)
            audio_file.name = f"voice_input.{ext}"

            response = await self.openai_client.audio.transcriptions.create(
                model=self.stt_model,
                file=audio_file,
                language=language if language else None,
                response_format="json",
                prompt=_STT_PROMPT_ES,
                temperature=0.0,
            )

            if hasattr(response, "text"):
                text = response.text.strip()
                detected_lang = getattr(response, "language", language) or language
            else:
                text = str(response).strip()
                detected_lang = language

            if not text:
                return {"success": False, "text": "", "error": "No se detectó voz en el audio."}

            # gpt-4o-mini-transcribe (response_format=json) no devuelve segmentos ni
            # no_speech_prob, así que no hay confianza por-segmento: se reporta 1.0.
            avg_confidence = 1.0

            if _is_gibberish(text):
                logger.warning(f"Transcripción basura detectada: '{text[:60]}' — rechazando.")
                return {"success": False, "text": "", "error": "No se pudo entender. Por favor habla con claridad.", "raw": text}

            log_msg = f"Transcribed ({detected_lang}, conf={avg_confidence:.2f}): '{text[:80]}...'" if len(text) > 80 else f"Transcribed ({detected_lang}): '{text}'"
            logger.info(log_msg)
            return {"success": True, "text": text, "language": detected_lang, "confidence": avg_confidence}

        except Exception as e:
            logger.error(f"Error en transcripción: {e}")
            return {"success": False, "text": "", "error": str(e)}

    # ── TTS (OpenAI gpt-4o-mini-tts) ─────────────────────────────────────────

    async def synthesize_to_bytes(self, text: str, voice: str = DEFAULT_VOICE) -> bytes:
        """
        Sintetiza audio y lo retorna directamente en bytes MP3 (en memoria).
        """
        if not self.tts_available:
            return b""

        speech = _speech_text(text)
        if not speech.strip():
            return b""

        try:
            buffer = BytesIO()
            async for chunk in stream_speech(
                speech, voice=voice, response_format="mp3"
            ):
                buffer.write(chunk)
            audio_bytes = buffer.getvalue()
            if audio_bytes:
                logger.info(
                    "[tts BYTES] %d bytes generados en memoria | voz=%s",
                    len(audio_bytes), resolve_voice(voice),
                )
            return audio_bytes
        except Exception as e:
            logger.error(f"Error en synthesize_to_bytes: {e}")
            return b""

    async def synthesize(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        tts_model: str = "",
        speed: float = 1.0,
    ) -> dict:
        """
        Convierte texto a audio con OpenAI gpt-4o-mini-tts.

        El modelo es fijo (el único que acepta el bloque `instructions` con el
        que se gobierna la interpretación): `tts_model` se conserva por
        compatibilidad de firma y se ignora, igual que en el STT.

        `speed` no se envía como parámetro de la API (gpt-4o-mini-tts no lo
        soporta): se traduce a una indicación de ritmo dentro de las
        instrucciones, que es donde suena natural.

        Voces recomendadas: coral, sage, nova, ballad (femeninas);
        ash, onyx, verse (masculinas).

        Returns: { "success": bool, "audio_bytes": bytes, "format": "mp3" }
        """
        if not self.tts_available:
            return {"success": False, "audio_bytes": b"", "error": "TTS no disponible."}

        if not text or not text.strip():
            return {"success": False, "audio_bytes": b"", "error": "Texto vacío."}

        speech = _speech_text(text)
        if not speech.strip():
            return {"success": False, "audio_bytes": b"", "error": "El texto quedó vacío después de limpiar."}

        try:
            buffer = BytesIO()
            async for chunk in stream_speech(
                speech, voice=voice, response_format="mp3", pace=speed
            ):
                buffer.write(chunk)
            audio_bytes = buffer.getvalue()

            if not audio_bytes:
                return {"success": False, "audio_bytes": b"", "error": "El TTS no generó audio."}

            logger.info(
                "[tts] %d bytes sintetizados | voz=%s | '%s'",
                len(audio_bytes), resolve_voice(voice), speech[:50],
            )
            return {"success": True, "audio_bytes": audio_bytes, "format": "mp3"}

        except Exception as e:
            logger.error(f"Error en síntesis TTS: {e}")
            return {"success": False, "audio_bytes": b"", "error": str(e)}

    async def synthesize_stream(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
    ) -> AsyncGenerator[bytes, None]:
        """
        Emite el MP3 según lo genera el modelo (streaming real del proveedor):
        el primer byte sale sin esperar a que la frase esté completa.
        """
        if not self.tts_available or not text or not text.strip():
            return

        speech = _speech_text(text)
        if not speech.strip():
            return

        try:
            logger.info(
                "[tts STREAM] voz=%s | '%s'", resolve_voice(voice), speech[:50]
            )
            async for chunk in stream_speech(
                speech, voice=voice, response_format="mp3", pace=speed
            ):
                yield chunk
        except Exception as e:
            logger.error(f"Error en stream TTS: {e}")



# ── Singleton helper ──────────────────────────────────────────────────────────

_engine_instance: VoiceEngine | None = None


def get_voice_engine() -> VoiceEngine:
    """Retorna el singleton VoiceEngine, creándolo si aún no existe."""
    global _engine_instance
    if _engine_instance is None:
        from core.config import settings
        _engine_instance = VoiceEngine(api_key=settings.OPENAI_API_KEY)
    return _engine_instance
