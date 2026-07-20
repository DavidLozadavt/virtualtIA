# Handoff — Unificación del STT en OpenAI `gpt-4o-mini-transcribe` (2026-07-19)

Sesión posterior al despliegue de Lyra Voice V2 (`docs/voice/HANDOFF_2026-07-19.md`).
Objetivo: que **todo** el sistema transcriba voz con OpenAI `gpt-4o-mini-transcribe`,
eliminando `whisper-1` (canal navegador) y **Deepgram** (línea telefónica de taxis).

Resultado final: **llamada real en producción funcionando de punta a punta** con
OpenAI Realtime — el barrio "Valle del Ortigal" (que `whisper-1` rompía) transcribe
bien, geocodifica, crea el servicio y dispara WhatsApp.

> Sin commits (pedido explícito del usuario). Todos los cambios son locales.

---

## 1. Punto de partida y motivación

Evidencia del usuario (`scratch/audio_diagnostic.py` sobre audios reales):
`gpt-4o-mini-transcribe` transcribe casi perfecto; `whisper-1` falla nombres propios
de barrios ("Valle del Hortigal" y variantes), lo que rompía la resolución de
direcciones aguas abajo.

Había **dos rutas STT** distintas en el repo:

| Ruta | Uso | Motor previo |
|---|---|---|
| `core/voice_engine` (`POST /voice/transcribe`) | Asistente de **navegador** (nexiservice, schoolsena) | OpenAI `whisper-1` (+ fallback Groq whisper-large-v3) |
| `services/voice/stt_stream` (WS `/freeswitch/audio`) | **Línea telefónica de taxis** (Lyra Voice V2) | **Deepgram nova-2** streaming |

`gpt-4o-mini-transcribe` **no se seleccionaba en ninguna parte del código vivo**. El
único lugar donde vivió (viejo `services/telephony/stt_service.py`) fue borrado al
reemplazar V1 por V2 (commit `3939494`).

---

## 2. Paso 1 — Canal navegador: `whisper-1` → `gpt-4o-mini-transcribe`

**Causa raíz:** cada rama de selección de modelo en `core/voice_engine.VoiceEngine`
codificaba `whisper-1` (o `whisper-large-v3` vía Groq); el router y los YAML pasaban
`stt_model: whisper-1`. Nunca gpt-4o.

**Landmine (verificado con docs oficiales OpenAI):** `gpt-4o-transcribe` y
`gpt-4o-mini-transcribe` **solo soportan `response_format="json"`** — el código pedía
`verbose_json`, así que un cambio ingenuo de modelo habría dado **HTTP 400**.

**Cambios:**
- `core/voice_engine.py`: constante `STT_MODEL = "gpt-4o-mini-transcribe"`; `__init__`
  sin ramas Groq/whisper (resuelve solo key OpenAI real, rechaza `sk-or`; STT
  deshabilitado si no hay key válida); `transcribe()` sin parámetro `stt_model`
  (imposible forzar otro modelo desde afuera), `response_format="json"`, bloque de
  `verbose_json`/segmentos eliminado; `_WHISPER_PROMPT_ES` → `_STT_PROMPT_ES`.
- `api/routers/browser_voice.py`: quitado el override `stt_model=...`; docs actualizadas.
- `projects/nexiservice.yaml`, `projects/schoolsena.yaml`: eliminada la línea obsoleta
  `stt_model: whisper-1`.
- `core/config.py`: comentarios de `OPENAI_WHISPER_KEY` (key OpenAI de STT) y
  `GROQ_API_KEY` (ya no se usa para STT).
- `tests/test_voice_engine_stt.py` (nuevo): blinda modelo == gpt-4o-mini-transcribe,
  `response_format="json"`, `sk-or` deshabilita STT (no cae a whisper), firma sin
  `stt_model`.

**Verificado en producción (VPS):** al reiniciar `prelyra`, el log muestra
`VoiceEngine STT (OpenAI gpt-4o-mini-transcribe) inicializado.`

---

## 3. Paso 2 — Línea telefónica: Deepgram → OpenAI Realtime transcription

Decisión del usuario (ante el fork técnico): **OpenAI Realtime WS (streaming)** para
mantener EXACTAMENTE el mismo flujo (parciales en vivo + VAD + endpointing + barge-in
+ NLU/geocoding anticipado), en vez del modo batch por-enunciado (más barato pero
pierde los parciales).

### Diseño

Solo se reescribió el **interior** de `services/voice/stt_stream.py`. La clase nueva
`OpenAIRealtimeSTT` emite los **mismos eventos tipados** (`TranscriptEvent`,
`UtteranceEndEvent`, `SpeechStartedEvent`) con la **misma interfaz pública**
(`connect`/`send_audio`/`events`/`close`) que consumía `DeepgramLiveSTT` → por eso
`runtime.py`, `endpointing.py`, `orchestrator.py`, `barge_in.py` **no cambiaron de
lógica** (solo el import del nombre de la clase y comentarios).

Mapeo de protocolo:
- `input_audio_buffer.speech_started` → `SpeechStartedEvent`
- `conversation.item.input_audio_transcription.delta` → `TranscriptEvent(is_final=False)`
  (los deltas son **incrementales** → se acumulan en un buffer de interim)
