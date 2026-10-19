# Auditoría Técnica Integral — Sistema de Llamadas de Voz (Lyra / FreeSWITCH)

**Fecha:** 2026-07-18
**Alcance:** línea de despacho de taxis por voz (Popayán), pipeline FreeSWITCH → STT → FSM/LLM → geocoding → TTS.
**Método:** 6 auditorías paralelas de código real (con cita `archivo:línea`) + síntesis arquitectónica.

> Nota de contexto ya resuelta: audio se escucha bien, se graba, se almacena y se reproduce en frontend. Ese problema **no es el que queda**. Lo que queda es: ASR, latencia, fluidez, interrupciones, silencios, comprensión, naturalidad, contexto, recuperación de errores.

---

## 0. Hallazgo más importante (root cause transversal)

El sistema en producción (`services/telephony/voice_call_engine.py` + `docs/freeswitch/lyra_call.lua`) **no es un agente de voz streaming — es un IVR de graba-sube-transcribe-responde-descarga-reproduce, turno a turno, con archivos WAV completos en cada paso.** Cada uno de los 6 informes llegó de forma independiente a la misma conclusión desde ángulos distintos (VAD, STT, TTS, LLM, geocoding, infra). Esto explica simultáneamente: la latencia percibida, la falta de barge-in, las repeticiones forzadas y la sensación de "plantilla" que describiste tras probar el sistema comercial.

Además: **existen ya, escritas y sin usar, piezas de una arquitectura moderna** (`core/streaming_pipeline.py` con STT streaming/partials/endpointing adaptativo/LLM streaming, y `core/conversation_repair.py` con manejo de barge-in y respuestas de reparación contextuales). Ninguna de las dos está conectada al flujo real de la llamada (`voice_call_engine.py`). Es decir: parte del "sistema de clase mundial" que pides ya fue diseñado en este repo — solo falta cablearlo.

---

## 1. Arquitectura actual del pipeline de llamada

Dos implementaciones paralelas coexisten en el código; **solo una está en producción** según el dialplan (`docs/freeswitch/99_lyra_ai.xml`):

**A) Record-loop (la que corre en producción hoy)**
```
FreeSWITCH answer()
  → session:sleep(400ms)                       [espera fija]
  → record_session (graba llamada completa)
  → LOOP (máx 20 turnos):
      session:recordFile(UTT_MAX_LEN=15s, UTT_SIL_THR=200, UTT_SIL_SECS=3s)
      → base64 encode (disco)
      → busybox wget --post-file → POST /freeswitch/audio-turn   [bloqueante, SIN timeout]
          → decodificar WAV → gate silencio -45dBFS → STT (REST, 1 llamada, sin streaming)
          → filtro alucinación → normalización → FSM (voice_call_engine.py)
          → [posible geocoding síncrono: hasta ~20s peor caso]
          → TTS (edge-tts, no streaming real) → ffmpeg → WAV en disco
      → busybox wget (descarga WAV respuesta)                     [bloqueante, SIN timeout]
      → session:streamFile() (reproduce completo, sin barge-in)
  → hangup → sube grabación completa → /freeswitch/recording
```

**B) WebSocket / mod_audio_stream (código completo, vivo en el repo, NO enrutado por el dialplan actual)**
- `api/routers/freeswitch.py:960-1227` implementa VAD adaptativo, buffer de audio, playback-gating — es la base de una arquitectura streaming real, pero está desconectada de tráfico real.

**Veredicto arquitectónico:** el diseño es turn-based, half-duplex, basado en archivos completos en cada etapa (STT, TTS, incluso el turno completo se sube/baja como WAV vía `wget`). Esto es estructuralmente lo opuesto a cómo operan Vapi/Retell/Bland/OpenAI Realtime, que mantienen un socket bidireccional persistente con audio en streaming continuo de principio a fin.

---

## 2. Speech-to-Text (por qué falla, por qué repite al usuario)

**Motor real en producción:** OpenAI REST, modelo `gpt-4o-mini-transcribe` (`services/telephony/stt_service.py:19`). Groq/whisper-large-v3 como fallback. **Deepgram está referenciado en `.env` y en comentarios pero es un stub sin implementar** (`stt_service.py:147-150`) — vestigio de la era Twilio/Media-Streams nunca completado.

