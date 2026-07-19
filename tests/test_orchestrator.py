"""Turn Orchestrator: los estados de negocio de V1 se preservan exactamente."""

import asyncio
from types import SimpleNamespace

import pytest

from core.geo_types import ResolutionStatus
from services.telephony.session_store import (
    CallSession,
    STATE_CONFIRMING_ORIGIN,
    STATE_CREATING_SERVICE,
    STATE_FINISHED,
    STATE_WAITING_GEO_CONTEXT,
    STATE_WAITING_ORIGIN,
)
from services.voice.nlu import NLUResult
from services.voice.orchestrator import (
    DTMF_BARRIO_MAP,
    GREETING,
    TurnOrchestrator,
    VoiceAction,
)


def _nlu(intent, pickup=None, landmark=None, conf=0.9):
    return NLUResult(
        intent=intent,
        pickup_span=pickup,
        destination_span=None,
        landmark_reference=landmark,
        pickup_confidence=conf if (pickup or landmark) else 0.0,
        destination_confidence=0.0,
        source="llm",
    )


def _geo(status, barrio=None, attempt=1, question=None):
    selected = SimpleNamespace(neighborhood=barrio) if barrio else None
    return SimpleNamespace(
        status=status,
        selected=selected,
        attempt=attempt,
        disambiguation_question=question,
    )


