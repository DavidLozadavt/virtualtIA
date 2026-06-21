"""Pre-procesamiento de audio telefónico previo a Whisper STT.

Whisper fue entrenado mayormente a 16 kHz. El audio telefónico llega a 8 kHz
(PCMU decodificado a PCM16), con poco nivel y con ruido/hum de baja frecuencia.
Este módulo limpia la señal *justo antes* de construir el WAV que se envía al
proveedor, en este orden:

  1. Resample 8000 Hz → 16000 Hz (FIR polifásico banda-limitado, anti-alias).
  2. High-pass ~80 Hz (quita hum de línea sin tocar la banda de voz 300–3400 Hz).
  3. Normalización de pico al 90% (señal consistente, sin clipping).

Nota de orden: la normalización es el ÚLTIMO paso a propósito. Un high-pass
Butterworth sobre-oscila (ringing) por encima del pico de la señal; si se
normalizara antes del filtro, ese ringing empujaría la salida por encima de
full-scale y volvería a producir clipping. Normalizar al final garantiza que el
pico final sea exactamente el objetivo (90%) y nunca haya clipping.

No toca VAD, detección de silencio ni el acumulador de audio: opera sobre el
buffer PCM16 ya acumulado. Si numpy/scipy no están disponibles, degrada con
gracia devolviendo el audio sin procesar (nunca rompe el STT).
"""

from __future__ import annotations

import logging
from math import gcd

logger = logging.getLogger("lyra.telephony.audio")

_TARGET_SAMPLE_RATE = 16000
_DEFAULT_TARGET_PEAK = 0.9      # 90% del rango int16 → margen anti-clipping
_DEFAULT_HPF_CUTOFF_HZ = 80.0   # bajo el F0 de voz (~85 Hz hombre), sobre el hum
_HPF_ORDER = 4
_INT16_MAX = 32767


def _try_numpy():
    try:
        import numpy as np

        return np
    except Exception as e:  # dependencia opcional: nunca romper el STT
        logger.warning("[audio] numpy no disponible (%s); pre-proceso omitido", e)
        return None


def resample_pcm16(
    pcm: bytes, src_rate: int, dst_rate: int = _TARGET_SAMPLE_RATE
) -> tuple[bytes, int]:
    """Remuestrea PCM16 mono a `dst_rate` con `scipy.signal.resample_poly`.

    Usa un filtro FIR polifásico (banda limitada), no interpolación lineal.
    Returns: (pcm_bytes, sample_rate_real).
    """
    if not pcm or src_rate == dst_rate:
        return pcm, src_rate

    np = _try_numpy()
    if np is None:
        return pcm, src_rate
    try:
        from scipy.signal import resample_poly
    except Exception as e:
        logger.warning(
            "[audio] scipy no disponible (%s); envío %d Hz sin remuestrear", e, src_rate
        )
        return pcm, src_rate

    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return pcm, src_rate

    g = gcd(src_rate, dst_rate)
    up = dst_rate // g
    down = src_rate // g

    resampled = resample_poly(samples.astype(np.float64), up, down)
    # El FIR puede sobre-oscilar (Gibbs); recortar al rango int16 evita
    # wraparound (clicks), no introduce silencios.
    resampled = np.clip(np.round(resampled), -32768, _INT16_MAX).astype(np.int16)
    return resampled.tobytes(), dst_rate


def peak_normalize(pcm: bytes, target_peak: float = _DEFAULT_TARGET_PEAK) -> bytes:
    """Escala la señal para que su pico alcance `target_peak` del rango int16.

    Peak normalization (no RMS): garantiza nivel consistente y deja margen para
    evitar clipping. El silencio se devuelve intacto (no se inventa energía).
    """
    if not pcm:
        return pcm
    np = _try_numpy()
    if np is None:
        return pcm

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return pcm

    peak = float(np.max(np.abs(samples)))
    if peak <= 0.0:
        return pcm  # silencio puro

    target = target_peak * _INT16_MAX
    gain = target / peak
    out = np.clip(np.round(samples * gain), -32768, _INT16_MAX).astype(np.int16)
    return out.tobytes()


