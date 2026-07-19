# Despliegue VPS — FreeSWITCH → Lyra Voice V2 (streaming full-duplex)

## Arquitectura

```
Entel SIP trunk
    → FreeSWITCH (VPS) — lyra_stream.lua + mod_audio_stream
        → WS /freeswitch/audio (bidireccional, PCM16 8k)
    → Lyra FastAPI (Voice V2)
        → Deepgram streaming (STT parciales + endpointing)
        → NLU structured-output → FSM de negocio
        → POST Laravel /taxi/solicitud-telefonica
        → edge-tts streaming → playback streamAudio
        → grabación mezclada server-side → /freeswitch/recording/{uuid}.wav
```

## Checklist de despliegue

### 1. Lyra Python (.env)

```bash
INTELLITAXI_API_BASE=https://TU_BACKEND/api
GOOGLE_MAPS_API_KEY=...
OPENAI_API_KEY=sk-proj-...        # NLU (structured outputs); NO OpenRouter

VOICE_SESSION_STORE=redis
REDIS_URL=redis://127.0.0.1:6379/0
CALL_SESSION_TTL_SEC=7200

# Voice V2
DEEPGRAM_API_KEY=...              # OBLIGATORIA
VOICE_STT_MODEL=nova-2
VOICE_STT_LANGUAGE=es-419
VOICE_STT_ENDPOINTING_MS=300
VOICE_STT_UTTERANCE_END_MS=1000
VOICE_ENDPOINT_HOLD_MS=900
VOICE_NLU_MODEL=gpt-4o-mini
VOICE_PLAYBACK_LEAD_MS=400
LYRA_TTS_VOICE=es-BO-SofiaNeural

# ESL solo localhost — NUNCA exponer 8021 a internet
FREESWITCH_ESL_HOST=127.0.0.1
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=CAMBIAR_PASSWORD_FUERTE
```

### 2. Redis

```bash
docker run -d --name lyra-redis -p 127.0.0.1:6379:6379 redis:7-alpine
```

### 3. Dependencias Lyra

```bash
pip install -r requirements.txt
apt install -y ffmpeg   # decodificación TTS streaming
```

### 4. FreeSWITCH

1. Copiar `entel_gateway.xml.template` → `sip_profiles/external/entel.xml`
   (reemplazar placeholders Entel localmente, no en git).
2. Copiar `99_lyra_ai.xml` → `/etc/freeswitch/dialplan/public/`.
3. Copiar `lyra_stream.lua` → `/usr/share/freeswitch/scripts/` y editar
   `WS_BASE` (host:puerto por el que el contenedor alcanza al app).
4. Cargar `mod_audio_stream` (`modules.conf.xml`).
5. Audio crudo: ver `freeswitch_audio_config.md` (PCMU, sin CNG/AGC).

```bash
fs_cli -x "reloadxml"
fs_cli -x "module_exists mod_audio_stream"    # → true
fs_cli -x "sofia profile external restart"
```

## Comandos de validación

### Lyra (HTTP)

```bash
curl -s http://127.0.0.1:8098/freeswitch/health | jq
# → service: lyra-voice-v2, stt_available: true

curl -s -X POST http://127.0.0.1:8098/freeswitch/test-create-service \
  -H "Content-Type: application/json" \
  -d '{"telefono":"+573001234567","origen":"Centro Popayán","call_uuid":"test-vps-001"}' | jq
```

### FreeSWITCH (fs_cli)

```bash
fs_cli -x "sofia status gateway ENTEL_GATEWAY"
fs_cli -x "show channels"
fs_cli -x "uuid_dump <UUID_DE_LLAMADA>"
```

### Logs Lyra (por call_uuid)

```bash
grep "call_uuid=<UUID>" logs/lyra.log
grep "\[stt\]" logs/lyra.log        # deepgram connected
grep "\[runtime\]" logs/lyra.log    # call started / barge-in / call closed
grep "\[backend\]" logs/lyra.log
```

## Checklist llamada real

- [ ] Gateway Entel `REGED` en `sofia status gateway`
- [ ] `GET /freeswitch/health` → `ok: true`, `redis_ok: true`, `stt_available: true`
- [ ] `test-create-service` crea servicio en Laravel
- [ ] Al contestar: log `[transport] stream start` + `[stt] deepgram connected`
- [ ] Saludo audible < 1 s tras contestar
- [ ] Hablar encima de Lyra con contenido → se calla en ≤ ~0.5 s
- [ ] Laravel recibe `canal_origen=FREESWITCH_AI_CALL`
- [ ] Llamada cuelga tras crear servicio
- [ ] `GET /freeswitch/recording/<uuid>.wav` reproduce la llamada completa

## Concurrencia (40–60 llamadas)

- [ ] Confirmar canales SIP con Entel
- [ ] `VOICE_SESSION_STORE=redis` activo
- [ ] Límites de concurrencia Deepgram / OpenAI del plan contratado
- [ ] Monitorear CPU/RAM (`htop`) — AEC ≈ 1-2 ms por frame por llamada
- [ ] Laravel + MySQL con pool adecuado
- [ ] Considerar FS e IA en hosts separados si CPU > 80%

## Seguridad

- ESL (8021) **solo localhost** o VPN
- `/freeswitch/*` detrás de firewall — solo IP FreeSWITCH
- No exponer Redis (6379) a internet
- Usar `wss://` si el WebSocket cruza red pública (STREAM_TLS_* de mod_audio_stream)

## Rollback

V1 (record-loop) fue eliminado del árbol: el rollback es restaurar el backup
completo del proyecto previo a la migración V2 y sus scripts Lua/dialplan.

## Criterios de éxito

1. Llamada Entel → FS → Lyra sin archivos intermedios
2. `call_uuid` en logs Python y Laravel
3. Conversación natural: parciales STT en logs, barge-in operativo
4. Servicio creado en Laravel sin duplicados (idempotencia por `call_uuid`)
5. Grabación disponible para el panel del operador
