"""Control de eco — que Lyra no se escuche a sí misma en manos libres.

El problema real
----------------
Cuando el usuario tiene el teléfono en altavoz, la voz de Lyra sale por el
altavoz, rebota en la sala y vuelve por el micrófono como si fuera habla del
usuario. Para el reconocedor es voz humana perfectamente válida — ningún
supresor de ruido ni detector de voz lo va a rechazar, porque *es* voz. La única
defensa correcta es aprovechar que conocemos exactamente la señal que se
reprodujo y restarla.

Tres etapas, en el orden en que se necesitan
--------------------------------------------
1. **Alineación** (`gcc_phat`): el retardo de ida y vuelta de una llamada SIP con
   altavoz va de decenas de ms a más de medio segundo, y no es constante. Sin
   alinear primero, un filtro adaptativo no converge nunca: estaría intentando
   restar audio que todavía no ha llegado. Se re-estima periódicamente y solo se
   acepta una alineación nueva si la confianza del pico supera el umbral.

2. **Filtro lineal adaptativo multi-retardo** (MDF, *multidelay block frequency
   domain adaptive filter*): un filtro complejo por banda y por partición
   temporal, adaptado por NLMS en el dominio frecuencial. Es la misma familia de
   algoritmo que el filtro lineal de AEC3 de WebRTC, y aquí cuesta unas pocas
   centenas de multiplicaciones complejas por trama de 16 ms. Cubre una cola de
   eco configurable (por defecto ~128 ms), suficiente para la reverberación de
   una sala pequeña más el retardo residual de alineación.

3. **Supresión de eco residual**: el filtro lineal nunca cancela del todo,
   porque el altavoz del teléfono es no lineal (satura y distorsiona) y eso no
   es modelable con un filtro lineal. Lo que queda se atenúa con una ganancia
   tipo Wiener por banda, calculada sobre la estimación de eco del propio filtro
   y una fuga (*coupling*) aprendida por banda.

Doble habla
-----------
Si el usuario habla mientras suena el eco, adaptar el filtro con esa señal lo
destruye (divergencia): el filtro intentaría explicar la voz del usuario como si
fuera eco. Se detecta comparando la coherencia entre lo capturado y el eco
estimado; en doble habla el filtro **se congela pero sigue filtrando**, que es
el comportamiento correcto.

La etapa además publica `ctx.echo_detected`, que la puerta de voz usa para
cerrarse cuando la trama está dominada por eco: defensa en profundidad, porque
un residual con timbre humano pasaría cualquier detector de voz.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from services.audio.dsp import EPSILON, SpectralStream, gcc_phat, smooth
from services.audio.frames import rms
from services.audio.pipeline import BaseStage, FrameContext
from services.audio.reference import FarEndReference

logger = logging.getLogger("lyra.audio.echo")


class EchoControlStage(BaseStage):
    """Cancelación lineal + supresión de eco residual con referencia del TTS."""

    name = "echo_control"

    def __init__(
        self,
        reference: FarEndReference,
        *,
        rate: int,
        frame_size: int,
        hop_size: int,
        tail_ms: float,
        step_size: float,
        search_ms: float,
        realign_ms: float,
        align_confidence: float,
        residual_strength: float,
        residual_floor: float,
        detect_margin_db: float,
        tail_hold_ms: float,
        align_min_dbfs: float,
    ):
        self.rate = int(rate)
        self._align_min_level = float(10.0 ** (float(align_min_dbfs) / 20.0))
        self.reference = reference
        self.step_size = float(step_size)
        self.residual_strength = float(residual_strength)
        self.residual_floor = float(residual_floor)
        self.detect_margin = 10.0 ** (float(detect_margin_db) / 10.0)
        self.align_confidence = float(align_confidence)

        self._stream = SpectralStream(frame_size, hop_size, channels=2)
        self._bins = frame_size // 2 + 1
        self._hop = int(hop_size)
        self._partitions = max(1, round(tail_ms / (hop_size / self.rate * 1000.0)))
        self._search = int(search_ms / 1000.0 * self.rate)
        self._realign_samples = int(realign_ms / 1000.0 * self.rate)
        self._tail_hold_samples = int(tail_hold_ms / 1000.0 * self.rate)
        self._align_window = max(self._search * 2, self.rate // 2)
        # Historia mínima para intentar una alineación creíble (~350 ms): menos
        # que eso y GCC-PHAT no tiene material suficiente, más y se retrasa la
        # primera cancelación sin necesidad.
        self._min_align_history = max(self.rate // 3, self._search // 4)
        self._pending_realign = True

        self._weights = np.zeros((self._partitions, self._bins), dtype=np.complex128)
        self._ref_history = np.zeros(
            (self._partitions, self._bins), dtype=np.complex128
        )
        self._coupling: Optional[np.ndarray] = None
        self._gain_state: Optional[np.ndarray] = None
        self._capture_history = np.zeros(0, dtype=np.float32)
        self._lag = 0
        self._lag_confidence = 0.0
        self._since_realign = 0
        self._pending_realign = True
        self._since_playback = self._tail_hold_samples + 1
        self.frames_cancelled = 0
        self.frames_echo_detected = 0
        self.last_erle_db = 0.0
        self._erle_sum = 0.0
        self._erle_count = 0

    @property
    def latency_ms(self) -> float:
        return round(self._stream.latency_ms(self.rate), 1)

    def reset(self) -> None:
        self._stream.reset()
        self._weights[:] = 0.0
        self._ref_history[:] = 0.0
        self._coupling = None
        self._gain_state = None
        self._capture_history = np.zeros(0, dtype=np.float32)
        self._lag = 0
        self._lag_confidence = 0.0
        self._since_realign = 0
        self._pending_realign = True
        self._since_playback = self._tail_hold_samples + 1

    # ── alineación ──

    def _track_history(self, block: np.ndarray) -> None:
        self._capture_history = np.concatenate((self._capture_history, block))
        if self._capture_history.size > self._align_window:
            self._capture_history = self._capture_history[-self._align_window :]

    def _maybe_realign(self, timestamp: float, block_size: int) -> None:
        self._since_realign += block_size
        # Al empezar un playback se intenta alinear cuanto antes: cada trama sin
        # alineación es una trama sin cancelación, y son justo las primeras — el
        # momento más vulnerable de la llamada, cuando el filtro aún no ha
        # convergido y el usuario podría estar oyendo a Lyra por el altavoz.
        due = self._pending_realign or self._since_realign >= self._realign_samples
        if not due:
            return
        if self._capture_history.size < self._min_align_history:
            return
        self._since_realign = 0
        # Si el reloj ya rebasó el audio publicado (playback terminado o pausado),
        # la ventana de referencia está incompleta por la derecha y cualquier
        # medición saldría desplazada. Se conserva la alineación anterior.
        if self.reference.right_edge_gap(timestamp) > self._hop:
            return
        candidate, base = self.reference.window(
            timestamp, self._capture_history.size, self._search
        )
        if candidate.size < self._capture_history.size // 2:
            return
        # Sin energía real en ambos lados no hay nada que alinear. Es una
        # salvaguarda imprescindible: GCC-PHAT normaliza la magnitud del espectro
        # cruzado, así que sobre silencio amplifica ruido numérico y devuelve un
        # pico "de alta confianza" en un retardo arbitrario. Aceptarlo desajusta
        # el filtro entero justo al principio del playback (el TTS suele empezar
        # con unas décimas de silencio).
        if (
            rms(self._capture_history) < self._align_min_level
            or rms(candidate) < self._align_min_level
        ):
            return
        self._pending_realign = False
        lag, confidence = gcc_phat(
            self._capture_history, candidate, max_lag=self._search
        )
        if confidence < self.align_confidence:
            return
        # Ambas ventanas terminan en el mismo instante nominal, pero la de
        # referencia empieza `offset` muestras antes para poder buscar hacia el
        # pasado. Un desplazamiento `lag` medido entre los dos arreglos equivale
        # entonces a un retardo real de `lag + offset` muestras.
        offset = candidate.size - self._capture_history.size
        new_lag = max(0, lag + offset)
        if new_lag != self._lag:
            logger.debug(
                "[audio] realineación de eco lag=%d→%d conf=%.2f (base=%d)",
                self._lag,
                new_lag,
                confidence,
                base,
            )
            # Un salto de alineación invalida todo lo aprendido para el retardo
            # anterior: los pesos restarían en el sitio errado y el acoplamiento
            # por banda quedó medido contra una referencia desalineada. El
            # seguimiento de mínimos del acoplamiento no se recupera solo (baja
            # rápido y sube muy despacio, a propósito), así que hay que borrarlo.
            self._weights[:] = 0.0
            self._coupling = None
            self._gain_state = None
        self._lag = new_lag
        self._lag_confidence = confidence

    # ── proceso ──

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block

        playing = ctx.playback_active or self.reference.active
        if playing:
            if self._since_playback > self._tail_hold_samples:
                # Playback nuevo: el camino de eco pudo cambiar (el usuario movió
                # el teléfono, activó el altavoz) — hay que volver a alinear.
                self._pending_realign = True
            self._since_playback = 0
        else:
            self._since_playback += block.size

        # Sin playback reciente no hay eco posible: el audio pasa intacto y no se
        # adapta nada (adaptar sobre voz limpia solo destruiría el filtro).
        if (
            self._since_playback > self._tail_hold_samples
            or not self.reference.has_content()
        ):
            self._track_history(block)
            return block

        self._track_history(block)
        # Re-alinear solo mientras hay playback vigente. Terminado el audio de
        # Lyra, la ventana de referencia se queda a medias y una medición sobre
        # ella devolvería un retardo falso — justo cuando el retardo bueno ya
        # está aprendido y el camino de eco no ha cambiado. En la cola posterior
        # se conserva la última alineación válida.
        if playing:
            self._maybe_realign(ctx.timestamp, block.size)

        aligned = self.reference.aligned(ctx.timestamp, block.size, self._lag)
        return self._stream.process(
            [block, aligned], lambda spectra: self._filter_frame(spectra, ctx)
        )

    def _filter_frame(self, spectra: list[np.ndarray], ctx: FrameContext) -> np.ndarray:
        captured = spectra[0]
        far_end = spectra[1]

        # Historial de referencia: la trama nueva entra en la posición 0.
        self._ref_history = np.roll(self._ref_history, 1, axis=0)
        self._ref_history[0] = far_end

        estimate = np.sum(self._weights * self._ref_history, axis=0)
        residual = captured - estimate

        captured_magnitude = np.abs(captured)
        reference_magnitude = np.abs(far_end)
        residual_magnitude = np.abs(residual)
        captured_power = float(np.sum(captured_magnitude**2))
        estimate_power = float(np.sum(np.abs(estimate) ** 2))
        reference_power = float(np.sum(reference_magnitude**2))
        residual_power = float(np.sum(residual_magnitude**2))

        # ── detección de doble habla ──
        # Se compara la captura con el eco estimado por el filtro: si están
        # correlacionados, lo que entra es eco; si no, hay voz cercana. Mientras
        # el filtro no ha aprendido nada (estimación ~0) la comparación no
        # significa nada, y tratarla como doble habla dejaría el filtro
        # bloqueado para siempre sin poder converger.
        if estimate_power <= EPSILON:
            near_end_speech = False
        else:
            coherence = float(np.abs(np.vdot(estimate, captured))) / (
                float(np.sqrt(estimate_power * captured_power)) + EPSILON
            )
            near_end_speech = coherence < 0.5 and residual_power > estimate_power

        if reference_power > EPSILON and not near_end_speech:
            norm = np.sum(np.abs(self._ref_history) ** 2, axis=0) + EPSILON
            self._weights += (
                self.step_size
                * (residual / norm)[np.newaxis, :]
                * np.conj(self._ref_history)
            )
            self.frames_cancelled += 1

        # ── acoplamiento referencia → micrófono, por banda ──
        # Se estima directamente desde la referencia alineada (no desde el
        # estimado del filtro), así la supresión funciona desde la primera trama
        # y no depende de que el filtro lineal haya convergido ni de que la
        # alineación sea exacta al nivel de muestra: las magnitudes son mucho más
        # tolerantes al desfase que la fase.
        # El seguimiento es de mínimos (baja rápido, sube despacio): así el
        # acoplamiento converge a la ganancia real del camino de eco en vez de
        # inflarse cuando el usuario habla encima.
        if reference_power > EPSILON and captured_power > EPSILON:
            ratio = np.minimum(
                captured_magnitude / (reference_magnitude + EPSILON), 4.0
            ).astype(np.float32)
            if self._coupling is None:
                self._coupling = ratio
            else:
                falling = ratio < self._coupling
                self._coupling = np.where(
                    falling,
                    0.7 * self._coupling + 0.3 * ratio,
                    0.995 * self._coupling + 0.005 * ratio,
                ).astype(np.float32)
        coupling = (
            self._coupling
            if self._coupling is not None
            else np.zeros(self._bins, dtype=np.float32)
        )

        echo_magnitude = coupling * reference_magnitude
        gain = (residual_magnitude - self.residual_strength * echo_magnitude) / (
            residual_magnitude + EPSILON
        )
        gain = np.clip(gain, self.residual_floor, 1.0)
        # Suavizado temporal: una ganancia que salta entre tramas suena a
        # borboteo y el reconocedor lo interpreta como fonemas inexistentes.
        self._gain_state = smooth(self._gain_state, gain.astype(np.float32), 0.6)
        gain = self._gain_state

        output_power = float(np.sum((residual_magnitude * gain) ** 2))
        echo_power = float(np.sum(echo_magnitude**2))
        # ¿La trama está dominada por eco? Se compara el eco estimado con lo que
        # queda de campo cercano *antes* de suprimir. Compararlo con la salida ya
        # suprimida sería circular: cuanto mejor cancela la etapa, menos eco
        # detectaría, y la puerta se abriría justo cuando el eco es fuerte.
        # Se toma la mejor estimación disponible del eco presente: la del filtro
        # lineal (que sí estima el eco real) o la del acoplamiento espectral (que
        # por diseño es una cota inferior, porque sigue mínimos). Con el filtro
        # convergido la primera domina; en los primeros instantes, la segunda.
        echo_present = max(estimate_power, echo_power)
        near_end_estimate = max(captured_power - echo_present, EPSILON)
        if echo_present > self.detect_margin * near_end_estimate:
            ctx.echo_detected = True
            self.frames_echo_detected += 1

        if captured_power > EPSILON and output_power > 0.0:
            # ERLE promediado, no instantáneo: el valor de una sola trama no dice
            # nada sobre cómo está cancelando la etapa a lo largo de la llamada.
            instant = 10.0 * float(np.log10(captured_power / output_power))
            self._erle_sum += instant
            self._erle_count += 1
            self.last_erle_db = round(self._erle_sum / self._erle_count, 1)

        return residual * gain

    def stats(self) -> dict:
        return {
            "lag_samples": self._lag,
            "lag_confidence": round(self._lag_confidence, 3),
            "frames_adapted": self.frames_cancelled,
            "frames_echo_detected": self.frames_echo_detected,
            "erle_db": self.last_erle_db,
        }
