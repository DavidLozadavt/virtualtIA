"""
STT directo para telefonía — sin Twilio.

Soporta:
  - openai: gpt-4o-mini-transcribe / whisper-1 (SDK oficial OpenAI)
  - groq: batch Whisper sobre chunks de audio
  - deepgram: placeholder para streaming (fase 5)
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from core.config import settings

logger = logging.getLogger("lyra.telephony.stt")

_OPENAI_TRANSCRIBE_DEFAULT = "gpt-4o-mini-transcribe"
_GROQ_WHISPER_DEFAULT = "whisper-large-v3"


def _openai_stt_api_key() -> str:
    """API key OpenAI para STT (nunca OpenRouter sk-or)."""
    dedicated = (settings.OPENAI_STT_API_KEY or "").strip()
    if dedicated:
        return dedicated
    fallback = (settings.OPENAI_API_KEY or "").strip()
    if fallback and not fallback.startswith("sk-or"):
        return fallback
    legacy = (settings.OPENAI_WHISPER_KEY or "").strip()
    if legacy:
        return legacy
    return ""


def _resolve_stt_provider() -> str:
    explicit = (
        (settings.TELEPHONY_STT_PROVIDER or settings.STT_PROVIDER or "").strip().lower()
    )
    if explicit in ("openai", "groq", "deepgram"):
        if explicit == "groq" and not settings.GROQ_API_KEY and _openai_stt_api_key():
            logger.info(
                "[stt] TELEPHONY_STT_PROVIDER=groq sin GROQ_API_KEY — usando openai"
            )
            return "openai"
        return explicit

    if _openai_stt_api_key():
        return "openai"
    if settings.GROQ_API_KEY:
        return "groq"
    return "openai"


def _resolve_openai_stt_model() -> str:
    return (
        (settings.OPENAI_STT_MODEL or "").strip()
        or (settings.TELEPHONY_STT_MODEL or "").strip()
        or _OPENAI_TRANSCRIBE_DEFAULT
    )


def _resolve_groq_stt_model() -> str:
    return (settings.TELEPHONY_STT_MODEL or "").strip() or _GROQ_WHISPER_DEFAULT


def _is_whisper_model(model: str) -> bool:
    return "whisper" in (model or "").lower()


def _extract_transcription_text(response) -> str:
    """Extrae solo el texto transcrito; nunca serializa el objeto completo."""
    raw = getattr(response, "text", None)
    if raw is None:
        if isinstance(response, dict):
            raw = response.get("text")
        elif hasattr(response, "model_dump"):
            try:
                raw = response.model_dump().get("text")
            except Exception:
                raw = None
    if raw is None:
        return ""
    return str(raw).strip()


class TelephonySTTService:
    """Convierte audio telefónico (µ-law/PCM/WAV) a texto."""

    def __init__(self):
        self.provider = _resolve_stt_provider()
        self.language = settings.TELEPHONY_STT_LANGUAGE or "es"
        self.sample_rate = settings.TELEPHONY_SAMPLE_RATE or 8000
        self.model = ""
        self._client = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            self._client = None
            logger.warning("[stt] openai package not installed")
            return

        if self.provider == "deepgram":
            self._client = None
            logger.info("[stt] provider=deepgram (streaming pending)")
            return

        if self.provider == "openai":
            api_key = _openai_stt_api_key()
            if not api_key:
                self._client = None
                logger.warning("[stt/openai] provider selected but no API key configured")
                return
            self._client = AsyncOpenAI(api_key=api_key)
            self.model = _resolve_openai_stt_model()
            logger.info("[stt/openai] provider enabled model=%s", self.model)
            return

        if self.provider == "groq":
            if not settings.GROQ_API_KEY:
                self._client = None
                logger.warning("[stt] provider=groq but GROQ_API_KEY not configured")
                return
            self._client = AsyncOpenAI(
                api_key=settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
            )
            self.model = _resolve_groq_stt_model()
            logger.info("[stt] provider=groq model=%s", self.model)
            return

        self._client = None
        logger.warning("[stt] unknown provider=%s", self.provider)

    @property
    def available(self) -> bool:
        return self._client is not None or self.provider == "deepgram"

    async def transcribe_telephony_chunk(
        self,
        audio_bytes: bytes,
        *,
        encoding: str = "auto",
        call_uuid: str = "",
    ) -> dict:
        """
        Transcribe audio telefónico 8 kHz mono.

        encoding: pcm16 | mulaw | auto (mod_audio_stream suele enviar PCM16).
        """
        if not audio_bytes:
            return {"success": False, "text": "", "confidence": 0.0, "error": "empty audio"}

        enc = (encoding or "auto").lower()
        if enc == "auto":
            enc = _guess_encoding(audio_bytes)

        if enc in ("pcm16", "linear", "l16", "s16le"):
            wav_bytes = _pcm16_to_wav(audio_bytes, self.sample_rate)
        else:
            wav_bytes = _mulaw_to_wav(audio_bytes, self.sample_rate)

        return await self._transcribe_wav_bytes(
            wav_bytes,
            call_uuid=call_uuid,
            verbose=_is_whisper_model(self.model),
        )

    async def transcribe_mulaw_chunk(
        self,
        mulaw_bytes: bytes,
        *,
        call_uuid: str = "",
    ) -> dict:
        """Transcribe un chunk de audio µ-law 8kHz mono."""
        if not mulaw_bytes:
            return {"success": False, "text": "", "confidence": 0.0, "error": "empty audio"}

        if self.provider == "deepgram":
            return {
                "success": False,
                "text": "",
                "confidence": 0.0,
                "error": "Deepgram streaming not yet wired — use batch provider for now",
            }

        wav_bytes = _mulaw_to_wav(mulaw_bytes, self.sample_rate)
        return await self._transcribe_wav_bytes(
            wav_bytes,
            call_uuid=call_uuid,
            verbose=_is_whisper_model(self.model),
        )

    async def transcribe_base64_payload(
        self,
        payload_b64: str,
        *,
        encoding: str = "mulaw",
        call_uuid: str = "",
    ) -> dict:
        """Decodifica payload base64 (mod_audio_stream) y transcribe."""
        try:
            raw = base64.b64decode(payload_b64)
        except Exception as e:
            return {"success": False, "text": "", "confidence": 0.0, "error": str(e)}

        if encoding in ("mulaw", "pcmu"):
            return await self.transcribe_mulaw_chunk(raw, call_uuid=call_uuid)

        return await self._transcribe_wav_bytes(raw, call_uuid=call_uuid)

    async def _transcribe_wav_bytes(
        self,
        wav_bytes: bytes,
        *,
        call_uuid: str,
        verbose: bool = False,
    ) -> dict:
        if not self._client:
            return {"success": False, "text": "", "confidence": 0.0, "error": "STT not configured"}

        log_prefix = "[stt/openai]" if self.provider == "openai" else "[stt]"
        logger.info("%s transcribing audio... call_uuid=%s model=%s", log_prefix, call_uuid, self.model)

        try:
            audio_file = BytesIO(wav_bytes)
            audio_file.name = "call_audio.wav"

            create_kwargs: dict = {
                "model": self.model,
                "file": audio_file,
            }

            if _is_whisper_model(self.model):
                create_kwargs["language"] = self.language if self.language else None
                create_kwargs["response_format"] = "verbose_json" if verbose else "json"
                create_kwargs["temperature"] = 0.0
            else:
                create_kwargs["response_format"] = "json"

            response = await self._client.audio.transcriptions.create(**create_kwargs)

            text = _extract_transcription_text(response)
            if not text:
                logger.info(
                    "%s transcript_text=\"\" no_speech call_uuid=%s",
                    log_prefix,
                    call_uuid,
                )
                return {
                    "success": False,
                    "text": "",
                    "confidence": 0.0,
                    "error": "no speech detected",
                }

            confidence = 1.0
            if verbose:
                segments = getattr(response, "segments", []) or []
                if segments:
                    probs = [
                        s.get("no_speech_prob", 0)
                        for s in segments
                        if isinstance(s, dict)
                    ]
                    if probs:
                        confidence = round(1.0 - sum(probs) / len(probs), 3)

            logger.info(
                '%s transcript_text="%s" call_uuid=%s conf=%.2f',
                log_prefix,
                text[:200],
                call_uuid,
                confidence,
            )
            return {
                "success": True,
                "text": text,
                "confidence": confidence,
                "error": "",
            }
        except Exception as e:
            logger.error("%s error=%s call_uuid=%s", log_prefix, e, call_uuid)
            return {"success": False, "text": "", "confidence": 0.0, "error": str(e)}


def _guess_encoding(audio_bytes: bytes) -> str:
    """Heurística: mod_audio_stream mono 8k envía PCM16 en frames pares (~320 B)."""
    if len(audio_bytes) % 2 != 0:
        return "mulaw"
    if len(audio_bytes) in (160, 320, 640, 1280):
        return "pcm16"
    return "pcm16"


def _pcm16_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
    import wave

    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def _mulaw_to_wav(mulaw_data: bytes, sample_rate: int) -> bytes:
    """Convierte µ-law a WAV PCM 16-bit (stdlib, sin dependencias extra)."""
    import audioop
    import wave

    pcm = audioop.ulaw2lin(mulaw_data, 2)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
