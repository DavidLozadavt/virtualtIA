"""
orchestrator/tool_runner.py — Agent loop con límite estricto de herramientas.
"""

import importlib
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

from orchestrator.intent_router import detect_intent
from orchestrator.response_engine import generate_response, _format_distance, _build_business_list
from tools.shared.utils import (
    normalize_text as _normalize_shared,
    WEEKDAY_MAP as _WEEKDAY_MAP,
    MONTH_MAP as _MONTH_MAP,
    looks_like_iso_date as _looks_like_iso_date,
    looks_like_time_24h as _looks_like_time_24h,
    format_time_24h as _format_time_24h,
    parse_date_candidate as _parse_date_candidate,
    parse_time_candidate as _parse_time_candidate,
    is_generic_query as _is_generic_query,
    recover_suggested_city_from_history as _recover_suggested_city_from_history,
    recover_last_search_args_from_history as _recover_last_search_args_from_history,
    recover_last_businesses_from_history as _recover_last_businesses_from_history,
    find_anchored_id_in_messages as _find_anchored_id_in_messages,
    recover_last_appointment_entities as _recover_last_appointment_entities,
    recover_appointment_details_from_history as _recover_appointment_details_from_history,
    extract_session_user_id as _extract_session_user_id,
    extract_session_today as _extract_session_today,
    get_recent_user_messages as _get_recent_user_messages,
    extract_tastes_from_history as _extract_tastes_from_history,
    resolve_schedule_datetime as _resolve_schedule_datetime,
    build_schedule_clarification as _build_schedule_clarification,
    match_property_id_in_reply as _match_property_id_in_reply,
    inject_ids_into_titles as _inject_ids_into_titles,
    resolve_personality as _resolve_personality,
)

from orchestrator.tool_registry import ToolRegistry
from orchestrator.interceptors import manager as interceptor_manager
from core.llm_engine import LLMUnavailable
from core.logger import setup_logger

logger = setup_logger("lyra.tool_runner")


def _normalize(s: str) -> str:
    """Thin wrapper — delegates to tools.shared.utils.normalize_text."""
    return _normalize_shared(s)


def _is_generic_query(text: str | None) -> bool:
    """
    Determina si una consulta es genérica (ej: "este", "ese", "el negocio") 
    o si menciona un nombre específico. Usa límites de palabra para evitar 
    falsos positivos como "oeste" conteniendo "este".
    """
    if not text:
        return True
    
    text_norm = _normalize(text)
    # Palabras que indican que el usuario NO está nombrando un negocio específico
    generic_keywords = [
        "este", "ese", "aqui", "alli", "negocio", "local", "tienda", 
        "ella", "ello", "esta", "compañia", "empresa", "lugar", "sitio", "dia", "sol", "mar", "paz"
    ]
    
    # Si el texto es muy corto y contiene una de estas palabras, es genérico
    words = text_norm.split()
    if len(words) <= 3:
        for kw in generic_keywords:
            if re.search(rf"\b{kw}\b", text_norm):
                return True
                
    return False


