"""
Almacén de archivos TTS para reproducción por FreeSWITCH (playback).

Genera WAV 8 kHz mono — compatible con mod_curl + playback en FS.
Evita respuestas HTTP >64 KB con audio_base64.
"""

from __future__ import annotations

import audioop
import logging
import subprocess
import tempfile
import time
import uuid
import wave
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from core.config import settings

logger = logging.getLogger("lyra.telephony.tts_files")

_MAX_FILES = 300
_TTL_SEC = 3600


class TTSFileStore:
    def __init__(self) -> None:
        self._dir = Path(settings.FREESWITCH_TTS_CACHE_DIR or "data/freeswitch_tts")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _prune(self) -> None:
        files = sorted(self._dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        if len(files) <= _MAX_FILES:
            return
        for p in files[: len(files) - _MAX_FILES]:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def save_telephony_audio(
        self,
        tts_result: dict,
        *,
        call_uuid: str = "",
    ) -> Tuple[str, Path]:
        """
        Persiste audio telefónico como WAV 8kHz mono.

        Returns: (audio_id, absolute_path)
        """
        audio_id = uuid.uuid4().hex[:16]
        wav_bytes = _to_wav_8k_mono(tts_result)
        if not wav_bytes:
            raise ValueError("TTS produced empty audio")

        path = self._dir / f"{audio_id}.wav"
        path.write_bytes(wav_bytes)
        self._prune()

        logger.info(
            "[tts_files] generated call_uuid=%s audio_id=%s path=%s size=%d bytes",
            call_uuid,
            audio_id,
            path,
            len(wav_bytes),
        )
        return audio_id, path

    def get_path(self, audio_id: str) -> Optional[Path]:
        safe = audio_id.replace(".wav", "").replace("/", "").replace("\\", "")
        path = self._dir / f"{safe}.wav"
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > _TTL_SEC:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return path


def build_audio_url(audio_id: str, request: Optional[object] = None) -> str:
    """Construye URL HTTP para FreeSWITCH playback."""
    base = (settings.FREESWITCH_HTTP_BASE_URL or "").rstrip("/")
    if not base and request is not None:
        try:
            base = str(request.base_url).rstrip("/")
        except Exception:
            pass
    if not base:
        base = f"http://{settings.HOST}:{settings.PORT}"
        if settings.HOST == "0.0.0.0":
            base = f"http://127.0.0.1:{settings.PORT}"
    return f"{base}/freeswitch/audio-file/{audio_id}.wav"


def _to_wav_8k_mono(tts_result: dict) -> bytes:
    if tts_result.get("mulaw"):
        return _mulaw_bytes_to_wav(tts_result["mulaw"], settings.TELEPHONY_SAMPLE_RATE or 8000)
    if tts_result.get("mp3"):
        return _mp3_bytes_to_wav_8k(tts_result["mp3"])
    return b""


def _mulaw_bytes_to_wav(mulaw_data: bytes, sample_rate: int) -> bytes:
    pcm = audioop.ulaw2lin(mulaw_data, 2)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _mp3_bytes_to_wav_8k(mp3_bytes: bytes) -> bytes:
    import os

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_f:
        mp3_f.write(mp3_bytes)
        mp3_path = mp3_f.name

    wav_path = mp3_path + ".wav"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", mp3_path,
                "-ar", "8000", "-ac", "1", "-f", "wav", wav_path,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return Path(wav_path).read_bytes()
    finally:
        for p in (mp3_path, wav_path):
            try:
                os.unlink(p)
            except OSError:
                pass


_store: Optional[TTSFileStore] = None


def get_tts_file_store() -> TTSFileStore:
    global _store
    if _store is None:
        _store = TTSFileStore()
    return _store
