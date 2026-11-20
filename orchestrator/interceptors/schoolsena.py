import re
import logging
from typing import Optional, Dict, Any
from tools.schoolsena import TOOLS_REGISTRY

logger = logging.getLogger("lyra.interceptors.schoolsena")


# ── Helpers de Rol ────────────────────────────────────────────────────────────

#: Mismas palabras con que el backend de SchoolSena autoriza la coordinación
#: (`KeyUtil::hasRole`, coincidencia por subcadena sin mayúsculas). Si Lyra
#: ofrece algo que el backend niega, el usuario recibe un 403 por algo que el
#: asistente le acababa de sugerir.
_BACKEND_COORD_ROLES = ("admin", "coordinador", "regional", "centro", "vt")


def _get_roles(user_role: str) -> tuple[bool, bool, bool]:
    r = (user_role or "").lower()
    is_coordinator = any(x in r for x in _BACKEND_COORD_ROLES)
    is_instructor  = any(x in r for x in ["instructor", "docente", "docenteup"])
    is_student     = any(x in r for x in ["estudiante", "alumno", "aprendiz"])
    return is_coordinator, is_instructor, is_student


_STUDENT_TOOLS = {"get_clases_hoy", "get_horario", "get_actividades_pendientes", "get_notas"}
_INSTRUCTOR_TOOLS = _STUDENT_TOOLS | {"get_entregas", "get_estudiantes_ficha", "get_fichas_activas"}
_COORDINATOR_TOOLS = _INSTRUCTOR_TOOLS | {
    "get_resumen_admin", "get_nomina_resumen", "get_lista_instructores", "get_contratos_vencimiento",
}


def allowed_tools(user_role: str, caps: Optional[dict] = None) -> set:
    """
    Herramientas de datos que se le ofrecen al modelo.

    Con las capacidades del backend (permisos efectivos) se ofrecen exactamente
    las consultas habilitadas; el rol sólo queda como respaldo si no se pudieron
    leer. En cualquier caso el backend vuelve a autorizar cada consulta.
    """
    from orchestrator.interceptors.schoolsena_caps import allowed_tools as _by_caps
    por_permiso = _by_caps(caps)
    if por_permiso is not None:
        return por_permiso
    is_coordinator, is_instructor, is_student = _get_roles(user_role or "")
    if is_coordinator:
        return set(_COORDINATOR_TOOLS)
    if is_instructor:
        return set(_INSTRUCTOR_TOOLS)
    if is_student:
        return set(_STUDENT_TOOLS)
    return set()


def _has(text: str, keywords) -> bool:
    """
    Alguna palabra clave aparece al INICIO de una palabra del texto.

    La búsqueda por subcadena hacía que "información" contuviera "formación" y
    se respondiera con el mensaje de regionales a quien preguntaba por un curso.
    """
    return any(re.search(r"(?<![a-z0-9áéíóúñü])" + re.escape(kw), text) for kw in keywords)


def _denied(msg: str = None) -> Dict[str, Any]:
    return {"reply": msg or "🔒 No tienes permiso para consultar esta información.", "final_data": {"access_denied": True}}


# ── Opciones rápidas por rol ──────────────────────────────────────────────────

def _schedule_quick_replies(is_coordinator: bool, is_instructor: bool, is_student: bool) -> list:
    if is_student:
        return [
            {"title": "🌞 Mis clases de hoy",       "text": "clases de hoy"},
            {"title": "📅 Mi horario esta semana",   "text": "mi horario de la semana"},
            {"title": "🗓️ Todo el mes",              "text": "horario del mes"},
        ]
    if is_instructor:
        return [
            {"title": "🌞 Mis fichas de hoy",        "text": "fichas de hoy"},
            {"title": "📅 Mis fichas esta semana",   "text": "fichas de la semana"},
            {"title": "📋 Todas mis fichas activas", "text": "fichas activas"},
        ]
    if is_coordinator:
        return [
            {"title": "📋 Fichas activas del centro", "text": "fichas activas"},
            {"title": "👨‍🏫 Lista de instructores",   "text": "lista instructores"},
            {"title": "📊 Dashboard general",         "text": "dashboard"},
        ]
    return [
        {"title": "🌞 Clases de hoy",  "text": "clases de hoy"},
        {"title": "📅 Esta semana",    "text": "mi horario de la semana"},
        {"title": "🗓️ Del mes",        "text": "horario del mes"},
    ]


def _schedule_prompt(is_coordinator: bool, is_instructor: bool, is_student: bool) -> str:
    if is_student:
        return (
            "📅 ¿Qué parte de tu horario académico quieres ver?\n\n"
            "Puedo mostrarte tus clases de **hoy**, el calendario de **esta semana** "
            "o el cronograma completo del **mes** con todas tus materias."
        )
    if is_instructor:
        return (
            "📅 ¿Qué deseas consultar sobre tu carga académica?\n\n"
            "Puedo mostrarte las **fichas que tienes programadas hoy**, "
            "tu agenda de **la semana** o el listado de todas tus **fichas activas** en el centro."
        )
    if is_coordinator:
        return (
            "📅 ¿Qué información del centro necesitas?\n\n"
            "Puedo mostrarte las **fichas activas**, la **lista de instructores** "
            "o el **dashboard** con el estado general del centro."
        )
    return (
        "📅 ¿Sobre qué horario tienes dudas?\n\n"
        "Dime si quieres ver **tus clases de hoy**, el horario de **esta semana** "
        "o el cronograma del **mes**."
    )


