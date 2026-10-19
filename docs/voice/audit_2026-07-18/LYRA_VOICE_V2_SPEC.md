# Lyra Voice V2 — Especificación técnica del motor conversacional de voz

Síntesis de 7 investigaciones independientes (PersonaPlex, plataformas SOTA, NLU/extracción, latencia, naturalidad, extracción de direcciones, clasificación reuse/replace del repo actual) + verificación de código propia de esta sesión.

**Alcance estricto**: solo el motor conversacional de voz. Cero cambios a: creación de servicios, asignación de conductores, WhatsApp, auth, DB, APIs de backend, proveedores de geocoding (Google/Nominatim), reglas de negocio de despacho.

---

## 0. Principio rector, extraído de PersonaPlex sin copiarlo

PersonaPlex no es una arquitectura replicable aquí (es un modelo único fine-tuned de Moshi/Kyutai, sin STT/LLM/TTS discretos — entrenar algo así está fuera de alcance de este proyecto). Lo que sí es transferible, verificado en el informe del Agente 1:

- **Full-duplex real** (escuchar y hablar en la misma ventana de tiempo) es lo que produce interrupción natural — no un "gate" que descarta audio del usuario mientras el bot habla (que es exactamente lo que hace el sistema actual, `ws_audio_buffer.py:gate_playback`).
- **Endpointing no es un temporizador de silencio fijo** — es una señal continua (en PersonaPlex, aprendida; en un sistema cascada, un modelo dedicado). El sistema actual usa `UTT_SIL_SECS=3` fijo — el ítem que más se aleja del estado del arte.
- **Streaming de extremo a extremo** — texto y audio se generan frame a frame, no se espera una oración completa antes de sintetizar. El sistema actual espera el turno completo, en ambas direcciones.

V2 no busca replicar un modelo E2E propietario. Busca aplicar estos tres principios dentro de una arquitectura cascada (STT→NLU→TTS) bien diseñada, que es exactamente lo que hacen Retell/Vapi/LiveKit/Pipecat (confirmado por el Agente 2: todos son cascada, no E2E, y logran 500-700ms).

---

## 1. Frontera de reutilización (del Agente 7, verificada, sin cambios)

**No se toca (bucket A — negocio):** `services/telephony/backend_client.py`, `voice_call_engine.py:94-122` (WhatsApp), `_ORIGIN_ADDRESS_OVERRIDES`/`DTMF_BARRIO_MAP` (datos), `services/telephony/phone_utils.py`, `services/telephony/session_store.py` (contrato de sesión), llamadas HTTP reales a Google/Nominatim.

**Se reutiliza sin modificar (bucket B — utilidades sin acoplamiento a turnos):** `core/location_match.py`, `core/geocoder_service.py` (el pipeline `run_pipeline()` en sí, no las reglas de negocio que contiene), `core/address_utils.py`, `tools/popayan_geodata.py`, `core/stt_enhancer.py`. **Esto es la pieza más importante del diseño**: el resolver de direcciones ya existe, es bueno, y no tiene ningún acoplamiento al motor conversacional — V2 lo llama exactamente igual que V1.

**Se reemplaza (bucket C):** `services/telephony/voice_call_engine.py` (el control-loop FSM, no los *estados de negocio* que representa), `services/telephony/stt_service.py` (patrón REST batch), `services/telephony/tts_service.py` (sin streaming), `docs/freeswitch/lyra_call.lua` (transporte record-loop), el modelo HTTP request/response-por-turno de `api/routers/freeswitch.py`.

**Se rescata parcialmente (bucket D):** `AdaptiveEndpointController` y `_get_contextual_hints`/`_build_hint_vocab` de `core/streaming_pipeline.py`; `ConversationRepair.generate_repair`+`REPAIR_TEMPLATES`+`ZONES` y `BargeInHandler` de `core/conversation_repair.py`; el handler WS ya escrito en `api/routers/freeswitch.py:960-1054` como punto de partida de transporte (no producción hoy, pero más cerca de streaming real que Lua).

