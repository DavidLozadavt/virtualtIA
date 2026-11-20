# Lyra, fase 2: fallos reales, aislamiento, recomendaciones, voz y mapa

**Fecha:** 2026-11-17 · **Alcance:** NexiService y SchoolSena · **Estado:** sin commit (Lyra, `postandserviceFront` y
`schoolSenaFrontFinal`). Continúa la fase 1 (`AUDITORIA-LYRA-2026-11-16.md`).

## 0. Lo primero que se encontró

**Las pruebas manuales corrieron sobre el código anterior.**
- El proceso de Lyra en el 8098 (PID 10856) arrancó a las 22:05 y los cambios de la fase 1 son de las 23:39. No se recarga solo.
- "¿Buscas un negocio, quieres ver servicios o prefieres agendar una cita?" dentro de School es literalmente el fallback
  viejo de `tool_runner`.
- Se reprodujeron todas las conversaciones reales del 2026-11-16 (23:48–23:53) por `ChatService`, con el código ya
  actualizado (`scripts/replay_turns.py`). Resultado:

| Caso real | Fase 1 | Fase 2 |
|---|---|---|
| School: "¿Cómo me fue en el examen de ayer?" → fallback de NexiService | corregido (va a notas) | además filtra por fecha y palabra |
| School: "Contratos próximos a vencer" → 24 viñetas vacías | **seguía** | corregido |
| School: voz "guion bajo", "COP peso" | **seguía** | corregido |
| Nexi: restaurantes + "me gusta el pescado" → recomienda por nota | **seguía** | corregido |
| Nexi: dolor de cabeza → "Listo… Seguimos con Fogón Criollo" | **seguía** | corregido |
| Nexi: "hospitales para medicina general" → una óptica primero | **seguía** | corregido |
| Nexi: saludo "Sistema Nexo iniciado… terminal" | **seguía** | corregido |
| Nexi: mapa recorre 4 negocios en ~2 s mientras la voz dice el primero | **seguía** | corregido |

El 8098 se reinició con el código final al terminar esta fase.

## 1. Aislamiento entre proyectos (prioridad 1)

**Causas encontradas (además del fallback compartido de la fase 1):**
- **Saludo de NexiService al arrancar:** `main.py` emite el saludo de NexiService por el canal de Pusher compartido
  `lyra-channel` y el front de School, suscrito, **reemplaza su saludo por el de NexiService**. Local y producción comparten
  la app de Pusher (`ERP/CLAUDE.md`).
- **Interceptores en cadena:** el manager pasaba cada petición por los interceptores de todos los proyectos, y cada uno
  tenía que acordarse de comprobar el `project_id`.
- **Router de NexiService en turnos de School:** se pasaba un turno de School por `detect_intent`, que es el
  router/semántica de NexiService.
- **Defaults de NexiService:** `/voice/config` respondía con un saludo de NexiService; `/voice/synthesize_stream` emitía
  `stop_audio` por el canal compartido en cada frase (nadie lo escucha y costaba una conexión TLS).

**Mecanismo:**
- **Despacho por registro** (`interceptors/manager.py`): cada proyecto pasa sólo por su interceptor y su
  post-ejecución.
- **Marca de origen:** cada módulo de interceptor declara su proyecto (`PROJECT`) y marca con esa constante lo que
  produce, no con el proyecto de la petición. `orchestrator/project_scope.enforce` la contrasta en `ChatService`; si no
  coincide, descarta la respuesta (y sus fichas), responde con una del proyecto correcto y cuenta el evento.
  - La marca es por turno (`TURN_OUTPUT_KEYS`), para que no se herede del turno anterior.
  - Hay además otra capa: cada interceptor comprueba su `project_id`.
- **School no pasa por la comprensión de NexiService:** ni `detect_intent` ni `vt_inventario`.
- **Pusher:** el evento lleva `project_id` y cada front ignora lo ajeno. Se quitó `stop_audio`, y el saludo de
  respaldo no nombra ningún producto.
- **Roles:** los permisos de coordinación copian `KeyUtil::hasRole` del backend. "AUXILIAR COORDINACION" no es
  coordinación en el backend y Lyra se lo ofrecía.

