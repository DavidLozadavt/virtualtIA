"""Pipeline de mejora de audio de captura — ensamblaje y fachada pública.

Arquitectura (el orden lo fija `AUDIO_PIPELINE_STAGES`, no el código):

    PCM16 8 kHz del transporte
      ↓ preprocess      paso-alto + límite de picos (quita viento, retumbe, clics)
      ↓ echo_control    alineación GCC-PHAT + filtro MDF + supresión residual
      ↓ dereverb        supresión de cola tardía de sala
      ↓ speaker_focus   marca voz de fondo por dominancia de nivel (TV, oficina)
      ↓ voice_gate      Silero VAD + puerta retroactiva: solo voz humana sale
      ↓ denoise         DPDFNet 8 kHz nativo (ONNX/CPU); se salta con el canal cerrado
      ↓ voice_focus     post-filtro de voz objetivo: armónicos + modulación silábica
      ↓ normalize       ganancia lenta a nivel objetivo + limitador suave
    PCM16 8 kHz hacia el STT

La puerta va **antes** del supresor a propósito: el detector ve la señal natural
(los artefactos de supresión lo hacen abrir con ruido de fondo) y el supresor no
gasta CPU con el canal cerrado.

Cada etapa es independiente y se puede quitar, reordenar o sustituir desde
configuración. Las etapas que necesitan otra tasa de muestreo la declaran y el
pipeline inserta remuestreadores con estado por su cuenta.

`CaptureEnhancer` es la única clase que el runtime de voz necesita conocer: recibe
el PCM del usuario, recibe el PCM que Lyra reproduce (referencia para el eco) y
devuelve PCM limpio. Si algo falta (modelo no descargado, dependencia ausente),
degrada a la mejor alternativa disponible y lo registra: una llamada nunca se cae
por el pipeline de audio.

Concurrencia: una instancia por llamada, con **todo** su estado aislado (buffers,
colas, estado recurrente de los modelos, referencia de eco, métricas). Lo único
compartido entre llamadas son las sesiones ONNX —inmutables— y el ejecutor de
hilos; ver `services/audio/runtime_pool.py`. El procesamiento sale del bucle de
eventos con `process_async`, con un solo bloque en vuelo por llamada.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from core.config import settings
from services.audio.frames import float_to_pcm16, pcm16_to_float
from services.audio.pipeline import AudioPipeline, AudioStage, FrameContext
from services.audio.reference import FarEndReference
from services.audio.runtime_pool import (
    get_audio_executor,
    max_concurrent_calls,
    prewarm_async,
    session_count,
    shutdown_audio_executor,
)
from services.audio.stages import (
    DenoiseStage,
    DereverbStage,
    EchoControlStage,
    EnergyDetector,
    NormalizeStage,
    OnnxStreamEnhancer,
    PreprocessStage,
    SileroOnnxDetector,
    SpeakerFocusStage,
    SpectralGateEnhancer,
    VoiceFocusStage,
    VoiceGateStage,
)

logger = logging.getLogger("lyra.audio")

__all__ = [
    "AudioPipeline",
    "CaptureEnhancer",
    "FarEndReference",
    "FrameContext",
    "build_capture_pipeline",
    "max_concurrent_calls",
    "prewarm_async",
    "resolve_model_path",
    "shutdown_audio_executor",
]


def resolve_model_path(configured: str) -> Optional[Path]:
    """Ruta de un modelo: absoluta tal cual, relativa contra la raíz del proyecto."""
    value = (configured or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    return path


# ── constructores de etapa (uno por nombre configurable) ──


def _build_preprocess(rate: int, _reference: FarEndReference) -> AudioStage:
    return PreprocessStage(
        rate=rate,
        highpass_hz=settings.AUDIO_HIGHPASS_HZ,
        peak_limit=settings.AUDIO_PEAK_LIMIT,
    )


def _build_echo_control(rate: int, reference: FarEndReference) -> AudioStage:
    return EchoControlStage(
        reference,
        rate=rate,
        frame_size=int(settings.AUDIO_STFT_FRAME),
        hop_size=int(settings.AUDIO_STFT_HOP),
        tail_ms=settings.AUDIO_ECHO_TAIL_MS,
        step_size=settings.AUDIO_ECHO_STEP_SIZE,
        search_ms=settings.AUDIO_ECHO_SEARCH_MS,
        realign_ms=settings.AUDIO_ECHO_REALIGN_MS,
        align_confidence=settings.AUDIO_ECHO_ALIGN_CONFIDENCE,
        align_min_dbfs=settings.AUDIO_ECHO_ALIGN_MIN_DBFS,
        residual_strength=settings.AUDIO_ECHO_RESIDUAL_STRENGTH,
        residual_floor=settings.AUDIO_ECHO_RESIDUAL_FLOOR,
        detect_margin_db=settings.AUDIO_ECHO_DETECT_MARGIN_DB,
        tail_hold_ms=settings.AUDIO_ECHO_TAIL_HOLD_MS,
    )


def _build_dereverb(rate: int, _reference: FarEndReference) -> AudioStage:
    return DereverbStage(
        rate=rate,
        frame_size=int(settings.AUDIO_STFT_FRAME),
        hop_size=int(settings.AUDIO_STFT_HOP),
        rt60_sec=settings.AUDIO_DEREVERB_RT60_SEC,
        direct_frames=int(settings.AUDIO_DEREVERB_DIRECT_FRAMES),
        strength=settings.AUDIO_DEREVERB_STRENGTH,
        floor=settings.AUDIO_DEREVERB_FLOOR,
    )


def _build_spectral_enhancer(rate: int):
    return SpectralGateEnhancer(
        rate=rate,
        frame_size=int(settings.AUDIO_STFT_FRAME),
        hop_size=int(settings.AUDIO_STFT_HOP),
    )


def _build_denoise(rate: int, _reference: FarEndReference) -> Optional[AudioStage]:
    backend = (settings.AUDIO_DENOISE_BACKEND or "").strip().lower()
    if backend in ("", "none", "off"):
        return None

    stage_rate = int(settings.AUDIO_DENOISE_RATE) or rate
    enhancer = None
    if backend == "onnx":
        model_path = resolve_model_path(settings.AUDIO_DENOISE_MODEL_PATH)
        try:
            enhancer = OnnxStreamEnhancer(
                model_path=str(model_path),
                rate=stage_rate,
                threads=int(settings.AUDIO_DENOISE_THREADS),
                output_delay_samples=(
                    int(settings.AUDIO_DENOISE_DELAY_SAMPLES)
                    if int(settings.AUDIO_DENOISE_DELAY_SAMPLES) >= 0
                    else None
                ),
                protect_floor_db=settings.AUDIO_DENOISE_PROTECT_FLOOR_DB,
                protect_min_gain=settings.AUDIO_DENOISE_PROTECT_MIN_GAIN,
            )
        except Exception as e:  # noqa: BLE001 — el modelo puede faltar en el host
            logger.warning(
                "[audio] supresor ONNX no disponible (%s); se usa el respaldo "
                "espectral, que es notablemente peor. Descarga el modelo con: "
                "python scripts/fetch_audio_models.py",
                e,
            )
    elif backend != "spectral":
        logger.warning(
            "[audio] AUDIO_DENOISE_BACKEND=%r desconocido; se usa el respaldo espectral",
            backend,
        )

    if enhancer is None:
        enhancer = _build_spectral_enhancer(stage_rate)

    limit = float(settings.AUDIO_DENOISE_ATTN_LIMIT_DB)
    return DenoiseStage(
        enhancer,
        rate=stage_rate,
        attn_limit_db=None if limit < 0.0 else limit,
        bypass_on_speech_absent=bool(settings.AUDIO_DENOISE_BYPASS_ON_SILENCE),
        bypass_hold_ms=settings.AUDIO_DENOISE_BYPASS_HOLD_MS,
    )


def _build_speaker_focus(rate: int, _reference: FarEndReference) -> AudioStage:
    return SpeakerFocusStage(
        rate=rate,
        frame_ms=settings.AUDIO_FOCUS_FRAME_MS,
        integration_ms=settings.AUDIO_FOCUS_INTEGRATION_MS,
        window_sec=settings.AUDIO_FOCUS_WINDOW_SEC,
        percentile=settings.AUDIO_FOCUS_PERCENTILE,
        margin_db=settings.AUDIO_FOCUS_MARGIN_DB,
        silence_db=settings.AUDIO_FOCUS_SILENCE_DBFS,
        min_frames=int(settings.AUDIO_FOCUS_MIN_FRAMES),
    )


def _build_voice_gate(rate: int, _reference: FarEndReference) -> AudioStage:
    backend = (settings.AUDIO_VAD_BACKEND or "").strip().lower()
    detector = None
    if backend == "silero":
        model_path = resolve_model_path(settings.AUDIO_VAD_MODEL_PATH)
        try:
            detector = SileroOnnxDetector(
                model_path=str(model_path),
                rate=rate,
                threads=int(settings.AUDIO_VAD_THREADS),
            )
        except Exception as e:  # noqa: BLE001 — falta el .onnx o onnxruntime
            logger.warning(
                "[audio] Silero VAD no disponible (%s); se usa el detector por "
                "energía, que NO distingue voz de ruido. Descarga el modelo con: "
                "python scripts/fetch_audio_models.py",
                e,
            )
    elif backend not in ("energy",):
        logger.warning(
            "[audio] AUDIO_VAD_BACKEND=%r desconocido; se usa el detector por energía",
            backend,
        )

    if detector is None:
        detector = EnergyDetector(rate=rate, frame_size=max(1, rate // 50))

    return VoiceGateStage(
        detector,
        rate=rate,
        threshold=settings.AUDIO_VAD_THRESHOLD,
        release_margin=settings.AUDIO_VAD_RELEASE_MARGIN,
        min_speech_ms=settings.AUDIO_VAD_MIN_SPEECH_MS,
        hangover_ms=settings.AUDIO_VAD_HANGOVER_MS,
        pre_roll_ms=settings.AUDIO_GATE_PRE_ROLL_MS,
        attenuation=settings.AUDIO_GATE_ATTENUATION,
        echo_penalty=settings.AUDIO_GATE_ECHO_PENALTY,
        echo_hold_ms=settings.AUDIO_GATE_ECHO_HOLD_MS,
        background_penalty=settings.AUDIO_GATE_BACKGROUND_PENALTY,
    )


def _build_voice_focus(rate: int, _reference: FarEndReference) -> AudioStage:
    return VoiceFocusStage(
        rate=rate,
        frame_size=int(settings.AUDIO_STFT_FRAME),
        hop_size=int(settings.AUDIO_STFT_HOP),
        f0_min=settings.AUDIO_FOCUS_F0_MIN_HZ,
        f0_max=settings.AUDIO_FOCUS_F0_MAX_HZ,
        voicing_threshold=settings.AUDIO_FOCUS_VOICING_THRESHOLD,
        harmonic_strength=settings.AUDIO_FOCUS_HARMONIC_STRENGTH,
        harmonic_limit_hz=settings.AUDIO_FOCUS_HARMONIC_LIMIT_HZ,
        harmonic_width_hz=settings.AUDIO_FOCUS_HARMONIC_WIDTH_HZ,
        modulation_strength=settings.AUDIO_FOCUS_MODULATION_STRENGTH,
        modulation_fast_ms=settings.AUDIO_FOCUS_MODULATION_FAST_MS,
        modulation_slow_ms=settings.AUDIO_FOCUS_MODULATION_SLOW_MS,
        modulation_target=settings.AUDIO_FOCUS_MODULATION_TARGET,
        floor=settings.AUDIO_FOCUS_POST_FLOOR,
        smoothing=settings.AUDIO_FOCUS_POST_SMOOTHING,
    )


def _build_normalize(rate: int, _reference: FarEndReference) -> AudioStage:
    return NormalizeStage(
        rate=rate,
        target_dbfs=settings.AUDIO_NORMALIZE_TARGET_DBFS,
        max_gain_db=settings.AUDIO_NORMALIZE_MAX_GAIN_DB,
        min_gain_db=settings.AUDIO_NORMALIZE_MIN_GAIN_DB,
        attack=settings.AUDIO_NORMALIZE_ATTACK,
        release=settings.AUDIO_NORMALIZE_RELEASE,
        limit=settings.AUDIO_NORMALIZE_LIMIT,
        speech_only=bool(settings.AUDIO_NORMALIZE_SPEECH_ONLY),
        silence_dbfs=settings.AUDIO_FOCUS_SILENCE_DBFS,
    )


STAGE_BUILDERS = {
    "preprocess": _build_preprocess,
    "echo_control": _build_echo_control,
    "dereverb": _build_dereverb,
    "denoise": _build_denoise,
    "speaker_focus": _build_speaker_focus,
    "voice_gate": _build_voice_gate,
    "voice_focus": _build_voice_focus,
    "normalize": _build_normalize,
}


def build_capture_pipeline(
    *,
    rate: int,
    reference: FarEndReference,
    stages: Optional[str] = None,
) -> AudioPipeline:
    """Construye el pipeline de captura según configuración."""
    names = [
        name.strip()
        for name in (stages or settings.AUDIO_PIPELINE_STAGES or "").split(",")
        if name.strip()
    ]
    built: list[AudioStage] = []
    for name in names:
        builder = STAGE_BUILDERS.get(name)
        if builder is None:
            logger.warning("[audio] etapa desconocida en la configuración: %r", name)
            continue
        try:
            stage = builder(rate, reference)
        except Exception:
            logger.exception("[audio] no se pudo construir la etapa %s", name)
            continue
        if stage is not None:
            built.append(stage)
    return AudioPipeline(
        built, input_rate=rate, strict=bool(settings.AUDIO_PIPELINE_STRICT)
    )


class CaptureEnhancer:
    """Fachada por llamada: PCM sucio entra, PCM apto para el STT sale.

    Además recibe el audio que Lyra reproduce (`publish_playback`), que es la
    referencia con la que se cancela el eco cuando el usuario está en altavoz.
    """

    def __init__(
        self,
        *,
        rate: int = 8000,
        enabled: Optional[bool] = None,
        stages: Optional[str] = None,
    ):
        self.rate = int(rate)
        self.enabled = (
            bool(settings.AUDIO_PIPELINE_ENABLED) if enabled is None else bool(enabled)
        )
        self.reference = FarEndReference(
            sample_rate=self.rate,
            window_sec=float(settings.AUDIO_ECHO_REFERENCE_WINDOW_SEC),
        )
        self._block_timeout = float(settings.AUDIO_BLOCK_TIMEOUT_MS) / 1000.0
        self.blocks_degraded = 0
        self._pending: Optional[asyncio.Future] = None
        self._stages = stages
        self._pipeline: Optional[AudioPipeline] = None
        self._build_lock = threading.Lock()
        if not self.enabled:
            logger.info("[audio] pipeline de captura desactivado (paso directo)")

    # ── construcción diferida ──

    @property
    def pipeline(self) -> Optional[AudioPipeline]:
        """Pipeline de esta llamada, construido en el primer uso.

        La construcción no es gratis la primera vez del proceso: carga las
        sesiones ONNX y mide el retardo del supresor (cerca de un segundo). Hacerlo
        en el constructor significaría hacerlo en el bucle de eventos justo al
        entrar la llamada, bloqueando a todas las demás. Al diferirlo, ocurre
        dentro del hilo de trabajo que procesa el primer bloque — y una sola vez
        por proceso, porque las sesiones quedan compartidas.
        `runtime_pool.prewarm()` permite adelantarlo al arranque.
        """
        if self._pipeline is not None or not self.enabled:
            return self._pipeline
        with self._build_lock:
            if self._pipeline is None:
                self._pipeline = build_capture_pipeline(
                    rate=self.rate, reference=self.reference, stages=self._stages
                )
                logger.info(
                    "[audio] pipeline de captura activo etapas=%s latencia=%.0f ms",
                    ",".join(self._pipeline.active_stage_names),
                    self._pipeline.latency_ms,
                )
        return self._pipeline

    # ── captura ──

    def process(
        self, pcm: bytes, *, timestamp: float, playback_active: bool = False
    ) -> tuple[bytes, Optional[FrameContext]]:
        """Devuelve (PCM procesado, contexto). Sin pipeline, el PCM va intacto.

        Bloquea al llamante: úsala solo fuera del bucle de eventos (pruebas,
        análisis offline). En una llamada real se usa `process_async`.
        """
        pipeline = self.pipeline
        if pipeline is None:
            return pcm, None
        return pipeline.process_pcm(
            pcm, timestamp=timestamp, playback_active=playback_active
        )

    async def process_async(
        self, pcm: bytes, *, timestamp: float, playback_active: bool = False
    ) -> tuple[bytes, Optional[FrameContext]]:
        """Igual que `process`, pero ejecutándose fuera del bucle de eventos.

        El cómputo se manda a un ejecutor de hilos compartido por el proceso.
        ONNX Runtime, numpy y scipy liberan el GIL mientras calculan, así que
        varias llamadas simultáneas aprovechan varios núcleos de verdad en vez de
        serializarse en el bucle de asyncio. El orden dentro de una misma llamada
        está garantizado: quien llama espera cada bloque antes de leer el
        siguiente del transporte.
        """
        if not self.enabled:
            return pcm, None

        # Un solo bloque en vuelo por llamada, siempre. Es la garantía de que el
        # estado recurrente del pipeline recibe el audio en orden: si un bloque
        # que expiró siguiera corriendo mientras entra el siguiente, dos hilos
        # mutarían los mismos buffers y el resultado sería basura silenciosa.
        # Se comprueba el futuro pendiente directamente en vez de confiar en un
        # callback del bucle: un llamante que no cediera el control nunca dejaría
        # correr ese callback y la llamada quedaría degradada para siempre.
        if self._pending is not None:
            if not self._pending.done():
                self.blocks_degraded += 1
                return pcm, None
            self._pending = None

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            get_audio_executor(),
            functools.partial(
                self._process_locked,
                pcm,
                timestamp=timestamp,
                playback_active=playback_active,
            ),
        )
        if self._block_timeout <= 0.0:
            return await future
        try:
            # `shield` deja claro el contrato: el trabajo ya lanzado no se
            # cancela (un futuro de ejecutor tampoco podría), solo se deja de
            # esperar por él.
            result = await asyncio.wait_for(
                asyncio.shield(future), timeout=self._block_timeout
            )
        except asyncio.TimeoutError:
            # El proceso está saturado. Entregar el bloque sin procesar es peor
            # señal para el reconocedor, pero acumular retardo es peor para la
            # conversación entera: el atraso nunca se recupera. Se cuenta para
            # que el operador lo vea y baje el tope de llamadas simultáneas. El
            # bloque sigue terminando en su hilo; hasta entonces no se admite
            # otro para esta llamada.
            self.blocks_degraded += 1
            self._pending = future
            return pcm, None
        return result

    # ── referencia de playback (eco) ──

    def playback_started(self, at_time: float) -> None:
        self.reference.start(at_time)

    def publish_playback(self, pcm: bytes) -> None:
        if not self.enabled or not pcm:
            return
        self.reference.publish(pcm16_to_float(pcm))

    def playback_finished(self, *, at_time: Optional[float] = None) -> None:
        """Fin del playback. Con `at_time` (barge-in) descarta lo que no llegó a sonar."""
        if at_time is None:
            self.reference.stop()
        else:
            self.reference.truncate(at_time)

    # ── ciclo de vida ──

    def _process_locked(
        self, pcm: bytes, *, timestamp: float, playback_active: bool
    ) -> tuple[bytes, Optional[FrameContext]]:
        """Cuerpo que corre en el hilo de trabajo (construye el pipeline si hace falta)."""
        pipeline = self.pipeline
        if pipeline is None:
            return pcm, None
        return pipeline.process_pcm(
            pcm, timestamp=timestamp, playback_active=playback_active
        )

    def reset(self) -> None:
        """Nuevo turno de escucha: limpia estados que no deben cruzar turnos."""
        if self._pipeline is not None:
            self._pipeline.reset()

    def clear_reference(self) -> None:
        self.reference.clear()

    @property
    def latency_ms(self) -> float:
        pipeline = self.pipeline
        return pipeline.latency_ms if pipeline is not None else 0.0

    def stats(self) -> dict:
        pipeline = self._pipeline
        data: dict = {
            "enabled": self.enabled,
            "built": pipeline is not None,
            # Sesiones ONNX vivas en el proceso: debe ser una por modelo, nunca una
            # por llamada (si crece con las llamadas, hay una fuga de recursos).
            "onnx_sessions": session_count(),
            # Bloques entregados sin procesar por saturación: cualquier valor > 0
            # dice que el proceso atiende más llamadas de las que su CPU sostiene.
            "blocks_degraded": self.blocks_degraded,
        }
        if pipeline is None:
            return data
        data.update(pipeline.stats())
        for stage in pipeline._stages:  # noqa: SLF001 — métricas propias
            extra = getattr(stage, "stats", None)
            if callable(extra):
                try:
                    data["stages"].setdefault(stage.name, {}).update(extra())
                except Exception:  # pragma: no cover - las métricas nunca rompen
                    logger.debug("[audio] métricas de %s fallaron", stage.name)
        return data


def enhance_pcm_once(
    pcm: bytes, *, rate: int = 8000, stages: Optional[str] = None
) -> bytes:
    """Procesa un audio completo de una vez (uso puntual: pruebas, análisis).

    No es el camino de la llamada en vivo — no hay referencia de eco ni
    continuidad de estado entre invocaciones — pero permite medir el efecto del
    pipeline sobre una grabación real con la misma configuración de producción.
    """
    enhancer = CaptureEnhancer(rate=rate, enabled=True, stages=stages)
    if enhancer.pipeline is None:
        return pcm
    samples = pcm16_to_float(pcm)
    ctx = FrameContext(timestamp=0.0, input_rate=rate)
    chunk = max(1, rate // 50)
    out: list[np.ndarray] = []
    for start in range(0, samples.size, chunk):
        ctx.echo_detected = False
        ctx.background_voice = False
        out.append(enhancer.pipeline.process_block(samples[start : start + chunk], ctx))
    return float_to_pcm16(
        np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
    )