---

## 2. Arquitectura V2 — componentes

```
FreeSWITCH (mod_audio_stream, WS bidireccional continuo — NO record-loop)
    ↓ audio streaming continuo, ambas direcciones, sin gate de silencio artificial
Transport Layer (nuevo — reemplaza freeswitch.py:520 audio_turn + lyra_call.lua)
    ↓
Streaming STT Layer (nuevo — reemplaza stt_service.py)
    → emite hipótesis parciales continuamente
    → endpointing híbrido acústico+semántico (no UTT_SIL_SECS fijo)
    ↓ (transcript parcial estable, no espera turno completo)
NLU / Turn-Understanding Layer (nuevo — el corazón del pedido del usuario)
    → LLM con Structured Outputs (json_schema strict/tool_choice forzado)
    → arranca sobre partial STABLE (preemptive generation), no espera fin de turno
    → extrae SOLO spans: {intent, pickup_span, destination_span, confidence}
    → NO resuelve la dirección — solo extrae el span de texto
    ↓ span crudo (ej. "Valle del Hortigal")
Bucket B, sin cambios: core/geocoder_service.py / core/location_match.py
    → resuelve el span a entidad canónica (esto YA funciona bien, no se toca)
    ↓ entidad resuelta
Turn Orchestrator (nuevo — reemplaza el control-loop de voice_call_engine.py,
                    los ESTADOS DE NEGOCIO del FSM se preservan tal cual)
    → decide next_state igual que hoy (waiting_origin → confirming_origin → ...)
    → genera texto de respuesta (rescata ConversationRepair para variación/reparación)
    ↓ texto (streaming, por oración)
Streaming TTS Layer (nuevo — reemplaza tts_service.py)
    → sintetiza por chunk de oración, no espera el texto completo
    → primer audio en <300ms desde el primer chunk de texto
    ↓ audio streaming
Barge-in / Full-duplex controller (nuevo)
    → AEC del lado servidor (no depende de AEC de cliente — no existe en PSTN)
    → clasificador de interrupción real (no backchannel) sobre el audio entrante
    → si interrupción confirmada: cancela síntesis en curso, trunca contexto a lo
      realmente escuchado, entrega el nuevo audio al STT streaming
    ↓
Bucket A, sin cambios: backend_client.py, WhatsApp, DB
```

---

## 3. Especificación por capa

### 3.1 Transporte (reemplaza `lyra_call.lua` + `audio_turn`)

- **Decisión**: usar `mod_audio_stream` de FreeSWITCH (ya soportado por el módulo, confirmado en investigación previa de esta sesión) sobre WebSocket, streaming bidireccional continuo — no grabación de archivo completo por turno.
- El handler WS ya existente en `api/routers/freeswitch.py:960-1054` es el punto de partida (bucket D, salvage), pero su lógica de "gate de reproducción" (mutear/descartar audio del usuario mientras el bot habla) **debe eliminarse**, no rescatarse — es exactamente lo opuesto al full-duplex que se busca (§0).
- Eliminar por completo el ciclo `busybox wget` de `lyra_call.lua` — no hay archivo WAV completo en ningún punto del camino caliente.

### 3.2 STT streaming (reemplaza `stt_service.py`)

- Requisito no negociable: el motor debe exponer **hipótesis parciales** sobre WebSocket y **endpointing híbrido acústico+semántico integrado**, no un temporizador de silencio externo. Del Agente 2 y Agente 4: Deepgram (Nova-3/Flux, 250ms de detección de fin de turno, semántico+acústico) y AssemblyAI (Universal-Streaming, turn detector con `end_of_turn_confidence`) son los dos motores documentados con esta capacidad nativa, ambos vía WebSocket. **Nota de honestidad**: ninguna de las 7 investigaciones verificó soporte de español colombiano específicamente en estos motores — esto es un ítem de validación pendiente, no un hecho confirmado, antes de comprometerse a un proveedor.
- Sesgo de vocabulario: reutilizar el mecanismo de `_build_stt_prompt`/`catalog_terms` (bucket B, ya existe) pero aplicado como *keyword/keyterm boosting real* del proveedor elegido (no un prompt de texto suave como hoy), y — rescatando `_get_contextual_hints` de `streaming_pipeline.py` (bucket D) — narrowing dinámico según el estado del FSM (en `confirming_origin` no hace falta el vocabulario completo de barrios).
- Filtro de alucinación y gate de silencio actuales (`freeswitch.py:408-445`) se conservan como capa adicional, no se descartan — son baratos y ya probados en producción.

