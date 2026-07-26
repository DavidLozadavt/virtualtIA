"""Síntesis de voz — OpenAI `gpt-4o-mini-tts` en streaming.

Único motor de TTS del sistema. Se eligió por lo que ningún otro ofrece: el
bloque `instructions` que gobierna la INTERPRETACIÓN (ritmo, pausas,
entonación, fonética) en vez de dejarla al azar del modelo. Ese bloque viaja
en cada solicitud (`services/voice/tts_prompt.py`): el resultado no es una
lectura, es una operadora hablando.

Streaming de verdad, en dos niveles:

* del proveedor — `with_streaming_response` entrega los bytes de audio según se
  generan, no al terminar la frase;
* hacia el transporte — cada bloque se remuestrea al instante de los 24 kHz del
  modelo a los 8 kHz de la línea telefónica (`StreamResampler` conserva el
  estado del filtro entre bloques, así que el resultado es idéntico a
  remuestrear la frase completa: ni un clic en las fronteras) y sale en chunks
  de 20 ms. El primer audio no espera a la última sílaba.

Frente al motor anterior (edge-tts → MP3 → pipe de ffmpeg) desaparecen un
proceso por frase y la decodificación intermedia: el camino es más corto, no
más largo.

Incluye caché en memoria de frases ya sintetizadas (saludo, confirmaciones
fijas) → esas frases suenan con latencia ~0 y sin red.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import AsyncIterator, Optional

import numpy as np

from core.config import settings
from services.audio.frames import StreamResampler
from services.voice.text_normalize import prepare_for_speech
from services.voice.tts_prompt import speech_instructions

logger = logging.getLogger("lyra.voice.tts")

SAMPLE_RATE = 8000
# `response_format="pcm"` de la API: s16le mono a 24 kHz, sin cabecera.
PROVIDER_SAMPLE_RATE = 24000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # PCM16 mono
# mod_audio_stream v1.0.3 (playback bidireccional) asume el segmento de stream
# terminado si no recibe datos en 100ms, y espera paquetes de ~20ms enviados
# cada ≤20ms (idealmente 10ms) — ver README.playback.md. Chunks de 200ms
# dejaban el módulo sin datos por más de su timeout entre cada envío: nunca
# alcanzaba a completar un ciclo de reproducción (cero eventos chunk_played).
_CHUNK_BYTES = 320  # 20 ms por chunk hacia el transporte
_CACHE_MAX_ENTRIES = 200

# Voces del modelo. Ninguna es "de idioma": la fonética la fijan el texto en
# español y las instrucciones, así que la voz solo elige el timbre. `coral` es
# la voz cálida y cercana de atención al cliente.
OPENAI_VOICES = frozenset(
    {
        "alloy", "ash", "ballad", "coral", "echo", "fable",
        "nova", "onyx", "sage", "shimmer", "verse",
    }
)
DEFAULT_VOICE = "coral"


class TTSError(RuntimeError):
    """La síntesis falló de forma no recuperable para esta oración."""


def resolve_voice(name: Optional[str]) -> str:
    """Nombre de voz válido para el modelo, o el configurado por defecto."""
    candidate = (name or "").strip().lower()
    if candidate in OPENAI_VOICES:
        return candidate
    if candidate:
        logger.warning(
            "[tts] voz %r no existe en OpenAI TTS — se usa la configurada", name
        )
    configured = (settings.VOICE_TTS_VOICE or "").strip().lower()
    return configured if configured in OPENAI_VOICES else DEFAULT_VOICE


_client = None


def get_tts_client():
    """Cliente OpenAI compartido (una conexión TLS reutilizada por todas las
    frases: abrirla por síntesis costaba más que sintetizar)."""
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        key = settings.openai_audio_key()
        if not key:
            raise TTSError(
                "TTS sin credencial: se necesita una key real de OpenAI "
                "(OPENAI_API_KEY / OPENAI_WHISPER_KEY). OpenRouter (sk-or...) "
                "no sintetiza audio."
            )
        # Sin reintentos internos: el reintento lo decide quien conoce el turno
        # (el runtime reintenta la oración), y aquí un reintento oculto solo
        # añadiría latencia invisible.
        _client = AsyncOpenAI(api_key=key, max_retries=0)
    return _client


async def stream_speech(
    text: str,
    *,
    voice: Optional[str] = None,
    response_format: str = "pcm",
    pace: float = 1.0,
    timeout: Optional[float] = None,
) -> AsyncIterator[bytes]:
    """Bytes del proveedor tal como llegan, sin bufferear la frase completa.

    `response_format="pcm"` para la línea telefónica (se remuestrea aquí mismo);
    `"mp3"` para el canal navegador, que reproduce el formato tal cual.
    """
    clean = (text or "").strip()
    if not clean:
        return

    client = get_tts_client()
    limit = float(timeout if timeout is not None else settings.VOICE_TTS_TIMEOUT_SEC)
    try:
        async with client.audio.speech.with_streaming_response.create(
            model=settings.VOICE_TTS_MODEL,
            voice=resolve_voice(voice),
            input=clean,
            instructions=speech_instructions(pace),
            response_format=response_format,
            timeout=limit,
        ) as response:
            async for chunk in response.iter_bytes():
                if chunk:
                    yield chunk
    except asyncio.CancelledError:
        raise
    except TTSError:
        raise
    except Exception as exc:  # noqa: BLE001 — cualquier fallo del proveedor
        raise TTSError(
            f"TTS falló model={settings.VOICE_TTS_MODEL} "
            f"voice={resolve_voice(voice)} text={clean[:60]!r}: {exc}"
        ) from exc


async def _with_deadline(
    source: AsyncIterator[bytes], limit: float, label: str
) -> AsyncIterator[bytes]:
    """Acota el tiempo TOTAL de una síntesis: una frase que no termina cuelga el
    turno, y un turno colgado es una llamada perdida."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    iterator = source.__aiter__()
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TTSError(f"TTS timeout ({limit}s) {label}")
            try:
                chunk = await asyncio.wait_for(
                    iterator.__anext__(), timeout=remaining
                )
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                raise TTSError(f"TTS timeout ({limit}s) {label}") from exc
            yield chunk
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001 — cerrar nunca rompe el turno
                pass


