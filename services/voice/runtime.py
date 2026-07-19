"""Runtime de llamada V2 — composición del pipeline de voz.

Por llamada:

  frames WS → [grabadora pista usuario] → OpenAI Realtime STT (siempre, sin gate)
             ↘ clasificador de barge-in (solo mientras el bot habla)
  eventos STT → endpointer híbrido → TurnReady → filtros → NLU → FSM
  respuesta → TTS (texto completo) → WAV compartido → ESL uuid_broadcast

Los turnos se serializan en una cola (el orden de eventos nunca se cruza);
la escucha jamás se pausa: durante el playback el audio del usuario sigue
llegando al STT y el clasificador decide si es interrupción real.

Pivote de playback (2026-07-19): `mod_audio_stream` v1.0.3 (binario oficial,
playback vía streamAudio por WS) no inyecta audio en el canal pese a seguir
la documentación al pie de la letra — confirmado con logs reales, pendiente
de soporte del vendor. El playback usa mientras tanto ESL `uuid_broadcast`
sobre un WAV en disco compartido (ver `audio_file_store.py`), el mecanismo
ya probado en V1. La captura (este mismo WS, streaming completo sin gate) no
cambió. El AEC de referencia far-end queda sin usar: `uuid_broadcast`
reproduce dentro de FreeSWITCH sin que Python controle el timing real, así
que ya no hay una referencia de tiempo válida para cancelar eco por NLMS —
la defensa contra eco pasa a ser el filtro de texto
(`filters.looks_like_bot_echo`) más el clasificador de barge-in, que exige
contenido con significado, no solo energía.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
from fastapi import WebSocket

from core.config import settings
from services.telephony.esl_client import get_esl_client
from services.telephony.session_store import (
    CallSession,
    STATE_FINISHED,
    SessionStore,
    get_session_store,
)
from services.voice import filters
from services.voice.audio_file_store import get_audio_file_store
from services.voice.barge_in import InterruptionClassifier
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
    AudioFrame,
    FreeSwitchTransport,
    StreamStart,
    StreamStop,
)
from services.voice.tts_stream import BYTES_PER_SECOND, StreamingTTS, TTSError

logger = logging.getLogger("lyra.voice.runtime")

_WATCHDOG_TICK_SEC = 0.1
_TTS_FAILURE_APOLOGY = (
    "Estamos presentando una falla técnica. Por favor llámanos de nuevo en unos minutos."
)
_STT_FAILURE_APOLOGY = (
    "Estamos presentando una falla técnica. Por favor llámanos de nuevo en unos minutos."
)


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
        self.tts = tts or StreamingTTS()
        self.nlu = nlu or TurnNLU()
        self.http_client = http_client

        self.session: Optional[CallSession] = None
        self.recorder: Optional[CallRecorder] = None
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

        self._silence_deadline: Optional[float] = None

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
        self.session = self.store.get_or_create(
            call_uuid, caller_phone=self.transport.caller_number
        )
        if self.transport.caller_number and not self.session.caller_phone:
            self.session.caller_phone = self.transport.caller_number
            self.store.save(self.session)
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
        if self.session.service_created or self.session.state == STATE_FINISHED:
            # Reconexión sobre un canal que Lyra ya dio por terminado (p. ej.
            # mod_audio_stream reabriendo el WS tras un uuid_kill que no
            # alcanzó a completarse) — no volver a saludar ni resetear el
            # estado: colgar directo, es la red de seguridad real.
            logger.warning(
                "[runtime] reconexión sobre sesión terminal call_uuid=%s — colgando",
                call_uuid,
            )
            await self._hangup()
            return

        await self._turn_queue.put(("greeting", "", 0.0))

    # ── audio entrante ──

    async def _on_audio(self, pcm: bytes) -> None:
        if self._ending or self.recorder is None:
            return
        self.recorder.add_user_audio(pcm)
        if self.stt is not None:
            await self.stt.send_audio(pcm)
        if self._playing:
            self.classifier.feed_audio(pcm)
            if self.classifier.should_interrupt():
                await self._handle_barge_in()

    # ── eventos STT ──

    async def _stt_loop(self) -> None:
        assert self.stt is not None
        loop = asyncio.get_running_loop()
        try:
            async for event in self.stt.events():
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
        except asyncio.CancelledError:
            raise

    async def _do_greeting(self) -> None:
        assert self.session is not None
        turn = self.orchestrator.handle_inbound(self.session)
        self.store.save(self.session)
        await self._speak_and_wait(turn.speak_text)
        self._arm_silence_timer()

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
        turn = await self.orchestrator.process_turn(
            session,
            text=norm,
            nlu=nlu_result,
            confidence=confidence,
            http_client=self.http_client,
        )
        self.store.save(session)
        self._turns_done += 1
        await self._finish_turn(turn)

    async def _finish_turn(self, turn: VoiceTurnResult) -> None:
        """Habla el resultado y ejecuta la acción (create/hangup/listen)."""
        assert self.session is not None
        session = self.session

        if turn.action == VoiceAction.CREATE_SERVICE:
            # Frase de espera en paralelo con la creación del servicio: el
            # usuario nunca queda en silencio mientras el backend responde.
            wait_task = asyncio.create_task(self._speak_and_wait(turn.speak_text))
            final = await self.orchestrator.process_turn(
                session, text="", http_client=self.http_client
            )
            self.store.save(session)
            await asyncio.wait({wait_task})
            if wait_task.cancelled():
                pass  # barge-in sobre la frase de espera: el resultado igual se habla
            elif wait_task.exception():
                raise wait_task.exception()
            turn = final

        await self._speak_and_wait(turn.speak_text)

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

    async def _speak_and_wait(self, text: str) -> None:
        """Reproduce `text`; retorna al terminar o al ser interrumpido."""
        if not (text or "").strip() or self._ending:
            return
        task = asyncio.create_task(self._play_text(text))
        self._playout_task = task
        await asyncio.wait({task})
        if task.cancelled():
            return  # barge-in: el turno del usuario ya está en camino
        exc = task.exception()
        if exc is not None:
            raise exc

    async def _play_text(self, text: str) -> None:
        """Sintetiza el texto completo, lo sube a broadcast y espera su duración.

        `mod_audio_stream` no reproduce vía WS (ver docstring del módulo) —
        se usa `uuid_broadcast` sobre un WAV compartido, como en V1.
        """
        from services.voice.text_normalize import split_sentences

        self._playing = True
        self.classifier.reset()
        self._sentence_marks = []
        call_uuid = self.transport.call_uuid or ""
        pcm = bytearray()
        try:
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
                        logger.warning(
                            "[runtime] TTS retry sentence=%r", sentence[:60]
                        )
                self._sentence_marks.append((len(pcm) / BYTES_PER_SECOND, sentence))

            if not pcm or self.transport.closed or self._ending:
                return

            if self.recorder is not None:
                self.recorder.add_bot_audio(bytes(pcm))

            store = get_audio_file_store()
            _, container_path, duration = store.save_pcm(bytes(pcm), call_uuid=call_uuid)

            if not (settings.FREESWITCH_ESL_ENABLED and call_uuid):
                raise TTSError("ESL deshabilitado o sin call_uuid — no se puede reproducir")

            self._play_started = asyncio.get_running_loop().time()
            ok = await get_esl_client().uuid_broadcast(call_uuid, container_path, "aleg")
            if not ok:
                raise TTSError(f"uuid_broadcast falló call_uuid={call_uuid}")

            await asyncio.sleep(duration)
        finally:
            self._playing = False
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
        if self.recorder is not None:
            self.recorder.write_wav()
        if self.session is not None:
            if self.session.service_created or self.session.state == STATE_FINISHED:
                self.store.save(self.session)
            self.orchestrator.forget(self.session.call_uuid)
        await self.transport.close()
        logger.info("[runtime] call closed call_uuid=%s", self.transport.call_uuid)