### 3.3 NLU / extracción de entidades — responde directamente al ejemplo del usuario

Este es el componente nuevo central. Basado en el Agente 3 y Agente 6:

- **Mecanismo**: OpenAI Structured Outputs (`json_schema` strict mode) o Anthropic `tool_choice` forzado — **no reglas regex, no keyword matching**. La garantía de schema-conformance es por **constrained decoding** (compilación del schema a FSM, logit masking en cada token) — el modelo literalmente no puede emitir un token que rompa el schema. Esto es mecanismo documentado, no una feature de marketing (Agente 3, §5-6).
- **Diseño de schema** (aplicando el hallazgo del Agente 3 §9-10 y Agente 6 §2,7,8): campos anulables, el LLM solo puede rellenar lo que dice explícitamente el usuario, nunca inferir:
  ```json
  {
    "intent": "greeting | provide_pickup | provide_destination | confirm_yes | confirm_no | correction | repeat_request | chitchat_only | unclear",
    "pickup_span": "string | null",
    "destination_span": "string | null",
    "landmark_reference": "string | null",
    "confidence": {"pickup_span": 0.0-1.0, "destination_span": 0.0-1.0}
  }
  ```
- Ejemplo exacto del usuario: *"Buenas, si mira, estoy aquí en Valle del Hortigal, por favor"* → `{intent: "provide_pickup", pickup_span: "Valle del Hortigal", destination_span: null, confidence: {pickup_span: 0.9}}`. El resto de la oración (saludo, muletillas, cortesía) se descarta **por diseño del schema**, no porque el modelo "decida ignorarlo" de forma no determinista — el schema no tiene ningún campo donde ese texto podría ir.
- Frases puramente conversacionales ("Buenas.", "Cómo estás.", "Muchas gracias.", "Si Dios quiere.") → `{intent: "chitchat_only" | "greeting", pickup_span: null, ...}` — el Turn Orchestrator (§3.4) decide si responde brevemente o simplemente avanza sin generar una pregunta nueva.
- **Frontera estricta LLM↔resolver (Agente 3 §10, Agente 6 §7 — hallazgo con más consenso de fuentes de toda la investigación)**: el LLM **nunca** resuelve la dirección. Solo extrae el span de texto. La resolución (fuzzy matching, geocoding, confianza, ambigüedad) sigue siendo 100% responsabilidad de `core/geocoder_service.py`/`core/location_match.py` — bucket B, sin cambios. Esto significa que toda la ingeniería de precisión ya construida (guard `_NEVER_AUTOACCEPT`, corrección de barrio, cascada Google→Nominatim) se conserva íntegra; V2 solo cambia *qué texto* llega a esa cascada y *qué tan rápido*.
- **Referencias indirectas** ("frente al hospital", "donde siempre"): el schema captura esto en `landmark_reference`, y el LLM solo entrega el span — la resolución landmark→coordenada sigue siendo trabajo del geocoder (Agente 6 §7, papers citados sobre "coordinates from context" confirman que esta es la división de trabajo correcta en la literatura actual, no una limitación).

### 3.4 Turn Orchestrator (reemplaza el control-loop de `voice_call_engine.py`, preserva sus estados de negocio)