def _to_pcm16(samples: np.ndarray) -> bytes:
    scaled = np.clip(samples * 32768.0, -32768.0, 32767.0)
    return scaled.astype("<i2").tobytes()


class StreamingTTS:
    """Sintetiza oraciones de forma incremental y cachea frases repetidas."""

    def __init__(self, voice: Optional[str] = None):
        self.voice = resolve_voice(voice or settings.VOICE_TTS_VOICE)
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    async def synthesize_sentence(self, sentence: str) -> AsyncIterator[bytes]:
        """Genera PCM16 8k mono en chunks de 20 ms para una oración."""
        norm = prepare_for_speech(sentence)
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
        """PCM 24k del modelo → 8k telefónico, remuestreado al vuelo."""
        resampler = StreamResampler(PROVIDER_SAMPLE_RATE, SAMPLE_RATE)
        label = f"voice={self.voice} text={norm_text[:60]!r}"
        source = stream_speech(norm_text, voice=self.voice, response_format="pcm")

        leftover = b""
        pending = bytearray()
        got_audio = False
        async for raw in _with_deadline(
            source, float(settings.VOICE_TTS_TIMEOUT_SEC), label
        ):
            data = leftover + raw
            # El stream puede cortar un sample en dos: el byte suelto espera al
            # bloque siguiente en vez de convertirse en un chasquido.
            usable = len(data) - (len(data) % 2)
            leftover = data[usable:]
            if not usable:
                continue

            samples = (
                np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0
            )
            out = resampler.process(samples)
            if out.size == 0:
                continue
            pending.extend(_to_pcm16(out))
            while len(pending) >= _CHUNK_BYTES:
                got_audio = True
                yield bytes(pending[:_CHUNK_BYTES])
                del pending[:_CHUNK_BYTES]

        if pending:
            got_audio = True
            yield bytes(pending)

        if not got_audio:
            raise TTSError(f"TTS no produjo audio {label}")

    async def prewarm(self, phrases: list[str]) -> None:
        """Sintetiza frases fijas al arrancar (llenan la caché sin bloquear)."""
        for phrase in phrases:
            norm = prepare_for_speech(phrase)
            if not norm.strip() or norm in self._cache:
                continue
            try:
                async for _ in self.synthesize_sentence(phrase):
                    pass
            except TTSError as e:
                logger.warning("[tts] prewarm failed for %r: %s", phrase[:60], e)
                return
