# Lyra, fase 3: estado de la conversación, acciones seguras, School real e imágenes

**Fecha:** 2026-11-17 · **Estado:** sin commit (Lyra, `postandserviceFront`, `schoolSenaFrontFinal`, `SchoolSenaBackFinal`).
Continúa `AUDITORIA-LYRA-FASE2-2026-11-17.md`. Respaldo previo de los archivos sin commit y de las bases `lyra_db` y
`virtualt_school-vt`: `C:\xampp\htdocs\_respaldo_fase3_2026-11-17\`.

## 1. El caso crítico: "Sí, por favor" agendaba en un restaurante

**Reproducido** por `ChatService` antes de tocar nada: después de "¿te busco centros médicos?", el "Sí, por favor"
respondía "Con gusto te ayudo a agendar tu cita en Fogón Criollo Oeste" y navegaba a `/empresa/5#servicios`.

**Causa (tres defectos que se sumaban):**
1. **Foco = reserva.** Cada recomendación o selección escribía su negocio en las ranuras de la reserva
   (`booking.business_id`), aunque nadie hubiera pedido agendar.
2. **La oferta no existía como dato.** "¿Te busco centros médicos?" era sólo texto: el estado no guardaba qué se había
   propuesto.
3. **Un "sí" se aplicaba a lo que hubiera en memoria.** La regla era "si hay ranuras de reserva, un sí continúa la
   reserva". Una ranura vieja ganaba a la propuesta reciente.

**Arquitectura nueva (`core/semantic/types.py`, `engine.py`, interceptor de NexiService):**
- **Turno numerado en el servidor.** `ChatService` asigna `turn` de forma atómica en `lyra_conversations.final_data`
  (`next_turn`) y lo devuelve como `turn_id`. Si un turno superado termina tarde, su estado no se guarda (escritura
  condicionada al turno).
- **Propuesta tipada.** `state.propose(intent, args, label, options)` registra lo que Lyra ofrece en su respuesta. La
  usan el síntoma (buscar atención), el negocio único ("¿ver servicios **o** agendar?", con dos opciones), la reserva
  en un negocio ("¿agendamos ahí?"), otra ciudad y el saludo con nombre.
- **Un "sí" sólo acepta la propuesta del turno inmediatamente anterior** (`live_offer`). La caducidad se comprueba al
  leer, además de al empezar el turno.
  - Sin propuesta vigente → aclaración breve. Nunca se adivina.
  - Con alternativas → "¿Qué prefieres: ver sus servicios o agendar una cita?".
  - "No", "mejor no", "no gracias" → `decline_offer`.
  - Aceptaciones reconocidas: sí, claro, dale, por favor, porfa, eso, listo, ok…
- **El foco ya no abre una reserva.** Sólo se llenan ranuras con el objetivo `booking` en curso.
- **Cambio de dominio = reserva estacionada.** Buscar o recomendar otra cosa mueve la reserva a `parked_booking`.
  "Retomemos la cita" la vuelve a poner en curso; nada más la reactiva.
- **Historial de listas.** Cada lista anterior queda en `history` con su rubro. "Volvamos a los restaurantes" o
  "regresa a lo anterior" la vuelven a mostrar (con el mapa) sin repetir ninguna acción.
- **Referencias.** "El segundo" se resuelve contra la lista actual. Fuera de rango → "Solo te mostré una opción".
  "Quiero reservar en el segundo" agenda sobre ese elemento.
- **Una hora con un negocio en foco y sin reserva** se propone ("¿Quieres que te agende en X para…?"); no se agenda.

**Frontend (Nexi y School, `LyraAssistant.tsx`):**
- Cada envío es un turno local con `AbortController`.
- Una respuesta de un turno superado no se aplica: ni texto, ni voz, ni navegación, ni mapa.
- Se descarta también si su `turn_id` del servidor es menor que el último aplicado.
- Los temporizadores diferidos del mapa y los focos por segmento de voz comprueban que el turno siga vigente.
- "Nueva conversación" y "cambiar asistente" invalidan lo que esté en camino.
- Una petición cancelada no marca a Lyra como caída.
- El saludo tardío ya no borra lo que el usuario escribió.

## 2. Recomendaciones y salud

