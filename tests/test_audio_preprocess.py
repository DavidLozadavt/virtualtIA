"""Tests del pre-procesamiento de audio previo a Whisper (audio_preprocess).

Cubre el pipeline: resample 8k→16k, normalización de pico (90%) y high-pass
~80 Hz. Verifica que:
  - el resample produce el número correcto de muestras,
  - la normalización no genera clipping (y sí escala al pico objetivo),
  - el high-pass no corta el rango de voz telefónica (300–3400 Hz) y sí
    atenúa el ruido de baja frecuencia (<80 Hz).
"""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest

from services.telephony.audio_preprocess import (
    _TARGET_SAMPLE_RATE,
    highpass_filter,
    peak_normalize,
    preprocess_pcm16,
    resample_pcm16,
)

SRC_RATE = 8000


def _sine_pcm16(freq: float, ms: int, rate: int, amp: int = 12000) -> bytes:
    n = int(rate * ms / 1000)
    out = bytearray()
    for i in range(n):
        val = int(amp * math.sin(2 * math.pi * freq * i / rate))
        out += struct.pack("<h", val)
    return bytes(out)


def _samples(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16)


def _peak(pcm: bytes) -> int:
    s = _samples(pcm)
    return int(np.max(np.abs(s.astype(np.int32)))) if s.size else 0


def _band_energy(pcm: bytes, rate: int, lo: float, hi: float) -> float:
    """Energía espectral en la banda [lo, hi] Hz vía FFT."""
    s = _samples(pcm).astype(np.float64)
    if s.size == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(s))
    freqs = np.fft.rfftfreq(s.size, d=1.0 / rate)
    mask = (freqs >= lo) & (freqs <= hi)
    return float(np.sum(spec[mask] ** 2))


# ── resample_pcm16 ───────────────────────────────────────────────────────────

def test_resample_doubles_sample_count_and_sets_rate():
    pcm = _sine_pcm16(1000, 100, SRC_RATE)  # 800 muestras @ 8k
    out, rate = resample_pcm16(pcm, SRC_RATE)
    assert rate == _TARGET_SAMPLE_RATE == 16000
    assert len(out) // 2 == (len(pcm) // 2) * 2  # up=2, down=1


def test_resample_preserves_duration_seconds():
    pcm = _sine_pcm16(440, 250, SRC_RATE)
    out, rate = resample_pcm16(pcm, SRC_RATE)
    dur_in = (len(pcm) // 2) / SRC_RATE
    dur_out = (len(out) // 2) / rate
    assert dur_out == pytest.approx(dur_in, abs=1e-3)


def test_resample_noop_when_rates_match():
    pcm = _sine_pcm16(440, 50, _TARGET_SAMPLE_RATE)
    out, rate = resample_pcm16(pcm, _TARGET_SAMPLE_RATE)
    assert out == pcm and rate == _TARGET_SAMPLE_RATE


def test_resample_empty_is_safe():
    out, rate = resample_pcm16(b"", SRC_RATE)
    assert out == b"" and rate == SRC_RATE


# ── peak_normalize ───────────────────────────────────────────────────────────

def test_normalize_scales_quiet_signal_up_to_target():
    pcm = _sine_pcm16(1000, 100, _TARGET_SAMPLE_RATE, amp=2000)  # señal débil
    out = peak_normalize(pcm, target_peak=0.9)
    expected = int(0.9 * 32767)
    assert _peak(out) == pytest.approx(expected, rel=0.02)


def test_normalize_never_clips():
    # Señal ya fuerte (cerca de full-scale): tras normalizar NO debe pasar de
    # 90% del rango int16 → nunca clipping a ±32767.
    pcm = _sine_pcm16(1000, 100, _TARGET_SAMPLE_RATE, amp=32000)
    out = peak_normalize(pcm, target_peak=0.9)
    peak = _peak(out)
    assert peak <= int(0.9 * 32767) + 1
    assert peak < 32767  # sin clipping


def test_normalize_silence_stays_silent():
    pcm = b"\x00\x00" * 800
    out = peak_normalize(pcm, target_peak=0.9)
    assert _peak(out) == 0  # no inventa energía / no divide por cero


# ── highpass_filter ──────────────────────────────────────────────────────────

def test_highpass_keeps_voice_band():
    # Tonos en banda telefónica de voz deben sobrevivir (300–3400 Hz).
    for freq in (300, 1000, 3400):
        pcm = _sine_pcm16(freq, 200, _TARGET_SAMPLE_RATE, amp=10000)
        out = highpass_filter(pcm, _TARGET_SAMPLE_RATE, cutoff_hz=80.0)
        e_in = _band_energy(pcm, _TARGET_SAMPLE_RATE, freq - 50, freq + 50)
        e_out = _band_energy(out, _TARGET_SAMPLE_RATE, freq - 50, freq + 50)
        # Conserva al menos ~80% de la energía del tono de voz.
        assert e_out >= 0.8 * e_in, f"voz {freq}Hz atenuada de más"


def test_highpass_attenuates_low_frequency_hum():
    # Un hum de 50 Hz (zumbido de línea) debe quedar fuertemente atenuado.
    pcm = _sine_pcm16(50, 200, _TARGET_SAMPLE_RATE, amp=10000)
    out = highpass_filter(pcm, _TARGET_SAMPLE_RATE, cutoff_hz=80.0)
    e_in = _band_energy(pcm, _TARGET_SAMPLE_RATE, 0, 80)
    e_out = _band_energy(out, _TARGET_SAMPLE_RATE, 0, 80)
    assert e_out < 0.25 * e_in  # al menos -6 dB


def test_highpass_empty_is_safe():
    assert highpass_filter(b"", _TARGET_SAMPLE_RATE) == b""


# ── preprocess_pcm16 (pipeline completo) ─────────────────────────────────────

def test_preprocess_outputs_16k_and_doubles_samples():
    pcm = _sine_pcm16(1000, 200, SRC_RATE, amp=4000)
    out, rate = preprocess_pcm16(pcm, SRC_RATE, call_uuid="t1")
    assert rate == 16000
    assert len(out) // 2 == (len(pcm) // 2) * 2


def test_preprocess_no_clipping():
    pcm = _sine_pcm16(1000, 200, SRC_RATE, amp=30000)
    out, _ = preprocess_pcm16(pcm, SRC_RATE, call_uuid="t2")
    assert _peak(out) < 32767


def test_preprocess_preserves_voice_band():
    pcm = _sine_pcm16(1000, 200, SRC_RATE, amp=8000)
    out, rate = preprocess_pcm16(pcm, SRC_RATE, call_uuid="t3")
    e = _band_energy(out, rate, 950, 1050)
    assert e > 0.0  # el tono de voz sobrevive al pipeline


def test_preprocess_empty_is_safe():
    out, rate = preprocess_pcm16(b"", SRC_RATE)
    assert out == b"" and rate == SRC_RATE
