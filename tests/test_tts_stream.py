"""TTS OpenAI: instrucciones en cada síntesis, remuestreo 24k→8k, caché."""

import asyncio

import pytest

from services.voice import tts_stream
from services.voice.tts_prompt import speech_instructions
from services.voice.tts_stream import (
    DEFAULT_VOICE,
    SAMPLE_RATE,
    StreamingTTS,
    TTSError,
    resolve_voice,
    stream_speech,
)


def _collect(tts, sentence):
    async def _run():
        chunks = []
        async for chunk in tts.synthesize_sentence(sentence):
            chunks.append(chunk)
        return chunks

    return asyncio.run(_run())


# ── Doble del cliente OpenAI ────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: list[bytes]):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_bytes(self, chunk_size=None):
        for chunk in self._payload:
            yield chunk


class _FakeSpeech:
    def __init__(self, payload, delay=0.0):
        self.calls: list[dict] = []
        self._payload = payload
        self._delay = delay

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._delay:

            class _Slow(_FakeResponse):
                async def iter_bytes(inner, chunk_size=None):  # noqa: N805
                    await asyncio.sleep(self._delay)
                    yield b"\x00\x00"

            return _Slow(self._payload)
        return _FakeResponse(self._payload)


class _FakeClient:
    def __init__(self, payload, delay=0.0):
        self.speech = _FakeSpeech(payload, delay)
        self.audio = type("_Audio", (), {})()
        self.audio.speech = type("_Speech", (), {})()
        self.audio.speech.with_streaming_response = self.speech


@pytest.fixture
def fake_client(monkeypatch):
    """Instala un cliente falso; devuelve una fábrica para elegir el payload."""

    holder: dict[str, _FakeClient] = {}

    def install(payload, delay=0.0):
        client = _FakeClient(payload, delay)
        holder["client"] = client
        monkeypatch.setattr(tts_stream, "get_tts_client", lambda: client)
        return client

    return install


def _pcm24k(seconds: float) -> bytes:
    """PCM16 mono a 24 kHz (lo que devuelve `response_format="pcm"`)."""
    return b"\x10\x00" * int(24000 * seconds)


# ── Caché y troceo ──────────────────────────────────────────────────────────


def test_cache_hit_avoids_synthesis(monkeypatch):
    tts = StreamingTTS(voice="coral")
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
    # La caché re-trocea en chunks de 20 ms (320 bytes).
    assert all(len(c) <= 320 for c in second)


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


# ── Contrato con el proveedor ───────────────────────────────────────────────


def test_every_request_carries_operator_instructions(fake_client):
    client = fake_client([_pcm24k(0.05)])
    tts = StreamingTTS(voice="coral")

    _collect(tts, "Hola, con mucho gusto te ayudo.")
    _collect(tts, "¿Dónde te recojo?")

    assert len(client.speech.calls) == 2
    for call in client.speech.calls:
        assert call["instructions"] == speech_instructions()
        assert "operadora telefónica colombiana" in call["instructions"]
        assert call["model"] == "gpt-4o-mini-tts"
        assert call["voice"] == "coral"
        assert call["response_format"] == "pcm"


def test_request_text_is_normalized_and_punctuated(fake_client):
    client = fake_client([_pcm24k(0.05)])
    tts = StreamingTTS()

    _collect(tts, "Listo te recojo en la Cra. 4 #70AN-09")

    sent = client.speech.calls[0]["input"]
    assert "carrera cuatro" in sent
    assert "#" not in sent
    assert not any(ch.isdigit() for ch in sent)
    assert sent.startswith("Listo,")  # coma de respiración
    assert sent.endswith(".")         # la frase cae, no queda suspendida


def test_pcm_is_resampled_to_8k(fake_client):
    fake_client([_pcm24k(0.5)])
    tts = StreamingTTS()

    chunks = _collect(tts, "Medio segundo de audio.")
    total = sum(len(c) for c in chunks)

    # 0,5 s a 24 kHz → 0,5 s a 8 kHz (un tercio de las muestras).
    assert total == int(0.5 * SAMPLE_RATE) * 2
    assert all(len(c) == 320 for c in chunks[:-1])  # 20 ms hacia el transporte


def test_split_sample_across_chunks_is_not_lost(fake_client):
    """El stream puede cortar un sample en dos: el byte suelto espera, no
    desalinea el resto del audio ni se convierte en un chasquido."""
    payload = _pcm24k(0.25)
    fake_client([payload[:1201], payload[1201:]])  # frontera impar
    tts = StreamingTTS()

    total = sum(len(c) for c in _collect(tts, "Cuarto de segundo."))
    assert total == int(0.25 * SAMPLE_RATE) * 2


def test_silent_provider_raises(fake_client):
    fake_client([])
    tts = StreamingTTS()

    with pytest.raises(TTSError):
        _collect(tts, "Nada de audio.")


def test_timeout_raises_tts_error(fake_client, monkeypatch):
    fake_client([b""], delay=0.5)
    monkeypatch.setattr(
        tts_stream.settings, "VOICE_TTS_TIMEOUT_SEC", 0.05, raising=False
    )
    tts = StreamingTTS()

    with pytest.raises(TTSError):
        _collect(tts, "Una síntesis que no termina.")


def test_provider_failure_becomes_tts_error(monkeypatch):
    class _Boom:
        def create(self, **kwargs):
            raise RuntimeError("503 upstream")

    client = type("_C", (), {})()
    client.audio = type("_A", (), {})()
    client.audio.speech = type("_S", (), {})()
    client.audio.speech.with_streaming_response = _Boom()
    monkeypatch.setattr(tts_stream, "get_tts_client", lambda: client)

    async def _run():
        async for _ in stream_speech("Hola.", voice="coral"):
            pass

    with pytest.raises(TTSError):
        asyncio.run(_run())


# ── Voces ───────────────────────────────────────────────────────────────────


def test_resolve_voice_accepts_model_voices():
    assert resolve_voice("coral") == "coral"
    assert resolve_voice("VERSE") == "verse"


def test_resolve_voice_rejects_foreign_names():
    # Nombres del motor anterior (Azure/edge) ya no existen: no pueden llegar al
    # proveedor como voz o la síntesis falla con 400 en plena llamada.
    assert resolve_voice("es-CO-SalomeNeural") == DEFAULT_VOICE
    assert resolve_voice(None) == DEFAULT_VOICE
    assert resolve_voice("") == DEFAULT_VOICE


def test_pace_changes_only_the_rhythm_hint():
    relaxed = speech_instructions()
    faster = speech_instructions(1.3)
    slower = speech_instructions(0.6)

    assert relaxed in faster and relaxed in slower
    assert "más ágil" in faster
    assert "más pausado" in slower


def test_instructions_forbid_the_wrong_voices():
    text = speech_instructions().lower()
    for banned in (
        "robótica", "asistente virtual", "gps", "narradora", "locutora",
        "comercial", "publicitaria", "mecánica", "ritmo uniforme",
        "entonación plana", "pronunciación inglesa", "acento estadounidense",
    ):
        assert banned in text, banned
    for asked in (
        "pausas naturales", "respiraciones", "entonación cálida",
        "profesional", "español de colombia", "preguntas",
    ):
        assert asked in text, asked
