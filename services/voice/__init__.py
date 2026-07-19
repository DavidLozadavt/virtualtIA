"""Lyra Voice V2 — motor conversacional de voz streaming full-duplex.

Arquitectura (docs/voice/audit_2026-07-18/LYRA_VOICE_V2_SPEC.md):

  FreeSWITCH (mod_audio_stream, WS bidireccional)
      → transport   (frames PCM 8k + metadata; playback streamAudio con pacing)
      → aec         (cancelación de eco NLMS lado servidor)
      → stt_stream  (Deepgram nova-2 streaming, parciales + endpointing nativo)
      → endpointing (híbrido acústico + semántico)
      → nlu         (LLM structured-output: extrae SOLO spans, nunca resuelve)
      → orchestrator(estados de negocio del FSM preservados tal cual)
      → tts_stream  (edge-tts incremental por oración → PCM 8k)
      → barge_in    (clasificador interrupción real vs backchannel)

La resolución de direcciones sigue siendo 100% de core/geocoder_service y
core/location_match (bucket B, sin cambios). La creación de servicios,
WhatsApp, sesiones y backend (bucket A) se llaman exactamente igual que antes.
"""

from services.voice.runtime import VoiceCallRuntime

__all__ = ["VoiceCallRuntime"]
