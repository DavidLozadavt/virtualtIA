"""Parseo de mensajes Deepgram, keywords del catálogo y URL de conexión."""

from urllib.parse import parse_qs, urlparse

from services.voice.stt_stream import (
    DeepgramLiveSTT,
    SpeechStartedEvent,
    TranscriptEvent,
    UtteranceEndEvent,
    build_keywords,
    parse_deepgram_message,
)


def test_parse_results_message():
    ev = parse_deepgram_message({
        "type": "Results",
        "is_final": True,
        "speech_final": True,
        "start": 1.2,
        "duration": 0.8,
        "channel": {
            "alternatives": [
                {"transcript": "estoy en pubenza", "confidence": 0.93}
            ]
        },
    })
    assert isinstance(ev, TranscriptEvent)
    assert ev.text == "estoy en pubenza"
    assert ev.is_final and ev.speech_final
    assert ev.confidence == 0.93


def test_parse_utterance_end_and_speech_started():
    assert isinstance(
        parse_deepgram_message({"type": "UtteranceEnd", "last_word_end": 2.1}),
        UtteranceEndEvent,
    )
    assert isinstance(
        parse_deepgram_message({"type": "SpeechStarted", "timestamp": 0.4}),
        SpeechStartedEvent,
    )
    assert parse_deepgram_message({"type": "Metadata"}) is None


def test_keywords_from_catalog():
    kws = build_keywords()
    assert 0 < len(kws) <= 100
    lowered = [k.lower() for k in kws]
    assert "pubenza" in lowered            # prioridad
    assert "pubensa" in lowered            # variante fonética real
    assert "calle" not in lowered          # genéricos filtrados
    assert all("," not in k for k in kws)  # tokens sueltos


def test_build_url_parameters():
    stt = DeepgramLiveSTT(call_uuid="u1")
    parsed = urlparse(stt.build_url())
    qs = parse_qs(parsed.query)
    assert parsed.scheme == "wss" and "deepgram" in parsed.netloc
    assert qs["model"] == ["nova-2"]
    assert qs["language"] == ["es-419"]
    assert qs["encoding"] == ["linear16"]
    assert qs["sample_rate"] == ["8000"]
    assert qs["interim_results"] == ["true"]
    assert qs["vad_events"] == ["true"]
    assert "endpointing" in qs and "utterance_end_ms" in qs
    assert all(":" in kw for kw in qs["keywords"])  # intensifier presente
