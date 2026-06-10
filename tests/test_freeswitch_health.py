"""Pruebas mínimas del router FreeSWITCH (sin credenciales reales)."""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_root_health():
    res = client.get("/health")
    assert res.status_code == 200


def test_freeswitch_health():
    res = client.get("/freeswitch/health")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["service"] == "lyra-intellitaxi-freeswitch"
    assert data["telephony_provider"] == "freeswitch"
    assert "backend_api" in data
    assert "ws_path" in data


def test_freeswitch_websocket_accepts_start(monkeypatch):
    """WS acepta conexión y evento start sin credenciales OpenAI/Groq."""
    fake_engine = MagicMock()
    fake_engine.stt_available = False
    fake_engine.synthesize_to_bytes = AsyncMock(return_value=b"\x00\x01\x02")

    monkeypatch.setattr(
        "services.telephony.providers.freeswitch_provider.get_voice_engine",
        lambda: fake_engine,
    )
    monkeypatch.setattr(
        "services.telephony.providers.freeswitch_provider.mp3_to_ulaw",
        lambda _mp3: b"\xff" * 320,
    )

    with client.websocket_connect("/freeswitch/audio") as ws:
        ws.send_json({
            "event": "start",
            "start": {
                "callId": "test-uuid-001",
                "from": "+573001234567",
                "sampleRate": 8000,
            },
        })
        ws.close()
