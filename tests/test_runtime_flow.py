"""Flujo completo del runtime V2 con transporte/STT/TTS/ESL simulados.

Simula una llamada real de punta a punta:
  conexión WS → saludo (uuid_broadcast) → "estoy en pubenza" → confirmación →
  "sí" → creación de servicio (backend fake) → despedida → colgado (uuid_kill).

Playback vía ESL uuid_broadcast (pivote 2026-07-19, ver runtime.py) — el WS
de mod_audio_stream se usa solo para captura, ya no para audio saliente.
"""

import asyncio
from types import SimpleNamespace

import pytest

from core.config import settings
from core.geo_types import ResolutionStatus
from services.telephony.session_store import SessionStore
from services.voice.nlu import TurnNLU
from services.voice.orchestrator import TurnOrchestrator
from services.voice.runtime import VoiceCallRuntime
from services.voice.stt_stream import TranscriptEvent
from services.voice.tts_stream import StreamingTTS


class FakeWebSocket:
    """WS mínimo compatible con FreeSwitchTransport (solo captura)."""

    def __init__(self, call_uuid="e2e-uuid", caller="3001234567"):
        self.query_params = {"call_uuid": call_uuid, "caller_number": caller}
        self.headers = {}
        self.scope = {"app": None}
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def receive(self):
        return await self._inbox.get()

    async def close(self):
        self.closed = True
        await self._inbox.put({"type": "websocket.disconnect"})

    def push_disconnect(self):
        self._inbox.put_nowait({"type": "websocket.disconnect"})


class FakeSTT:
    """Reemplaza OpenAIRealtimeSTT: eventos inyectados por el test."""

    instances: list["FakeSTT"] = []

    def __init__(self, call_uuid: str, sample_rate: int = 8000):
        self.call_uuid = call_uuid
        self.queue: asyncio.Queue = asyncio.Queue()
        self.audio_bytes = 0
        self.closed = False
        FakeSTT.instances.append(self)

    async def connect(self):
        return None

    async def send_audio(self, pcm: bytes):
        self.audio_bytes += len(pcm)

    async def events(self):
        while True:
            ev = await self.queue.get()
            if ev is None:
                return
            yield ev

    async def close(self):
        self.closed = True
        self.queue.put_nowait(None)


class FakeESLClient:
    """Reemplaza FreeSwitchESLClient: registra llamadas en vez de conectar TCP."""

    def __init__(self):
        self.broadcasts: list[tuple[str, str, str]] = []
        self.kills: list[str] = []
        self.breaks: list[str] = []

    async def uuid_broadcast(self, call_uuid: str, media_uri: str, leg: str = "aleg") -> bool:
        self.broadcasts.append((call_uuid, media_uri, leg))
        return True

    async def uuid_kill(self, call_uuid: str, cause: str = "NORMAL_CLEARING") -> bool:
        self.kills.append(call_uuid)
        return True

    async def uuid_break(self, call_uuid: str) -> bool:
        self.breaks.append(call_uuid)
        return True


class FakeGeocoder:
    def prewarm(self, query, attempt=1):
        pass

    async def resolve(self, query, attempt=1):
        return SimpleNamespace(
            status=ResolutionStatus.RESOLVED,
            selected=SimpleNamespace(neighborhood="Pubenza"),
            attempt=attempt,
            disambiguation_question=None,
        )


class FakeBackend:
    def __init__(self):
        self.calls = []

    async def create_service_from_geocoded(self, **kwargs):
        self.calls.append(kwargs)
        return True, "Te enviaremos los datos del conductor por WhatsApp. ¡Buen viaje!"


def _final(text):
    return TranscriptEvent(
        text=text, confidence=0.9, is_final=True, speech_final=True
    )