- **"Recomiéndame clínica".** Un tipo de establecimiento más estrecho que la categoría ("clínica" dentro de "Salud y
  Bienestar") es un **requisito obligatorio**: se busca en el nombre o la categoría del negocio, nunca en sus servicios.
  - Respuesta real: "No tengo clínicas registradas en Popayán. En Cali sí hay: Clínica Vital Sur y Clínica Vital Este.
    Aquí, lo más parecido es Consultorio Médico Vida Sana (consultorios y centros médicos), aunque no figura como
    clínica."
  - Ya no sale la óptica.
- **Búsqueda de atención tras un síntoma** (`find_care`):
  - Sólo consultorios, centros médicos, clínicas, hospitales, IPS y urgencias.
  - Las ópticas o fisioterapias que registran "Consulta Médica General" aparecen como alternativa, y dichas así, sólo
    si no hay ningún centro médico.
  - No se ofrece agendar.
- **Alarma.** "Impresionante", "terrible", "de golpe", "pecho", "falta el aire"… anteponen urgencias o el **123**,
  antes que la lista. Sin diagnóstico.
- **Negocio inexistente.** "Barbería VIP": "No encontré **barbería vip** en Popayán. Tampoco tengo barberías
  registradas". En la base local no existe. Antes decía "registrado **vip**".
- **Saludo con nombre parecido.** "Hola vida" / "hola sana" saluda y **propone** el negocio que se parece
  ("Si buscabas Consultorio Médico Vida Sana, dime"). "Hola mi vida" es sólo un saludo.
- **Sólo el negocio que se nombra.** Se exige nombrar lo que **distingue** al negocio (dos palabras distintivas). "Busco
  un lugar para cortarme el pelo" ya no abre "Corte de Clase".

## 3. Calidad de las respuestas

Las causas estaban en la estructura, no en frases sueltas:
- **Los requisitos son conceptos, no secuencias de palabras.** "Que tenga pescado, me encanta el pescado" da
  `["pescado"]`. "Pescado y carne" da dos requisitos, que se dicen "**pescado** y **carne**".
- **Clases cerradas de palabras.**
  - Marcadores de discurso ("ahora", "mejor", "antes", "primero") y verbos de hallar ("encontrar") ya no son contenido.
  - Los gerundios tampoco: "Encontré 5 configurandos" ya no sale.
  - "Mi empresa" no abre el directorio; "cerca de mí" sí busca.
  - "En la plataforma" se reconoce como la propia plataforma.
- **Concordancia.**
  - El tipo se dice con la palabra del usuario y en singular o plural correctos ("clínicas registradas",
    "no figura como clínica").
  - "Te lo / te los marco".
  - "Volvemos a los restaurantes que te mostré".
  - El género con excepciones (`spa`, `programa`, `tema`…).
- **Marcadores técnicos.** `_strip_debug_markers` sólo quita los que emite el sistema (`[BIZ:…]`, `[CITY:…]`, las tres
  banderas). Antes borraba cualquier texto en mayúsculas entre corchetes, incluido el título real de una actividad.
- **Sin el antiguo "fallback" para "ahora busca otra cosa"**: ahora responde "Claro. ¿Qué quieres que busque?".

## 4. School contra el backend real

**El 8005 respondía** (404 en `/`, 401 sin token, 422 en login vacío). Lo que fallaba eran los arneses de prueba: no
cargaban el `.env`, así que `SCHOOLSENA_API_BASE` caía al 8000 (business). Se corrigió `scripts/replay_turns.py`.

**La base local:**
- `ficha` y `matriculaAcademica` estaban **vacías**: "No hay fichas activas" era verdad.
- Las 533 calificaciones son de una persona sin usuario.
- La conversación real (`d2205ce0…`) era de `admin@gmail.com`, rol ADMINISTRADOR VT.

**Datos sintéticos** (`scripts/fixtures/school_local_lyra.php`):
- Ficha `9900001` con un aprendiz y un admin de prueba, `@lyra-prueba.local`.
- Un examen (cuestionario) calificado ayer, un taller, una actividad pendiente y una vencida.
- Sólo se ejecuta contra 127.0.0.1, es idempotente y se borra con `--borrar`.
- La contraseña sale de `LYRA_TEST_PASSWORD` y no está en el repo.

**Backend (`LyraController`, compatible: sólo campos nuevos):**
- `misNotas`: `idActividad`, `tipoActividad`, `fechaFinal`, ordenadas. `pendientes`: `idActividad`, `tipoActividad`,
  ordenadas por fecha límite.
- `fichasActivas`: marca `activa` (apertura ACTIVA y clases no terminadas), con `fin_clases`.
- Nueva ruta `GET lyra/fichas/codigo/{codigo}/estudiantes`. La ruta por id se mantiene (`whereNumber`).
- **Seguridad que faltaba.** `estudiantes` y `entregas` exigen rol de gestión y la empresa del token.
  - Antes, cualquier sesión veía nombres y notas de otros aprendices de cualquier actividad.
  - Ficha de otra empresa: 404.
- `listaInstructores`: `indefinido` y `vigente`. La fecha 2070-12-31 era el relleno de los contratos a término
  indefinido.
- `contratosVencimiento`: sólo nombre, número, fecha y tipo. Antes devolvía el contrato entero, con cuenta bancaria.

**Lyra:**
- **Tipos de error.** Cada fallo dice qué pasó (`not_found`, `no_route`, `forbidden`, `auth`, `unreachable`,
  `timeout`, `server`). Un fallo de comunicación nunca sale como "no hay registros".
- **Rol.** Una cuenta que no es de aprendiz que pide "actividades pendientes" o "resultados de mi examen" recibe la
  explicación ("las consulta cada aprendiz con su cuenta…"), no "no hay registros".
- **Examen.** "Dame los resultados de mi examen" ahora es una consulta de notas; antes, "fuera de lo que manejo".
  Con `tipoActividad` se filtran los cuestionarios.
- **Pendientes.** Se separan "Por entregar" y "Con el plazo vencido".
- **Instructores.** 27 con contrato vigente. Se muestran 10 y "lista completa de instructores" da todos. Los 11
  contratos en estado activo con fecha vencida se cuentan aparte, para revisarlos.
- **Nómina.** 38 contratos sin salario: "no puedo calcular la nómina", no "$0".
- **Materias.** "592376 - 03 DESARROLLAR…" → "Desarrollar procesos lógicos…".
- **Ficha por código**, con respaldo por id si el servidor aún no tiene la ruta nueva.

**Validado de punta a punta.** Login real en el 8005 → tokens → `ChatService` → backend 8005, con los dos perfiles.

## 5. Imágenes

- **Detección.** `utils/Assets.ts` reconoce las URLs de bancos de fotos (picsum, unsplash, placehold, ui-avatars…).
  `fixImageUrl` las trata como "sin imagen", y `realImage()` filtra las que vienen crudas de la base.
- **Respaldos.**
  - Logos y personas: iniciales generadas en el navegador (`initialsAvatar`). Antes iban a ui-avatars.com, que
    recibía cada nombre (un tercero).
  - Portadas y servicios: un degradado neutro (`neutralImage`).
  - También sustituye los `/default-*.png`, que no existían en `public/`.
- **No se tocó la base:** las 97 URLs de prueba siguen ahí. Sólo cambia cómo se muestran.
- **Medido en la página de inicio:** 0 imágenes de bancos de fotos (antes, la mayoría) y 44 respaldos locales. Avisos
  de `src` vacío: de 104 a 4.

## 6. Pusher

- **Alcance.** El secret estaba en `core/pusher.py` desde el primer commit (`8732667`, 2026-05-15) y está en GitHub
  (`DavidLozadavt/virtualtIA`). Ningún otro repo local lo tiene. La key pública está además en `schoolSenaFrontFinal/.env`
  y en `nexiserviceAdminReact`.
- **Corrección.** Sin valores por defecto: sin variables de entorno, Pusher queda apagado y no falla (todos los
  llamadores ya contemplaban `None`). Variables documentadas en `.env.example`.
  - Comprobado: Lyra arranca con "[Pusher] Sin credenciales… apagados".
  - Ya no emite eventos a producción.
- **Otras credenciales.** Sólo `FREESWITCH_ESL_PASSWORD="ClueCon"`, el valor de fábrica de FreeSWITCH (no es un secreto
  propio, pero debe sobreescribirse en producción).
- **No se rotó nada** (ver §9).

## 7. Resultados

"Antes" = estado al terminar la fase 2.

| Conjunto | Uso | Fase 2 | **Fase 3** |
|---|---|---:|---:|
| NexiService muestra 1 (63) | no independiente | 88,9 % | **95,2 %** |
| NexiService muestra 2 (52) | desarrollo | 96,2 % | 96,2 % |
| NexiService muestra 3 (55) | se usó en esta fase (sus fallos) | 89,1 % | **92,7 %** |
| **NexiService muestra 4 (50), nueva** | **independiente: etiquetada antes, corrida al inicio y al final** | 72,0 % | **74,0 %** |
| NexiService dev (22) | desarrollo | 95,5 % | 95,5 % |
| School held-out (90) / dev (54) | | 97,8 % / 98,1 % | 97,8 % / 98,1 % |

**Conversaciones de varios turnos** (`scripts/eval_dialogo.py`, nuevo, mide por separado):

| Batería | Escenarios completos | Turnos | Entidades | Seguridad de acciones |
|---|---:|---:|---:|---:|
| `dialogos_fase3` (18), antes de cambiar código | 6/18 | 58,1 % | 47,1 % | 74,1 % |
| `dialogos_fase3` (18), al final (desarrollo) | 18/18 | 100 % | 100 % | 100 % |
| **`dialogos_fase3_independiente` (12)**, escrita al final | **11/12** | **96,2 %** | **5/5** | **19/19** |

- **Batería independiente.** Su primera corrida mostró 6 defectos de redacción y de interpretación ("porfa" no
  aceptaba, "pescado carne", "antes" como requisito, "listo" con saludo, "spas registradas"). Se corrigieron después,
  así que la cifra final ya no es ciega.
- **El único escenario que falla (i04)** es un error de la expectativa: no se había mostrado ninguna lista, y "no tengo
  una lista anterior" es lo correcto.
- **Muestra 4.** Ningún fallo nuevo. De 14 a 13; los 13 ya fallaban antes de la fase:
  - **Etiqueta equivalente (3).** Un solo resultado se muestra y se enfoca (`search_businesses` con `fly_to_business`)
    donde la etiqueta pide `navigate_to_company`. "Barbería VIP" no existe. Con la etiqueta corregida: 80 %.
  - **Erratas (3):** "ue puedo hacer", "que negocios tines", "Baberia vip".
  - **Intención mal (7):** "¿cómo me registro?", "busca en la web qué es IA" (abre el directorio), "ir", "casanare",
    "barberías cerca de mi ubicación" (sólo ubica), "estoy configurando mi empresa…" (reseñas), "algún servicio que me
    recomiendes".
- **Pruebas.** `pytest`: 1085 pasan (51 nuevas: `test_fase3_contexto.py`, `test_fase3_school.py`). Los 30 fallos son
  idénticos a la base (WhatsApp/IntelliTaxi).
- **Otros proyectos.** 143 frases reales de IntelliTaxi, RentUs y wilson: salida idéntica.
- **GPT.** 0 llamadas y 0 tokens en todas las pruebas. Ningún cambio de esta fase escala al modelo.

## 8. Pruebas reales en el navegador

Chrome con Playwright, `localhost:5173`, Lyra nueva en el 8098.

- **Caso crítico completo:** restaurantes, dolor de cabeza, "sí, por favor", "muéstrame el segundo", "volvamos a los
  restaurantes".
  - Atención médica con aviso de urgencia, "solo te mostré una opción" y los restaurantes otra vez en el mapa.
  - **Ninguna navegación a `/empresa/…`.**
- **Sin probar:**
  - Voz (TTS y STT) y conversaciones simultáneas en dos pestañas del navegador. En el servidor la escritura por turno
    sí está probada.
  - El front de School en el navegador.
  - El celular real.
- **Lyra se cerró** al final de la sesión porque el sistema se quedó sin memoria; hay que levantarla otra vez.

## 9. Pendientes

**Requieren tu intervención:**
1. **Rotar el secret de Pusher (app 2140848).** Está en el historial público de GitHub desde mayo.
   - En el panel de Pusher: *App Keys* → generar un secret nuevo y ponerlo en las variables de entorno de producción
     de Lyra y de cualquier backend que use esa app.
   - **Riesgo:** mientras no se actualice el entorno, Lyra en producción deja de emitir eventos (el chat sigue
     funcionando).
   - Para local, crear una app de desarrollo aparte.
   - Sacarlo del historial (`git filter-repo`) exige reescribir `main`: decisión tuya, y no sirve sin rotar.
2. **¿`PUSHER_*` está definido en producción?** Con este cambio, si producción dependía del valor por defecto del
   código, Lyra deja de emitir eventos allí hasta que se definan las variables.
3. **Desplegar el cambio de School** (`LyraController`, ruta nueva). Mientras tanto, Lyra usa el respaldo por id.
4. **11 contratos en estado activo con fecha vencida** en School: dato de negocio para revisar.

**Sin corregir, con causa:**
- **"Talleres de mecánica" → "Taller de Arte".**
  - "Taller" se ancla como categoría y el tipo exigido coincide con el nombre "Taller de Arte".
  - En los datos sintéticos todos los negocios registran todos los servicios, así que la evidencia "mecánica" se cumple
    en cualquiera.
  - Hace falta resolver los sustantivos genéricos ("taller", "centro", "lugar") contra la categoría real antes de
    exigirlos.
- **Erratas** de la muestra 4 e **intenciones** listadas en §7.
- **Precio**, como criterio de orden, sigue sin implementar.
- **Detección de salud:** sigue siendo una lista cerrada.