- Los **estados de negocio no cambian**: `waiting_origin`, `confirming_origin`, `waiting_geo_context`, `creating_service`, `finished` siguen existiendo exactamente igual, con las mismas transiciones de negocio. Lo que cambia es *cómo* se llega a cada transición: hoy es un f-string bloqueante esperando el turno completo; en V2 es un manejador de eventos disparado por el output del NLU streaming.
- **Generación anticipada (preemptive generation)**, del Agente 4 §1: cuando el STT streaming marca un partial como "estable" (no necesariamente el fin de turno confirmado), el NLU puede arrancar sobre ese partial. Si el turno termina distinto a lo esperado, se descarta y se re-ejecuta — mismo patrón que LiveKit documenta.
- **Ejecución especulativa de solo-lectura** (Agente 4 §4, patrón "speculative interaction agents"): apenas el NLU entrega un `pickup_span` con confianza razonable, se puede lanzar `geocoder_service.run_pipeline()` especulativamente en paralelo, sin esperar la confirmación final del turno completo — es una operación de solo lectura (no crea el servicio, no llama al backend), por lo que es segura para especular. Si el turno termina invalidando el span (el usuario se corrigió), se cancela y se descarta el resultado — nunca se usa un resultado especulativo para crear un servicio real (eso permanece estrictamente secuencial y confirmado, tocando bucket A).
- **Reparación natural de errores** (Agente 5 §1-2, rescatando `ConversationRepair.generate_repair`, bucket D): en vez de repetir literalmente "Disculpa, no te entendí, ¿me puedes repetir la dirección?" cada vez, usar plantillas con variación y — cuando la confianza es media (no baja, no alta) — **confirmación implícita** en vez de pregunta explícita: repetir el valor entendido dentro de la siguiente pregunta natural ("Vale, ¿Valle del Hortigal es donde te recogemos?") en vez de un loop de sí/no separado.
- **Detección de autocorrección** ("no espera, mejor en la carrera 5"): el Agente 5 confirma que esto es un área de investigación activa, sin solución única establecida — se documenta como *gap conocido*, no se inventa una solución no respaldada. Mitigación pragmática: el NLU re-procesa la oración completa (no solo el delta) en cada turno, dejando que el modelo reinterprete la oración entera incluyendo la corrección — esto es la opción "b" documentada en el Agente 5 §5 (LLM full-utterance rewriting), preferible a construir un clasificador dedicado sin evidencia de que sea necesario para este dominio.
- **Cambios de idea entre turnos** (usuario cambia de dirección tras confirmar): tratado como Dialogue State Tracking estándar (Agente 3 §7, Agente 6 §8) — el estado de sesión permite override explícito de un slot ya lleno cuando el NLU detecta `intent: correction` en un turno posterior, sin reiniciar la conversación completa.

### 3.5 TTS streaming (reemplaza `tts_service.py`)

- Requisito: síntesis por chunk de texto (frontera de oración, no palabra por palabra ni buffer completo), primer byte de audio objetivo <300ms desde el primer chunk de texto disponible del LLM — no desde la respuesta completa. Del Agente 2/4: Cartesia Sonic (WS, chunk/context-based, 40-90ms TTFB documentado) es el único motor de los investigados con esta característica confirmada por fuente propia y medición independiente (Coval).
- edge-tts (motor actual) **no tiene ningún mecanismo de streaming real documentado** ni en su propio código (confirmado en auditoría previa: `synthesize_stream` bufferea todo antes de trocear) — no es viable para V2 sin cambiar de motor. Esta es una decisión que requiere validación de calidad de voz en español colombiano antes de comprometerse (no investigada en esta sesión — flag explícito, no se asume).
- Normalización de texto antes de sintetizar (números, direcciones tipo "Cra 17 # 8C-55") — gap ya identificado en auditoría previa, se corrige en V2 independientemente del motor elegido.

### 3.6 Barge-in / full-duplex real

