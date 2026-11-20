# Lyra — auditoría y reconstrucción conversacional (NexiService y SchoolSena)

**Fecha:** 2026-11-16 · **Alcance:** conexiones `nexiservice` y `schoolsena`. Los cambios compartidos están
limitados a esos dos proyectos y la comprensión de IntelliTaxi, RentUs y wilson da la misma salida (ver §4).
**Estado:** sin commit.

## 1. Cómo funciona (flujo real)

```
Front (5173 / 4210)
  ├─ voz: Web Speech API del navegador (useLyraVoice.ts) → texto
  └─ POST /chat {message, nav_catalog, current_path, JWT}
       ChatService
         guard (inyección / cambio de rol)        → respuesta fija
         limpieza del habla (NUEVO)               → petición final
         detect_intent (core/semantic, router)    → intención + args
         run_agent_loop
           interceptor del proyecto               → resuelve sin modelo
           compuerta de escalamiento (NUEVO)      → local o GPT, con motivo
           GPT (solo si LLM_EXTERNAL_ENABLED=true)
  TTS: /voice/synthesize_stream (edge-tts)
```

GPT entra por dos puntos: el bucle del agente (`tool_runner.run_agent_loop`) y la etapa C semántica de
NexiService (`core/semantic/llm_resolver`). Los dos están detrás de `LLM_EXTERNAL_ENABLED` (hoy `false` en local).
`services/voice/*`, `stt_enhancer.py` y `VOICE_*` son de telefonía y WhatsApp: los fronts no los usan.

## 2. Diagnóstico

| # | Problema | Causa |
|---|----------|-------|
| 1 | SchoolSena respondía mal sin enterarse. | Cascada de palabras clave en orden fijo, donde gana la primera subcadena: "cuánto cuesta" iba a nómina, cualquier "hoy" (incluido "el clima de hoy") a clases de hoy, "general" al dashboard, "¿qué fichas hay activas?" al horario y "sedes" o "regionales" a un texto fijo de SofiaPlus. |
| 2 | SchoolSena no tenía contexto: "¿y mañana?" o "¿y de la 2601?" se perdían. | El único estado era un regex para "y mañana". |
| 3 | "Mañana" mostraba la semana completa. | `_run` solo filtraba el día cuando era "hoy". |
| 4 | Fuga entre proyectos: lo no resuelto en School respondía "¿Buscas un negocio...?". | `_local_default_reply` era común a todos los proyectos. |
| 5 | Fuga entre usuarios y proyectos. | `get_or_create_conversation` buscaba solo por id: un `conversation_id` ajeno entregaba el historial y el contexto de otra persona. |
| 6 | School arrancaba con la personalidad "nexo", que no existe en su YAML. | El valor por defecto estaba fijo en `chat_service`. |
| 7 | El arranque de la frase hablada no se limpiaba: "el segundo por fa" no resolvía la referencia. | No había limpieza de muletillas, cortesías, rectificaciones ni repeticiones. |
| 8 | La voz se cortaba en la primera pausa. | `continuous=false` e `interimResults=false`: Chrome cierra en el primer silencio. |
| 9 | La voz de School reescribía "escuela" como "SchoolSena". | Un mapa fonético demasiado amplio. |
| 10 | NexiService: "qué servicios ofrece Fogón..." abría el perfil; "¿cómo lo contrato?" lanzaba una búsqueda; "cómo funciona el primero" respondía con las capacidades del asistente; "explícame el primero" (sobre servicios) agendaba. | Faltaban marcos de oferta y reseñas sobre un negocio nombrado; "contratar" no era verbo de cita; un ordinal no cancelaba la lectura "pregunta al asistente"; una selección de servicio con marco informativo iba a reserva. |
| 11 | No había forma de medir. | `lyra_llm_usage.path='local'` contaba como "local" también las respuestas equivocadas y el "no te entendí". |
| 12 | Con `tool_registry=None`, School se caía. | `tool_runner.py:799` (en producción no se dispara: `main.py` siempre arma el registro). |