- `conversation.item.input_audio_transcription.completed` → `TranscriptEvent(is_final=True,
  speech_final=True)` — el cierre de enunciado lo decide el `server_vad` de OpenAI

### Decisiones técnicas (todas verificadas, no supuestas)

- **Audio:** FreeSWITCH entrega PCM16 8 kHz → se envía como **`audio/pcmu`** (G.711
  μ-law, 8 kHz telefónico nativo) → **cero resample**. El formato `audio/pcm` del
  Realtime **solo admite 24 kHz** (confirmado en el tipo `AudioPCM` del SDK), por eso
  μ-law. Encoder propio con numpy (`pcm16_to_ulaw`) — **no depende de `audioop`** (que
  no es dependencia del proyecto y podría faltar en el Python del VPS).
- **Endpointing:** `server_vad` (`silence_duration_ms`) cierra el enunciado. La capa
  semántica propia (retención de direcciones dictadas) sigue igual encima.
- **Confianza:** real, calculada desde `logprobs` (`include:
  ["item.input_audio_transcription.logprobs"]`) → evita el falso-`1.0` que antes
  degradaba la reparación. Comprobada honesta en vivo: prompt limpio 0.999; enunciado
  degradado 0.435.
- **Sesgo de barrios:** OpenAI **no tiene keyword-boost** como Deepgram → los nombres
  de Popayán (Pubenza, Yanaconas, Valle del Ortigal…) pasan al **prompt** de la sesión
  (`build_prompt()`, sesgo suave).

### Protocolo Realtime — hallazgos NO documentados (descubiertos con la probe en vivo)

La probe `scratch/realtime_stt_probe.py` (ejercita la clase real de producción)
encontró 3 desajustes que ninguna doc predecía:

| Error del server (close 4000) | Causa | Fix |
|---|---|---|
| `beta_api_shape_disabled` | La cuenta rechaza el shape beta (`OpenAI-Beta: realtime=v1`) | Usar el shape **GA** vía el SDK `AsyncOpenAI().realtime.connect()` |
| `missing_model` | El handshake WS exige identificar la sesión | — |
| `invalid_model` | El `model` de la URL espera un modelo *realtime*, no de transcripción | `extra_query={"intent":"transcription"}` y **sin** `model` en la URL |

**Recipe final de conexión** (`OpenAIRealtimeSTT.connect`):
```python
client = AsyncOpenAI(api_key=settings.openai_stt_key())
cm = client.realtime.connect(extra_query={"intent": "transcription"})  # sin model, sin header beta
conn = await cm.__aenter__()
await conn.session.update(session={
  "type": "transcription",
  "audio": {"input": {
    "format": {"type": "audio/pcmu"},
    "transcription": {"model": "gpt-4o-mini-transcribe", "language": "es", "prompt": "..."},
    "turn_detection": {"type": "server_vad", "silence_duration_ms": 600, ...},
    "noise_reduction": {"type": "near_field"},
  }},
  "include": ["item.input_audio_transcription.logprobs"],
})
```

### Cambios de configuración

- `core/config.py`: **eliminadas** `DEEPGRAM_API_KEY`, `VOICE_STT_ENDPOINTING_MS`,
  `VOICE_STT_UTTERANCE_END_MS`, `VOICE_STT_KEYWORD_BOOST`. `VOICE_STT_MODEL` →
  `gpt-4o-mini-transcribe`, `VOICE_STT_LANGUAGE` → `es`; **nuevas** `VOICE_STT_SILENCE_MS`,
  `VOICE_STT_VAD_THRESHOLD`, `VOICE_STT_PREFIX_PADDING_MS`. Método nuevo
  `settings.openai_stt_key()` (OPENAI_WHISPER_KEY o OPENAI_API_KEY; rechaza `sk-or`).
- `.env` (VPS y local): borradas `DEEPGRAM_API_KEY`, `VOICE_STT_ENDPOINTING_MS`,
  `VOICE_STT_UTTERANCE_END_MS`, `VOICE_PLAYBACK_LEAD_MS` (huérfana). Usa la
  `OPENAI_API_KEY` `sk-proj-…` ya presente.
- `api/routers/freeswitch.py` (`/health`): `stt_provider="openai-realtime"`,
  `stt_available` = `bool(settings.openai_stt_key())` (antes leía `DEEPGRAM_API_KEY`,
  que ya no existe → habría dado `AttributeError`).

### Archivos tocados (paso 2)
`services/voice/stt_stream.py` (reescrito), `core/config.py`, `services/voice/runtime.py`,
`services/voice/endpointing.py`, `services/voice/__init__.py`, `api/routers/freeswitch.py`,
`.env`, `tests/test_stt_stream.py` (reescrito), `tests/test_runtime_flow.py`,
`scratch/realtime_stt_probe.py` (nuevo). Docs: `STREAMING_DEPLOY.md`, `VPS_DEPLOY.md`,
`ARCHITECTURE.md`, `README.md`, `FREESWITCH_MIGRATION.md`, `freeswitch_audio_config.md`,
nota en `LYRA_VOICE_V2_IMPLEMENTATION.md`.

