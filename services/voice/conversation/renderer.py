"""Speech Renderer — ejecuta un plan y devuelve el audio de la respuesta.

Un plan se convierte en UN solo buffer PCM: las etapas habladas se sintetizan
con el TTS existente (sin tocarlo: recibe una función `synth`), las pausas se
materializan como silencio y el fondo contextual como su lecho de sonido. Un
único buffer significa una única reproducción, así que las pausas no cuestan
ni una conexión extra ni latencia añadida.

También devuelve las marcas temporales de cada etapa hablada, que el runtime
usa para saber qué alcanzó a oír el usuario si interrumpe.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from services.voice.conversation.ambient import AmbientSoundManager
from services.voice.conversation.plan import SegmentKind, SpeechPlan

logger = logging.getLogger("lyra.voice.conversation.renderer")

SAMPLE_RATE = 8000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # PCM16 mono

# Síntesis simultánea de etapas. Cada una abre un proceso de decodificación;
# más de tres en paralelo no mejora el tiempo total y sí carga el host.
_MAX_PARALLEL_SYNTH = 3

SynthFn = Callable[[str], Awaitable[bytes]]


@dataclass
class RenderedSpeech:
    pcm: bytes = b""
    text: str = ""
    marks: list[tuple[float, str]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return len(self.pcm) / BYTES_PER_SECOND

    def __bool__(self) -> bool:
        return bool(self.pcm)


def silence(duration: float, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    samples = int(max(0.0, duration) * sample_rate)
    return b"\x00\x00" * samples


class SpeechRenderer:
    """Plan → PCM. No conoce el TTS ni el transporte: recibe `synth`."""

    def __init__(
        self,
        ambient: Optional[AmbientSoundManager] = None,
        *,
        sample_rate: int = SAMPLE_RATE,
    ):
        self.sample_rate = sample_rate
        self._ambient = ambient or AmbientSoundManager(sample_rate=sample_rate)

    async def render(self, plan: SpeechPlan, synth: SynthFn) -> RenderedSpeech:
        speech_segments = plan.speech_segments
        if not speech_segments:
            return RenderedSpeech()

        audio_by_text = await self._synthesize(speech_segments, synth)

        pcm = bytearray()
        marks: list[tuple[float, str]] = []
        spoken: list[str] = []
        for segment in plan.segments:
            if segment.kind is SegmentKind.SPEECH:
                chunk = audio_by_text.get(segment.text, b"")
                if not chunk:
                    continue
                pcm.extend(chunk)
                # La marca apunta al FINAL de la etapa: es lo que el usuario
                # alcanzó a oír si interrumpe justo después.
                marks.append((len(pcm) / (self.sample_rate * 2), segment.text))
                spoken.append(segment.text)
            elif segment.kind is SegmentKind.PAUSE:
                pcm.extend(silence(segment.duration, sample_rate=self.sample_rate))
            elif segment.kind is SegmentKind.AMBIENT:
                pcm.extend(
                    self._ambient.bed(
                        segment.ambient,
                        segment.duration,
                        sample_rate=self.sample_rate,
                    )
                )

        return RenderedSpeech(
            pcm=bytes(pcm), text=" ".join(spoken).strip(), marks=marks
        )

    async def _synthesize(self, segments, synth: SynthFn) -> dict[str, bytes]:
        """Sintetiza las etapas únicas en paralelo acotado."""
        texts: list[str] = []
        for segment in segments:
            if segment.text and segment.text not in texts:
                texts.append(segment.text)

        semaphore = asyncio.Semaphore(_MAX_PARALLEL_SYNTH)

        async def _one(text: str) -> bytes:
            async with semaphore:
                return await synth(text)

        results = await asyncio.gather(
            *(_one(t) for t in texts), return_exceptions=True
        )
        audio: dict[str, bytes] = {}
        for text, result in zip(texts, results):
            if isinstance(result, BaseException):
                raise result
            audio[text] = result or b""
        return audio
