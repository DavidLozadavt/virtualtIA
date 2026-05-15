import re
import logging
from typing import Optional, Dict, Any
from tools.schoolsena import TOOLS_REGISTRY

logger = logging.getLogger("lyra.interceptors.schoolsena")


async def _execute_and_format(tool_name: str, params: dict, context: dict, prefix: str, empty_msg: str = None) -> Optional[Dict[str, Any]]:
    """Execute a SchoolSena tool and format the result as a chat reply."""
    tool = TOOLS_REGISTRY.get(tool_name)
    if not tool:
        return None

    try:
        result = await tool["handler"](params, context)
    except Exception as e:
        logger.error(f"Error executing tool '{tool_name}': {e}", exc_info=True)
        return {"reply": f"Error técnico: {str(e)}", "final_data": {"error": str(e)}}

    if result.get("error"):
        error_msg = result['error']
        if "no se encontró" in error_msg.lower() or "sin datos" in error_msg.lower():
            reply = f"ℹ️ {error_msg}. Si crees que esto es un error, por favor contacta a la coordinación de tu centro."
        else:
            reply = f"⚠️ No pude completar la consulta: {error_msg}"
        return {"reply": reply, "final_data": {"error": error_msg}}

    data = result.get("result")
    properties = []
    
    if not data:
        reply = empty_msg if empty_msg else f"{prefix} No se encontraron datos para mostrar en este momento."
    elif isinstance(data, list):
        if len(data) == 0:
            reply = empty_msg if empty_msg else f"{prefix} Por ahora no hay registros disponibles."
        else:
            from datetime import datetime, timedelta
            now = datetime.now()
            
            # 1. Determine Timeframe
            timeframe = context.get("timeframe")
            user_text = (context.get("user_text") or "").lower()
            if not timeframe:
                if "hoy" in user_text: timeframe = "today"
                elif "semana" in user_text: timeframe = "week"
                elif "mes" in user_text: timeframe = "month"
                else: timeframe = "week" # Default

            # 2. Calculate Week Dates
            # now.weekday() is 0 (Mon) to 6 (Sun)
            start_of_week = now - timedelta(days=now.weekday())
            week_dates = {}
            days_list_es = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]
            for i, day_name in enumerate(days_list_es):
                date_val = start_of_week + timedelta(days=i)
                week_dates[day_name] = date_val.strftime("%d/%m")

            target_day = days_list_es[now.weekday()]
            
            lines = []
            entities = []
            
            is_horario_tool = tool_name in ["get_horario", "get_clases_hoy"]
            
            # For get_horario, only show validity if in 'month' view
            if tool_name == "get_horario" and data and isinstance(data[0], dict) and timeframe == "month":
                first = data[0]
                f_ini = first.get("fechaInicial") or first.get("fecha_inicial")
                f_fin = first.get("fechaFinal") or first.get("fecha_final")
                if f_ini and f_fin:
                    prefix += f"Vigencia: del {f_ini} al {f_fin}\n\n"

            seen_slots = set()
            for item in data:
                if isinstance(item, dict):
                    # Filter: if today was requested
                    item_dia_obj = item.get("dia")
                    item_dia_name = ""
                    if isinstance(item_dia_obj, dict):
                        item_dia_name = (item_dia_obj.get("dia") or "").upper()
                    elif item.get("dia_nombre"):
                        item_dia_name = str(item["dia_nombre"]).upper()
                    
                    if is_horario_tool and timeframe == "today":
                        if item_dia_name and item_dia_name != target_day:
                            continue

                    # Special handling for Schedule (Horario) Deduplication
                    is_horario = tool_name in ["get_horario", "get_clases_hoy"]
                    dia_info = ""
                    time_info = ""
                    
                    if "horaInicial" in item or "hora_inicial" in item:
                        h_ini = (item.get("horaInicial") or item.get("hora_inicial", ""))[:5]
                        h_fin = (item.get("horaFinal") or item.get("hora_final", ""))[:5]
                        time_info = f"{h_ini}-{h_fin}"

                    if is_horario:
                        slot_key = (item_dia_name, time_info)
                        if slot_key in seen_slots:
                            continue
                        seen_slots.add(slot_key)

                    # Smart display based on item fields
                    name = (item.get("nombre") or item.get("nombreMateria") or 
                            item.get("tituloActividad") or item.get("materia_nombre") or
                            item.get("materiaNombre"))
                    
                    gm = item.get("gradoMateria") or item.get("grado_materia")
                    if not name and isinstance(gm, dict):
                        materia = gm.get("materia", {})
                        if isinstance(materia, dict):
                            name = (materia.get("nombreMateria") or materia.get("nombre") or 
                                   materia.get("materia_nombre") or materia.get("nombre_materia") or
                                   materia.get("materiaNombre"))
                    
                    if not name and item.get("codigo"):
                        name = f"Ficha {item['codigo']}" if "ficha" in prefix.lower() else str(item["codigo"])
                    
                    if item_dia_name:
                        date_str = f" ({week_dates.get(item_dia_name)})" if timeframe in ["today", "week"] else ""
                        dia_info = item_dia_name.capitalize() + date_str
                    
                    if not name and item.get("materia_nombre"):
                        name = item.get("materia_nombre")
                    
                    # Construct line
                    if is_horario:
                        header = f"**{dia_info} {time_info}**".strip()
                        # For schedule, keep it short: Ficha + Status
                        ficha_info = ""
                        ficha_obj = item.get("ficha")
                        if isinstance(ficha_obj, dict) and ficha_obj.get("codigo"):
                            ficha_info = f" [Ficha {ficha_obj['codigo']}]"
                        
                        estado_info = f" - {item['estado']}" if item.get("estado") else ""
                        line = f"- {header} | {ficha_info}{estado_info}"
                    else:
                        prefix_name = f"**{name}**" if name else ""
                        extra = ""
                        if dia_info or time_info:
                            extra += f" | {dia_info} {time_info}".strip()
                        if isinstance(item.get("ficha"), dict) and item["ficha"].get("codigo"):
                            extra += f" [Ficha {item['ficha']['codigo']}]"
                        date_info = f" [Vence: {item.get('fecha_fin') or item.get('fechaFin')}]" if (item.get("fecha_fin") or item.get("fechaFin")) else ""
                        estado_info = f" - Estado: {item['estado']}" if item.get("estado") else ""
                        line = f"- {prefix_name}{extra}{date_info}{estado_info}"
                        
                    lines.append(line)
                    if not is_horario:
                        entities.append({
                            "id": item.get("id"),
                            "name": name or "SENA",
                            "category": item.get("tipo") or item.get("codigo") or "SENA",
                            "logo": item.get("logo") or item.get("imagen"),
                            "entity_type": item.get("entity_type") or ("ficha" if "fichas" in prefix.lower() else "actividad" if "actividades" in prefix.lower() else "clase" if "clases" in prefix.lower() else "persona")
                        })
                else:
                    lines.append(f"- {item}")
            
            if not lines:
                 reply = empty_msg if empty_msg else f"{prefix} No hay registros para este periodo."
            else:
                 reply = prefix + "\n".join(lines)
                 
            if entities:
                properties.append({"businesses": entities})
    elif isinstance(data, dict):
        lines = []
        for k, v in data.items():
            key_label = k.replace("_", " ").capitalize()
            # Format currency for payroll
            if "nomina" in k or "valor" in k or "total" in k:
                try: v = f"${float(v):,.0f} COP" 
                except: pass
            lines.append(f"- **{key_label}**: {v}")
        reply = prefix + "\n".join(lines)
    else:
        reply = f"{prefix}{data}"

    return {
        "reply": reply, 
        "final_data": {"reply": reply},
        "properties": properties
    }


