# Resumen de sesión — Auditoría de voz Lyra + diseño V2 (2026-07-18)

Contexto para continuar en una nueva sesión. Todos los documentos generados están en `docs/voice/audit_2026-07-18/`.

---

## Cronología de lo que se hizo

### 1. Auditoría técnica completa del sistema de llamadas (18 agentes/subagentes en total a lo largo de la sesión)

Primer pedido: auditoría exhaustiva del pipeline de voz completo (arquitectura, VAD, STT, TTS, LLM, geocoding, dependencias/infra). Se lanzaron 6 agentes paralelos, cada uno leyó código real con citas `archivo:línea`. Resultado consolidado en:

**`MASTER_AUDIT.md`** — hallazgo raíz: el sistema en producción es un IVR graba→sube→transcribe→responde→descarga→reproduce por turno completo (record-loop vía `docs/freeswitch/lyra_call.lua` + `api/routers/freeswitch.py:audio_turn`), no streaming. Ya existen escritas y sin usar piezas de arquitectura moderna: `core/streaming_pipeline.py` (STT partials, endpointing adaptativo, LLM streaming) y `core/conversation_repair.py` (barge-in, reparación de errores) — ninguna conectada al flujo real. Piso de latencia por turno: ~3.5-6s. Concurrencia real=1 (MySQL síncrono bloqueando el event loop). Plan de 6 fases incluido.

### 2. Script de diagnóstico de audio independiente

Se creó `scratch/audio_diagnostic.py` (no toca el sistema principal) — analiza WAV (duración, sample rate, RMS/pico dBFS, clipping, SNR, VAD por energía, espectro, %habla/silencio, velocidad de habla) y opcionalmente compara transcripciones de múltiples motores STT (`--transcribe`: OpenAI gpt-4o-mini-transcribe, whisper-1, Groq whisper-large-v3) con similitud/diff de palabras.

Se corrió sobre 2 audios reales del usuario:
- `05004a4f-0e46-4ec8-aa36-76ac698fca61.wav`: 42s, 8kHz, SNR 50dB, sin clipping. `gpt-4o-mini-transcribe` **perdió una frase entera** del usuario ("Sí, recogerme en la terminal de Toro Bajo") que `whisper-1` sí capturó. Similitud 78.9%.
- `2aea56d5-8f3a-4c36-a602-c4b735ae8f65.wav`: 54.5s, 8kHz, SNR 48.6dB. Similitud solo 61%. Patrón repetido en ambos audios: los dos motores fallan sistemáticamente el nombre de la empresa ("Cabs del Alcázar" → variantes erróneas en ambos) — mishearing sistemático de nombre propio, no ruido aleatorio.

Conclusión de esta parte: la calidad acústica no es el problema (confirmado con datos reales), el motor/vocabulario STT sí.

### 3. Traza forense: qué modelo STT usa realmente producción

Pedido explícito de no confiar en conclusiones previas y verificar todo desde cero. Resultado en **`STT_TRACE_VERIFICADO.md`** — traza completa salto por salto desde FreeSWITCH hasta el FSM, con archivo:línea en cada paso.

**Hallazgo crítico**: el `.env` fue modificado el mismo día de la sesión (21:40) fijando `OPENAI_STT_MODEL=whisper-1` y `TELEPHONY_STT_MODEL=whisper-1` explícitamente — estas variables tienen prioridad absoluta sobre el default de código (`gpt-4o-mini-transcribe`) en `stt_service.py:96-101`. Pero `logs/lyra.log` termina en 01:05:11 (antes del cambio de `.env`) — no hay ningún log que confirme si el proceso vivo ya recogió el `.env` nuevo, porque el modelo se resuelve **una sola vez**, al arrancar el proceso (constructor de `TelephonySTTService`).

Respuestas verificadas:
1. Modelo real usado hasta la última llamada registrada: `gpt-4o-mini-transcribe` (demostrado por logs de la app, no supuesto). Será `whisper-1` en el próximo reinicio del proceso si el `.env` no cambia.
2. Sí existe camino a Whisper — no por timeout/retry (no existen), sino por config estática de proceso, que es justo lo que el `.env` actual ya especifica.
3. El informe anterior era parcialmente correcto: acertó el default de código y coincidía con logs de ese momento, pero no vio el `.env` modificado después.
4. El script de diagnóstico NO reproduce el pipeline exacto de producción: cliente distinto (sync vs async), sin prompt de vocabulario Popayán, sin preprocesamiento DSP, sin postprocesamiento (alucinación/normalización/eco).

Se volvió a correr el script de diagnóstico con credenciales corregidas por el usuario sobre ambos audios (mismos resultados de arriba, ya con transcripciones reales comparadas).

### 4. Diseño de Lyra Voice V2 (8 agentes: 7 investigación + 1 síntesis)

