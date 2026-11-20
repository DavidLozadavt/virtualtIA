# Nexo en School — Fase 7: formularios, eventos y su vínculo (2026-11-17)

Sin commit (virtualtIA, SchoolSenaBackFinal, schoolSenaFrontFinal). NexiService sin cambios funcionales.

## 1. Relación eventos ↔ formularios (investigada antes de implementar)
- **Ya existía en School**: `evento.idFormularioInterno → formularios.id` (migración
  `2026_05_20_181919`, FK `ON DELETE SET NULL`). Un evento tiene **un** formulario interno: su
  formulario de inscripción. Lo vinculan el asistente `FormIntegrationWizard` (POST
  `eventos-multimedia/{id}` con `idFormularioInterno`, `formProvider: 'interno'`, `formUrl: ''`) y
  `EventForm`. Las inscripciones son las `formulario_respuestas` de ese formulario (lo que cuenta
  `EventoController::inscripciones`); `participante_evento` es otro registro (asistentes con cuenta).
- Estados: `formularios.estado` ∈ borrador/publicado/cerrado. Un formulario en borrador no se puede
  responder sin sesión (`showPublic` → 403): vincular **no** equivale a publicar.
- Las modalidades "asistencia/evaluación/satisfacción" no existen como relación en la base; no se
  crearon (habrían sido vínculos sin soporte en School). Se usan como **plantillas** de formulario.

## 2. Constructor conversacional (mismo contrato que el front)
- Contrato del constructor (`formBuilderTypes.ts`, `QuestionCard.tsx`): tipos del enum
  (texto_corto, texto_largo, opcion_multiple, casillas, desplegable, escala_lineal, fecha, hora,
  archivo); "Número" = texto_corto + `configuracion.soloNumeros`; escala con min/max; no hay
  "correo" ni "teléfono" como tipo, ni secciones. Nexo usa exactamente eso.
- **Borrador estructurado** (`schoolsena_formdraft.py`, puro): cada instrucción → operaciones
  (agregar varias, en posición; eliminar; mover; tipo; obligatoriedad, incl. "todas menos…";
  opciones; renombrar; duplicar; título/instrucciones; deshacer con historial; descartar a lo
  guardado; convertir textos a selección "donde tenga sentido"; acortar; quitar datos personales;
  "solo necesito…"; cambiar a plantilla). Ordinales sobre la versión vigente; la pregunta "en foco"
  resuelve "mejor que sea desplegable".
- Estados distinguidos: propuesto (plantilla), borrador sin guardar, guardado (verificado leyéndolo
  de vuelta), publicado (sólo con confirmación). Con título, el formulario ya **no** se crea vacío:
  se prepara el borrador ("sí" lo crea vacío si se quiere).
- **Guardado** (`PUT lyra/formularios/{id}`) con el MISMO `FormularioController::update`
  (sincroniza preguntas/opciones por id; respuestas y opciones conservan sus ids). `version` = huella
  de la estructura → 409 si el constructor lo cambió entre medias (no se pisa; "cargar la de School"
  o "sobrescribir"). Crear con preguntas en `POST lyra/formularios`. Validación con el contrato del
  constructor (tipos, opciones obligatorias en selección, claves de configuración conocidas, escala).
- **Generación asistida local**: "un formulario para inscribir aprendices a una capacitación" →
  propuesta (nombre, tipo y nº de documento, correo, ficha, programa, teléfono opcional) sin datos
  sensibles (sin nacimiento, dirección, género, salud). 0 llamadas a GPT.

## 3. Referencias a registros (`state["entidades"]`)
Cada evento/formulario creado, guardado, vinculado o mostrado queda con tipo, id real y nombre.
Resolución: número explícito → "el primero que hicimos" → nombre/fecha contra School (cruzado con lo
recordado si dice "ese/el que creamos") → lo más reciente. Varios coinciden → se pregunta con números
(la elección se re-ejecuta por id). Antes de escribir se revalida contra School (existe, misma
institución, permiso).

