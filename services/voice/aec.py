"""Cancelación de eco acústico (AEC) del lado servidor — NLMS + delay tracking.

En PSTN/SIP no existe el AEC de navegador (spec §3.6): sin esto, la voz TTS
del bot reinyectada por el canal dispara el VAD/STT de entrada y produce
falsas interrupciones. V1 lo "resolvía" descartando el audio del usuario
mientras el bot hablaba (gate half-duplex). V2 cancela el eco y deja pasar
la voz real del usuario en todo momento (full-duplex).

Implementación:
  - Filtro adaptativo NLMS de `VOICE_AEC_TAPS` coeficientes @8 kHz.
  - Estimación de retardo far↔near por correlación cruzada (FFT), refrescada
    periódicamente; el filtro se reinicia si el retardo salta.
  - Detección de doble-habla (Geigel): congela la adaptación cuando el usuario
    habla encima del bot, para no "aprender" su voz como eco.

Ambos relojes (near = frames que llegan de FreeSWITCH, far = audio que
enviamos con pacing casi-real-time) corren a 8 kHz, así que los contadores de
muestras propios de cada stream sirven como línea de tiempo común, con el
desfase real absorbido por el estimador de retardo.
"""

from __future__ import annotations

import logging

import numpy as np

from core.config import settings

logger = logging.getLogger("lyra.voice.aec")

SAMPLE_RATE = 8000
_FAR_BUFFER_SECONDS = 12
_MAX_DELAY_SAMPLES = 6400          # 800 ms de desfase máximo contemplado
_XCORR_WINDOW = 8192               # ~1 s de ventana para estimar retardo
_XCORR_REFRESH_SAMPLES = SAMPLE_RATE  # re-estimar cada ~1 s de near
_XCORR_MIN_PEAK_RATIO = 4.0        # pico/promedio mínimo para aceptar retardo
_DELAY_JUMP_RESET = 80             # muestras: salto de retardo que resetea el filtro
_FAR_ACTIVE_RMS = 60.0             # energía far mínima para considerar eco posible
_NLMS_MU = 0.5
_NLMS_EPS = 1e-6
_GEIGEL_THRESHOLD = 0.5            # near > 0.5 * max(far reciente) → doble habla