def _recover_suggested_city_from_history(messages: list[dict]) -> str | None:
    """Recupera la ciudad sugerida en el último mensaje de la herramienta o del asistente."""
    for m in reversed(messages):
        if m.get("role") == "tool":
            try:
                content = m.get("content", "{}")
                if isinstance(content, str):
                    content = json.loads(content)
                city = content.get("_suggested_city") or content.get("suggested_next_city")
                if city:
                    return city
            except:
                continue
    
    # Fallback: Extraer del tag [CITY:X] del asistente (forma más confiable)
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content") or ""
            if isinstance(content, str):
                # Tag explícito [CITY:X]
                tag_match = re.search(r"\[CITY:([^\]]+)\]", content)
                if tag_match:
                    return tag_match.group(1).strip()
                # Variante: "hay excelentes opciones en **Ciudad**"
                match = re.search(r"(?:opciones|negocios|resultados)\s+en\s+\*?\*?([a-zA-Z\u00e1\u00e9\u00ed\u00f3\u00fa\u00c1\u00c9\u00cd\u00d3\u00da\u00f1\u00d1 ]+?)\*?\*?\s*(?:\(|\?|\[)", content, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
            break
            
    return None


def _recover_last_search_args_from_history(messages: list[dict]) -> dict:
    """Recupera tanto la ciudad como la categoría de la última búsqueda desde los tool messages."""
    for m in reversed(messages):
        if m.get("role") == "tool":
            try:
                content = m.get("content", "{}")
                if isinstance(content, str):
                    content = json.loads(content)
                # Si tiene el campo 'category', nos interesa
                if "category" in content:
                    # Extraer el ID del primer negocio sugerido si existe
                    suggested_biz_id = None
                    suggested_biz_name = None
                    biz_list = content.get("suggested_businesses", [])
                    # Si solo hay 1-2 negocios, podemos considerar navegación directa
                    if biz_list and len(biz_list) > 0:
                        suggested_biz_id = biz_list[0].get("id")
                        suggested_biz_name = biz_list[0].get("name")

                    return {
                        "city": content.get("suggested_next_city") or content.get("city"),
                        "category": content.get("category"),
                        "suggested_biz_id": suggested_biz_id,
                        "suggested_biz_name": suggested_biz_name,
                        "biz_count": len(biz_list) if biz_list else 0
                    }
            except:
                continue
    
    # Fallback: intentar recuperar ciudad Y categoría del asistente si no hay tool message claro
    suggested_city = _recover_suggested_city_from_history(messages)
    # Recuperar categoría del texto del asistente si hay [CITY:X] (indica búsqueda fallida con sugerencia)
    last_category = ""
    if suggested_city:
        for m in reversed(messages):
            if m.get("role") == "assistant":
                content = m.get("content") or ""
                # Buscar patrón "No encontré **barberias**" o "No encontré **ver negocios de barberias**"
                cat_match = re.search(r"No encontré?\s+\*\*([^*]+)\*\*", content, re.IGNORECASE)
                if cat_match:
                    raw_cat = cat_match.group(1).strip().lower()
                    # Intentar resolver a categoría canónica
                    from orchestrator.intent_router import _CATEGORY_KEYWORDS
                    resolved = None
                    for cat, kws in _CATEGORY_KEYWORDS.items():
                        if any(kw in raw_cat for kw in kws):
                            resolved = cat
                            break
                    last_category = resolved or raw_cat
                break
    if suggested_city:
        return {"city": suggested_city, "category": last_category, "suggested_biz_id": None, "biz_count": 0}
        
    return {}


def _recover_last_businesses_from_history(messages: list[dict]) -> list[dict]:
    """
    Recupera la lista de negocios mencionados en el historial.
    Busca tanto en respuestas de herramientas (JSON) como en texto del asistente (Etiquetas BIZ).
    """
    # 1. Intentar desde mensajes de herramienta (Más preciso)
    for m in reversed(messages):
        if m.get("role") == "tool":
            try:
                content = json.loads(m.get("content") or "{}")
                # Algunos tools devuelven 'businesses' o 'results'
                biz_list = content.get("businesses") or content.get("results") or content.get("suggested_businesses")
                if biz_list and isinstance(biz_list, list):
                    logger.info(f"DEBUG: Negocios recuperados de 'tool': {[b.get('name') or b.get('razonSocial') for b in biz_list]}")
                    return [{"id": str(b.get("id")), "name": b.get("name") or b.get("razonSocial")} for b in biz_list]
            except:
                continue
    
    # 2. Fallback: Reconstruir desde etiquetas [BIZ:ID] en mensajes del asistente
    biz_list = []
    messages_scanned = 0
    user_messages_seen = 0
    max_messages = 12 # Escaneo profundo
    
    for m in reversed(messages):
        messages_scanned += 1
        if messages_scanned > max_messages: break
        
        role = m.get("role")
        if role == "user":
            user_messages_seen += 1
            # Si ya tenemos 2+ negocios y hemos pasado por 2+ interacciones de usuario, paramos
            if len(biz_list) >= 2 and user_messages_seen >= 3: break
            continue
            
        if role == "assistant":
            content = m.get("content") or ""
            # Soportar contenido en formato de bloques (lista de dicts)
            if isinstance(content, list):
                content = " ".join([block.get("text", "") for block in content if block.get("type") == "text"])
            
            if not isinstance(content, str): continue
            
            # Buscar nombres en negrita y sus IDs asociados
            # Template: **Nombre** ... [BIZ:ID]
            for _match in re.finditer(r'\*\*(.+?)\*\*', content):
                _biz_name = _match.group(1).strip()
                # El ID suele estar justo después del nombre o al final de la línea/párrafo
                _start_idx = content.find(f"**{_biz_name}**")
                if _start_idx == -1: continue
                
                # Buscamos el ID en los siguientes 1000 caracteres tras el nombre (para reportes largos)
                _lookahead = content[_start_idx:_start_idx+1000]
                
                # Validación crítica: Si en el lookahead aparece OTRA negrita, este ID no pertenece a este nombre
                _next_bold = content.find("**", _start_idx + len(_biz_name) + 4)
                if _next_bold != -1 and _next_bold < (_start_idx + 1000):
                    _lookahead = content[_start_idx:_next_bold]

                _biz_id_match = re.search(r'\[(?:BIZ|ID|TAG):(\d+)\]', _lookahead)
                
                if _biz_id_match:
                    biz_id = str(_biz_id_match.group(1))
                    if not any(b["id"] == biz_id for b in biz_list):
                        biz_list.append({"id": biz_id, "name": _biz_name})
    
    if biz_list:
        logger.info(f"DEBUG: Negocios reconstruidos del historial ({len(biz_list)}): {[b['name'] for b in biz_list]}")
    else:
        logger.warning("DEBUG: No se pudieron recuperar negocios del historial.")
        
    return biz_list


def _find_anchored_id_in_messages(messages: list[dict]) -> int | None:
    """Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible."""
    logger.info(f"DEBUG: Iniciando búsqueda de ID anclado en {len(messages)} mensajes.")
    for i, msg in enumerate(reversed(messages)):
        role = msg.get("role")
        content = msg.get("content") or ""
        
        # Soportar contenido en formato de bloques
        if isinstance(content, list):
            content = " ".join([block.get("text", "") for block in content if block.get("type") == "text"])
            
        if not content or not isinstance(content, str):
            continue
            
        logger.info(f"DEBUG: Escaneando mensaje {i} (rol={role}): {content[:100]}...")

        # Busca [ID:X], [BIZ:X] o [TAG:X] con flexibilidad de espacios
        matches = re.findall(r"\[(?:ID|BIZ|TAG)[:\s]+(\d+)\]", content, re.IGNORECASE)
        # Soporte para enlaces de NexiService: /empresa/35
        link_matches = re.findall(r"/empresa/(\d+)", content)
        
        all_matches = matches + link_matches
        if all_matches:
            try:
                # Tomar el ÚLTIMO encontrado en el texto (ej: si lista varios, el último es el más reciente o relevante)
                found_id = int(all_matches[-1])
                logger.info(f"DEBUG: ¡ÉXITO! ID {found_id} encontrado en mensaje de rol '{role}'.")
                return found_id
            except ValueError:
                continue
    
    logger.warning(f"DEBUG: No se encontró ningún ID anclado [BIZ:X] en el historial de mensajes actual.")
    return None


def _recover_last_appointment_entities(messages: list[dict]) -> dict:
    """Busca en el historial menciones de servicios, profesionales o negocios en contextos de agendamiento."""
    entities = {"service_name": None, "professional_name": None, "business_name": None}
    for msg in reversed(messages):
        content = (msg.get("content") or "").lower()
        if not isinstance(content, str): continue
        
        # Si el asistente mencionó disponibilidad o agendamiento o usó tags explícitos
        if msg.get("role") == "assistant":
            # Soporte para TAGS explícitos (Prioridad alta)
            srv_tag = re.search(r"\[SERVICIO:([^\]]+)\]", content, re.IGNORECASE)
            if srv_tag and not entities["service_name"]:
                entities["service_name"] = srv_tag.group(1).strip()
            
            biz_tag = re.search(r"\[(?:BIZ|ID):(\d+)\]", content, re.IGNORECASE)
            # Nota: Aquí no podemos guardar el ID en 'entities' porque el diccionario es de nombres, 
            # pero el ID se recupera mediante _find_anchored_id_in_messages.

            if "agendar" in content or "disponibles" in content or "cita" in content or "[SERVICIO:" in content:
                # Buscar negocio
                biz_match = re.search(r"(?:en|al|el)\s+(?!servicio\s+de)([a-zA-Z0-9áéíóúÁÉÍÓÚñÑ\s]{2,})", content, re.IGNORECASE)
                if biz_match and not entities["business_name"]:
                    entities["business_name"] = biz_match.group(1).strip()

                # Buscar servicio en **SERVICIO** (ej: "para **reparacion**")
                srv_match = re.search(r"para \*\*([^*]+)\*\*", content)
                if srv_match and not entities["service_name"]:
                    entities["service_name"] = srv_match.group(1).strip()
                
                # Buscar profesional en **PROFESIONAL** (ej: "con **valentina**")
                prof_match = re.search(r"con \*\*([^*]+)\*\*", content)
                if prof_match and not entities["professional_name"]:
                    entities["professional_name"] = prof_match.group(1).strip()
                
        # Si el usuario mencionó entidades en turnos previos
        if msg.get("role") == "user":
            if " en " in content and not entities["business_name"]:
                # Tomar lo que sigue a "en " hasta el siguiente conector
                biz_part = content.split(" en ")[-1].split(" para ")[0].split(" con ")[0].strip()
                if len(biz_part) >= 2:
                    entities["business_name"] = biz_part

            if " con " in content and not entities["professional_name"]:
                prof_part = content.split(" con ")[-1].split(" para ")[0].split(" en ")[0].strip()
                if len(prof_part.split()) >= 2: # Probablemente un nombre
                    entities["professional_name"] = prof_part

            if " para " in content and not entities["service_name"]:
                srv_part = content.split(" para ")[-1].split(" con ")[0].split(" en ")[0].strip()
                entities["service_name"] = srv_part
                
        if entities["service_name"] and entities["professional_name"] and entities["business_name"]:
            break
    return entities

def _recover_appointment_details_from_history(messages: list[dict], target_biz_id: int = None) -> dict | None:
    """Recupera los detalles de una reserva pendiente de confirmación."""
    logger.info(f"DEBUG: Intentando recuperar detalles de cita de {len(messages)} mensajes. BIZ_TARGET={target_biz_id}")
    for msg in reversed(messages):
        role = msg.get("role")
        content = msg.get("content") or ""
        
        # Soportar contenido en formato de bloques
        if isinstance(content, list):
            content = " ".join([block.get("text", "") for block in content if block.get("type") == "text"])
        
        if not content or not isinstance(content, str):
            continue

        if role == "assistant":
            # Caso 1: Confirmación directa de cita (V3.3)
            if "CONFIRMACI" in content and "NECESARIA" in content:
                service_match = re.search(r"Reserva:\s*\*?\*?([^*\n\]]+)\*?\*?", content)
                # Tiempo puede estar en: "Hora solicitada: 10:00", "a las **10:00**", "10:00" post-anchor
                time_match = (
                    re.search(r"solicitada[:\s*]+\*?\*?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", content, re.IGNORECASE) or
                    re.search(r"a\s+las\s+\*?\*?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\*?\*?", content, re.IGNORECASE) or
                    re.search(r"a\s+las\s+\*?\*?(\d{1,2})\s+de\s+la\s+(mañana|tarde|noche)", content, re.IGNORECASE) or
                    re.search(r"\bHora:\s*\*?\*?(\d{1,2}(?::\d{2})?)\b", content, re.IGNORECASE)
                )

                biz_id_match = re.search(r'\[(?:BIZ|ID|TAG):(\d+)\]', content)
                biz_id = int(biz_id_match.group(1)) if biz_id_match else None

                # Si tenemos un target_biz_id y no coincide, seguimos buscando (evitar cruces de negocios)
                if target_biz_id and biz_id and biz_id != target_biz_id:
                    continue

                # Normalizar tiempo capturado
                _time_val = None
                if time_match:
                    _raw = time_match.group(1).strip()
                    # Si es solo hora con am/pm (e.g., "2 pm" o "14:pm")
                    if ":" in _raw:
                        h_part, m_part = _raw.split(":", 1)
                        m_part = re.sub(r'[^0-9]', '', m_part) or "00"
                        _time_val = f"{int(h_part):02d}:{m_part[:2]}"
                    elif re.match(r'^\d{1,2}$', _raw):
                        _time_val = f"{int(_raw):02d}:00"
                    else:
                        _time_val = _raw

                if service_match and _time_val:
                    _srv_clean = service_match.group(1).strip().rstrip('*').strip()
                    # Quitar sufijos de hora del servicio
                    _srv_clean = re.sub(r'\s+a\s+las.*$', '', _srv_clean, flags=re.IGNORECASE).strip()
                    logger.info(f"DEBUG: Recuperada cita directa -> {_srv_clean} @ {_time_val} (BIZ={biz_id})")
                    return {
                        "service_name": _srv_clean,
                        "time": _time_val,
                        "date": "today" if "hoy" in content.lower() else ("tomorrow" if "mañana" in content.lower() else None),
                        "business_id": biz_id
                    }
            
            # Caso 2: Intención de agendar tras INFO de servicio (V3.6)
            elif "agendar" in content.lower() and "[SERVICIO:" in content:
                srv_match = re.search(r"\[SERVICIO[:\s]+([^\]]+)\]", content, re.IGNORECASE)
                biz_match = re.search(r"\[BIZ[:\s]+(\d+)\]", content, re.IGNORECASE)
                
                if srv_match and biz_match:
                    found_srv = srv_match.group(1).strip()
                    found_biz = int(biz_match.group(1))
                    
                    if target_biz_id and found_biz != target_biz_id:
                        continue
                        
                    logger.info(f"DEBUG: ¡ÉXITO! Recuperado contexto -> Servicio: {found_srv}, BIZ: {found_biz}")
                    return {
                        "service_name": found_srv,
                        "time": None,
                        "date": None,
                        "business_id": found_biz
                    }

            # Caso 3: Asistente preguntó la hora (flujo de agendamiento pendiente) (V3.9)
            elif any(p in content.lower() for p in [
                "a qué hora te gustaría agendar", "a qué hora", "qué hora te gustar",
                "hay plena disponibilidad", "con gusto te ayudo a agendar",
                "cuándo te gustaría", "para qué fecha", "¿a qué hora", "que hora te"
            ]):
                biz_id_match = re.search(r'\[(?:BIZ|ID|TAG):(\d+)\]', content)
                biz_id = int(biz_id_match.group(1)) if biz_id_match else None
                
                if target_biz_id and biz_id and biz_id != target_biz_id:
                    continue

                # Prioridad 1: [SERVICIO:X] en el propio mensaje del asistente
                _srv_anchor_match = re.search(r'\[SERVICIO:([^\]]+)\]', content, re.IGNORECASE)
                if biz_id and _srv_anchor_match:
                    _srv_from_anchor = _srv_anchor_match.group(1).strip()
                    logger.info(f"DEBUG: Servicio desde anchor del asistente: '{_srv_from_anchor}' (BIZ={biz_id})")
                    return {
                        "service_name": _srv_from_anchor,
                        "time": None,
                        "date": None,
                        "business_id": biz_id
                    }
                # Prioridad 2: Buscar en mensajes de usuario
                if biz_id:
                    _srv_from_user = None
                    # Regex amplio: captura "para X", "de X", "servicio X", "agendar en X", "agendar X"
                    _srv_re = re.compile(
                        r"(?:agendar(?:\s+en)?|para|de|el servicio de|servicio|quiero un|quiero una|quiero)\s+"
                        r"(?:el|la|un|una|los|las)?\s*"
                        r"([a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\s]{3,35}?)"
                        r"(?:\s+a\s+las|\s+\d|\s*$)",
                        re.IGNORECASE
                    )
                    for _um in reversed(messages):
                        if _um.get("role") == "user":
                            _ut = (_um.get("content") or "").lower()
                            _srv_m = _srv_re.search(_ut)
                            if _srv_m:
                                _cand = _srv_m.group(1).strip()
                                _bad = ["hoy", "manana", "ma\u00f1ana", "cali", "popayan", "bogota",
                                        "medellin", "tarde", "noche", "madrugada", "este", "ese", "aqui"]
                                if not any(b in _cand for b in _bad) and len(_cand) >= 3:
                                    _srv_from_user = _cand
                                    break
                    logger.info(f"DEBUG: Contexto de booking pendiente. BIZ={biz_id}, srv_from_user={_srv_from_user}")
                    return {
                        "service_name": _srv_from_user,
                        "time": None,
                        "date": None,
                        "business_id": biz_id
                    }
    return None



def _extract_session_user_id(messages: list[dict]) -> str | None:
    """Lee el ID de usuario que el context builder incrusta en el system prompt."""
    try:
        content = messages[0].get("content") or ""
        match = re.search(r"ID del usuario autenticado:\s*(.+)", content)
        if match:
            return match.group(1).strip()
    except Exception as exc:
        logger.warning(f"No se pudo leer user_id de sesión: {exc}")
    return None


def _extract_session_today(messages: list[dict]) -> date:
    """Extrae la fecha base de la sesión para resolver expresiones relativas."""
    try:
        content = messages[0].get("content") or ""
        match = re.search(r"Fecha de hoy:\s*(\d{4}-\d{2}-\d{2})", content)
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except Exception as exc:
        logger.warning(f"No se pudo leer la fecha base de sesión: {exc}")
    return datetime.now().date()


def _get_recent_user_messages(messages: list[dict], limit: int = 3) -> list[str]:
    """Toma los ultimos mensajes del usuario para completar datos omitidos por el LLM."""
    recent: list[str] = []
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = (msg.get("content") or "").strip()
        if content:
            recent.append(content)
        if len(recent) >= limit:
            break
    return recent


def _extract_tastes_from_history(messages: list[dict]) -> str:
    """
    Analiza el historial para extraer categorías o temas de interés recurrentes.
    """
    history_text = " ".join([m.get("content", "") for m in messages if m.get("role") == "user"])
    history_text = history_text.lower()
    
    # Categorías conocidas (mapeo rápido)
    from orchestrator.intent_router import _CATEGORY_KEYWORDS
    
    tastes = []
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in history_text for kw in kws):
            tastes.append(cat)
            
    return tastes[0] if tastes else ""


