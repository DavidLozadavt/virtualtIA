"""
tests/test_voice_engine_confirm_match.py

Cubre Fix #1: Decision.CONFIRM (match dudoso) ya NO se trata como ACCEPT. Antes
de fijar el origen, el motor pregunta "¿Te refieres a X?" y consume la respuesta
sí/no en STATE_CONFIRMING_MATCH:
  - Sí          → fija X confiable y continúa el flujo normal.
  - No          → no fija; cae a repreguntar/LLM.
  - Sin claridad→ se trata como No.
  - Máx. 1 confirmación por candidato (skip_confirm_for evita re-preguntar).
"""

import asyncio

import services.telephony.voice_call_engine as vce
from services.telephony.session_store import (
    CallSession,
    STATE_WAITING_ORIGIN,
    STATE_CONFIRMING_MATCH,
    STATE_CONFIRMING_ORIGIN,
)
from core.geo_types import (
    GeoCandidate,
    GeoResolution,
    LocationType,
    ResolutionStatus,
)


class _Match:
    def __init__(self, canonical):
        self.canonical = canonical
        self.disambiguation_candidates = []


def _resolved(barrio=None):
    cand = GeoCandidate(
        lat=2.44, lng=-76.61, display_name="x, Popayán",
        source="google", location_type=LocationType.GEOMETRIC_CENTER,
        confidence=0.5, neighborhood=barrio,
    )
    return GeoResolution(
        status=ResolutionStatus.RESOLVED, query="x", attempt=1, selected=cand,
    )


def _async_pipeline(barrio="Campanario"):
    async def _spy(query, attempt=1):
        return _resolved(barrio=barrio)
    return _spy


def _engine():
    return vce.VoiceCallEngine()


def _session(state=STATE_WAITING_ORIGIN):
    return CallSession(call_uuid="t-confirm", state=state)


def _patch_confirm(monkeypatch, canonical="Campanario"):
    monkeypatch.setattr(vce, "resolve_location_entity", lambda *a, **k: _Match(canonical))
    monkeypatch.setattr(vce, "decide", lambda m: vce.Decision.CONFIRM)
    monkeypatch.setattr(vce, "is_filler", lambda t: False)
    monkeypatch.setattr(vce, "looks_like_place", lambda t: True)


# ── CONFIRM pregunta, NO fija ────────────────────────────────────────────────

def test_confirm_match_asks_and_does_not_fix_origin(monkeypatch):
    _patch_confirm(monkeypatch)
    engine, session = _engine(), _session()

    res = asyncio.run(engine._handle_waiting_origin(session, "campanaryo", 0.4))

    assert session.state == STATE_CONFIRMING_MATCH
    assert session.pending_match_confirmation == {"canonical": "Campanario"}
    assert session.origen_text is None          # NO se fijó el origen dudoso
    assert "Campanario" in res.speak_text
    assert res.action == vce.VoiceAction.LISTEN


# ── Sí → fija confiable y continúa flujo normal ──────────────────────────────

def test_confirm_match_yes_fixes_trusted_origin(monkeypatch):
    monkeypatch.setattr(vce, "run_pipeline", _async_pipeline("Campanario"))
    engine = _engine()
    session = _session(state=STATE_CONFIRMING_MATCH)
    session.pending_match_confirmation = {"canonical": "Campanario"}

    res = asyncio.run(engine._handle_confirming_match(session, "sí", 0.4))

    assert session.pending_match_confirmation is None
    assert session.state == STATE_CONFIRMING_ORIGIN
    assert session.origen_text and "campanario" in session.origen_text.lower()
    assert session.origen_barrio == "Campanario"
    assert res.action == vce.VoiceAction.LISTEN


# ── No → no fija; repregunta ─────────────────────────────────────────────────

def test_confirm_match_no_does_not_fix(monkeypatch):
    engine = _engine()
    session = _session(state=STATE_CONFIRMING_MATCH)
    session.pending_match_confirmation = {"canonical": "Campanario"}

    res = asyncio.run(engine._handle_confirming_match(session, "no", 0.4))

    assert session.pending_match_confirmation is None
    assert session.state == STATE_WAITING_ORIGIN
    assert session.origen_text is None          # negado → NO se fija
    assert res.action == vce.VoiceAction.LISTEN


# ── Sin respuesta clara → se trata como No ───────────────────────────────────

def test_confirm_match_unclear_treated_as_no(monkeypatch):
    # Respuesta ambigua: _parse_si_no devuelve None. Cae a waiting_origin con el
    # texto; se re-clasifica como nueva ubicación dudosa, pero skip_confirm_for
    # impide re-preguntar por el MISMO candidato → no vuelve a CONFIRMING_MATCH.
    _patch_confirm(monkeypatch, canonical="Campanario")

    async def _fake_llm(self, text):
        return "Campanario"

    monkeypatch.setattr(vce.VoiceCallEngine, "_extract_origin_llm", _fake_llm)
    monkeypatch.setattr(vce, "run_pipeline", _async_pipeline("Campanario"))

    engine = _engine()
    session = _session(state=STATE_CONFIRMING_MATCH)
    session.pending_match_confirmation = {"canonical": "Campanario"}

    res = asyncio.run(engine._handle_confirming_match(session, "el de allá no sé", 0.4))

    assert session.state != STATE_CONFIRMING_MATCH   # NO se re-pregunta
    assert res.action == vce.VoiceAction.LISTEN


# ── Máx 1 confirmación: skip_confirm_for evita re-preguntar mismo candidato ──

def test_skip_confirm_for_prevents_reask(monkeypatch):
    _patch_confirm(monkeypatch, canonical="Campanario")

    async def _fake_llm(self, text):
        return "Campanario"

    monkeypatch.setattr(vce.VoiceCallEngine, "_extract_origin_llm", _fake_llm)
    monkeypatch.setattr(vce, "run_pipeline", _async_pipeline("Campanario"))

    engine, session = _engine(), _session()

    res = asyncio.run(
        engine._handle_waiting_origin(
            session, "campanaryo", 0.4, skip_confirm_for="Campanario"
        )
    )

    assert session.state == STATE_CONFIRMING_ORIGIN   # pasó por finalize, no re-ask
    assert session.pending_match_confirmation is None
    assert res.action == vce.VoiceAction.LISTEN