---

## 4. Paso 3 — Optimización del reconecto fantasma

`mod_audio_stream` reabre el WS ocasionalmente tras colgar (comportamiento conocido,
inofensivo). El guard de "sesión terminal" en `runtime._initialize()` ya lo colgaba
sin re-saludar, **pero** conectaba el STT **antes** del chequeo → cada reconecto
fantasma abría una sesión OpenAI Realtime facturable que no transcribía nada.

**Fix:** mover el chequeo `service_created / STATE_FINISHED` **antes** de crear la
grabadora y conectar el STT. Ahora un reconecto fantasma solo produce:
```
[runtime] reconexión sobre sesión terminal call_uuid=... — colgando
```
sin `[stt] openai realtime connected` previo, sin grabadora, sin log espurio de
`call started`. `_shutdown()` ya maneja `stt=None`/`recorder=None` con guardas.

**Desplegado** (reinicio de `prelyra` 2026-07-19 23:54, arranque limpio confirmado).
El efecto se verá en la próxima llamada que **cree servicio** (sin servicio no hay
reconecto post-hangup que atrapar).

---

## 5. Verificación

- **74/74 tests** pasan (`python -m pytest tests/`). `python -m compileall` limpio;
  `import main` OK.
- **Probe en vivo** (`scratch/realtime_stt_probe.py` sobre WAV real de 73 s): parciales
  + finales, "Valle del Ortigal" perfecto (conf 0.979–0.999), confianza logprobs honesta.
- **Producción (VPS `prelyra`, llamada real +573117103957):**
  ```
  [stt] openai realtime connected model=gpt-4o-mini-transcribe
  raw="...aquí al Valle del Ortigal, si es posible."
  [nlu] intent=provide_pickup pickup='...Valle del Ortigal' conf=0.85
  [orchestrator] speculative geocode prewarm 'Valle del Ortigal'
  [geocoder] resolved → 'manzana 23#2a28, Popayán' + barrio override 'Valle del Ortigal'
  "Sí, por favor." → confirm_yes → creating_service
  [backend] create_service canal=FREESWITCH_AI_CALL origen='Valle del Ortigal'
  [orchestrator] WhatsApp template sent status=200
  [recorder] saved seconds=37.0 ; uuid_kill ok=True
  ```
  Sin errores ni tracebacks. Flujo idéntico al de Deepgram (confirmación implícita,
  geocoding especulativo, colgado limpio).
- **Segunda llamada de producción (da5b4e14, +573117103957, tras desplegar el fix del
  paso 3):** dirección con números transcrita correcta —
  `raw="...aquí en Carrera 52, calle número 3C6." → norm="...carrera 52 calle # 3c6"`,
  NLU `provide_pickup conf=0.90`. Google devolvió solo `GEOMETRIC_CENTER conf=0.50`
  (dirección ambigua) → la regla anti-autoaceptación enrutó a `CONTEXT_GATHERING`
  (pide más contexto) en vez de despachar a coordenada dudosa — **geocoder
  precision-first funcionando, no un fallo de STT**. El usuario colgó antes de aclarar
  → cierre único y limpio, sin churn de reconecto. No creó servicio (comportamiento del
  usuario + dirección ambigua, ambos esperados).

---

## 6. Qué se logró

- Sistema unificado: **navegador y telefonía transcriben exclusivamente con OpenAI
  `gpt-4o-mini-transcribe`**. Sin `whisper-1`, sin Groq, sin Deepgram en el árbol ni
  en `.env`. Sin compatibilidad dual.
- El síntoma original (barrios mal transcritos rompiendo direcciones) **resuelto y
  verificado en una llamada real de producción**.
- Flujo de la llamada intacto: solo cambió el proveedor de STT.

## 7. Pendiente / notas

1. ~~Reiniciar `prelyra` para tomar el fix del reconecto fantasma~~ — **hecho**
   (23:54, arranque limpio). Falta observar el log de una llamada que cree servicio
   para confirmar que ya no hay `[stt] connected` en el reconecto terminal.
2. **Costo:** OpenAI Realtime se factura por tokens de audio — vigilar el consumo bajo
   carga real (antes Deepgram era más barato). Revisar límites de concurrencia del plan.
3. Barge-in real (interrumpir a Lyra a media frase) implementado (`uuid_break`) pero
   aún sin verificar en vivo.
4. `scratch/realtime_stt_probe.py` queda como herramienta de re-verificación del
   protocolo (útil si OpenAI cambia el shape del Realtime a futuro).
5. Sin commits — los hace el usuario manualmente.

## 8. Referencias
- Handoff del despliegue V2: `docs/voice/HANDOFF_2026-07-19.md`
- Guía de despliegue: `docs/freeswitch/STREAMING_DEPLOY.md`, `docs/freeswitch/VPS_DEPLOY.md`
- Implementación V2: `docs/voice/LYRA_VOICE_V2_IMPLEMENTATION.md`
- STT nuevo: `services/voice/stt_stream.py`; probe: `scratch/realtime_stt_probe.py`
