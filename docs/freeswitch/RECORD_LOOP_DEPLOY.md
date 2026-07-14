# Record-loop deploy (sin mod_audio_stream)

Captura de voz con módulos **stock** de FreeSWITCH. El contenedor
`freeswitch-directo` (imagen `safarov/freeswitch`) NO trae `mod_audio_stream`,
así que la conversación se maneja con `record` + HTTP + `playback` vía un script
Lua, y la llamada completa se graba con `record_session` para el frontend.

## Arquitectura

```
FreeSWITCH (contenedor)                 App Lyra (host :8098)
──────────────────────                  ─────────────────────
lyra_call.lua
  answer + record_session  ───────────► (graba llamada completa local)
  POST /freeswitch/inbound-call ──────► crea sesión + saludo → audio_url
  ◄─ baja y reproduce saludo
  LOOP:
    recordFile(utt.wav)  (silencio=fin)
    POST /freeswitch/audio-turn (WAV) ─► STT → motor → audio_url + hangup
    ◄─ baja y reproduce respuesta
  al colgar:
    POST /freeswitch/recording (WAV) ──► guarda grabación por call_uuid
                                          (frontend: GET /freeswitch/recording/{uuid}.wav)
```

## Requisitos en el contenedor

1. **mod_lua** cargado:
   ```bash
   docker exec freeswitch-directo fs_cli -x 'module_exists mod_lua'
   # si dice false:
   docker exec freeswitch-directo fs_cli -x 'load mod_lua'
   ```
   Para que cargue siempre, añadir `<load module="mod_lua"/>` a
   `/etc/freeswitch/autoload_configs/modules.conf.xml`.

2. **busybox wget** con `--post-file` (el dialplan viejo ya usaba busybox wget).
   Verificar:
   ```bash
   docker exec freeswitch-directo busybox wget --help 2>&1 | grep -i post-file
   ```
   Si NO soporta `--post-file`, instalar curl y cambiar los `os.execute` del Lua
   a `curl` (avísame y te paso la variante).

## Instalar

1. Copiar el script Lua al contenedor:
   ```bash
   docker cp docs/freeswitch/lyra_call.lua freeswitch-directo:/usr/share/freeswitch/scripts/lyra_call.lua
   ```
   (si esa ruta no existe, buscar la real:
   `docker exec freeswitch-directo find / -type d -name scripts 2>/dev/null | grep freeswitch`)

2. Reemplazar el dialplan:
   ```bash
   docker cp docs/freeswitch/99_lyra_ai.xml freeswitch-directo:/etc/freeswitch/dialplan/public/99_lyra_ai.xml
   ```

3. Recargar:
   ```bash
   docker exec freeswitch-directo fs_cli -x 'reloadxml'
   ```

4. Reiniciar el app (host) para tomar los endpoints nuevos:
   ```bash
   sudo systemctl restart prelyra
   ```

## Ajustar `APP` en el Lua

`lyra_call.lua` tiene `local APP = "http://172.17.0.1:8098"`. Debe ser el mismo
host:puerto por el que FreeSWITCH ya baja el saludo (verificado en tu log:
`http://172.17.0.1:8098`). Si cambia el bridge docker, ajústalo ahí.

## Verificar (llamada de prueba)

`fs_cli` con `/log 7`, o `journalctl -u prelyra -f`. Debes ver, en orden:
```
[freeswitch] inbound-call ... (saludo)
[freeswitch] audio-turn call_uuid=... wav_bytes=... pcm_bytes=...
[stt/openai] transcript_text="..."
[freeswitch] audio-turn result ... audio_url=...
... (repite por turno) ...
[freeswitch] recording saved call_uuid=... bytes=...
```

## Frontend: audio de la llamada

La grabación completa se sirve por `GET /freeswitch/recording/{call_uuid}.wav`.
El servicio guarda `call_uuid`, así que el frontend arma la URL con ese id y la
reproduce en el apartado del servicio. No hace falta llamada de update.

## Tuning de calidad de transcripción

En `lyra_call.lua`:
- `UTT_MAX_LEN` (15s) — tope por locución.
- `UTT_SIL_THR` (200) — umbral de silencio; súbelo si corta por ruido de fondo,
  bájalo si no detecta el fin de frase.
- `UTT_SIL_SECS` (3) — segundos de silencio para cerrar; súbelo si corta a quien
  dicta pausado.