**Causas raíz concretas:**
1. **Sin streaming ni partials.** Una sola llamada REST bloqueante por turno, después de que FreeSWITCH ya cortó la grabación. Cero hipótesis parciales llegan a producción — `core/streaming_pipeline.py` las implementa pero está desconectado (ver §0).
2. **Confianza falsa.** El modelo en uso (`gpt-4o-mini-transcribe`) nunca reporta `no_speech_prob`/confidence real; el código hardcodea `confidence = 1.0` siempre (`stt_service.py:313`). Toda lógica downstream "basada en confianza" es un no-op.
3. **`temperature` sin fijar** para el modelo real en uso (solo la rama whisper fija `temperature=0.0`) — más varianza/alucinación de la necesaria.
4. **Sesgo de vocabulario débil:** un string estático de ~48 términos vía el campo `prompt` (soft-priming, no boosting real de decodificación), igual en todos los estados de diálogo — no hay narrowing contextual (ya diseñado en el módulo muerto `streaming_pipeline.py:_get_contextual_hints`, sin cablear).
5. **Corta direcciones a la mitad:** `UTT_SIL_SECS=3` es un único umbral fijo, sin adaptación a velocidad de habla ni a pausas naturales dentro de una dirección larga ("calle dieciséis... [pausa >3s] ...cuarenta y uno"). El propio módulo muerto (`AdaptiveEndpointController`) ya resuelve esto — no está en uso.
6. **`UTT_MAX_LEN=15s`** corta explicaciones largas de adultos mayores a mitad de frase, sin aviso al usuario.
7. Gate de alucinación por dBFS pico estático (-45dBFS) + lista fija de frases conocidas de alucinación de Whisper — reactivo, no generalizable.

**Qué existe en otro repo/módulo y no se usa:** `speech/` (faster-whisper offline, Silero VAD, `phonetic_index`, `confusion_miner`, `learning_loop`) — infraestructura de aprendizaje ya construida per memoria de proyecto, completamente desconectada del path real de la llamada telefónica.

---

## 3. Text-to-Speech

**Motor:** `edge-tts` (wrapper no oficial sobre Microsoft Edge Read-Aloud, sin SLA). Voz `es-CO-SalomeNeural` (con un default inconsistente `es-BO-SofiaNeural` en otro archivo — bug de config).

**Causas raíz:**
1. **`synthesize_stream()` no transmite nada** — pese al nombre, espera el MP3 completo en memoria antes de trocearlo (`core/voice_engine.py:373-402`). Cero streaming real de síntesis en todo el repo.
2. Pipeline por turno: TTS cloud → MP3 en RAM → archivo temporal → subproceso ffmpeg → WAV en disco → `wget` de descarga por FreeSWITCH → reproducción. 3 archivos temporales + 1 subproceso + 2 saltos HTTP por cada frase que dice el bot.
3. **Sin normalización de texto para habla** (números, direcciones tipo "Cra 17 # 8C-55") antes de sintetizar — el texto crudo del LLM/FSM va directo a TTS.
4. **Sin caché de frases fijas** (saludo, confirmaciones estándar) — se resintetiza vía la nube en cada llamada aunque el texto sea idéntico.
5. **Sin timeout** en la llamada de síntesis — un cuelgue de edge-tts puede colgar el turno indefinidamente.
6. Fallo silencioso: si la síntesis falla, la llamada continúa **sin reproducir nada y sin frase de error** — silencio total para el usuario.

---

## 4. LLM / orquestación / prompts

**Hallazgo clave, confirma la queja de memoria ("bot plantilla fija, sin LLM en respuestas", 2026-07-18): es verdad y es estructural, no una regresión.**

- `voice_call_engine.py` (la FSM real del teléfono) **no importa nada de `orchestrator/`**. Cada respuesta hablada es un f-string embebido directamente en la lógica de transición de estado.
- El **único** uso de LLM en toda la llamada telefónica es `_extract_origin_llm()` (`voice_call_engine.py:659-687`): extracción de entidad de respaldo cuando el matching local falla, `timeout=4s`, `max_tokens=60`, sin `temperature` fijada (inconsistente con el resto del repo). Nunca genera el texto que escucha el usuario.
- `core/conversation_repair.py` (hipótesis contextuales, `BargeInHandler`, respuestas de reparación inteligentes en vez de "no entendí") está completamente escrito y **nunca importado** por la FSM real.
- El stack `orchestrator/` (chat de texto, WhatsApp) sí tiene tool-calling real con OpenRouter/gpt-4o-mini y buena compresión de historial — pero es un producto distinto, no la llamada telefónica.
- `core/streaming_pipeline.py::stream_llm_response()` ya implementa LLM streaming con callback para arrancar TTS incrementalmente — cero llamadas a esta función en todo el repo.

