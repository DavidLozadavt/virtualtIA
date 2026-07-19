# Despliegue Lyra Voice V2 — streaming full-duplex (mod_audio_stream)

Reemplaza por completo el record-loop de V1 (`lyra_call.lua` + `/audio-turn`).
Un solo camino de voz: WebSocket bidireccional continuo.

## Arquitectura

```
Llamada entrante (Entel/SIP)
  → FreeSWITCH dialplan 99_lyra_ai.xml → lyra_stream.lua
      → answer + uuid_audio_stream start ws://<app>/freeswitch/audio?call_uuid=...&caller_number=...
  → Lyra (FastAPI WS):
      audio del llamante (PCM16 8k, tiempo real) → AEC → Deepgram streaming
      parciales → endpointing híbrido → NLU (spans) → FSM de negocio
      respuesta → edge-tts por oración → streamAudio (playback full-duplex)
  → Lyra cuelga con ESL uuid_kill; la grabación completa la mezcla el servidor
    y queda en FREESWITCH_RECORDINGS_DIR/{call_uuid}.wav
    (GET /freeswitch/recording/{call_uuid}.wav — contrato del panel intacto)
```

## Requisitos en el contenedor FreeSWITCH

1. **mod_audio_stream** compilado y cargado:
   - ⚠️ La imagen desplegada (`safarov/freeswitch`, contenedor
     `freeswitch-directo`) **NO lo trae** (verificado 2026-07-14): hay que
     compilarlo dentro del contenedor o cambiar a una imagen que lo incluya
     (repo: `github.com/amigniter/mod_audio_stream`, build CMake estándar).
   - `modules.conf.xml`: `<load module="mod_audio_stream"/>`
   - verificar: `fs_cli -x "module_exists mod_audio_stream"` → `true`
2. Scripts:
   - `lyra_stream.lua` → `/usr/share/freeswitch/scripts/`
   - `99_lyra_ai.xml` → `/etc/freeswitch/dialplan/public/`
3. Editar `WS_BASE` en `lyra_stream.lua` con el host:puerto por el que el
   contenedor alcanza al app (típico Docker: `ws://172.17.0.1:8098`).
4. ESL accesible desde el app (`FREESWITCH_ESL_HOST/PORT/PASSWORD` en `.env`)
   — se usa para colgar (`uuid_kill`).
5. `busybox`/`wget` ya NO son necesarios.

## Variables nuevas del app (.env)

| Variable | Valor recomendado | Uso |
|---|---|---|
| `DEEPGRAM_API_KEY` | (obligatoria) | STT streaming |
| `VOICE_STT_MODEL` | `nova-2` | soporta español streaming + `keywords` |
| `VOICE_STT_LANGUAGE` | `es-419` | español latinoamericano |
| `VOICE_STT_ENDPOINTING_MS` | `300` | pausa acústica → fin de turno |
| `VOICE_STT_UTTERANCE_END_MS` | `1000` | cierre por gap de palabras |
| `VOICE_ENDPOINT_HOLD_MS` | `900` | retención semántica (direcciones dictadas) |
| `VOICE_NLU_MODEL` | `gpt-4o-mini` | extracción de spans (structured outputs) |
| `VOICE_PLAYBACK_LEAD_MS` | `400` | buffer de playback (define latencia de corte en barge-in) |
| `LYRA_TTS_VOICE` | `es-CO-SalomeNeural` | voz (igual que V1) |

`OPENAI_API_KEY` (no OpenRouter) se usa para el NLU si `VOICE_NLU_API_KEY`
está vacía.

## Verificación post-deploy

1. `curl http://<app>/freeswitch/health` → `service: lyra-voice-v2`,
   `stt_available: true`.
2. Llamada de prueba: el saludo debe sonar <1 s tras contestar (frase
   pre-cacheada).
3. Hablar encima de Lyra con una frase con contenido → debe callarse en
   ≤ ~0.5 s (pacing) y procesar lo dicho.
4. Al colgar: `GET /freeswitch/recording/<uuid>.wav` reproduce la llamada
   completa (ambas voces mezcladas).
5. Logs del app: `[stt] deepgram connected`, `[runtime] call started`,
   `[orchestrator] ...`, `[recorder] saved`.

## Rollback

Restaurar `lyra_call.lua` + el `99_lyra_ai.xml` de record-loop desde el
backup del proyecto (V1 fue eliminado del árbol en la migración V2).
