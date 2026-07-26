"""TTS del canal navegador: mismo motor, mismas instrucciones, MP3 streaming."""

import asyncio

import pytest

from core import voice_engine
from core.voice_engine import VoiceEngine


@pytest.fixture
def engine(monkeypatch):
    """VoiceEngine con TTS habilitado y sin tocar el STT (no hay red)."""
    monkeypatch.setattr(
        VoiceEngine, "__init__", lambda self, api_key="": None, raising=True
    )
    eng = VoiceEngine()
    eng.tts_available = True
    return eng


@pytest.fixture
def captured(monkeypatch):
    """Sustituye el streaming del proveedor y registra cada llamada."""
    calls: list[dict] = []

    async def fake_stream(text, *, voice=None, response_format="pcm", pace=1.0, timeout=None):
        calls.append(
            {
                "text": text,
                "voice": voice,
                "response_format": response_format,
                "pace": pace,
            }
        )
        yield b"ID3mp3-1"
        yield b"mp3-2"

    monkeypatch.setattr(voice_engine, "stream_speech", fake_stream)
    return calls


def test_synthesize_returns_mp3_from_openai(engine, captured):
    result = asyncio.run(engine.synthesize("**Hola!** Soy Lyra", voice="coral"))

    assert result["success"] is True
    assert result["format"] == "mp3"
    assert result["audio_bytes"] == b"ID3mp3-1mp3-2"
    assert captured[0]["response_format"] == "mp3"
    assert captured[0]["voice"] == "coral"
    assert "**" not in captured[0]["text"]  # markdown fuera antes de hablar


def test_synthesize_normalizes_text_for_spanish(engine, captured):
    asyncio.run(engine.synthesize("Te espero en la Cra. 4 #70AN-09", voice="coral"))

    sent = captured[0]["text"]
    assert "carrera cuatro" in sent
    assert not any(ch.isdigit() for ch in sent)


def test_speed_travels_as_rhythm_not_as_api_speed(engine, captured):
    # gpt-4o-mini-tts ignora `speed`: el ritmo se pide en las instrucciones.
    asyncio.run(engine.synthesize("Hola", voice="coral", speed=1.4))
    assert captured[0]["pace"] == 1.4


def test_stream_yields_provider_chunks(engine, captured):
    async def _run():
        return [c async for c in engine.synthesize_stream("Hola", voice="sage")]

    chunks = asyncio.run(_run())
    assert chunks == [b"ID3mp3-1", b"mp3-2"]
    assert captured[0]["response_format"] == "mp3"


def test_synthesize_to_bytes_concatenates(engine, captured):
    audio = asyncio.run(engine.synthesize_to_bytes("Hola", voice="nova"))
    assert audio == b"ID3mp3-1mp3-2"


def test_empty_text_never_reaches_the_provider(engine, captured):
    result = asyncio.run(engine.synthesize("   "))
    assert result["success"] is False
    assert captured == []


def test_provider_failure_is_reported_not_raised(engine, monkeypatch):
    async def boom(text, **kwargs):
        raise voice_engine.TTSError("sin credencial")
        yield b""

    monkeypatch.setattr(voice_engine, "stream_speech", boom)

    result = asyncio.run(engine.synthesize("Hola"))
    assert result["success"] is False
    assert "sin credencial" in result["error"]