@pytest.fixture()
def fast_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FREESWITCH_ESL_ENABLED", True)
    monkeypatch.setattr(settings, "FREESWITCH_RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setattr(settings, "FREESWITCH_TTS_SHARED_DIR", str(tmp_path / "tts_shared"))
    monkeypatch.setattr(settings, "FREESWITCH_TTS_CONTAINER_DIR", "/tmp/lyra-tts")
    monkeypatch.setattr(settings, "VOICE_SILENCE_PROMPT_SEC", 30.0)
    return tmp_path


def test_full_call_flow(fast_settings, monkeypatch):
    import services.voice.runtime as rt

    FakeSTT.instances.clear()
    monkeypatch.setattr(rt, "OpenAIRealtimeSTT", FakeSTT)

    fake_esl = FakeESLClient()
    monkeypatch.setattr(rt, "get_esl_client", lambda: fake_esl)

    tts = StreamingTTS()

    async def fake_synth(norm_text):
        yield b"\x00\x01" * 800  # 0.1 s de audio por oración

    monkeypatch.setattr(tts, "_synthesize_stream", fake_synth)

    nlu = TurnNLU()
    nlu._client = None  # fuerza el clasificador determinista local

    backend = FakeBackend()
    store = SessionStore()
    orch = TurnOrchestrator(backend=backend, geocoder=FakeGeocoder())

    ws = FakeWebSocket()
    runtime = VoiceCallRuntime(
        ws, store=store, orchestrator=orch, tts=tts, nlu=nlu
    )

    async def _wait_mic_open():
        # La escucha se reabre solo cuando Lyra terminó su respuesta y volvemos a
        # esperar el siguiente turno. Inyectar audio antes se descarta (mic cerrado).
        for _ in range(200):
            await asyncio.sleep(0.02)
            if runtime._mic_open:
                return
        raise AssertionError("el micrófono nunca se reabrió")

    async def scenario():
        run_task = asyncio.create_task(runtime.run())
        # Espera a que el saludo se reproduzca (uuid_broadcast disparado).
        for _ in range(100):
            await asyncio.sleep(0.02)
            if fake_esl.broadcasts:
                break
        assert fake_esl.broadcasts, "el saludo nunca se reprodujo"
        stt = FakeSTT.instances[0]
        await _wait_mic_open()   # micrófono reabierto tras el saludo

        # Turno 1: el usuario da el barrio envuelto en cortesía.
        stt.queue.put_nowait(_final("buenas estoy en pubenza por favor"))
        for _ in range(300):
            await asyncio.sleep(0.02)
            sess = store.get("e2e-uuid")
            if sess and sess.state == "confirming_origin" and runtime._mic_open:
                break
        sess = store.get("e2e-uuid")
        assert sess is not None and sess.state == "confirming_origin"
        assert sess.origen_barrio == "Pubenza"

        # Turno 2: confirmación → creación de servicio → colgado.
        stt.queue.put_nowait(_final("sí señora"))
        for _ in range(300):
            await asyncio.sleep(0.02)
            if backend.calls and ws.closed:
                break
        assert backend.calls, "el servicio nunca se creó"
        assert backend.calls[0]["celular"] == "+573001234567"
        assert backend.calls[0]["origen"]  # origen capturado

        ws.push_disconnect()
        await asyncio.wait_for(run_task, timeout=5)

    asyncio.run(scenario())

    # Playback: cada turno hablado disparó un uuid_broadcast con WAV real.
    assert len(fake_esl.broadcasts) >= 2
    for call_uuid, path, leg in fake_esl.broadcasts:
        assert call_uuid == "e2e-uuid"
        assert path.startswith("/tmp/lyra-tts/") and path.endswith(".wav")
        assert leg == "aleg"

    # Colgado real vía ESL uuid_kill. `_shutdown` reintenta como red de
    # seguridad (idempotente) si `_hangup` no llegó a completarlo — por eso
    # puede aparecer más de una vez, siempre sobre el mismo call_uuid.
    assert fake_esl.kills and all(u == "e2e-uuid" for u in fake_esl.kills)

    # La sesión terminal sobrevive con service_created (regla V1 preservada).
    sess = store.get("e2e-uuid")
    assert sess is not None and sess.service_created

    # Grabación server-side escrita (pista del bot presente).
    rec = fast_settings / "recordings" / "e2e-uuid.wav"
    assert rec.is_file() and rec.stat().st_size > 44

    # Los WAV de playback quedaron en el directorio compartido.
    shared = list((fast_settings / "tts_shared").glob("*.wav"))
    assert len(shared) >= 2


# ── mensajes de continuidad ("un momento por favor") mientras se procesa ──

def test_narration_matches_the_process_actually_running():
    """La narración corresponde al PROCESO real y su texto lo decide la capa
    conversacional, con formulaciones que no se repiten consecutivamente."""
    from types import SimpleNamespace

    from services.telephony.session_store import (
        STATE_CONFIRMING_ORIGIN,
        STATE_WAITING_GEO_CONTEXT,
        STATE_WAITING_ORIGIN,
    )
    from services.voice.conversation import PHRASE_BANK, SpeechIntent
    from services.voice.runtime import VoiceCallRuntime

    rt = VoiceCallRuntime(FakeWebSocket())

    assert rt._narration_kind(
        STATE_WAITING_ORIGIN, "cra 17 #6e-20",
        SimpleNamespace(best_pickup="cra 17 #6e-20"),
    ) == "address"
    assert rt._narration_kind(
        STATE_WAITING_ORIGIN, "el campanario",
        SimpleNamespace(best_pickup="el campanario"),
    ) == "place"
    assert rt._narration_kind(
        STATE_WAITING_GEO_CONTEXT, "por la iglesia",
        SimpleNamespace(best_pickup="por la iglesia"),
    ) == "geo_context"
    assert rt._narration_kind(
        STATE_CONFIRMING_ORIGIN, "sí", SimpleNamespace(best_pickup=None)
    ) == "generic"

    # El texto narrado sale del banco de la categoría correspondiente y nunca
    # se repite dos veces seguidas dentro de la misma llamada.
    allowed = {
        part
        for phrase in PHRASE_BANK["narrate_address"]
        for part in phrase.parts
    }
    previous = None
    for _ in range(12):
        request = rt.conversation.narration("address")
        assert request.intent is SpeechIntent.NARRATE and request.did_work
        plan = rt.conversation.plan(request)
        narrated = [s.text for s in plan.speech_segments if s.text in allowed]
        assert narrated, "la narración no salió del banco de 'address'"
        assert narrated[0] != previous
        previous = narrated[0]


def test_closed_mic_drops_turns_and_audio():
    """Con la escucha cerrada: ni se encolan turnos, ni se envía audio al STT,
    ni se evalúa barge-in. Al reabrir, la escucha se reanuda."""
    from services.audio import CaptureEnhancer
    from services.voice.endpointing import TurnReady
    from services.voice.runtime import VoiceCallRuntime

    rt = VoiceCallRuntime(FakeWebSocket())

    class _Rec:
        def __init__(self):
            self.user = 0

        def add_user_audio(self, pcm):
            self.user += len(pcm)

    class _STT:
        def __init__(self):
            self.sent = 0

        async def send_audio(self, pcm):
            self.sent += len(pcm)

    rt.recorder = _Rec()
    rt.stt = _STT()
    # Este test aísla la compuerta de escucha: el pipeline de audio se apaga para
    # que el resultado no dependa de si la señal de prueba es voz reconocible
    # (con el pipeline activo, un patrón sintético sin voz se silencia a propósito
    # — eso se prueba aparte, en tests/test_audio_pipeline.py).
    rt.enhancer = CaptureEnhancer(rate=8000, enabled=False)

    rt._close_mic()
    assert rt._mic_open is False

    # TurnReady no encola nada mientras la escucha está cerrada.
    asyncio.run(rt._dispatch_signal(TurnReady(text="calle 5 numero 3 45", confidence=0.9)))
    assert rt._turn_queue.empty()

    # El audio entrante se graba a disco pero NO llega al STT (sin escucha).
    asyncio.run(rt._on_audio(b"\x00\x01" * 100))
    assert rt.recorder.user > 0
    assert rt.stt.sent == 0

    # Reabrir reanuda el envío al STT.
    rt._open_mic()
    assert rt._mic_open is True
    asyncio.run(rt._on_audio(b"\x00\x01" * 100))
    assert rt.stt.sent > 0


def test_costly_turn_emits_message_before_processing(fast_settings, monkeypatch):
    """Un turno costoso emite el mensaje de espera ANTES de procesar: aparece un
    uuid_broadcast (el mensaje) mientras el pipeline lento aún corre, y luego la
    respuesta — al menos 2 broadcasts nuevos tras el saludo."""
    import services.voice.runtime as rt

    FakeSTT.instances.clear()
    monkeypatch.setattr(rt, "OpenAIRealtimeSTT", FakeSTT)

    fake_esl = FakeESLClient()
    monkeypatch.setattr(rt, "get_esl_client", lambda: fake_esl)

    tts = StreamingTTS()

    async def fake_synth(norm_text):
        yield b"\x00\x01" * 800

    monkeypatch.setattr(tts, "_synthesize_stream", fake_synth)

    nlu = TurnNLU()
    nlu._client = None

    class SlowGeocoder:
        def prewarm(self, query, attempt=1):
            pass

        async def resolve(self, query, attempt=1):
            await asyncio.sleep(0.3)   # procesamiento perceptiblemente lento
            return SimpleNamespace(
                status=ResolutionStatus.RESOLVED,
                selected=SimpleNamespace(neighborhood="Pubenza"),
                attempt=attempt,
                disambiguation_question=None,
            )

    store = SessionStore()
    orch = TurnOrchestrator(backend=FakeBackend(), geocoder=SlowGeocoder())
    ws = FakeWebSocket()
    runtime = VoiceCallRuntime(ws, store=store, orchestrator=orch, tts=tts, nlu=nlu)

    async def scenario():
        run_task = asyncio.create_task(runtime.run())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if fake_esl.broadcasts:
                break
        # Micrófono reabierto tras el saludo antes de inyectar el turno.
        for _ in range(200):
            await asyncio.sleep(0.02)
            if runtime._mic_open:
                break
        greeting_count = len(fake_esl.broadcasts)
        stt = FakeSTT.instances[0]
        stt.queue.put_nowait(_final("buenas estoy en pubenza por favor"))
        # Mensaje de espera + respuesta → ≥2 broadcasts nuevos tras el saludo.
        for _ in range(300):
            await asyncio.sleep(0.02)
            if len(fake_esl.broadcasts) - greeting_count >= 2:
                break
        assert len(fake_esl.broadcasts) - greeting_count >= 2
        for _ in range(200):
            await asyncio.sleep(0.02)
            sess = store.get("e2e-uuid")
            if sess and sess.state == "confirming_origin" and runtime._mic_open:
                break
        sess = store.get("e2e-uuid")
        assert sess is not None and sess.state == "confirming_origin"
        ws.push_disconnect()
        await asyncio.wait_for(run_task, timeout=5)

    asyncio.run(scenario())


def test_tts_cache_is_shared_between_calls():
    """Una instancia de TTS por llamada dejaba la caché vacía siempre: cada
    aviso se resintetizaba desde cero y llegaba tarde."""
    from services.voice.runtime import VoiceCallRuntime, get_shared_tts

    a = VoiceCallRuntime(FakeWebSocket(call_uuid="a"))
    b = VoiceCallRuntime(FakeWebSocket(call_uuid="b"))
    assert a.tts is b.tts is get_shared_tts()


def test_announcement_waits_until_it_is_actually_audible():
    """El aviso tiene que estar sonando ANTES de arrancar el trabajo pesado: el
    procesamiento incluye CPU síncrona que bloquea el bucle de eventos."""
    from services.voice.runtime import VoiceCallRuntime

    rt = VoiceCallRuntime(FakeWebSocket())

    async def scenario():
        # Mientras no se ordene la reproducción, la espera no retorna.
        rt._broadcast_event.clear()
        never = asyncio.create_task(asyncio.sleep(10))
        try:
            waiter = asyncio.create_task(rt._wait_until_audible(never, 5.0))
            await asyncio.sleep(0.1)
            assert not waiter.done(), "siguió sin esperar a que el aviso sonara"

            # En cuanto el audio se ordena a reproducir, sigue de inmediato.
            rt._broadcast_event.set()
            await asyncio.wait_for(waiter, timeout=1.0)
        finally:
            never.cancel()

    asyncio.run(scenario())


def test_announcement_wait_gives_up_instead_of_blocking_the_turn():
    """Si la síntesis se demora, el procesamiento arranca igual: el tope existe
    para que un TTS lento nunca retrase el trabajo."""
    from services.voice.runtime import VoiceCallRuntime

    rt = VoiceCallRuntime(FakeWebSocket())

    async def scenario():
        rt._broadcast_event.clear()
        slow = asyncio.create_task(asyncio.sleep(10))
        try:
            loop = asyncio.get_running_loop()
            started = loop.time()
            await rt._wait_until_audible(slow, 0.2)
            assert 0.15 <= loop.time() - started < 1.0
        finally:
            slow.cancel()

    asyncio.run(scenario())
