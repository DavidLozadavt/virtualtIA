# Nexo en School — Fase 6: continuidad (2026-11-17)

Sin commit (virtualtIA, SchoolSenaBackFinal). NexiService sin cambios funcionales.

## 1. Diagnóstico
| Síntoma | Causa estructural |
|---|---|
| "¿Te llevo a la pantalla?" → "Sí" → "No te entendí" | La pregunta sólo existía en el texto. No había representación de lo propuesto; el "sí" llegaba al NLU sin nada a qué referirse. Las preguntas del selector de menú tampoco quedaban registradas. |
| "¿Cómo se va a llamar?" → "NU EVENT" → navega a Eventos | El flujo de creación no tenía prioridad sobre la recuperación de módulos: un valor que se parecía a un módulo se leía como intención. Una creación interrumpida se perdía. |
| "El viernes de la próxima semana" → 2026-11-20 | El lector de fechas trataba "próximo viernes" y "viernes de la próxima semana" igual; no había fecha completa en el resumen. |
| "Planeación" → "Redes" → Planeación otra vez | La ruta de Planeación contiene "redes" y el desempate prefería el módulo consultable; el nombre exacto no contaba. |
| "Dame los pagos pendientes" → sin datos | No había consulta integrada (y la del backend no filtraba por empresa). |
| "Dame un resumen completo" → no entiende | No existía un acto de resumen sobre las consultas autorizadas. |
| "Crear formulario y también… cuáles activos" → sólo una | El separador de compuestas no admitía "y también/además". |
| 94 pantallas en una lista | Presentación plana, sin niveles. |

## 2. Correcciones
- **Propuestas** (`schoolsena_ops.py`): toda respuesta que pregunta deja `oferta` = {tipo
  (navegar/elegir), módulo, ruta, opciones, turno}; el interceptor la sella con el turno y sólo
  vale en el siguiente (y 10 min). Respuestas breves clasificadas (afirma, niega, navega,
  muestra) se interpretan contra ella; las aclaraciones del selector de menú también.
- **Operaciones conversacionales genéricas** (`OPERATIONS`): campos declarados (texto, fecha,
  hora, hora fin, modalidad, descripción), pregunta por lo que falta, resumen con fecha completa,
  sí inequívoco ("listo/ok/bueno" no autorizan), correcciones en el resumen, cancelación, pausa
  como borrador ante un cambio de tema explícito y reanudación ("continuemos con el evento").
  El dato pedido tiene prioridad; un mensaje con datos de la operación es respuesta aunque se
  parezca a un módulo. Eventos y **formularios** (nuevo) usan el mismo motor.
- **Fechas** (`core/semantic/fechas.py`, sólo School; NexiService intacto): este/el/próximo
  viernes, viernes de la próxima semana, mañana, pasado mañana, dentro de N días (8 = una
  semana, uso colombiano, avisado), primer/último día del mes, fechas escritas, cambio de año.
  "El próximo viernes" cercano se avisa para confirmar.
- **Módulos**: nombre exacto antes que consultabilidad; empates → "¿A cuál te refieres?" con
  propuesta de elección; un nombre de una palabra sólo cuenta si es distintivo; "¿Y los X?"
  repite la consulta sobre otro módulo; nombre pelado = navegar sólo si no trae más contenido.
- **Accesibilidad** = permisos de la pantalla ∪ consultas/operaciones que el backend habilita.
- **Compuestas** con "y también/además/luego…": se atiende primero la consulta y después la
  operación; lo que una parte no nombra lo hereda de la otra.
- **Resúmenes**: general (consultas autorizadas, en paralelo, máx. 6 fuentes), de un módulo,
  del mes, y "¿qué tengo pendiente?".
- **Permisos por niveles**: panorama (pantallas, áreas, qué consulta Nexo, qué crea), detalle
  por área, "¿cuáles puedo consultar desde aquí?", "¿cuáles requieren abrir la pantalla?",
  "¿qué puedo crear?", "¿qué no puedo modificar?", con la aclaración de que School autoriza por
  módulo y las opciones de pantalla son de la interfaz.

## 3. Cobertura nueva (backend + Lyra, permiso y empresa del token)
Pagos pendientes (empresa; la ruta original no filtraba), solicitudes de inscripción (lógica
original, empresa forzada del token; la ruta original acepta `idCompany` en la petición),
configuración de inscripción, historias y reels, informes de instructores, y **crear
formularios** (borrador). Total: 26 consultas + 2 creaciones.

## 4. Seguridad
- `App\Util\PermisosEfectivos` (fuente única) y middleware `permiso.efectivo` aplicado a
  `POST/POST{id}/DELETE eventos-multimedia` y `POST/PUT/DELETE formularios`, con los mismos
  permisos que exige la pantalla del front (más el permiso dedicado). Verificado: aprendiz
  básico 403 (antes 201), admin 201 y borrado 200. Compatible con los consumidores actuales.
- Despliegue: revisar que los usuarios que hoy crean eventos/formularios tengan alguno de esos
  permisos (lo exige ya su pantalla); desplegar backend; monitorear 403 la primera semana.

## 5. Pruebas
| Prueba | Resultado |
|---|---|
| Continuidad real (`scripts/e2e_school_continuidad.py`, 10 casos obligatorios) | 17/17 |
| Permisos reales (`scripts/e2e_school_permisos.py`) | 25/25 |
| **Navegador real** (`scripts/e2e_school_navegador.py`, login por formulario) | 15/15 |
| Fechas (29 casos, distintos días, fin de mes y de año) | 29/29 |
| pytest | 1247 ✔ (+62) / mismos 30 ✗ |

Navegador: hubo que usar el front ya levantado en 4210 (el backend sólo admite por CORS
4200/4210/5173); en 5175 el login fallaba por CORS, no por credenciales.

## 6. Métricas
| Conjunto | Fase 5 | Fase 6 |
|---|---:|---:|
| School dev / heldout | 100 / 100 | 100 / 98,9 (bare "resumen" → resumen general en vez del centro) |
| Fase 4 independiente | 100 | 100 |
| Fase 5 independiente | 85,7 (1.ª) | 92,9 |
| **Fase 6 independiente (continuidad)** | — | **14/24 = 58,3 % en la 1.ª corrida**; 24/24 tras corregir familias (ya no ciego) |
| GPT | 0 | 0 |
| Latencia NLU local | p50 62 ms | p50 30–69 ms, p95 ≤ 181 ms |

Familias halladas por la corrida ciega y corregidas: verbos de crear ("monta/organiza/programa
un…"), "no, cancélalo", "¿y los formularios?", arnés que pasaba School por el router de
NexiService (crash `biz_name` sólo del arnés; el fallo latente del router queda anotado).

## 7. Regresiones NexiService
Cinco conjuntos idénticos, diálogos 18/18 · 11/12 · 11/12, reservas reales 8/8.
