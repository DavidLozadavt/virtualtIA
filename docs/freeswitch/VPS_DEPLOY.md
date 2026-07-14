# Despliegue VPS — FreeSWITCH directo → Lyra (sin Twilio)

## Arquitectura objetivo

```
Entel SIP trunk
    → FreeSWITCH (VPS)
        → POST /freeswitch/inbound-call
        → WS  /freeswitch/audio (mod_audio_stream)
    → Lyra FastAPI
        → STT (Groq/OpenAI)
        → voice_call_engine
        → POST Laravel /taxi/solicitud-telefonica
        → TTS edge-tts → audio respuesta
```

## Checklist de despliegue

### 1. Lyra Python (.env)

```bash
INTELLITAXI_API_BASE=https://TU_BACKEND/api
GROQ_API_KEY=...
GOOGLE_MAPS_API_KEY=...

VOICE_SESSION_STORE=redis
REDIS_URL=redis://127.0.0.1:6379/0
CALL_SESSION_TTL_SEC=7200

TELEPHONY_STT_PROVIDER=groq
TELEPHONY_STT_MODEL=whisper-large-v3
TELEPHONY_STT_LANGUAGE=es
TELEPHONY_AUDIO_CODEC=PCMU
TELEPHONY_SAMPLE_RATE=8000
LYRA_TTS_VOICE=es-BO-SofiaNeural

# WS de captura: DEBE ser el host:puerto por el que FreeSWITCH alcanza el app.
# Si FreeSWITCH corre en contenedor y el app en el host, usa la IP del bridge
# docker + el PORT del app (el MISMO que ya funciona para el WAV), NO 127.0.0.1.
# Ej. contenedor→host: ws://172.17.0.1:8098/freeswitch/audio
# En runtime build_ws_audio_url ya lo deriva del request; esto es fallback.
FREESWITCH_WS_AUDIO_URL=ws://172.17.0.1:8098/freeswitch/audio
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
# TTS telefónico µ-law
apt install -y ffmpeg
```

### 4. Apagar FreeSWITCH viejo (Twilio)

Evita conflicto de registro SIP con Entel:

```bash
docker stop freeswitch-twilio   # o systemctl stop freeswitch-twilio
```

### 5. FreeSWITCH nuevo

1. Copiar `entel_gateway.xml.template` → `sip_profiles/external/entel.xml`
2. Reemplazar placeholders Entel (localmente, no en git)
3. Copiar `ai_dialplan.xml.template` → dialplan
4. Reemplazar `IA_HOST`, `IA_PORT`, `IA_WS_SCHEME`
5. Instalar `mod_audio_stream` si no está cargado

```bash
fs_cli -x "reloadxml"
fs_cli -x "sofia profile external restart"
```

## Comandos de validación

### Lyra (HTTP)

```bash
# Health
curl -s http://127.0.0.1:8000/freeswitch/health | jq

# Crear servicio en Laravel (sin audio)
curl -s -X POST http://127.0.0.1:8000/freeswitch/test-create-service \
  -H "Content-Type: application/json" \
  -d '{"telefono":"+573001234567","origen":"Centro Popayán","call_uuid":"test-vps-001"}' | jq

# Registrar llamada entrante
curl -s -X POST http://127.0.0.1:8000/freeswitch/inbound-call \
  -H "Content-Type: application/json" \
  -d '{"call_uuid":"550e8400-e29b-41d4-a716-446655440000","caller_number":"+573001234567","destination_number":"6028231111"}' | jq

# Simular turno STT (sin audio)
curl -s -X POST http://127.0.0.1:8000/freeswitch/process-text \
  -H "Content-Type: application/json" \
  -d '{"call_uuid":"550e8400-e29b-41d4-a716-446655440000","text":"Centro","confidence":0.95}' | jq

# Confirmar origen
curl -s -X POST http://127.0.0.1:8000/freeswitch/process-text \
  -H "Content-Type: application/json" \
  -d '{"call_uuid":"550e8400-e29b-41d4-a716-446655440000","text":"sí","confidence":0.99}' | jq
```

### FreeSWITCH (fs_cli)

```bash
fs_cli -x "reloadxml"
fs_cli -x "sofia status"
fs_cli -x "sofia status profile external"
fs_cli -x "sofia status gateway ENTEL_GATEWAY"
fs_cli -x "show channels"
fs_cli -x "uuid_dump <UUID_DE_LLAMADA>"
```

### Logs Lyra (por call_uuid)

```bash
grep "call_uuid=550e8400" logs/lyra.log
grep "\[freeswitch\]" logs/lyra.log
grep "\[backend\]" logs/lyra.log
```

## Checklist llamada real

- [ ] Gateway Entel `REGED` en `sofia status gateway`
- [ ] `GET /freeswitch/health` → `ok: true`, `redis_ok: true`
- [ ] `test-create-service` crea servicio en Laravel
- [ ] Llamada entrante genera log `inbound-call call_uuid=...`
- [ ] WebSocket `start` en logs `freeswitch/ws`
- [ ] STT produce texto en logs `stt_turn`
- [ ] Laravel recibe `canal_origen=FREESWITCH_AI_CALL`
- [ ] Usuario escucha respuesta TTS
- [ ] Llamada cuelga tras crear servicio

## Concurrencia (40–60 llamadas)

- [ ] Confirmar canales SIP con Entel
- [ ] `VOICE_SESSION_STORE=redis` activo
- [ ] Monitorear CPU/RAM (`htop`)
- [ ] Límites API STT (Groq/Deepgram)
- [ ] Laravel + MySQL con pool adecuado
- [ ] Considerar FS e IA en hosts separados si CPU > 80%

## Seguridad

- ESL (8021) **solo localhost** o VPN
- `/freeswitch/*` detrás de firewall — solo IP FreeSWITCH
- No exponer Redis (6379) a internet
- Usar `wss://` si WebSocket cruza red pública

## Rollback temporal

1. Detener FreeSWITCH nuevo / revertir dialplan Entel
2. Reactivar FreeSWITCH + Twilio:
   ```bash
   docker start freeswitch-twilio
   ```
3. Lyra mantiene fallback Twilio en `/voice` y `/process_speech`
4. Verificar llamada de prueba vía Twilio
5. Tiempo estimado: 5–10 minutos

## Criterios de éxito

1. Llamada Entel → FS sin Twilio
2. `call_uuid` en logs Python y Laravel
3. STT sin Twilio
4. Servicio creado en Laravel
5. TTS audible al cliente
6. Sin duplicados en reintento (`call_uuid` idempotente)
7. Twilio apagable sin afectar operación