async def pre_llm_interceptor(
    project_id: str,
    intent_name: str,
    args: Dict[str, Any],
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """SchoolSena pre-LLM interceptor."""
    if project_id != "schoolsena":
        return None

    user_text = (context.get("user_text") or "").lower()
    user_data = context.get("user_data") or {}
    user_role = user_data.get("role", "").lower()
    
    logger.info(f"INTERCEPTOR SchoolSena | Text: '{user_text}' | Role: '{user_role}'")

    # ── Role Checks ───────────────────────────────────────────────
    is_coordinator = any(r in user_role for r in ["coordinador", "admin", "rector", "coordinacion"])
    is_instructor = any(r in user_role for r in ["instructor", "docente", "docenteup", "instructor sena"])
    is_student = any(r in user_role for r in ["estudiante", "alumno", "aprendiz"])
    
    logger.info(f"DEBUG SchoolSena | user_role: '{user_role}' | is_coordinator: {is_coordinator} | is_instructor: {is_instructor}")

    # ── 1. Fichas / Grupos ────────────────────────────────────────
    if "ficha" in user_text:
        logger.info("MATCH: 'ficha' detected")
        ficha_match = re.search(r"ficha\s+(\d+)", user_text)
        if ficha_match and any(kw in user_text for kw in ["estudiante", "cuanto", "cuánto", "quien", "quién", "lista"]):
            ficha_id = ficha_match.group(1)
            logger.info(f"EXEC: get_estudiantes_ficha (ID: {ficha_id})")
            return await _execute_and_format("get_estudiantes_ficha", {"ficha_id": ficha_id}, context, f"👥 Estudiantes en la ficha {ficha_id}:\n", f"No se encontraron estudiantes registrados para la ficha {ficha_id}.")
        
        logger.info("EXEC: get_fichas_activas")
        return await _execute_and_format("get_fichas_activas", {}, context, "📋 Fichas activas:\n", "No tienes fichas activas asignadas en este momento.")

    # ── 2. Nómina (Admin Only) ────────────────────────────────────
    if any(kw in user_text for kw in ["nómina", "nomina", "presupuesto", "cuánto cuesta", "cuanto cuesta"]):
        logger.info("MATCH: 'nomina' detected")
        if not is_coordinator:
            logger.info("DENIED: Not coordinator")
            return {"reply": "🔒 Acceso denegado. Solo personal administrativo puede consultar la nómina.", "final_data": {"access_denied": True}}
        return await _execute_and_format("get_nomina_resumen", {}, context, "💰 Resumen de nómina mensual:\n", "No hay datos de nómina disponibles para mostrar.")

    # ── 3. Actividades / Tareas / Entregas ────────────────────────
    if any(kw in user_text for kw in ["actividad", "tarea", "taller", "trabajo", "evidencia", "entrega"]):
        logger.info("MATCH: 'actividad/entrega' detected")
        actividad_match = re.search(r"actividad\s+(\d+)", user_text)
        if actividad_match and any(kw in user_text for kw in ["entrega", "quien", "quién", "lista"]):
            if not (is_instructor or is_coordinator):
                return {"reply": "🔒 Solo instructores pueden ver las entregas de otros estudiantes.", "final_data": {"access_denied": True}}
            actividad_id = actividad_match.group(1)
            return await _execute_and_format("get_entregas", {"actividad_id": actividad_id}, context, f"📥 Entregas para la actividad {actividad_id}:\n", f"No hay entregas registradas para la actividad {actividad_id}.")
        
        return await _execute_and_format("get_actividades_pendientes", {}, context, "📝 Actividades pendientes:\n", "¡Excelente! No tienes actividades pendientes en este momento.")

    # ── 4. Instructores (Admin Only) ──────────────────────────────
    if "instructor" in user_text and any(kw in user_text for kw in ["quiénes", "quienes", "lista", "cuáles", "cuales"]):
        logger.info("MATCH: 'instructor' detected")
        if not is_coordinator:
            return {"reply": "🔒 No tienes permisos para ver la lista completa de instructores.", "final_data": {"access_denied": True}}
        return await _execute_and_format("get_lista_instructores", {}, context, "👨‍🏫 Instructores activos:\n", "No se encontraron instructores activos en el sistema.")

    # ── 5. Contratos (Admin Only) ─────────────────────────────────
    if any(kw in user_text for kw in ["vencer", "vencimiento", "contrato"]):
        logger.info("MATCH: 'contrato' detected")
        if not is_coordinator:
            return {"reply": "🔒 Información de contratos restringida a coordinación.", "final_data": {"access_denied": True}}
        return await _execute_and_format("get_contratos_vencimiento", {}, context, "⚠️ Contratos próximos a vencer (30 días):\n", "No hay contratos próximos a vencer en los siguientes 30 días.")

    # ── 6. Resumen / Dashboard (Admin Only) ───────────────────────
    if any(kw in user_text for kw in ["resumen admin", "estado del centro", "dashboard", "estadística", "estadistica"]):
        logger.info("MATCH: 'dashboard' detected")
        if not is_coordinator:
            return {"reply": "🔒 El dashboard administrativo no está disponible para tu rol.", "final_data": {"access_denied": True}}
        return await _execute_and_format("get_resumen_admin", {}, context, "📊 Estado general del centro:\n", "No se pudo obtener el estado general del centro.")

    # ── 7. Clases y Horario ───────────────────────────────────────
    if any(kw in user_text for kw in ["clase", "materia", "aula", "hoy", "ahora", "horario", "semana", "calendario"]):
        logger.info("MATCH: 'horario/clases' detected")
        
        # If the user is just asking "horario" or "clases" without a specific time frame
        # we offer quick options to be more specific
        if user_text.strip() in ["horario", "clases", "mi horario", "mis clases", "que clases tengo"]:
            return {
                "reply": "📅 ¿Qué horario deseas consultar?",
                "final_data": {"show_options": True},
                "properties": [
                    {
                        "quick_replies": [
                            {"title": "🌞 Clases de Hoy", "text": "clases de hoy"},
                            {"title": "📅 Esta Semana", "text": "mi horario de la semana"},
                            {"title": "🗓️ Del Mes", "text": "horario del mes"}
                        ]
                    }
                ]
            }

        # Today
        if "hoy" in user_text or "ahora" in user_text or "clases de hoy" in user_text:
            context["timeframe"] = "today"
            return await _execute_and_format("get_clases_hoy", {}, context, "🌞 **Tus clases para hoy**:\n", "No tienes clases programadas para el día de hoy.")

        # Month
        if "mes" in user_text:
            context["timeframe"] = "month"
            return await _execute_and_format("get_horario", {}, context, "🗓️ **Tu Horario del Mes**:\n(Mostrando registros vigentes en el calendario actual)\n", "No se encontró un horario asignado para este mes.")

        # Week (default if 'horario' is detected but not 'hoy' or 'mes')
        if any(kw in user_text for kw in ["horario", "semana", "calendario"]):
            context["timeframe"] = "week"
            return await _execute_and_format("get_horario", {}, context, "📅 **Tu Horario de la Semana**:\n", "No se encontró un horario asignado.")

    # ── 8. Notas ──────────────────────────────────────────────────
    if any(kw in user_text for kw in ["nota", "calificación", "calificacion", "rendimiento", "cuanto saque", "cuanto saqué"]):
        logger.info("MATCH: 'notas' detected")
        return await _execute_and_format("get_notas", {}, context, "📊 Calificaciones:\n", "Aún no tienes calificaciones registradas en el sistema.")

    # ── 9. Geografía / Centro / Regionales ────────────────────────
    if any(kw in user_text for kw in ["regional", "centro", "formación", "formacion", "sede", "ciudad", "departamento"]):
        logger.info("MATCH: 'geo/centro' detected")
        # If we don't have a tool for this, we provide a structured fast response
        return {
            "reply": "📍 Actualmente el sistema está configurado para tu Centro de Formación local. Para consultas sobre otras regionales o sedes nacionales, por favor utiliza el portal de SofíaPlus.",
            "final_data": {"geo_info": True}
        }

    # ── 10. Mi Perfil / Quién soy ──────────────────────────────────
    if any(kw in user_text for kw in ["quien soy", "quién soy", "mi perfil", "mis datos", "mi rol"]):
        logger.info("MATCH: 'profile' detected")
        name = user_data.get("name") or user_data.get("nombre") or "Usuario"
        role = user_role.capitalize()
        email = user_data.get("email") or "No registrado"
        reply = (
            f"👤 **Tu Perfil en SchoolSena**:\n"
            f"- **Nombre**: {name}\n"
            f"- **Rol**: {role}\n"
            f"- **Email**: {email}\n"
        )
        return {"reply": reply, "final_data": {"profile": user_data}}

    # ── 11. Saludos y Ayuda ───────────────────────────────────────
    if any(kw in user_text for kw in ["hola", "buenos dias", "buenas tardes", "que haces", "ayuda", "puedes hacer", "quien eres"]):
        logger.info("MATCH: 'greeting/help' detected")
        help_msg = (
            "🎓 ¡Hola! Soy tu asistente de SchoolSena.\n\n"
            "Puedo ayudarte con:\n"
            "- Consultar tus **clases de hoy** y **horario** semanal.\n"
            "- Ver tus **actividades pendientes** y **calificaciones**.\n"
        )
        if is_coordinator:
            help_msg += "- Revisar el **resumen de nómina** y **contratos por vencer**.\n"
            help_msg += "- Ver la **lista de instructores** y **fichas activas**.\n"
        
        help_msg += "\n¿En qué puedo apoyarte hoy?"
        return {"reply": help_msg, "final_data": {"is_greeting": True}}

    # ── 12. Fallback (Cerebro Rápido) ─────────────────────────────
    # Captura CUALQUIER cosa que huela a SENA o academia
    sena_keywords = [
        "sistema", "sena", "estudiante", "aprendiz", "instructor", "coordinador", "plataforma", 
        "grupos", "entregas", "formación", "formacion", "regional", "centro", "sede", "sofia", "plus"
    ]
    if any(kw in user_text for kw in sena_keywords):
         logger.info("MATCH: 'global sena fallback' detected")
         return {
             "reply": "Entiendo que tu consulta es sobre SchoolSena, pero no puedo procesar esa solicitud específica automáticamente. Prueba con 'mis notas', 'clases de hoy', 'fichas' o 'nomina'.",
             "final_data": {"fallback": True}
         }

    logger.info("NO MATCH in SchoolSena interceptor")

    return None


async def post_execution_interceptor(
    tool_name: str,
    args: Dict[str, Any],
    output: Dict[str, Any],
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Optional post-execution processing."""
    # Convert tool output to frontend properties when using LLM loop
    if output and output.get("error") is None and "result" in output:
        data = output["result"]
        if isinstance(data, list) and len(data) > 0:
            entities = []
            for item in data:
                if isinstance(item, dict):
                    name = item.get("nombre") or item.get("nombreMateria") or item.get("tituloActividad") or item.get("codigo") or "Elemento"
                    entities.append({
                        "id": item.get("id"),
                        "name": name,
                        "category": item.get("tipo") or item.get("codigo") or "SENA",
                        "logo": item.get("logo") or item.get("imagen"),
                        "entity_type": item.get("entity_type") or ("ficha" if "fichas" in tool_name else "actividad" if "actividades" in tool_name else "clase" if "clases" in tool_name else "persona")
                    })
            if entities:
                final_data = context.get("final_data", {})
                if "properties" not in final_data:
                    final_data["properties"] = []
                final_data["properties"].append({"businesses": entities})
                context["final_data"] = final_data
                
    return None
