# Nexo en School — Fase 5 (2026-11-17)

Integración estructural entre permisos efectivos, módulos reales y conversación. Sin
commit (virtualtIA, SchoolSenaBackFinal). NexiService no se tocó (regresión verificada).

## 1. Diagnóstico de los 10 casos reales
| Caso | Causa |
|---|---|
| "¿Qué regionales están registrados?" → fuera de capacidades | No existía consulta de regionales ni representación de módulos: sólo capacidades fijas del NLU. |
| "Regionales" → navega (bien) | El menú del front sí tenía la sección. |
| "¿Cuántos hay?" → actividades pendientes | Navegar no registraba el módulo como contexto; el estado seguía con la capacidad anterior. |
| "¿Qué permisos tengo?" → no comprende | Los permisos se usaban para autorizar, nunca se modelaron como tema de conversación. |
| "Gestión de informes de instructores" → lista de instructores | El NLU elegía por la primera palabra que tocaba un concepto; no había descripción de módulos. |
| "Crear un nuevo evento" → fuera de capacidades | Ninguna operación de escritura integrada; además el endpoint de School no verifica permisos. |
| "Llévame a formularios o cuáles tengo activos" → sólo navega | La navegación se resolvía primero y no había lectura de peticiones compuestas. |

Resultado: el problema era de **representación** (no había catálogo de módulos ligado a
permisos) y de **integración** (no había consultas ni escrituras más allá de 15 capacidades
fijas), no de sinónimos.

## 2. Arquitectura de autorización
1. **Permisos efectivos (backend, en vivo):** `LyraController::permisos()` = roles de la
   activación + permisos directos (Spatie `getAllPermissions`), en la empresa del token.
   `GET lyra/capacidades` devuelve permisos, consultas habilitadas, secciones con ruta y —sólo
   a quien administra roles y permisos— el catálogo completo.
2. **Inventario del código** (`scripts/school_inventory.py` → `projects/school_inventario.json`
   y `docs/SCHOOL_INVENTARIO.md`): 155 rutas del front con sus `requiredPermissions` (resuelve
   las constantes `...PERMISOS_X`), componente, endpoints que usa la pantalla y operaciones.
   Es lo que liga permiso ↔ módulo, porque el backend no conoce los permisos de cada pantalla.
3. **Registro de integraciones** (`schoolsena_modules.py`): `QUERIES` (12 consultas) y
   `CREATES` (crear evento), cada una con su ruta, filtros y presentación.
4. **Decisión por petición:** módulos accesibles = inventario ∩ permisos efectivos; ejecutar
   sólo si hay integración registrada; el backend vuelve a autorizar cada consulta (403 →
   se dice y se vacía la caché). Caché de capacidades: 60 s por token.
- Un permiso nuevo con ruta → navegable sin código. Sin integración → "tienes acceso, todavía no
  lo consulto desde aquí". No se llama a ningún endpoint por el solo hecho de existir el permiso.

**Hallazgo de modelo:** School no tiene permisos por operación. Un permiso abre una pantalla y lo
que se puede hacer dentro (consultar/crear/editar/eliminar) es lo que ofrece la pantalla. Nexo
lo explica así; no inventa granularidad que no existe.

## 3. Inventario y cobertura
- 129 pantallas con permiso (sin parámetros); operaciones en su UI: consultar 122, crear 114,
  editar 78, eliminar 79.
- **Consultadas por Nexo con datos reales (backend, permiso y empresa):** horario, notas,
  pendientes, fichas, estudiantes de ficha, entregas, resumen, nómina, instructores, contratos,
  centros, sedes, ambientes, periodos (de empresa o propios), jornadas, planeación, regionales,
  eventos (filtro estado), formularios (filtro estado), carpetas viajeras, configuración de pagos.
- **Escritura:** crear evento (permiso, empresa, validación, resumen, sí explícito, verificación
  posterior). Editar/eliminar: se reconocen y se explican; no se ejecutan desde la conversación.
