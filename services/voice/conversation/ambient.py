"""Ambient Sound Manager — el sonido de una operadora trabajando.

Solo existe cuando hay una acción que lo justifique (una consulta realmente en
curso). Tiene que OÍRSE por teléfono: un lecho por debajo del umbral de la
banda estrecha de 8 kHz simplemente no llega. El nivel está calibrado para
quedar claramente audible pero muy por debajo de la voz — se percibe como
fondo de oficina, nunca como un efecto.

Genera PCM 16-bit mono; el runtime lo intercala en el mismo buffer de audio de
la respuesta. No toca el pipeline de captura ni el TTS.
"""

from __future__ import annotations

import math
import random
from array import array
from typing import Optional

# Pico de los transitorios sobre 32767. ~-21 dBFS: audible en un auricular
# telefónico, del orden de 15 dB por debajo de la voz sintetizada.
_PEAK = 0.09
# Lecho de sala continuo, muy por debajo de los transitorios (~-40 dBFS).
_FLOOR_RATIO = 0.11
_FADE_SEC = 0.03

# Perfiles por tipo de trabajo:
#   (transitorios/segundo, duración del transitorio, brillo, prob. de ráfaga)
# El brillo controla el filtro: alto = tecla seca, bajo = papel/roce.
_PROFILES: dict[str, tuple[float, float, float, float]] = {
    "typing": (9.0, 0.016, 0.85, 0.55),   # teclado: ráfagas de teclas
    "clicks": (3.2, 0.010, 0.95, 0.20),   # ratón/consulta: clics sueltos y secos
    "paper": (2.2, 0.055, 0.35, 0.30),    # papeles: roce más largo y opaco
}
_DEFAULT_PROFILE = "typing"

# Matiz de narración → textura de fondo que le corresponde.
KIND_TO_AMBIENT = {
    "address": "typing",
    "place": "typing",
    "geo_context": "clicks",
    "service": "paper",
    "generic": "clicks",
}


def ambient_for(kind: str) -> str:
    return KIND_TO_AMBIENT.get(kind or "generic", "clicks")


class AmbientSoundManager:
    """Sintetiza fondo de trabajo audible pero claramente subordinado a la voz."""

    def __init__(self, rng: Optional[random.Random] = None, sample_rate: int = 8000):
        self._rng = rng or random.Random()
        self.sample_rate = sample_rate

    def kinds(self) -> tuple[str, ...]:
        return tuple(_PROFILES)

    def peak_amplitude(self) -> float:
        return _PEAK

    def _transient(self, length: int, brightness: float) -> list[float]:
        """Un golpe corto: ataque instantáneo y caída exponencial.

        `brightness` mezcla ruido crudo (seco, tipo tecla) con ruido filtrado
        paso-bajo (opaco, tipo papel).
        """
        out: list[float] = []
        low = 0.0
        for j in range(length):
            white = self._rng.uniform(-1.0, 1.0)
            low = 0.72 * low + 0.28 * white
            sample = brightness * white + (1.0 - brightness) * low * 2.2
            envelope = math.exp(-5.0 * j / max(1, length))
            out.append(sample * envelope)
        return out

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

        per_sec, hit_sec, brightness, burst_p = _PROFILES.get(
            kind, _PROFILES[_DEFAULT_PROFILE]
        )
        peak = _PEAK * 32767.0
        buf = [0.0] * n

        # Lecho de sala: ruido tenue y filtrado, siempre presente.
        floor_amp = peak * _FLOOR_RATIO
        prev = 0.0
        for i in range(n):
            prev = 0.88 * prev + 0.12 * self._rng.uniform(-1.0, 1.0)
            buf[i] = prev * floor_amp

        # Transitorios. Las teclas humanas salen en ráfagas, no espaciadas
        # como un metrónomo: cada golpe puede arrastrar 1-3 más muy seguidos.
        hit_len = max(2, int(hit_sec * rate))
        events = max(1, int(per_sec * duration))
        placed = 0
        while placed < events:
            start = self._rng.randrange(0, n)
            run = 1
            while run < 4 and self._rng.random() < burst_p:
                run += 1
            for k in range(run):
                offset = start + int(k * hit_len * self._rng.uniform(1.6, 3.4))
                if offset >= n:
                    break
                gain = peak * self._rng.uniform(0.6, 1.0)
                for j, value in enumerate(self._transient(hit_len, brightness)):
                    idx = offset + j
                    if idx >= n:
                        break
                    buf[idx] += value * gain
                placed += 1

        # Entrada y salida suaves: nunca un chasquido al empezar o terminar.
        fade = min(int(_FADE_SEC * rate), n // 2)
        for j in range(fade):
            factor = j / fade
            buf[j] *= factor
            buf[n - 1 - j] *= factor

        samples = array("h", bytes(2 * n))
        for i, value in enumerate(buf):
            samples[i] = max(-32768, min(32767, int(value)))
        return samples.tobytes()