class EchoCanceller:
    """NLMS mono 8 kHz con estimación de retardo y guardia de doble-habla."""

    def __init__(self, taps: int | None = None):
        self.taps = int(taps or settings.VOICE_AEC_TAPS or 256)
        self._w = np.zeros(self.taps, dtype=np.float64)
        far_len = SAMPLE_RATE * _FAR_BUFFER_SECONDS
        self._far = np.zeros(far_len, dtype=np.float64)
        self._far_len = far_len
        self._far_written = 0          # total de muestras far recibidas
        self._near_read = 0            # total de muestras near procesadas
        self._delay = 0                # retardo estimado far→near (muestras)
        self._delay_locked = False
        self._last_xcorr_at = 0
        self._near_hist = np.zeros(_XCORR_WINDOW, dtype=np.float64)

    # ── far end (lo que el bot reproduce) ──

    def add_far(self, pcm: bytes) -> None:
        if not pcm:
            return
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        n = samples.size
        pos = self._far_written % self._far_len
        end = pos + n
        if end <= self._far_len:
            self._far[pos:end] = samples
        else:
            first = self._far_len - pos
            self._far[pos:] = samples[:first]
            self._far[: end - self._far_len] = samples[first:]
        self._far_written += n

    def _far_slice(self, start: int, length: int) -> np.ndarray:
        """Muestras far [start, start+length) en la línea de tiempo absoluta."""
        out = np.zeros(length, dtype=np.float64)
        lo = max(start, self._far_written - self._far_len)
        hi = min(start + length, self._far_written)
        if hi <= lo:
            return out
        idx = np.arange(lo, hi) % self._far_len
        out[lo - start : hi - start] = self._far[idx]
        return out

    def far_recently_active(self, window_ms: int = 400) -> bool:
        """True si el bot emitió audio con energía en la ventana reciente."""
        n = int(SAMPLE_RATE * window_ms / 1000)
        ref = self._far_slice(max(0, self._far_written - n), n)
        if ref.size == 0:
            return False
        return float(np.sqrt(np.mean(ref**2))) >= _FAR_ACTIVE_RMS

    # ── near end (lo que llega del usuario + eco) ──

    def process_near(self, pcm: bytes) -> bytes:
        """Devuelve el frame near con el eco estimado sustraído."""
        if not pcm:
            return pcm
        near = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        n = near.size
        frame_start = self._near_read
        self._near_read += n

        self._push_near_history(near)
        if self._near_read - self._last_xcorr_at >= _XCORR_REFRESH_SAMPLES:
            self._last_xcorr_at = self._near_read
            self._estimate_delay()

        # Referencia alineada: near[t] ↔ far[t - delay]. Se necesita el tramo
        # far que cubre el frame más la cola del filtro.
        ref_start = frame_start - self._delay - self.taps + 1
        ref = self._far_slice(ref_start, self.taps - 1 + n)

        if float(np.max(np.abs(ref))) < 1.0:
            return pcm  # bot en silencio en la ventana alineada: nada que cancelar

        # Doble-habla (Geigel): si el near supera la mitad del pico far
        # reciente, el usuario está hablando encima → filtrar sin adaptar.
        far_peak = float(np.max(np.abs(ref)))
        near_peak = float(np.max(np.abs(near)))
        adapt = near_peak <= _GEIGEL_THRESHOLD * max(far_peak, 1.0)

        residual = np.empty(n, dtype=np.float64)
        w = self._w
        for i in range(n):
            x = ref[i : i + self.taps][::-1]
            y = float(np.dot(w, x))
            e = near[i] - y
            residual[i] = e
            if adapt:
                norm = float(np.dot(x, x)) + _NLMS_EPS
                w += (_NLMS_MU * e / norm) * x

        out = np.clip(np.round(residual), -32768, 32767).astype(np.int16)
        return out.tobytes()

    # ── estimación de retardo ──

    def _push_near_history(self, near: np.ndarray) -> None:
        n = near.size
        if n >= _XCORR_WINDOW:
            self._near_hist = near[-_XCORR_WINDOW:].copy()
            return
        self._near_hist = np.roll(self._near_hist, -n)
        self._near_hist[-n:] = near

    def _estimate_delay(self) -> None:
        """Correlación cruzada near↔far sobre la última ventana (~1 s)."""
        near = self._near_hist
        if float(np.max(np.abs(near))) < 1.0:
            return
        near_end = self._near_read
        far_start = near_end - _XCORR_WINDOW - _MAX_DELAY_SAMPLES
        far = self._far_slice(far_start, _XCORR_WINDOW + _MAX_DELAY_SAMPLES)
        if float(np.max(np.abs(far))) < _FAR_ACTIVE_RMS:
            return

        # C[k] = Σ_i far[i+k]·near[i]  (correlación cruzada vía FFT). Con
        # far cubriendo [near_end - W - MAXD, near_end) y near la ventana
        # [near_end - W, near_end), el retardo es d = MAXD - argmax(C[0..MAXD]).
        size = 1
        while size < far.size + near.size:
            size <<= 1
        corr = np.fft.irfft(
            np.fft.rfft(far, size) * np.conj(np.fft.rfft(near, size)), size
        )
        valid = corr[: _MAX_DELAY_SAMPLES + 1]
        peak_idx = int(np.argmax(np.abs(valid)))
        peak = float(np.abs(valid[peak_idx]))
        mean = float(np.mean(np.abs(valid))) + 1e-9
        if peak / mean < _XCORR_MIN_PEAK_RATIO:
            return

        new_delay = _MAX_DELAY_SAMPLES - peak_idx
        if self._delay_locked and abs(new_delay - self._delay) <= _DELAY_JUMP_RESET:
            return
        if self._delay_locked:
            logger.info(
                "[aec] delay jump %d → %d samples — filter reset",
                self._delay,
                new_delay,
            )
            self._w[:] = 0.0
        else:
            logger.info("[aec] delay locked at %d samples", new_delay)
        self._delay = new_delay
        self._delay_locked = True