**Prueba** (`tests/test_aislamiento_proyectos.py`):
- 33 turnos de los dos proyectos corridos **concurrentemente** (`asyncio.gather`) por `ChatService`, mezclando
  vocabulario del otro proyecto ("quiero agendar una cita" en School, "mis notas" en Nexi) y mensajes sin sentido.
- Resultado: origen correcto en el 100 % de los turnos, ninguna respuesta con vocabulario exclusivo del otro proyecto
  y 0 eventos de contaminación.
- **Trip-wire probado:** un interceptor mal registrado a propósito (responde a todo como NexiService) se detecta y la
  respuesta no llega al usuario (evento contado = 1, sin fichas).

## 2. School: lo que existe de verdad

- **Capacidades reales** (`LyraController`): las notas traen título de la actividad, materia, nota numérica o
  estándar y **fecha de calificación**.
  - No hay "exámenes": la tabla tiene `tipoActividad` ('cuestionario'), pero el endpoint no lo devuelve.
- **"¿Cómo me fue en el examen de ayer?":** filtra las notas por la fecha de calificación de ayer y lo dice
  ("el sistema no marca qué actividades son exámenes"). Si no hay notas de ese día, lo dice y da la fecha de la
  última nota registrada.
- **Fechas pasadas:** "ayer", "anteayer" y "el lunes pasado", tolerando errores como "ayyer".
- **Presentadores por herramienta** (`schoolsena_present.py`), escritos contra los payloads reales:
  - notas: muestran la nota, que antes no salía;
  - contratos: persona y fecha; antes salían 24 viñetas vacías y la cuenta bancaria nunca se muestra;
  - nómina: sin `COP` ni timestamp;
  - resumen, estudiantes, entregas y pendientes, con su fecha límite.
- **Arnés de evaluación:** usa los payloads reales y marca defectos de presentación (viñeta vacía, clave interna,
  timestamp, `COP`, cuenta, y nota ausente en una consulta de notas). Ese chequeo encontró además las entregas vacías.
- **Filtro por palabra sin coincidencias:** "¿cuánto saqué en química?" lo dice y muestra todas las notas; antes
  respondía "sin nota". "materias" o "clases" no se usan como filtro.
- **Síntoma con consulta:** "estoy enfermo, ¿tengo clase hoy?" responde el horario con una línea de cuidado delante.
- **Sin camino real contra el backend:**
  - El 8005 escucha pero no respondió en 3 s y no hay JWT de prueba (`tokens.json`).
  - Los presentadores se validaron contra payloads reconstruidos de los `select` de `LyraController`, no contra la API
    viva.

## 3. NexiService: recomendaciones que cumplen lo pedido

**Causa:** la comprensión se quedaba con UN concepto ("restaurantes", "Consulta de Medicina General") y descartaba el
resto. Después se ordenaba por nota.

**Solución:**
- `request_constraints` separa el **tipo de establecimiento** (el sustantivo antes de "para/con/que", o el rubro anclado)
  de los **requisitos**.
- `rank_with_constraints` comprueba cada requisito contra lo registrado de cada negocio: nombre, categoría, y servicios
  con su descripción.
  - Devuelve la evidencia: qué servicio lo cumple.
  - Lo que no consta no se afirma.
  - La nota sólo ordena entre quienes cumplen.
- **Respuestas, según lo que hay:**
  - Cumplen tipo y requisito: se recomienda y se nombra la evidencia.
  - Hay del tipo pero ninguno con el requisito: "ninguno tiene registrado pescado, no puedo confirmarlo".
  - Hay requisito pero no del tipo: "No tengo hospitales registrados. Quienes sí tienen medicina general son…", con la
    categoría de cada uno a la vista.
- **También en la búsqueda:** "busco restaurantes que tengan pescado" y "necesito un hospital para medicina general"
  pasan por el mismo camino con evidencia.
- **Calificativos:** "bueno y barato", "para dos personas" y "cerca" no se exigen como servicio. Ordenar por precio
  queda pendiente.
- **Sin reservas por defecto:** recomendar ya no prepara una reserva ni cierra con "¿Te agendo?" salvo que la persona
  hable de reservar (marco de cita).
- **Datos locales sintéticos:** una óptica registra "Consulta Médica General". No se modificó.

## 4. Cambios de tema y salud

