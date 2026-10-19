"""TTS streaming por oración — edge-tts incremental → PCM 8 kHz mono.

edge-tts (>=7) entrega chunks MP3 según llegan del WebSocket del servicio
(verificado en edge_tts/communicate.py: yield por mensaje binario). Aquí cada
oración se decodifica en streaming vía un pipe de ffmpeg (mp3→s16le 8k mono),
de modo que el primer audio sale cientos de ms después del primer chunk de
texto, no al final de la síntesis completa.

Incluye caché en memoria de frases ya sintetizadas (saludo, confirmaciones
fijas) → esas frases suenan con latencia ~0 y sin red.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import AsyncIterator, Optional

from core.config import settings
from services.telephony.ffmpeg_bin import ffmpeg_executable
from services.voice.text_normalize import normalize_for_speech

logger = logging.getLogger("lyra.voice.tts")

SAMPLE_RATE = 8000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # PCM16 mono
# mod_audio_stream v1.0.3 (playback bidireccional) asume el segmento de stream
# terminado si no recibe datos en 100ms, y espera paquetes de ~20ms enviados
# cada ≤20ms (idealmente 10ms) — ver README.playback.md. Chunks de 200ms
# dejaban el módulo sin datos por más de su timeout entre cada envío: nunca
# alcanzaba a completar un ciclo de reproducción (cero eventos chunk_played).
_CHUNK_BYTES = 320  # 20 ms por chunk hacia el transporte
_CACHE_MAX_ENTRIES = 200


class TTSError(RuntimeError):
    """La síntesis falló de forma no recuperable para esta oración."""


class StreamingTTS:
    """Sintetiza oraciones de forma incremental y cachea frases repetidas."""

    def __init__(self, voice: Optional[str] = None):
        self.voice = voice or settings.LYRA_TTS_VOICE or "es-CO-SalomeNeural"
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    async def synthesize_sentence(self, sentence: str) -> AsyncIterator[bytes]:
        """Genera PCM16 8k mono en chunks de ~200 ms para una oración."""
        norm = normalize_for_speech(sentence)
        if not norm.strip():
            return

        cached = self._cache.get(norm)
        if cached is not None:
            self._cache.move_to_end(norm)
            for i in range(0, len(cached), _CHUNK_BYTES):
                yield cached[i : i + _CHUNK_BYTES]
            return

        collected = bytearray()
        async for chunk in self._synthesize_stream(norm):
            collected.extend(chunk)
            yield chunk

        if collected:
            self._cache[norm] = bytes(collected)
            self._cache.move_to_end(norm)
            while len(self._cache) > _CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)

    async def _synthesize_stream(self, norm_text: str) -> AsyncIterator[bytes]:
        """edge-tts (mp3 incremental) → ffmpeg pipe → PCM 8k, con timeout."""
        import edge_tts

        proc = await asyncio.create_subprocess_exec(
            ffmpeg_executable(),
            "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None and proc.stdout is not None

        stderr_buf = bytearray()

        async def _drain_stderr() -> None:
            assert proc.stderr is not None
            while True:
                data = await proc.stderr.read(4096)
                if not data:
                    return
                stderr_buf.extend(data)

        async def _feed_mp3() -> None:
            communicate = edge_tts.Communicate(norm_text, self.voice)
            try:
                async for message in communicate.stream():
                    if message["type"] == "audio" and message["data"]:
                        proc.stdin.write(message["data"])
                        await proc.stdin.drain()
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        stderr_task = asyncio.create_task(_drain_stderr())
        feeder = asyncio.create_task(_feed_mp3())
        deadline = asyncio.get_running_loop().time() + float(
            settings.VOICE_TTS_TIMEOUT_SEC
        )
        got_audio = False
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TTSError(
                        f"TTS timeout ({settings.VOICE_TTS_TIMEOUT_SEC}s) "
                        f"voice={self.voice} text={norm_text[:60]!r}"
                    )
                chunk = await asyncio.wait_for(
                    proc.stdout.read(_CHUNK_BYTES), timeout=remaining
                )
                if not chunk:
                    break
                got_audio = True
                yield chunk

            await asyncio.wait_for(feeder, timeout=5.0)
            if not got_audio:
                raise TTSError(
                    f"TTS produced no audio voice={self.voice} "
                    f"text={norm_text[:60]!r} ffmpeg={stderr_buf[:200]!r}"
                )
        except asyncio.TimeoutError as exc:
            raise TTSError(
                f"TTS timeout voice={self.voice} text={norm_text[:60]!r}"
            ) from exc
        except Exception as exc:
            if isinstance(exc, TTSError):
                raise
            raise TTSError(
                f"TTS failed voice={self.voice} text={norm_text[:60]!r}: {exc}"
            ) from exc
        finally:
            feeder.cancel()
            stderr_task.cancel()
            for task in (feeder, stderr_task):
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if proc.returncode is None:
                proc.kill()
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass

    async def prewarm(self, phrases: list[str]) -> None:
        """Sintetiza frases fijas al arrancar (llenan la caché sin bloquear)."""
        for phrase in phrases:
            norm = normalize_for_speech(phrase)
            if not norm.strip() or norm in self._cache:
                continue
            try:
                async for _ in self.synthesize_sentence(phrase):
                    pass
            except TTSError as e:
                logger.warning("[tts] prewarm failed for %r: %s", phrase[:60], e)
                return
