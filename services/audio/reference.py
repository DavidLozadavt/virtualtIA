"""Referencia far-end — el audio que Lyra reprodujo, alineado en el tiempo.

Para cancelar eco hace falta la señal de referencia (lo que sonó por el altavoz
del usuario) alineada con la señal capturada. En esta arquitectura la referencia
existe y es exacta en contenido: el PCM del TTS lo genera el propio proceso
Python (`services/voice/tts_stream.py`). Lo que NO se conoce con precisión es su
posición temporal, porque el playback ocurre dentro de FreeSWITCH vía
`uuid_broadcast` y viaja por la red del operador: el retardo real de ida y
vuelta va de decenas de ms a más de medio segundo en manos libres.

Por eso este módulo guarda la referencia como una línea de tiempo continua
anclada al instante en que se ordenó el playback, y expone la ventana de
búsqueda necesaria para que el estimador de retardo (`echo.py`, GCC-PHAT) fije
el desfase real. El anclaje es solo la hipótesis inicial; la alineación fina la
resuelve la correlación, no el reloj.

El buffer se acota en segundos (`window_sec`): solo interesa el pasado reciente,
y una llamada larga no debe crecer en memoria.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FarEndReference:
    """Ventana deslizante del audio reproducido, indexada por tiempo monotónico.

    Seguro para uso concurrente: el playback publica desde la tarea de TTS
    mientras la captura consume desde el loop de audio.
    """

    sample_rate: int
    window_sec: float
    _samples: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32), init=False
    )
    _start_index: int = field(default=0, init=False)  # índice absoluto del primer sample
    _anchor_time: float | None = field(default=None, init=False)
    _anchor_index: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _active: bool = field(default=False, init=False)

    @property
    def capacity(self) -> int:
        return max(1, int(self.sample_rate * self.window_sec))

    @property
    def active(self) -> bool:
        """True mientras haya playback vigente publicado (hay eco posible)."""
        return self._active

    def start(self, at_time: float) -> None:
        """Marca el inicio de un playback: ancla la línea de tiempo en `at_time`."""
        with self._lock:
            self._anchor_time = at_time
            self._anchor_index = self._start_index + self._samples.size
            self._active = True

    def publish(self, samples: np.ndarray) -> None:
        """Añade audio reproducido (float32, `sample_rate`) al final de la ventana."""
        if samples.size == 0:
            return
        with self._lock:
            block = samples.astype(np.float32, copy=False)
            self._samples = (
                block.copy()
                if self._samples.size == 0
                else np.concatenate((self._samples, block))
            )
            overflow = self._samples.size - self.capacity
            if overflow > 0:
                self._samples = self._samples[overflow:]
                self._start_index += overflow

    def stop(self) -> None:
        """Marca el fin del playback. El contenido sigue disponible para la cola de eco."""
        with self._lock:
            self._active = False

    def truncate(self, at_time: float) -> None:
        """Descarta la referencia posterior a `at_time` (playback interrumpido).

        En un barge-in el audio de Lyra se corta a mitad: lo que quedaba en el
        buffer nunca sonó y por tanto nunca va a volver como eco. Dejarlo haría
        que el filtro adaptativo intentara cancelar algo inexistente y se
        desajustara justo cuando el usuario está hablando.
        """
        with self._lock:
            if self._anchor_time is None or self._samples.size == 0:
                self._active = False
                return
            elapsed = max(0.0, at_time - self._anchor_time)
            played_end = self._anchor_index + int(elapsed * self.sample_rate)
            keep = played_end - self._start_index
            if 0 <= keep < self._samples.size:
                self._samples = self._samples[:keep]
            self._active = False

    def clear(self) -> None:
        with self._lock:
            self._samples = np.zeros(0, dtype=np.float32)
            self._start_index = 0
            self._anchor_time = None
            self._anchor_index = 0
            self._active = False

    def has_content(self) -> bool:
        with self._lock:
            return bool(self._samples.size)

    def aligned(self, at_time: float, length: int, lag: int) -> np.ndarray:
        """`length` muestras de referencia alineadas con la trama capturada.

        `lag` es el retardo estimado de la captura respecto a la referencia: con
        lag>0 el eco que llega ahora corresponde a audio reproducido `lag`
        muestras antes. Si la ventana no cubre ese pasado (playback recién
        iniciado) se rellena con ceros por delante — equivale a "todavía no había
        nada reproducido", que es la verdad.
        """
        with self._lock:
            if self._samples.size == 0 or self._anchor_time is None or length <= 0:
                return np.zeros(max(0, length), dtype=np.float32)
            elapsed = max(0.0, at_time - self._anchor_time)
            nominal_end = self._anchor_index + int(elapsed * self.sample_rate)
            end = nominal_end - lag
            begin = end - length
            available_lo = self._start_index
            available_hi = self._start_index + self._samples.size
            out = np.zeros(length, dtype=np.float32)
            take_lo = max(begin, available_lo)
            take_hi = min(end, available_hi)
            if take_hi > take_lo:
                chunk = self._samples[take_lo - available_lo : take_hi - available_lo]
                out[take_lo - begin : take_lo - begin + chunk.size] = chunk
            return out

    def right_edge_gap(self, at_time: float) -> int:
        """Muestras que la línea de tiempo lleva de adelanto sobre lo publicado.

        Positivo significa que el reloj ya pasó del final del audio reproducido:
        el borde derecho de la ventana no está respaldado por audio real. Medir
        un retardo contra una ventana así devuelve un valor falso, así que quien
        alinea debe abstenerse.
        """
        with self._lock:
            if self._anchor_time is None:
                return 0
            elapsed = max(0.0, at_time - self._anchor_time)
            nominal_end = self._anchor_index + int(elapsed * self.sample_rate)
            return int(nominal_end - (self._start_index + self._samples.size))

    def window(self, at_time: float, length: int, search: int) -> tuple[np.ndarray, int]:
        """Ventana de referencia candidata para una trama capturada en `at_time`.

        Devuelve `(muestras, offset_base)` donde `muestras` cubre
        `length + search` muestras terminando en la posición nominal de la
        trama, y `offset_base` es el índice absoluto de su primera muestra.
        La ventana extra (`search`) es el rango donde el estimador de retardo
        puede buscar el desfase real; sin ella un eco más tardío que la
        hipótesis del reloj quedaría fuera del alcance del filtro adaptativo.
        """
        with self._lock:
            if self._samples.size == 0 or self._anchor_time is None:
                return np.zeros(0, dtype=np.float32), 0
            elapsed = max(0.0, at_time - self._anchor_time)
            nominal_end = self._anchor_index + int(elapsed * self.sample_rate)
            want = length + max(0, search)
            end = min(nominal_end, self._start_index + self._samples.size)
            begin = max(self._start_index, end - want)
            if end - begin <= 0:
                return np.zeros(0, dtype=np.float32), begin
            lo = begin - self._start_index
            hi = end - self._start_index
            return self._samples[lo:hi].copy(), begin