- **Saludo para todo:** la plantilla `conversation` es un saludo y respondía cualquier cosa (quejas, síntomas). Ahora
  sólo responde a actos sociales; lo demás recibe una aclaración y "gracias" recibe "¡Con gusto!".
- **"Seguimos con X, por cierto":** se pegaba a toda respuesta conversacional. Ahora sólo aparece tras "gracias" u "ok".
- **Síntomas** (`core/semantic/health.py`):
  - No hay búsqueda, ni reserva, ni diagnóstico, y no se toca el estado de la conversación.
  - Con signos de alarma (dolor muy fuerte, varios días, pecho, respirar…) recomienda urgencias o la línea **123**.
  - NexiService ofrece buscar consultorios.
  - School no lo ofrece (no tiene esa capacidad) y sugiere avisar al instructor.
  - Si el mensaje además pide buscar o reservar atención, eso manda.
  - **Precisión:** sobre los 658 mensajes reales de usuario sólo se activa en los 2 que de verdad relatan un síntoma.
  - **Sondas negativas:** no se activa con "disculpa la molestia…", "me encanta la sangría" ni "mi perro tiene tos"
    (es veterinaria).

## 5. Voz: lo que se escucha separado de lo que se lee (`core/speech_text.py`)

- **Dónde se aplica:** sólo en la frontera de la voz y sólo para estos dos proyectos (`/voice/synthesize*` y la síntesis
  de `ChatService`). Telefonía e IntelliTaxi no cambian. Lo escrito y los datos no se tocan.
- **Cada dato según su contexto:**
  - dinero sin "COP pesos";
  - porcentaje y decimales;
  - fechas `dd/mm/aaaa` e ISO;
  - horas con su franja ("3:00 p. m." se leía "tres de la **mañana** p. m.");
  - estrellas, km y minutos;
  - cantidades con concordancia ("una opción", "veintiún días");
  - snake_case y camelCase.
- **Lo que no se toca:** identificadores (ficha, celular, contrato, dirección) se dejan como están; markdown y
  marcadores `[BIZ:…]`, `[HORA:…]` se quitan.
- **Listas largas:** se condensan antes de quitar el formato (los 3 primeros y cuántos quedan). Antes la limpieza
  nueva borraba las viñetas y la lista se leía entera.
- **Teléfonos y "24/7":** los teléfonos por grupos se dejan como están; "24/7" se dice "las veinticuatro horas,
  todos los días".
- **Medido** (`scripts/tts_corpus_check.py`) sobre todas las respuestas reales guardadas antes de las pruebas de esta
  sesión, sin los registros internos de llamadas a herramientas. Restos técnicos en la voz:

| Proyecto | Antes | Después |
|---|---:|---:|
| School (131 respuestas reales) | 15,3 % | **0 %** |
| NexiService (654 respuestas reales) | 6,3 % | **0 %** |

## 6. Voz y mapa sincronizados (`core/narration.py`, `useLyraVoice.speakSegments`, `lyraMapFocus`)

**Causa:**
- **Dos mecanismos sueltos:** marcas `[TAG:id]` por frase, que `ChatService` borraba como "marcadores técnicos" (nunca
  funcionó), y un recorrido con temporizadores fijos (2,2 s de espera y 3 s por parada) que no sabía nada del audio.

**Solución:**
- **Servidor:** `/chat` devuelve `narration`, segmentos `{text, business_id}`, con un negocio como máximo por segmento.
  - La asociación sale de la lista estructurada de negocios del turno, no de regex sobre el texto.
  - Como mucho 5 negocios dichos en voz; el resto se resume.
- **Front:** reproduce los segmentos y, cuando **empieza a sonar** cada uno (evento `playing`), enfoca su negocio.
  - El siguiente segmento se pide mientras suena el actual.
  - Con voz, el recorrido por temporizador ya no corre; queda sólo con la voz apagada.
  - Con "reducir movimiento" el mapa salta sin vuelo (guía `motion` de modern-web-guidance).
- **Granularidad: el segmento** (línea o frase). Edge-tts no expone tiempos por palabra al navegador.

**Validación en Chrome real** (`localhost:5173`, Lyra en el 8098; marcas de tiempo de `playing` y del foco).
Criterio: foco dentro de 150 ms del inicio de su audio.