# ── Formateador de fichas asignadas (instructor) ──────────────────────────────

async def _run_fichas_asignadas(context: dict) -> Optional[Dict[str, Any]]:
    """
    Obtiene el horario del instructor y deduplica por código de ficha,
    mostrando cada ficha una sola vez con su estado y días en los que aparece.
    """
    tool = TOOLS_REGISTRY.get("get_horario")
    if not tool:
        return None

    try:
        result = await tool["handler"]({}, context)
    except Exception as e:
        logger.error(f"Error en tool 'get_horario' (fichas asignadas): {e}", exc_info=True)
        return {"reply": f"⚠️ Error técnico al consultar las fichas: {e}", "final_data": {"error": str(e)}}

    if result.get("error"):
        err = result["error"]
        return {"reply": f"⚠️ No pude obtener tus fichas: {err}", "final_data": {"error": err}}

    data = result.get("result")
    if not data:
        return {"reply": "No tienes fichas asignadas en este momento.", "final_data": {}}

    # Deduplicar por código de ficha
    fichas: Dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue

        ficha_obj = item.get("ficha")
        codigo = None
        if isinstance(ficha_obj, dict):
            codigo = ficha_obj.get("codigo")
        if not codigo:
            codigo = item.get("codigo")
        if not codigo:
            continue

        codigo = str(codigo)
        estado = item.get("estado") or ""

        dia_obj = item.get("dia")
        dia_name = ""
        if isinstance(dia_obj, dict):
            dia_name = (dia_obj.get("dia") or "").capitalize()
        elif item.get("dia_nombre"):
            dia_name = str(item["dia_nombre"]).capitalize()

        if codigo not in fichas:
            # La API de estudiantes pide el id de la ficha, no su código.
            db_id = ficha_obj.get("id") if isinstance(ficha_obj, dict) else item.get("idFicha")
            fichas[codigo] = {"estado": estado, "dias": [], "id": db_id}

        if dia_name and dia_name not in fichas[codigo]["dias"]:
            fichas[codigo]["dias"].append(dia_name)

    if not fichas:
        return {"reply": "No tienes fichas asignadas en este momento.", "final_data": {}}

    lines = []
    entities = []
    for codigo, info in fichas.items():
        estado_str = f" · {info['estado']}" if info["estado"] else ""
        dias_str   = f" · Días: {', '.join(info['dias'])}" if info["dias"] else ""
        lines.append(f"- 📋 **Ficha {codigo}**{estado_str}{dias_str}")
        entities.append({
            "id":          codigo,
            "name":        f"Ficha {codigo}",
            "category":    info["estado"] or "SENA",
            "logo":        None,
            "entity_type": "ficha",
        })

    count = len(lines)
    count_note = f"(mostrando {count} ficha{'s' if count != 1 else ''})\n" if count > 5 else ""
    reply = f"📋 **Tus fichas asignadas:**\n{count_note}" + "\n".join(lines)

    return {
        "reply": reply,
        "final_data": {"reply": reply},
        # Para "los estudiantes del primero": sólo fichas con id real.
        "_presented": [{"type": "ficha", "id": info["id"], "label": f"Ficha {codigo}"}
                       for codigo, info in fichas.items() if info.get("id") is not None],
    }


# ── Ejecutor + formateador ────────────────────────────────────────────────────

