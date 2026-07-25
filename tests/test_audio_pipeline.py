"""Pipeline de mejora de audio: trama, STFT, eco, puerta de voz y ensamblaje.

Los tests que necesitan un modelo descargado se saltan si el archivo no está
(`scripts/fetch_audio_models.py`); el resto cubre el pipeline completo con
detectores y supresores de prueba, para que la lógica quede verificada sin
depender de binarios externos.
"""

import asyncio
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from services.audio import CaptureEnhancer, build_capture_pipeline, resolve_model_path
from services.audio.dsp import SpectralStream, gcc_phat, hann_sqrt_window, vorbis_window
from services.audio.frames import (
    FrameSlicer,
    StreamResampler,
    dbfs,
    float_to_pcm16,
    pcm16_to_float,
    resample,
    rms,
)
from services.audio.pipeline import AudioPipeline, BaseStage, FrameContext
from services.audio.reference import FarEndReference
from services.audio.stages import (
    DenoiseStage,
    DereverbStage,
    EchoControlStage,
    NormalizeStage,
    PreprocessStage,
    SpeakerFocusStage,
    VoiceFocusStage,
    VoiceGateStage,
)

RATE = 8000
BLOCK = 160  # 20 ms, el tamaño que entrega mod_audio_stream


def _tone(samples: int, freq: float = 440.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(samples) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _noise(samples: int, amp: float = 0.05, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (amp * rng.normal(0, 1, samples)).astype(np.float32)


def _feed(pipeline, signal, *, playback=False, reference=None):
    """Empuja una señal por bloques de 20 ms respetando el contrato de timestamp."""
    out = []
    for start in range(0, signal.size, BLOCK):
        block = signal[start : start + BLOCK]
        ctx = FrameContext(
            timestamp=(start + block.size) / RATE,
            input_rate=RATE,
            playback_active=playback,
        )
        out.append(pipeline.process_block(block, ctx))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


# ── trama y tasa de muestreo ──


def test_pcm_roundtrip_is_lossless_within_quantization():
    signal = _tone(1000)
    assert np.abs(pcm16_to_float(float_to_pcm16(signal)) - signal).max() < 1e-4


def test_pcm16_to_float_tolerates_odd_byte_count():
    assert pcm16_to_float(b"\x01").size == 0
    assert pcm16_to_float(b"\x01\x02\x03").size == 1


def test_frame_slicer_never_loses_samples():
    slicer = FrameSlicer(256)
    emitted = 0
    for _ in range(10):
        emitted += sum(frame.size for frame in slicer.push(_tone(100)))
    assert emitted + slicer.pending == 1000
    assert emitted % 256 == 0


def test_stream_resampler_matches_continuous_resampling():
    signal = _tone(4000, freq=700.0)
    up = StreamResampler(RATE, 16000)
    streamed = np.concatenate(
        [up.process(signal[i : i + BLOCK]) for i in range(0, signal.size, BLOCK)]
    )
    assert streamed.size == signal.size * 2
    # Ida y vuelta: la señal se recupera salvo el retardo de grupo del filtro.
    down = StreamResampler(16000, RATE)
    restored = np.concatenate(
        [down.process(streamed[i : i + 320]) for i in range(0, streamed.size, 320)]
    )
    delay = 32
    usable = min(restored.size - delay, signal.size - delay)
    error = np.abs(restored[delay : delay + usable] - signal[:usable]).max()
    assert error < 0.02


def test_resample_is_identity_for_equal_rates():
    signal = _tone(100)
    assert resample(signal, RATE, RATE) is signal


def test_level_helpers():
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0
    assert dbfs(np.zeros(10, dtype=np.float32)) == -120.0
    # Un seno de amplitud 0.5 tiene RMS 0.5/√2 → -9.03 dBFS.
    assert dbfs(_tone(1000, amp=0.5)) == pytest.approx(-9.03, abs=0.1)


# ── núcleo DSP ──


def test_wola_neutral_transform_reproduces_signal():
    """Una transformación neutra debe devolver la señal original: si esto falla,
    cualquier ganancia espectral que aplique una etapa viene contaminada."""
    stream = SpectralStream(256, 128, channels=1)
    signal = _tone(3000, freq=311.0) + _noise(3000, amp=0.02)
    produced = np.concatenate(
        [stream.process([signal[i : i + 100]], lambda s: s[0]) for i in range(0, 3000, 100)]
    )
    delay = stream.delay_samples
    usable = min(produced.size - delay, signal.size)
    assert np.abs(produced[delay : delay + usable] - signal[:usable]).max() < 1e-5


def test_wola_pads_short_channels_with_silence():
    stream = SpectralStream(256, 128, channels=2)
    main = _tone(512)
    out = stream.process([main, main[:100]], lambda s: s[0] - s[1])
    assert out.size > 0


def test_spectral_stream_rejects_bad_geometry():
    with pytest.raises(ValueError):
        SpectralStream(256, 100)
    with pytest.raises(ValueError):
        SpectralStream(256, 128, channels=2).process([_tone(256)], lambda s: s[0])


@pytest.mark.parametrize("true_lag", [0, 37, 411, -25])
def test_gcc_phat_recovers_known_delay(true_lag):
    rng = np.random.default_rng(5)
    reference = rng.normal(0, 1, 4000).astype(np.float32)
    capture = np.zeros(4000, dtype=np.float32)
    if true_lag >= 0:
        capture[true_lag:] = 0.3 * reference[: 4000 - true_lag]
    else:
        capture[: 4000 + true_lag] = 0.3 * reference[-true_lag:]
    lag, confidence = gcc_phat(capture, reference, max_lag=800)
    assert lag == true_lag
    assert confidence > 0.5


def test_gcc_phat_handles_empty_input():
    assert gcc_phat(np.zeros(0, dtype=np.float32), _tone(100)) == (0, 0.0)


def test_windows_satisfy_overlap_conditions():
    # Vorbis (Princen-Bradley) y raíz de Hann: w²[n] + w²[n+N/2] = 1.
    for window in (vorbis_window(256), hann_sqrt_window(256)):
        squared = window.astype(np.float64) ** 2
        total = squared[:128] + squared[128:]
        assert np.abs(total - 1.0).max() < 1e-6


# ── referencia far-end ──


def test_reference_aligned_returns_expected_past_window():
    reference = FarEndReference(sample_rate=RATE, window_sec=4.0)
    reference.start(0.0)
    ramp = (np.arange(800, dtype=np.float32) / 800.0)
    reference.publish(ramp)
    # En t=0.1 s (800 muestras) pidiendo 100 muestras con retardo 200:
    # corresponden a las muestras [500, 600) de lo reproducido.
    aligned = reference.aligned(0.1, 100, 200)
    assert np.allclose(aligned, ramp[500:600])


def test_reference_pads_when_playback_just_started():
    reference = FarEndReference(sample_rate=RATE, window_sec=4.0)
    reference.start(0.0)
    reference.publish(np.ones(80, dtype=np.float32))
    aligned = reference.aligned(0.01, 160, 0)
    assert aligned.size == 160
    assert aligned[:80].tolist() == [0.0] * 80  # antes del playback no había nada


def test_reference_truncate_discards_unplayed_audio():
    reference = FarEndReference(sample_rate=RATE, window_sec=4.0)
    reference.start(0.0)
    reference.publish(np.ones(8000, dtype=np.float32))  # un segundo de audio
    reference.truncate(0.25)  # interrumpido a los 250 ms
    assert reference.active is False
    # Lo que nunca sonó ya no está disponible como referencia.
    assert not reference.aligned(0.5, 800, 0).any()


def test_reference_right_edge_gap_detects_stale_timeline():
    reference = FarEndReference(sample_rate=RATE, window_sec=4.0)
    reference.start(0.0)
    reference.publish(np.ones(800, dtype=np.float32))
    assert reference.right_edge_gap(0.1) == 0
    assert reference.right_edge_gap(0.2) == 800


def test_reference_window_is_bounded_by_capacity():
    reference = FarEndReference(sample_rate=RATE, window_sec=0.1)
    reference.start(0.0)
    reference.publish(np.ones(4000, dtype=np.float32))
    assert reference.has_content()
    samples, _ = reference.window(0.5, 200, 100)
    assert samples.size <= 800  # 0.1 s de capacidad


# ── pipeline: puentes de tasa y aislamiento de fallos ──


class _RateStage(BaseStage):
    name = "rate_probe"

    def __init__(self, rate):
        self.rate = rate
        self.seen = 0

    def process(self, block, ctx):
        self.seen += block.size
        return block


def test_pipeline_bridges_sample_rates_and_returns_to_input_rate():
    stage = _RateStage(16000)
    pipeline = AudioPipeline([stage], input_rate=RATE)
    signal = _tone(1600)
    out = _feed(pipeline, signal)
    assert stage.seen == pytest.approx(signal.size * 2, rel=0.05)
    assert out.size == pytest.approx(signal.size, rel=0.05)


class _BrokenStage(BaseStage):
    name = "broken"

    def process(self, block, ctx):
        raise RuntimeError("fallo deliberado")


def test_failing_stage_is_disabled_and_audio_keeps_flowing():
    keeper = _RateStage(RATE)
    pipeline = AudioPipeline([_BrokenStage(), keeper], input_rate=RATE)
    out = _feed(pipeline, _tone(480))
    assert "broken" not in pipeline.active_stage_names
    assert out.size == 480  # la etapa siguiente sigue recibiendo audio
    assert pipeline.stats()["stages"]["broken"]["errors"] == 1


def test_strict_pipeline_propagates_stage_errors():
    pipeline = AudioPipeline([_BrokenStage()], input_rate=RATE, strict=True)
    with pytest.raises(RuntimeError):
        _feed(pipeline, _tone(160))


def test_pipeline_reset_is_safe_for_all_stages():
    pipeline = AudioPipeline([_RateStage(16000)], input_rate=RATE)
    _feed(pipeline, _tone(320))
    pipeline.reset()  # no debe lanzar


# ── preacondicionamiento ──


def test_preprocess_removes_dc_and_rumble():
    stage = PreprocessStage(rate=RATE, highpass_hz=90.0, peak_limit=0.97)
    signal = _tone(4000, freq=25.0, amp=0.4) + 0.5  # retumbe + offset DC
    out = _feed(AudioPipeline([stage], input_rate=RATE), signal)
    assert abs(float(np.mean(out[400:]))) < 0.02
    assert rms(out[400:]) < rms(signal) / 4


def test_preprocess_limits_peaks_softly_without_hard_clipping():
    stage = PreprocessStage(rate=RATE, highpass_hz=90.0, peak_limit=0.5)
    out = _feed(AudioPipeline([stage], input_rate=RATE), _tone(800, amp=0.95))
    assert float(np.max(np.abs(out))) <= 1.0
    assert float(np.max(np.abs(out))) > 0.5  # comprime, no recorta a plomo


# ── puerta de voz ──


class _ScriptedDetector:
    """Detector de prueba: devuelve la probabilidad indicada por trama."""

    frame_size = 256

    def __init__(self, probabilities):
        self.probabilities = list(probabilities)
        self.index = 0
        self.resets = 0

    def probability(self, frame):
        value = (
            self.probabilities[self.index]
            if self.index < len(self.probabilities)
            else self.probabilities[-1]
        )
        self.index += 1
        return value

    def reset(self):
        self.resets += 1


def _gate(detector, **overrides):
    params = dict(
        rate=RATE,
        threshold=0.6,
        release_margin=0.15,
        min_speech_ms=64.0,   # 2 tramas de 32 ms
        hangover_ms=64.0,
        pre_roll_ms=64.0,
        attenuation=0.0,
        echo_penalty=0.3,
        echo_hold_ms=64.0,
        background_penalty=1.0,
    )
    params.update(overrides)
    return VoiceGateStage(detector, **params)


def _run_gate(gate, frames, **ctx_kwargs):
    """Procesa `frames` tramas de 256 muestras de tono y devuelve la salida."""
    out = []
    for index in range(frames):
        ctx = FrameContext(timestamp=index * 0.032, input_rate=RATE, **ctx_kwargs)
        out.append(gate.process(_tone(256, freq=300.0 + index), ctx))
    return np.concatenate(out)


def test_gate_mutes_non_speech_to_digital_silence():
    gate = _gate(_ScriptedDetector([0.0]))
    out = _run_gate(gate, 10)
    assert out.size == (10 - 2) * 256  # dos tramas retenidas por el pre-roll
    assert not out.any()


def test_gate_opens_retroactively_so_word_onset_survives():
    """El detector confirma voz en la 3ª trama; las dos anteriores (el ataque de
    la palabra) deben salir sin atenuar gracias al pre-roll."""
    gate = _gate(_ScriptedDetector([0.9] * 12))
    out = _run_gate(gate, 6)
    assert out.size == 4 * 256
    assert rms(out[:256]) > 0.1  # la primera trama emitida NO viene silenciada


def test_gate_keeps_hangover_and_then_closes():
    gate = _gate(_ScriptedDetector([0.9, 0.9, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))
    out = _run_gate(gate, 10)
    frames = out.reshape(-1, 256)
    voiced = [bool(rms(frame) > 0.01) for frame in frames]
    assert voiced[0] and voiced[1]      # apertura retroactiva
    assert voiced[3]                    # colgado tras la última voz
    assert not voiced[-1]               # y finalmente cierra


def test_gate_hysteresis_keeps_open_below_open_threshold():
    # 0.5 no abre (umbral 0.6) pero sí mantiene abierto (0.6 - 0.15 = 0.45).
    gate = _gate(_ScriptedDetector([0.9, 0.9, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]))
    out = _run_gate(gate, 8)
    assert all(rms(frame) > 0.01 for frame in out.reshape(-1, 256))


def test_gate_demands_more_evidence_while_echo_is_present():
    """Con eco detectado, una probabilidad de 0.8 (que normalmente abriría) ya no
    basta: es la defensa contra que Lyra se escuche a sí misma."""
    gate = _gate(_ScriptedDetector([0.8] * 12))
    out = _run_gate(gate, 8, echo_detected=True)
    assert not out.any()
    # Sin eco, la misma señal sí pasa.
    assert _run_gate(_gate(_ScriptedDetector([0.8] * 12)), 8).any()


def test_gate_echo_hold_survives_gaps_in_detection():
    gate = _gate(_ScriptedDetector([0.95] * 12))
    ctx_echo = FrameContext(timestamp=0.0, input_rate=RATE, echo_detected=True)
    gate.process(_tone(256), ctx_echo)
    # Trama siguiente sin eco declarado: el colgado debe seguir exigiendo más.
    for index in range(1, 6):
        gate.process(
            _tone(256), FrameContext(timestamp=index * 0.032, input_rate=RATE)
        )
    assert gate.stats()["frames_echo_penalized"] >= 2


def test_gate_vetoes_background_voice():
    gate = _gate(_ScriptedDetector([0.99] * 12))
    assert not _run_gate(gate, 8, background_voice=True).any()


def test_gate_attenuation_can_leave_a_residual_floor():
    gate = _gate(_ScriptedDetector([0.0]), attenuation=0.25)
    out = _run_gate(gate, 6)
    assert 0.0 < rms(out) < rms(_tone(256)) * 0.5


def test_gate_reset_clears_detector_and_queue():
    gate = _gate(_ScriptedDetector([0.9] * 12))
    _run_gate(gate, 4)
    gate.reset()
    assert gate.detector.resets == 1
    assert gate.latency_ms == pytest.approx(64.0, abs=1.0)


# ── foco en el hablante principal ──


def test_speaker_focus_flags_only_distant_voices():
    stage = SpeakerFocusStage(
        rate=RATE,
        frame_ms=20.0,
        integration_ms=100.0,
        window_sec=2.0,
        percentile=85.0,
        margin_db=18.0,
        silence_db=-55.0,
        min_frames=10,
    )
    pipeline = AudioPipeline([stage], input_rate=RATE)
    loud = _tone(8000, amp=0.3)

    flags = []
    for start in range(0, loud.size, BLOCK):
        ctx = FrameContext(timestamp=(start + BLOCK) / RATE, input_rate=RATE)
        pipeline.process_block(loud[start : start + BLOCK], ctx)
        flags.append(ctx.background_voice)
    assert not any(flags)  # el hablante dominante nunca se marca como fondo

    quiet = _tone(2000, amp=0.3 * (10 ** (-24 / 20)))  # 24 dB por debajo
    marked = False
    for start in range(0, quiet.size, BLOCK):
        ctx = FrameContext(timestamp=(start + BLOCK) / RATE, input_rate=RATE)
        pipeline.process_block(quiet[start : start + BLOCK], ctx)
        marked = marked or ctx.background_voice
    assert marked


def test_speaker_focus_stays_quiet_until_window_is_populated():
    stage = SpeakerFocusStage(
        rate=RATE,
        frame_ms=20.0,
        integration_ms=100.0,
        window_sec=2.0,
        percentile=85.0,
        margin_db=6.0,
        silence_db=-55.0,
        min_frames=50,
    )
    ctx = FrameContext(timestamp=0.02, input_rate=RATE)
    stage.process(_tone(BLOCK, amp=0.001), ctx)
    assert ctx.background_voice is False


# ── supresión de ruido: límite de atenuación ──


class _DelayedSilencer:
    """Supresor de prueba: borra todo y devuelve la salida retardada."""

    def __init__(self, delay):
        self.output_delay_samples = delay
        self._buffer = np.zeros(0, dtype=np.float32)

    def enhance(self, block):
        self._buffer = np.concatenate((self._buffer, block))
        if self._buffer.size < self.output_delay_samples:
            return np.zeros(0, dtype=np.float32)
        out = np.zeros(self._buffer.size - self.output_delay_samples, dtype=np.float32)
        self._buffer = self._buffer[out.size :]
        return out

    def reset(self):
        self._buffer = np.zeros(0, dtype=np.float32)


def test_denoise_attn_limit_mixes_the_aligned_original():
    """Con el supresor borrando todo, la salida debe ser exactamente la señal
    original escalada por alpha y alineada con el retardo del modelo."""
    delay = 320
    stage = DenoiseStage(_DelayedSilencer(delay), rate=RATE, attn_limit_db=12.0)
    signal = _tone(4000)
    out = _feed(AudioPipeline([stage], input_rate=RATE), signal)
    alpha = 10 ** (-12.0 / 20.0)
    expected = alpha * signal[: max(0, out.size - delay)]
    assert out.size > delay
    assert np.abs(out[delay:] - expected).max() < 1e-6
    assert stage.latency_ms == pytest.approx(delay / RATE * 1000.0, abs=0.1)


def test_denoise_without_limit_returns_only_the_model_output():
    stage = DenoiseStage(_DelayedSilencer(0), rate=RATE, attn_limit_db=None)
    out = _feed(AudioPipeline([stage], input_rate=RATE), _tone(1600))
    assert not out.any()


def test_denoise_zero_limit_leaves_the_signal_untouched():
    stage = DenoiseStage(_DelayedSilencer(0), rate=RATE, attn_limit_db=0.0)
    signal = _tone(1600)
    out = _feed(AudioPipeline([stage], input_rate=RATE), signal)
    assert np.abs(out - signal[: out.size]).max() < 1e-6


# ── dereverberación ──


def test_dereverb_attenuates_a_reverberant_tail():
    from scipy.signal import lfilter

    stage = DereverbStage(
        rate=RATE,
        frame_size=256,
        hop_size=128,
        rt60_sec=0.35,
        direct_frames=2,
        strength=1.0,
        floor=0.1,
    )
    impulse = np.zeros(1600, dtype=np.float32)
    impulse[0] = 1.0
    room = np.exp(-np.arange(1200) / 400.0).astype(np.float32)
    reverberant = lfilter(room, [1.0], impulse).astype(np.float32)
    out = _feed(AudioPipeline([stage], input_rate=RATE), reverberant)
    tail_in = rms(reverberant[800:])
    tail_out = rms(out[800:])
    assert tail_out < tail_in


# ── control de eco ──


def _echo_stage(reference, **overrides):
    params = dict(
        rate=RATE,
        frame_size=256,
        hop_size=128,
        tail_ms=128.0,
        step_size=0.3,
        search_ms=200.0,
        realign_ms=200.0,
        align_confidence=0.35,
        residual_strength=1.6,
        residual_floor=0.05,
        detect_margin_db=3.0,
        tail_hold_ms=500.0,
        align_min_dbfs=-45.0,
    )
    params.update(overrides)
    return EchoControlStage(reference, **params)


def _echo_scenario(delay=400, near_end=None, duration=24000):
    rng = np.random.default_rng(3)
    played = (0.3 * rng.normal(0, 1, duration)).astype(np.float32)
    capture = np.zeros(duration, dtype=np.float32)
    capture[delay:] = 0.5 * played[: duration - delay]
    if near_end is not None:
        capture[: near_end.size] += near_end
    return played, capture


def test_echo_stage_is_transparent_without_playback():
    reference = FarEndReference(sample_rate=RATE, window_sec=4.0)
    stage = _echo_stage(reference)
    signal = _tone(1600)
    out = _feed(AudioPipeline([stage], input_rate=RATE), signal)
    assert np.abs(out - signal).max() == 0.0  # sin referencia, ni se toca


def test_echo_stage_finds_the_delay_and_cancels():
    reference = FarEndReference(sample_rate=RATE, window_sec=8.0)
    stage = _echo_stage(reference)
    played, capture = _echo_scenario(delay=400)
    reference.start(0.0)
    reference.publish(played)

    pipeline = AudioPipeline([stage], input_rate=RATE)
    out = _feed(pipeline, capture, playback=True)

    assert stage.stats()["lag_samples"] == 400
    assert stage.stats()["lag_confidence"] > 0.5
    # Se mide la segunda mitad: la primera incluye la convergencia del filtro.
    half = out.size // 2
    assert rms(out[half:]) < rms(capture[half:]) / 3
    assert stage.stats()["erle_db"] > 6.0


def test_echo_stage_reports_echo_dominated_frames():
    reference = FarEndReference(sample_rate=RATE, window_sec=8.0)
    stage = _echo_stage(reference)
    played, capture = _echo_scenario(delay=400)
    reference.start(0.0)
    reference.publish(played)

    detected = 0
    for start in range(0, capture.size, BLOCK):
        block = capture[start : start + BLOCK]
        ctx = FrameContext(
            timestamp=(start + block.size) / RATE,
            input_rate=RATE,
            playback_active=True,
        )
        stage.process(block, ctx)
        detected += int(ctx.echo_detected)
    assert detected > 0
    assert stage.stats()["frames_echo_detected"] > 0


def test_echo_stage_preserves_near_end_speech_during_double_talk():
    reference = FarEndReference(sample_rate=RATE, window_sec=8.0)
    stage = _echo_stage(reference)
    near = _tone(24000, freq=520.0, amp=0.35)
    played, capture = _echo_scenario(delay=400, near_end=near)
    reference.start(0.0)
    reference.publish(played)
    out = _feed(AudioPipeline([stage], input_rate=RATE), capture, playback=True)
    # El tono del campo cercano debe seguir presente en la salida.
    half = out.size // 2
    assert rms(out[half:]) > rms(near[half:]) / 3


def test_echo_stage_ignores_silent_alignment_attempts():
    """GCC-PHAT sobre silencio devuelve picos espurios de alta confianza: la
    compuerta de energía debe impedir que se acepten."""
    reference = FarEndReference(sample_rate=RATE, window_sec=8.0)
    stage = _echo_stage(reference)
    rng = np.random.default_rng(11)
    played = (0.3 * rng.normal(0, 1, 24000)).astype(np.float32)
    reference.start(0.0)
    reference.publish(played)
    silence = np.zeros(24000, dtype=np.float32)
    _feed(AudioPipeline([stage], input_rate=RATE), silence, playback=True)
    assert stage.stats()["lag_samples"] == 0


# ── normalización ──


def test_normalize_only_adapts_on_confirmed_speech():
    quiet = _tone(8000, amp=0.02)
    stage = NormalizeStage(
        rate=RATE,
        target_dbfs=-20.0,
        max_gain_db=12.0,
        min_gain_db=-6.0,
        attack=0.7,
        release=0.9,
        limit=0.95,
    )
    out = []
    for start in range(0, quiet.size, BLOCK):
        ctx = FrameContext(timestamp=start / RATE, input_rate=RATE)
        out.append(stage.process(quiet[start : start + BLOCK], ctx))
    assert rms(np.concatenate(out)) == pytest.approx(rms(quiet), rel=0.01)

    stage.reset()
    out = []
    for start in range(0, quiet.size, BLOCK):
        ctx = FrameContext(timestamp=start / RATE, input_rate=RATE, speech_active=True)
        out.append(stage.process(quiet[start : start + BLOCK], ctx))
    assert rms(np.concatenate(out)) > rms(quiet) * 2


def test_normalize_speech_only_disabled_adapts_on_any_signal():
    stage = NormalizeStage(
        rate=RATE,
        target_dbfs=-20.0,
        max_gain_db=12.0,
        min_gain_db=-6.0,
        attack=0.7,
        release=0.9,
        limit=0.95,
        speech_only=False,
    )
    quiet = _tone(8000, amp=0.02)
    out = []
    for start in range(0, quiet.size, BLOCK):
        ctx = FrameContext(timestamp=start / RATE, input_rate=RATE)
        out.append(stage.process(quiet[start : start + BLOCK], ctx))
    assert rms(np.concatenate(out)) > rms(quiet) * 2


def test_normalize_limiter_bounds_the_output():
    stage = NormalizeStage(
        rate=RATE,
        target_dbfs=-1.0,
        max_gain_db=24.0,
        min_gain_db=0.0,
        attack=0.0,
        release=0.0,
        limit=0.5,
    )
    loud = _tone(4000, amp=0.9)
    out = []
    for start in range(0, loud.size, BLOCK):
        ctx = FrameContext(timestamp=start / RATE, input_rate=RATE, speech_active=True)
        out.append(stage.process(loud[start : start + BLOCK], ctx))
    assert float(np.max(np.abs(np.concatenate(out)))) <= 1.0
    assert "gain_db" in stage.stats()


# ── ensamblaje y fachada ──


def test_disabled_enhancer_is_a_passthrough():
    enhancer = CaptureEnhancer(rate=RATE, enabled=False)
    pcm = float_to_pcm16(_tone(320))
    out, ctx = enhancer.process(pcm, timestamp=0.04)
    assert out == pcm
    assert ctx is None
    assert enhancer.latency_ms == 0.0
    assert enhancer.stats()["enabled"] is False
    assert enhancer.stats()["built"] is False
    enhancer.reset()  # sin pipeline no debe fallar


def test_builder_skips_unknown_stage_names():
    reference = FarEndReference(sample_rate=RATE, window_sec=1.0)
    pipeline = build_capture_pipeline(
        rate=RATE, reference=reference, stages="preprocess,no_existe,normalize"
    )
    assert pipeline.stage_names == ["preprocess", "normalize"]


def test_builder_honours_stage_order():
    reference = FarEndReference(sample_rate=RATE, window_sec=1.0)
    pipeline = build_capture_pipeline(
        rate=RATE, reference=reference, stages="normalize,preprocess"
    )
    assert pipeline.stage_names == ["normalize", "preprocess"]


def test_enhancer_publishes_playback_as_echo_reference():
    enhancer = CaptureEnhancer(rate=RATE, enabled=True, stages="echo_control")
    enhancer.playback_started(0.0)
    enhancer.publish_playback(float_to_pcm16(_tone(1600)))
    assert enhancer.reference.active is True
    assert enhancer.reference.has_content()
    enhancer.playback_finished()
    assert enhancer.reference.active is False


def test_enhancer_truncates_reference_on_barge_in():
    enhancer = CaptureEnhancer(rate=RATE, enabled=True, stages="echo_control")
    enhancer.playback_started(0.0)
    enhancer.publish_playback(float_to_pcm16(np.ones(8000, dtype=np.float32)))
    enhancer.playback_finished(at_time=0.25)
    assert not enhancer.reference.aligned(0.5, 800, 0).any()


def test_enhancer_reports_latency_and_stage_stats():
    enhancer = CaptureEnhancer(
        rate=RATE, enabled=True, stages="preprocess,speaker_focus,normalize"
    )
    for start in range(0, 4000, BLOCK):
        enhancer.process(
            float_to_pcm16(_tone(BLOCK)), timestamp=(start + BLOCK) / RATE
        )
    stats = enhancer.stats()
    assert stats["enabled"] is True
    assert "baseline_db" in stats["stages"]["speaker_focus"]
    assert enhancer.latency_ms >= 0.0


# ── modelos reales (se saltan si no están descargados) ──


def _model_missing(configured: str) -> bool:
    path = resolve_model_path(configured)
    return path is None or not path.is_file()


@pytest.mark.skipif(
    _model_missing("models/silero_vad.onnx"),
    reason="modelo Silero VAD ausente (python scripts/fetch_audio_models.py)",
)
def test_silero_rejects_noise_and_the_pipeline_outputs_silence():
    from services.audio.stages.vad import SileroOnnxDetector

    detector = SileroOnnxDetector("models/silero_vad.onnx", RATE)
    noise = _noise(RATE * 2, amp=0.15, seed=3)
    probabilities = [
        detector.probability(noise[i : i + detector.frame_size])
        for i in range(0, noise.size - detector.frame_size, detector.frame_size)
    ]
    assert max(probabilities) < 0.5  # ruido: nunca se confunde con voz

    enhancer = CaptureEnhancer(rate=RATE, enabled=True)
    out = b""
    for start in range(0, noise.size, BLOCK):
        chunk, _ = enhancer.process(
            float_to_pcm16(noise[start : start + BLOCK]),
            timestamp=(start + BLOCK) / RATE,
        )
        out += chunk
    assert not pcm16_to_float(out).any()  # al STT no llega nada de ruido


@pytest.mark.skipif(
    _model_missing("models/silero_vad.onnx"),
    reason="modelo Silero VAD ausente (python scripts/fetch_audio_models.py)",
)
def test_silero_detector_validates_frame_size_and_model_rate():
    from services.audio.stages.vad import SileroOnnxDetector

    detector = SileroOnnxDetector("models/silero_vad.onnx", RATE)
    assert detector.frame_size == 256
    with pytest.raises(ValueError):
        detector.probability(np.zeros(100, dtype=np.float32))
    with pytest.raises(ValueError):
        SileroOnnxDetector("models/silero_vad.onnx", 44100)
    with pytest.raises(FileNotFoundError):
        SileroOnnxDetector("models/no_existe.onnx", RATE)


@pytest.mark.skipif(
    _model_missing("models/dpdfnet2_8khz.onnx"),
    reason="modelo de supresión ausente (python scripts/fetch_audio_models.py)",
)
def test_onnx_denoiser_reduces_noise_and_measures_its_own_delay():
    from services.audio.stages.denoise import OnnxStreamEnhancer

    enhancer = OnnxStreamEnhancer("models/dpdfnet2_8khz.onnx", RATE)
    assert enhancer.win_len == 160  # 20 ms a 8 kHz
    assert enhancer.output_delay_samples > 0

    noise = _noise(RATE * 2, amp=0.1, seed=5)
    out = np.concatenate(
        [enhancer.enhance(noise[i : i + BLOCK]) for i in range(0, noise.size, BLOCK)]
    )
    assert rms(out) < rms(noise) / 2  # el ruido de fondo se va


@pytest.mark.skipif(
    _model_missing("models/silero_vad.onnx"),
    reason="modelo Silero VAD ausente (python scripts/fetch_audio_models.py)",
)
def test_default_pipeline_reports_every_configured_stage():
    enhancer = CaptureEnhancer(rate=RATE, enabled=True)
    assert enhancer.pipeline is not None
    # La puerta va ANTES del supresor: el detector ve la señal natural y el
    # supresor no gasta CPU con el canal cerrado.
    expected = [
        "preprocess",
        "echo_control",
        "dereverb",
        "speaker_focus",
        "voice_gate",
        "denoise",
        "voice_focus",
        "normalize",
    ]
    assert enhancer.pipeline.stage_names == expected
    assert 0.0 < enhancer.latency_ms < 400.0


# ── post-filtro de voz objetivo ──


def _voice_focus(**overrides):
    params = dict(
        rate=RATE,
        frame_size=256,
        hop_size=128,
        f0_min=70.0,
        f0_max=320.0,
        voicing_threshold=0.30,
        harmonic_strength=3.0,
        harmonic_limit_hz=2000.0,
        harmonic_width_hz=60.0,
        modulation_strength=1.0,
        modulation_fast_ms=30.0,
        modulation_slow_ms=500.0,
        modulation_target=0.35,
        floor=0.05,
        smoothing=0.6,
    )
    params.update(overrides)
    return VoiceFocusStage(**params)


def _harmonic_voice(samples: int, f0: float = 120.0, amp: float = 0.3) -> np.ndarray:
    """Señal con estructura de voz: fundamental + armónicos + ritmo silábico."""
    t = np.arange(samples) / RATE
    signal = np.zeros(samples, dtype=np.float64)
    for harmonic in range(1, 12):
        signal += (1.0 / harmonic) * np.sin(2 * np.pi * f0 * harmonic * t)
    signal *= 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t)  # sílabas a 4 Hz
    signal /= max(np.max(np.abs(signal)), 1e-9)
    return (amp * signal).astype(np.float32)


def test_voice_focus_keeps_a_harmonic_voice():
    stage = _voice_focus()
    signal = _harmonic_voice(RATE * 2)
    out = _feed(AudioPipeline([stage], input_rate=RATE), signal)
    assert rms(out) > rms(signal) * 0.5
    assert stage.stats()["voiced_ratio"] > 0.5
    assert 100.0 < stage.stats()["f0_mean_hz"] < 140.0


def test_voice_focus_attenuates_a_sustained_chord_more_than_a_voice():
    """Un acorde sostenido (varias fundamentales, sin ritmo silábico) debe perder
    más energía que una voz: es el criterio que ataca la música de fondo."""
    t = np.arange(RATE * 2) / RATE
    chord = np.zeros(t.size, dtype=np.float64)
    for root in (220.0, 277.2, 329.6):
        for harmonic in (1, 2, 3):
            chord += np.sin(2 * np.pi * root * harmonic * t) / harmonic
    chord = (0.3 * chord / np.max(np.abs(chord))).astype(np.float32)

    voice = _harmonic_voice(RATE * 2)
    chord_out = _feed(AudioPipeline([_voice_focus()], input_rate=RATE), chord)
    voice_out = _feed(AudioPipeline([_voice_focus()], input_rate=RATE), voice)
    chord_loss = rms(chord_out) / max(rms(chord), 1e-9)
    voice_loss = rms(voice_out) / max(rms(voice), 1e-9)
    assert chord_loss < voice_loss


def test_voice_focus_attenuates_a_pure_tone():
    stage = _voice_focus()
    tone = _tone(RATE * 2, freq=1000.0, amp=0.3)
    out = _feed(AudioPipeline([stage], input_rate=RATE), tone)
    assert rms(out) < rms(tone) * 0.6


def test_voice_focus_can_be_disabled_per_criterion():
    signal = _harmonic_voice(RATE)
    neutral = _voice_focus(harmonic_strength=0.0, modulation_strength=0.0)
    out = _feed(AudioPipeline([neutral], input_rate=RATE), signal)
    delay = 128
    usable = min(out.size - delay, signal.size)
    assert np.abs(out[delay : delay + usable] - signal[:usable]).max() < 1e-4


def test_voice_focus_publishes_pitch_and_resets():
    stage = _voice_focus()
    ctx = FrameContext(timestamp=0.02, input_rate=RATE)
    for _ in range(8):
        stage.process(_harmonic_voice(BLOCK), ctx)
    assert "voice_focus_f0" in ctx.notes
    stage.reset()
    assert stage.latency_ms == pytest.approx(16.0, abs=1.0)


# ── concurrencia y aislamiento entre llamadas ──


def test_onnx_sessions_are_shared_across_calls():
    """Los pesos se cargan una vez por proceso, no una por llamada: con una sesión
    por llamada, cuarenta llamadas serían cientos de MB de copias idénticas."""
    from services.audio.runtime_pool import session_count

    enhancers = [CaptureEnhancer(rate=RATE, enabled=True) for _ in range(6)]
    for enhancer in enhancers:
        enhancer.process(float_to_pcm16(_tone(BLOCK)), timestamp=0.02)
    # Con modelos presentes son 2 sesiones (VAD + supresor); sin modelos, 0.
    assert session_count() <= 2


def test_calls_do_not_share_state():
    """Dos llamadas con la misma entrada dan la misma salida, y lo que pasa en una
    no altera a la otra."""
    first = CaptureEnhancer(rate=RATE, enabled=True)
    second = CaptureEnhancer(rate=RATE, enabled=True)
    signal = _harmonic_voice(RATE)

    out_a, _ = first.process(float_to_pcm16(signal[:BLOCK]), timestamp=0.02)
    out_b, _ = second.process(float_to_pcm16(signal[:BLOCK]), timestamp=0.02)
    assert out_a == out_b  # mismo punto de partida, ningún estado compartido

    # La segunda llamada procesa silencio; la primera debe seguir su curso igual
    # que si la otra no existiera.
    reference = CaptureEnhancer(rate=RATE, enabled=True)
    reference.process(float_to_pcm16(signal[:BLOCK]), timestamp=0.02)
    for index in range(1, 12):
        chunk = signal[index * BLOCK : (index + 1) * BLOCK]
        second.process(float_to_pcm16(np.zeros(BLOCK, dtype=np.float32)), timestamp=index * 0.02)
        out_first, _ = first.process(float_to_pcm16(chunk), timestamp=index * 0.02)
        out_ref, _ = reference.process(float_to_pcm16(chunk), timestamp=index * 0.02)
    assert out_first == out_ref


def test_pipeline_is_built_off_the_constructor():
    """La construcción (carga de modelos, ~1 s la primera vez) no ocurre en el
    constructor: si ocurriera, bloquearía el bucle de eventos al entrar la llamada."""
    enhancer = CaptureEnhancer(rate=RATE, enabled=True)
    assert enhancer.stats()["built"] is False
    enhancer.process(float_to_pcm16(_tone(BLOCK)), timestamp=0.02)
    assert enhancer.stats()["built"] is True


def test_process_async_runs_outside_the_event_loop():
    enhancer = CaptureEnhancer(rate=RATE, enabled=True)
    loop_thread = threading.get_ident()
    seen: list[int] = []
    original = enhancer._process_locked

    def spy(*args, **kwargs):
        seen.append(threading.get_ident())
        return original(*args, **kwargs)

    enhancer._process_locked = spy

    async def scenario():
        await enhancer.process_async(float_to_pcm16(_tone(BLOCK)), timestamp=0.02)

    asyncio.run(scenario())
    assert seen and all(ident != loop_thread for ident in seen)


def test_process_async_degrades_instead_of_queueing_when_saturated(monkeypatch):
    """Saturado, el pipeline entrega el PCM sin procesar en vez de acumular
    retardo: el atraso de una llamada en tiempo real no se recupera nunca."""
    enhancer = CaptureEnhancer(rate=RATE, enabled=True)
    enhancer._block_timeout = 0.001

    def slow(*args, **kwargs):
        time.sleep(0.05)
        return b"", None

    enhancer._process_locked = slow
    pcm = float_to_pcm16(_tone(BLOCK))

    async def scenario():
        return await enhancer.process_async(pcm, timestamp=0.02)

    out, ctx = asyncio.run(scenario())
    assert out == pcm and ctx is None
    assert enhancer.blocks_degraded == 1
    assert enhancer.stats()["blocks_degraded"] == 1


def test_only_one_block_per_call_is_ever_in_flight():
    """Saturado, la llamada NO encola: un bloque abandonado sigue corriendo en su
    hilo y hasta que termine no se admite otro. Es lo que garantiza que el estado
    recurrente nunca se muta desde dos hilos a la vez."""
    enhancer = CaptureEnhancer(rate=RATE, enabled=True)
    enhancer._block_timeout = 0.005
    concurrent = 0
    peak = 0
    lock = threading.Lock()

    def slow(*args, **kwargs):
        nonlocal concurrent, peak
        with lock:
            concurrent += 1
            peak = max(peak, concurrent)
        time.sleep(0.05)
        with lock:
            concurrent -= 1
        return b"", None

    enhancer._process_locked = slow
    pcm = float_to_pcm16(_tone(BLOCK))

    async def scenario():
        for _ in range(10):
            await enhancer.process_async(pcm, timestamp=0.02)
            await asyncio.sleep(0.001)

    asyncio.run(scenario())
    assert peak == 1
    assert enhancer.blocks_degraded > 0


def test_worker_pool_and_call_limit_are_bounded():
    from services.audio.runtime_pool import (
        available_cpus,
        default_worker_threads,
        get_audio_executor,
        max_concurrent_calls,
    )

    assert available_cpus() >= 1
    assert default_worker_threads() >= 1
    assert get_audio_executor() is get_audio_executor()  # uno por proceso
    assert max_concurrent_calls() >= 1


def test_concurrent_calls_stay_isolated_under_threads():
    """Varias llamadas procesando a la vez en el pool: cada una debe producir el
    mismo resultado que si estuviera sola."""
    signal = _harmonic_voice(RATE)
    blocks = [signal[i * BLOCK : (i + 1) * BLOCK] for i in range(20)]

    solo = CaptureEnhancer(rate=RATE, enabled=True)
    expected = b"".join(
        solo.process(float_to_pcm16(block), timestamp=(index + 1) * 0.02)[0]
        for index, block in enumerate(blocks)
    )

    async def one_call():
        enhancer = CaptureEnhancer(rate=RATE, enabled=True)
        chunks = []
        for index, block in enumerate(blocks):
            out, _ = await enhancer.process_async(
                float_to_pcm16(block), timestamp=(index + 1) * 0.02
            )
            chunks.append(out)
        return b"".join(chunks)

    async def scenario():
        return await asyncio.gather(*[one_call() for _ in range(6)])

    results = asyncio.run(scenario())
    assert all(result == expected for result in results)


# ── ahorro de CPU con el canal cerrado ──


class _CountingEnhancer:
    """Supresor de prueba que cuenta cuántas veces se ejecuta de verdad."""

    def __init__(self):
        self.output_delay_samples = 0
        self.calls = 0
        self.bypassed = 0

    def enhance(self, block):
        self.calls += 1
        return block

    def advance_silent(self, block):
        self.bypassed += 1
        return np.zeros(block.size, dtype=np.float32)

    def reset(self):
        pass


def test_denoise_skips_inference_after_sustained_silence():
    enhancer = _CountingEnhancer()
    stage = DenoiseStage(
        enhancer,
        rate=RATE,
        attn_limit_db=None,
        bypass_on_speech_absent=True,
        bypass_hold_ms=100.0,
    )
    silence = np.zeros(BLOCK, dtype=np.float32)
    for index in range(20):
        stage.process(silence, FrameContext(timestamp=index * 0.02, input_rate=RATE))
    assert enhancer.bypassed > 10          # dejó de inferir
    assert enhancer.calls <= 6            # solo durante la retención inicial
    assert stage.stats()["bypass_ratio"] > 0.5


def test_denoise_resumes_inference_as_soon_as_speech_returns():
    enhancer = _CountingEnhancer()
    stage = DenoiseStage(
        enhancer,
        rate=RATE,
        attn_limit_db=None,
        bypass_on_speech_absent=True,
        bypass_hold_ms=100.0,
    )
    silence = np.zeros(BLOCK, dtype=np.float32)
    for index in range(20):
        stage.process(silence, FrameContext(timestamp=index * 0.02, input_rate=RATE))
    before = enhancer.calls
    speech_ctx = FrameContext(timestamp=0.42, input_rate=RATE, speech_active=True)
    stage.process(_tone(BLOCK), speech_ctx)
    assert enhancer.calls == before + 1


def test_denoise_bypass_preserves_the_sample_timeline():
    """El ahorro no debe desplazar la línea de tiempo: con y sin bypass la etapa
    entrega el mismo número de muestras."""
    signal = np.concatenate([_tone(BLOCK * 10, amp=0.0), _tone(BLOCK * 10)])
    counts = {}
    for bypass in (False, True):
        stage = DenoiseStage(
            _CountingEnhancer(),
            rate=RATE,
            attn_limit_db=None,
            bypass_on_speech_absent=bypass,
            bypass_hold_ms=40.0,
        )
        total = 0
        for index in range(0, signal.size, BLOCK):
            ctx = FrameContext(timestamp=index / RATE, input_rate=RATE)
            total += stage.process(signal[index : index + BLOCK], ctx).size
        counts[bypass] = total
    assert counts[False] == counts[True]


@pytest.mark.skipif(
    _model_missing("models/dpdfnet2_8khz.onnx"),
    reason="modelo de supresión ausente (python scripts/fetch_audio_models.py)",
)
def test_onnx_enhancer_advance_silent_matches_the_frame_accounting():
    from services.audio.stages.denoise import OnnxStreamEnhancer

    reference = OnnxStreamEnhancer("models/dpdfnet2_8khz.onnx", RATE)
    bypassed = OnnxStreamEnhancer("models/dpdfnet2_8khz.onnx", RATE)
    signal = _noise(BLOCK * 12, amp=0.05)
    produced_real = sum(
        reference.enhance(signal[i : i + BLOCK]).size
        for i in range(0, signal.size, BLOCK)
    )
    produced_skip = sum(
        bypassed.advance_silent(signal[i : i + BLOCK]).size
        for i in range(0, signal.size, BLOCK)
    )
    assert produced_real == produced_skip


def test_stage_fallbacks_work_without_any_model(monkeypatch):
    """Sin modelos descargados el pipeline no se cae: usa respaldo espectral y
    detector por energía, y lo registra."""
    from core.config import settings

    monkeypatch.setattr(settings, "AUDIO_VAD_MODEL_PATH", "models/inexistente.onnx")
    monkeypatch.setattr(settings, "AUDIO_DENOISE_MODEL_PATH", "models/inexistente.onnx")
    enhancer = CaptureEnhancer(rate=RATE, enabled=True)
    assert enhancer.pipeline is not None
    signal = _tone(3200, amp=0.3)
    total = 0
    for start in range(0, signal.size, BLOCK):
        chunk, _ = enhancer.process(
            float_to_pcm16(signal[start : start + BLOCK]),
            timestamp=(start + BLOCK) / RATE,
        )
        total += len(chunk)
    assert total > 0