def highpass_filter(
    pcm: bytes,
    sample_rate: int,
    cutoff_hz: float = _DEFAULT_HPF_CUTOFF_HZ,
    order: int = _HPF_ORDER,
) -> bytes:
    """High-pass Butterworth (SOS, fase cero) en `cutoff_hz`.

    Atenúa hum/ruido de baja frecuencia (<80 Hz) sin tocar la banda de voz
    telefónica (300–3400 Hz). Usa `sosfiltfilt` (cero desfase) para no introducir
    retardo de grupo en la voz.
    """
    if not pcm:
        return pcm
    np = _try_numpy()
    if np is None:
        return pcm
    try:
        from scipy.signal import butter, sosfiltfilt
    except Exception as e:
        logger.warning("[audio] scipy no disponible (%s); high-pass omitido", e)
        return pcm

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return pcm

    nyq = sample_rate / 2.0
    wn = cutoff_hz / nyq
    if not (0.0 < wn < 1.0):
        return pcm  # cutoff fuera de rango: no filtrar

    sos = butter(order, wn, btype="highpass", output="sos")
    # filtfilt necesita longitud > 3*(orden de los SOS); en frames muy cortos
    # cae a filtrado simple para no lanzar excepción.
    padlen = 3 * (sos.shape[0] * 2)
    if samples.size <= padlen:
        from scipy.signal import sosfilt

        filtered = sosfilt(sos, samples)
    else:
        filtered = sosfiltfilt(sos, samples)

    out = np.clip(np.round(filtered), -32768, _INT16_MAX).astype(np.int16)
    return out.tobytes()


def _peak_dbfs(pcm: bytes) -> float:
    """Pico en dBFS (0 = full-scale). -inf si silencio. Solo para logs."""
    np = _try_numpy()
    if np is None or not pcm:
        return float("-inf")
    s = np.frombuffer(pcm, dtype=np.int16)
    if s.size == 0:
        return float("-inf")
    peak = float(np.max(np.abs(s.astype(np.float64))))
    if peak <= 0.0:
        return float("-inf")
    import math

    return 20.0 * math.log10(peak / _INT16_MAX)


def preprocess_pcm16(
    pcm: bytes,
    src_rate: int,
    *,
    dst_rate: int = _TARGET_SAMPLE_RATE,
    target_peak: float = _DEFAULT_TARGET_PEAK,
    hpf_cutoff_hz: float = _DEFAULT_HPF_CUTOFF_HZ,
    call_uuid: str = "",
) -> tuple[bytes, int]:
    """Pipeline completo: resample → normalize → high-pass.

    Returns (pcm_bytes, sample_rate). Emite logs INFO con Hz entrada/salida,
    duración en ms y nivel (dBFS) antes/después de normalizar, para verificar en
    producción que el pre-procesamiento corre.
    """
    if not pcm:
        return pcm, src_rate

    dur_ms_in = (len(pcm) / 2) / src_rate * 1000.0

    # 1) Resample
    pcm, rate = resample_pcm16(pcm, src_rate, dst_rate)

    # 2) High-pass (antes de normalizar: ver nota de orden en el docstring)
    pcm = highpass_filter(pcm, rate, cutoff_hz=hpf_cutoff_hz)

    # 3) Normalización de pico (medimos antes/después para el log)
    peak_before = _peak_dbfs(pcm)
    pcm = peak_normalize(pcm, target_peak=target_peak)
    peak_after = _peak_dbfs(pcm)

    logger.info(
        "[audio] preprocess call_uuid=%s in=%dHz out=%dHz dur=%.0fms "
        "peak_before=%.1fdBFS peak_after=%.1fdBFS hpf=%.0fHz",
        call_uuid,
        src_rate,
        rate,
        dur_ms_in,
        peak_before,
        peak_after,
        hpf_cutoff_hz,
    )
    return pcm, rate
