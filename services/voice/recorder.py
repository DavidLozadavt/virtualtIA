"""Grabación de llamada completa del lado servidor.

V1 dependía de `record_session` en FreeSWITCH + subida del WAV al colgar.
En V2 ambos sentidos del audio ya pasan por el servidor (frames del usuario
por el WS de mod_audio_stream, PCM del TTS que nosotros mismos generamos),
así que la grabación se mezcla aquí y se escribe directamente en
FREESWITCH_RECORDINGS_DIR/{call_uuid}.wav — el mismo contrato de archivo que
sirve GET /freeswitch/recording/{call_uuid}.wav al panel del operador.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

from core.config import settings

logger = logging.getLogger("lyra.voice.recorder")

SAMPLE_RATE = 8000


def sanitize_recording_id(value: str) -> str:
    return value.replace(".wav", "").replace("/", "").replace("\\", "").strip()


def recording_path(call_uuid: str) -> Path:
    safe = sanitize_recording_id(call_uuid)
    rec_dir = Path(settings.FREESWITCH_RECORDINGS_DIR or "data/freeswitch_recordings")
    rec_dir.mkdir(parents=True, exist_ok=True)
    return rec_dir / f"{safe}.wav"


class CallRecorder:
    """Mezcla la pista del usuario (near) y la del bot (far) en un WAV mono.

    La pista near marca la línea de tiempo (llega en tiempo real desde
    FreeSWITCH); cada chunk del bot se ancla a la posición near del momento
    en que se envió al playback, que el pacing mantiene casi-real-time.
    """

    def __init__(self, call_uuid: str):
        self.call_uuid = call_uuid
        self._near: list[bytes] = []
        self._near_samples = 0
        self._far_segments: list[tuple[int, bytes]] = []

    def add_user_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        self._near.append(pcm)
        self._near_samples += len(pcm) // 2

    def add_bot_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        self._far_segments.append((self._near_samples, pcm))

    def write_wav(self) -> Path | None:
        if not self._near and not self._far_segments:
            return None

        near = (
            np.frombuffer(b"".join(self._near), dtype=np.int16).astype(np.int32)
            if self._near
            else np.zeros(0, dtype=np.int32)
        )
        total = near.size
        for pos, pcm in self._far_segments:
            total = max(total, pos + len(pcm) // 2)

        mix = np.zeros(total, dtype=np.int32)
        mix[: near.size] = near
        for pos, pcm in self._far_segments:
            seg = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
            mix[pos : pos + seg.size] += seg

        out = np.clip(mix, -32768, 32767).astype(np.int16)
        path = recording_path(self.call_uuid)
        try:
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(out.tobytes())
        except OSError as e:
            logger.error(
                "[recorder] write failed call_uuid=%s err=%s", self.call_uuid, e
            )
            return None
        logger.info(
            "[recorder] saved call_uuid=%s path=%s seconds=%.1f",
            self.call_uuid,
            path,
            out.size / SAMPLE_RATE,
        )
        return path
