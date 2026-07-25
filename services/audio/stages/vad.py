"""Aislamiento de voz — detección neuronal de habla y puerta retroactiva.

Esta es la etapa que decide qué llega al reconocedor. Su criterio no es "hay
energía" sino "hay voz humana", y esa diferencia es la que elimina televisión,
música, motores, viento, ladridos, llanto y tráfico: todos ellos tienen energía
de sobra y ninguno es voz.

Detector
--------
Silero VAD (v6, MIT) ejecutado directamente sobre ONNX Runtime. Se eligió por
tres razones medibles, no por popularidad:

* Es el único VAD neuronal de uso extendido con **modelo nativo de 8 kHz** (no
  remuestrea internamente): el audio telefónico entra sin adaptaciones.
* En el benchmark público de rechazo de ruido no-voz (ESC-50: ladridos, llanto,
  motores, viento, lluvia) marca 0.87 de acierto, frente a 0.42 del siguiente
  competidor neuronal y 0.00 del VAD de WebRTC — que por diseño clasifica casi
  cualquier ruido ambiental como voz porque su objetivo original era no perder
  audio en un códec, no discriminar.
* Coste: <1 ms por trama de 32 ms en un solo hilo de CPU.

Se reimplementa el envoltorio de inferencia (unas pocas decenas de líneas: 32
muestras de contexto arrastrado y estado recurrente de forma (2,1,128)) en lugar
de instalar el paquete `silero-vad`, que arrastra `torch` y `torchaudio` como
dependencias obligatorias. El modelo es un archivo intercambiable
(`AUDIO_VAD_MODEL_PATH`): sustituirlo por otro detector es cambiar una clase que
implemente `SpeechDetector`.

Puerta retroactiva
------------------
Un detector, por bueno que sea, confirma la voz unas tramas después de que
empezó. Silenciar esas primeras tramas decapita la consonante inicial y arruina
justo la palabra más informativa. Por eso la puerta retiene `pre_roll` tramas y
abre hacia atrás cuando confirma habla: el reconocedor recibe el ataque completo
de la palabra. El precio es un retardo estructural igual al pre-roll (~100 ms
configurables), y es el único buffering deliberado del pipeline.

El no-habla se **atenúa hasta silencio digital, no se descarta**: el flujo debe
conservar su línea de tiempo porque el detector de fin de enunciado del
reconocedor remoto mide silencio para cerrar el turno. Descartar tramas haría
que nunca viera silencio y el turno no cerraría nunca.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

from services.audio.frames import FrameSlicer, rms
from services.audio.pipeline import BaseStage, FrameContext

logger = logging.getLogger("lyra.audio.vad")

# Tamaños exigidos por el modelo Silero (32 ms en ambos casos) y contexto que
# el propio modelo espera recibir concatenado por delante de cada trama.
SILERO_FRAME_SIZES = {8000: 256, 16000: 512}
SILERO_CONTEXT_SIZES = {8000: 32, 16000: 64}
SILERO_STATE_SHAPE = (2, 1, 128)


class SpeechDetector(Protocol):
    """Detector de voz intercambiable: una probabilidad por trama exacta."""

    frame_size: int

    def probability(self, frame: np.ndarray) -> float:
        """Probabilidad de voz humana en [0, 1] para una trama de `frame_size`."""

    def reset(self) -> None:
        """Olvida el estado recurrente (nueva llamada o nuevo turno)."""


class SileroOnnxDetector:
    """Silero VAD sobre ONNX Runtime, sin dependencia de torch."""

    def __init__(self, model_path: str, rate: int, threads: int = 1):
        if rate not in SILERO_FRAME_SIZES:
            raise ValueError(f"Silero VAD admite 8000/16000 Hz, no {rate}")
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"modelo Silero VAD no encontrado: {path}")
        if "16k" in path.name and rate != 16000:
            raise ValueError(
                f"{path.name} es un modelo solo de 16 kHz; se pidió {rate} Hz"
            )
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = threads
        options.intra_op_num_threads = threads
        self._session = onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"], sess_options=options
        )
        self.rate = int(rate)
        self.frame_size = SILERO_FRAME_SIZES[rate]
        self._context_size = SILERO_CONTEXT_SIZES[rate]
        self._sr = np.array(rate, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros(SILERO_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros((1, self._context_size), dtype=np.float32)

    def probability(self, frame: np.ndarray) -> float:
        if frame.size != self.frame_size:
            raise ValueError(
                f"trama de {frame.size} muestras; el modelo exige {self.frame_size}"
            )
        chunk = frame.astype(np.float32, copy=False).reshape(1, -1)
        model_input = np.concatenate((self._context, chunk), axis=1)
        output, self._state = self._session.run(
            None, {"input": model_input, "state": self._state, "sr": self._sr}
        )
        self._context = model_input[:, -self._context_size :]
        return float(output[0][0])


class EnergyDetector:
    """Respaldo sin modelo: piso de ruido adaptativo + presencia de banda de voz.

    No pretende igualar a un detector neuronal — no distingue una voz de una
    televisión. Existe para que la ausencia del archivo de modelo degrade el
    sistema en vez de romperlo, y para que el pipeline sea probable sin
    dependencias binarias. Su uso se registra como advertencia.
    """

    def __init__(self, rate: int, frame_size: int, snr_db: float = 8.0):
        self.rate = int(rate)
        self.frame_size = int(frame_size)
        self.snr_db = float(snr_db)
        self._floor: Optional[float] = None

    def reset(self) -> None:
        self._floor = None

    def probability(self, frame: np.ndarray) -> float:
        level = rms(frame)
        if self._floor is None:
            self._floor = max(level, 1e-5)
        # El piso sube despacio y baja rápido: así una voz sostenida no lo
        # arrastra hacia arriba, pero un cambio de ambiente se aprende pronto.
        alpha = 0.995 if level > self._floor else 0.9
        self._floor = alpha * self._floor + (1.0 - alpha) * level
        if level <= 0.0:
            return 0.0
        snr = 20.0 * np.log10(max(level, 1e-9) / max(self._floor, 1e-9))
        return float(np.clip((snr - self.snr_db) / 12.0 + 0.5, 0.0, 1.0))


class VoiceGateStage(BaseStage):
    """Puerta de voz con histéresis, pre-roll retroactivo y colgado (hangover)."""

    name = "voice_gate"

    def __init__(
        self,
        detector: SpeechDetector,
        *,
        rate: int,
        threshold: float,
        release_margin: float,
        min_speech_ms: float,
        hangover_ms: float,
        pre_roll_ms: float,
        attenuation: float,
        echo_penalty: float,
        echo_hold_ms: float,
        background_penalty: float,
    ):
        self.rate = int(rate)
        self.detector = detector
        self.threshold = float(threshold)
        self.release_margin = float(release_margin)
        self.attenuation = float(attenuation)
        self.echo_penalty = float(echo_penalty)
        self.background_penalty = float(background_penalty)

        frame = detector.frame_size
        self._frame_ms = frame / self.rate * 1000.0
        self._slicer = FrameSlicer(frame)
        self._min_speech_frames = max(1, round(min_speech_ms / self._frame_ms))
        self._hangover_frames = max(0, round(hangover_ms / self._frame_ms))
        # El pre-roll debe cubrir al menos la confirmación mínima de habla; si no,
        # las tramas que la confirmaron ya habrían salido silenciadas.
        self._pre_roll_frames = max(
            self._min_speech_frames, round(pre_roll_ms / self._frame_ms)
        )
        self._echo_hold_frames = max(0, round(echo_hold_ms / self._frame_ms))
        self._queue: deque[list] = deque()
        self._consecutive = 0
        self._release = 0
        self._echo_hold = 0
        self._open = False
        self.frames_total = 0
        self.frames_voiced = 0
        self.frames_echo_penalized = 0

    @property
    def latency_ms(self) -> float:
        return round(self._pre_roll_frames * self._frame_ms, 1)

    def reset(self) -> None:
        self.detector.reset()
        self._slicer.reset()
        self._queue.clear()
        self._consecutive = 0
        self._release = 0
        self._echo_hold = 0
        self._open = False

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        produced: list[np.ndarray] = []
        for frame in self._slicer.push(block):
            produced.append(self._process_frame(frame, ctx))
        return (
            np.concatenate(produced)
            if produced
            else np.zeros(0, dtype=np.float32)
        )

    def _process_frame(self, frame: np.ndarray, ctx: FrameContext) -> np.ndarray:
        self.frames_total += 1
        probability = self.detector.probability(frame)
        ctx.speech_probability = probability

        # Eco y voz de fondo no se pueden rechazar por timbre: son voz humana de
        # verdad (la de Lyra, la del televisor) y el detector las acepta con
        # razón. Lo que se hace es **exigir más evidencia** mientras están
        # presentes: la voz del interlocutor llega con probabilidad muy alta,
        # mientras que un residual de eco ya cancelado o una voz lejana se quedan
        # cortos. Una penalización ≥ 1.0 equivale a un veto absoluto.
        # El colgado de eco evita que un hueco momentáneo en la detección abra la
        # puerta justo entre dos sílabas del propio eco.
        if ctx.echo_detected:
            self._echo_hold = self._echo_hold_frames
        elif self._echo_hold > 0:
            self._echo_hold -= 1

        penalty = 0.0
        if self._echo_hold > 0 or ctx.echo_detected:
            penalty = max(penalty, self.echo_penalty)
            self.frames_echo_penalized += 1
        if ctx.background_voice:
            penalty = max(penalty, self.background_penalty)

        required = self.threshold + penalty
        if self._open:
            required -= self.release_margin
        speaking = probability >= required

        if speaking:
            self._consecutive += 1
        else:
            self._consecutive = 0

        if not self._open and self._consecutive >= self._min_speech_frames:
            self._open = True
            self._release = self._hangover_frames
            # Apertura retroactiva: las tramas aún retenidas pertenecen al ataque
            # de la palabra y deben salir sin atenuar.
            for pending in self._queue:
                pending[1] = True
        elif self._open:
            if speaking:
                self._release = self._hangover_frames
            elif self._release > 0:
                self._release -= 1
            else:
                self._open = False

        ctx.speech_active = self._open
        self._queue.append([frame, self._open])

        if len(self._queue) <= self._pre_roll_frames:
            return np.zeros(0, dtype=np.float32)

        oldest, gate_open = self._queue.popleft()
        if gate_open:
            self.frames_voiced += 1
            return oldest
        if self.attenuation <= 0.0:
            return np.zeros(oldest.size, dtype=np.float32)
        return (oldest * self.attenuation).astype(np.float32, copy=False)

    def stats(self) -> dict:
        return {
            "frames_total": self.frames_total,
            "frames_voiced": self.frames_voiced,
            "frames_echo_penalized": self.frames_echo_penalized,
            "voiced_ratio": round(
                self.frames_voiced / self.frames_total if self.frames_total else 0.0, 3
            ),
        }
