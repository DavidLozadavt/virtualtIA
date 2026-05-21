import re
import logging
from typing import Optional, Dict, Any
from tools.schoolsena import TOOLS_REGISTRY

logger = logging.getLogger("lyra.interceptors.schoolsena")


# ── Helpers de Rol ────────────────────────────────────────────────────────────

def _get_roles(user_role: str) -> tuple[bool, bool, bool]:
    r = user_role.lower()
    is_coordinator = any(x in r for x in ["coordinador", "admin", "rector", "coordinacion"])
    is_instructor  = any(x in r for x in ["instructor", "docente", "docenteup", "instructor sena"])
    is_student     = any(x in r for x in ["estudiante", "alumno", "aprendiz"])
    return is_coordinator, is_instructor, is_student


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
            fichas[codigo] = {"estado": estado, "dias": []}

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
    count_note = f"_(mostrando {count} ficha{'s' if count != 1 else ''})_\n" if count > 5 else ""
    reply = f"📋 **Tus fichas asignadas:**\n{count_note}" + "\n".join(lines)

    return {
        "reply": reply,
        "final_data": {"reply": reply},
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
        reply = (
            f"ℹ️ {err}. Si crees que hay un error, contacta a la coordinación."
            if any(w in err.lower() for w in ["no se encontró", "sin datos", "not found"])
            else f"⚠️ No pude completar la consulta: {err}"
        )
        return {"reply": reply, "final_data": {"error": err}}

    data  = result.get("result")
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

            if is_schedule and timeframe == "today" and dia_name and dia_name != today_name:
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
                date_label = f" ({week_dates[dia_name]})" if dia_name in week_dates and timeframe in ("today", "week") else ""
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

        count_note = f"_(mostrando {len(lines)} registro{'s' if len(lines) != 1 else ''})_\n" if len(lines) > 5 else ""
        reply = prefix + count_note + "\n".join(lines)
        if entities:
            props.append({"businesses": entities})

    elif isinstance(data, dict):
        lines = []
        for k, v in data.items():
            label = k.replace("_", " ").capitalize()
            if any(x in k.lower() for x in ["nomina", "valor", "total", "salario", "sueldo"]):
                try: v = f"${float(v):,.0f} COP"
                except: pass
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

async def pre_llm_interceptor(
    project_id: str,
    intent_name: str,
    args: Dict[str, Any],
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    if project_id != "schoolsena":
        return None

    user_text = (context.get("user_text") or "").lower().strip()
    user_data = context.get("user_data") or {}
    user_role = user_data.get("role", "").lower()
    user_name = user_data.get("name") or user_data.get("nombre") or "Usuario"

    is_coordinator, is_instructor, is_student = _get_roles(user_role)

    logger.info(f"SchoolSena interceptor | text='{user_text}' | role='{user_role}'")

    # ── 1. Saludo / Ayuda ─────────────────────────────────────────────────────
    greeting_kws = ["hola", "buenos dias", "buenas tardes", "buenas noches", "start"]
    help_kws     = ["ayuda", "puedes hacer", "que haces", "quien eres"]
    schedule_kws = [
        "clase", "materia", "aula", "hoy", "ahora", "horario", "semana",
        "calendario", "ficha", "mi agenda", "qué tengo", "que tengo",
        "nota", "calificacion", "actividad", "tarea",
    ]

    is_greeting = any(kw in user_text for kw in greeting_kws)
    is_help     = any(kw in user_text for kw in help_kws)
    has_query   = any(kw in user_text for kw in schedule_kws)

    if is_greeting and not has_query:
        name_part = f", {user_name.split()[0]}" if user_name != "Usuario" else ""
        if is_student:
            msg = (
                f"🎓 ¡Hola{name_part}! Soy tu asistente académico de SchoolSena.\n\n"
                "Puedo ayudarte con:\n"
                "- 📅 **Tus clases de hoy** y el horario semanal\n"
                "- 📝 **Actividades pendientes** y estado de entregas\n"
                "- 📊 **Tus calificaciones** por materia\n\n"
                "Solo escríbeme lo que necesitas, por ejemplo: _\"¿qué clases tengo hoy?\"_ "
                "o _\"¿tengo tareas pendientes?\"_"
            )
        elif is_instructor:
            msg = (
                f"📚 ¡Hola{name_part}! Soy el asistente académico de SchoolSena.\n\n"
                "Como instructor puedo ayudarte con:\n"
                "- 📅 **Tus fichas de hoy** y el calendario semanal\n"
                "- 👥 **Estudiantes de una ficha** específica\n"
                "- 📥 **Entregas de una actividad** (quién entregó y quién no)\n\n"
                "Prueba con: _\"¿qué fichas tengo hoy?\"_, _\"fichas activas\"_ "
                "o _\"entregas actividad 12\"_"
            )
        elif is_coordinator:
            msg = (
                f"🏛️ ¡Hola{name_part}! Soy el asistente de gestión de SchoolSena.\n\n"
                "Para coordinación tengo disponible:\n"
                "- 📋 **Fichas activas** del centro\n"
                "- 👨‍🏫 **Lista de instructores** activos\n"
                "- ⚠️ **Contratos próximos a vencer**\n"
                "- 💰 **Resumen de nómina** mensual\n"
                "- 📊 **Dashboard** con el estado general\n\n"
                "Puedes escribir directamente lo que necesitas."
            )
        else:
            msg = (
                f"¡Hola{name_part}! Soy el asistente de SchoolSena. "
                "Puedo consultar horarios, fichas, actividades y más. ¿En qué te ayudo?"
            )
        return {"reply": msg, "final_data": {"is_greeting": True}}

    if is_help:
        name_part = f", {user_name.split()[0]}" if user_name != "Usuario" else ""
        if is_instructor:
            msg = (
                f"📚 Claro{name_part}, aquí va lo que puedo hacer por ti:\n\n"
                "- 📅 **Fichas de hoy** y calendario semanal\n"
                "- 👥 **Estudiantes de una ficha** específica\n"
                "- 📥 **Entregas de una actividad**\n\n"
                "Prueba: _\"fichas de hoy\"_, _\"fichas activas\"_ o _\"entregas actividad 12\"_"
            )
        elif is_coordinator:
            msg = (
                f"🏛️ Claro{name_part}, puedo ayudarte con:\n\n"
                "- 📋 **Fichas activas** del centro\n"
                "- 👨‍🏫 **Lista de instructores** activos\n"
                "- ⚠️ **Contratos próximos a vencer**\n"
                "- 💰 **Resumen de nómina** mensual\n"
                "- 📊 **Dashboard** general"
            )
        else:
            msg = (
                f"🎓 Claro{name_part}, puedo ayudarte con:\n\n"
                "- 📅 **Clases de hoy** y horario semanal\n"
                "- 📝 **Actividades pendientes**\n"
                "- 📊 **Tus calificaciones**"
            )
        return {"reply": msg, "final_data": {"is_greeting": True}}

    # ── 2. Mi perfil ──────────────────────────────────────────────────────────
    if any(kw in user_text for kw in ["quien soy", "quién soy", "mi perfil", "mis datos", "mi rol"]):
        email = user_data.get("email") or "No registrado"
        reply = (
            f"👤 **Tu Perfil en SchoolSena**\n"
            f"- **Nombre**: {user_name}\n"
            f"- **Rol**: {user_role.capitalize() or 'No definido'}\n"
            f"- **Email**: {email}\n"
        )
        return {"reply": reply, "final_data": {"profile": user_data}}

    # ── 3. Fichas / Grupos ────────────────────────────────────────────────────
    ficha_match = re.search(r"ficha\s+(\d+)", user_text)

    if ficha_match and any(kw in user_text for kw in ["estudiante", "cuanto", "cuánto", "quien", "quién", "lista", "alumnos"]):
        ficha_id = ficha_match.group(1)
        return await _run(
            "get_estudiantes_ficha", {"ficha_id": ficha_id}, context,
            f"👥 **Estudiantes en la ficha {ficha_id}:**\n",
            f"No hay estudiantes registrados en la ficha {ficha_id}.",
        )

    # ── "mis fichas asignadas" / "fichas asignadas" → instructor ve fichas únicas
    if any(kw in user_text for kw in ["mis fichas", "mi fichas", "fichas asignadas"]) or user_text.strip() in ["fichas", "ficha", "las fichas"]:
        if is_instructor:
            return await _run_fichas_asignadas(context)
        # coordinador o rol genérico: fichas activas del centro
        return await _run(
            "get_fichas_activas", {}, context,
            "📋 **Fichas activas en el centro:**\n",
            "No hay fichas activas registradas en este momento.",
        )

    if any(kw in user_text for kw in ["fichas activas", "fichas del centro", "todos los grupos"]):
        return await _run(
            "get_fichas_activas", {}, context,
            "📋 **Fichas activas en el centro:**\n",
            "No hay fichas activas registradas en este momento.",
        )

    # ── 4. Nómina ─────────────────────────────────────────────────────────────
    if any(kw in user_text for kw in ["nómina", "nomina", "presupuesto", "cuánto cuesta", "cuanto cuesta"]):
        if not is_coordinator:
            return _denied("🔒 Solo el personal de coordinación puede consultar la información de nómina.")
        return await _run("get_nomina_resumen", {}, context, "💰 **Resumen de nómina mensual:**\n", "No hay datos de nómina disponibles.")

    # ── 5. Actividades / Tareas / Entregas ────────────────────────────────────
    if any(kw in user_text for kw in ["actividad", "tarea", "taller", "trabajo", "evidencia", "entrega", "pendiente"]):
        act_match = re.search(r"actividad\s+(\d+)", user_text)
        if act_match and any(kw in user_text for kw in ["entrega", "quien", "quién", "lista", "entregó"]):
            if not (is_instructor or is_coordinator):
                return _denied("🔒 Solo instructores y coordinadores pueden ver las entregas de otros estudiantes.")
            act_id = act_match.group(1)
            return await _run(
                "get_entregas", {"actividad_id": act_id}, context,
                f"📥 **Entregas — Actividad {act_id}:**\n",
                f"Ningún estudiante ha entregado aún la actividad {act_id}.",
            )
        label = "📝 **Actividades pendientes:**\n" if is_student else "📝 **Actividades pendientes del sistema:**\n"
        empty = "✅ ¡Todo al día! No tienes actividades pendientes en este momento." if is_student else "No hay actividades pendientes registradas."
        return await _run("get_actividades_pendientes", {}, context, label, empty)

    # ── 6. Instructores ───────────────────────────────────────────────────────
    if "instructor" in user_text and any(kw in user_text for kw in ["quiénes", "quienes", "lista", "cuáles", "cuales", "hay", "cuantos"]):
        if not is_coordinator:
            return _denied("🔒 Solo la coordinación puede consultar la lista completa de instructores.")
        return await _run("get_lista_instructores", {}, context, "👨‍🏫 **Instructores activos en el centro:**\n", "No se encontraron instructores activos en el sistema.")

    # ── 7. Contratos ──────────────────────────────────────────────────────────
    if any(kw in user_text for kw in ["vencer", "vencimiento", "contrato"]):
        if not is_coordinator:
            return _denied("🔒 La información de contratos está restringida a coordinación.")
        return await _run("get_contratos_vencimiento", {}, context, "⚠️ **Contratos próximos a vencer (próximos 30 días):**\n", "✅ No hay contratos por vencer en los próximos 30 días.")

    # ── 8. Dashboard / Resumen Admin ──────────────────────────────────────────
    if any(kw in user_text for kw in ["resumen admin", "estado del centro", "dashboard", "estadística", "estadistica", "general"]):
        if not is_coordinator:
            return _denied("🔒 El dashboard administrativo no está disponible para tu rol.")
        return await _run("get_resumen_admin", {}, context, "📊 **Estado general del centro:**\n", "No se pudo obtener el estado general del centro.")

    # ── 9. Notas ──────────────────────────────────────────────────────────────
    if any(kw in user_text for kw in ["nota", "calificación", "calificacion", "rendimiento", "cuanto saque", "cuánto saqué", "mis notas"]):
        return await _run("get_notas", {}, context, "📊 **Tus calificaciones:**\n", "Aún no tienes calificaciones registradas en el sistema.")

    # ── 10. Horario / Clases ──────────────────────────────────────────────────
    if any(kw in user_text for kw in [
        "clase", "materia", "aula", "hoy", "ahora", "horario", "semana",
        "calendario", "ficha", "mi agenda", "qué tengo", "que tengo"
    ]):
        # Respuestas directas sin prompt interactivo

        # Hoy
        if any(kw in user_text for kw in ["hoy", "ahora", "clases de hoy", "fichas de hoy"]):
            context["timeframe"] = "today"
            label = "🌞 **Tus fichas programadas para hoy:**\n" if is_instructor else "🌞 **Tus clases para hoy:**\n"
            empty = "No tienes fichas programadas para el día de hoy." if is_instructor else "No tienes clases programadas para el día de hoy."
            return await _run("get_clases_hoy", {}, context, label, empty)

        # Mañana
        if any(kw in user_text for kw in ["mañana", "manana", "dia de mañana", "dia de manana"]):
            from datetime import datetime, timedelta
            days_es       = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]
            tomorrow_name = days_es[(datetime.now().weekday() + 1) % 7]
            tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%d/%m")
            context["timeframe"]           = "tomorrow"
            context["target_day_override"] = tomorrow_name
            label = f"📅 **Tus fichas para mañana ({tomorrow_name.capitalize()} {tomorrow_date}):**\n" if is_instructor else f"📅 **Tus clases para mañana ({tomorrow_name.capitalize()} {tomorrow_date}):**\n"
            empty = f"No tienes fichas programadas para mañana ({tomorrow_name.capitalize()})." if is_instructor else f"No tienes clases programadas para mañana ({tomorrow_name.capitalize()})."
            return await _run("get_horario", {}, context, label, empty)

        # Mes
        if "mes" in user_text:
            context["timeframe"] = "month"
            label = "🗓️ **Fichas asignadas este mes:**\n" if is_instructor else "🗓️ **Tu horario del mes:**\n"
            return await _run("get_horario", {}, context, label, "No se encontró horario asignado para este mes.")

        # Semana / genérico (Fallback para cualquier consulta de horario)
        from datetime import datetime
        context["timeframe"] = "week"
        label = "📅 **Tu horario esta semana:**\n" if is_instructor else "📅 **Tu horario de la semana:**\n"
        week_result = await _run("get_horario", {}, context, label, "No se encontró horario asignado para esta semana.")

        if week_result and week_result.get("reply"):
            days_es    = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]
            today_name = days_es[datetime.now().weekday()].capitalize()
            has_today  = today_name.upper() in week_result["reply"].upper()
            note = (
                f"\n\n---\n📌 **Hoy es {today_name}** — tienes actividad programada."
                if has_today else
                f"\n\n---\n📌 **Hoy es {today_name}** — sin actividad programada."
            )
            week_result["reply"] += note
        return week_result

    # ── 11. Geografía / Centro ────────────────────────────────────────────────
    if any(kw in user_text for kw in ["regional", "centro", "formación", "formacion", "sede", "ciudad", "departamento"]):
        return {
            "reply": (
                "📍 El sistema está configurado para tu Centro de Formación local. "
                "Para consultas sobre otras regionales o sedes nacionales, "
                "utiliza el portal **SofíaPlus** (https://Sofia.senasofiaplus.edu.co)."
            ),
            "final_data": {"geo_info": True},
        }

    # ── 12. Fallback SENA ─────────────────────────────────────────────────────
    if any(kw in user_text for kw in ["sena", "sistema", "plataforma", "sofia", "plus", "aprendiz", "grupos", "formación", "formacion", "regional"]):
        if is_student:       suggestions = "**clases de hoy**, **mi horario**, **tareas pendientes** o **mis notas**"
        elif is_instructor:  suggestions = "**fichas de hoy**, **fichas activas** o **entregas actividad [ID]**"
        elif is_coordinator: suggestions = "**fichas activas**, **lista instructores**, **nómina** o **dashboard**"
        else:                suggestions = "**clases de hoy**, **fichas** o **mis notas**"
        return {
            "reply": f"No pude identificar exactamente qué necesitas, pero como {user_role or 'usuario'} puedes preguntarme por: {suggestions}.",
            "final_data": {"fallback": True},
        }

    logger.info("SchoolSena interceptor: no match")
    return None


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