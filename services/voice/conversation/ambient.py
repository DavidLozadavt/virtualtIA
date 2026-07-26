"""Ambient Sound Manager — fondo contextual extremadamente discreto.

Solo existe cuando hay una acción que lo justifique (una consulta realmente en
curso). Su función es reforzar que la operadora está trabajando, no llamar la
atención: el pico se mantiene en torno al 1 % de la escala completa, muy por
debajo de la voz, con entrada y salida suavizadas para que no se perciba dónde
empieza ni dónde termina.

Genera PCM 16-bit mono; el runtime lo intercala en el mismo buffer de audio de
la respuesta. No toca el pipeline de captura ni el TTS.
"""

from __future__ import annotations

import math
import random
from array import array
from typing import Optional

# Pico absoluto del fondo, sobre 32767. Deliberadamente minúsculo.
_PEAK = 0.012
_FLOOR_RATIO = 0.22      # nivel del lecho de sala frente al pico
_FADE_SEC = 0.02

# Perfiles: cuántos transitorios por segundo y qué tan secos son.
_PROFILES: dict[str, tuple[float, float]] = {
    # kind: (transitorios por segundo, duración del transitorio en segundos)
    "search": (7.0, 0.012),   # teclado suave
    "lookup": (4.5, 0.018),   # clics espaciados de consulta
    "note": (2.5, 0.030),     # papel / anotación
}
_DEFAULT_PROFILE = "search"


class AmbientSoundManager:
    """Sintetiza lechos de sonido ambiente muy por debajo del nivel de voz."""

    def __init__(self, rng: Optional[random.Random] = None, sample_rate: int = 8000):
        self._rng = rng or random.Random()
        self.sample_rate = sample_rate

    def kinds(self) -> tuple[str, ...]:
        return tuple(_PROFILES)

    def peak_amplitude(self) -> float:
        return _PEAK

    def bed(
        self,
        kind: str,
        duration: float,
        *,
        sample_rate: Optional[int] = None,
    ) -> bytes:
        """PCM del fondo para `duration` segundos. Silencio si no aplica."""
        rate = sample_rate or self.sample_rate
        n = int(max(0.0, duration) * rate)
        if n <= 0:
            return b""

        rate_per_sec, click_sec = _PROFILES.get(kind, _PROFILES[_DEFAULT_PROFILE])
        peak = _PEAK * 32767.0
        samples = array("h", bytes(2 * n))

        # Lecho de sala: ruido muy tenue y filtrado (media móvil de un polo).
        floor_amp = peak * _FLOOR_RATIO
        prev = 0.0
        for i in range(n):
            white = self._rng.uniform(-1.0, 1.0)
            prev = 0.85 * prev + 0.15 * white
            samples[i] = int(prev * floor_amp)

        # Transitorios: pulsos cortísimos con caída exponencial.
        click_len = max(1, int(click_sec * rate))
        expected = max(0, int(rate_per_sec * duration))
        for _ in range(expected):
            start = self._rng.randrange(0, n)
            gain = peak * self._rng.uniform(0.55, 1.0)
            for j in range(click_len):
                idx = start + j
                if idx >= n:
                    break
                env = math.exp(-4.0 * j / click_len)
                value = samples[idx] + int(self._rng.uniform(-1.0, 1.0) * gain * env)
                samples[idx] = max(-32768, min(32767, value))

        # Entrada y salida suaves: nunca un chasquido al empezar o terminar.
        fade = min(int(_FADE_SEC * rate), n // 2)
        for j in range(fade):
            factor = j / fade
            samples[j] = int(samples[j] * factor)
            samples[n - 1 - j] = int(samples[n - 1 - j] * factor)

        return samples.tobytes()
