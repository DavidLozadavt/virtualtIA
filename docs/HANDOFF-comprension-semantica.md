# Handoff — Comprensión semántica de Lyra/Nexo para NexiService

**Fecha:** 2026-08-13 · **Estado:** funcionando, sin commit · **Rama:** `main`

Este documento explica qué se cambió, por qué, y qué queda pendiente. Está
escrito para quien retome el trabajo sin haber estado en la sesión.

---

## 1. El problema

Lyra respondía a coincidencias de palabras, no a lo que la persona quería decir.
Los logs mostraban frases conversacionales convertidas en consultas SQL:

```
"necesito que te mueras"   → intent='navigate_to_company' → LIKE '%que%te%mueras%'
"que me puedes ofrecer"    → intent='navigate_to_company' → LIKE '%que%me%puedes%ofrecer%'
"alguna medicina"          → intent='navigate_to_company' → LIKE '%alguna%medicina%'
```

Y la respuesta al usuario era `No encontré 'que me puedes ofrecer'.`

El encargo no era arreglar esas tres frases, sino **la causa que permite que eso
ocurra**, y que Lyra pasara de ser un buscador con saludo incorporado a un
asistente que entiende, conversa y acompaña.

---

## 2. Las causas raíz

| # | Causa | Dónde estaba |
|---|-------|--------------|
| 1 | **Carga de la prueba invertida.** Cuatro *catch-all* asumían que cualquier texto no reconocido era el nombre de una empresa. Cualquier frase de 4 caracteres acababa en `navigate_to_company`. | `intent_router.detect_intent` |
| 2 | **Nada sabía qué contiene NexiService.** Las categorías estaban escritas a mano (7 rubros) mientras la base tiene 9 categorías de empresa, 103 empresas, 69 servicios y 23 categorías de servicio. Sin eso no se puede distinguir *"no existe"* de *"no entendí"*. | `tools/shared/utils.CATEGORY_KEYWORDS` |
| 3 | **El contexto se reconstruía raspando markdown** del propio asistente con expresiones regulares (negritas, `[BIZ:id]`). Frágil y sin posiciones: por eso *"el segundo"* no funcionaba. | `tool_runner`, `interceptors/helpers` |
| 4 | **La herramienta de búsqueda aceptaba cualquier cadena** y la convertía en `LIKE`. | `tools/nexiservice.search_businesses` |
| 5 | **Reglas de palabras clave sin arbitraje.** `"tienen"` disparaba el catálogo de servicios lo mismo en *"¿qué servicios tiene X?"* que en *"¿qué negocios tienen?"*. | todo `intent_router` |

---

## 3. Lo que se construyó

### Capa nueva: `core/semantic/` (3.062 líneas, 9 módulos)

Tres etapas encadenadas. La regla que gobierna todo:

> Una frase se convierte en búsqueda **sólo si** su estructura expresa una
> búsqueda **y** su contenido se ancla a algo que existe. Si falta cualquiera de
> las dos, el sistema conversa o pregunta, pero no consulta la base con el texto
> crudo.

| Módulo | Líneas | Qué hace |
|--------|-------:|----------|
| `speech_act.py` | 523 | **Etapa A.** Decide la *función* del mensaje usando sólo clases cerradas del español: pronombres, interrogativos, modalidad, deixis, ordinales, morfología de persona. Más marcos universales (cita, agente humano, tiempo, oferta, valoración, identidad, web, recomendación, comparación, mapa). **No conoce ni una palabra del catálogo.** |
| `catalog.py` | 433 | **Etapa B.** Índice invertido raíz→concepto construido **leyendo la base de datos**. Pesos IDF. Reconoce cualquier rubro que se registre en el futuro sin tocar código. |
| `llm_resolver.py` | 191 | **Etapa C.** Sólo si A dice "necesidad" y B falla. El modelo elige entre etiquetas **reales**; `NINGUNO` es respuesta válida. **Desactivado por defecto.** |
| `lexicon.py` | 457 | Inventario de clases cerradas. *Si una palabra nombra algo que se puede vender, reservar o visitar, no va aquí.* |
| `morphology.py` | 269 | Robustez ante erratas, falta de tildes y ruido de STT. |
| `reference.py` | 140 | Resuelve *"el segundo"*, *"ese"*, *"con ella"*, *"ahí"* contra lo mostrado. |
| `types.py` | 336 | `Act`, `Understanding`, `Grounding`, `ConversationState`. |
| `engine.py` | 663 | Orquesta A→B→C y decide intent + args. |