async def _run(tool_name: str, params: dict, context: dict,
               prefix: str, empty_msg: str = None) -> Optional[Dict[str, Any]]:
    tool = TOOLS_REGISTRY.get(tool_name)
    if not tool:
        return None

    try:
        result = await tool["handler"](params, context)
    except Exception as e:
        logger.error(f"Error en tool '{tool_name}': {e}", exc_info=True)
        return {"reply": f"⚠️ Error técnico al consultar '{tool_name}': {e}", "final_data": {"error": str(e)}}

    if result.get("error"):
        err = result["error"]
        kind = result.get("error_kind") or "server"
        if kind == "not_found":
            reply = f"ℹ️ {err[0].upper() + err[1:]}. Revisa el número; si crees que es un error, avisa a la coordinación."
        elif kind in ("unreachable", "timeout"):
            # Un fallo de comunicación nunca se presenta como "no hay registros".
            reply = (f"⚠️ No pude consultar SchoolSena: {err}. No es que no haya datos; "
                     "inténtalo de nuevo en un momento.")
        elif kind == "no_route":
            reply = f"⚠️ {err[0].upper() + err[1:]}: el servidor no tiene esa consulta habilitada todavía."
        else:
            reply = f"⚠️ No pude completar la consulta: {err}"
        return {"reply": reply, "final_data": {"error": err, "error_kind": kind}}

    data  = result.get("result")

    # Presentador propio de la herramienta, escrito contra su respuesta real.
    from orchestrator.interceptors.schoolsena_present import PRESENTERS
    presenter = PRESENTERS.get(tool_name)
    if presenter:
        text = presenter(data, context)
        if text:
            return {"reply": text, "final_data": {"reply": text}}
    props = []

    if not data:
        return {"reply": empty_msg or f"{prefix}No hay datos disponibles.", "final_data": {}}

    if isinstance(data, list):
        if not data:
            return {"reply": empty_msg or f"{prefix}Sin registros por ahora.", "final_data": {}}

        from datetime import datetime, timedelta
        now = datetime.now()

        timeframe = context.get("timeframe", "week")
        user_text = (context.get("user_text") or "").lower()
        if not context.get("timeframe"):
            if "hoy" in user_text or "ahora" in user_text: timeframe = "today"
            elif "mes" in user_text:                        timeframe = "month"

        days_es    = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]
        start_week = now - timedelta(days=now.weekday())
        week_dates = {d: (start_week + timedelta(days=i)).strftime("%d/%m") for i, d in enumerate(days_es)}
        today_name = days_es[now.weekday()]

        lines, entities, seen_slots = [], [], set()
        is_schedule = tool_name in ("get_horario", "get_clases_hoy")

        for item in data:
            if not isinstance(item, dict):
                lines.append(f"- {item}")
                continue

            # ── Nombre principal ──────────────────────────────────────────────
            name = (
                item.get("nombre") or item.get("nombreMateria") or
                item.get("tituloActividad") or item.get("materia_nombre") or
                item.get("materiaNombre")
            )
            gm = item.get("gradoMateria") or item.get("grado_materia")
            if not name and isinstance(gm, dict):
                mat = gm.get("materia", {})
                if isinstance(mat, dict):
                    name = (mat.get("nombreMateria") or mat.get("nombre") or
                            mat.get("materia_nombre") or mat.get("nombre_materia"))

            # ── Día ───────────────────────────────────────────────────────────
            dia_obj  = item.get("dia")
            dia_name = ""
            if isinstance(dia_obj, dict):   dia_name = (dia_obj.get("dia") or "").upper()
            elif item.get("dia_nombre"):    dia_name = str(item["dia_nombre"]).upper()

            # Un día concreto (hoy, mañana, "el viernes") muestra sólo ese día;
            # antes sólo se filtraba "hoy" y "mañana" devolvía la semana entera.
            filter_day = (today_name if timeframe == "today"
                          else context.get("target_day_override") if timeframe in ("tomorrow", "day") else None)
            if is_schedule and filter_day and dia_name and dia_name != filter_day:
                continue

            # ── Hora ──────────────────────────────────────────────────────────
            h_ini    = (item.get("horaInicial") or item.get("hora_inicial") or "")[:5]
            h_fin    = (item.get("horaFinal")   or item.get("hora_final")   or "")[:5]
            time_str = f"{h_ini}–{h_fin}" if h_ini else ""

            if is_schedule:
                slot = (dia_name, time_str)
                if slot in seen_slots: continue
                seen_slots.add(slot)

            # ── Ficha ─────────────────────────────────────────────────────────
            ficha_obj = item.get("ficha")
            ficha_str = ""
            if isinstance(ficha_obj, dict) and ficha_obj.get("codigo"): ficha_str = f"Ficha {ficha_obj['codigo']}"
            elif item.get("codigo"):                                     ficha_str = f"Ficha {item['codigo']}"

            estado_str = f" · {item['estado']}" if item.get("estado") else ""

            vence     = item.get("fecha_fin") or item.get("fechaFin")
            vence_str = f" · Vence: {vence}" if vence else ""

            # ── Construir línea ───────────────────────────────────────────────
            if is_schedule:
                date_label = f" ({week_dates[dia_name]})" if dia_name in week_dates and timeframe in ("today", "week", "tomorrow", "day") else ""
                day_part   = f"{dia_name.capitalize()}{date_label}" if dia_name else ""
                parts      = [p for p in [day_part, time_str, ficha_str] if p]
                line       = "- **" + " | ".join(parts[:2]) + "**" + (f" · {parts[2]}" if len(parts) > 2 else "") + estado_str
            else:
                name_str  = name.strip() if isinstance(name, str) else name
                bold_name = f"**{name_str}**" if name_str else ""
                parts     = [p for p in [bold_name, ficha_str, time_str] if p]
                line      = "- " + "  ".join(parts) + vence_str + estado_str

            lines.append(line)

            if not is_schedule and tool_name not in ["get_fichas_activas", "get_lista_instructores"]:
                entities.append({
                    "id":          item.get("id"),
                    "name":        name or ficha_str or "SENA",
                    "category":    item.get("tipo") or item.get("codigo") or "SENA",
                    "logo":        item.get("logo") or item.get("imagen"),
                    "entity_type": _entity_type(tool_name),
                })

        if not lines:
            return {"reply": empty_msg or f"{prefix}Sin registros para este periodo.", "final_data": {}}

        count_note = f"(mostrando {len(lines)} registro{'s' if len(lines) != 1 else ''})\n" if len(lines) > 5 else ""
        reply = prefix + count_note + "\n".join(lines)
        if entities:
            props.append({"businesses": entities})

    elif isinstance(data, dict):
        lines = []
        for k, v in data.items():
            label = k.replace("_", " ").capitalize()
            if any(x in k.lower() for x in ["nomina", "valor", "total", "salario", "sueldo"]):
                # Mismo formateo que en NexiService: es el que la capa de voz
                # sabe leer como un precio y no como una fila de dígitos.
                from core.speech_format import format_price

                v = format_price(v, fallback=str(v))
            lines.append(f"- **{label}**: {v}")
        reply = prefix + "\n".join(lines)

    else:
        reply = f"{prefix}{data}"

    return {"reply": reply, "final_data": {"reply": reply}, "properties": props}