- **Navegables:** todas las pantallas a las que el usuario tiene permiso.
- **No expuesto:** pagos/facturación y nómina detallada (datos financieros), ediciones y
  borrados (sin contrato de confirmación probado).

## 4. Integraciones implementadas
- Backend School: `lyra/regionales`, `lyra/eventos` (GET/POST), `lyra/formularios`,
  `lyra/carpetas-viajeras`, `lyra/configuracion-pagos`; `capacidades` con catálogo condicionado;
  lista blanca alineada con los permisos de cada pantalla (centros, periodos, jornadas).
- Lyra: `schoolsena_modules.py` (registro, recuperación por cobertura y palabras distintivas,
  actos, compuestas, contexto, permisos, creación), herramientas nuevas en `tools/schoolsena.py`,
  interceptor (capa de módulos antes del menú; la navegación fija el módulo activo), ayuda
  adaptada a permisos, arnés con permisos simulados por perfil.

## 5. Pruebas de permisos (reales, `scripts/e2e_school_permisos.py`) — 25/25
Mismo rol con permisos distintos; aprendiz con permisos extra; instructor con nómina y regionales
directos; admin restringido; permiso nuevo navegable; escritura no autorizada rechazada por el
backend (403); revocación en caliente (negado en el turno siguiente, sin esperar caché); "¿qué
permisos tengo?", "¿tengo permiso para crear eventos?", "permisos que no tengo" (sólo con
catálogo); operación inexistente; herramienta no integrada; evento creado, persistido con los
datos pedidos y borrado.

## 6. Métricas
| Medida | Antes (fase 4) | Fase 5 |
|---|---:|---:|
| 10 casos del diagnóstico resueltos | 4/10 | 10/10 |
| Pantallas que Nexo entiende (navegar) | menú enviado por el front | 129 por permiso |
| Consultas con datos reales | 16 | 21 + 1 creación |
| School dev / heldout / fase 4 indep. | 100 / 100 / 100 (*) | 100 / 100 / 100 (*) |
| School fase 5 independiente (1.ª corrida) | — | 24/28 (85,7 %) |
| Permisos e2e reales | — | 25/25 |
| GPT | 0 | 0 |
| Latencia NLU local (fase 5 indep.) | — | p50 62 ms · p95 169 ms |
(*) ya no ciegos: casos corregidos mirándolos en fases 4–5.

Fallos de la corrida independiente: "q eventos tenemos programados" y "conceptos de la
configuración de pagos" (umbral de palabras distintivas; el segundo quedó cubierto después por la
regla de nombre completo), "las sedes porfa" (se toma como sección y navega), "el primero"
(artefacto del arnés: sus datos simulados de formularios vienen vacíos).

## 7. Regresiones
pytest 1185 ✔ (+31) y los mismos 30 ✗ previos (WhatsApp/IntelliTaxi). NexiService: cinco
conjuntos idénticos, diálogos 18/18 · 11/12 · 11/12, reservas reales 8/8.

## 8. Hallazgos de School que requieren decisión
- `POST eventos-multimedia` (y en general los CRUD) no verifican permisos en el backend: sólo el
  front oculta la pantalla. Nexo usa `lyra/eventos`, que sí verifica; el endpoint original
  sigue abierto a cualquier sesión.
- La pantalla de eventos y la de formularios se abren con `GESTION_USUARIO` o
  `AULA_VIRTUAL_INSTRUCTOR`, no con `GESTION_EVENTOS`/`GESTION_FORMULARIOS` (que existen).
- 13 permisos usados por rutas del front no existen en la base (p. ej. `BOLETIN_ADMIN`,
  `GESTION_RUTAS`, `AULA_VIRTUAL_INSTRUCTOR_PLANEACION_PEDAGOGICA`): nadie puede abrir esas pantallas.
- El rol `APRENDIZ` tiene 70 permisos; `INSTRUCTOR SENA` incluye regionales y centros.
