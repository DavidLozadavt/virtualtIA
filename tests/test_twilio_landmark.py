"""
tests/test_twilio_landmark.py

Equivalente Twilio del cierre de gap: en process_speech (estado waiting_origin)
un nombre propio / landmark (is_street=False) ahora también pasa por
run_pipeline y el guard _NEVER_AUTOACCEPT, manteniendo intactos is_street y los
shortcuts del catálogo (trusted_origin).

Los builders de TwiML/TTS se stubbean para no tocar red ni edge-tts; sólo se
verifica el estado de la sesión resultante.
"""

import types
import asyncio

import pytest

import api.routers.twilio as tw
from core.geo_types import (
    GeoCandidate, GeoResolution, LocationType, ResolutionStatus,
)


class _FakeReq:
    def __init__(self, data):
        self._d = data
        self.query_params = {}
        self.headers = {"x-forwarded-host": "example.com", "x-forwarded-proto": "https"}
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(http_client=None)
        )

    async def form(self):
        return self._d


@pytest.fixture
def twilio_env(monkeypatch):
    """Stubs ligeros: sin TwiML real, sin TTS, sin red."""
    async def _fake_gather(msg, *a, **k):
        return msg

    monkeypatch.setattr(tw, "_twiml_gather_adaptive", _fake_gather)
    monkeypatch.setattr(tw, "_twiml_response", lambda x: x)
    monkeypatch.setattr(tw, "_get_process_speech_url", lambda *a, **k: "http://x/process_speech")

    calls = {"run_pipeline": 0}
    monkeypatch.setattr(tw, "_RUN_PIPELINE_CALLS", calls, raising=False)
    return calls


def _seed_session(call_sid):
    s = tw.get_session(call_sid)
    s.state = "waiting_origin"
    s.origen_text = None
    s.origen_barrio = None
    s.retry_count = 0
    return s


def _resolved(loc_type=LocationType.GEOMETRIC_CENTER, barrio=None):
    c = GeoCandidate(
        lat=2.44, lng=-76.61, display_name="x, Popayán", source="google",
        location_type=loc_type, confidence=0.5, neighborhood=barrio,
    )
    return GeoResolution(
        status=ResolutionStatus.RESOLVED, query="x", attempt=1, selected=c,
    )


def _context():
    return GeoResolution(
        status=ResolutionStatus.CONTEXT_GATHERING, query="x", attempt=1,
        disambiguation_question="¿En qué barrio o referencia cercana queda?",
    )


def test_twilio_trusted_landmark_resolved_confirms(twilio_env, monkeypatch):
    _seed_session("TW-LM-1")

    async def _rp(query, attempt=1):
        twilio_env["run_pipeline"] += 1
        return _resolved(LocationType.GEOMETRIC_CENTER, barrio=None)

    monkeypatch.setattr(tw, "run_pipeline", _rp)

    req = _FakeReq({"CallSid": "TW-LM-1",
                    "SpeechResult": "Terminal de Transportes",
                    "Confidence": "0.9"})
    asyncio.run(tw.process_speech(req))

    # El landmark ahora geocodifica en la captura inicial y confirma.
    assert twilio_env["run_pipeline"] == 1
    assert tw.get_session("TW-LM-1").state == "confirming_origin"


def test_twilio_trusted_landmark_coarse_falls_to_plain_confirm(twilio_env, monkeypatch):
    # Catálogo confiable + precisión gruesa → NO nag de barrio (Q5).
    _seed_session("TW-LM-2")

    async def _rp(query, attempt=1):
        return _context()

    monkeypatch.setattr(tw, "run_pipeline", _rp)

    req = _FakeReq({"CallSid": "TW-LM-2",
                    "SpeechResult": "Valle del Ortigal",
                    "Confidence": "0.9"})
    asyncio.run(tw.process_speech(req))

    st = tw.get_session("TW-LM-2").state
    assert st == "confirming_origin"
    assert st != "waiting_geo_context"
