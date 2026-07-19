"""Flujo completo del runtime V2 con transporte/STT/TTS simulados.

Simula una llamada real de punta a punta:
  conexión WS → saludo streaming → "estoy en pubenza" → confirmación →
  "sí" → creación de servicio (backend fake) → despedida → colgado.
"""

import asyncio
import json
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
    """WS mínimo compatible con FreeSwitchTransport."""

    def __init__(self, call_uuid="e2e-uuid", caller="3001234567"):
        self.query_params = {"call_uuid": call_uuid, "caller_number": caller}
        self.headers = {}
        self.scope = {"app": None}
        self.sent: list[dict] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def receive(self):
        return await self._inbox.get()

    async def send_text(self, text: str):
        self.sent.append(json.loads(text))

    async def close(self):
        self.closed = True
        await self._inbox.put({"type": "websocket.disconnect"})

    def push_disconnect(self):
        self._inbox.put_nowait({"type": "websocket.disconnect"})


class FakeSTT:
    """Reemplaza DeepgramLiveSTT: eventos inyectados por el test."""

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
    monkeypatch.setattr(settings, "FREESWITCH_ESL_ENABLED", False)
    monkeypatch.setattr(settings, "FREESWITCH_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "VOICE_PLAYBACK_LEAD_MS", 5000)  # sin sleeps de pacing
    monkeypatch.setattr(settings, "VOICE_SILENCE_PROMPT_SEC", 30.0)
    return tmp_path


def test_full_call_flow(fast_settings, monkeypatch):
    import services.voice.runtime as rt

    FakeSTT.instances.clear()
    monkeypatch.setattr(rt, "DeepgramLiveSTT", FakeSTT)

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

    async def scenario():
        run_task = asyncio.create_task(runtime.run())
        # Espera a que el saludo suene (mensajes streamAudio enviados).
        for _ in range(100):
            await asyncio.sleep(0.02)
            if ws.sent:
                break
        assert ws.sent, "el saludo nunca se envió"
        stt = FakeSTT.instances[0]

        # Turno 1: el usuario da el barrio envuelto en cortesía.
        stt.queue.put_nowait(_final("buenas estoy en pubenza por favor"))
        for _ in range(200):
            await asyncio.sleep(0.02)
            sess = store.get("e2e-uuid")
            if sess and sess.state == "confirming_origin":
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

    # Playback: todos los mensajes salientes son streamAudio válidos.
    assert all(m["type"] == "streamAudio" for m in ws.sent)
    assert all(m["data"]["sampleRate"] == 8000 for m in ws.sent)

    # La sesión terminal sobrevive con service_created (regla V1 preservada).
    sess = store.get("e2e-uuid")
    assert sess is not None and sess.service_created

    # Grabación server-side escrita (pista del bot presente).
    rec = fast_settings / "e2e-uuid.wav"
    assert rec.is_file() and rec.stat().st_size > 44