**Conclusión:** el "sistema de clase mundial" al que quieres llegar en la parte conversacional ya tiene sus piezas de diseño escritas (`conversation_repair.py`, `streaming_pipeline.py`) — el trabajo pendiente es de integración, no de invención desde cero.

---

## 5. Geocoding / resolución de direcciones

Pipeline real: `Cache (LRU+MySQL) → Google Places Autocomplete → Google Geocoding → Google Places Text Search → Nominatim → CONTEXT_GATHERING`, con reintentos acotados (`MAX_PIPELINE_ATTEMPTS=3`).

- **Buen diseño de precisión:** matching tipado (`location_match.py`) con jerarquía EXACT>ALIAS>SUBSTRING>PHONETIC>FUZZY, regla anti-autoaceptación de resultados de baja precisión (`_NEVER_AUTOACCEPT`), corrección de "barrio" poco fiable de Google mediante alias locales — todo con motivación documentada en incidentes reales previos.
- **Riesgo de latencia real:** en el peor caso, la cadena Google→Google→Google→Nominatim puede encadenar hasta ~18-24s de HTTP síncrono **dentro del turno de llamada en vivo**, sin presupuesto de tiempo máximo ni frase de "espera" explícita en ese tramo.
- **Nominatim serializado globalmente** a 1 request/1.1s (`_NOM_LOCK`) — el propio código admite en un comentario que esto genera latencia bajo carga concurrente y sugiere self-host, sin implementar.
- **Triplicación de índices**: tres módulos (`geocoder_service.py`, `location_match.py`, `address_utils.py`) construyen cada uno su propio índice de alias de barrios/lugares desde el mismo `popayan_geodata.py`, de forma independiente — riesgo de divergencia si se agrega un alias nuevo.
- Matching por bigramas de caracteres + heurística fonética + `SequenceMatcher`, no embeddings ni Levenshtein real — funciona bien para Popayán por estar calibrado con casos reales, pero no escala ni generaliza como un enfoque semántico/embedding.

---

## 6. Dependencias, código muerto, infraestructura

- **Concurrencia real = 1**: `uvicorn.run(..., workers=)` no está configurado (`main.py:245-250`), y **MySQL es síncrono** (`PyMySQL`, sin driver async) en el camino caliente de cada turno (`orchestrator/memory_manager.py` y otros 8 archivos) — **una sola llamada lenta a MySQL bloquea el event loop para todas las llamadas concurrentes**. Este es probablemente el hallazgo de mayor apalancamiento de toda la auditoría de infraestructura.
- `pusher.Pusher.trigger()` también es síncrono y se invoca dentro de `async def lifespan()` — puede bloquear el loop durante recargas de configuración.
- Limpieza de Twilio incompleta: campo `"twilio_fallback_active": True` hardcodeado y **falso** en `/freeswitch/health`; credenciales Twilio vivas y sin revocar en `.env`; variables de sintonía de voz Twilio parcialmente aún leídas por el módulo muerto `streaming_pipeline.py`.
- **Logging estructurado de turnos se detuvo el 2026-06-12** (`logs/turns/*.jsonl`) mientras el tráfico real sigue hasta hoy (`logs/lyra.log`) — significa que el diagnóstico de calidad de diálogo del 2026-07-18 (memoria de proyecto) se hizo **sin** los datos estructurados que existían para depurar justo este tipo de problema. No hay instrumentación de latencia por etapa (STT/LLM/TTS) en ningún log.
- Sin lockfile de dependencias (`requirements.txt` con pisos sueltos, ej. `openai>=1.0.0` sin techo).
- VAD es 100% energía/RMS (stdlib `audioop`), no hay ningún modelo ML de VAD (`.onnx`/`.pt`) en el repo pese a que `numpy`/`scipy` están instalados (aparentemente no usados por el VAD).

---

## 7. Latencia — mapa completo de dónde se pierde tiempo