def _entity_type(tool_name: str) -> str:
    return {
        "get_fichas_activas":         "ficha",
        "get_actividades_pendientes": "actividad",
        "get_clases_hoy":             "clase",
        "get_horario":                "clase",
        "get_lista_instructores":     "persona",
        "get_estudiantes_ficha":      "persona",
    }.get(tool_name, "elemento")


# ── Interceptor principal ─────────────────────────────────────────────────────

# ── Textos por rol ────────────────────────────────────────────────────────────

def _first_name(user_data: dict) -> str:
    name = (user_data.get("name") or user_data.get("nombre") or "").strip()
    return name.split()[0].capitalize() if name and name.lower() != "usuario" else ""


def _offer(is_coordinator: bool, is_instructor: bool, is_student: bool) -> tuple:
    """Qué puede pedir este rol: frase corta y respuestas rápidas."""
    if is_coordinator:
        return ("las fichas activas, la lista de instructores, los contratos por vencer, la nómina o el resumen del centro",
                [{"title": "Fichas activas", "text": "fichas activas"},
                 {"title": "Contratos por vencer", "text": "contratos por vencer"},
                 {"title": "Resumen del centro", "text": "resumen del centro"}])
    if is_instructor:
        return ("tus fichas de hoy o de la semana, los estudiantes de una ficha o quién entregó una actividad",
                [{"title": "Fichas de hoy", "text": "fichas de hoy"},
                 {"title": "Mis fichas", "text": "mis fichas"},
                 {"title": "Mi semana", "text": "mi horario de la semana"}])
    if is_student:
        return ("tus clases de hoy o de la semana, tus actividades pendientes o tus notas",
                [{"title": "Clases de hoy", "text": "clases de hoy"},
                 {"title": "Actividades pendientes", "text": "actividades pendientes"},
                 {"title": "Mis notas", "text": "mis notas"}])
    return ("tus clases, tus actividades y tus notas, una vez inicies sesión", [])


def _school_reply(reply: str, outcome: str, state: dict, quick_replies: Optional[list] = None,
                  clarify: bool = False, **extra) -> Dict[str, Any]:
    fd = {"school_outcome": outcome, "_school": state, **extra}
    if quick_replies:
        fd["quick_replies"] = quick_replies
    if clarify:
        fd["needs_clarification"] = True
    return {"reply": reply, "final_data": fd}


#: Lo que cada capacidad exige del rol. None = cualquier sesión válida.
_CAP_ROLES = {
    "schedule": None, "activities": None, "grades": None,
    "students": "instructor", "deliveries": "instructor", "fichas": "instructor",
    "payroll": "coordinator", "instructors": "coordinator", "contracts": "coordinator", "dashboard": "coordinator",
}
_DENIED_TEXT = {
    "payroll": "Tu usuario no tiene permiso para consultar la nómina.",
    "instructors": "Tu usuario no tiene permiso para ver la lista de instructores.",
    "contracts": "Tu usuario no tiene permiso para consultar contratos.",
    "dashboard": "Tu usuario no tiene permiso para ver el resumen del centro.",
    "students": "Tu usuario no tiene permiso para ver los aprendices de una ficha.",
    "deliveries": "Tu usuario no tiene permiso para ver las entregas de otros aprendices.",
    "fichas": "Tu usuario no tiene permiso para gestionar fichas.",
    "centers": "Tu usuario no tiene permiso para consultar los centros de formación.",
    "sites": "Tu usuario no tiene permiso para consultar las sedes.",
    "rooms": "Tu usuario no tiene permiso para consultar los ambientes.",
    "periods": "Tu usuario no tiene permiso para consultar los periodos.",
    "shifts": "Tu usuario no tiene permiso para consultar las jornadas.",
    "planning": "Tu usuario no tiene permiso para consultar la planeación.",
}


def _after_nav(state: dict, nav_fd: dict) -> dict:
    """Tras navegar, el módulo de destino pasa a ser el contexto: "¿cuántos hay?" habla de él."""
    destino = (nav_fd.get("nav_target") or {}).get("path")
    if destino:
        from orchestrator.interceptors.schoolsena_modules import modules
        m = next((x for x in modules() if x["ruta"] == destino), None)
        state = {**state, "module": destino, "query": (m or {}).get("consulta"), "cap": None}
    opciones = nav_fd.get("nav_options") or []
    # "Encontré varias secciones parecidas, ¿a cuál?" también es una propuesta:
    # "la segunda" o el nombre de una de ellas la responde en el turno siguiente.
    state = {**state, "oferta": {"tipo": "elegir", "opciones": [
        {"texto": f"llévame a {o.get('title')}", "ruta": o.get("path"), "nombre": (o.get("title") or "").split("›")[-1].strip()}
        for o in opciones]} if opciones else None}
    return state


