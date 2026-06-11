# Migración Twilio → FreeSWITCH — Lyra / virtualtIA

> Guía operativa completa: [docs/freeswitch/VPS_DEPLOY.md](freeswitch/VPS_DEPLOY.md)  
> Templates FS: [entel_gateway.xml.template](freeswitch/entel_gateway.xml.template), [ai_dialplan.xml.template](freeswitch/ai_dialplan.xml.template)

## Objetivo

Pasar de:

```
Entel → FreeSWITCH → Twilio → Lyra → Laravel
```

a:

```
Entel → FreeSWITCH → Lyra → Laravel
```

## Rutas nuevas (Python)

| Método | Ruta | Uso |
|--------|------|-----|
| GET | `/freeswitch/health` | Health del módulo |
| POST | `/freeswitch/test-create-service` | Prueba IA → Laravel sin audio |
| POST | `/freeswitch/inbound-call` | Registrar llamada entrante |
| POST | `/freeswitch/process-text` | Turno con texto STT ya transcrito |
| WS | `/freeswitch/audio` | Audio mod_audio_stream |

## Fase 1 — Validar backend sin Twilio

```bash
curl -X POST http://localhost:8000/freeswitch/test-create-service \
  -H "Content-Type: application/json" \
  -d '{"telefono":"+573001234567","origen":"Centro Popayán","call_uuid":"test-001"}'
```

Respuesta esperada: `"success": true` y solicitud creada en Laravel.

## Fase 2 — Apagar FreeSWITCH viejo (Twilio)

**Importante:** Dos instancias FreeSWITCH con la misma troncal Entel generan conflicto de registro SIP.

### Si está en Docker

```bash
# Identificar contenedor FS viejo
docker ps | grep freeswitch

# Detener (NO eliminar hasta validar rollback)
docker stop <container_id_twilio_fs>
```

### Si está en systemd

```bash
sudo systemctl stop freeswitch-twilio
sudo systemctl disable freeswitch-twilio  # solo tras cutover exitoso
```

## Fase 3 — FreeSWITCH nuevo (sin Twilio)

### Dialplan mínimo (ejemplo)

En `dialplan/default.xml` o contexto Entel:

```xml
<extension name="lyra_inbound">
  <condition field="destination_number" expression="^(\d+)$">
    <action application="answer"/>
    <action application="set" data="call_uuid=${uuid}"/>
    <action application="curl" data="http://LYRA_HOST:8000/freeswitch/inbound-call post application/json {'call_uuid':'${uuid}','caller_number':'${caller_id_number}','destination_number':'${destination_number}'}"/>
    <!-- mod_audio_stream hacia Lyra -->
    <action application="eval" data="${uuid_audio_stream(ws://LYRA_HOST:8000/freeswitch/audio ${uuid} mono 8k)}"/>
  </condition>
</extension>
```

Ajustar según módulos instalados (`mod_audio_stream`, `mod_curl`).

### Variables de entorno Lyra

Ver `.env.example` sección FreeSWITCH.

Producción:

```
VOICE_SESSION_STORE=redis
REDIS_URL=redis://127.0.0.1:6379/0
TELEPHONY_STT_PROVIDER=groq
GROQ_API_KEY=...
INTELLITAXI_API_BASE=https://tu-backend/api
```

## Fase 4 — Prueba de llamada real

1. `GET /freeswitch/health` → `ok: true`
2. Llamar al número Entel
3. Ver logs: `[freeswitch] inbound-call call_uuid=...`
4. Verificar WebSocket: `[freeswitch/ws] start call_uuid=...`
5. Confirmar POST Laravel en logs: `[backend] create_service`

## Rollback temporal

Si falla el flujo nuevo:

1. **Reactivar FreeSWITCH + Twilio:**
   ```bash
   docker start <container_id_twilio_fs>
   # o: sudo systemctl start freeswitch-twilio
   ```

2. **Lyra sigue sirviendo Twilio** — rutas `/voice` y `/process_speech` no fueron eliminadas.

3. **Revertir dialplan Entel** al gateway Twilio anterior.

4. **No eliminar** variables `TWILIO_*` hasta cutover definitivo.

Tiempo estimado de rollback: 5–10 minutos (restart FS + verificar registro SIP).

## Concurrencia (40–60 llamadas)

Requisitos:

- `VOICE_SESSION_STORE=redis` obligatorio
- Validar canales concurrentes con Entel
- STT externo (Groq/Deepgram) con límites de API
- Monitorear CPU/RAM VPS
- Considerar separar FreeSWITCH e Lyra en hosts distintos si se satura

## Backend Laravel — checklist

Sin cambios obligatorios. Verificar:

- [ ] `/taxi/solicitud-telefonica` no exige `CallSid`
- [ ] Acepta `canal_origen: FREESWITCH_AI_CALL`
- [ ] Acepta `call_uuid` opcional
- [ ] Idempotencia ante reintentos (mismo `call_uuid`)

## Archivos creados

```
services/telephony/
  backend_client.py
  session_store.py
  voice_call_engine.py
  stt_service.py
  tts_service.py
  phone_utils.py

api/routers/freeswitch.py
```
