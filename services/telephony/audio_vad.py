"""
Detección simple de fin de turno (VAD por energía) para audio µ-law 8 kHz.
"""

from __future__ import annotations

import audioop
from typing import Callable, Tuple

from core.config import settings


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


# ── Calibración adaptativa de umbral ───────────────────────────────────────────
#
# El umbral RMS fijo (350 µ-law / 500 PCM16) rechazaba voces bajas o temblorosas
# de adultos mayores: hablan por debajo de ese piso, el VAD nunca detecta habla y
# la locución se corta por timeout o se descarta. En vez de un absoluto, medimos
# el ruido de fondo en los primeros ~300ms de la captura (antes de que el usuario
# hable) y situamos el umbral como un delta relativo SOBRE ese piso, acotado por
# un mínimo absoluto para que el silencio total no lo vuelva ~0.

# Mínimo absoluto del umbral por codec (bajo los antiguos 350/500 a propósito,
# para dar margen a voces bajas en líneas limpias).
_MIN_FLOOR_MULAW = 150.0
_MIN_FLOOR_PCM16 = 200.0


def _measure_noise_floor(
    buffer: bytes,
    frame_bytes: int,
    rms_fn: Callable[[bytes], float],
    calib_frames: int,
) -> float:
    """Piso de ruido = percentil 25 del RMS de los primeros `calib_frames`.

    El percentil 25 (no el promedio ni el mínimo) es robusto: ignora frames altos
    si el usuario empieza a hablar pronto, y descarta un dropout puntual a 0.
    """
    end = min(len(buffer), frame_bytes * calib_frames)
    vals = [
        rms_fn(buffer[i : i + frame_bytes])
        for i in range(0, end - frame_bytes + 1, frame_bytes)
    ]
    if not vals:
        return 0.0
    vals.sort()
    idx = min(len(vals) - 1, int(len(vals) * 0.25))
    return vals[idx]


def _adaptive_threshold(noise_floor: float, mult: float, min_floor: float) -> float:
    """Umbral relativo al piso de ruido, acotado inferiormente."""
    return max(min_floor, noise_floor * mult)


def _hangover_frames(frame_ms: int, silence_frames: int) -> int:
    """Frames de silencio requeridos = max(base, padding configurado).

    El padding (FS_VAD_HANGOVER_MS, ~600ms) evita cortar pausas naturales entre
    palabras de quien dicta una dirección despacio.
    """
    hangover_ms = getattr(settings, "FS_VAD_HANGOVER_MS", 600) or 600
    pad = round(float(hangover_ms) / max(1, frame_ms))
    return int(max(silence_frames, pad))


def _detect_end(
    buffer: bytes,
    *,
    frame_bytes: int,
    rms_fn: Callable[[bytes], float],
    frame_ms: int,
    silence_frames: int,
    base_threshold: float,
    min_speech_frames: int,
    min_floor: float,
) -> Tuple[bool, int]:
    """Núcleo VAD compartido (µ-law / PCM16) con umbral adaptativo + hangover."""
    if len(buffer) < frame_bytes * min_speech_frames:
        return False, 0

    calib_ms = getattr(settings, "FS_VAD_NOISE_CALIB_MS", 300) or 300
    calib_frames = max(1, round(float(calib_ms) / max(1, frame_ms)))
    mult = getattr(settings, "FS_VAD_NOISE_MULT", 1.8) or 1.8

    noise_floor = _measure_noise_floor(buffer, frame_bytes, rms_fn, calib_frames)
    threshold = _adaptive_threshold(noise_floor, float(mult), min_floor)

    hangover = _hangover_frames(frame_ms, silence_frames)

    speech_frames = 0
    trailing_silence = 0
    max_speech = 0

    for i in range(0, len(buffer) - frame_bytes + 1, frame_bytes):
        frame = buffer[i : i + frame_bytes]
        if rms_fn(frame) >= threshold:
            speech_frames += 1
            trailing_silence = 0
            max_speech = max(max_speech, speech_frames)
        else:
            if speech_frames > 0:
                trailing_silence += 1
            if trailing_silence >= hangover and max_speech >= min_speech_frames:
                return True, max_speech

    return False, max_speech


def detect_end_of_utterance_pcm16(
    buffer: bytes,
    *,
    frame_ms: int = 20,
    sample_rate: int = 8000,
    silence_frames: int = 20,
    speech_threshold: float = 500.0,
    min_speech_frames: int = 12,
) -> Tuple[bool, int]:
    """VAD para PCM 16-bit (mod_audio_stream mono 8 kHz), umbral adaptativo."""
    frame_bytes = int(sample_rate * frame_ms / 1000) * 2
    return _detect_end(
        buffer,
        frame_bytes=frame_bytes,
        rms_fn=pcm16_rms,
        frame_ms=frame_ms,
        silence_frames=silence_frames,
        base_threshold=speech_threshold,
        min_speech_frames=min_speech_frames,
        min_floor=_MIN_FLOOR_PCM16,
    )


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
    Umbral calibrado contra el ruido de fondo + hangover de pausa.

    Returns: (end_detected, speech_frame_count)
    """
    frame_bytes = int(sample_rate * frame_ms / 1000)
    return _detect_end(
        buffer,
        frame_bytes=frame_bytes,
        rms_fn=mulaw_rms,
        frame_ms=frame_ms,
        silence_frames=silence_frames,
        base_threshold=speech_threshold,
        min_speech_frames=min_speech_frames,
        min_floor=_MIN_FLOOR_MULAW,
    )
