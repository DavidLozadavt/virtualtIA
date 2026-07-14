"""
tests/test_voice_engine_landmark.py

Cubre el cierre del gap en _handle_waiting_origin (FreeSWITCH): los nombres
propios / landmarks (is_street=False) ahora también pasan por run_pipeline y el
guard _NEVER_AUTOACCEPT en la captura inicial, manteniendo intactos is_street,
_ORIGIN_ADDRESS_OVERRIDES y los shortcuts del catálogo (trusted).
"""

import re
import asyncio

import pytest

import services.telephony.voice_call_engine as vce
from services.telephony.session_store import (
    CallSession,
    STATE_WAITING_ORIGIN,
    STATE_CONFIRMING_ORIGIN,
    STATE_WAITING_GEO_CONTEXT,
)
from core.geo_types import (
    GeoCandidate,
    GeoResolution,
    LocationType,
    ResolutionStatus,
)

_IS_STREET_RE = re.compile(r"(?:calle|carrera|cl|cra|kr|kra)\s*\.?\s*\d+")


def _resolved(loc_type=LocationType.GEOMETRIC_CENTER, barrio=None):
    cand = GeoCandidate(
        lat=2.44, lng=-76.61, display_name="x, Popayán",
        source="google", location_type=loc_type, confidence=0.5,
        neighborhood=barrio,
    )
    return GeoResolution(
        status=ResolutionStatus.RESOLVED, query="x", attempt=1, selected=cand,
    )


def _context():
    return GeoResolution(
        status=ResolutionStatus.CONTEXT_GATHERING, query="x", attempt=1,
        disambiguation_question="¿En qué barrio o referencia cercana queda?",
    )


def _engine():
    return vce.VoiceCallEngine()


def _session():
    return CallSession(call_uuid="t-landmark", state=STATE_WAITING_ORIGIN)


# ── "la paz" sigue EXACTAMENTE igual que hoy ─────────────────────────────────

def test_la_paz_override_constant_and_routes_as_street():
    # El override no cambió y su valor forzado ES una dirección de calle, así
    # que toma la rama is_street (vieja), NUNCA la nueva rama de landmark.
    forced = vce._ORIGIN_ADDRESS_OVERRIDES["la paz"]
    assert forced == "Cra. 4 #70AN-09, Popayán, Cauca"
    assert _IS_STREET_RE.search(forced.lower()) is not None


def test_la_paz_first_turn_asks_disambiguation_without_geocoding(monkeypatch):
    # "la paz" resuelve AMBIGUOUS (real) → pide desambiguación y NO geocodifica.
    called = {"run_pipeline": 0}

    async def _spy(query, attempt=1):
        called["run_pipeline"] += 1
        return _resolved()

    monkeypatch.setattr(vce, "run_pipeline", _spy)
    engine, session = _engine(), _session()

    res = asyncio.run(engine._handle_waiting_origin(session, "la paz", 0.9))

    assert called["run_pipeline"] == 0           # no geocode en el turno 1
    assert session.pending_disambiguation is not None
    assert res.action == vce.VoiceAction.LISTEN


# ── Landmark trusted (catálogo ACCEPT) → ahora SÍ geocodifica en captura ─────

def test_trusted_landmark_resolved_confirms(monkeypatch):
    called = {"run_pipeline": 0}

    async def _spy(query, attempt=1):
        called["run_pipeline"] += 1
        return _resolved(LocationType.GEOMETRIC_CENTER, barrio=None)

    monkeypatch.setattr(vce, "run_pipeline", _spy)
    engine, session = _engine(), _session()

    res = asyncio.run(
        engine._handle_waiting_origin(session, "Terminal de Transportes", 0.9)
    )

    assert called["run_pipeline"] == 1           # landmark ahora geocodifica
    assert session.state == STATE_CONFIRMING_ORIGIN
    assert res.action == vce.VoiceAction.LISTEN


def test_trusted_landmark_coarse_falls_to_plain_confirm(monkeypatch):
    # Catálogo confiable + precisión gruesa: NO degradar a pedir barrio; se
    # confirma el nombre (mismo shortcut que ya funcionaba). Q5.
    async def _spy(query, attempt=1):
        return _context()

    monkeypatch.setattr(vce, "run_pipeline", _spy)
    engine, session = _engine(), _session()

    res = asyncio.run(
        engine._handle_waiting_origin(session, "Valle del Ortigal", 0.9)
    )

    assert session.state == STATE_CONFIRMING_ORIGIN          # confirm plano
    assert session.state != STATE_WAITING_GEO_CONTEXT        # no nag de barrio
    assert res.action == vce.VoiceAction.LISTEN


# ── Landmark NO confiable + precisión gruesa → pedir barrio/referencia ───────

def test_untrusted_landmark_coarse_asks_context(monkeypatch):
    class _M:
        canonical = None
        disambiguation_candidates = []

    monkeypatch.setattr(vce, "resolve_location_entity", lambda *a, **k: _M())
    monkeypatch.setattr(vce, "decide", lambda m: vce.Decision.REJECT)
    monkeypatch.setattr(vce, "is_filler", lambda t: False)
    monkeypatch.setattr(vce, "looks_like_place", lambda t: True)

    async def _fake_llm(self, text):
        return "Parque Industrial Norte"

    monkeypatch.setattr(vce.VoiceCallEngine, "_extract_origin_llm", _fake_llm)

    async def _spy(query, attempt=1):
        return _context()

    monkeypatch.setattr(vce, "run_pipeline", _spy)
    engine, session = _engine(), _session()

    res = asyncio.run(
        engine._handle_waiting_origin(session, "el parque industrial ese", 0.9)
    )

    assert session.state == STATE_WAITING_GEO_CONTEXT
    assert res.action == vce.VoiceAction.LISTEN


# ── Confirmación: no crear servicio sin señal afirmativa real ────────────────

def _confirming_session():
    s = CallSession(call_uuid="t-confirm", state=STATE_CONFIRMING_ORIGIN)
    s.origen_text = "Valle del Ortigal"
    s.origen_barrio = "Valle del Ortigal"
    return s


def test_confirm_garbage_long_does_not_create_service():
    # Alucinación de STT sobre silencio: frase larga que no es sí/no ni lugar.
    # NO debe crear el servicio (bug: implicit confirm lo creaba sin "sí").
    engine, session = _engine(), _confirming_session()
    res = asyncio.run(
        engine._handle_confirming_origin(
            session, "Subtitulos realizados por la comunidad de Amara org", 1.0
        )
    )
    assert res.action == vce.VoiceAction.LISTEN
    assert session.state == STATE_CONFIRMING_ORIGIN


def test_confirm_short_ack_creates_service():
    # Ack coloquial corto (≤3 palabras) sí es confirmación implícita (anti-bucle).
    engine, session = _engine(), _confirming_session()
    res = asyncio.run(engine._handle_confirming_origin(session, "de una", 1.0))
    assert res.action == vce.VoiceAction.CREATE_SERVICE


def test_confirm_explicit_yes_creates_service():
    engine, session = _engine(), _confirming_session()
    res = asyncio.run(engine._handle_confirming_origin(session, "sí", 1.0))
    assert res.action == vce.VoiceAction.CREATE_SERVICE