## 3. Decisiones

- **No se agregó ningún modelo local ni embeddings.** La base ya tiene morfología tolerante a erratas y a fonética
  (`core/semantic/morphology`) y un motor por actos de habla y anclaje al catálogo. Bastó con reutilizarlos. Además
  Python 3.14 en Windows complica los wheels de PyTorch/ONNX. Si el held-out se estanca, ese es el siguiente paso.
- **School:** se cambió la cascada por `orchestrator/interceptors/schoolsena_nlu.py`. Cada capacidad declara sus conceptos
  con peso; el mensaje se compara por raíz y con tolerancia a erratas. Lo temporal es una ranura, no una intención.
  Unas pocas relaciones estructurales resuelven los choques reales (ficha + momento = horario; dinero + instructor = nómina).
  El estado `_school` guarda la última consulta, sus ranuras y lo mostrado para los seguimientos, y caduca a los 30 min.
  Lo ambiguo se pregunta y lo ajeno se rechaza, siempre en local.
- **NexiService:** se conserva el motor semántico y se corrigen las cinco causas del punto 10, sin reescribirlo.
- **Limpieza del habla** (`core/semantic/utterance.py`), solo para estos dos proyectos: arranques ("mira, es que, lo que
  pasa es que..."), muletillas, "por fa", abreviaturas ("q", "x", "xq"), repeticiones y rectificaciones. Si la rectificación
  ocurre dentro del mismo mensaje, se queda la última versión. Si apunta al turno anterior ("no, mejor..."), se conserva
  el "no" y se quita el "mejor", que es un marcador de discurso y no un superlativo. El mensaje se guarda tal cual: solo
  cambia lo que interpreta la comprensión.
- **Compuerta de escalamiento** (`orchestrator/escalation.py`): no escala ruido (teclazos, una letra) ni mensajes de
  menos de dos palabras de contenido; escala con el motivo que deja el interceptor o `unresolved:<intención>`.
  La etapa C anota `semantic_c`. El motivo va a `lyra_llm_usage.gate_reason`; `+motivo` significa que habría escalado,
  pero el modelo está apagado. `path` sigue siendo `llm`/`local`.
- **Dueño de la conversación (NexiService y School):** los fronts no adoptan el `conversation_id` que devuelve el servidor
  y cambian de `user_id` al iniciar sesión (`user_client_demo` → id de la persona).
  - Un hilo anónimo del mismo proyecto pasa al usuario que inicia sesión, así no se rompe la reserva que pidió login.
  - Un hilo de otro usuario o proyecto se reemplaza por un id derivado (uuid5), que es estable entre turnos.
  - En los demás proyectos el riesgo de leer un hilo ajeno sigue como estaba.
- **Voz:** se mantiene el motor (Web Speech API, gratis y local en el navegador). Se cambió la segmentación: escucha
  continua con resultados parciales y fin de turno por silencio (1,3 s; 2,6 s si la frase termina en "y", "que",
  "este"...), con un tope de 30 s. En Android se mantiene el modo por frase porque el continuo duplica resultados.
  La interpretación del habla espontánea la hace el backend con la limpieza descrita arriba.

## 4. Resultados

Evaluación offline (`scripts/eval_nlu.py`): sigue el mismo camino que `/chat` hasta la puerta de GPT, con el menú
real de School (`chat_battery.SS_NAV`), sin OpenAI y sin escribir en la BD. Con `--stage-c` cuenta también los turnos
que llamarían a la etapa C semántica de NexiService (ocurre dentro de `detect_intent`, antes de la compuerta). Un
contador responde NINGUNO, así que no sale nada a la API. El "antes" se midió con el mismo arnés sobre una copia de
`HEAD` (`git archive`).

| Conjunto | Métrica | Antes | Después |
|---|---|---:|---:|
| School held-out (90 turnos, reales) | acierto | 78,9 % | **97,8 %** |
| | respuesta local equivocada | 11,1 % | 2,2 % |
| | iría a GPT | 10,0 % | **0 %** |
| NexiService held-out 1 (63, reales) | acierto | 82,5 % | 87,3 % |
| | iría a GPT (etapa C) | 14,3 % | 11,1 % |
| NexiService held-out 2 (52, reales, **nunca vistos**) | acierto | 71,2 % | **71,2 %** |
| | iría a GPT (etapa C) | 17,3 % | 15,4 % |
| School dev (54) | acierto | 51,9 % | 98,1 % (conjunto de construcción) |
| NexiService dev (22) | acierto | 72,7 % | 95,5 % (conjunto de construcción) |

**Cómo leer estas cifras:**
- **School:** la mejora es grande y estructural. Pero el held-out no es del todo independiente: algunas reglas y pruebas
  usan frases parecidas, y ese held-out quedó como prueba de `pytest`. No existe otro conjunto real de School sin usar
  (solo hay 108 mensajes distintos en `lyra_db`). Una etiqueta se cambió al usar el menú real: "llévame a mis clases"
  ahora acepta también mostrar el horario, porque esa sección no existe en el menú real.
- **NexiService held-out 1:** la ganancia son 3 turnos (n030, c001#1, c005#0), que ya fallaban en la línea base y tienen
  casos parecidos en dev. No es una medida independiente.
- **NexiService held-out 2** es la medida honesta: se etiquetó antes de correrlo y no cambia. Las mejoras de NexiService
  cubren referencias, rectificaciones y cortesías ("el segundo por fa", "¿cómo lo contrato?", "explícame el primero"),
  que casi no aparecen en esa muestra. 5 de sus 13 errores son saludos con nombre ("hola lyra", "hola nexo") que se
  responden con un saludo pero quedan clasificados como `conversation`; la etiqueta esperada era `greeting`.
- **Uso de GPT:** con el modelo encendido, School no escalaría en ninguno de los conjuntos. NexiService escalaría entre
  11 % y 15 % de los turnos, siempre por la etapa C ("dónde llevar a mi perro", "nómina", "hola bro"...). La compuerta
  del agente no escala nada en estos conjuntos.
- **Tokens:** 0 llamadas y 0 tokens durante las pruebas. No se corrió ninguna prueba con el modelo encendido.
- **Latencia local:** School p50 de 5 → 13 ms (p95 22 ms). NexiService sin cambio (p50 40–55 ms).
- **Camino real:** una conversación de School por `ChatService` con un token con claims, sin firma válida (la API responde
  401), conservó el contexto (hoy → "¿y mañana?" → "no, mejor las actividades"). Las claves de un solo turno no pasan
  al siguiente. Otra prueba de punta a punta corrió en una instancia temporal en el 8097, ya apagada; la del 8098 no se tocó.
- **Pruebas:** `pytest` 978 pasan (35 nuevas). Los 30 fallos son los mismos de antes, todos de WhatsApp/IntelliTaxi.
- **Otras conexiones:** las 293 frases reales de IntelliTaxi, RentUs y wilson pasadas por `detect_intent` dan una salida
  idéntica antes y después. Además, los cambios compartidos de `ChatService` están limitados a NexiService y School:
  limpieza del habla, dueño de la conversación y personalidad por defecto. La compuerta devuelve `legacy` para los demás.
  Esto último no lo cubre la foto.

## 5. Archivos

Lyra: `core/semantic/utterance.py` (nuevo), `orchestrator/interceptors/schoolsena_nlu.py` (nuevo),
`orchestrator/escalation.py` (nuevo), `orchestrator/interceptors/schoolsena.py`, `services/chat_service.py`,
`orchestrator/tool_runner.py`, `core/llm_usage.py`, `core/semantic/{engine,speech_act,lexicon}.py`,
`orchestrator/memory_manager.py` (claves de turno), `orchestrator/navigation.py` (`coverage`),
`core/semantic/llm_resolver.py` (sin etapa C para ruido; motivo `semantic_c`), `migrations/006_llm_usage_gate_reason.sql`
(columna opcional `gate_reason`; **corrida en local, falta en producción**; el código funciona sin ella),
`scripts/eval_nlu.py` + `eval/*.yaml` (nuevos), `tests/test_schoolsena_nlu.py` y `tests/test_utterance_y_escalamiento.py` (nuevos).
Fronts: `src/hooks/useLyraVoice.ts` en `postandserviceFront` y `schoolSenaFrontFinal` (`tsc` sin errores; `eslint` da
los mismos 2 errores que ya tenía el original: `React` no definido en el tipo).

## 6. Limitaciones y pendientes

- **Fallos del held-out que no se corrigieron, para no ajustar las reglas a los datos con que se mide:**
  - School:
    - "dime el clima de hoy" → clases de hoy: un "hoy" suelto se toma como seguimiento.
    - "como instructor qué puedo hacer en lyra" → permiso denegado de la lista de instructores.
  - NexiService:
    - "hola mi vida" → busca "Vida Sana".
    - "asd" → saludo.
    - "necesito ayuda estoy configurando mi empresa" → búsqueda.
    - "que servicios ofrece nexiservice" y "negocios" → saludo genérico.
    - "busco un lugar para cortarme el pelo" → abre un solo negocio.
    - "quiero ver multiservicios motor oeste popayán" → busca el rubro en vez de abrir el negocio.
    - c002: mal etiquetado mío. En local no hay barberías en Popayán, así que "el primero" no tiene antecedente.
- **Fichas:** la API `lyra/fichas/{id}/estudiantes` recibe el id de la base y devuelve solo el conteo. El usuario suele
  escribir el código ("ficha 2558"), que Lyra reenvía tal cual: ya pasaba antes y necesita un endpoint por código en
  School. Las referencias "la primera" sí usan el id real cuando el horario lo trae.
- "dónde gestiono los usuarios" (dev) no navega: "gestiono" no coincide con "Gestión".
- La etapa C tiene caché en memoria: el conteo de `--stage-c` no cuenta claves repetidas dentro de una misma corrida
  (lo mismo pasa en producción).
- Las peticiones creativas ("escríbeme un poema") en NexiService terminan en "no tengo X registrados".
- **Voz:** no hay corpus de audio, así que no se midió WER ni se comparó con otros motores. La mejora de segmentación
  se razonó y se comprobó con `tsc`, no con usuarios. Queda la prueba manual (abajo). Criterio 6: **parcial**.
- Los conjuntos de evaluación los etiqueté yo. El held-out es independiente del desarrollo, pero no de mi criterio.
- `pytest` escribe filas en `lyra_db` (ya pasaba antes).
- School solo escala a GPT con frases de 3 o más palabras de contenido que no se reconocen como nada. Con el modelo
  encendido esto es raro; el modelo no tiene más datos que las tools.
- No se probó con el modelo encendido: no se sabe si en producción `LLM_EXTERNAL_ENABLED` está en `true`.

### Prueba manual de voz (Chrome de escritorio)
1. Decir "hola, mira... es que quería saber mi horario" con una pausa de 1 s después de "mira": debe llegar la frase completa.
2. Decir "cuánto vale... bueno no, espera, mejor dime mis clases de mañana": Lyra responde con las clases de mañana.
3. Decir "quiero ver restaurantes en Popayán y..." y callar 2 s: no debe enviar todavía; enviará a los 2,6 s.
4. Pulsar el micrófono de nuevo mientras habla: envía lo dicho hasta ese momento.
5. En Android: comprobar que no se duplica el texto.

### Medir
```
python scripts/eval_nlu.py eval/schoolsena_heldout.yaml eval/nexiservice_heldout.yaml -v
SELECT path, COUNT(*), SUM(prompt_tokens+completion_tokens), SUM(cost_usd) FROM lyra_llm_usage
 WHERE project_slug IN ('nexiservice','schoolsena') GROUP BY path;
```
