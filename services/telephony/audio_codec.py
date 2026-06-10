"""Utilidades de audio para telefonía PSTN (8 kHz µ-law / PCM)."""

from __future__ import annotations

import io
import logging
import wave
from typing import Optional

try:
    import audioop
except ImportError:  # Python 3.13+
    import audioop_lts as audioop  # type: ignore

logger = logging.getLogger("lyra.telephony.audio")

SAMPLE_RATE = 8000
SAMPLE_WIDTH = 2  # 16-bit PCM


def ulaw_to_wav_bytes(ulaw_data: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convierte µ-law 8 kHz mono a WAV para STT."""
    pcm = audioop.ulaw2lin(ulaw_data, SAMPLE_WIDTH)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    return audioop.lin2ulaw(pcm_data, SAMPLE_WIDTH)


def mp3_to_ulaw(mp3_bytes: bytes) -> Optional[bytes]:
    """Convierte MP3 (edge-tts) a µ-law 8 kHz mono. Requiere pydub + ffmpeg."""
    try:
        from pydub import AudioSegment

        seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
        seg = seg.set_frame_rate(SAMPLE_RATE).set_channels(1).set_sample_width(SAMPLE_WIDTH)
        pcm = seg.raw_data
        return pcm_to_ulaw(pcm)
    except Exception as e:
        logger.error(f"mp3_to_ulaw failed: {e}")
        return None


def pcm_rms(pcm_chunk: bytes) -> float:
    """Energía RMS de un chunk PCM 16-bit."""
    if len(pcm_chunk) < 2:
        return 0.0
    return float(audioop.rms(pcm_chunk, SAMPLE_WIDTH))