- Requiere **AEC del lado servidor**, no del lado cliente — el Agente 5 §4 es explícito en que esto es la diferencia crítica entre WebRTC de navegador (que trae AEC nativo) y telefonía PSTN/SIP (que no la trae de forma confiable): sin esto, la propia voz TTS del bot re-dispara el VAD de entrada y genera falsas interrupciones — el problema que el sistema actual "resuelve" descartando audio del usuario en vez de cancelando el eco (`ws_audio_buffer.py:gate_playback`, ya identificado como anti-patrón en §0).
- Clasificador de interrupción real vs. backchannel/ruido: patrón documentado por LiveKit (Agente 5 §4) — un modelo ligero (CNN sobre las primeras ~200-500ms de audio detectado) decide si es interrupción real antes de cancelar el TTS, evitando cortar el habla del bot por un "mm-hmm" del usuario.
- Al confirmarse interrupción real: cancelar síntesis en curso, truncar el historial de conversación a lo que el usuario efectivamente escuchó (no lo que el bot generó completo) — mismo patrón que OpenAI Realtime/`conversation.item.truncate` (Agente 2).

### 3.7 Latencia — técnicas aplicadas, mapeadas a la tabla de latencia del audit anterior

| Fuente de latencia en V1 (auditoría previa) | Técnica V2 aplicada | Fuente |
|---|---|---|
| `UTT_SIL_SECS=3` fijo | Endpointing híbrido acústico+semántico del STT streaming (~250ms) | Agente 2 (Deepgram Flux), Agente 4 §6 |
| Espera turno completo antes de STT | Hipótesis parciales continuas | Agente 2, todos los motores streaming |
| Espera respuesta LLM completa antes de TTS | TTS por chunk de oración | Agente 4 §2 |
| `wget` de subida/bajada de WAV sin timeout | Transporte streaming continuo, sin archivos | Agente 2, Agente 4 §5 |
| Geocoding síncrono hasta ~20s en el turno | Ejecución especulativa de solo-lectura en paralelo | Agente 4 §4 |
| Gate de reproducción descarta audio del usuario | AEC servidor + clasificador de interrupción real | Agente 5 §4 |
| Extracción LLM solo como fallback (`_extract_origin_llm`) | Extracción NLU en cada turno vía structured output, no solo fallback | Agente 3 |

---

## 4. Lo que NO cambia — checklist de verificación de la restricción del usuario

- [ ] `services/telephony/backend_client.py` — sin tocar.
- [ ] Creación de servicio, asignación de conductor — sin tocar (bucket A).
- [ ] Integración WhatsApp (`_send_whatsapp_message_async`) — sin tocar.
- [ ] Autenticación, esquema DB — sin tocar.
- [ ] `core/geocoder_service.py`, `core/location_match.py`, `core/address_utils.py`, `tools/popayan_geodata.py` — llamados igual, cero cambios internos.
- [ ] Estados de negocio del FSM (`waiting_origin`, `confirming_origin`, `waiting_geo_context`, `creating_service`, `finished`) — mismos nombres, mismas transiciones de negocio, mismas reglas.
- [ ] APIs existentes del backend IntelliTaxi — sin tocar.

Lo único que cambia es *el motor conversacional*: transporte, STT, extracción de lenguaje natural, orquestación de turno, TTS, manejo de interrupciones.

---

## 5. Ítems explícitamente sin validar en esta investigación (no asumir, no comprometerse sin verificar)

- Soporte de español colombiano en Deepgram Flux/AssemblyAI Universal-Streaming para el dominio específico de direcciones de Popayán — no investigado, requiere prueba piloto antes de elegir proveedor.
- Calidad de voz en español de Cartesia Sonic frente a edge-tts actual — no investigado.
- Costo real de los proveedores streaming (Deepgram/Cartesia) vs. el modelo actual (OpenAI batch + edge-tts gratuito) — fuera de alcance de esta investigación técnica.
- Compatibilidad exacta de `mod_audio_stream` con la versión de FreeSWITCH ya desplegada — el handler WS existe en el código pero nunca fue probado en producción real (bucket D, salvage, no verificado como funcional end-to-end).
- Ningún mecanismo de detección de autocorrección intra-turno tiene solución establecida en la literatura (Agente 5) — la mitigación propuesta en §3.4 es pragmática, no una técnica citada como "la forma correcta."
