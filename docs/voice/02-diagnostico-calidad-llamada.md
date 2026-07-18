# Diagnóstico: calidad de comprensión en llamada de despacho (2026-07-18)

Queja usuario: bot suena robótico/plantilla, hace preguntas genéricas, no
entiende cuando el llamante se corrige a sí mismo ("perdón, es tal
dirección"), no maneja bien cuando hay otra persona hablando. Prioridad:
**entender al usuario y darle la dirección correcta al conductor.**

Audio real de referencia: `05004a4f-0e46-4ec8-aa36-76ac698fca61.wav`
(no se pudo transcribir en esta sesión — sin ffmpeg/python en el entorno;
pendiente análisis manual o con herramienta que sí tenga el binario).

## Causa raíz — 4 problemas independientes, no uno solo

### 1. Respuestas del bot = plantillas fijas, no generación real (causa de "robótico")
Ningún texto que dice el bot pasa por LLM. Son f-strings con slots:
- `services/telephony/voice_call_engine.py:55-58` — `GREETING` fijo.
- `:184`, `:420`, `:455`, `:582`, `:590`, `:650` — líneas fijas por estado.
- `api/routers/twilio.py:1765` — mismo patrón (handler de producción).
- Reintentos/silencio: dict `(state, count) -> texto fijo` (`voice_call_engine.py:214-224`).
- El único uso de LLM es `_extract_origin_llm` (`:659-687`), y solo para
  **extraer** el lugar, no para **redactar** la respuesta.

Efecto: cero variación, cero adaptación al tono/contexto del llamante →
sensación de plantilla.

### 2. Auto-corrección del llamante ("perdón, es...") — no se detecta en turno cerrado
- `core/stt_enhancer.py:841-892` (`repair_location_transcription`) — el
  nombre engaña: solo corrige **ortografía/fonética** de un lugar ya
  transcrito contra el catálogo. No es corrección conversacional.
- `core/address_utils.py:432-435` (`_is_correction_request`) — detección
  por keywords: `corregir, cambiar, equivoke, me equivoque, no es ahi,
  esta mal, error`. **No incluye** "perdón", "espera", "mejor dicho",
  "digo", "corrijo" — justo las muletillas reales que la gente usa.
- `core/conversation_repair.py:407-443` (`BargeInHandler`,
  `INTERRUPT_SIGNALS` en `:415-419`) sí cubre "espera"/"me equivoqué", pero
  **solo dispara sobre resultados parciales mientras el TTS está
  hablando** (`api/routers/twilio.py:2593-2617`) — y ni siquiera cancela el
  TTS todavía (`:2604-2607`, comentario explícito: "Por ahora: loguear").
- **No existe ninguna ruta que tome un turno ya cerrado/final** ("perdón,
  es carrera 5 con calle 3") **y separe la parte antes/después de la
  muletilla de corrección**. Hoy ese string completo se manda tal cual a
  `resolve_location_entity`/extracción LLM, con "perdón, es" contaminando
  la entrada.

Esto explica directamente la queja del usuario: el llamante se corrige en
una sola frase (no interrumpe al bot, no es barge-in), y el sistema no
tiene ese caso cubierto.

### 3. STT bias — existe pero es genérico y estático, no por-llamada
- `services/telephony/stt_service.py:_build_stt_prompt` (`:32-60`):
  vocabulario fijo (Popayán, Cauca, barrios conocidos + hasta 40 términos
  del catálogo) inyectado como `prompt` a Whisper/OpenAI.
- Se cachea **una sola vez por proceso** (`_STT_PROMPT_CACHE`) — no se
  ajusta con lo que ya se sabe de la llamada en curso (ej. si el usuario
  ya dijo el barrio, no se refuerza ese barrio específico en el siguiente
  turno).
- No sesga vocabulario de corrección/números/muletillas — solo nombres de
  lugar.

### 4. Máquina de estados — un solo slot, guion fijo, no adaptativo
- `voice_call_engine.py`: solo modela **un** slot (`origen`).
  `ASK_DESTINATION = False` (`:52`) — destino está **desactivado por
  completo**, contradice la meta de "dar bien la ubicación al conductor"
  si el destino también importa.
- Flujo lineal fijo: `STATE_WAITING_ORIGIN → STATE_WAITING_GEO_CONTEXT →
  STATE_CONFIRMING_ORIGIN → STATE_CREATING_SERVICE → STATE_FINISHED`.
  El orden de preguntas no se adapta a lo que el usuario ya dijo
  espontáneamente.
- **Corrección de memoria de proyecto**: memorias previas mencionaban
  `classify_turn`, slot-filling multi-slot y catálogo RC1-RC7
  "implementados". Grep de todo el repo no encuentra `classify_turn` ni
  esa lógica — no existe en el código actual. Esas memorias están
  desactualizadas (referían a `docs/voice/02..09` que ya no existen en el
  repo) y se corrigen con este documento.
- `twilio.py` (handler más completo, el que de verdad corre en producción)
  repite el mismo patrón: if/else lineal sobre `_is_correction_request`/
  `_parse_si_no`, no un motor de slots generalizado.

### 5. No cubierto en absoluto
- "Está con otra persona" / llamante de terceros — sin detección.
- Ruido de fondo / cross-talk (dos voces simultáneas) — `audio_preprocess.py`
  hace resample/high-pass/normalize, no VAD multi-hablante ni diarización.

### Nota aparte — no es el motor STT en sí
`logs/asr_events.jsonl` (datos viejos de prueba, jun-2026) muestra
transcripciones cortadas a exactamente 10000ms fijos — evidencia de un
chunking rígido de una arquitectura anterior. La configuración actual de
producción (`docs/freeswitch/lyra_call.lua:23-25`: `UTT_MAX_LEN=15s`,
silencio de cierre `UTT_SIL_SECS=3`) ya no trabaja así, así que ese
síntoma específico probablemente ya no aplica — pero confirma que en el
pasado la calidad de transcripción se vio afectada por corte de audio, no
solo por el modelo.

## Propuesta priorizada (impacto en "entender bien y dar ubicación correcta")

**P0 — corrección de auto-repair en turno cerrado** (el bug que el usuario
reportó explícitamente)
- Ampliar `_is_correction_request` con marcadores reales: "perdón",
  "espera", "mejor dicho", "digo", "corrijo", "no espera".
- Nueva función: dado un turno final con marcador de corrección en medio,
  quedarse solo con el segmento **posterior** al marcador (reusar la
  lógica de `extract_post_interrupt_content` de `conversation_repair.py`
  pero aplicada a turnos completos, no solo parciales).

**P1 — slot de destino** (ahora mismo apagado, y sin destino el conductor
no tiene ruta completa)
- Reactivar `ASK_DESTINATION` con extracción propia (reusar
  `_extract_origin_llm` generalizado a "extract_place_llm").

**P1 — dejar de sonar plantilla sin reescribir todo el motor**
- No hace falta LLM en cada línea. Suficiente con 3-4 variantes por
  estado + selección aleatoria/contextual, y usar lo ya extraído para
  personalizar la frase (nombre del barrio, hora, etc.) — bajo costo, alto
  impacto perceptual.

**P2 — bias STT por-llamada**
- Reforzar el prompt de Whisper dinámicamente con el barrio/vía ya
  mencionados en la llamada en curso (no solo el catálogo estático).

**P2 — tercero en la llamada / cross-talk**
- Requiere diarización o heurística de energía dual — evaluar costo/
  beneficio aparte, no es trivial con el pipeline actual.

## Siguiente paso
Este documento es diagnóstico, no implementación. ¿Con cuál prioridad
arrancamos — P0 (auto-corrección) primero, dado que es la queja concreta
que trajiste?
