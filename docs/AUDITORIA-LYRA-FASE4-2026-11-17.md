# Lyra / Nexo — Fase 4 (2026-11-17)

Reservas transaccionales, continuidad tras el login, autorización dinámica en School,
consultar ≠ navegar, refinamiento de lo mostrado, identidad Nexo y tarjetas sin imagen.
Todo sin commit (virtualtIA, NexiService front y backendInvent, School back y front).

## 1. Correcciones (causa → solución)

### Reservas «confirmadas» que no aparecían en «Mis Reservas»
- **Causa verificada en la base:** la reserva sí se creaba (agenda 12, 18/11 09:00), pero Lyra
  escribía directo en `agenda` y usaba el id de la PERSONA como id del CLIENTE (`tercero`). El
  administrador no tiene ficha de cliente con su correo: la cita quedó en `idCliente=1`, que es
  otra persona (Sofía Restrepo). «Mis Reservas» busca por `tercero.email` → invisible para él.
  Además: no había resumen ni "sí" explícito (al tener todos los datos se creaba), la identidad
  salía del `user_id` que manda el navegador (manipulable) y el éxito se anunciaba sin id.
- **Solución** (`tools/nexi_booking.py`, `confirm_appointment` en dos fases):
  - identidad = token verificado contra la pasarela (`user_logged`), caché 60 s;
  - creación con el MISMO endpoint del front (`store_agenda_servicio_nexiservice`): ficha de
    cliente por correo, agenda, prestación, carrito, aviso al negocio; y si la empresa es del
    ERP, el backend la delega allá (con la escritura directa nunca habría llegado al ERP);
  - fase 1: valida servicio, fecha (zona America/Bogota, "mañana" se fija en ese momento),
    hora, que no haya pasado y choque de agenda → resumen "¿La agendo?" registrado como
    propuesta del turno; fase 2 sólo con un sí a ESE resumen ("sí", "confírmala", "agéndala";
    "listo/ok" no bastan: se repregunta; cambiar un dato vuelve a resumir);
  - confirmada sólo con id real y tras encontrarla en «Mis Reservas»; el número que se da es
    el mismo que ve el usuario (#000004);
  - idempotencia: antes de crear se busca; timeout tras enviar → se consulta (creada =
    confirmada; si no, "estado desconocido" y se propone verificar, sin duplicar);
  - estados: preparing, awaiting_confirmation, awaiting_auth, submitted, confirmed,
    rejected, failed, unknown.
- backendInvent: `orWhere('identificacion', null)` buscaba "IS NULL" y podía asignar la cita a
  cualquier cliente sin documento → sólo se usa si viene.

### Continuidad después del login
- La reserva aceptada se guarda en el servidor (estado de la conversación) con caducidad de
  30 min y, si había sesión que expiró, atada a esa persona. El front sólo guarda en
  `sessionStorage` el id de conversación + caducidad (sobrevive a recarga y redirección).
- `require_auth` → el front lleva al login; al aparecer la sesión envía `__AUTH_COMPLETED__`
  (no se guarda como mensaje). El servidor revalida token, horario y autorización y crea; si
  no había un sí previo, sólo muestra el resumen (entrar no autoriza nada).
- Verificado en el navegador CON recarga completa: resumen → sí → login → reserva #000004
  creada sola → «Mis Reservas» la muestra.

### «Mis Reservas» mostraba un día menos
- `new Date("2026-11-19")` = medianoche UTC → 18/11 en Colombia. Afectaba a TODAS las
  reservas (también manuales). Se lee como fecha local.

### "De esos cuatros… pescado" → "No encontré cuatros pescado"
- **Causa:** ninguna capa reconocía el alcance "de esos / los anteriores / los cuatro / entre
  esos"; "cuatros" (plural con errata) sobrevivía como contenido y `request_constraints` lo tomó
  por tipo de establecimiento.
- **Solución:** `core/semantic/refinement.py` separa ALCANCE (conjunto mostrado u ordinal),
  OPERACIÓN (recomendar, filtrar, descartar, más barato, más cercano, abren hoy) y REQUISITO.
  El manejador evalúa SÓLO los negocios mostrados (`rank_with_constraints(only_ids=…)`) y
  sólo afirma lo registrado (carta = servicios con precio). Horario de atención y domicilio no
  existen en la base: se dice. Además los numerales ya no son contenido ("a las 11" iba al
  directorio).

### Tarjetas con imagen rota
- **Causa:** sin logo válido `fixImageUrl` da null y se pintaba `<img>` sin `src` → icono roto
  con el texto alternativo; `onError` nunca llegaba (no hay petición).
- **Solución:** sin foto (vacía, de banco o que falla) la tarjeta pasa a forma compacta con
  iniciales en el acento del sistema; con foto, `loading="lazy"`, tamaño reservado, alt vacío
  (el nombre ya es el título). Nombres a dos líneas, categoría truncada. Verificado: 5/5
  tarjetas compactas, 0 imágenes rotas, celular y escritorio.
- Párrafos: `_strip_debug_markers` borraba toda línea en blanco y pegaba el texto final al
  último ítem de cada lista. Ahora sólo comprime 3+ saltos.

### School: autorización por permisos efectivos
- Antes: por nombre de rol con subcadena (`hasRole('ADMIN')`). Ahora `LyraController`
  declara `CAPACIDADES` (consulta → permisos que la habilitan) y calcula los permisos EN VIVO
  (roles de la activación + permisos directos, Spatie `getAllPermissions`). Cada consulta se
  autoriza en el backend; `GET lyra/capacidades` le dice a Nexo qué consultas tiene y TODAS
  las secciones con ruta a las que tiene permiso (un permiso nuevo es navegable sin código;
  no consultable hasta que exista su consulta).
- Lyra: `schoolsena_caps.py` (caché 5 min por token); la oferta, las negaciones y las
  herramientas que se ofrecen al modelo salen de ahí. "Soy administrador" no cambia nada.

### School: funcionalidades reales y consultar ≠ navegar
- Nuevas consultas seguras (empresa del token): centros, sedes, ambientes (infraestructura),
  periodos (los de la empresa o, sin permiso de gestión, los de la propia matrícula),
  jornadas, planeación (redes y programas).
- NLU: capacidades nuevas + acto: listar / contar ("¿cuántos…?") / leer ("léemelos", prosa
  para voz, también como seguimiento) / navegar ("llévame…"); nombre pelado de sección = abrir.
- "¿Cómo me fue en el examen de ayer?" → horario del lunes 23/11: "parcial"/"saqué" no
  evocaban notas, el seguimiento llevaba "ayer" al horario y el día pasado se proyectaba a la
  semana siguiente. Ahora examen + pasado/verbo de resultado = nota; examen futuro =
  actividad; "ayer" es la fecha de ayer.

### Identidad Nexo
- School: personalidad "sena" → "nexo" (misma voz y avatar que Nexo en NexiService), Nexo
  por defecto, plantillas propias de School (no las comerciales), quien tenía "sena"
  guardado pasa a Nexo; el front muestra Nexo/Lyra como NexiService.

## 2. Evidencia de reservas (servicios locales reales, sin mocks)
`scripts/e2e_reservas_nexi.py` — 8/8: resumen pide confirmación; sin sesión pide login; tras
login se confirma con número real; aparece en Mis Reservas del usuario correcto; un segundo sí
no duplica; el mismo horario para otro usuario se detecta como ocupado; token inválido no
reserva; cancelación (endpoint del front) y lectura posterior. Navegador: flujo completo con
recarga, reserva #000004 visible el 19/11 a las 15:00.

## 3. Autorización dinámica verificada (usuarios reales, backend 8005)
| Usuario (rol) | Permisos | Centros | Nómina | Contratos | Sección nueva |
|---|---:|---|---|---|---|
| aprendiz.basico (APRENDIZ SENA) | 7 | 403 / negado | negado | negado | no la ve |
| aprendiz.plus (APRENDIZ SENA + directos) | 9 | lista 3 | negado | negado | navega |
| admin.centro (ADMIN CENTRO) | 8 | negado | negado | lista 24 | no la ve |
| admin (ADMINISTRADOR VT) | 72 | lista 3 | ✔ | ✔ | — |

## 4. Métricas
| Medida | Fase 3 | Fase 4 |
|---|---:|---:|
| pytest | 1085 ✔ / 30 ✗ base | 1154 ✔ (+69 nuevas) / mismos 30 ✗ |
| NexiService NLU dev / m1 / m2 / m3 / m4 | 95,5 / 95,2 / 96,2 / 92,7 / 74,0 | idénticos |
| Diálogos fase 3 (dev / indep.) | 18/18 · 11/12 | 18/18 · 11/12 |
| Diálogos fase 4 independiente | — | 5/12 1.ª corrida → 11/12 (expectativas mal escritas: tildes/mayúsculas y "no reservar" en turnos que pedían la cita) |
| Frases de refinamiento del enunciado | 2/9 | 9/9 (desarrollo) |
| School dev / heldout | 98,1 / 96,7* | 100 / 100 (5 casos corregidos mirándolos: ya no ciegos) |
| School fase 4 independiente | — | 24/25 (96 %), una corrida |
| Reservas reales e2e | — | 8/8 |
| Llamadas a GPT en todas las evaluaciones | 0 | 0 |
| Latencia NLU local School | — | p50 16–17 ms, p95 24–29 ms |
*heldout tras añadir las consultas nuevas, antes de corregir.

## 5. Errores que quedan
- f11: "a las 13" justo después de una lista entra al flujo de reserva preguntando el negocio.
- s02: "enséñame los centros" se toma como nombre de sección y navega.
- Domicilio, horario de atención y "comida de mar" como concepto no existen en los datos: se
  responde que no consta (correcto, pero no útil).
- Reservas: la disponibilidad previa se mira en la base local; para empresas del ERP la decide
  el ERP al crear (409 → se informa). Sin bloqueo entre el resumen y el sí (lo cubre el 409 o
  el re-chequeo al ejecutar).
- `get_reservas_perfil_tercero` y `store_agenda_servicio_nexiservice` no exigen sesión: el
  backend acepta cualquier correo. Lyra ya sólo usa el verificado; el endpoint sigue abierto.
- Cancelar desde «Mis Reservas» cancela el carrito entero (todas las citas pendientes del
  cliente en esa empresa).

## 6. Datos locales tocados
- Migraciones aditivas locales: pasarela `make_persona_identificacion_nullable`,
  `add_invitado_to_tipo_registro_enum`, `create_prestador_profiles_table` (respaldo previo de
  persona/usuario/tercero en `_respaldo_fase4_2026-11-17`).
- Usuarios sintéticos: NexiService cliente.uno/dos@lyra-prueba.local (registro real);
  School aprendiz.basico/plus, admin.centro + permiso LYRA_PRUEBA_NUEVA (fixture, `--borrar`).
- Reservas de prueba creadas y canceladas. La agenda 12 (prueba del administrador, a nombre
  de Sofía Restrepo por el error) sigue en la base local sin tocar.