| Etapa | Fuente | Tiempo |
|---|---|---|
| Post-answer | `session:sleep(400)` fijo | 400ms |
| Fin de turno del usuario | `UTT_SIL_SECS=3` (silencio fijo antes de cortar grabación) | 3000ms mínimo garantizado por turno |
| Subida de audio | base64 + `wget --post-file`, **sin timeout** | variable, sin cota superior |
| STT | 1 llamada REST bloqueante a OpenAI, sin streaming | cientos de ms a ~2s |
| Geocoding (si aplica) | Cadena síncrona Google×3 + Nominatim, timeouts de 6s cada uno | hasta ~18-24s peor caso |
| LLM extracción origen (fallback) | `timeout=4.0` | hasta 4s cuando se dispara |
| Backend crear servicio | `httpx` read timeout 8s | hasta 8s |
| TTS | edge-tts completo (no streaming) + ffmpeg ×1-2 + escritura a disco | varios cientos de ms a 1-2s+ |
| Descarga de audio de respuesta | `wget`, **sin timeout** | variable |
| Gate post-reproducción (path WS) | `_PLAYBACK_GATE_TAIL_SEC=0.4` | 400ms extra de "sordera" tras cada frase del bot |
| Hangover VAD (path WS) | `FS_VAD_HANGOVER_MS=600` | 600ms |

**Piso de latencia por turno, incluso en el mejor caso, sin geocoding lento:** ~3.5-6s de silencio+red+proceso antes de que el usuario oiga respuesta — comparable a un IVR clásico, no a un agente conversacional moderno (objetivo de industria: <800ms primer audio).

**Reducciones concretas, en orden de apalancamiento:**
1. Streaming real de extremo a extremo (audio bidireccional continuo, STT streaming, LLM streaming→TTS streaming) elimina la mayoría de las esperas fijas de golpe.
2. Endpointing adaptativo a velocidad de habla (ya escrito, sin cablear) en vez de `UTT_SIL_SECS=3` fijo.
3. Presupuesto de tiempo máximo + "voy a verificar tu dirección, un momento" temprano para la cadena de geocoding.
4. Quitar los `wget` sin timeout (riesgo de colgar la llamada indefinidamente, no solo de latencia).
5. MySQL async / workers>1 para que la latencia de una llamada no contamine a las demás.

---

## 8. Comparación con el estado del arte

| Dimensión | Este sistema | Vapi/Retell/Bland/OpenAI Realtime/Deepgram/ElevenLabs |
|---|---|---|
| Transporte de audio | Archivo completo por turno, subido/bajado por HTTP (`wget`) | Socket bidireccional persistente, streaming continuo |
| STT | REST batch, 1 llamada por turno, sin partials | Streaming, hipótesis parciales cada 100-300ms |
| Endpointing | Umbral de silencio fijo (3s), energía pura | VAD/endpointing adaptativo, a veces semántico |
| TTS | Generación completa, sin streaming real, sin SSML | Streaming, <300ms al primer byte, control de prosodia |
| Interrupciones | No existen — el audio del usuario durante el TTS se descarta, no se actúa sobre él | Barge-in real con cancelación de eco |
| LLM↔TTS | LLM casi no genera el habla (FSM fija); cuando se usa LLM, es bloqueante, no streaming | Tokens LLM alimentan TTS incrementalmente |
| Vocabulario/contexto | Prompt estático, igual en todo el diálogo | Boosting dinámico por estado de conversación |
| Concurrencia backend | 1 worker, MySQL síncrono en el hot path | Arquitectura async/escalable horizontal |

---

## 9. Plan de evolución (fases)

### Fase 0 — Contención inmediata (1-2 semanas)
- **Objetivo:** eliminar riesgos de cuelgue y datos falsos sin tocar arquitectura.
- Timeouts a los `wget` de Lua; instrumentar latencia por etapa en logs estructurados; restaurar `logs/turns/*.jsonl`; arreglar `twilio_fallback_active`; fijar `temperature=0` y confianza real donde sea posible en STT; revocar credenciales Twilio muertas.
- **Beneficio:** visibilidad real + deja de haber riesgo de llamada colgada indefinidamente.
- **Riesgo:** bajo. **Esfuerzo:** bajo. **Dependencias:** ninguna.

### Fase 1 — Cablear lo que ya existe (2-4 semanas)
- **Objetivo:** conectar `core/conversation_repair.py` (barge-in handler, respuestas de reparación) y las partes reutilizables de `core/streaming_pipeline.py` (endpointing adaptativo, contextual hints) al flujo real (`voice_call_engine.py`), sin migrar transporte todavía.
- **Beneficio:** mejoras de fluidez/naturalidad/recuperación de errores con esfuerzo relativamente bajo porque el diseño ya está hecho.
- **Riesgo:** medio (cambio de comportamiento en sistema de despacho real, requiere pruebas). **Dependencias:** Fase 0 (logging para validar el cambio).

