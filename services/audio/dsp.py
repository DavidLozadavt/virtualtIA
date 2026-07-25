"""Núcleo DSP compartido — STFT con estado, alineación temporal y suavizados.

Varias etapas (supresión de eco residual, dereverberación, puerta espectral)
necesitan exactamente lo mismo: analizar la señal en el dominio frecuencial
trama a trama, aplicar una ganancia y reconstruir sin discontinuidades. Ese
andamiaje vive aquí una sola vez.

`SpectralStream` implementa WOLA (weighted overlap-add) con ventana raíz de
Hann: análisis y síntesis usan la misma ventana y, con salto de media trama, la
suma de sus cuadrados es constante, así que una transformación neutra devuelve
la señal original (verificado en tests). Admite N canales sincronizados para que
una etapa pueda decidir la ganancia del canal principal mirando además la
referencia de eco alineada.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np

EPSILON = 1e-12


def hann_sqrt_window(size: int) -> np.ndarray:
    """Ventana raíz de Hann periódica: análisis y síntesis idénticas en WOLA."""
    n = np.arange(size, dtype=np.float64)
    hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * n / size)
    return np.sqrt(hann).astype(np.float32)


def vorbis_window(size: int) -> np.ndarray:
    """Ventana Vorbis (Princen-Bradley): w²[n] + w²[n+N/2] = 1.

    Es la ventana con la que se exportaron los modelos de supresión de ruido
    usados aquí, así que el análisis debe usar exactamente esta y no una Hann.
    """
    half = size / 2.0
    indices = np.arange(size, dtype=np.float64)
    inner = np.sin(0.5 * np.pi * (indices + 0.5) / half)
    return np.sin(0.5 * np.pi * inner * inner).astype(np.float32)


SpectralTransform = Callable[[list[np.ndarray]], np.ndarray]


class SpectralStream:
    """Analiza/reconstruye un flujo continuo por STFT, con memoria entre bloques.

    Retardo estructural: `frame_size - hop_size` muestras (con 256/128 a 8 kHz,
    16 ms). Es el precio de decidir ganancias con resolución frecuencial.
    """

    def __init__(self, frame_size: int, hop_size: int, channels: int = 1):
        if frame_size <= 0 or hop_size <= 0 or frame_size % hop_size != 0:
            raise ValueError("hop_size debe dividir exactamente a frame_size")
        self.frame_size = int(frame_size)
        self.hop_size = int(hop_size)
        self.channels = int(channels)
        self._window = hann_sqrt_window(self.frame_size)
        overlap = self.frame_size // self.hop_size
        squared = self._window.astype(np.float64) ** 2
        accumulated = np.zeros(self.frame_size, dtype=np.float64)
        for shift in range(overlap):
            accumulated += np.roll(squared, shift * self.hop_size)
        self._norm = float(np.mean(accumulated)) or 1.0
        self._priming = self.frame_size - self.hop_size
        self._inputs: list[np.ndarray] = []
        self._ola = np.zeros(self.frame_size, dtype=np.float32)
        self.reset()

    @property
    def delay_samples(self) -> int:
        """Retardo entrada→salida en muestras."""
        return self.frame_size - self.hop_size

    def latency_ms(self, rate: int) -> float:
        return self.delay_samples / float(rate) * 1000.0

    def reset(self) -> None:
        # El primer bloque llega precedido de silencio para que la primera trama
        # emitida ya tenga solape completo (si no, arrancaría con media ventana).
        self._inputs = [
            np.zeros(self._priming, dtype=np.float32) for _ in range(self.channels)
        ]
        self._ola = np.zeros(self.frame_size, dtype=np.float32)

    def process(
        self, blocks: Sequence[np.ndarray], transform: SpectralTransform
    ) -> np.ndarray:
        """Empuja un bloque por canal y devuelve la señal reconstruida disponible.

        `transform` recibe los espectros (rFFT de la trama enventanada) de todos
        los canales y devuelve el espectro ya modificado del canal principal.
        Un canal más corto que el principal se rellena con ceros: una referencia
        agotada significa "no hay eco que cancelar", no un error.
        """
        if len(blocks) != self.channels:
            raise ValueError(f"se esperaban {self.channels} canales")
        main_size = blocks[0].size
        if main_size == 0:
            return np.zeros(0, dtype=np.float32)

        for idx in range(self.channels):
            chan = blocks[idx]
            if chan.size < main_size:
                padded = np.zeros(main_size, dtype=np.float32)
                if chan.size:
                    padded[: chan.size] = chan
                chan = padded
            elif chan.size > main_size:
                chan = chan[:main_size]
            self._inputs[idx] = np.concatenate(
                (self._inputs[idx], chan.astype(np.float32, copy=False))
            )

        produced: list[np.ndarray] = []
        while self._inputs[0].size >= self.frame_size:
            spectra = [
                np.fft.rfft(self._inputs[idx][: self.frame_size] * self._window)
                for idx in range(self.channels)
            ]
            modified = transform(spectra)
            frame = np.fft.irfft(modified, n=self.frame_size).astype(np.float32)
            self._ola += frame * self._window
            produced.append(self._ola[: self.hop_size] / self._norm)
            self._ola = np.roll(self._ola, -self.hop_size)
            self._ola[-self.hop_size :] = 0.0
            for idx in range(self.channels):
                self._inputs[idx] = self._inputs[idx][self.hop_size :]

        return (
            np.concatenate(produced) if produced else np.zeros(0, dtype=np.float32)
        )


def gcc_phat(
    capture: np.ndarray, reference: np.ndarray, *, max_lag: Optional[int] = None
) -> tuple[int, float]:
    """Retardo de `capture` respecto a `reference` por GCC-PHAT.

    GCC-PHAT (Generalized Cross Correlation with PHAse Transform) normaliza la
    magnitud del espectro cruzado y correla solo la fase: encuentra el retardo
    aunque el eco vuelva fuertemente filtrado y atenuado por el altavoz y por el
    canal telefónico — que es exactamente el caso del manos libres. La
    correlación cruzada simple, en cambio, se sesga hacia las bandas con más
    energía y falla con eco débil.

    Devuelve `(retardo_en_muestras, confianza)`. La confianza en [0, 1] compara
    el pico con la energía media de la correlación: por debajo del umbral de
    quien llama, no hay alineación creíble y no debe usarse.
    """
    if capture.size == 0 or reference.size == 0:
        return 0, 0.0
    size = 1
    while size < capture.size + reference.size:
        size <<= 1
    cross = np.fft.rfft(capture, n=size) * np.conj(np.fft.rfft(reference, n=size))
    cross /= np.maximum(np.abs(cross), EPSILON)
    correlation = np.fft.irfft(cross, n=size)
    limit = int(min(max_lag if max_lag is not None else size // 2, size // 2 - 1))
    if limit <= 0:
        return 0, 0.0
    window = np.concatenate((correlation[-limit:], correlation[: limit + 1]))
    peak_index = int(np.argmax(window))
    mean_abs = float(np.mean(np.abs(window))) or EPSILON
    ratio = float(abs(window[peak_index]) / mean_abs)
    lag = peak_index - limit
    return int(lag), float(np.clip(ratio / 8.0, 0.0, 1.0))


def smooth(previous: Optional[np.ndarray], current: np.ndarray, alpha: float) -> np.ndarray:
    """Suavizado exponencial entre tramas (alpha = peso del pasado)."""
    if previous is None or previous.shape != current.shape:
        return current.astype(np.float32, copy=True)
    return (alpha * previous + (1.0 - alpha) * current).astype(np.float32, copy=False)
