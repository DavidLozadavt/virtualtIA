"""Parseo de eventos OpenAI Realtime, codificación g711 μ-law y prompt de sesgo.

El STT telefónico migró de Deepgram a OpenAI Realtime (gpt-4o-mini-transcribe)
manteniendo los mismos eventos tipados que consume el endpointer.
"""

import struct

from services.voice.stt_stream import (
    OpenAIRealtimeSTT,
    SpeechStartedEvent,
    TranscriptEvent,
    build_prompt,
    pcm16_to_ulaw,
)


def _new_stt() -> OpenAIRealtimeSTT:
    return OpenAIRealtimeSTT(call_uuid="test-uuid")


def test_speech_started_maps_and_resets_interim():
    stt = _new_stt()
    stt._interim = "algo viejo"
    ev = stt._parse({"type": "input_audio_buffer.speech_started", "audio_start_ms": 400})
    assert isinstance(ev, SpeechStartedEvent)
    assert ev.timestamp == 0.4
    assert stt._interim == ""  # el nuevo enunciado limpia el acumulador


def test_delta_accumulates_into_interim():
    stt = _new_stt()
    e1 = stt._parse(
        {"type": "conversation.item.input_audio_transcription.delta", "delta": "estoy en "}
    )
    e2 = stt._parse(
        {"type": "conversation.item.input_audio_transcription.delta", "delta": "pubenza"}
    )
    assert isinstance(e1, TranscriptEvent) and not e1.is_final
    assert e1.text == "estoy en"
    # el delta es incremental: el interim acumula, no reemplaza
    assert e2.text == "estoy en pubenza"
    assert not e2.speech_final


def test_completed_is_final_and_resets():
    stt = _new_stt()
    stt._parse(
        {"type": "conversation.item.input_audio_transcription.delta", "delta": "valle del ortigal"}
    )
    ev = stt._parse(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "estoy en el valle del ortigal",
        }
    )
    assert isinstance(ev, TranscriptEvent)
    assert ev.is_final and ev.speech_final
    assert ev.text == "estoy en el valle del ortigal"
    assert stt._interim == ""  # listo para el siguiente enunciado


def test_confidence_from_logprobs():
    import math

    stt = _new_stt()
    # dos tokens con logprob ~ -0.1 → confianza ~ exp(-0.1) ≈ 0.905
    ev = stt._parse(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "sí",
            "logprobs": [{"token": "sí", "logprob": -0.1}, {"token": ".", "logprob": -0.1}],
        }
    )
    assert abs(ev.confidence - math.exp(-0.1)) < 0.01
    # sin logprobs → 1.0 (no falso-bajo)
    stt2 = _new_stt()
    ev2 = stt2._parse(
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "hola"}
    )
    assert ev2.confidence == 1.0


def test_unknown_and_error_events_ignored():
    stt = _new_stt()
    assert stt._parse({"type": "input_audio_buffer.committed"}) is None
    assert stt._parse({"type": "error", "error": {"message": "x"}}) is None


def test_ulaw_encoding_known_values():
    # G.711 μ-law: silencio (0) → 0xFF; +full-scale → 0x80; -full-scale → 0x00.
    assert pcm16_to_ulaw(struct.pack("<h", 0)) == bytes([0xFF])
    assert pcm16_to_ulaw(struct.pack("<h", 32635)) == bytes([0x80])
    assert pcm16_to_ulaw(struct.pack("<h", -32635)) == bytes([0x00])
    # longitud: 1 byte μ-law por cada muestra PCM16 (2 bytes)
    pcm = struct.pack("<4h", 1000, -1000, 5000, -5000)
    assert len(pcm16_to_ulaw(pcm)) == 4
    assert pcm16_to_ulaw(b"") == b""


def test_build_prompt_biases_popayan_names():
    prompt = build_prompt()
    assert "Popayán" in prompt
    assert "Pubenza" in prompt  # barrio distintivo listado como sesgo
    assert len(prompt) <= 520  # acotado


def test_session_config_uses_gpt4o_mini_and_ulaw():
    # Shape GA: session.type=transcription, audio.input.{format,transcription,turn_detection}
    cfg = _new_stt()._session_config()
    assert cfg["type"] == "transcription"
    inp = cfg["audio"]["input"]
    assert inp["format"]["type"] == "audio/pcmu"  # G.711 μ-law (8k telefónico)
    assert inp["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert inp["transcription"]["language"] == "es"
    assert inp["turn_detection"]["type"] == "server_vad"
    assert cfg["include"] == ["item.input_audio_transcription.logprobs"]