### Estado conversacional explícito

`ConversationState` se serializa dentro de `final_data['_semantic_state']`, que
el orquestador **ya persistía** por conversación. No hay almacenamiento nuevo.
Guarda lo mostrado *con su posición*, el foco y las ranuras de reserva. El
raspado por regex queda como respaldo para conversaciones antiguas.

---

## 4. Las cinco rondas

### Ronda 1 — La comprensión

Se eliminaron los cuatro *catch-all*. Se añadieron `get_business_professionals()`
y `find_businesses_offering()` (entra por la tabla `servicios`; sin ella
*"alguna medicina"* daba cero resultados). `search_businesses` rechaza consultas
que parecen frases salvo que vengan ancladas (`grounded=True`).

### Ronda 2 — Asistente, no buscador

- Cuando la comprensión **no** halla intención de catálogo, el destino por
  defecto es **conversar**, no pedir aclaración. Antes *"no entiendo"* o
  *"tengo una duda"* recibían *«no manejo nada relacionado con…»*.
- Saludo y reseña se separan por **persona verbal**: *"¿cómo estás?"* (2ª) es
  saludo, *"¿cómo está?"* (3ª) es reputación. Antes `"como esta"` se buscaba
  como subcadena y aparecía dentro de `"como estas"`.
- **Bug de producción:** `LLMEngine.generate()` devolvía la cadena
  `"Error de conexión."` *como si fuera texto del asistente* — se imprimía en el
  chat, se enviaba a TTS para leerla en voz alta, y debajo colgaban fichas de una
  búsqueda anterior. Ahora lanza `LLMUnavailable(reason, status_code)`.

### Ronda 3 — Lenguaje natural largo

Caso real que fallaba:

> *"me siento muy mal de la cabeza, quisiera hacer una reserva para un hospital o
> algo con una profesional que me pueda atender"* → *"Encontré 6 opciones de medico"*

Cuatro reglas antiguas cortaban antes de la comprensión y hubo que condicionarlas
al acto de habla: `CATEGORY_KEYWORDS` (exacto **y** fuzzy), `_APPOINTMENT_COMPOSITE`
y los `[CONTEXT-AWARE] FAST PATHS`.

También: el catálogo incorporó `CATEGORY_KEYWORDS` como conceptos con
`aliases_are_names=True`. *"hospital"*, *"moto"* y *"gimnasio"* no están en la
base pero son como habla la gente — es conocimiento que el proyecto **ya tenía**,
ahora disponible también para la comprensión.

### Ronda 4 — Modo local

Petición explícita: *"no quiero nada de openrouter, quiero que todo salga de aquí
de Lyra localmente"*.

Bandera `LLM_EXTERNAL_ENABLED` (**por defecto `false`**). Con ella apagada no se
hace **ninguna** llamada de red para conversar, buscar ni agendar.

En vez de seguir tapando secuestros uno a uno (llevaba cinco), **la compuerta
semántica pasó a ser la decisión principal**, antes de todas las reglas de
palabras clave. Devuelve si `disposition != CLARIFY`; si es `CLARIFY`, cede a las
reglas antiguas. Para no perder capacidades se añadieron al motor los marcos que
sólo cubrían esas reglas.

### Ronda 5 — Reserva con cuenta + pulido

**Bug grave:** el usuario pidió las **8:30** y se agendó a las **07:00**.

1. `_extract_time` no parseaba `"8;30"` (la normalización borra la puntuación).
2. Peor: al no tener hora, la recuperaba raspando el historial **incluida la
   lista de horarios OCUPADOS**, tomando el primer hueco ocupado como la hora
   elegida. Es decir, **agendaba encima de una cita existente**.

