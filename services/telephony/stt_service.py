"""
STT directo para telefonía — sin Twilio.

Soporta:
  - groq / openai: batch Whisper sobre chunks de audio
  - deepgram: placeholder para streaming (fase 5)
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import Optional

from core.config import settings

logger = logging.getLogger("lyra.telephony.stt")


class TelephonySTTService:
    """Convierte audio telefónico (µ-law/PCM/WAV) a texto."""

    def __init__(self):
        self.provider = (settings.TELEPHONY_STT_PROVIDER or "groq").lower()
        self.model = settings.TELEPHONY_STT_MODEL or "whisper-large-v3"
        self.language = settings.TELEPHONY_STT_LANGUAGE or "es"
        self.sample_rate = settings.TELEPHONY_SAMPLE_RATE or 8000
        self._client = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            from openai import AsyncOpenAI

            if self.provider == "deepgram":
                # Streaming se implementará en fase 5 con SDK dedicado
                self._client = None
                logger.info("[stt] provider=deepgram (streaming pending)")
                return

            if self.provider == "groq" and settings.GROQ_API_KEY:
                self._client = AsyncOpenAI(
                    api_key=settings.GROQ_API_KEY,
                    base_url="https://api.groq.com/openai/v1",
                )
                self.model = settings.TELEPHONY_STT_MODEL or "whisper-large-v3"
            elif settings.OPENAI_WHISPER_KEY:
                self._client = AsyncOpenAI(api_key=settings.OPENAI_WHISPER_KEY)
                self.model = "whisper-1"
            elif settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("sk-or"):
                self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                self.model = "whisper-1"
            else:
                self._client = None
                logger.warning("[stt] No STT API key configured")
        except ImportError:
            self._client = None
            logger.warning("[stt] openai package not installed")

    @property
    def available(self) -> bool:
        return self._client is not None or self.provider == "deepgram"

    async def transcribe_mulaw_chunk(
        self,
        mulaw_bytes: bytes,
        *,
        call_uuid: str = "",
    ) -> dict:
        """
        Transcribe un chunk de audio µ-law 8kHz mono.
        Convierte a WAV antes de enviar a Whisper.
        """
        if not mulaw_bytes:
            return {"success": False, "text": "", "confidence": 0.0, "error": "empty audio"}

        if self.provider == "deepgram":
            return {
                "success": False,
                "text": "",
                "confidence": 0.0,
                "error": "Deepgram streaming not yet wired — use batch provider for now",
            }

        if not self._client:
            return {"success": False, "text": "", "confidence": 0.0, "error": "STT not configured"}

        try:
            wav_bytes = _mulaw_to_wav(mulaw_bytes, self.sample_rate)
            audio_file = BytesIO(wav_bytes)
            audio_file.name = "call_audio.wav"

            response = await self._client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                language=self.language if self.language else None,
                response_format="verbose_json",
                temperature=0.0,
            )

            text = (getattr(response, "text", None) or str(response)).strip()
            confidence = 1.0
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
                "[stt] call_uuid=%s conf=%.2f text=%r",
                call_uuid,
                confidence,
                text[:80],
            )
            return {
                "success": bool(text),
                "text": text,
                "confidence": confidence,
                "error": "" if text else "no speech detected",
            }
        except Exception as e:
            logger.error("[stt] call_uuid=%s error=%s", call_uuid, e)
            return {"success": False, "text": "", "confidence": 0.0, "error": str(e)}

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

        # PCM lineal 16-bit
        return await self._transcribe_wav_bytes(raw, call_uuid=call_uuid)

    async def _transcribe_wav_bytes(self, wav_bytes: bytes, *, call_uuid: str) -> dict:
        if not self._client:
            return {"success": False, "text": "", "confidence": 0.0, "error": "STT not configured"}
        try:
            audio_file = BytesIO(wav_bytes)
            audio_file.name = "call_audio.wav"
            response = await self._client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                language=self.language if self.language else None,
                response_format="json",
                temperature=0.0,
            )
            text = (getattr(response, "text", None) or str(response)).strip()
            return {"success": bool(text), "text": text, "confidence": 1.0, "error": ""}
        except Exception as e:
            logger.error("[stt] wav call_uuid=%s error=%s", call_uuid, e)
            return {"success": False, "text": "", "confidence": 0.0, "error": str(e)}


def _mulaw_to_wav(mulaw_data: bytes, sample_rate: int) -> bytes:
    """Convierte µ-law a WAV PCM 16-bit (stdlib, sin dependencias extra)."""
    import audioop
    import struct
    import wave

    pcm = audioop.ulaw2lin(mulaw_data, 2)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
