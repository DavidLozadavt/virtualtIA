# FreeSWITCH + Lyra IA — ejemplos de configuración

Archivos de ejemplo con **placeholders**. No contienen credenciales ni URLs de producción.

## Contenido

| Archivo | Descripción |
|---------|-------------|
| `sip_profiles/external/emtel.xml.example` | Gateway SIP (EMTEL) |
| `sip_profiles/external/twilio_ia.xml.example` | Gateway SIP Twilio Elastic Trunk |
| `dialplan/public/intellitaxi.xml.example` | Llamada entrante → WebSocket IA |

## WebSocket de audio (placeholder)

```
ws://CONTAINER_NAME:PORT/freeswitch/audio
```

Reemplazar `WS_URL_PLACEHOLDER` en el dialplan según la red Docker o VPS.

## Puertos de referencia

| Puerto | Uso |
|--------|-----|
| 8000/tcp | API Lyra + WebSocket audio |
| 5060/udp | SIP |
| 16384-32768/udp | RTP |
| 8021/tcp | ESL (opcional) |

## Red Docker

Nombre sugerido: `telephony_net`

Los comandos operativos de despliegue en VPS no forman parte de este repositorio.
