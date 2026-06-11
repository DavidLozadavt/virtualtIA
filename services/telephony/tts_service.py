"""
TTS para telefonía — reutiliza edge-tts, preparado para G.711.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger("lyra.telephony.tts")


class TelephonyTTSService:
    """Genera audio de respuesta para llamadas telefónicas."""

    def __init__(self, voice: Optional[str] = None):
        self.voice = voice or settings.LYRA_TTS_VOICE or "es-BO-SofiaNeural"
        self.codec = (settings.TELEPHONY_AUDIO_CODEC or "PCMU").upper()

    async def synthesize_mp3(self, text: str) -> bytes:
        from core.voice_engine import get_voice_engine

        engine = get_voice_engine()
        audio = await engine.synthesize_to_bytes(text, voice=self.voice)
        return audio or b""

    async def synthesize_for_telephony(self, text: str) -> dict:
        """
        Genera audio listo para telefonía.

        Returns:
            {
              "mp3": bytes,
              "mulaw": bytes | None,  # si codec PCMU
              "format": "mp3" | "mulaw"
            }
        """
        mp3 = await self.synthesize_mp3(text)
        if not mp3:
            return {"mp3": b"", "mulaw": None, "format": "mp3"}

        result = {"mp3": mp3, "mulaw": None, "format": "mp3"}

        if self.codec in ("PCMU", "MULAW", "ULAW"):
            try:
                mulaw = _mp3_to_mulaw(mp3)
                result["mulaw"] = mulaw
                result["format"] = "mulaw"
                logger.info("[tts] synthesized %d mulaw bytes", len(mulaw))
            except Exception as e:
                logger.warning("[tts] mulaw conversion failed: %s — returning mp3", e)

        return result


def _mp3_to_mulaw(mp3_bytes: bytes) -> bytes:
    """
    MP3 → PCM 8kHz mono → µ-law.
    Requiere ffmpeg en PATH. Si no está, lanza excepción.
    """
    import subprocess
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_f:
        mp3_f.write(mp3_bytes)
        mp3_path = mp3_f.name

    pcm_path = mp3_path + ".pcm"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", mp3_path,
                "-ar", "8000", "-ac", "1", "-f", "s16le", pcm_path,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        import audioop

        with open(pcm_path, "rb") as f:
            pcm = f.read()
        return audioop.lin2ulaw(pcm, 2)
    finally:
        for p in (mp3_path, pcm_path):
            try:
                os.unlink(p)
            except OSError:
                pass