# _looks_like_iso_date, _looks_like_time_24h, _format_time_24h,
# _parse_date_candidate, _parse_time_candidate
# → Now imported from tools.shared.utils (see top of file).


def _resolve_schedule_datetime(messages: list[dict], tool_args: dict) -> tuple[str, str]:
    """Normaliza fecha/hora preferidas desde args del LLM o el historial reciente."""
    base_date = _extract_session_today(messages)
    recent_user_messages = _get_recent_user_messages(messages)
    candidates: list[str] = []

    for key in ("preferred_date", "preferred_time", "date", "time", "fecha", "hora"):
        value = tool_args.get(key)
        if value:
            candidates.append(str(value))

    candidates.extend(recent_user_messages)
    if len(recent_user_messages) > 1:
        candidates.append(" ".join(reversed(recent_user_messages)))

    preferred_date = ""
    preferred_time = ""

    for candidate in candidates:
        if not preferred_date:
            preferred_date = _parse_date_candidate(candidate, base_date) or ""
        if not preferred_time:
            preferred_time = _parse_time_candidate(candidate) or ""
        if preferred_date and preferred_time:
            break

    raw_date = str(tool_args.get("preferred_date") or "").strip()
    raw_time = str(tool_args.get("preferred_time") or "").strip()

    if not preferred_date and _looks_like_iso_date(raw_date):
        preferred_date = raw_date
    if not preferred_time and _looks_like_time_24h(raw_time):
        preferred_time = raw_time

    return preferred_date, preferred_time


