# Lyra Voice V2 — Implementación (2026-07-19)

> **Actualización posterior (STT):** el motor de STT telefónico migró de
> **Deepgram nova-2** a **OpenAI Realtime (`gpt-4o-mini-transcribe`)** para
> unificar todo el sistema en OpenAI. El flujo (parciales → endpointing híbrido
> → NLU → FSM) no cambió; solo el proveedor. Las menciones a Deepgram, `keywords`
> y `DEEPGRAM_API_KEY` de este documento quedan como registro histórico —
> ver `services/voice/stt_stream.py`, `docs/freeswitch/STREAMING_DEPLOY.md` y
> `docs/freeswitch/VPS_DEPLOY.md` para el estado actual.

Implementación completa de `docs/voice/audit_2026-07-18/LYRA_VOICE_V2_SPEC.md`.
V1 (record-loop) fue eliminado del árbol; existe únicamente V2.

## Mapa spec → código

| Capa (spec) | Módulo | Notas |
|---|---|---|
| §3.1 Transporte | `services/voice/transport.py` + `docs/freeswitch/lyra_stream.lua` + `99_lyra_ai.xml` | mod_audio_stream WS bidireccional; playback `streamAudio` (raw base64 8k); SIN gate de reproducción; cancelación de barge-in por pacing de chunks (~200 ms, ≤`VOICE_PLAYBACK_LEAD_MS` adelantado) |
| §3.2 STT streaming | `services/voice/stt_stream.py` | Deepgram **nova-2**, `language=es-419`, `interim_results`, `endpointing=300`, `utterance_end_ms=1000`, `vad_events`; `keywords` (≤100) derivadas del catálogo (salvage `_build_hint_vocab`); cliente WS propio sobre `websockets` |
| §3.2 endpointing híbrido | `services/voice/endpointing.py` | acústico (speech_final/UtteranceEnd) + semántico (retención si el texto termina en continuación: preposición, tipo de vía, número colgado) — reemplaza `UTT_SIL_SECS=3` |
| §3.2 filtros conservados | `services/voice/filters.py` | anti-alucinación, anti-eco textual, `preprocess_stt` (bucket B) |
| §3.3 NLU | `services/voice/nlu.py` | `response_format: json_schema strict` (constrained decoding), schema exacto del spec; el LLM extrae SOLO spans; degradación determinista local si el LLM falla; corre en CADA turno |
| §3.4 Turn Orchestrator | `services/voice/orchestrator.py` | estados de negocio V1 idénticos (`waiting_origin`, `confirming_origin`, `waiting_geo_context`, `creating_service`, `finished`); overrides, DTMF map, WhatsApp, guard de troncal y handoff de barrio preservados verbatim; preemptive NLU + geocoding especulativo read-only (`SpeculativeGeocoder`); reparación con variación (`ConversationRepair`, salvage); confirmación implícita acotada (regla V1 + guardas de intent) |
| §3.5 TTS streaming | `services/voice/tts_stream.py` + `text_normalize.py` | edge-tts `stream()` incremental (verificado en el código instalado) → pipe ffmpeg mp3→PCM8k por chunks; síntesis por oración; caché de frases fijas (latencia ~0); normalización es-CO de números/nomenclatura; timeout + retry + fallo audible (cuelga, no silencio) |
| §3.6 Barge-in / full-duplex | `services/voice/aec.py` + `barge_in.py` | AEC NLMS servidor (256 taps @8k) con estimación de retardo por correlación FFT y guardia Geigel de doble-habla; clasificador interrupción real (energía sostenida + contenido + contexto FSM — "sí" interrumpe en confirmación); truncado del historial a lo escuchado (`note_partial_delivery`) |
| Runtime | `services/voice/runtime.py` | composición por llamada, cola de turnos serializada, watchdog (hold semántico + silencios), frase de espera en paralelo con la creación del servicio, grabación mezclada server-side |
| Router | `api/routers/freeswitch.py` | WS `/freeswitch/audio`; conservados: `GET /recording/{uuid}.wav` (panel operador), `/health`, `/test-create-service` |