def _sellar(resultado: Optional[Dict[str, Any]], final_data: dict) -> Optional[Dict[str, Any]]:
    """Ata la propuesta de esta respuesta al turno en que se hizo (vale sólo en el siguiente)."""
    import time as _t
    st = ((resultado or {}).get("final_data") or {}).get("_school")
    if isinstance(st, dict) and isinstance(st.get("oferta"), dict):
        st["oferta"] = {**st["oferta"], "turn": int(final_data.get("_turn") or 0), "ts": _t.time()}
    return resultado


def _roles_from_permissions(permisos: list, fallback: tuple) -> tuple:
    """
    (gestiona, instruye, estudia) a partir de PERMISOS, no del nombre del rol.

    Sólo da forma a la conversación —"tus clases" o "tus fichas", qué ofrecer
    primero—; lo que el usuario puede consultar lo deciden las capacidades.
    """
    ps = set(permisos or [])
    if not ps:
        return fallback
    gestiona = bool(ps & {"GESTION_ACADEMICA", "ADMINISTRATIVO_SENA", "GESTION_REGIONAL", "GESTION_CENTROS_FORMACION",
                          "GESTION_NOMINA", "GESTION_CONTRATOS"})
    instruye = any(p.startswith("AULA_VIRTUAL_INSTRUCTOR") for p in ps)
    estudia = any(p.startswith("AULA_VIRTUAL_APRENDIZ") for p in ps)
    return gestiona, instruye, estudia


def _role_allows(cap: str, is_coordinator: bool, is_instructor: bool, is_student: bool,
                 logged_in: bool = True) -> bool:
    need = _CAP_ROLES.get(cap)
    if need is None:
        return logged_in
    if need == "instructor":
        return is_instructor or is_coordinator
    return is_coordinator


async def _execute(reading, context: dict, roles: tuple) -> Optional[Dict[str, Any]]:
    """Ejecuta la capacidad leída con la herramienta real y redacta la respuesta."""
    from orchestrator.interceptors import schoolsena_nlu as nlu

    is_coordinator, is_instructor, is_student = roles
    cap = reading.capability
    tf = reading.timeframe

    listas = {
        "centers": "get_centros_formacion", "sites": "get_sedes", "rooms": "get_ambientes",
        "periods": "get_periodos", "shifts": "get_jornadas", "planning": "get_planeacion",
    }
    if cap in listas:
        # Consultar no es navegar: se trae la información y se presenta según
        # lo pedido — lista, cuántos hay o leída de corrido para la voz.
        context["school_act"] = reading.act
        return await _run(listas[cap], {}, context, "", "No hay registros.")

    if cap == "schedule":
        day = nlu.target_day(reading)
        context["timeframe"] = tf
        if day:
            context["target_day_override"] = day
        who = "fichas" if is_instructor and not is_student else "clases"
        if tf == "today":
            return await _run("get_clases_hoy", {}, context, f"🌞 **Tus {who} de hoy:**\n",
                              f"Hoy no tienes {who} programadas.")
        if tf in ("tomorrow", "day"):
            label = nlu.day_label(day, on_date=reading.on_date)
            title = f"mañana ({label})" if tf == "tomorrow" else label.lower()
            when = "mañana" if tf == "tomorrow" else f"el {label.lower()}"
            return await _run("get_horario", {}, context, f"📅 **Tus {who} para {title}:**\n",
                              f"No tienes {who} programadas {when}.")
        if tf == "month":
            return await _run("get_horario", {}, context, f"🗓️ **Tus {who} del mes:**\n",
                              "No encontré horario asignado para este mes.")
        return await _run("get_horario", {}, context, "📅 **Tu horario de la semana:**\n",
                          "No encontré horario asignado para esta semana.")

    if cap == "activities":
        if not is_student:
            # Las actividades pendientes son las de la matrícula del propio
            # usuario. Para una cuenta de coordinación o de instrucción la
            # consulta siempre vuelve vacía, y decir "no hay actividades
            # pendientes registradas" daba a entender que en el centro no hay.
            return {"reply": ("Las actividades pendientes son las que tiene asignadas un aprendiz en su matrícula, "
                              "y tu cuenta no es de aprendiz. Para ver cómo va una actividad puedo mostrarte sus "
                              "entregas: dime, por ejemplo, «entregas actividad 12»."),
                    "final_data": {"school_outcome": "role_mismatch"}}
        return await _run("get_actividades_pendientes", {}, context, "📝 **Actividades pendientes:**\n",
                          "✅ Estás al día: no tienes actividades pendientes.")

    if cap == "grades" and not is_student:
        return {"reply": ("Las calificaciones las consulta cada aprendiz con su propia cuenta, y tu cuenta no es de "
                          "aprendiz. Desde aquí no puedo ver las notas de otra persona; lo que sí puedo mostrarte "
                          "son las entregas de una actividad («entregas actividad 12»)."),
                "final_data": {"school_outcome": "role_mismatch"}}

    if cap == "grades":
        # "¿Cómo me fue en el examen de ayer?": se filtra por la fecha de
        # calificación (es la única fecha que da el backend) y por las palabras
        # que nombran la actividad o la materia.
        context["grades_filter"] = {"date": reading.on_date, "words": reading.filter_words,
                                    "mentions_exam": reading.mentions_exam}
        return await _run("get_notas", {}, context, "📊 **Tus calificaciones:**\n",
                          "Todavía no tienes calificaciones registradas.")

    if cap == "fichas":
        if reading.scope == "mine" and is_instructor:
            return await _run_fichas_asignadas(context)
        return await _run("get_fichas_activas", {}, context, "📋 **Fichas activas del centro:**\n",
                          "Tu centro no tiene fichas registradas en el sistema.")

    if cap == "students":
        f = reading.ficha
        return await _run("get_estudiantes_ficha", {"ficha_id": f}, context,
                          f"👥 **Estudiantes de la ficha {f}:**\n", f"La ficha {f} no tiene estudiantes registrados.")

    if cap == "deliveries":
        a = reading.actividad
        return await _run("get_entregas", {"actividad_id": a}, context,
                          f"📥 **Entregas de la actividad {a}:**\n", f"Nadie ha entregado todavía la actividad {a}.")

    if cap == "instructors":
        context["full_list"] = bool(re.search(r"\b(complet[ao]s?|tod[oa]s|entera|todo)\b", context.get("user_text") or ""))

    simple = {
        "payroll": ("get_nomina_resumen", "💰 **Resumen de nómina mensual:**\n", "No hay datos de nómina disponibles."),
        "instructors": ("get_lista_instructores", "👨‍🏫 **Instructores activos:**\n", "No encontré instructores activos."),
        "contracts": ("get_contratos_vencimiento", "⚠️ **Contratos que vencen en los próximos 30 días:**\n",
                      "✅ Ningún contrato vence en los próximos 30 días."),
        "dashboard": ("get_resumen_admin", "📊 **Estado general del centro:**\n", "No pude obtener el estado del centro."),
    }
    tool, prefix, empty = simple[cap]
    return await _run(tool, {}, context, prefix, empty)