def _build_schedule_clarification(property_id: str, preferred_date: str, preferred_time: str) -> str:
    if not str(property_id or "").strip():
        return "Necesito saber cuál propiedad quieres visitar antes de agendarla."
    if not preferred_date and not preferred_time:
        return "Para agendar la visita necesito la fecha y la hora que prefieres."
    if not preferred_date:
        return "Para agendar la visita necesito la fecha que prefieres."
    return "Para agendar la visita necesito la hora que prefieres."


def _match_property_id_in_reply(reply: str, properties: list[dict]) -> int | None:
    """
    Intenta identificar qué propiedad mencionó la IA en su respuesta de texto.
    Estrategia en cascada: ancla canónica → título → precio → fuzzy → fallback.
    """
    reply_norm = _normalize(reply)

    match = re.search(r"\[ID:\s*(\d+)\]", reply, re.IGNORECASE)
    if match:
        return int(match.group(1))

    for prop in properties:
        title_norm = _normalize(prop.get("title") or "")
        if title_norm and title_norm in reply_norm:
            logger.info(f"ID MATCH by title: '{prop.get('title')}' → ID {prop.get('id')}")
            return prop.get("id")

    reply_digits = reply_norm.replace(".", "").replace(",", "")
    for prop in properties:
        price_str = str(int(float(prop.get("price") or 0))) if prop.get("price") else ""
        if price_str and price_str in reply_digits:
            logger.info(f"ID MATCH by price: {prop.get('price')} → ID {prop.get('id')}")
            return prop.get("id")

    generic_words = {"apartamento", "estudio", "casa", "bogota", "medellin", "local", "oficina"}
    for prop in properties:
        title_norm = _normalize(prop.get("title") or "")
        parts = [
            word for word in re.split(r"[\s\-,]+", title_norm)
            if len(word) > 3 and word not in generic_words
        ]
        hits = sum(1 for part in parts if part in reply_norm)
        if hits >= 2:
            logger.info(f"ID FUZZY MATCH: '{prop.get('title')}' (hits={hits}) → ID {prop.get('id')}")
            return prop.get("id")

    if len(properties) == 1:
        logger.info(f"ID FALLBACK (single result): ID {properties[0].get('id')}")
        return properties[0].get("id")

    logger.warning("ID MATCH: no se pudo identificar la propiedad en la respuesta.")
    return None