**Reserva exige cuenta.** `confirm_appointment` ya no crea reservas anónimas.
Devuelve `needs_auth=True` + `pending_reservation` con todo lo acordado. El nombre
sale de la cuenta, no de un texto escrito en el chat.

---

## 5. El flujo de reserva hoy

```
U: me siento muy mal de la cabeza, quisiera una reserva para un hospital
   o algo con una profesional que me pueda atender
N: Para agendar tu cita encontré 6 opciones de medico en Popayán:
   1. Consultorio Médico Vida Sana Popayán  … ¿En cuál quieres que te agende?

U: Quiero agendarla en Consultorio Médico Vida Sana Popayán
N: Con gusto te ayudo a agendar tu cita en Consultorio Médico Vida Sana
   Popayán. Ya tienen ocupado hoy a las 07:00, de 07:00 a 07:30 y a las
   10:00. ¿A qué hora te viene bien?

U: Quiero agendar una consulta de medcina        ← con errata
N: Con gusto te ayudo a agendar tu Consulta de Medicina General. …

U: Quiero agendarla para las 8;30 am
N: Tengo todo listo para dejarla agendada. Sólo falta que entres a tu cuenta
   —o que crees una si aún no la tienes— para poder confirmarla a tu nombre.
   → needs_auth = true
   → pending_reservation = {business_id, service_name, professional_name,
                            time: "08:30", date: "tomorrow"}

U: (inicia sesión)
N: ¡Listo, Sofía Restrepo! Tu Consulta de Medicina General queda para mañana
   a las 08:30.
```

---

## 6. Archivos

### Nuevos

```
core/semantic/           9 módulos, 3.062 líneas
tests/test_semantic_understanding.py    752   comprensión aislada
tests/test_semantic_routing.py          228   router + herramientas integrados
tests/test_assistant_resilience.py      273   fallos, modo local, órdenes de pantalla
tests/test_reservation_auth.py          180   reserva con cuenta + renderizado
```

### Modificados (+1.359 / −222)

| Archivo | Qué cambió |
|---------|------------|
| `orchestrator/intent_router.py` | Compuerta semántica como decisión principal; catch-all eliminados; guardas por acto de habla; `_extract_time` reescrito |
| `orchestrator/interceptors/nexiservice.py` | Estado conversacional; `WAITING_AUTH`; órdenes de pantalla; manejadores nuevos |
| `tools/nexiservice.py` | `get_business_professionals`, `find_businesses_offering`, guarda anti-frase, mensajes en lenguaje natural |
| `core/llm_engine.py` | `LLMUnavailable`; los errores dejan de ser "texto del asistente" |
| `orchestrator/tool_runner.py` | Modo local; manejo de fallos sin filtrar detalles |
| `core/config.py`, `.env.example` | `LLM_EXTERNAL_ENABLED` |
| `api/schemas/chat.py` | `needs_auth`, `pending_reservation` |
| `orchestrator/memory_manager.py` | Persistir el contexto no puede tumbar la respuesta |
| `core/logger.py` | UTF-8 en stdout (la consola de Windows reventaba con `→` y emojis) |
| `orchestrator/context_builder.py` | La conversación conserva historial |
| `services/chat_service.py` | Estado semántico entre turnos; no adjunta resultados a un error |
| `projects/nexiservice.yaml` | Instrucciones de asistente general |

---

## 7. Cómo validar

```bash
python -m pytest tests/ -q          # 419 pasan
python -m tests.test_booking_flow   # 5662 OK, 0 fallos
```

> **Los 3 `ERROR` de `test_booking_flow.py` bajo pytest son PREEXISTENTES.** Ese
> archivo se ejecuta con `python -m tests.test_booking_flow`; sus funciones toman
> parámetros posicionales que pytest interpreta como *fixtures*.

---

## 8. Pendiente

