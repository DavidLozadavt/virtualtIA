"""Regresión: uuid_kill debe completarse aunque _hangup() se cancele a mitad
de camino (bug real 2026-07-19 — el canal no colgaba, mod_audio_stream
reabría el WS y Lyra volvía a saludar en la sesión ya terminada)."""

import asyncio

from services.telephony.session_store import CallSession, STATE_FINISHED
from services.voice.runtime import VoiceCallRuntime


class SlowFakeESL:
    """uuid_kill lento — deja tiempo de cancelar la tarea que lo espera."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.kills: list[str] = []

    async def uuid_kill(self, call_uuid, cause="NORMAL_CLEARING"):
        await asyncio.sleep(self.delay)
        self.kills.append(call_uuid)
        return True

    async def uuid_broadcast(self, call_uuid, media_uri, leg="aleg"):
        return True

    async def uuid_break(self, call_uuid):
        return True


class DummyTransport:
    call_uuid = "race-uuid"
    closed = False

    async def close(self):
        self.closed = True


def _runtime(monkeypatch, esl):
    import services.voice.runtime as rt
    from core.config import settings

    monkeypatch.setattr(settings, "FREESWITCH_ESL_ENABLED", True)
    monkeypatch.setattr(rt, "get_esl_client", lambda: esl)

    runtime = VoiceCallRuntime.__new__(VoiceCallRuntime)
    runtime.transport = DummyTransport()
    runtime._ending = False
    runtime.session = CallSession(call_uuid="race-uuid", state=STATE_FINISHED)
    return runtime


def test_kill_channel_survives_task_cancellation(monkeypatch):
    """asyncio.shield debe dejar correr uuid_kill aunque la tarea que lo
    invoca se cancele mientras está en vuelo."""
    esl = SlowFakeESL(delay=0.05)
    runtime = _runtime(monkeypatch, esl)

    async def scenario():
        task = asyncio.create_task(runtime._kill_channel())
        await asyncio.sleep(0.01)  # dejar arrancar el uuid_kill (ya en vuelo)
        task.cancel()  # simula la carrera: algo cancela la tarea contenedora
        try:
            await task
        except asyncio.CancelledError:
            pass
        # El uuid_kill blindado con shield debe completar igual, aunque la
        # tarea que lo esperaba ya se haya cancelado.
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    assert esl.kills == ["race-uuid"]


def test_shutdown_kills_channel_even_if_hangup_never_ran(monkeypatch):
    """Red de seguridad: _shutdown() cuelga el canal aunque _hangup() nunca
    se haya ejecutado."""
    esl = SlowFakeESL(delay=0.0)
    runtime = _runtime(monkeypatch, esl)
    runtime.store = None
    runtime.stt = None
    runtime.recorder = None
    runtime.orchestrator = None
    runtime._tasks = []
    runtime._playout_task = None
    runtime.session = None  # evita tocar store.save en _shutdown

    asyncio.run(runtime._shutdown())
    assert esl.kills == ["race-uuid"]