def _inject_ids_into_titles(properties: list[dict]) -> list[dict]:
    """Inyecta el ancla [ID: X] en el título para el siguiente turno."""
    for prop in properties:
        prop_id = prop.get("id")
        title = prop.get("title") or ""
        if prop_id and f"[ID: {prop_id}]" not in title:
            prop["title"] = f"{title} [ID: {prop_id}]"
    return properties


def _resolve_personality(project_config: dict) -> str:
    """Resuelve el nombre de la personalidad activa desde project_config."""
    name = (project_config.get("assistant_name") or "Lyra").lower()
    return name if name in ("lyra", "nexo") else "lyra"


def _resp(project_config: dict, conv_id: str, intent: str, scenario: str = "default", variables: dict | None = None) -> str | None:
    """Atajo para generate_response con resolución automática de personalidad."""
    personality = _resolve_personality(project_config)
    logger.info(f"RESOLVING RESPONSE | Intent: {intent} | Personality: {personality}")
    return generate_response(conv_id, personality, intent, scenario, variables)


def _local_default_reply(user_text: str) -> str:
    """
    Qué decir cuando no hay nada preparado y no se sale a un modelo.

    Es el último recurso del modo local: no se sabe qué quiere el usuario, así
    que se le pregunta en vez de inventarle una intención.
    """
    if not (user_text or "").strip():
        return "¿En qué te puedo ayudar?"
    return (
        "No estoy seguro de haberte entendido. ¿Buscas un negocio, quieres ver "
        "servicios o prefieres agendar una cita?"
    )


