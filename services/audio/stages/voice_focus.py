"""Post-filtro de voz objetivo — separar la voz del resto de la escena.

Por qué hace falta
------------------
Un supresor de ruido resuelve "voz contra ruido", no "voz contra música". La
familia DeepFilterNet (a la que pertenece el supresor de este pipeline) está
entrenada con ruido como adversario, y su propia documentación reconoce que no
sirve para reducir música: los instrumentos armónicos y las voces cantadas se
solapan con el habla en tiempo y en frecuencia, así que el modelo las deja pasar.
Medido en este proyecto: con música de fondo el supresor la atenúa 17 dB mientras
el usuario habla, y eso todavía deja un lecho musical perfectamente transcribible
que provoca inserciones en el reconocedor.

Un modelo de extracción de hablante objetivo resolvería esto, pero en una llamada
entrante no existe un audio de enrolamiento del usuario, y la literatura de 2026
que intenta obtenerlo del propio comienzo de la llamada concluye que **ninguno de
los modelos probados mejora la tasa de error frente a no hacer nada**. El único
modelo abierto de aislamiento sin enrolamiento (`weya-ai/hush`) se exporta sin
estado recurrente accesible, de modo que no puede ejecutarse trama a trama.

Qué explota esta etapa
----------------------
Lo que sí distingue a una voz humana del resto de la escena en un solo canal:

1. **Estructura armónica de un único tono.** Una voz tiene una frecuencia
   fundamental con sus armónicos. La música tiene varias fundamentales
   simultáneas (un acorde), y el ruido no tiene ninguna. Se estima el tono
   dominante de la trama y se atenúa la energía que no encaja en su rejilla de
   armónicos. El criterio se aplica solo por debajo de `harmonic_limit_hz` y solo
   cuando la trama está sonorizada: las consonantes sordas (s, f, ch) son
   deliberadamente inarmónicas y borrarlas costaría palabras.

2. **Modulación silábica.** El habla varía de nivel varias veces por segundo
   (4-8 Hz, el ritmo de las sílabas). Una nota sostenida, un ventilador o un
   motor mantienen su nivel. Por banda se compara la envolvente rápida con la
   lenta: poca variación relativa significa fuente sostenida, no voz.

Ambos criterios son ortogonales al supresor neuronal y baratos (una FFT por
trama, sin modelo). Es la arquitectura multi-etapa que usan los sistemas
ganadores de los retos de supresión: una etapa aprende, la siguiente corrige
sobre lo que la primera dejó.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from services.audio.dsp import EPSILON, SpectralStream, smooth
from services.audio.pipeline import BaseStage, FrameContext


class VoiceFocusStage(BaseStage):
    """Atenúa lo que no encaja con una voz: acordes, tonos sostenidos, zumbidos."""

    name = "voice_focus"

    def __init__(
        self,
        *,
        rate: int,
        frame_size: int,
        hop_size: int,
        f0_min: float,
        f0_max: float,
        voicing_threshold: float,
        harmonic_strength: float,
        harmonic_limit_hz: float,
        harmonic_width_hz: float,
        modulation_strength: float,
        modulation_fast_ms: float,
        modulation_slow_ms: float,
        modulation_target: float,
        floor: float,
        smoothing: float,
    ):
        self.rate = int(rate)
        self._stream = SpectralStream(frame_size, hop_size, channels=1)
        self._bins = frame_size // 2 + 1
        self._frame_size = int(frame_size)
        self._frequencies = np.fft.rfftfreq(frame_size, d=1.0 / rate).astype(np.float32)

        self._lag_min = max(2, int(rate / max(f0_max, 1.0)))
        self._lag_max = min(frame_size - 1, int(rate / max(f0_min, 1.0)))
        self.voicing_threshold = float(voicing_threshold)
        self.harmonic_strength = float(harmonic_strength)
        self.harmonic_width = float(harmonic_width_hz)
        self._harmonic_band = self._frequencies <= float(harmonic_limit_hz)

        hop_sec = hop_size / float(rate)
        self._fast_alpha = float(np.exp(-hop_sec / max(modulation_fast_ms / 1000.0, 1e-4)))
        self._slow_alpha = float(np.exp(-hop_sec / max(modulation_slow_ms / 1000.0, 1e-4)))
        self.modulation_strength = float(modulation_strength)
        self.modulation_target = float(modulation_target)

        self.floor = float(floor)
        self.smoothing = float(smoothing)

        self._fast: Optional[np.ndarray] = None
        self._slow: Optional[np.ndarray] = None
        self._gain_state: Optional[np.ndarray] = None
        self.frames_voiced = 0
        self.frames_total = 0
        self._f0_sum = 0.0
        self._f0_count = 0

    @property
    def latency_ms(self) -> float:
        return round(self._stream.latency_ms(self.rate), 1)

    def reset(self) -> None:
        self._stream.reset()
        self._fast = None
        self._slow = None
        self._gain_state = None

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block
        return self._stream.process([block], lambda spectra: self._filter(spectra, ctx))

    # ── criterios ──

    def _estimate_pitch(self, power: np.ndarray) -> tuple[float, float]:
        """Tono dominante y confianza de sonoridad, por autocorrelación espectral.

        La autocorrelación se obtiene de la densidad espectral de potencia con una
        transformada inversa: es el mismo cálculo que en el dominio del tiempo pero
        reutilizando la FFT que ya se hizo, sin coste adicional apreciable.
        """
        autocorrelation = np.fft.irfft(power, n=self._frame_size)
        energy = float(autocorrelation[0])
        if energy <= EPSILON:
            return 0.0, 0.0
        window = autocorrelation[self._lag_min : self._lag_max + 1]
        if window.size == 0:
            return 0.0, 0.0
        peak = int(np.argmax(window))
        confidence = float(window[peak] / energy)
        lag = self._lag_min + peak
        return self.rate / float(lag), max(0.0, confidence)

    def _harmonic_weight(self, f0: float) -> np.ndarray:
        """Peso por banda según su cercanía a un armónico de `f0`.

        La distancia se mide en unidades de f0: el resto de dividir la frecuencia
        entre f0 dice a qué distancia está del armónico más cercano, así que basta
        una operación vectorizada en vez de recorrer armónico por armónico.
        """
        if f0 <= 0.0:
            return np.ones(self._bins, dtype=np.float32)
        ratio = self._frequencies / f0
        distance = np.abs(ratio - np.round(ratio)) * f0
        weight = np.exp(-0.5 * (distance / max(self.harmonic_width, 1.0)) ** 2)
        # Fuera de la banda armónica el criterio no aplica (consonantes sordas y
        # banda alta): peso neutro.
        return np.where(self._harmonic_band, weight, 1.0).astype(np.float32)

    def _modulation_weight(self, magnitude: np.ndarray) -> np.ndarray:
        """Peso por banda según cuánto varía su envolvente (ritmo silábico)."""
        if self._fast is None or self._slow is None:
            self._fast = magnitude.copy()
            self._slow = magnitude.copy()
            return np.ones(self._bins, dtype=np.float32)
        self._fast = self._fast_alpha * self._fast + (1.0 - self._fast_alpha) * magnitude
        self._slow = self._slow_alpha * self._slow + (1.0 - self._slow_alpha) * magnitude
        depth = np.abs(self._fast - self._slow) / (self._slow + EPSILON)
        return np.clip(depth / max(self.modulation_target, 1e-6), 0.0, 1.0).astype(
            np.float32
        )

    # ── filtro ──

    def _filter(self, spectra: list[np.ndarray], ctx: FrameContext) -> np.ndarray:
        spectrum = spectra[0]
        magnitude = np.abs(spectrum).astype(np.float32)
        power = magnitude.astype(np.float64) ** 2

        self.frames_total += 1
        f0, voicing = self._estimate_pitch(power)
        voiced = voicing >= self.voicing_threshold
        if voiced:
            self.frames_voiced += 1
            self._f0_sum += f0
            self._f0_count += 1

        weight = np.ones(self._bins, dtype=np.float32)
        if voiced and self.harmonic_strength > 0.0:
            weight *= self._harmonic_weight(f0) ** self.harmonic_strength
        modulation = self._modulation_weight(magnitude)
        if self.modulation_strength > 0.0:
            weight *= modulation**self.modulation_strength

        gain = np.clip(weight, self.floor, 1.0)
        self._gain_state = smooth(self._gain_state, gain, self.smoothing)
        ctx.notes["voice_focus_f0"] = round(f0, 1)
        return spectrum * self._gain_state

    def stats(self) -> dict:
        return {
            "frames_voiced": self.frames_voiced,
            "voiced_ratio": round(
                self.frames_voiced / self.frames_total if self.frames_total else 0.0, 3
            ),
            "f0_mean_hz": round(self._f0_sum / self._f0_count, 1) if self._f0_count else 0.0,
        }
