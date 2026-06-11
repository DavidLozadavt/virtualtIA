"""
Detección simple de fin de turno (VAD por energía) para audio µ-law 8 kHz.
"""

from __future__ import annotations

import audioop
from typing import Tuple


def mulaw_rms(mulaw_bytes: bytes) -> float:
    """RMS del frame µ-law decodificado a PCM 16-bit."""
    if len(mulaw_bytes) < 80:
        return 0.0
    pcm = audioop.ulaw2lin(mulaw_bytes, 2)
    return float(audioop.rms(pcm, 2))


def is_speech_frame(mulaw_bytes: bytes, threshold: float = 350.0) -> bool:
    return mulaw_rms(mulaw_bytes) >= threshold


def pcm16_rms(pcm_bytes: bytes) -> float:
    """RMS de frame PCM 16-bit mono."""
    if len(pcm_bytes) < 80:
        return 0.0
    return float(audioop.rms(pcm_bytes, 2))


def is_speech_frame_pcm16(pcm_bytes: bytes, threshold: float = 500.0) -> bool:
    return pcm16_rms(pcm_bytes) >= threshold


def detect_end_of_utterance_pcm16(
    buffer: bytes,
    *,
    frame_ms: int = 20,
    sample_rate: int = 8000,
    silence_frames: int = 20,
    speech_threshold: float = 500.0,
    min_speech_frames: int = 12,
) -> Tuple[bool, int]:
    """VAD para PCM 16-bit (mod_audio_stream mono 8 kHz)."""
    frame_bytes = int(sample_rate * frame_ms / 1000) * 2
    if len(buffer) < frame_bytes * min_speech_frames:
        return False, 0

    speech_frames = 0
    trailing_silence = 0
    max_speech = 0

    for i in range(0, len(buffer) - frame_bytes + 1, frame_bytes):
        frame = buffer[i : i + frame_bytes]
        if is_speech_frame_pcm16(frame, speech_threshold):
            speech_frames += 1
            trailing_silence = 0
            max_speech = max(max_speech, speech_frames)
        else:
            if speech_frames > 0:
                trailing_silence += 1
            if trailing_silence >= silence_frames and max_speech >= min_speech_frames:
                return True, max_speech

    return False, max_speech


def detect_end_of_utterance(
    buffer: bytes,
    *,
    frame_ms: int = 20,
    sample_rate: int = 8000,
    silence_frames: int = 25,
    speech_threshold: float = 350.0,
    min_speech_frames: int = 15,
) -> Tuple[bool, int]:
    """
    Analiza buffer µ-law y detecta si hay suficiente habla seguida de silencio.

    Returns: (end_detected, speech_frame_count)
    """
    frame_bytes = int(sample_rate * frame_ms / 1000)
    if len(buffer) < frame_bytes * min_speech_frames:
        return False, 0

    speech_frames = 0
    trailing_silence = 0
    max_speech = 0

    for i in range(0, len(buffer) - frame_bytes + 1, frame_bytes):
        frame = buffer[i : i + frame_bytes]
        if is_speech_frame(frame, speech_threshold):
            speech_frames += 1
            trailing_silence = 0
            max_speech = max(max_speech, speech_frames)
        else:
            if speech_frames > 0:
                trailing_silence += 1
            if trailing_silence >= silence_frames and max_speech >= min_speech_frames:
                return True, max_speech

    return False, max_speech
