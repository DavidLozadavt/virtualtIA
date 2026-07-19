"""Transporte mod_audio_stream: protocolo, identidad y playback."""

import base64
import json

from services.voice.transport import (
    build_stream_audio_message,
    resolve_call_uuid,
    resolve_caller_number,
)


def test_stream_audio_message_format():
    pcm = b"\x01\x02" * 160
    msg = json.loads(build_stream_audio_message(pcm))
    assert msg["type"] == "streamAudio"
    data = msg["data"]
    assert data["audioDataType"] == "raw"
    assert data["sampleRate"] == 8000
    assert base64.b64decode(data["audioData"]) == pcm


def test_resolve_identity_from_query():
    qp = {"call_uuid": "abc-123", "caller_number": "3001234567"}
    assert resolve_call_uuid(qp, {}) == "abc-123"
    assert resolve_caller_number(qp, {}) == "+573001234567"


def test_resolve_identity_from_start_metadata():
    data = {
        "event": "start",
        "start": {
            "callId": "uuid-9",
            "customParameters": {"caller_number": "573009876543"},
        },
    }
    assert resolve_call_uuid({}, {}, data) == "uuid-9"
    assert resolve_caller_number({}, {}, data) == "+573009876543"


def test_resolve_identity_headers_fallback():
    headers = {"x-call-uuid": "hdr-uuid", "x-caller-number": "3005556677"}
    assert resolve_call_uuid({}, headers) == "hdr-uuid"
    assert resolve_caller_number({}, headers) == "+573005556677"
