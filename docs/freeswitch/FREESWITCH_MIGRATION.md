# FreeSWITCH ↔ Lyra — arquitectura de integración (Voice V2)

> Guía de despliegue del motor streaming: [STREAMING_DEPLOY.md](STREAMING_DEPLOY.md)
> Guía VPS: [VPS_DEPLOY.md](VPS_DEPLOY.md)
> Gateway Entel: [entel_gateway.xml.template](entel_gateway.xml.template)

## Flujo

```
Entel SIP trunk → FreeSWITCH → lyra_stream.lua
    → uuid_audio_stream (WS full-duplex) → Lyra Voice V2
        → OpenAI Realtime streaming (STT parciales, gpt-4o-mini-transcribe)
        → NLU structured-output (spans) → FSM de negocio
        → geocoding (Google/Nominatim) → Laravel /taxi/solicitud-telefonica
        → OpenAI TTS streaming → playback streamAudio
    → Lyra cuelga vía ESL uuid_kill
```

## Rutas del app (Python)

| Método | Ruta | Uso |
|--------|------|-----|
| GET | `/freeswitch/health` | Health del gateway de voz |
| POST | `/freeswitch/test-create-service` | Prueba IA → Laravel sin audio |
| WS | `/freeswitch/audio` | Audio bidireccional mod_audio_stream |
| GET | `/freeswitch/recording/{call_uuid}.wav` | Grabación completa (panel operador) |

Los endpoints del record-loop V1 (`/inbound-call`, `/audio-turn`,
`/process-text`, `/audio-file`, `POST /recording`) fueron eliminados: toda la
conversación viaja por el WebSocket y la grabación la mezcla el servidor.

## Backend Laravel — checklist (sin cambios)

- [ ] `/taxi/solicitud-telefonica` acepta `canal_origen: FREESWITCH_AI_CALL`
- [ ] Acepta `call_uuid` opcional
- [ ] Idempotencia ante reintentos (mismo `call_uuid`)

## Validación rápida

```bash
curl -s http://127.0.0.1:8098/freeswitch/health | jq
curl -s -X POST http://127.0.0.1:8098/freeswitch/test-create-service \
  -H "Content-Type: application/json" \
  -d '{"telefono":"+573001234567","origen":"Centro Popayán","call_uuid":"test-001"}' | jq
```

Llamada real: ver checklist de [STREAMING_DEPLOY.md](STREAMING_DEPLOY.md).