## Decisiones sobre ítems no validados del spec (§5)

- **STT es-CO**: Deepgram nova-2 soporta español streaming (`es`/`es-419`) y
  `keywords` en todas las lenguas (docs oficiales, verificado 2026-07-19).
  AssemblyAI Universal-Streaming quedó descartado (sin español streaming).
  Queda pendiente el piloto con audios reales de Popayán (requiere
  `DEEPGRAM_API_KEY`).
- **TTS**: se conserva la voz Azure ya validada en producción vía edge-tts,
  cuyo `stream()` upstream SÍ es incremental (el buffering era del wrapper V1,
  eliminado). Cartesia queda descartado: calidad es-CO no validable sin cuenta.
- **mod_audio_stream**: playback bidireccional confirmado en el README oficial
  (`streamAudio`, audioDataType raw/wav/mp3). No hay comando kill de playback
  en la versión open-source → cancelación por pacing (dejar de enviar corta el
  audio en ≤ ~`VOICE_PLAYBACK_LEAD_MS`).

## Lo que NO cambió (checklist §4 del spec — verificado con git diff vacío)

- `services/telephony/backend_client.py`, `session_store.py`, `phone_utils.py`,
  `idempotency.py`, `esl_client.py`
- `core/geocoder_service.py`, `core/location_match.py`, `core/address_utils.py`,
  `tools/popayan_geodata.py`, `core/stt_enhancer.py`
- WhatsApp (`_send_whatsapp_message_async` movido verbatim al orquestador)
- Estados y transiciones de negocio del FSM; contrato Laravel
  `/taxi/solicitud-telefonica`; grabaciones para el panel
  (`GET /freeswitch/recording/{uuid}.wav`)

## Eliminado (V1 completo)

`voice_call_engine.py`, `call_handler.py`, `stt_service.py`, `tts_service.py`,
`tts_file_store.py`, `ws_audio_buffer.py`, `audio_vad.py`,
`audio_preprocess.py`, `core/streaming_pipeline.py` (piezas útiles rescatadas),
`lyra_call.lua`, `RECORD_LOOP_DEPLOY.md`, `ai_dialplan.xml.template`,
endpoints `/inbound-call`, `/audio-turn`, `/process-text`, `/audio-file`,
`POST /recording`, settings muertos (`TELEPHONY_STT_*`, `FS_VAD_*`,
`STT_PROVIDER`, `FREESWITCH_WS_AUDIO_URL`, …), `speech/` (huérfano),
`scratch/audio_diagnostic.py`. `core/conversation_repair.py` quedó recortado a
lo que V2 usa (memoria, reparación, templates, BargeInHandler).

## Verificación

- `python -m compileall` limpio; `import main` OK.
- **65 tests** en `tests/` (todos pasan): normalización TTS, endpointing
  híbrido, NLU (schema/parse/fallback), orquestador (18 casos de negocio:
  confirmaciones, correcciones DST, handoff de barrio, DTMF, silencios,
  troncal, idempotencia de flujo), AEC (atenuación ≥6 dB con retardo de 50 ms,
  supervivencia de voz en doble-habla), barge-in, transporte, grabadora y un
  **flujo end-to-end** completo (saludo → origen → confirmación → servicio →
  colgado) con transporte/STT/TTS simulados.

## Go-live pendiente (infra, no código)

1. `DEEPGRAM_API_KEY` en `.env` (obligatoria).
2. `mod_audio_stream` cargado en el FreeSWITCH desplegado + copiar
   `lyra_stream.lua` / `99_lyra_ai.xml` (ver `docs/freeswitch/STREAMING_DEPLOY.md`).
3. Piloto de llamadas reales para calibrar `VOICE_ENDPOINT_HOLD_MS`,
   `VOICE_BARGE_MIN_MS` y validar keywords Deepgram con acento payanés.
