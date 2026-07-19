"""STT del canal de navegador (core/voice_engine): el sistema usa
gpt-4o-mini-transcribe de forma exclusiva.

Regresión: whisper-1 mishearía nombres de barrios ("Valle del Ortigal" →
"Hortigal"/variantes) rompiendo la resolución de direcciones. Estos tests
blindan que ninguna ruta pueda volver a seleccionar whisper-1 y que la llamada
a OpenAI use response_format="json" (único formato soportado por el modelo).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core.voice_engine import STT_MODEL, VoiceEngine


def _engine_with_openai_key(monkeypatch) -> VoiceEngine:
    from core.config import settings
    monkeypatch.setattr(settings, "OPENAI_WHISPER_KEY", "sk-test-openai", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test-openai", raising=False)
    return VoiceEngine(api_key="sk-test-openai")


def test_stt_model_is_gpt_4o_mini_transcribe(monkeypatch):
    engine = _engine_with_openai_key(monkeypatch)
    assert engine.stt_model == "gpt-4o-mini-transcribe"
    assert STT_MODEL == "gpt-4o-mini-transcribe"
    assert engine.stt_available is True


def test_openrouter_key_disables_stt(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "OPENAI_WHISPER_KEY", "", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-or-v1-xxxx", raising=False)
    engine = VoiceEngine(api_key="sk-or-v1-xxxx")
    # OpenRouter no soporta audio → STT deshabilitado, nunca cae a whisper-1.
    assert engine.stt_available is False
    assert engine.stt_model == "gpt-4o-mini-transcribe"


def test_transcribe_calls_openai_with_gpt4o_and_json(monkeypatch):
    engine = _engine_with_openai_key(monkeypatch)

    create = AsyncMock(return_value=SimpleNamespace(text="estoy en el valle del ortigal"))
    engine.openai_client = MagicMock()
    engine.openai_client.audio.transcriptions.create = create

    result = asyncio.run(
        engine.transcribe(audio_bytes=b"\x00" * 4000, language="es", content_type="audio/wav")
    )

    assert result["success"] is True
    assert result["text"] == "estoy en el valle del ortigal"

    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini-transcribe"
    assert kwargs["response_format"] == "json"     # verbose_json NO soportado por el modelo
    assert kwargs["model"] != "whisper-1"


def test_transcribe_has_no_stt_model_override():
    # La firma no acepta stt_model: ninguna ruta (router/yaml) puede forzar whisper-1.
    import inspect

    params = inspect.signature(VoiceEngine.transcribe).parameters
    assert "stt_model" not in params
