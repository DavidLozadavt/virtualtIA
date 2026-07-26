"""Runtime de llamada V2 — composición del pipeline de voz.

Por llamada:

  frames WS → [grabadora pista usuario] → pipeline de audio (services/audio)
             → OpenAI Realtime STT
             ↘ clasificador de barge-in (solo mientras el bot habla)
  eventos STT → endpointer híbrido → TurnReady → filtros → NLU → FSM
  intención del turno → capa conversacional (plan de habla: etapas, pausas,
                        sonido contextual) → TTS por etapa → un solo WAV
                        compartido → ESL uuid_broadcast
                                   ↘ referencia far-end del cancelador de eco

El FSM nunca entrega texto al sintetizador: entrega una intención
(`VoiceTurnResult.speech`) y `services/voice/conversation` decide qué se dice,
en cuántas etapas y con qué ritmo.

Los turnos se serializan en una cola (el orden de eventos nunca se cruza);
la escucha jamás se pausa: durante el playback el audio del usuario sigue
llegando al STT y el clasificador decide si es interrupción real.

Pivote de playback (2026-07-19): `mod_audio_stream` v1.0.3 (binario oficial,
playback vía streamAudio por WS) no inyecta audio en el canal pese a seguir
la documentación al pie de la letra — confirmado con logs reales, pendiente
de soporte del vendor. El playback usa mientras tanto ESL `uuid_broadcast`
sobre un WAV en disco compartido (ver `audio_file_store.py`), el mecanismo
ya probado en V1. La captura (este mismo WS, streaming completo sin gate) no
cambió.

Defensa contra eco, en capas (de la señal al texto):
  1. `services/audio` cancela el eco usando el PCM del TTS como referencia. Que
     `uuid_broadcast` reproduzca dentro de FreeSWITCH ya no lo impide: el reloj
     solo aporta la hipótesis inicial y el desfase real (de decenas de ms a más
     de medio segundo en manos libres) lo resuelve la alineación por correlación.
  2. La puerta de voz exige más evidencia mientras hay eco detectado.
  3. La escucha permanece cerrada durante el procesamiento y la respuesta.
  4. El filtro de texto (`filters.looks_like_bot_echo`) descarta lo que aun así
     se cuele, y el clasificador de barge-in exige contenido con significado.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx
from fastapi import WebSocket

from core.address_utils import looks_like_place
from core.config import settings
from services.audio import CaptureEnhancer
from services.telephony.esl_client import get_esl_client
from services.telephony.session_store import (
    CallSession,
    STATE_FINISHED,
    STATE_WAITING_GEO_CONTEXT,
    STATE_WAITING_ORIGIN,
    SessionStore,
    get_session_store,
)
from services.voice import filters
from services.voice.audio_file_store import get_audio_file_store
from services.voice.barge_in import InterruptionClassifier
from services.voice.conversation import (
    ConversationEngine,
    RenderedSpeech,
    SpeechIntent,
    SpeechPlan,
    SpeechRequest,
    fixed_phrases,
)
from services.voice.endpointing import HybridEndpointer, StablePartial, TurnReady
from services.voice.nlu import TurnNLU
from services.voice.orchestrator import (
    TurnOrchestrator,
    VoiceAction,
    VoiceTurnResult,
)
from services.voice.recorder import CallRecorder
from services.voice.stt_stream import (
    OpenAIRealtimeSTT,
    SpeechStartedEvent,
    STTStreamError,
    TranscriptEvent,
)
from services.voice.transport import (
    SAMPLE_RATE,
    AudioFrame,
    FreeSwitchTransport,
    StreamStart,
    StreamStop,
)
from services.voice.tts_stream import StreamingTTS, TTSError

logger = logging.getLogger("lyra.voice.runtime")

_WATCHDOG_TICK_SEC = 0.1
_TTS_FAILURE_APOLOGY = (
    "Estamos presentando una falla técnica. Por favor llámanos de nuevo en unos minutos."
)
_STT_FAILURE_APOLOGY = (
    "Estamos presentando una falla técnica. Por favor llámanos de nuevo en unos minutos."
)

# Cuántas frases de espera adicionales admite una operación que se alarga. Por
# encima de esto la espera se vuelve cháchara.
_MAX_WAIT_PHRASES = 2

# Tope de espera a que un aviso ("ya mismo te busco esa dirección") esté
# realmente sonando antes de arrancar el trabajo pesado. Con la caché caliente
# se resuelve en milisegundos; el tope solo evita que una síntesis lenta retrase
# el procesamiento.
_ANNOUNCE_AUDIBLE_TIMEOUT = 1.5

# El sintetizador se comparte entre llamadas: su caché de frases es lo que hace
# que los avisos y acuses salgan al instante en vez de esperar a la red. Una
# instancia por llamada volvía a sintetizar desde cero cada vez.
_shared_tts: Optional[StreamingTTS] = None
# El banco fijo se pre-sintetiza una sola vez por proceso.
_bank_prewarmed = False


def get_shared_tts() -> StreamingTTS:
    global _shared_tts
    if _shared_tts is None:
        _shared_tts = StreamingTTS()
    return _shared_tts

_STREET_TEXT_RE = re.compile(r"(?:calle|carrera|cl|cra|kr|kra)\s*\.?\s*\d+", re.IGNORECASE)


def _is_street_text(text: str) -> bool:
    return bool(_STREET_TEXT_RE.search((text or "").lower()))


class VoiceCallRuntime:
    """Conduce una llamada completa sobre el WS de mod_audio_stream."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        store: Optional[SessionStore] = None,
        orchestrator: Optional[TurnOrchestrator] = None,
        tts: Optional[StreamingTTS] = None,
        nlu: Optional[TurnNLU] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.transport = FreeSwitchTransport(websocket)
        self.store = store or get_session_store()
        self.orchestrator = orchestrator or TurnOrchestrator()
        self.tts = tts or get_shared_tts()
        self.nlu = nlu or TurnNLU()
        self.http_client = http_client

        # Capa conversacional: entre el resultado lógico del turno y el TTS.
        # Decide qué se dice, en cuántas etapas, con qué pausas y con qué
        # tiempos. El texto final nunca sale directo del orquestador.
        self.conversation = ConversationEngine(sample_rate=SAMPLE_RATE)

        self.session: Optional[CallSession] = None
        self.recorder: Optional[CallRecorder] = None
        # Mejora de audio de captura (services/audio): supresión de ruido,
        # aislamiento de voz y cancelación de eco antes del STT. El PCM del TTS
        # se le publica como referencia far-end para poder cancelar el eco del
        # altavoz del usuario.
        self.enhancer = CaptureEnhancer(rate=SAMPLE_RATE)
        self.classifier = InterruptionClassifier()
        self.endpointer = HybridEndpointer(
            hold_ms=int(settings.VOICE_ENDPOINT_HOLD_MS),
            hold_max_ms=int(settings.VOICE_ENDPOINT_HOLD_MAX_MS),
        )
        self.stt: Optional[OpenAIRealtimeSTT] = None

        self._initialized = False
        self._ending = False
        self._turns_done = 0
        self._turn_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

        # Estado de playback (para barge-in y truncado de historial).
        self._playout_task: Optional[asyncio.Task] = None
        self._play_started: float = 0.0
        self._sentence_marks: list[tuple[float, str]] = []
        self._playing = False
        # Se activa cuando el audio del turno ya está ordenado a reproducir:
        # permite esperar a que un aviso sea audible antes de seguir.
        self._broadcast_event = asyncio.Event()

        self._silence_deadline: Optional[float] = None

        # Canal de escucha (micrófono). Se CIERRA por completo desde que el turno
        # del usuario termina y arranca el procesamiento, hasta que Lyra terminó
        # su respuesta y volvemos a esperar el siguiente turno. Cerrado ⇒ no se
        # envía audio al STT, no hay endpointing ni barge-in, y ningún turno nuevo
        # entra a la cola. Nunca hay una ventana donde Lyra se escuche a sí misma
        # ni donde una palabra suelta cancele un procesamiento ya iniciado.
        self._mic_open = True

    # ── ciclo de vida ──

    async def run(self) -> None:
        try:
            self.transport.resolve_identity()
            if self.transport.call_uuid:
                await self._initialize()

            async for event in self.transport.events():
                if isinstance(event, StreamStart):
                    if not self._initialized and self.transport.call_uuid:
                        await self._initialize()
                elif isinstance(event, AudioFrame):
                    if not self._initialized:
                        if self.transport.call_uuid:
                            await self._initialize()
                        else:
                            continue  # audio antes de conocer la llamada
                    await self._on_audio(event.pcm)
                elif isinstance(event, StreamStop):
                    break
                if self._ending:
                    break
        except Exception:
            logger.exception(
                "[runtime] fatal error call_uuid=%s", self.transport.call_uuid
            )
        finally:
            await self._shutdown()

    async def _initialize(self) -> None:
        self._initialized = True
        call_uuid = self.transport.call_uuid or ""
        # La memoria de expresiones y el estado conversacional son de ESTA
        # llamada: se reconstruyen en cuanto se conoce su identidad.
        self.conversation = ConversationEngine(call_uuid, sample_rate=SAMPLE_RATE)
        self.session = self.store.get_or_create(
            call_uuid, caller_phone=self.transport.caller_number
        )
        if self.transport.caller_number and not self.session.caller_phone:
            self.session.caller_phone = self.transport.caller_number
            self.store.save(self.session)
        if self.session.service_created or self.session.state == STATE_FINISHED:
            # Reconexión sobre un canal que Lyra ya dio por terminado (p. ej.
            # mod_audio_stream reabriendo el WS tras un uuid_kill que no
            # alcanzó a completarse) — colgar directo, sin conectar el STT ni
            # abrir la grabadora: abrir una sesión OpenAI Realtime aquí sería una
            # conexión facturable que no transcribiría nada. Es la red de
            # seguridad real; no re-saluda ni resetea el estado.
            logger.warning(
                "[runtime] reconexión sobre sesión terminal call_uuid=%s — colgando",
                call_uuid,
            )
            await self._hangup()
            return

        self.recorder = CallRecorder(call_uuid)

        self.stt = OpenAIRealtimeSTT(call_uuid=call_uuid)
        try:
            await self.stt.connect()
        except STTStreamError as e:
            logger.error("[runtime] STT unavailable call_uuid=%s err=%s", call_uuid, e)
            self.stt = None
            await self._speak_safe(_STT_FAILURE_APOLOGY)
            await self._hangup()
            return

        self._tasks.append(asyncio.create_task(self._stt_loop()))
        self._tasks.append(asyncio.create_task(self._watchdog_loop()))
        self._tasks.append(asyncio.create_task(self._turn_worker()))

        logger.info(
            "[runtime] call started call_uuid=%s caller=%s",
            call_uuid,
            self.transport.caller_number,
        )

        await self._turn_queue.put(("greeting", "", 0.0))

    # ── audio entrante ──

    async def _on_audio(self, pcm: bytes) -> None:
        if self._ending or self.recorder is None:
            return
        # La grabación guarda el audio TAL CUAL llegó: es la evidencia de la
        # llamada, no la señal de trabajo del reconocedor.
        self.recorder.add_user_audio(pcm)

        # El pipeline se alimenta SIEMPRE, incluso con la escucha cerrada: es
        # durante el playback cuando el cancelador de eco puede aprender el
        # camino acústico (es el único momento en que existe referencia), y
        # mantenerlo alimentado evita además huecos de estado al reabrir.
        # Fuera del bucle de eventos: el pipeline consume varios ms de CPU por
        # bloque y ejecutarlo aquí serializaría todas las llamadas del proceso.
        #
        # IMPORTANTE: este `await` es lo que garantiza el orden de los bloques de
        # ESTA llamada. `run()` no lee el evento siguiente del transporte hasta
        # que este termina, así que el estado recurrente del pipeline recibe el
        # audio en secuencia. Convertir esto en `create_task()` o en un envío sin
        # esperar reordenaría los bloques entre hilos del pool y corrompería ese
        # estado en silencio: la llamada seguiría funcionando, pero peor.
        clean, _ctx = await self.enhancer.process_async(
            pcm,
            timestamp=asyncio.get_running_loop().time(),
            playback_active=self._playing,
        )

        # Canal de escucha cerrado durante procesamiento/respuesta: no se acepta
        # audio nuevo (ni STT ni barge-in).
        if not self._mic_open:
            return
        if self.stt is not None:
            await self.stt.send_audio(clean)
        if self._playing:
            self.classifier.feed_audio(clean)
            if self.classifier.should_interrupt():
                await self._handle_barge_in()

    # ── eventos STT ──

    async def _stt_loop(self) -> None:
        assert self.stt is not None
        loop = asyncio.get_running_loop()
        try:
            async for event in self.stt.events():
                # Escucha cerrada: se drena el evento pero NO se procesa —
                # sin endpointing, sin barge-in, sin encolar turnos.
                if not self._mic_open:
                    continue
                now = loop.time()
                if isinstance(event, SpeechStartedEvent):
                    self._silence_deadline = None
                elif isinstance(event, TranscriptEvent):
                    if event.text:
                        self._silence_deadline = None
                    if event.text and self._playing:
                        self.classifier.feed_partial(
                            event.text,
                            self.session.state if self.session else "",
                        )
                        if self.classifier.should_interrupt():
                            await self._handle_barge_in()
                for signal in self.endpointer.on_event(event, now):
                    await self._dispatch_signal(signal)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "[runtime] stt loop error call_uuid=%s", self.transport.call_uuid
            )

    async def _dispatch_signal(self, signal: object) -> None:
        if not self._mic_open:
            return  # escucha cerrada: no se generan turnos ni especulación
        if isinstance(signal, StablePartial):
            self._on_stable_partial(signal)
        elif isinstance(signal, TurnReady):
            await self._turn_queue.put(("turn", signal.text, signal.confidence))

    def _on_stable_partial(self, signal: StablePartial) -> None:
        """Generación anticipada: NLU + geocoding especulativo sobre el parcial."""
        if self.session is None:
            return
        norm = filters.normalize_transcript(signal.text, signal.confidence)
        if not norm or filters.is_stt_hallucination(norm):
            return
        session = self.session
        task = self.nlu.preempt(norm, session.state, session.last_message)
        if task is None:
            return

        def _prewarm(done: asyncio.Task) -> None:
            if done.cancelled() or done.exception() is not None:
                return
            try:
                self.orchestrator.prewarm_origin(session, norm, done.result())
            except Exception:  # noqa: BLE001 — la especulación nunca rompe la llamada
                logger.debug("[runtime] prewarm skipped", exc_info=True)

        task.add_done_callback(_prewarm)

    # ── vigilancia: retención semántica del endpointer + silencios ──

    async def _watchdog_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while not self._ending:
                await asyncio.sleep(_WATCHDOG_TICK_SEC)
                now = loop.time()

                # Escucha cerrada: sin endpointing por temporizador ni prompts de
                # silencio (no se evalúa nada del canal de entrada).
                if not self._mic_open:
                    continue

                deadline = self.endpointer.pending_deadline
                if deadline is not None and now >= deadline:
                    for signal in self.endpointer.on_timer(now):
                        await self._dispatch_signal(signal)

                if (
                    self._silence_deadline is not None
                    and now >= self._silence_deadline
                    and not self._playing
                    and self._turn_queue.empty()
                    and not self.endpointer.has_speech()
                ):
                    self._silence_deadline = None
                    await self._turn_queue.put(("silence", "", 0.0))
        except asyncio.CancelledError:
            raise

    # ── procesamiento serializado de turnos ──

    async def _turn_worker(self) -> None:
        try:
            while not self._ending:
                kind, text, confidence = await self._turn_queue.get()
                # El turno arranca: se CIERRA la escucha antes de cualquier
                # procesamiento o audio de Lyra, y solo se reabre al terminar la
                # respuesta (regla de micrófono cerrado durante el turno).
                self._close_mic()
                # El usuario cedió la palabra: empieza el turno conversacional
                # de Lyra (estado UNDERSTANDING, sin audio emitido todavía).
                self.conversation.begin_turn()
                try:
                    if kind == "greeting":
                        await self._do_greeting()
                    elif kind == "silence":
                        await self._do_silence_turn()
                    else:
                        await self._handle_turn(text, confidence)
                except asyncio.CancelledError:
                    raise
                except TTSError as e:
                    logger.error(
                        "[runtime] TTS failure call_uuid=%s err=%s",
                        self.transport.call_uuid,
                        e,
                    )
                    await self._hangup()
                except Exception:
                    logger.exception(
                        "[runtime] turn error call_uuid=%s", self.transport.call_uuid
                    )
                finally:
                    # Respuesta terminada y de vuelta a espera: reabrir la escucha.
                    self.conversation.end_turn()
                    self._open_mic()
        except asyncio.CancelledError:
            raise

    # ── control del canal de escucha (micrófono) ──

    def _close_mic(self) -> None:
        """Cierra la escucha: descarta audio/eventos entrantes, sin STT,
        endpointing ni barge-in. No cancela el procesamiento en curso."""
        self._mic_open = False
        self._silence_deadline = None
        self.classifier.reset()

    def _open_mic(self) -> None:
        """Reabre la escucha una vez terminada la respuesta. Descarta cualquier
        estado parcial acumulado para no arrastrar audio previo al nuevo turno."""
        if self._ending:
            return
        self.classifier.reset()
        self.conversation.user_speaking()
        reset = getattr(self.endpointer, "reset", None)
        if callable(reset):
            try:
                reset()
            except Exception:  # pragma: no cover - defensivo
                pass
        self._mic_open = True

    async def _do_greeting(self) -> None:
        assert self.session is not None
        turn = self.orchestrator.handle_inbound(self.session)
        self.store.save(self.session)
        await self._deliver(turn)
        self._prewarm_phrase_bank()
        self._arm_silence_timer()

    def _prewarm_phrase_bank(self) -> None:
        """Pre-sintetiza el banco fijo una vez por proceso, en segundo plano.

        Son ~80 frases cortas y cerradas (acuses, avisos, esperas). Con ellas en
        la caché compartida del TTS, un aviso sale al instante en lugar de
        esperar cientos de milisegundos de red. Se lanza después del saludo para
        no competir con el primer audio de la llamada.
        """
        global _bank_prewarmed
        if _bank_prewarmed:
            return
        _bank_prewarmed = True
        self._tasks.append(asyncio.create_task(self._run_prewarm()))

    async def _run_prewarm(self) -> None:
        try:
            await self.tts.prewarm(fixed_phrases())
            logger.info("[runtime] banco conversacional pre-sintetizado")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — el precalentado nunca rompe la llamada
            logger.debug("[runtime] prewarm del banco omitido", exc_info=True)

    async def _do_silence_turn(self) -> None:
        assert self.session is not None
        turn = self.orchestrator.handle_silence(self.session)
        self.store.save(self.session)
        await self._finish_turn(turn)

    async def _handle_turn(self, raw_text: str, confidence: float) -> None:
        if self.session is None or self._ending:
            return
        session = self.session

        if self._turns_done >= int(settings.VOICE_MAX_TURNS):
            logger.warning(
                "[runtime] max turns reached call_uuid=%s", session.call_uuid
            )
            await self._hangup()
            return

        if filters.is_stt_hallucination(raw_text):
            logger.info(
                "[runtime] dropped STT hallucination call_uuid=%s text=%r",
                session.call_uuid,
                raw_text[:120],
            )
            return

        norm = filters.normalize_transcript(raw_text, confidence)
        if filters.looks_like_bot_echo(norm, session.last_message):
            logger.info(
                "[runtime] dropped bot-echo call_uuid=%s text=%r",
                session.call_uuid,
                norm[:120],
            )
            return

        if self._playing:
            # Fin de turno mientras el bot habla y el clasificador no lo elevó
            # a interrupción → backchannel puro: no es un turno.
            if self.classifier.meaningful_tokens(norm, session.state):
                await self._handle_barge_in()
            else:
                logger.info(
                    "[runtime] dropped backchannel during playback text=%r",
                    norm[:80],
                )
                return

        logger.info(
            '[runtime] turn call_uuid=%s raw="%s" norm="%s"',
            session.call_uuid,
            raw_text[:200],
            norm[:200],
        )

        nlu_result = await self.nlu.extract(norm, session.state, session.last_message)
        turn = await self._process_turn_with_narration(session, norm, nlu_result, confidence)
        self.store.save(session)
        self._turns_done += 1
        await self._finish_turn(turn)

    async def _process_turn_with_narration(
        self, session: CallSession, norm: str, nlu_result, confidence: float
    ):
        """Narra el trabajo ANTES de empezarlo y acompaña la espera si se alarga.

        Orden garantizado: (1) el turno del usuario terminó y la escucha ya está
        cerrada; (2) si el turno hará trabajo costoso, Lyra narra de inmediato el
        proceso que va a ejecutar; (3) recién entonces comienza el procesamiento
        interno, que corre igual que siempre en `proc` mientras la narración
        suena; (4) si `proc` tarda más de lo esperado, la conversación se
        mantiene viva con frases de espera distintas entre sí.

        La narración solo describe procesos que existen de verdad, y el texto lo
        elige la capa conversacional — aquí solo se decide QUÉ proceso corre."""
        state_at_entry = session.state
        narration_kind: Optional[str] = None
        filler_task: Optional[asyncio.Task] = None
        if self._should_announce(session, norm, nlu_result) and not self._ending:
            narration_kind = self._narration_kind(state_at_entry, norm, nlu_result)
            logger.info(
                "[runtime] narrating work call_uuid=%s state=%s kind=%s",
                session.call_uuid, state_at_entry, narration_kind,
            )
            # Narración emitida YA (antes de arrancar el pipeline). El texto lo
            # decide la capa conversacional, nunca esta capa.
            self._broadcast_event.clear()
            filler_task = asyncio.create_task(
                self._speak_request_safe(self.conversation.narration(narration_kind))
            )
            # Se espera a que el aviso esté SONANDO antes de arrancar el trabajo:
            # el procesamiento incluye tramos de CPU síncrona (parser de
            # direcciones) que bloquean el bucle de eventos, y sin esta espera la
            # síntesis del aviso quedaba detrás de ellos — el "ya te la busco"
            # llegaba cuando la búsqueda ya había terminado.
            await self._wait_until_audible(filler_task, _ANNOUNCE_AUDIBLE_TIMEOUT)

        # Con la narración ya iniciada, arranca todo el procesamiento interno.
        proc = asyncio.create_task(
            self.orchestrator.process_turn(
                session,
                text=norm,
                nlu=nlu_result,
                confidence=confidence,
                http_client=self.http_client,
            )
        )
        try:
            if filler_task is not None:
                await filler_task   # el audio de la narración termina primero
                # Espera inteligente: si la operación se alarga, la conversación
                # se mantiene viva con frases distintas, nunca con silencio.
                await self._keep_alive(proc, narration_kind)
            return await proc
        except asyncio.CancelledError:
            proc.cancel()
            raise

    async def _wait_until_audible(self, task: asyncio.Task, timeout: float) -> None:
        """Cede el bucle hasta que el audio del turno empiece a sonar.

        Retorna antes si la reproducción ya se ordenó, si la tarea terminó (o
        falló) o si se agota el tope — nunca deja el procesamiento esperando.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while not task.done() and not self._broadcast_event.is_set():
            if loop.time() >= deadline or self._ending:
                return
            await asyncio.sleep(0.02)

    async def _keep_alive(self, proc: asyncio.Task, kind: Optional[str]) -> None:
        """Acompaña una operación que se alarga, sin repetir la misma frase."""
        for attempt in range(_MAX_WAIT_PHRASES):
            if proc.done() or self._ending:
                return
            await asyncio.wait(
                {proc}, timeout=self.conversation.wait_check_interval(attempt)
            )
            if proc.done() or self._ending:
                return
            await self._speak_request_safe(self.conversation.wait_more(kind or ""))

    def _should_announce(self, session: CallSession, norm: str, nlu_result) -> bool:
        """True si el turno hará trabajo costoso (geocodificación/resolución) y por
        tanto debe anunciarse antes de procesar. Turnos triviales (saludo, repetir,
        confirmar sí/no) no anuncian nada."""
        state = session.state
        if state == STATE_WAITING_GEO_CONTEXT:
            # Universo cerrado (≥2 candidatos) ⇒ resolución local rápida, sin red;
            # sin él se re-geocodifica (costoso) ⇒ anunciar.
            cands = getattr(session, "geo_candidates", None) or []
            return len(cands) < 2
        if state == STATE_WAITING_ORIGIN:
            intent = getattr(nlu_result, "intent", "") or ""
            if intent in (
                "greeting", "chitchat_only", "repeat_request",
                "confirm_yes", "confirm_no",
            ):
                return False
            return (
                _is_street_text(norm)
                or bool(getattr(nlu_result, "best_pickup", None))
                or looks_like_place(norm)
            )
        return False

    def _narration_kind(self, state: str, norm: str, nlu_result) -> str:
        """Qué proceso REAL está corriendo — la narración nunca inventa uno."""
        if state == STATE_WAITING_GEO_CONTEXT:
            return "geo_context"
        if state == STATE_WAITING_ORIGIN:
            span = getattr(nlu_result, "best_pickup", None) or norm
            return "address" if _is_street_text(span) or _is_street_text(norm) else "place"
        return "generic"

    async def _finish_turn(self, turn: VoiceTurnResult) -> None:
        """Habla el resultado y ejecuta la acción (create/hangup/listen)."""
        assert self.session is not None
        session = self.session

        if turn.action == VoiceAction.CREATE_SERVICE:
            # Acuse en paralelo con la creación del servicio: el usuario nunca
            # queda en silencio mientras el backend responde.
            wait_task = asyncio.create_task(self._deliver(turn))
            create = asyncio.create_task(
                self.orchestrator.process_turn(
                    session, text="", http_client=self.http_client
                )
            )
            await asyncio.wait({wait_task})
            # Barge-in sobre el acuse: el resultado igual se habla. Y si el
            # acuse falló, la creación NUNCA se aborta a medias — se deja
            # terminar y recién después se propaga el fallo.
            speak_exc = None if wait_task.cancelled() else wait_task.exception()
            await self._keep_alive(create, "service")
            final = await create
            self.store.save(session)
            if speak_exc is not None:
                raise speak_exc
            turn = final

        await self._deliver(turn)

        if turn.action == VoiceAction.HANGUP:
            # Igual que V1: la sesión sobrevive solo si el servicio se creó
            # (para que audio residual no reinicie el flujo).
            if not session.service_created:
                self.store.delete(session.call_uuid)
            await self._hangup()
        else:
            self._arm_silence_timer()

    def _arm_silence_timer(self) -> None:
        self._silence_deadline = (
            asyncio.get_running_loop().time()
            + float(settings.VOICE_SILENCE_PROMPT_SEC)
        )

    # ── playback vía ESL uuid_broadcast ──

    async def _deliver(self, turn: VoiceTurnResult) -> None:
        """Habla el resultado de un turno a través de la capa conversacional."""
        request = turn.speech
        if request is None:
            # Red de seguridad: un turno sin intención declarada se dice tal
            # cual, troceado en ideas y sin adornos.
            if not (turn.speak_text or "").strip():
                return
            request = SpeechRequest(
                intent=SpeechIntent.REPROMPT, text=turn.speak_text
            )
        await self._speak_request(request)

    async def _speak_request(self, request: SpeechRequest) -> Optional[RenderedSpeech]:
        """Planifica la intención, la reproduce y espera a que termine."""
        if self._ending:
            return None
        plan = self.conversation.plan(request)
        if not plan.segments:
            return None
        rendered = await self._speak_plan_and_wait(plan)
        if rendered is not None and rendered.text and self.session is not None:
            # El historial guarda lo REALMENTE dicho: es contra eso que se
            # compara el eco textual y lo que se repite si el usuario lo pide.
            self.session.last_message = rendered.text
        return rendered

    async def _speak_request_safe(self, request: SpeechRequest) -> None:
        try:
            await self._speak_request(request)
        except TTSError as e:
            logger.error("[runtime] speak failed: %s", e)

    async def _speak_plan_and_wait(self, plan: SpeechPlan) -> Optional[RenderedSpeech]:
        """Reproduce un plan; retorna al terminar o al ser interrumpido."""
        task = asyncio.create_task(self._play_plan(plan))
        self._playout_task = task
        await asyncio.wait({task})
        if task.cancelled():
            return None  # barge-in: el turno del usuario ya está en camino
        exc = task.exception()
        if exc is not None:
            raise exc
        return task.result()

    async def _speak_and_wait(self, text: str) -> None:
        """Reproduce texto suelto (disculpas técnicas), sin intención declarada."""
        if not (text or "").strip() or self._ending:
            return
        await self._speak_request(self.conversation.fallback(text))

    async def _synth_stage(self, text: str) -> bytes:
        """PCM de una etapa hablada, con el mismo reintento de TTS de siempre."""
        from services.voice.text_normalize import split_sentences

        pcm = bytearray()
        for sentence in split_sentences(text):
            attempt = 0
            while True:
                try:
                    async for chunk in self.tts.synthesize_sentence(sentence):
                        pcm.extend(chunk)
                    break
                except TTSError:
                    attempt += 1
                    if attempt >= 2:
                        raise
                    logger.warning("[runtime] TTS retry sentence=%r", sentence[:60])
        return bytes(pcm)

    async def _play_plan(self, plan: SpeechPlan) -> Optional[RenderedSpeech]:
        """Renderiza el plan a un único buffer y lo reproduce.

        Las pausas y el fondo contextual viajan DENTRO del mismo PCM: una sola
        síntesis, un solo WAV y un solo `uuid_broadcast`, igual que antes. El
        ritmo humano no cuesta ni una conexión extra ni latencia añadida.

        `mod_audio_stream` no reproduce vía WS (ver docstring del módulo) —
        se usa `uuid_broadcast` sobre un WAV compartido, como en V1.
        """
        self._playing = True
        self.conversation.playback_started()
        self.classifier.reset()
        self._sentence_marks = []
        call_uuid = self.transport.call_uuid or ""
        try:
            rendered = await self.conversation.render(plan, self._synth_stage)
            self._sentence_marks = list(rendered.marks)

            if not rendered.pcm or self.transport.closed or self._ending:
                return rendered

            if self.recorder is not None:
                self.recorder.add_bot_audio(rendered.pcm)

            store = get_audio_file_store()
            _, container_path, duration = store.save_pcm(
                rendered.pcm, call_uuid=call_uuid
            )

            if not (settings.FREESWITCH_ESL_ENABLED and call_uuid):
                raise TTSError("ESL deshabilitado o sin call_uuid — no se puede reproducir")

            self._play_started = asyncio.get_running_loop().time()
            # Referencia far-end para el cancelador de eco: el audio exacto que
            # va a sonar, anclado al instante en que se ordena reproducirlo. El
            # desfase real de la red lo resuelve después la alineación por
            # correlación, no este reloj.
            self.enhancer.playback_started(self._play_started)
            self.enhancer.publish_playback(rendered.pcm)
            ok = await get_esl_client().uuid_broadcast(call_uuid, container_path, "aleg")
            if not ok:
                raise TTSError(f"uuid_broadcast falló call_uuid={call_uuid}")
            self._broadcast_event.set()

            await asyncio.sleep(duration)
            return rendered
        finally:
            self._playing = False
            self.conversation.playback_finished()
            self.enhancer.playback_finished()
            self.classifier.reset()

    def _heard_text(self) -> str:
        """Oraciones que el usuario alcanzó a escuchar antes de interrumpir."""
        elapsed = asyncio.get_running_loop().time() - self._play_started
        heard = [s for end, s in self._sentence_marks if end <= elapsed + 0.2]
        return " ".join(heard).strip()

    async def _handle_barge_in(self) -> None:
        task = self._playout_task
        if task is None or task.done():
            return
        logger.info(
            "[runtime] barge-in confirmed call_uuid=%s", self.transport.call_uuid
        )
        call_uuid = self.transport.call_uuid or ""
        if settings.FREESWITCH_ESL_ENABLED and call_uuid:
            try:
                await get_esl_client().uuid_break(call_uuid)
            except Exception as e:
                logger.warning("[runtime] uuid_break failed: %s", e)
        # El audio que quedaba por reproducir nunca sonó: descartarlo de la
        # referencia para que el cancelador no intente restar un eco inexistente.
        self.enhancer.playback_finished(
            at_time=asyncio.get_running_loop().time()
        )
        task.cancel()
        if self.session is not None:
            self.orchestrator.note_partial_delivery(self.session, self._heard_text())
            self.store.save(self.session)
        self.classifier.reset()

    async def _speak_safe(self, text: str) -> None:
        try:
            await self._speak_and_wait(text)
        except TTSError as e:
            logger.error("[runtime] speak_safe failed: %s", e)

    # ── fin de llamada ──

    async def _hangup(self) -> None:
        if self._ending:
            return
        self._ending = True
        await self._kill_channel()
        await self.transport.close()

    async def _kill_channel(self) -> None:
        """Cuelga el canal real de FreeSWITCH vía ESL.

        Blindado con `asyncio.shield`: si la tarea que nos contiene se
        cancela a mitad de camino (p. ej. una carrera con `_shutdown()`),
        el propio `uuid_kill` sigue corriendo hasta terminar en vez de
        abortarse a medias — sin esto el canal podía quedar vivo y
        `mod_audio_stream` reabría el WS, repitiendo el saludo indefinidamente.
        Idempotente: llamarlo dos veces (aquí y en `_shutdown`) es inofensivo,
        un `uuid_kill` sobre un canal ya muerto solo responde `-ERR`.
        """
        call_uuid = self.transport.call_uuid or ""
        if not (settings.FREESWITCH_ESL_ENABLED and call_uuid):
            return
        try:
            await asyncio.shield(get_esl_client().uuid_kill(call_uuid))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[runtime] uuid_kill failed: %s", e)

    async def _shutdown(self) -> None:
        self._ending = True
        for task in self._tasks:
            task.cancel()
        if self._playout_task is not None:
            self._playout_task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Red de seguridad: si `_hangup()` nunca llegó a ejecutarse (o su
        # `uuid_kill` quedó a medias por la cancelación de arriba), el canal
        # real de FreeSWITCH puede seguir vivo — sin esto, mod_audio_stream
        # reabre el WS y el runtime siguiente vuelve a saludar. Idempotente.
        await self._kill_channel()
        if self.stt is not None:
            await self.stt.close()
        # Telemetría del pipeline de audio. `_shutdown` corre en cualquier ruta
        # de fallo, incluida una en la que el constructor no llegó a terminar:
        # el cierre nunca debe romperse por una métrica.
        enhancer = getattr(self, "enhancer", None)
        if enhancer is not None:
            logger.info(
                "[runtime] audio pipeline call_uuid=%s stats=%s",
                self.transport.call_uuid,
                enhancer.stats(),
            )
        if self.recorder is not None:
            self.recorder.write_wav()
        if self.session is not None:
            if self.session.service_created or self.session.state == STATE_FINISHED:
                self.store.save(self.session)
            self.orchestrator.forget(self.session.call_uuid)
        await self.transport.close()
        logger.info("[runtime] call closed call_uuid=%s", self.transport.call_uuid)