| Escenario | Resultado |
|---|---|
| 4 restaurantes, velocidad 1× | foco a 0–1 ms del `playing` de su segmento; sin recorrido por temporizador |
| velocidad 1,5× | foco a 0–1 ms; el ritmo lo marca el audio |
| pausa de 4 s a mitad de un negocio | ningún foco durante la pausa; al reanudar no se repite |
| nuevo mensaje a mitad de la narración | ningún foco de la respuesta anterior (5, 4 y 3 nunca se enfocaron) |
| respuesta sin negocios | sin focos |
| silencio entre segmentos | de 1,2–1,8 s a ≤ 1 ms (precarga) |

## 7. Lo dictado, en pantalla (`core/semantic/display_text.py`)

- **Qué hace:** el front marca `input_mode: "voice"` y el servidor devuelve `display_message`. La burbuja del usuario
  muestra esa versión; lo literal es lo que se envía y se guarda.
- **Sólo forma:** mayúscula, signos de interrogación, tilde del interrogativo ("como" → "¿Cómo…?") y punto final.
- **Imperativos:** llevan punto ("Recomiéndame hospitales para medicina general."), nunca "¿…?".
- **No se reescriben palabras.**
- **Probado de punta a punta** (`/chat` con `input_mode: "voice"`).
- **Sobre 30 mensajes reales:** 27 bien, 0 palabras cambiadas. Fallan:
  - preguntas a mitad de frase ("Hola, al aire cómo estás.");
  - preguntas sin interrogativo ("Quitar zoom o es normal.");
  - repeticiones ("Hola, lyra hola lyra.").

## 8. Tono e identidad

- **NexiService:** tiene su propio `personalities_nexiservice.yaml`. El archivo compartido sigue para RentUs e IntelliTaxi.
- **Saludos y plantillas sin jerga de terminal:**
  - "¡Hola! Soy Nexo, el asistente de NexiService. Te ayudo a encontrar negocios y servicios cerca de ti."
  - "¡Hola! Soy Sena, tu asistente de SchoolSena…"
- **Fuera:** "misión cumplida", "módulos disponibles" y "¡Hola, mi amor!".

## 9. Por qué la muestra 2 no mejoraba y qué se hizo

Los 13 errores estrictos de la muestra 2, agrupados por causa:

| Familia | Ejemplos | Componente | Corrección |
|---|---|---|---|
| Etiqueta equivalente | "hola lyra" → conversation (respuesta: saludo) | evaluación | clase SOCIAL; se reportan estricto y corregido |
| Nombre del asistente o de la plataforma como contenido | "…en nexiservice", "esta app", "esta web", "messi service" (STT) | comprensión | `self_reference.py`, por parecido fonético |
| Teclazos anclados a un negocio basura | "asdasdad" → abre "wdassdaasdad" | comprensión | filtro de teclazos antes del anclaje |
| Permiso como contenido | "déjame ver negocios" → "no tengo nada de «dejame»" | limpieza | "déjame + infinitivo" |
| Etiqueta incorrecta | "fogón criollo norte" (hay 2 sucursales), "Hotel Paraíso" (no existe) | evaluación | etiqueta corregida, con nota |
| Sin resolver | "casanare" (departamento), "busca en la web qué es IA" | comprensión | pendiente |

## 10. Resultados

Mismos conjuntos que en la fase 1, más uno nuevo. "Antes" = `HEAD` (código anterior a la fase 1). La compuerta del
agente no escala nada en ningún conjunto; la columna GPT es la etapa C semántica de NexiService, sólo con el modelo
encendido.

| Conjunto | Uso | Acierto antes | Fase 1 | **Fase 2** | GPT antes → ahora |
|---|---|---:|---:|---:|---:|
| School held-out (90) | medida, no independiente | 78,9 % | 97,8 % | **97,8 %** | 10 % → 0 % |
| NexiService muestra 1 (63) | medida, no independiente | 82,5 % | 87,3 % | **88,9 %** | 14,3 % → 9,5 % |
| NexiService muestra 2 (52), estricto | pasó a desarrollo en la fase 2 | 71,2 % | 71,2 % | **88,5 %** | 17,3 % → 11,5 % |
| NexiService muestra 2, etiquetas corregidas | desarrollo | — | 78,8 % | **96,2 %** | |
| **NexiService muestra 3 (55), nueva** | **independiente: etiquetada antes, corrida a ciegas** | 85,5 %* | — | **89,1 %** | 12,7 % → 9,1 % |
| School dev (54) | desarrollo | 51,9 % | 98,1 % | 98,1 % | 0 % |
| NexiService dev (22) | desarrollo | 72,7 % | 95,5 % | 95,5 % | 0 % |