## 4. Vínculo conversacional
Resumen con ambos registros, fecha completa, estado del formulario ("no está publicado: nadie podrá
inscribirse hasta que lo publiques; no lo publicaré sin que me lo pidas"), reemplazo de un vínculo
anterior, y guardado previo si el formulario todavía no existe en School. Sólo un sí inequívoco en el
turno siguiente lo ejecuta (`PUT lyra/eventos/{id}/formulario`); se verifica releyendo el evento.
Consultas: "¿qué formulario tiene ese evento?", "¿está publicado?", "¿dónde puedo verlo?",
"¿cuántas personas se han inscrito?" (respuestas reales del formulario vinculado, con permiso de eventos).

## 5. Seguridad
- Endpoints nuevos de Lyra (todos `auth:api`, empresa del token, 404 igual si no existe o es de otra
  institución): `GET/PUT lyra/formularios/{id}`, `PUT lyra/formularios/{id}/estado`,
  `GET lyra/eventos/{id}`, `PUT lyra/eventos/{id}/formulario`. Permisos = los de la pantalla
  (`editar_formulario`, `publicar_formulario` = GESTION_FORMULARIOS|GESTION_USUARIO|AULA_VIRTUAL_INSTRUCTOR;
  `vincular_formulario` = GESTION_EVENTOS|GESTION_USUARIO|AULA_VIRTUAL_INSTRUCTOR).
- **Hallazgo (no corregido, intervención):** las rutas originales `GET/PUT/DELETE formularios/{id}`,
  `GET eventos-multimedia/{id}`, `POST eventos-multimedia/{id}` (update), `DELETE eventos-multimedia/{id}`
  y `GET eventos-multimedia/{id}/attendees` buscan por id **sin filtrar empresa**: con permiso en su
  propia empresa, un usuario puede leer/modificar registros de otra conociendo el id. Además el
  update original de eventos acepta un `idFormularioInterno` de otra empresa. Nexo no las usa.

## 6. Frontend (School)
`LyraFormDraft.tsx` + estilos `lx-form*`: tarjeta compacta de la respuesta más reciente con
formulario (preguntas numeradas, tipo, opciones, "Obligatoria" en texto, estado "Sin guardar /
Cambios sin guardar / Guardado · borrador / Publicado", "Abrir en el constructor"). El texto de la
respuesta sigue describiendo todo (voz y clientes sin tarjeta). `ChatResponse` suma `form_draft` y
`school_outcome` (opcionales).

## 7. Pruebas
| Prueba | Resultado |
|---|---|
| Backend nuevo (humo real, 8005) | 17/17 |
| E2E conversacional real (`scripts/e2e_school_formularios.py`) | 51/51 |
| Navegador real fase 7 (`scripts/e2e_school_navegador_f7.py`): borrador, guardar, vincular, abrir y **editar en el constructor**, Nexo relee el cambio, vínculo visible en Formularios | 14/14 |
| Continuidad fase 6 / permisos / navegador fase 6 | 17/17 · 25/25 · 15/15 |
| pytest | 1263 ✔ (+16) / los mismos 30 ✗ (WhatsApp/IntelliTaxi, idénticos a la fase 6) |

El e2e cubre: nombres duplicados de formularios y eventos, "el primero que hicimos", cambio de tema,
reanudación, confirmación vieja que no autoriza, "listo" que no autoriza, cancelación, sin permiso,
fallo del backend al guardar (borrador conservado, reintento sin duplicar), guardar dos veces,
cambio concurrente (409), publicado y borrador, validación (selección sin opciones), varias
preguntas en un turno. Todo comprobado en la base; datos de prueba borrados.

## 8. Métricas
| Conjunto | Fase 6 | Fase 7 |
|---|---:|---:|
| **Fase 7 independiente** (52 turnos, servicios reales) | — | **39/52 = 75,0 % en la 1.ª corrida (ciega)**; 52/52 tras corregir familias (ya no ciego) |
| School dev / heldout / f4 / f5 / f6 | 100 / 98,9 / 100 / 92,9 / 100 | idénticos |
| NexiService 5 conjuntos | 95,5 / 95,2 / 96,2 / 92,7 / 74,0 | idénticos |
| Diálogos NexiService | 18/18 · 11/12 · 11/12 | idénticos |
| GPT | 0 | 0 |
| Latencia por turno con backend real (fase 7 ind.) | — | p50 ≈ 0,6 s, p95 ≈ 1,5 s (dominada por las llamadas a School) |

Familias de la corrida ciega, corregidas: título dicho como "ponle de nombre X" mientras se pedía;
continuaciones elípticas ("y otra abierta para…") y verbos de contenido ("va a pedir…"); deshacer
con "devuélvelo como estaba"; registros nombrados directamente ("el evento Cine foro X",
"abre el formulario X para editarlo"); "ponle el formulario X" con un evento en foco; "no, déjalo así"
sin nada pendiente.

## 9. Otros módulos (evaluado, no implementado)
El mecanismo es reutilizable: `recordar/resolver` sirve para cualquier registro con id; el patrón
"acción propuesta → sí al turno siguiente → verificar" para cualquier escritura; el borrador por
operaciones para listas ordenadas. Candidatos: **configuración de inscripción** (elige un formulario
`idFormularioInscripcion`: mismo resolver), **historias y reels** (el evento ya enlaza
`idGrupoMultimedia`: mismo patrón de vínculo), **solicitudes** (aprobar/rechazar = acción
confirmada). Redes/planeación/informes son CRUD de pantalla sin necesidad clara de edición
conversacional.

## 10. Pendientes reales
- Reservas reales de NexiService no se re-ejecutaron: los procesos del 8002 no muestran si corren con
  `MAIL_MAILER=log` y la prueba crea citas (correos). Fase 7 no toca su código.
- Ver §5 (rutas originales sin filtro de empresa): decisión y despliegue en School.
- Teléfono real/voz no probados.

## 11. Agregado: varias operaciones en un mensaje (`schoolsena_plan.py`)
"Créame un evento para el próximo viernes a las 10, hazme un formulario de inscripción con nombre,
documento, correo y ficha, deja el teléfono opcional y vincula el formulario con el evento": el mensaje
se parte en pasos (crear evento, crear formulario, vincular) y ajustes ("deja X opcional"); los pasos
se refieren a lo que crea el mismo plan, y lo que no crea se busca en School ("vincúlalo con el evento
Expo"). Permisos de TODOS los pasos antes de pedir datos; pide sólo lo que falta; un resumen único,
corregible ("mejor a las 11", "agrega la jornada", "que el correo sea opcional"); un sí inequívoco
ejecuta en orden verificando cada paso; si uno falla dice qué quedó hecho y el reintento no repite.
Lo enumerado queda obligatorio; lo dicho opcional, opcional. Nunca publica. E2E real: 62/62 (11 nuevas).