def _presented_from(result: Optional[Dict[str, Any]], cap: str) -> Optional[list]:
    """Lo que se le mostró al usuario, en orden, para resolver 'el primero'."""
    if not result:
        return None
    items = []
    blocks = result.get("properties") or (result.get("final_data") or {}).get("properties") or []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for e in block.get("businesses", []):
            kind = {"ficha": "ficha", "actividad": "actividad"}.get(e.get("entity_type"))
            if kind and e.get("id") is not None:
                items.append({"type": kind, "id": e["id"], "label": e.get("name")})
    return items + list(result.pop("_presented", None) or [])


#: Proyecto al que pertenece todo lo que produce este módulo (ver project_scope).
PROJECT = "schoolsena"


async def pre_llm_interceptor(
    project_id: str,
    intent_name: str,
    args: Dict[str, Any],
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    if project_id != "schoolsena":
        return None

    from orchestrator.interceptors import schoolsena_nlu as nlu
    from orchestrator.navigation import is_navigation_request, navigation_reply

    user_text = (context.get("user_text") or "").strip()
    user_data = context.get("user_data") or {}
    user_role = (user_data.get("role") or "").lower()
    final_data = context.get("final_data") or {}
    state = final_data.get("_school") or {}
    roles = _get_roles(user_role)
    # Un rol que no es de las tres familias (p. ej. "SECRETARIA CIADET") sigue
    # siendo una sesión: puede pedir lo propio y el backend decide qué datos da.
    logged_in = any(roles) or (bool(user_role) and user_role != "invitado")
    # Lo que este usuario puede hacer, según sus permisos efectivos (backend).
    from orchestrator.interceptors import schoolsena_caps as school_caps
    caps = await school_caps.load(context, final_data) if logged_in else None
    if caps:
        roles = _roles_from_permissions(caps.get("permisos") or [], roles)
    is_coordinator, is_instructor, is_student = roles
    name = _first_name(user_data)
    offer, quick = school_caps.offer(caps) or _offer(*roles)
    catalog = context.get("nav_catalog") or [
        {"title": f"{sec['grupo']} › {sec['nombre']}" if sec.get("grupo") else sec["nombre"], "path": sec["ruta"]}
        for sec in (caps or {}).get("secciones") or []]
    folded = nlu.normalize(user_text)

    # Un síntoma contado se atiende con prudencia, igual que en NexiService,
    # pero sin ofrecer búsqueda de consultorios: SchoolSena no tiene esa
    # capacidad. Lo que la conversación tenía en curso no se toca.
    from core.semantic.health import health_concern, health_reply
    _level = health_concern(user_text)
    reading = nlu.read(user_text, state, roles)
    # "Estoy enfermo, ¿tengo clase hoy?" pide su horario: se responde, con una
    # línea de cuidado delante. Sin consulta, sólo la orientación prudente.
    if _level and reading.kind != "capability":
        return _school_reply(
            health_reply(_level, can_search_care=False)
            + " Si te impide asistir a clase, avísale a tu instructor.", "out_of_scope", state)
    if _level:
        context["_care_prefix"] = "Lamento que te sientas mal; si no puedes asistir, avísale a tu instructor.\n\n"
    logger.info("SchoolSena NLU | text=%r | role=%r | reading=%s", user_text, user_role, reading)

    # ── Módulos de School: permisos, consulta, conteo, filtros, contexto, crear ──
    # Antes del menú: "muéstrame las regionales" pide datos, "¿cuántas hay?"
    # habla del módulo activo y "¿qué permisos tengo?" de la cuenta. La navegación
    # pura sigue más abajo para lo que no sea de un módulo.
    if logged_in:
        from orchestrator.interceptors import schoolsena_modules as school_mod
        hecho = school_mod.edit_followup(user_text, state) or await school_mod.handle(
            user_text, reading, state, caps, context)
        if hecho:
            return _sellar(hecho, final_data)

    # ── Navegación a una sección real del menú del usuario ────────────────────
    if is_navigation_request(user_text):
        nav = navigation_reply(user_text, catalog, context.get("current_path"))
        nav_fd = nav.get("final_data") or {}
        # "Llévame a mis actividades" sin esa sección en el menú: si es algo que
        # Lyra puede mostrar aquí mismo, se muestra en vez de decir que no existe.
        if nav_fd.get("nav_not_found") and reading.kind == "capability" and logged_in \
                and school_caps.allows(caps, reading.capability) is not False:
            result = await _execute(reading, context, roles)
            if result:
                result["reply"] = "No tienes esa sección en el menú, pero aquí lo tienes:\n\n" + result["reply"]
                result.setdefault("final_data", {})["_school"] = nlu.remember(
                    state, reading, _presented_from(result, reading.capability))
                return result
        nav_fd["_school"] = _after_nav(nlu.remember(state, nlu.Reading(kind="nav")), nav_fd)
        nav["final_data"] = nav_fd
        return _sellar(nav, final_data)

    # Nombre de una sección del menú sin verbo de navegación ("sedes",
    # "dónde gestiono los usuarios"): si el mensaje no es otra cosa, se abre.
    # Lo mismo con el nombre pelado de una sección que también se puede consultar
    # ("sedes"): sin pregunta ni verbo es la sección; "¿qué sedes hay?" o
    # "léeme las sedes" piden los datos.
    _bare_section = (reading.kind == "capability" and reading.capability in nlu.LISTABLE
                     and reading.act == "list" and len(folded.split()) <= 3
                     and not re.search(r"\b(que|cuales|cuantos|cuantas|dame|muestrame|lista|listado|ver|hay|tengo|"
                                       r"dime|mis|son|existen|tenemos)\b", folded))
    if (reading.kind in ("none", "clarify", "out_of_scope") or _bare_section) and catalog:
        from orchestrator.navigation import match_section
        m = match_section(f"llevame a {user_text}", catalog)
        # Todo el mensaje tiene que ser el nombre de la sección: una palabra
        # suelta en común ("pendientes", "formación") no basta.
        if m["status"] in ("match", "ambiguous") and m.get("coverage", 0) >= 1.0:
            nav = navigation_reply(f"llevame a {user_text}", catalog, context.get("current_path"))
            nav.setdefault("final_data", {})["_school"] = _after_nav(
                nlu.remember(state, nlu.Reading(kind="nav")), nav["final_data"])
            return _sellar(nav, final_data)

    kind = reading.kind
    new_state = nlu.remember(state, reading)

    if kind == "greeting":
        if re.search(r"\b(como estas|como vas|que tal estas|todo bien|como te va)\b", folded):
            hello = f"¡Muy bien{', ' + name if name else ''}, gracias! "
        else:
            hello = f"¡Hola{', ' + name if name else ''}! "
        return _school_reply(f"{hello}Puedo mostrarte {offer}. ¿Qué necesitas?", "greeting", new_state,
                             quick, is_greeting=True)

    if kind == "thanks":
        return _school_reply("¡Con gusto! Si necesitas algo más, aquí estoy.", "greeting", new_state, is_greeting=True)

    if kind == "help":
        msg = f"Puedo mostrarte {offer}."
        if caps:
            # Lo que este usuario puede hacer además de consultar, según sus permisos.
            if "crear_evento" in (caps.get("consultas") or []):
                msg += " También puedo crear eventos contigo."
            msg += " Y si quieres saber a qué módulos tienes acceso, pregúntame «¿qué permisos tengo?»."
        if re.search(r"\b(quien|que) eres\b|\btu nombre\b|\bcomo te llamas\b", folded):
            nombre = (context.get("project_config") or {}).get("assistant_name") or "Nexo"
            msg = f"Soy {nombre}, tu asistente de SchoolSena. " + msg
        sections = [c["title"].split("›")[-1].strip() for c in catalog if c.get("path") != "/"][:5]
        if sections:
            msg += " También te llevo a secciones del menú, por ejemplo " + ", ".join(sections) + "."
        return _school_reply(msg, "help", new_state, quick, is_greeting=True)

    if kind == "profile":
        email = user_data.get("email") or "no registrado"
        reply = (f"👤 Estás como **{user_data.get('name') or 'usuario'}**, con rol **{user_role or 'sin rol'}**"
                 f" (correo: {email}). Tu centro de formación aparece en tu perfil de SchoolSena.")
        return _school_reply(reply, "profile", new_state, profile=user_data)

    if kind == "out_of_scope":
        why = {
            "money": "En SchoolSena no manejo precios ni cobros.",
            "course_catalog": "La oferta de programas y cursos no la consulto desde aquí.",
        }.get(reading.reason, "Eso está fuera de lo que manejo en SchoolSena.")
        return _school_reply(f"{why} Lo que sí puedo mostrarte: {offer}.", "out_of_scope", new_state, quick)

    if kind == "clarify":
        if reading.reason == "missing_ficha":
            fichas = [p for p in (state.get("presented") or []) if p.get("type") == "ficha"][:5]
            hint = (" Tus fichas: " + ", ".join(str(p["id"]) for p in fichas) + ".") if fichas else ""
            return _school_reply(f"¿De qué ficha? Dime el número, por ejemplo «ficha 2558».{hint}", "clarify",
                                 new_state, [{"title": f"Ficha {p['id']}", "text": f"estudiantes ficha {p['id']}"} for p in fichas],
                                 clarify=True)
        if reading.reason == "missing_actividad":
            return _school_reply("¿De qué actividad? Dime el número, por ejemplo «entregas actividad 12».",
                                 "clarify", new_state, clarify=True)
        if reading.reason == "schedule_or_activities":
            return _school_reply("¿Quieres ver tus clases o tus actividades pendientes?", "clarify", new_state,
                                 [{"title": "Clases", "text": "mis clases de hoy"},
                                  {"title": "Actividades", "text": "actividades pendientes"}], clarify=True)
        return _school_reply(f"No te entendí bien. Puedo mostrarte {offer}.", "clarify", new_state, quick, clarify=True)

    if kind == "capability":
        cap = reading.capability
        if not logged_in:
            return _school_reply("Para consultar eso necesito que inicies sesión en SchoolSena.", "denied",
                                 new_state, access_denied=True, needs_auth=True)
        # Permiso efectivo: el del backend. Si no se pudo leer (None), decide
        # el backend al ejecutar la consulta (responde 403 y se dice así).
        if school_caps.allows(caps, cap) is False:
            if cap == "fichas" and school_caps.allows(caps, "schedule"):
                # Sin gestión de fichas, "mis fichas" es su horario.
                reading.capability, reading.timeframe = "schedule", reading.timeframe or "week"
            else:
                return _school_reply(f"🔒 {_DENIED_TEXT.get(cap, 'Tu usuario no tiene permiso para consultar eso.')}"
                                     + (f" Lo que sí puedo mostrarte: {offer}." if offer else ""),
                                     "denied", new_state, access_denied=True)
        result = await _execute(reading, context, roles)
        if result is None:
            return None
        if context.get("_care_prefix") and result.get("reply"):
            result["reply"] = context["_care_prefix"] + result["reply"]
        fd = result.setdefault("final_data", {})
        fd["_school"] = nlu.remember(state, reading, _presented_from(result, reading.capability))
        return result

    # ── Nada local lo resuelve ────────────────────────────────────────────────
    # Con el modelo encendido y un mensaje con sustancia se escala, registrando
    # el motivo; si no, se pregunta aquí con lo que el rol sí puede pedir.
    from core.config import settings
    clarify = _school_reply(f"No estoy seguro de qué necesitas. Puedo mostrarte {offer}.", "clarify",
                            new_state, quick, clarify=True)
    if settings.LLM_EXTERNAL_ENABLED and len(nlu._content_words(folded)) >= 3:
        final_data["_fallback_reply"] = clarify["reply"]
        final_data["_escalation_reason"] = "school_unmatched"
        final_data["_school"] = new_state
        return None
    return clarify


# ── Post-execution ────────────────────────────────────────────────────────────

async def post_execution_interceptor(
    tool_name: str,
    args: Dict[str, Any],
    output: Dict[str, Any],
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if output and output.get("error") is None and "result" in output:
        data = output["result"]
        if isinstance(data, list) and data:
            entities = [
                {
                    "id":          item.get("id"),
                    "name":        item.get("nombre") or item.get("nombreMateria") or item.get("tituloActividad") or item.get("codigo") or "Elemento",
                    "category":    item.get("tipo") or item.get("codigo") or "SENA",
                    "logo":        item.get("logo") or item.get("imagen"),
                    "entity_type": _entity_type(tool_name),
                }
                for item in data if isinstance(item, dict)
            ]
            if entities and tool_name not in ["get_fichas_activas", "get_lista_instructores"]:
                fd = context.get("final_data", {})
                fd.setdefault("properties", []).append({"businesses": entities})
                context["final_data"] = fd
    return None