| # | Qué | Quién |
|---|-----|-------|
| 1 | **Manejar `voice_action: "require_auth"`** — el backend ya emite la señal con `pending_reservation`; falta que el frontend abra el login/registro. Sin esto la reserva se queda esperando. | Frontend |
| 2 | **Commit.** Todo el trabajo está sin commitear. | — |
| 3 | **Registry de herramientas vacío.** `tools/nexiservice.py` no declara `SCHEMAS`, así que `ToolRegistry.for_project('nexiservice')` devuelve 0 herramientas y el modelo nunca recibe *tool-calling*. Hoy no hace daño (los interceptores ejecutan todo), pero las herramientas del YAML están muertas. Se probó caer a ellas y **se revirtió**: están declaradas sin parámetros y sin registrar. | Backend |
| 4 | **Etapa C sin verificar en vivo.** El saldo de OpenRouter está agotado (`Prompt tokens limit exceeded: 1068 > 170`). Probada con doble determinista. Irrelevante mientras el modo local esté activo. | — |
| 5 | **Redacción libre sin terceros.** Si se quiere que las respuestas se redacten al vuelo sin depender de una API, el camino es un modelo local (el `MODEL_PATH` con GGUF que el proyecto ya contempla). | Decisión |

---

## 9. Para quien mantenga esto

### Decisiones que no conviene deshacer

- **`phonetic_stem` = `stem(phonetic(w))`, no al revés.** Las reglas fonéticas
  del español dependen de la letra siguiente (*"ofrecer"* → /ofreser/); cortar
  sufijos primero destruye el contexto.
- **`stem()` ortográfico es la raíz primaria**; el plegado fonético es el segundo
  intento. Si se pliega antes de cortar, *"médico"* y *"medicina"* divergen.
- **Días y meses se comparan como formas exactas** (`CALENDAR_WORDS`), no por
  raíz: *"sábado"*→`sab` choca con *"sabes"*→`sab`.
- **`_assign_roles()`: cada palabra recibe UN papel.** Sin eso *"servicios"*
  activaba también el verbo *"servir"*, y *"atender"* chocaba con *"tener"*.
- **`_strip_clitics` exige que la base pueda alojar el clítico.** Antes
  *"conose"* → `kono` → `kon`, que es la preposición *"con"*, y activaba lecturas
  existenciales falsas.
- **Las órdenes de pantalla van ANTES de la comprensión.** Son botones dichos en
  voz alta, no actos de habla. Con la compuerta delante, el marco `map` se las
  come; y *"acercar"* colisiona fonéticamente con *"hacer"*.

### Probado y revertido (no repetir)

- **Exigir que ninguna palabra quede sin reconocer** (`unmatched + ambiguous` →
  aclaración). Tumbaba sintagmas perfectamente claros como *"atención médica"*
  sin arreglar bien *"revisar los ojos"*. Ese salto de sentido es trabajo de la
  etapa C, no de más heurística léxica.
- **Caer a las herramientas del YAML** cuando el registry está vacío. Están
  declaradas sin parámetros y sin registrar: ofrecérselas al modelo sólo produce
  llamadas que fallan.

### El patrón que se repitió cinco veces

Una regla de palabras clave veía *su* palabra y decidía por el mensaje entero.
Se tapó cinco veces antes de reordenar. **Si aparece un sexto caso, la respuesta
no es otra guarda: es comprobar por qué la comprensión no llegó a una lectura
clara y arreglar eso.**

---

## 10. Bugs introducidos durante la sesión y corregidos

Se dejan anotados porque son fáciles de reintroducir:

- Un `Decimal` de MySQL guardado en `PresentedItem.extra` → `TypeError: Object of
  type Decimal is not JSON serializable` → **500 en la petición entera**.
- `\n\n` **literal** (escapado) en tres mensajes: el usuario lo veía impreso. Hay
  una prueba que falla si vuelve a colarse.
- Datos fuera del marcador (`Hora solicitada: 08:30`) quedaban visibles, porque
  el limpiador sólo retira etiquetas en mayúsculas. Ahora van dentro de
  `[SERVICIO:…]` y `[HORA:…]`.
