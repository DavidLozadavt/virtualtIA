"""Pipeline modular de mejora de audio — contrato de etapa y ejecución.

Cada etapa es independiente: recibe un bloque `float32` a su tasa de trabajo y
devuelve otro bloque (posiblemente vacío si necesita acumular más muestras).
Ninguna etapa conoce a las demás; el orden y la composición se deciden en
`services/audio/__init__.py` a partir de la configuración.

Zonas de tasa de muestreo
-------------------------
Una etapa declara la tasa a la que quiere trabajar (`rate`, o `None` para
"la que venga"). El pipeline inserta automáticamente un remuestreador con
estado entre etapas de tasas distintas, así que la cancelación de eco puede
correr en el dominio nativo del teléfono (8 kHz, donde vive la referencia del
TTS) y la supresión neuronal en el dominio de banda ancha para el que se
entrenaron los modelos (16 kHz), sin que ninguna de las dos sepa de la otra.

Latencia
--------
El pipeline no añade buffering propio: la única latencia es la que cada etapa
necesite estructuralmente (tramas exactas del modelo, pre-roll del gate). Se
mide y se expone en `AudioPipeline.latency_ms` para poder auditarla.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

import numpy as np

from services.audio.frames import StreamResampler, float_to_pcm16, pcm16_to_float

logger = logging.getLogger("lyra.audio.pipeline")


@dataclass
class FrameContext:
    """Estado compartido por bloque, escrito por el pipeline y leído por etapas.

    `timestamp` es el reloj monotónico del instante en que el bloque **terminó**
    de recibirse (el de su última muestra). La referencia de eco lo toma como
    borde derecho de la ventana temporal, así que un desfase de un bloque aquí se
    traduce en un desfase de un bloque en la alineación del eco.
    """

    timestamp: float
    input_rate: int
    playback_active: bool = False
    speech_probability: float = 0.0
    speech_active: bool = False
    echo_detected: bool = False
    background_voice: bool = False
    notes: dict = field(default_factory=dict)


@runtime_checkable
class AudioStage(Protocol):
    """Etapa de procesamiento intercambiable."""

    name: str
    rate: Optional[int]

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        """Procesa un bloque float32 y devuelve el resultado (puede ser vacío)."""

    def reset(self) -> None:
        """Limpia el estado interno entre turnos/llamadas."""

    @property
    def latency_ms(self) -> float:
        """Retardo estructural que introduce la etapa, en milisegundos."""


class BaseStage:
    """Base común: nombre, tasa declarada, latencia nula y reset vacío."""

    name: str = "stage"
    rate: Optional[int] = None

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def reset(self) -> None:
        return None

    @property
    def latency_ms(self) -> float:
        return 0.0


@dataclass
class StageStats:
    blocks: int = 0
    samples_in: int = 0
    samples_out: int = 0
    cpu_sec: float = 0.0
    errors: int = 0

    def as_dict(self) -> dict:
        return {
            "blocks": self.blocks,
            "samples_in": self.samples_in,
            "samples_out": self.samples_out,
            "cpu_ms": round(self.cpu_sec * 1000.0, 1),
            "errors": self.errors,
        }


class AudioPipeline:
    """Ejecuta una secuencia de etapas sobre un flujo PCM16 continuo.

    Contrato de entrada/salida: bytes PCM16 mono a `input_rate` en ambos
    extremos, de modo que el llamante (transporte de voz) no cambia de formato.
    Una etapa que falle se desactiva para el resto de la llamada y el audio
    sigue fluyendo por las demás: una llamada nunca se cae por el pipeline.
    """

    def __init__(
        self,
        stages: list[AudioStage],
        *,
        input_rate: int,
        strict: bool = False,
    ):
        self.input_rate = input_rate
        self.strict = strict
        self._stages: list[AudioStage] = []
        self._bridges: list[Optional[StreamResampler]] = []
        self._stats: dict[str, StageStats] = {}
        self._disabled: set[str] = set()

        current_rate = input_rate
        for stage in stages:
            target = stage.rate or current_rate
            self._bridges.append(
                StreamResampler(current_rate, target) if target != current_rate else None
            )
            self._stages.append(stage)
            self._stats[stage.name] = StageStats()
            current_rate = target
        # Puente final de vuelta a la tasa del transporte.
        self._output_bridge = (
            StreamResampler(current_rate, input_rate)
            if current_rate != input_rate
            else None
        )
        self._output_rate = current_rate

    # ── introspección ──

    @property
    def stage_names(self) -> list[str]:
        return [s.name for s in self._stages]

    @property
    def active_stage_names(self) -> list[str]:
        return [s.name for s in self._stages if s.name not in self._disabled]

    @property
    def latency_ms(self) -> float:
        """Retardo estructural total del pipeline."""
        return round(
            sum(
                stage.latency_ms
                for stage in self._stages
                if stage.name not in self._disabled
            ),
            1,
        )

    def stats(self) -> dict:
        return {
            "stages": {name: st.as_dict() for name, st in self._stats.items()},
            "disabled": sorted(self._disabled),
            "latency_ms": self.latency_ms,
        }

    # ── ejecución ──

    def process_pcm(
        self,
        pcm: bytes,
        *,
        timestamp: float,
        playback_active: bool = False,
    ) -> tuple[bytes, FrameContext]:
        """Procesa un bloque PCM16 y devuelve (PCM16 procesado, contexto)."""
        ctx = FrameContext(
            timestamp=timestamp,
            input_rate=self.input_rate,
            playback_active=playback_active,
        )
        block = pcm16_to_float(pcm)
        block = self.process_block(block, ctx)
        return float_to_pcm16(block), ctx

    def process_block(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        for stage, bridge in zip(self._stages, self._bridges):
            if bridge is not None:
                block = bridge.process(block)
            if stage.name in self._disabled or block.size == 0:
                continue
            stats = self._stats[stage.name]
            started = time.perf_counter()
            try:
                result = stage.process(block, ctx)
            except Exception:
                stats.errors += 1
                self._disabled.add(stage.name)
                logger.exception(
                    "[audio] etapa %s deshabilitada tras un error", stage.name
                )
                if self.strict:
                    raise
                continue
            finally:
                stats.cpu_sec += time.perf_counter() - started
            stats.blocks += 1
            stats.samples_in += int(block.size)
            block = (
                result
                if result is not None
                else np.zeros(0, dtype=np.float32)
            )
            stats.samples_out += int(block.size)
        if self._output_bridge is not None:
            block = self._output_bridge.process(block)
        return block

    def reset(self) -> None:
        """Reinicia el estado de todas las etapas (nuevo turno de escucha)."""
        for stage in self._stages:
            try:
                stage.reset()
            except Exception:  # pragma: no cover - defensivo
                logger.debug("[audio] reset falló en %s", stage.name, exc_info=True)
        for bridge in self._bridges:
            if bridge is not None:
                bridge.reset()
        if self._output_bridge is not None:
            self._output_bridge.reset()
