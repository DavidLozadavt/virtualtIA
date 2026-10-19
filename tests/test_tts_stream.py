"""TTS streaming: caché de frases y troceo (la síntesis real se aísla)."""

import asyncio

from services.voice.tts_stream import StreamingTTS


def _collect(tts, sentence):
    async def _run():
        chunks = []
        async for chunk in tts.synthesize_sentence(sentence):
            chunks.append(chunk)
        return chunks

    return asyncio.run(_run())


def test_cache_hit_avoids_synthesis(monkeypatch):
    tts = StreamingTTS(voice="es-CO-SalomeNeural")
    calls = []

    async def fake_stream(norm_text):
        calls.append(norm_text)
        yield b"\x00\x01" * 4000  # 8000 bytes = 0.5 s

    monkeypatch.setattr(tts, "_synthesize_stream", fake_stream)

    first = _collect(tts, "Un momento por favor...")
    assert len(calls) == 1
    second = _collect(tts, "Un momento por favor...")
    assert len(calls) == 1  # servido desde caché
    assert b"".join(first) == b"".join(second)
    # La caché re-trocea en chunks de ~200 ms (3200 bytes).
    assert all(len(c) <= 3200 for c in second)


def test_empty_text_produces_nothing(monkeypatch):
    tts = StreamingTTS()

    async def fake_stream(norm_text):  # pragma: no cover — no debe llamarse
        raise AssertionError("no debe sintetizar texto vacío")
        yield b""

    monkeypatch.setattr(tts, "_synthesize_stream", fake_stream)
    assert _collect(tts, "   ") == []


def test_normalization_shares_cache_key(monkeypatch):
    tts = StreamingTTS()
    calls = []

    async def fake_stream(norm_text):
        calls.append(norm_text)
        yield b"\x01\x02" * 100

    monkeypatch.setattr(tts, "_synthesize_stream", fake_stream)
    _collect(tts, "Cra. 4 #70AN-09")
    _collect(tts, "carrera cuatro número setenta A N nueve")
    assert len(calls) == 1  # ambas normalizan al mismo texto → una síntesis
