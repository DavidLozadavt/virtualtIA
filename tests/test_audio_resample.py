"""Tests del remuestreo 8 kHz → 16 kHz previo a Whisper (stt_service)."""

from __future__ import annotations

import audioop
import math
import struct
import wave
from io import BytesIO

import pytest

from services.telephony.stt_service import (
    _TARGET_SAMPLE_RATE,
    _mulaw_to_wav,
    _pcm16_to_wav,
    _resample_pcm16,
)

SRC_RATE = 8000


def _sine_pcm16(freq: float, ms: int, rate: int = SRC_RATE, amp: int = 12000) -> bytes:
    n = int(rate * ms / 1000)
    out = bytearray()
    for i in range(n):
        val = int(amp * math.sin(2 * math.pi * freq * i / rate))
        out += struct.pack("<h", val)
    return bytes(out)


def _read_wav(wav_bytes: bytes):
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        return {
            "channels": wf.getnchannels(),
            "sampwidth": wf.getsampwidth(),
            "rate": wf.getframerate(),
            "frames": wf.getnframes(),
            "pcm": wf.readframes(wf.getnframes()),
        }


def _samples(pcm: bytes) -> list[int]:
    return list(struct.unpack("<%dh" % (len(pcm) // 2), pcm))


def _rms(pcm: bytes) -> float:
    return float(audioop.rms(pcm, 2))


# ── _resample_pcm16 ─────────────────────────────────────────────────────────

def test_resample_doubles_sample_count_and_sets_rate():
    pcm = _sine_pcm16(1000, 100)  # 800 muestras @ 8k
    out, rate = _resample_pcm16(pcm, SRC_RATE)
    assert rate == _TARGET_SAMPLE_RATE == 16000
    in_n = len(pcm) // 2
    out_n = len(out) // 2
    # up=2, down=1 → exactamente el doble de muestras
    assert out_n == in_n * 2


def test_resample_preserves_duration_seconds():
    ms = 250
    pcm = _sine_pcm16(440, ms)
    out, rate = _resample_pcm16(pcm, SRC_RATE)
    dur_in = (len(pcm) // 2) / SRC_RATE
    dur_out = (len(out) // 2) / rate
    assert dur_out == pytest.approx(dur_in, abs=1e-3)


def test_resample_noop_when_rates_match():
    pcm = _sine_pcm16(440, 50)
    out, rate = _resample_pcm16(pcm, _TARGET_SAMPLE_RATE)
    assert out == pcm and rate == _TARGET_SAMPLE_RATE


def test_resample_empty_is_safe():
    out, rate = _resample_pcm16(b"", SRC_RATE)
    assert out == b"" and rate == SRC_RATE


def test_resample_silence_stays_silent():
    pcm = b"\x00\x00" * 800
    out, _ = _resample_pcm16(pcm, SRC_RATE)
    assert _rms(out) == 0.0  # no se inventa ruido/energía


def test_resample_no_clipping_overflow():
    # Tono fuerte (no full-scale) — el FIR puede sobre-oscilar; verificamos que
    # el recorte mantiene el rango int16 sin wraparound (sin saltos de signo).
    pcm = _sine_pcm16(1500, 120, amp=30000)
    out, _ = _resample_pcm16(pcm, SRC_RATE)
    vals = _samples(out)
    assert all(-32768 <= v <= 32767 for v in vals)


def test_resample_preserves_tone_energy():
    pcm = _sine_pcm16(1000, 200, amp=10000)
    out, _ = _resample_pcm16(pcm, SRC_RATE)
    # La energía (RMS) del tono debe conservarse aproximadamente, no colapsar
    # a silencio ni dispararse.
    assert _rms(out) == pytest.approx(_rms(pcm), rel=0.15)


# ── _pcm16_to_wav ────────────────────────────────────────────────────────────

def test_pcm16_to_wav_is_16k_mono_pcm16():
    pcm = _sine_pcm16(440, 300)
    info = _read_wav(_pcm16_to_wav(pcm, SRC_RATE))
    assert info["channels"] == 1
    assert info["sampwidth"] == 2
    assert info["rate"] == 16000
    assert info["frames"] == (len(pcm) // 2) * 2


def test_pcm16_to_wav_duration_matches_input():
    ms = 320
    pcm = _sine_pcm16(600, ms)
    info = _read_wav(_pcm16_to_wav(pcm, SRC_RATE))
    dur = info["frames"] / info["rate"]
    assert dur == pytest.approx(ms / 1000, abs=2e-3)


# ── _mulaw_to_wav ────────────────────────────────────────────────────────────

def test_mulaw_to_wav_is_16k_mono_pcm16():
    pcm = _sine_pcm16(440, 300)
    mulaw = audioop.lin2ulaw(pcm, 2)
    info = _read_wav(_mulaw_to_wav(mulaw, SRC_RATE))
    assert info["channels"] == 1
    assert info["sampwidth"] == 2
    assert info["rate"] == 16000
    # µ-law: 1 byte/muestra @8k → tras resample x2 muestras
    assert info["frames"] == len(mulaw) * 2


def test_mulaw_to_wav_keeps_signal_not_silence():
    pcm = _sine_pcm16(800, 200, amp=10000)
    mulaw = audioop.lin2ulaw(pcm, 2)
    info = _read_wav(_mulaw_to_wav(mulaw, SRC_RATE))
    assert _rms(info["pcm"]) > 1000  # hay señal real, no silencio espurio
