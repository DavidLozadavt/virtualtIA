"""Anclaje al hablante objetivo — quedarse con una voz y atenuar las demás.

El problema que ninguna etapa anterior resuelve
-----------------------------------------------
La supresión de ruido separa voz de ruido, y lo hace bien: medido en este
proyecto, la música se atenúa 38 dB y el ruido continuo 31 dB. Pero contra voces
humanas de fondo el mismo pipeline solo consigue 10 dB con un televisor
encendido y 3 dB en habla simultánea, porque **el televisor emite voz** y todo lo
que el pipeline sabe medir (¿es voz?, ¿es armónico?, ¿está modulado?) responde
que sí, con razón. El criterio de nivel que había —"lo que suene mucho más bajo
que el dominante es fondo"— falla en cuanto la otra persona está cerca o el
televisor está alto.

Con esta etapa el televisor pasa de 10.2 a 21.5 dB de rechazo sin coste medible
para la voz del usuario (−6.1 dB antes y después). El habla simultánea cercana
sigue sin resolverse y no se resuelve así: ver el final de este encabezado.

Lo que separa de verdad a la persona que llama del resto no es el volumen ni el
timbre genérico: es **su identidad**. Esta etapa la aprende sola durante la
propia llamada y la usa para decidir.

Cómo funciona
-------------
1. **Enrolamiento sin enrolamiento.** En una llamada entrante nunca se oyó antes
   a esa persona, así que el patrón se construye con los primeros segundos de voz
   que cumplen a la vez tres condiciones: nivel de hablante dominante, rango
   dinámico de campo cercano (una voz próxima tiene valles profundos entre
   sílabas; una lejana los tiene rellenos por la reverberación de la sala) y
   ausencia de reproducción de Lyra. Hasta que ese patrón existe, la etapa **no
   atenúa nada**: equivocarse al principio cuesta las primeras palabras, que es
   el error más caro del sistema.
2. **Seguimiento continuo, con dos ventanas distintas.** Aquí está el detalle que
   decide si la etapa sirve de algo. Comparar dos fragmentos cortos entre sí no
   funciona: medido, el margen entre "misma persona" y "otra persona" con
   ventanas de 1 s es −0.34, o sea nada. Pero comparar un fragmento corto contra
   un **patrón estable** ya construido es un problema mucho más fácil, y también
   está medido:

       ventana 0.4 s contra patrón → 96.7 % de acierto (propio 0.47, ajeno 0.14)
       ventana 0.6 s contra patrón → 91.7 %
       ventana 1.5 s contra patrón → 80.0 %

   Por eso el patrón se construye con una ventana larga (segundos, una sola vez)
   y el seguimiento usa ventanas cortas con salto corto. Sin esa asimetría la
   etapa decidiría cada 2.5 s, y las voces ajenas se cuelan en los huecos de
   medio segundo que deja el usuario entre frases — que es exactamente donde se
   cuelan. La resolución de la decisión es lo que hace que funcione.

   (Contraintuitivamente la ventana **corta** acierta más que la larga: una
   ventana larga durante habla simultánea mezcla a los dos hablantes y su
   embedding queda a medio camino, mientras que una corta suele caer dentro de
   una sola voz.)

3. **La ventana no cruza turnos, y la frontera es relativa.** Corolario de lo
   anterior, y lo que más afila la decisión: cuando el hablante dominante calla
   se vacía el buffer, de modo que una ventana nunca contiene audio de dos
   turnos distintos. Sin esto, la ventana que debería ver "solo el televisor"
   durante la pausa del usuario arrastra medio segundo de voz del usuario, y
   como el usuario suena mucho más fuerte, **el embedding sale pareciéndose al
   usuario**: medido, en las regiones donde solo hablaba el fondo la etapa
   dejaba pasar el 94 % de las tramas.

   El detalle que lo hace funcionar es que la frontera se detecta **por caída
   relativa al nivel de habla**, no contra un umbral absoluto de silencio. Con
   un umbral absoluto no se detecta ninguna frontera cuando hay fondo continuo
   —el televisor nunca baja de −55 dBFS— y el buffer no se vacía nunca, que es
   exactamente lo que se midió: cero fronteras detectadas en las escenas de
   televisor y de restaurante. Lo que marca el final del turno del usuario no es
   que el micrófono quede en silencio, sino que **el nivel se desplome respecto
   a lo que venía siendo**, siga sonando algo o no.
4. **Decisión con histéresis.** Se sale del estado "es el usuario" solo por debajo
   de `reject`, y se vuelve a entrar por encima de `accept`. Un único valor
   umbral haría que la ganancia oscilara en cada duda y eso se oye —y se
   transcribe— peor que el propio ruido.
5. **Atenuación suave, nunca silencio.** La voz ajena se baja hasta un suelo
   configurable (por defecto −18 dB) con rampa por muestra. No se silencia: los
   reconocedores de la familia Whisper alucinan frases enteras sobre silencio
   digital insertado, así que un fondo muy bajo es más seguro que un hueco.
6. **Sesgo deliberado a quedarse corto.** La rampa de recuperación es más rápida
   que la de atenuación, y cualquier duda (sin patrón todavía, ventana con poca
   voz, similitud intermedia) deja pasar el audio intacto. Dejar colar una frase
   ajena cuesta una corrección; comerse media dirección del usuario cuesta la
   llamada.

Lo que esta etapa **no** hace: separar dos voces simultáneas en el mismo
instante. Eso exige un modelo de extracción de hablante objetivo, y no existe
ninguno abierto, causal y de 8 kHz que se pueda desplegar hoy (el análisis está
en `docs/voice/AUDIO_PIPELINE.md`). Lo que sí hace es que, durante el solape, la
identidad siga anclada al usuario, de modo que la etapa siguiente
(`voice_focus`) pueda peinar los armónicos del usuario y no los del otro.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

import numpy as np

from services.audio.embedding import SpeakerEmbedder, cosine
from services.audio.frames import FrameSlicer
from services.audio.pipeline import BaseStage, FrameContext

logger = logging.getLogger("lyra.audio.speaker")

_POWER_FLOOR = 1e-12


class TargetSpeakerStage(BaseStage):
    """Aprende la voz del usuario durante la llamada y atenúa el resto."""

    name = "speaker_lock"

    def __init__(
        self,
        embedder: Optional[SpeakerEmbedder],
        *,
        rate: int,
        frame_ms: float,
        enroll_window_sec: float,
        track_window_sec: float,
        hop_sec: float,
        min_voiced_ratio: float,
        enroll_windows: int,
        accept: float,
        reject: float,
        adapt: float,
        floor_db: float,
        attack_ms: float,
        release_ms: float,
        silence_dbfs: float,
        gap_ms: float,
        turn_drop_db: float,
        dynamic_range_db: float,
        mark_background: bool,
    ):
        self.rate = int(rate)
        self.embedder = embedder
        self._frame_size = max(1, int(rate * frame_ms / 1000.0))
        self._slicer = FrameSlicer(self._frame_size)

        # Dos ventanas: larga para construir el patrón una sola vez, corta para
        # seguirlo con resolución suficiente (ver el encabezado del módulo).
        self._enroll_samples = max(self._frame_size, int(enroll_window_sec * rate))
        self._track_samples = max(self._frame_size, int(track_window_sec * rate))
        self._hop_samples = max(self._frame_size, int(hop_sec * rate))
        self.min_voiced_ratio = float(min_voiced_ratio)
        self._enroll_hops = max(1, int(enroll_windows))

        self.accept = float(accept)
        self.reject = float(reject)
        self.adapt = float(adapt)
        self.floor = float(10.0 ** (floor_db / 20.0))
        self.silence_dbfs = float(silence_dbfs)
        self.dynamic_range_db = float(dynamic_range_db)
        self.mark_background = bool(mark_background)

        frame_sec = self._frame_size / float(rate)
        self._attack = float(np.exp(-frame_sec / max(attack_ms / 1000.0, 1e-4)))
        self._release = float(np.exp(-frame_sec / max(release_ms / 1000.0, 1e-4)))

        # Ventana de audio reciente y de niveles, en paralelo: una alimenta al
        # modelo, la otra decide si vale la pena invocarlo. La capacidad es la de
        # la ventana larga, porque la corta es un sufijo suyo.
        self._audio: deque[np.ndarray] = deque()
        self._audio_samples = 0
        self._levels: deque[float] = deque(
            maxlen=self._enroll_samples // self._frame_size + 1
        )
        self._since_hop = 0
        self._gap_frames = max(1, round(gap_ms / max(frame_ms, 1e-6)))
        self._silent_run = 0
        self.turn_drop_db = float(turn_drop_db)
        self._speech_level: Optional[float] = None

        self._target: Optional[np.ndarray] = None
        self._enroll_hops_seen = 0
        self._enroll_sum: Optional[np.ndarray] = None
        self._similarity = 0.0
        # Un turno recién empezado todavía no tiene ventana que puntuar. Hasta
        # que la tenga la etapa deja pasar el audio: nunca se arrastra la
        # decisión del turno anterior, que puede ser de otra persona.
        self._decided = False
        self._gain = 1.0

        self.windows_scored = 0
        self.frames_foreign = 0
        self.frames_total = 0
        self.segments_seen = 0
        self.last_dynamic_range_db = 0.0

    @property
    def active(self) -> bool:
        return self.embedder is not None

    def reset(self) -> None:
        """Nuevo turno de escucha: se conserva la identidad, se limpia el resto.

        El patrón del hablante **sobrevive al reset a propósito**: es la misma
        persona durante toda la llamada, y volver a aprenderlo en cada turno
        dejaría desprotegido justo el arranque de cada respuesta.
        """
        self._slicer.reset()
        self._audio.clear()
        self._audio_samples = 0
        self._levels.clear()
        self._since_hop = 0
        self._silent_run = 0
        self._speech_level = None
        self._decided = False
        self._gain = 1.0

    # ── análisis ──

    def _push_audio(self, frame: np.ndarray) -> None:
        self._audio.append(frame)
        self._audio_samples += frame.size
        while self._audio_samples - self._audio[0].size >= self._enroll_samples:
            self._audio_samples -= self._audio.popleft().size

    def _window(self, samples: int) -> np.ndarray:
        """Últimas `samples` muestras del buffer (la ventana corta es sufijo de la larga)."""
        if not self._audio:
            return np.zeros(0, dtype=np.float32)
        window = np.concatenate(self._audio)
        return window[-samples:] if window.size > samples else window

    def _voiced_ratio(self, frames: int) -> float:
        """Fracción de tramas con voz en las últimas `frames` tramas."""
        if not self._levels:
            return 0.0
        recent = list(self._levels)[-frames:]
        if not recent:
            return 0.0
        return sum(1 for level in recent if level > self.silence_dbfs) / len(recent)

    def _dynamic_range(self) -> float:
        """Rango dinámico de la envolvente (dB): profundidad de los valles.

        Una voz en campo cercano deja caer el nivel entre sílabas; a dos metros,
        la cola reverberante de la sala rellena esos valles y el rango se
        comprime. Es la diferencia física entre cerca y lejos que **sobrevive a
        la normalización de nivel**, y por eso decide quién puede enrolarse.
        """
        active = [level for level in self._levels if level > self.silence_dbfs]
        if len(active) < 8:
            return 0.0
        values = np.fromiter(active, dtype=np.float64, count=len(active))
        return float(np.percentile(values, 90) - np.percentile(values, 10))

    def _enroll(self, ctx: FrameContext) -> None:
        """Construye el patrón del usuario promediando varias ventanas limpias.

        El patrón **no** se saca de un único fragmento largo: una vez que las
        fronteras de turno vacían el buffer, ningún turno dura lo suficiente como
        para reunir varios segundos seguidos, y exigirlo dejaba a la etapa sin
        enrolar nunca (medido). Se promedian en cambio los embeddings de las
        primeras ventanas que **parecen del usuario**, que es además más robusto:
        promediar cancela lo que varía entre ventanas (el fonema concreto) y
        conserva lo que se repite (la identidad).

        Los tres filtros de admisión son los que evitan enrolar al televisor:
        la ventana debe estar llena de voz, tener el rango dinámico de una voz
        próxima —los valles entre sílabas de una voz lejana los rellena la
        reverberación de la sala— y sonar al nivel del hablante dominante. Un
        patrón equivocado convierte esta etapa en un filtro que borra al usuario,
        que es el peor fallo posible del sistema.
        """
        if self._audio_samples < self._enroll_samples:
            return
        if ctx.playback_active or ctx.echo_detected:
            return  # el eco metería la voz de Lyra en el patrón del usuario
        frames = max(1, self._enroll_samples // self._frame_size)
        if self._voiced_ratio(frames) < self.min_voiced_ratio:
            return
        self.last_dynamic_range_db = round(self._dynamic_range(), 1)
        if self.last_dynamic_range_db < self.dynamic_range_db:
            return
        recent = list(self._levels)[-frames:]
        if self._speech_level is not None and recent:
            if float(np.mean(recent)) < self._speech_level - self.turn_drop_db:
                return  # suena por debajo del dominante: no es quien llama

        embedding = self.embedder(self._window(self._enroll_samples))
        if embedding is None:
            return
        self.windows_scored += 1
        self._enroll_sum = (
            embedding.copy() if self._enroll_sum is None else self._enroll_sum + embedding
        )
        self._enroll_hops_seen += 1
        if self._enroll_hops_seen < self._enroll_hops:
            return
        norm = float(np.linalg.norm(self._enroll_sum))
        if norm <= 1e-9:
            return
        self._target = (self._enroll_sum / norm).astype(np.float32)
        self._similarity = 1.0
        logger.info(
            "[audio] patrón del hablante objetivo aprendido con %d ventanas "
            "(rango dinámico %.1f dB)",
            self._enroll_hops_seen,
            self.last_dynamic_range_db,
        )

    def _track(self, ctx: FrameContext) -> None:
        """Puntúa la ventana corta contra el patrón ya establecido."""
        if self._audio_samples < self._track_samples:
            return  # el turno acaba de empezar: aún no hay ventana que puntuar
        frames = max(1, self._track_samples // self._frame_size)
        if self._voiced_ratio(frames) < self.min_voiced_ratio:
            return  # ventana casi vacía: no informa de identidad, no se decide
        embedding = self.embedder(self._window(self._track_samples))
        if embedding is None:
            return
        self.windows_scored += 1
        self._similarity = cosine(embedding, self._target)
        self._decided = True

        # Nota de diseño, por si alguien lo intenta de nuevo: se probó mantener
        # además un patrón del **intruso** y decidir por la diferencia entre las
        # dos similitudes (la estructura de razón de verosimilitudes que
        # recomienda la literatura, y que sobre el papel está mejor calibrada
        # porque cancela lo que afecta a ambas hipótesis por igual). Medido aquí,
        # **empeora**: el rechazo medio de voz baja de 17.0 a 15.6 dB y la escena
        # de conversación cercana se desploma de 13.8 a 9.5 dB. La causa es que
        # el patrón del intruso se contamina — durante el habla simultánea las
        # ventanas contienen a los dos, y al absorberlas el intruso se parece
        # cada vez más al usuario y el margen pierde sentido. Con un solo patrón
        # y umbral absoluto no existe esa realimentación.

        # Adaptación guardada: solo con evidencia clara de que sigue siendo él y
        # con la ventana llena de voz. Sin estas condiciones el patrón derivaría
        # hacia quien más hable —o hacia la mezcla de ambos durante el solape—,
        # que es exactamente el fallo que esta etapa existe para evitar. El ritmo
        # es lento a propósito: corrige la deriva del canal (el usuario se mueve,
        # cambia de postura) sin poder reescribir la identidad.
        strong = self._similarity >= self.accept + 0.5 * (1.0 - self.accept)
        if strong and not ctx.playback_active and not ctx.echo_detected:
            updated = (1.0 - self.adapt) * self._target + self.adapt * embedding
            norm = float(np.linalg.norm(updated))
            if norm > 1e-9:
                self._target = (updated / norm).astype(np.float32)

    # ── decisión ──

    def _target_gain(self) -> float:
        """Ganancia deseada para la trama actual, con histéresis."""
        if self._target is None or not self._decided:
            return 1.0  # sin patrón, o sin ventana puntuada, no se atenúa nunca
        score = self._similarity
        if score >= self.accept:
            return 1.0
        if score <= self.reject:
            return self.floor
        # Zona intermedia: interpolación en dB. Es la zona del habla simultánea,
        # donde ninguna hipótesis gana porque las dos están presentes de verdad;
        # ahí conviene quedarse corto y dejar pasar.
        span = max(self.accept - self.reject, 1e-6)
        ratio = (score - self.reject) / span
        return float(self.floor ** (1.0 - ratio))

    def process(self, block: np.ndarray, ctx: FrameContext) -> np.ndarray:
        if block.size == 0:
            return block

        for frame in self._slicer.push(block):
            self.frames_total += 1
            power = float(np.mean(np.square(frame, dtype=np.float64))) + _POWER_FLOOR
            level = 10.0 * float(np.log10(power))
            self._levels.append(level)

            # Nivel del hablante dominante: sube deprisa y baja despacio, así que
            # representa "lo que se venía oyendo" y no el fonema de esta trama.
            if self._speech_level is None:
                self._speech_level = level
            elif level > self._speech_level:
                self._speech_level = 0.7 * self._speech_level + 0.3 * level
            else:
                self._speech_level = 0.995 * self._speech_level + 0.005 * level

            # Frontera de turno: el dominante calla y el buffer se vacía, para que
            # la ventana siguiente pertenezca a un solo hablante. Mientras dura la
            # pausa la decisión vuelve a neutro — no se arrastra la del turno
            # anterior, que puede ser de otra persona.
            quiet = level <= self.silence_dbfs or level <= self._speech_level - self.turn_drop_db
            if quiet:
                self._silent_run += 1
                if self._silent_run == self._gap_frames:
                    self._audio.clear()
                    self._audio_samples = 0
                    self._since_hop = 0
                    self._decided = False
                    self.segments_seen += 1
            else:
                self._silent_run = 0

            self._push_audio(frame.copy())
            self._since_hop += frame.size
            if self.embedder is not None and self._since_hop >= self._hop_samples:
                self._since_hop = 0
                try:
                    if self._target is None:
                        self._enroll(ctx)
                    else:
                        self._track(ctx)
                except Exception:  # noqa: BLE001 — el aislamiento nunca tumba la llamada
                    logger.exception("[audio] fallo puntuando la ventana de hablante")

        wanted = self._target_gain()
        # Rampa asimétrica: bajar despacio, subir deprisa. La asimetría es
        # deliberada — recuperar tarde al usuario le corta una sílaba, mientras
        # que atenuar tarde a un intruso solo deja pasar una fracción de sílaba.
        alpha = self._attack if wanted < self._gain else self._release
        gain = alpha * self._gain + (1.0 - alpha) * wanted
        if gain < 1.0:
            self.frames_foreign += 1

        ctx.notes["speaker_similarity"] = round(self._similarity, 3)
        ctx.notes["speaker_gain"] = round(gain, 3)
        if self.mark_background and self._target is not None and self._decided:
            # La identidad manda sobre el criterio de nivel de `speaker_focus`:
            # si la voz es la del usuario, deja de marcarse como fondo aunque
            # suene bajo (habla lejos del micrófono, o baja la voz).
            ctx.background_voice = self._similarity <= self.reject

        # Rampa por muestra dentro del bloque: un salto de ganancia entre
        # bloques produce un clic de banda ancha, y un reconocedor lo lee como
        # el ataque de una consonante.
        ramp = np.linspace(self._gain, gain, block.size, dtype=np.float32)
        self._gain = gain
        return (block * ramp).astype(np.float32, copy=False)

    def stats(self) -> dict:
        return {
            "enrolled": self._target is not None,
            "windows_scored": self.windows_scored,
            "segments_seen": self.segments_seen,
            "similarity": round(self._similarity, 3),
            "dynamic_range_db": self.last_dynamic_range_db,
            "foreign_ratio": round(
                self.frames_foreign / self.frames_total if self.frames_total else 0.0, 3
            ),
        }
