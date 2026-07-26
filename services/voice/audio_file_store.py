"""Almacén de audio de respuesta para reproducción vía ESL uuid_broadcast.

Pivote de arquitectura (2026-07-19): mod_audio_stream v1.0.3 (playback vía
streamAudio por WS) no inyecta audio en el canal pese a seguir la
documentación al pie de la letra (STREAM_PLAYBACK, formato JSON, chunking a
20ms) — confirmado con logs de FreeSWITCH y el vendor pendiente de contactar.
Mientras se resuelve con soporte, el playback vuelve al mecanismo YA PROBADO
en producción: FreeSWITCH reproduce un WAV local vía `uuid_broadcast` (ESL).

Requiere un volumen compartido entre el proceso Python y el contenedor
FreeSWITCH: Lyra escribe en `FREESWITCH_TTS_SHARED_DIR` (ruta del host) y
FreeSWITCH lo lee como `FREESWITCH_TTS_CONTAINER_DIR` (mismo contenido, bind
mount) — NO se sirve por HTTP para no depender de mod_httapi/http_cache,
que esta imagen mínima probablemente no trae.

La captura del usuario (WS de mod_audio_stream, STT streaming) no cambia en
absoluto — solo cambia cómo sale el audio del bot.
"""

from __future__ import annotations

import logging
import time
import uuid
import wave
from pathlib import Path
from typing import Tuple

from core.config import settings

logger = logging.getLogger("lyra.voice.audio_store")

SAMPLE_RATE = 8000
_MAX_FILES = 500
_TTL_SEC = 1800


def sanitize_audio_id(value: str) -> str:
    return value.replace(".wav", "").replace("/", "").replace("\\", "").strip()


class AudioFileStore:
    """Escribe WAV 8kHz mono en el directorio compartido con FreeSWITCH."""

    def __init__(self) -> None:
        self._dir = Path(settings.FREESWITCH_TTS_SHARED_DIR or "data/freeswitch_tts_shared")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._container_dir = (settings.FREESWITCH_TTS_CONTAINER_DIR or "/tmp/lyra-tts").rstrip("/")

    def _prune(self) -> None:
        files = sorted(self._dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        if len(files) > _MAX_FILES:
            for p in files[: len(files) - _MAX_FILES]:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        now = time.time()
        for p in files:
            try:
                if now - p.stat().st_mtime > _TTL_SEC:
                    p.unlink(missing_ok=True)
            except OSError:
                pass

    def save_pcm(self, pcm: bytes, *, call_uuid: str) -> Tuple[str, str, float]:
        """Escribe `pcm` (16-bit mono 8kHz) como WAV.

        Returns: (audio_id, ruta_dentro_del_contenedor, duración_segundos).
        ID único por turno (no reusar call_uuid): un archivo con el mismo
        nombre que uno reproducido segundos antes puede quedar cacheado por
        FreeSWITCH/el filesystem del contenedor.
        """
        audio_id = f"{sanitize_audio_id(call_uuid)}-{uuid.uuid4().hex[:8]}"
        path = self._dir / f"{audio_id}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
        self._prune()

        duration = len(pcm) / 2 / SAMPLE_RATE
        container_path = f"{self._container_dir}/{audio_id}.wav"
        logger.info(
            "[audio_store] saved call_uuid=%s audio_id=%s duration=%.2fs path=%s",
            call_uuid,
            audio_id,
            duration,
            container_path,
        )
        return audio_id, container_path, duration


_store: AudioFileStore | None = None


def get_audio_file_store() -> AudioFileStore:
    global _store
    if _store is None:
        _store = AudioFileStore()
    return _store