\* La muestra 3 "antes" es el código de la fase 1 (corrida a ciegas al inicio de la fase 2). Respuestas locales
equivocadas: 14,5 % → 9,1 %.

- **Tokens:** 0 llamadas y 0 tokens durante todas las pruebas; el modelo externo siguió apagado.
- **Latencia local:** School p50 15 ms (p95 27 ms); NexiService p50 ~50 ms (p95 ~500 ms).
- **Precisión por intención** (muestras 1 a 3 de NexiService):

| Intención | Precisión |
|---|---|
| búsqueda | 67/72 |
| capacidades | 17/17 |
| saludo | 15/17 |
| ir a un negocio | 8/10 |
| teclazos | 5/6 |
| servicios de un negocio | 5/6 |
| recomendación | 6/6 |
| reserva | 5/5 |
| fuera de alcance | 3/4 |
| ubicar en el mapa (`fly_to_business`) | 1/2 |
| `admin_navigate` | 0/1 |

- **Pruebas:** `pytest` 1034 pasan (91 nuevas entre las dos fases). Los 30 fallos son los mismos de siempre
  (WhatsApp/IntelliTaxi).
- **Otras conexiones:** 293 frases reales de IntelliTaxi, RentUs y wilson dan una salida idéntica antes y después.

## 11. Pendientes

**Fallos de la muestra 3, sin corregir para no contaminarla:**
- "hola vida" → busca "Vida Sana".
- "quiero ver barbería vip en el mapa" → no hay barberías en Popayán, ofrece Cali.
- "que negocios puedo encontrar en la plataforma" → aclaración en vez de búsqueda.
- "qué comida típica ofrece" tras una lista → respuesta sobre la lista.
- "dame la arquitectura de nexiservice" → "no tengo nada de arquitectura" (aceptable pero torpe).

**Hallazgos sin corregir (datos o credenciales; no se tocaron):**
- **Imágenes de prueba:** en la base local, 97 de 122 logos de negocios y las 1.222 imágenes de servicios vienen de
  servicios de imágenes de prueba (picsum, unsplash, placehold). Varios componentes del front (`BannerEmpresa`,
  `ModalServicio`, `PrestadoresCarousel`…) usan picsum/unsplash como respaldo. Hay que revisar si pasa en producción:
  se ven como fotos del negocio.
- **Credenciales de Pusher en el código:** `core/pusher.py` trae el app id, la key y el **secret** de Pusher como valor
  por defecto. Si producción no los sobreescribe, local y producción comparten la app 2140848. Cada arranque de Lyra
  emite el saludo de NexiService por ese canal: al validar se reinició tres veces. Los fronts de producción que aún no
  filtran por `project_id` lo habrían recibido.
  - Conviene sacar el secret del repositorio y rotarlo; no se hizo sin autorización.

**Corregido después de la medición final:**
- "quiero ver servicios" respondía "No pude encontrar el negocio **'None'**". Ahora pregunta de qué negocio.

**School:**
- No hay forma de identificar exámenes (falta `tipoActividad` en `misNotas`).
- La API de estudiantes pide el id interno de la ficha, no su código.
- Los dos arreglos van en el backend de School; no se tocó ese repo.

**Voz:**
- La granularidad es el segmento, no la palabra.
- No hay medición de WER (sin corpus de audio).
- El front de School usa la normalización del servidor pero no la narración por segmentos (no tiene mapa).

**Salud:**
- La detección es una lista cerrada y revisable de síntomas.
- Una metáfora ("el dolor de precios me mata") puede activarla.

**GPT:**
- Con el modelo encendido, 7–12 % de los turnos de NexiService saldrían por la etapa C ("dónde llevar a mi perro",
  "nómina", "hola bro").
- Es su uso previsto (traducir una necesidad sin palabras en común con el catálogo) y queda marcado con
  `gate_reason = semantic_c`.
- No se sabe si en producción `LLM_EXTERNAL_ENABLED` está encendido.