Pedido: diseñar arquitectura completa del motor conversacional de voz vía ingeniería inversa del estado del arte, sin tocar lógica de negocio. 7 agentes de investigación en paralelo (con web search, citas obligatorias) + síntesis final propia:

- **Agente 1** — PersonaPlex (NVIDIA): reverse-engineering completo. Es un modelo único E2E (fine-tune de Moshi/Kyutai), no una arquitectura cascada — no replicable directamente, pero sí sus principios: full-duplex real, endpointing aprendido (no timer fijo), streaming frame-a-frame. Latencias publicadas: turn-taking 0.170s, interrupción 0.240s (checkpoint liberado).
- **Agente 2** — comparación técnica de 14 plataformas SOTA (OpenAI Realtime, Gemini Live, Azure, Deepgram, ElevenLabs, Retell, Vapi, Bland, LiveKit, Pipecat, Cartesia, AssemblyAI, NVIDIA Riva/NeMo) con tabla comparativa de transporte/endpointing/streaming/barge-in/latencia, todo citado.
- **Agente 3** — técnicas de comprensión de lenguaje natural (no STT/TTS): structured outputs con constrained decoding (FSM + logit masking, mecanismo real explicado), function-calling-as-parser, y el hallazgo de mayor consenso: el LLM debe extraer solo el span de texto, la resolución/entity-linking va en un sistema determinista separado.
- **Agente 4** — técnicas de reducción de latencia: preemptive generation sobre partials, streaming LLM→TTS por oración, ejecución especulativa de tool calls de solo-lectura con rollback, WebRTC vs WebSocket, model routing.
- **Agente 5** — naturalidad conversacional: confirmación implícita en vez de loops de sí/no, barge-in real (AEC del lado servidor para PSTN, no solo WebRTC), autocorrección intra-turno señalado como área sin solución establecida en la literatura (gap honesto).
- **Agente 6** — extracción semántica de direcciones: NER/structured-output para el span, frontera LLM↔geocoder confirmada por múltiples papers, referencias indirectas ("frente al hospital") como campo separado que igual resuelve el geocoder.
- **Agente 7** — clasificación reuse/replace del código actual, verificada leyendo archivos reales (no solo confiando en auditorías previas): confirmó y corrigió un detalle del `MASTER_AUDIT.md` (`conversation_repair.py` no está 100% muerto — `get_progressive_retry_message` sí se usa en producción).

**`LYRA_VOICE_V2_SPEC.md`** (síntesis propia, no de agente) — especificación completa:
- Frontera dura de qué NO cambia (backend, WhatsApp, DB, geocoder_service/location_match/address_utils/popayan_geodata, estados de negocio del FSM).
- Arquitectura de 7 capas: transporte streaming (mod_audio_stream WS), STT streaming con endpointing híbrido, capa NLU nueva (structured outputs, extrae solo spans, nunca resuelve direcciones), Turn Orchestrator (preserva estados de negocio, agrega generación anticipada + ejecución especulativa de geocoding read-only), TTS streaming, barge-in real con AEC servidor.
- Tabla de mapeo latencia V1→técnica V2 para cada fuente de latencia identificada en el audit inicial.
- Sección explícita de "qué no quedó validado" (soporte español de Deepgram Flux/AssemblyAI, calidad TTS español de Cartesia, costos, compatibilidad real de `mod_audio_stream`) — para no vender certeza donde no la hay.

---

## Estado actual / qué falta

Todo lo hecho es **investigación y diseño**, no implementación. No se modificó ningún archivo del sistema en producción (solo se creó `scratch/audio_diagnostic.py`, standalone, y los 4 docs de `docs/voice/audit_2026-07-18/`).

Próximos pasos naturales si se retoma:
1. Validar los ítems marcados como "sin validar" en `LYRA_VOICE_V2_SPEC.md` §5 (español en Deepgram/AssemblyAI, calidad TTS Cartesia, costos, `mod_audio_stream` real).
2. Confirmar en qué estado quedó el `.env` (modelo STT real hoy) antes de tocar nada — reiniciar el proceso y verificar el log `[stt/openai] provider enabled model=...`.
3. Decidir si se ejecuta el plan de fases de `MASTER_AUDIT.md` (contención/backend async primero) en paralelo o antes de arrancar V2.
4. Si se decide avanzar con V2: empezar por la capa NLU (menor riesgo, no toca transporte ni FSM) como piloto aislado antes de tocar transporte/STT/TTS.

## Archivos de esta sesión

- `docs/voice/audit_2026-07-18/MASTER_AUDIT.md`
- `docs/voice/audit_2026-07-18/STT_TRACE_VERIFICADO.md`
- `docs/voice/audit_2026-07-18/LYRA_VOICE_V2_SPEC.md`
- `docs/voice/audit_2026-07-18/SESION_RESUMEN.md` (este archivo)
- `scratch/audio_diagnostic.py`