async def run_agent_loop(
    engine,
    messages: list[dict],
    project_id: str,
    project_config: dict,
    auth_header: str | None = None,
    user_data: dict | None = None,
    max_iterations: int = 5,
    user_lat: float | None = None,
    user_lng: float | None = None,
    active_city: str | None = None,
    active_company_id: str | None = None,
    conversation_id: str = "",
    tool_registry: ToolRegistry = None,
    local_intent: dict | None = None,
    final_data: dict | None = None,
) -> dict:
    """
    Main agent loop. Orchestrates interceptors, LLM calls, and tool execution.
    """
    # 1. Initialize result state
    if final_data is None:
        final_data = {
            "reply": "",
            "properties": [],
            "filters_applied": {},
            "map_center": None,
            "voice_action": None,
            "voice_action_payload": None,
            "needs_clarification": False,
            "clarification_question": None,
        }

    # Build shared context for tools and interceptors
    user_text = messages[-1]["content"] if messages and messages[-1]["role"] == "user" else ""
    
    context = {
        "project_id": project_id,
        "project_config": project_config,
        "auth_header": auth_header,
        "user_data": user_data,
        "user_lat": user_lat,
        "user_lng": user_lng,
        "active_city": active_city,
        "active_company_id": active_company_id,
        "conversation_id": conversation_id,
        "final_data": final_data,
        "messages": messages,
        "user_text": user_text,
        "_resp_func": _resp # Pass response helper
    }

    # 2. PRE-LLM INTERCEPTION (Intent Routing & Local Heuristics)
    # This step handles many queries without calling the LLM.
    if local_intent is None:
        from orchestrator.intent_router import detect_intent
        from core.semantic import ConversationState
        # Los canales que entran por aquí sin pasar por ChatService (WhatsApp,
        # voz) también merecen el contexto que la conversación ya estableció.
        intent_result = detect_intent(
            user_text,
            project_id=project_id,
            mentioned_city=active_city,
            current_context={
                "semantic_state": final_data.get(ConversationState.STORAGE_KEY),
                "role": (user_data or {}).get("role"),
                "active_company_id": active_company_id,
            },
        )
    else:
        intent_result = local_intent
    intent_name = intent_result.get("intent")
    intent_args = intent_result.get("args", {})

    intercepted = await interceptor_manager.run_pre_llm_interceptors(
        project_id, intent_name, intent_args, context
    )
    if intercepted:
        logger.info(f"Interceptor bypass for intent: {intent_name}")
        # Merge results into final_data if needed
        if "final_data" in intercepted:
            final_data.update(intercepted["final_data"])
        return {**intercepted, "final_data": final_data}

    # 3. LLM REASONING LOOP
    # NOTA: para nexiservice el registry viene vacío —`tools/nexiservice.py` no
    # declara SCHEMAS—, así que el modelo responde en texto y son los
    # interceptores quienes ejecutan las capacidades. No se cae aquí a las
    # herramientas del YAML: están declaradas sin parámetros y sin registrar, de
    # modo que ofrecérselas al modelo sólo produciría llamadas que fallan.
    tools_schema = tool_registry.get_schemas() if tool_registry else project_config.get("tools", [])

    # Modo local: no se sale a ningún modelo externo. Si algo llegó hasta aquí
    # sin respuesta, se usa la que dejó preparada el interceptor antes que
    # colgarse de una API que puede no estar.
    from core.config import settings
    if not settings.LLM_EXTERNAL_ENABLED:
        fallback = final_data.pop("_fallback_reply", None)
        final_data["reply"] = fallback or _local_default_reply(user_text)
        logger.info("Modo local: respuesta resuelta sin modelo externo.")
        return {"final_data": final_data, "reply": final_data["reply"]}

    for iteration in range(max_iterations):
        logger.info(f"Agent iteration {iteration + 1}/{max_iterations}")

        try:
            if tools_schema:
                result = engine.generate_with_tools(messages, tools_schema)
            else:
                result = {"type": "text", "content": engine.generate(messages)}
        except LLMUnavailable as e:
            # El modelo no respondió. Se le dice al usuario algo humano y, sobre
            # todo, NO se le entrega el texto del error: acabaría impreso en el
            # chat y leído en voz alta.
            logger.error("LLM no disponible (%s): %s", e.status_code, e.reason)
            # Si el turno era conversacional, hay una respuesta preparada que
            # sirve perfectamente: el usuario recibe un saludo, no una disculpa.
            fallback = final_data.pop("_fallback_reply", None)
            final_data["reply"] = fallback or (
                "Ahora mismo no puedo responderte con normalidad. "
                "Dame un momento e inténtalo de nuevo."
                if e.is_quota else
                "Tuve un problema técnico al procesar tu mensaje. ¿Lo intentamos otra vez?"
            )
            final_data["llm_error"] = True
            # Los resultados de búsquedas anteriores no acompañan a un error:
            # mostrar tarjetas de negocios bajo un mensaje de fallo confunde.
            final_data["properties"] = []
            final_data["voice_action"] = None
            final_data["voice_action_payload"] = None
            return {"final_data": final_data, "reply": final_data["reply"]}
        except Exception as e:
            logger.error("Error inesperado en el bucle del agente: %s", e, exc_info=True)
            final_data["reply"] = "Lo siento, tuve un problema al procesar tu solicitud. ¿Podrías intentarlo de nuevo?"
            final_data["llm_error"] = True
            final_data["properties"] = []
            return {"final_data": final_data, "reply": final_data["reply"]}

        # Case A: LLM responded with Text
        if result["type"] == "text":
            final_data["reply"] = result["content"]
            final_data.pop("_fallback_reply", None)
            break

        # Case B: LLM requested a Tool Call
        if result["type"] == "tool_call":
            tool_name = result["tool"]
            tool_args = dict(result.get("args") or {})
            tool_call_id = result.get("id", f"call_{tool_name}")

            logger.info(f"Tool Call: {tool_name} | Args: {tool_args}")

            # Pre-Execution Interception (Arg patching / Guards)
            await interceptor_manager.run_pre_execution_interceptors(
                tool_name, tool_args, context
            )

            # Execution via Registry
            if tool_registry:
                tool_output = await tool_registry.execute(tool_name, tool_args, context)
            else:
                tool_output = {"success": False, "error": f"Tool '{tool_name}' not in registry"}

            # Post-Execution Interception (UI state sync / Map management)
            await interceptor_manager.run_post_execution_interceptors(
                tool_name, tool_args, tool_output, context
            )
            logger.info(f"Tool Output: {tool_output}")

            # Record interaction in history in OpenAI-compatible format
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_args, ensure_ascii=False)
                    }
                }]
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(tool_output, ensure_ascii=False),
            })

            # Check for early exit (e.g., if a tool/interceptor already set the final reply)
            if final_data.get("reply") or final_data.get("needs_clarification"):
                break

    return {"final_data": final_data, "reply": final_data.get("reply")}