### Fase 2 — Backend no bloqueante (2-3 semanas, en paralelo con Fase 1)
- **Objetivo:** MySQL async o `run_in_threadpool`, Pusher no bloqueante, `workers>1`.
- **Beneficio:** capacidad de concurrencia real; una llamada lenta deja de degradar a las demás.
- **Riesgo:** medio (estado en memoria por-worker, ej. rate limiting, debe revisarse). **Dependencias:** ninguna, independiente de las demás fases.

### Fase 3 — Streaming real de transporte de audio (4-8 semanas)
- **Objetivo:** mover la producción del path record-loop al path WebSocket/`mod_audio_stream` ya existente en el código, con STT streaming real (Deepgram/Google/OpenAI Realtime) y VAD/endpointing sobre el stream continuo.
- **Beneficio:** el mayor salto de latencia y naturalidad de todo el plan — elimina la mayoría de las esperas fijas de la Fase 0/tabla de latencia.
- **Riesgo:** alto (cambio de arquitectura de transporte en sistema de despacho en producción; requiere plan de rollback y canary). **Dependencias:** Fase 0 (visibilidad), Fase 2 (backend debe soportar la carga).

### Fase 4 — TTS/LLM streaming de extremo a extremo con barge-in real (4-6 semanas)
- **Objetivo:** LLM streaming → TTS streaming (`stream_llm_response` ya escrito) + interrupción real con cancelación de eco (AEC), reemplazando el "gateo" actual que descarta audio del usuario durante el TTS.
- **Beneficio:** esto es específicamente lo que hace que el sistema comercial que probaste "nunca parezca confundirse" y no genere silencios incómodos — es la pieza que más se acerca a "clase mundial".
- **Riesgo:** alto (AEC y duplex real son la parte técnicamente más difícil). **Dependencias:** Fase 3 (requiere transporte streaming ya funcionando).

### Fase 5 — Consolidación y calidad continua (continuo)
- **Objetivo:** unificar los 3 índices de barrio duplicados, unificar los 2 diccionarios de corrección STT, cablear o eliminar `learning_loop`/`confusion_miner`/`phonetic_index`, normalización de texto para TTS (direcciones/números), caché de frases fijas.
- **Beneficio:** menor deuda técnica, menos riesgo de divergencia entre módulos duplicados, mejora incremental continua de precisión.
- **Riesgo:** bajo. **Dependencias:** ninguna, puede correr en paralelo con todo lo anterior.

---

## 10. Lista priorizada global (top 12, impacto × esfuerzo × riesgo)

| # | Fix | Impacto | Esfuerzo | Riesgo |
|---|---|---|---|---|
| 1 | Timeout en `wget` de Lua (evita cuelgue indefinido de llamada) | Alto | Bajo | Bajo |
| 2 | MySQL async / `run_in_threadpool` en hot path | Alto | Medio | Medio |
| 3 | Cablear `conversation_repair.py` (barge-in, reparación) a `voice_call_engine.py` | Alto | Medio | Medio |
| 4 | Migrar a transporte streaming (WS/`mod_audio_stream`) como único path | Muy alto | Alto | Alto |
| 5 | STT streaming real (Deepgram/Realtime) reemplazando REST batch | Muy alto | Alto | Medio-alto |
| 6 | LLM streaming → TTS streaming de extremo a extremo | Alto | Alto | Alto |
| 7 | Endpointing adaptativo (ya escrito) en vez de `UTT_SIL_SECS=3` fijo | Alto | Medio | Medio |
| 8 | Presupuesto de tiempo máximo en cadena de geocoding + frase de espera | Alto | Medio | Medio |
| 9 | Confianza real de STT (dejar de hardcodear 1.0) | Medio-alto | Bajo | Bajo |
| 10 | Caché de frases TTS fijas + normalización de texto (números/direcciones) | Medio | Bajo-medio | Bajo |
| 11 | `workers>1` en uvicorn | Alto | Bajo-medio | Medio |
| 12 | Restaurar logging estructurado por turno + instrumentación de latencia por etapa | Medio | Bajo-medio | Bajo |

---

## Apéndice — informes completos por módulo

Los 6 informes detallados (arquitectura/VAD/interrupciones, STT, TTS, LLM/prompts, geocoding, dependencias/infra) con todas las citas `archivo:línea` están disponibles en el historial de esta sesión y pueden anexarse en archivos separados si se requiere trazabilidad línea por línea para cada hallazgo.