class FakeGeocoder:
    """Sustituto del SpeculativeGeocoder: devuelve resultados programados."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []
        self.prewarmed = []

    def prewarm(self, query, attempt=1):
        self.prewarmed.append((query, attempt))

    async def resolve(self, query, attempt=1):
        self.calls.append((query, attempt))
        if self.results:
            return self.results.pop(0)
        return _geo(ResolutionStatus.FAILED, attempt=attempt)


class FakeBackend:
    def __init__(self, ok=True, msg="Te enviaremos los datos del conductor..."):
        self.ok = ok
        self.msg = msg
        self.calls = []

    async def create_service_from_geocoded(self, **kwargs):
        self.calls.append(kwargs)
        return self.ok, self.msg


def _orch(geo=None, backend=None):
    return TurnOrchestrator(
        backend=backend or FakeBackend(), geocoder=geo or FakeGeocoder()
    )


def _session(**kw):
    defaults = dict(call_uuid="t-uuid", caller_phone="+573001112233")
    defaults.update(kw)
    return CallSession(**defaults)


def run(coro):
    return asyncio.run(coro)


# ── entrada y saludo ──

def test_inbound_greeting():
    orch = _orch()
    s = _session()
    turn = orch.handle_inbound(s)
    assert turn.speak_text == GREETING
    assert s.state == STATE_WAITING_ORIGIN
    assert s.last_message == GREETING


def test_greeting_only_reasks_origin():
    orch = _orch()
    s = _session(state=STATE_WAITING_ORIGIN)
    turn = run(orch.process_turn(s, text="hola buenas", nlu=_nlu("greeting")))
    assert "dónde te recogemos" in turn.speak_text.lower()
    assert s.state == STATE_WAITING_ORIGIN


def test_chitchat_mid_flow_replays_question():
    orch = _orch()
    s = _session(
        state=STATE_WAITING_GEO_CONTEXT,
        geo_original_query="calle 8c # 17-55",
        last_message="¿En qué barrio queda?",
    )
    turn = run(orch.process_turn(s, text="ah bueno", nlu=_nlu("chitchat_only")))
    assert turn.speak_text == "¿En qué barrio queda?"
    assert s.state == STATE_WAITING_GEO_CONTEXT


# ── captura de origen ──

def test_pickup_span_resolved_and_confirmed():
    geo = FakeGeocoder([_geo(ResolutionStatus.RESOLVED, barrio="Pubenza")])
    orch = _orch(geo=geo)
    s = _session()
    turn = run(
        orch.process_turn(
            s,
            text="buenas si mira estoy aquí en pubenza por favor",
            nlu=_nlu("provide_pickup", pickup="pubenza"),
        )
    )
    assert s.state == STATE_CONFIRMING_ORIGIN
    assert s.origen_text  # capturado
    assert s.origen_barrio == "Pubenza"
    assert "correcto" in turn.speak_text.lower() or "recogemos" in turn.speak_text.lower()
    assert geo.calls  # se geocodificó (guard _NEVER_AUTOACCEPT ejercido)


def test_street_address_goes_to_geo_context_when_ambiguous():
    geo = FakeGeocoder([
        _geo(ResolutionStatus.CONTEXT_GATHERING, question="¿En qué barrio queda?")
    ])
    orch = _orch(geo=geo)
    s = _session()
    turn = run(
        orch.process_turn(
            s,
            text="estoy en la calle 16 numero 3 45",
            nlu=_nlu("provide_pickup", pickup="calle 16 numero 3 45"),
        )
    )
    assert s.state == STATE_WAITING_GEO_CONTEXT
    assert s.geo_original_query
    assert turn.speak_text == "¿En qué barrio queda?"


def test_unclear_without_span_asks_repair_not_address():
    orch = _orch()
    s = _session()
    turn = run(orch.process_turn(s, text="eh pues mire le cuento", nlu=_nlu("unclear")))
    assert s.state == STATE_WAITING_ORIGIN
    assert s.origen_text is None
    assert turn.speak_text  # reparación, no crash
    assert s.retry_count == 1


# ── contexto geográfico ──

def test_geo_context_failed_creates_with_barrio_handoff():
    geo = FakeGeocoder([_geo(ResolutionStatus.FAILED, attempt=2)])
    backend = FakeBackend()
    orch = _orch(geo=geo, backend=backend)
    s = _session(
        state=STATE_WAITING_GEO_CONTEXT,
        geo_original_query="calle 8c # 17-55",
        origen_text="calle 8c # 17-55",
        geo_attempt=1,
    )
    turn = run(
        orch.process_turn(
            s,
            text="eso queda en la esmeralda",
            nlu=_nlu("provide_pickup", pickup="la esmeralda"),
        )
    )
    assert s.state == STATE_CREATING_SERVICE
    assert turn.action == VoiceAction.CREATE_SERVICE
    assert s.origen_barrio  # barrio dado por el usuario
    assert "conductor" in turn.speak_text.lower()


# ── confirmación ──

def test_confirm_yes_creates_service_and_hangs_up():
    backend = FakeBackend()
    orch = _orch(backend=backend)
    s = _session(
        state=STATE_CONFIRMING_ORIGIN, origen_text="Pubenza", origen_barrio="Pubenza"
    )
    turn = run(orch.process_turn(s, text="sí señora", nlu=_nlu("confirm_yes")))
    assert turn.action == VoiceAction.CREATE_SERVICE
    assert s.state == STATE_CREATING_SERVICE

    final = run(orch.process_turn(s, text=""))
    assert final.action == VoiceAction.HANGUP
    assert final.backend_ok is True
    assert s.service_created and s.state == STATE_FINISHED
    assert backend.calls[0]["origen"] == "Pubenza"
    assert backend.calls[0]["celular"] == "+573001112233"


def test_trunk_number_blocked_as_customer():
    backend = FakeBackend()
    orch = _orch(backend=backend)
    s = _session(
        caller_phone="6028231111",
        state=STATE_CREATING_SERVICE,
        origen_text="Pubenza",
    )
    run(orch.process_turn(s, text=""))
    assert backend.calls[0]["celular"] is None


def test_backend_failure_resets_to_waiting_origin():
    backend = FakeBackend(ok=False, msg="Tuvimos un problema registrando tu servicio.")
    orch = _orch(backend=backend)
    s = _session(state=STATE_CREATING_SERVICE, origen_text="Pubenza")
    turn = run(orch.process_turn(s, text=""))
    assert turn.backend_ok is False
    assert s.state == STATE_WAITING_ORIGIN
    assert s.origen_text is None


def test_confirm_no_resets_capture():
    orch = _orch()
    s = _session(
        state=STATE_CONFIRMING_ORIGIN, origen_text="Pubenza", origen_barrio="Pubenza"
    )
    turn = run(orch.process_turn(s, text="no", nlu=_nlu("confirm_no")))
    assert s.state == STATE_WAITING_ORIGIN
    assert s.origen_text is None and s.origen_barrio is None
    assert "barrio o la dirección" in turn.speak_text


def test_correction_overrides_slot_and_reconfirms():
    orch = _orch()
    s = _session(state=STATE_CONFIRMING_ORIGIN, origen_text="Pubenza")
    turn = run(
        orch.process_turn(
            s,
            text="no espere mejor en la carrera quinta",
            nlu=_nlu("correction", pickup="la carrera quinta"),
        )
    )
    assert s.state == STATE_CONFIRMING_ORIGIN
    assert s.origen_text != "Pubenza"  # slot sobrescrito (DST override)
    assert "sí" in turn.speak_text.lower()


def test_implicit_confirm_short_ack():
    backend = FakeBackend()
    orch = _orch(backend=backend)
    s = _session(
        state=STATE_CONFIRMING_ORIGIN, origen_text="Pubenza", origen_barrio="Pubenza"
    )
    turn = run(orch.process_turn(s, text="listo pues", nlu=_nlu("chitchat_only")))
    assert turn.action == VoiceAction.CREATE_SERVICE
    assert s.state == STATE_CREATING_SERVICE


def test_implicit_confirm_rejected_for_long_or_place():
    orch = _orch()
    s = _session(
        state=STATE_CONFIRMING_ORIGIN, origen_text="Pubenza", origen_barrio="Pubenza"
    )
    turn = run(
        orch.process_turn(
            s,
            text="es que yo estaba esperando hace rato afuera",
            nlu=_nlu("unclear"),
        )
    )
    assert turn.action == VoiceAction.LISTEN
    assert s.state == STATE_CONFIRMING_ORIGIN


# ── DTMF, silencio, terminales ──

def test_dtmf_selects_barrio():
    orch = _orch()
    s = _session()
    turn = run(orch.process_turn(s, text="", digits="6"))
    assert s.origen_text == DTMF_BARRIO_MAP["6"]
    assert s.state == STATE_CONFIRMING_ORIGIN
    assert "Valle del Ortigal" in turn.speak_text


def test_silence_ladder_ends_in_hangup():
    orch = _orch()
    s = _session()
    t1 = orch.handle_silence(s)
    t2 = orch.handle_silence(s)
    t3 = orch.handle_silence(s)
    assert t1.action == VoiceAction.LISTEN
    assert t2.action == VoiceAction.LISTEN
    assert t3.action == VoiceAction.HANGUP


def test_terminal_state_hangs_up():
    orch = _orch()
    s = _session(state=STATE_FINISHED)
    turn = run(orch.process_turn(s, text="hola", nlu=_nlu("greeting")))
    assert turn.action == VoiceAction.HANGUP


def test_repeat_request_replays_last_message():
    orch = _orch()
    s = _session(last_message="¿Pubenza es correcto?")
    turn = run(orch.process_turn(s, text="¿me repites?", nlu=_nlu("repeat_request")))
    assert turn.speak_text == "¿Pubenza es correcto?"


# ── barge-in: truncado de historial ──

def test_note_partial_delivery_truncates_history():
    orch = _orch()
    s = _session(last_message="El punto es Pubenza. ¿Me confirmas?")
    orch.note_partial_delivery(s, "El punto es Pubenza.")
    assert s.last_message == "El punto es Pubenza."


# ── geocoding especulativo ──

def test_prewarm_origin_only_with_confident_span():
    geo = FakeGeocoder()
    orch = _orch(geo=geo)
    s = _session()
    orch.prewarm_origin(s, "estoy en pubenza", _nlu("provide_pickup", pickup="pubenza", conf=0.9))
    assert geo.prewarmed
    geo2 = FakeGeocoder()
    orch2 = _orch(geo=geo2)
    orch2.prewarm_origin(s, "eh", _nlu("unclear"))
    assert not geo2.prewarmed


def test_speculative_geocoder_reuses_prewarmed_task(monkeypatch):
    import services.voice.orchestrator as om

    calls = []

    async def fake_pipeline(query, attempt=1):
        calls.append((query, attempt))
        return _geo(ResolutionStatus.RESOLVED, barrio="Centro", attempt=attempt)

    monkeypatch.setattr(om, "run_pipeline", fake_pipeline)

    async def scenario():
        geo = om.SpeculativeGeocoder()
        geo.prewarm("pubenza", attempt=1)
        await asyncio.sleep(0)  # deja arrancar la tarea
        result = await geo.resolve("Pubenza", attempt=1)  # clave normalizada
        return result

    result = asyncio.run(scenario())
    assert result.selected.neighborhood == "Centro"
    assert len(calls) == 1  # una sola ejecución: la especulativa se reutilizó
