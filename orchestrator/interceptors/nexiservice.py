"""
orchestrator/interceptors/nexiservice.py — REFACTORED with Explicit State Machine

CAMBIOS PRINCIPALES vs versión anterior:
─────────────────────────────────────────
1. StateMachine explícita: cada conversación tiene un estado persistido en final_data
2. Estado WAITING_FOR_NAME tiene prioridad ABSOLUTA sobre intent detection
3. Recovery de servicio mejorado: _recover_pending_service_from_history()
4. Guard anticontaminación: el nombre nunca puede ser texto del asistente
5. Transiciones de estado claras y auditables en logs
"""

import logging
import re
from typing import Optional, Dict, Any, List
from orchestrator.interceptors.helpers import (
    _normalize,
    _is_generic_query,
    _find_anchored_id_in_messages,
    _recover_last_businesses_from_history,
    _recover_last_search_args_from_history,
    _recover_last_reservation_context_from_history,
)

logger = logging.getLogger("lyra.interceptors.nexiservice")


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════════════

class BookingState:
    """
    Estados posibles del flujo de reserva.
    El estado se persiste en final_data['_booking_state'].
    """
    IDLE              = "idle"               # Sin flujo activo
    WAITING_TIME      = "waiting_time"       # Tenemos negocio y servicio, falta hora
    WAITING_SERVICE   = "waiting_service"    # Tenemos negocio y hora, falta servicio
    WAITING_NAME      = "waiting_name"       # BLOQUEANTE: esperando nombre del reservante
    WAITING_DATE      = "waiting_date"       # Tenemos hora y servicio, falta fecha
    WAITING_PROF      = "waiting_prof"       # Hay múltiples profesionales, el usuario elige
    COMPLETE          = "complete"           # Reserva confirmada


def get_booking_state(final_data: Dict) -> str:
    return final_data.get("_booking_state", BookingState.IDLE)


def set_booking_state(final_data: Dict, state: str) -> None:
    prev = final_data.get("_booking_state", BookingState.IDLE)
    final_data["_booking_state"] = state
    logger.info("[STATE] %s → %s", prev, state)


def clear_booking_state(final_data: Dict) -> None:
    set_booking_state(final_data, BookingState.IDLE)
    for key in ("_pending_biz_id", "_pending_service", "_pending_time",
                "_pending_date", "_pending_prof", "reservation_name",
                "booking_time", "booking_date", "booking_service"):
        final_data.pop(key, None)


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. HELPERS DE VALIDACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

# Palabras que NUNCA pueden ser un nombre de persona
_NOT_A_NAME: frozenset = frozenset({
    # Afirmaciones
    "si", "sí", "yes", "ok", "claro", "correcto", "exacto", "dale", "bueno",
    "perfecto", "listo", "de acuerdo", "va", "ese", "esa",
    # Negaciones
    "no", "nope", "negativo",
    # Temporalidad
    "hoy", "mañana", "manana", "ayer", "ahora", "luego", "despues", "después",
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
    # Acciones comunes en el chatbot
    "reservar", "reserva", "agendar", "agenda", "cita", "servicio", "servicios",
    "menu", "menú", "cancelar", "confirmar", "volver", "salir",
    # Saludos
    "hola", "buenas", "buenos", "buen", "gracias",
})

# Frases del ASISTENTE que no deben confundirse con nombres
_ASSISTANT_PHRASES: tuple = (
    "quién la realizamos",
    "quien la realizamos",
    "a tu nombre",
    "nombre completo",
    "indícame",
    "indicame",
    "por favor",
)

_AFFIRMATIVE: frozenset = frozenset({
    "si", "sí", "yes", "ok", "claro", "correcto", "así es",
    "exacto", "ese", "esa", "dale", "va", "bueno",
})


def _is_valid_name(candidate: Optional[str]) -> bool:
    """
    Verifica que el candidato sea un nombre humano real.
    Retorna False si es None, demasiado corto, una palabra reservada
    o una frase del asistente.
    """
    if not candidate:
        return False
    c = candidate.strip()
    if len(c) < 2:
        return False
    c_norm = _normalize(c)

    # Frase del asistente filtrada literalmente
    for phrase in _ASSISTANT_PHRASES:
        if phrase in c_norm:
            return False

    # Palabra reservada exacta o contenida
    for reserved in _NOT_A_NAME:
        if c_norm == reserved or (len(reserved) > 4 and reserved in c_norm):
            return False

    # Evitar capturar ítems de lista (ej: "• servicio") o opciones numeradas
    if c.startswith('•') or re.match(r'^\d+[\.\)-]\s', c):
        return False

    # Números solos, horas, fechas
    if re.match(r"^\d{1,2}[:h]\d{0,2}(\s?(am|pm))?$", c_norm):
        return False
    if re.match(r"^\d{1,2}\s?(am|pm)$", c_norm):
        return False

    # Si parece una oración muy larga (más de 4 palabras) y no tiene mayúsculas de nombre propio
    words = c.split()
    if len(words) > 4:
        # Si ninguna palabra empieza por Mayúscula (excepto la primera tal vez)
        upper_words = [w for w in words[1:] if w[0].isupper()]
        if not upper_words:
            return False

    return True


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. RECOVERY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _recover_booking_datetime_from_history(messages: List[Dict]) -> Dict:
    """Extrae hora y fecha de los mensajes del asistente."""
    TIME_PATTERN = re.compile(r"\b(\d{2}:\d{2})\b")
    DATE_KEYWORDS = {
        "mañana": "tomorrow",
        "manana": "tomorrow",
        "hoy": "today",
        "pasado mañana": "day_after_tomorrow",
    }
    result = {"time": None, "date": None}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        time_match = TIME_PATTERN.search(content)
        if time_match:
            result["time"] = time_match.group(1)
        content_lower = content.lower()
        for keyword, value in DATE_KEYWORDS.items():
            if keyword in content_lower:
                result["date"] = value
                break
    return result


def _recover_pending_service_from_history(messages: List[Dict]) -> Optional[str]:
    """
    Recupera el nombre del servicio que el usuario seleccionó ANTES de que se
    le pidiera el nombre. Busca en el penúltimo mensaje del usuario que no sea
    un nombre (mensaje anterior al que proporcionó el nombre).

    También busca en mensajes del asistente que hayan confirmado un servicio
    (p.ej. "Tengo disponibilidad... [SERVICIO:tres leches]").
    """
    # 1. Buscar anchor [SERVICIO:xxx] en mensajes del asistente
    SERVICE_ANCHOR = re.compile(r"\[SERVICIO:([^\]]+)\]", re.IGNORECASE)
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            match = SERVICE_ANCHOR.search(msg.get("content", ""))
            if match:
                return match.group(1).strip()

    # 2. Buscar en mensajes del asistente la frase "tu **<Servicio>**"
    SRV_IN_REPLY = re.compile(
        r"tu\s+\*\*([^*]{2,60})\*\*", re.IGNORECASE
    )
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            match = SRV_IN_REPLY.search(msg.get("content", ""))
            if match:
                candidate = match.group(1).strip()
                # Excluir cosas como "nombre completo", "hora", etc.
                if _normalize(candidate) not in _NOT_A_NAME:
                    return candidate

    # 3. Buscar en mensajes del usuario: el mensaje justo ANTES del nombre
    #    El patrón es: usuario elige servicio → asistente pide nombre → usuario da nombre
    #    Así que buscamos user messages en orden inverso y tomamos el que no sea nombre válido
    user_msgs = [m for m in messages if m.get("role") == "user"]
    for msg in reversed(user_msgs):
        text = (msg.get("content") or "").strip()
        text_norm = _normalize(text)
        # Si es corto (1-3 palabras) y no es fecha/hora/afirmación, podría ser el servicio
        words = text.split()
        if 1 <= len(words) <= 4 and text_norm not in _NOT_A_NAME:
            # No parece un nombre (los nombres suelen ser Nombre Apellido con mayúsculas)
            if not re.match(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+$", text):
                return text

    return None


def _extract_name_from_messages(messages: List[Dict]) -> Optional[str]:
    """
    Busca el nombre en el historial:
    1. Respuesta directa después de que el asistente pidió el nombre
    2. Patrones directos (Me llamo X, Mi nombre es X)
    """
    NAME_PATTERNS = [
        re.compile(
            r"(?:me llamo|mi nombre es|soy|hablas con|a nombre de)\s+"
            r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)",
            re.IGNORECASE
        ),
    ]

    # Estrategia 1: respuesta secuencial al asistente preguntando nombre
    ASK_NAME_PHRASES = (
        "nombre completo", "tu nombre", "dime tu nombre",
        "a nombre de quién", "como te llamas", "quién la realizamos",
    )
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            content = (msg.get("content") or "").lower()
            if any(kw in content for kw in ASK_NAME_PHRASES):
                # El siguiente mensaje del usuario es el nombre
                for j in range(i + 1, len(messages)):
                    next_msg = messages[j]
                    if next_msg.get("role") == "user":
                        text = next_msg.get("content", "").strip()
                        if _is_valid_name(text):
                            return text
                        break

    # Estrategia 2: patrones directos
    for msg in reversed(messages):
        if msg.get("role") == "user":
            text = (msg.get("content") or "").strip()
            for pattern in NAME_PATTERNS:
                m = pattern.search(text)
                if m:
                    candidate = m.group(1).strip()
                    if _is_valid_name(candidate):
                        return candidate

    return None


def _extract_confirmed_name_from_assistant(messages: List[Dict]) -> Optional[str]:
    """Si el asistente preguntó '¿La reserva irá a tu nombre (Juan)?', extrae 'Juan'."""
    PAREN_NAME = re.compile(r"\(([^)]{3,50})\)")
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if "reserva irá a tu nombre" in content.lower():
                match = PAREN_NAME.search(content)
                if match:
                    return match.group(1).strip()
    return None


def _is_awaiting_service_selection(messages: List[Dict]) -> bool:
    """Retorna True si el último mensaje del asistente presentó una lista de servicios."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "").lower()
            return any(kw in content for kw in (
                "cuál servicio deseas",
                "qué servicio deseas",
                "escribe el nombre del servicio",
                "elige el servicio",
                "selecciona el servicio",
            ))
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# § 4. ANTI-HALLUCINATION GUARD
# ═══════════════════════════════════════════════════════════════════════════════

def _assert_data_from_db(data: Any, entity_label: str = "datos") -> Optional[str]:
    if data is None:
        return f"No encontré {entity_label} en la base de datos."
    if isinstance(data, list) and len(data) == 0:
        return f"No hay {entity_label} registrados en este momento."
    if isinstance(data, dict) and not data.get("success", True):
        return data.get("message", f"Error al consultar {entity_label}.")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# § 5. SUBCATEGORY FILTER
# ═══════════════════════════════════════════════════════════════════════════════

_SUBCATEGORY_KEYWORDS = {
    "postres": ["postre", "dulce", "torta", "pastel", "helado", "flan",
                "brownie", "galleta", "mousse"],
    "bebidas": ["bebida", "jugo", "trago", "cerveza", "vino", "limonada",
                "cafe", "coctel", "agua", "malteada", "smoothie", "te "],
    "comida_tipica": ["tipic", "regional", "crioll", "tradicional",
                      "autoctono", "ancestral", "casero"],
    "entradas": ["entrada", "aperitivo", "snack", "picada"],
    "platos_fuertes": ["almuerzo", "cena", "plato fuerte", "plato principal",
                       "ejecutivo", "corriente"],
    "menu": [],
}


def _filter_services_by_subcategory(services: List[Dict], subcategory: str) -> List[Dict]:
    if not subcategory or subcategory == "menu":
        return services
    keywords = _SUBCATEGORY_KEYWORDS.get(subcategory, [])
    if not keywords:
        return services
    filtered = []
    for s in services:
        combined = f"{_normalize(s.get('nombre', ''))} {_normalize(s.get('descripcion', '') or '')}"
        if any(kw in combined for kw in keywords):
            filtered.append(s)
    return filtered


# ═══════════════════════════════════════════════════════════════════════════════
# § 6. PRE-LLM INTERCEPTOR (Entry Point)
# ═══════════════════════════════════════════════════════════════════════════════

async def pre_llm_interceptor(project_id, intent_name, args, context):
    if project_id != "nexiservice":
        return None

    messages = context.get("messages", [])
    final_data = context.get("final_data", {})
    user_data = context.get("user_data", {})
    project_config = context.get("project_config", {})
    conversation_id = context.get("conversation_id", "")
    user_text = context.get("user_text", "").strip()
    _resp = context.get("_resp_func")

    current_state = get_booking_state(final_data)
    logger.info(
        "[INTERCEPTOR] intent=%s | state=%s | user_text='%s'",
        intent_name, current_state, user_text
    )

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORIDAD ABSOLUTA: Estado WAITING_FOR_NAME
    # Si estamos esperando un nombre, CUALQUIER input del usuario es el nombre.
    # No importa lo que el intent router haya detectado.
    # ══════════════════════════════════════════════════════════════════════════
    if current_state == BookingState.WAITING_NAME:
        return await _handle_waiting_name_state(
            user_text=user_text,
            messages=messages,
            user_data=user_data,
            final_data=final_data,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORIDAD ALTA: Redirigir navigate_to_company → request_appointment
    # cuando el usuario está seleccionando un servicio de la lista
    # ══════════════════════════════════════════════════════════════════════════
    if intent_name == "navigate_to_company" and _is_awaiting_service_selection(messages):
        service_candidate = args.get("business_name") or user_text
        dt_ctx = _recover_booking_datetime_from_history(messages)

        args["service_name"] = service_candidate
        args["business_name"] = None
        args["business_id"] = (
            final_data.get("selected_business_id")
            or final_data.get("_pending_biz_id")
        )
        args["time"] = args.get("time") or final_data.get("booking_time") or dt_ctx.get("time")
        args["date"] = args.get("date") or final_data.get("booking_date") or dt_ctx.get("date")
        args["reservation_name"] = final_data.get("reservation_name")
        intent_name = "request_appointment"
        logger.info(
            "[INTERCEPTOR] navigate_to_company → request_appointment (service selection). srv='%s'",
            service_candidate
        )

    # ══════════════════════════════════════════════════════════════════════════
    # ROUTING ESTÁNDAR
    # ══════════════════════════════════════════════════════════════════════════

    # — Conversación general / identidad / capacidades —
    if intent_name in ("greeting", "conversation", "identity", "capabilities"):
        return _handle_conversational(intent_name, project_config, conversation_id, _resp, final_data, context)

    if intent_name == "farewell":
        return _handle_conversational("farewell", project_config, conversation_id, _resp, final_data, context)

    # — Flujo de reserva —
    if intent_name == "request_appointment":
        return await _handle_request_appointment(args, messages, user_data, final_data)

    if intent_name == "confirm_appointment":
        return await _handle_confirm_appointment(args, messages, user_data, final_data)

    # — Búsqueda y navegación —
    if intent_name == "search_businesses":
        return await _handle_search_businesses(args, context, final_data)

    if intent_name == "get_business_services":
        return await _handle_get_business_services(args, messages, context, final_data)

    if intent_name == "navigate_to_company":
        return await _handle_navigate_to_company(args, context, final_data)

    if intent_name == "get_business_reviews":
        return await _handle_get_business_reviews(args, messages, final_data)

    if intent_name == "get_business_availability":
        return await _handle_get_business_availability(args, messages, final_data)

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# § 7. HANDLER: WAITING_FOR_NAME STATE (El fix central)
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_waiting_name_state(
    user_text: str,
    messages: List[Dict],
    user_data: Dict,
    final_data: Dict,
) -> Dict:
    """
    Maneja el estado WAITING_FOR_NAME con prioridad absoluta.

    Este handler se activa ANTES del intent router, garantizando que
    cualquier texto del usuario sea tratado como nombre.

    Flujo:
    1. Validar que el texto sea un nombre aceptable
    2. Persistir el nombre en final_data
    3. Recuperar el contexto de reserva pendiente (negocio, servicio, hora, fecha)
    4. Continuar el flujo de confirm_appointment
    5. Transicionar el estado correctamente
    """
    logger.info("[STATE:WAITING_NAME] Received input: '%s'", user_text)

    # Paso 1: Validar el nombre
    if not _is_valid_name(user_text):
        # El texto no parece un nombre válido — pedir de nuevo con más contexto
        logger.warning("[STATE:WAITING_NAME] Rejected invalid name: '%s'", user_text)
        return {
            "reply": (
                "Necesito tu nombre completo para registrar la reserva. "
                "Por favor, escribe tu nombre y apellido (ej: María González)."
            ),
            "final_data": final_data,
        }

    # Paso 2: Persistir nombre
    reservation_name = user_text.strip()
    final_data["reservation_name"] = reservation_name
    logger.info("[STATE:WAITING_NAME] Name accepted: '%s'", reservation_name)

    # Paso 3: Recuperar contexto de reserva pendiente
    biz_id   = final_data.get("_pending_biz_id") or final_data.get("selected_business_id")
    service  = final_data.get("_pending_service") or _recover_pending_service_from_history(messages)
    time_val = final_data.get("_pending_time") or final_data.get("booking_time")
    date_val = final_data.get("_pending_date") or final_data.get("booking_date")

    # También intentar recovery desde historial de mensajes si faltan datos
    if not time_val or not date_val:
        dt_ctx = _recover_booking_datetime_from_history(messages)
        time_val = time_val or dt_ctx.get("time")
        date_val = date_val or dt_ctx.get("date")

    logger.info(
        "[STATE:WAITING_NAME] Recovered context — biz=%s srv='%s' time=%s date=%s",
        biz_id, service, time_val, date_val
    )

    # Paso 4: ¿Tenemos suficiente contexto para confirmar?
    if not biz_id:
        # Caso muy raro: perdimos el negocio
        set_booking_state(final_data, BookingState.IDLE)
        return {
            "reply": (
                f"Gracias, **{reservation_name}**. ¿En qué negocio te gustaría agendar tu cita?"
            ),
            "final_data": final_data,
        }

    if not service:
        # Tenemos nombre pero no sabemos el servicio — pedir servicio
        set_booking_state(final_data, BookingState.WAITING_SERVICE)
        final_data["_pending_biz_id"] = biz_id
        return {
            "reply": (
                f"Perfecto, **{reservation_name}**. ¿Qué servicio deseas agendar?"
            ),
            "final_data": final_data,
        }

    if not time_val:
        set_booking_state(final_data, BookingState.WAITING_TIME)
        return {
            "reply": (
                f"Perfecto, **{reservation_name}**. ¿A qué hora deseas agendar tu **{service}**?"
            ),
            "final_data": final_data,
        }

    # Paso 5: Tenemos todo — confirmar la reserva
    set_booking_state(final_data, BookingState.COMPLETE)

    # RE-VERIFICAR: ¿Seguimos necesitando el catálogo?
    # Si ya tenemos service y time, la tool confirm_appointment debería ser llamada.
    tool_output = await _call_confirm_appointment(
        business_id=biz_id,
        service_name=service,
        time=time_val,
        date=date_val,
        reservation_name=reservation_name,
        user_data=user_data,
    )

    if tool_output.get("success"):
        clear_booking_state(final_data)
        set_booking_state(final_data, BookingState.IDLE)
    else:
        # Si falló, volver a IDLE para no quedar trabado
        clear_booking_state(final_data)
        logger.error("[STATE:WAITING_NAME] confirm_appointment failed: %s", tool_output.get("message"))

    return {
        "reply": tool_output.get("message"),
        "voice_action": "navigate" if tool_output.get("url") else None,
        "voice_action_payload": (
            {"url": tool_output.get("url")} if tool_output.get("url") else None
        ),
        "final_data": {
            **final_data,
            "last_booking_status": tool_output.get("success"),
        },
    }


async def _call_confirm_appointment(
    business_id, service_name, time, date, reservation_name, user_data
) -> Dict:
    """Thin wrapper para confirm_appointment con logging."""
    from tools.nexiservice import confirm_appointment
    logger.info(
        "[CONFIRM] biz=%s srv='%s' time=%s date=%s name='%s'",
        business_id, service_name, time, date, reservation_name
    )
    return await confirm_appointment(
        business_id=business_id,
        service_name=service_name,
        time=time,
        date=date,
        reservation_name=reservation_name,
        user_data=user_data,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# § 8. HANDLER: request_appointment (Refactorizado)
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_request_appointment(args: Dict, messages: List, user_data: Dict, final_data: Dict) -> Dict:
    from tools.nexiservice import request_appointment

    biz_id       = args.get("business_id")
    biz_name     = args.get("business_name")
    time_arg     = args.get("time")
    date_arg     = args.get("date")
    service_arg  = args.get("service_name")
    prof_name    = args.get("professional_name")

    # ── 1. Resolver usuario ──────────────────────────────────────────────────
    ext_id = user_data.get("external_user_id", "")
    is_anon = (
        not ext_id
        or str(ext_id) in ("user_client_demo", "unknown", "guest")
        or str(ext_id).startswith("anon_")
    )

    if not is_anon:
        res_name = (
            final_data.get("reservation_name")
            or user_data.get("name")
            or user_data.get("full_name")
            or user_data.get("nombre")
        )
        if not res_name:
            res_name = await _resolve_logged_user_name(ext_id, user_data)
            if res_name:
                final_data["reservation_name"] = res_name
    else:
        candidate = (
            args.get("reservation_name")
            or final_data.get("reservation_name")
            or _recover_last_reservation_context_from_history(messages).get("reservation_name")
            or _extract_name_from_messages(messages)
        )
        # Guard: afirmaciones no son nombres
        if candidate and _normalize(candidate) in _AFFIRMATIVE:
            candidate = _extract_confirmed_name_from_assistant(messages)
        
        # Si el candidato ya estaba en final_data y es válido, lo respetamos
        if final_data.get("reservation_name") and _is_valid_name(final_data.get("reservation_name")):
            res_name = final_data.get("reservation_name")
        else:
            res_name = candidate if _is_valid_name(candidate) else None

    # ── 2. Context Recovery ──────────────────────────────────────────────────
    if not biz_id and (_is_generic_query(biz_name) or not biz_name):
        biz_id = _find_anchored_id_in_messages(messages)

    if not biz_id or not service_arg or not time_arg or not date_arg:
        res_ctx = _recover_last_reservation_context_from_history(messages)
        dt_ctx  = _recover_booking_datetime_from_history(messages)

        biz_id      = biz_id      or final_data.get("selected_business_id") or final_data.get("_pending_biz_id")
        time_arg    = time_arg    or final_data.get("booking_time")  or res_ctx.get("time")  or dt_ctx.get("time")
        date_arg    = date_arg    or final_data.get("booking_date")  or res_ctx.get("date")  or dt_ctx.get("date")
        service_arg = service_arg or final_data.get("booking_service") or res_ctx.get("service_name")

    logger.info(
        "[REQUEST_APPOINTMENT] biz=%s srv='%s' time=%s date=%s name='%s'",
        biz_id, service_arg, time_arg, date_arg, res_name
    )

    # ── 3. Gate: negocio requerido ───────────────────────────────────────────
    if not biz_id and not biz_name:
        return {"reply": "¿En qué negocio te gustaría agendar tu cita?", "final_data": final_data}

    # ── 4. Persistir contexto en final_data ──────────────────────────────────
    if biz_id:
        final_data["_pending_biz_id"] = biz_id
    if res_name:
        final_data["reservation_name"] = res_name
    if time_arg:
        final_data["_pending_time"]  = time_arg
        final_data["booking_time"]   = time_arg
    if date_arg:
        final_data["_pending_date"]  = date_arg
        final_data["booking_date"]   = date_arg
    if service_arg:
        final_data["_pending_service"] = service_arg
        final_data["booking_service"]  = service_arg

    args["reservation_name"] = res_name

    # ── 5. Llamar a la tool ───────────────────────────────────────────────────
    tool_output = await request_appointment(
        business_id=biz_id,
        business_name=biz_name,
        time=time_arg,
        date=date_arg,
        service_name=service_arg,
        professional_name=prof_name,
        reservation_name=res_name,
        user_data=user_data,
    )

    # ── 6. Manejar respuesta de la tool ──────────────────────────────────────
    message = tool_output.get("message", "")
    needs_input = tool_output.get("needs_input", False)

    # DETECTAR si la tool pidió nombre → activar estado WAITING_FOR_NAME
    _ASKING_NAME_SIGNALS = (
        "nombre completo",
        "a nombre de quién",
        "indícame tu nombre",
        "necesito saber a nombre",
    )
    if needs_input and any(sig in message.lower() for sig in _ASKING_NAME_SIGNALS):
        set_booking_state(final_data, BookingState.WAITING_NAME)
        # Asegurarnos que el contexto pendiente esté guardado
        if service_arg and not final_data.get("_pending_service"):
            final_data["_pending_service"] = service_arg
        if time_arg and not final_data.get("_pending_time"):
            final_data["_pending_time"] = time_arg
        if date_arg and not final_data.get("_pending_date"):
            final_data["_pending_date"] = date_arg
        logger.info("[STATE] Transitioning to WAITING_NAME — context saved")

    elif tool_output.get("success") and not needs_input:
        # Reserva confirmada o flujo normal (no espera nombre)
        if tool_output.get("url") == "/perfil/mis-reservas":
            clear_booking_state(final_data)

    # Capturar nombre resuelto por la tool
    resolved_name = tool_output.get("reservation_name") or tool_output.get("user_name")
    if resolved_name and _is_valid_name(resolved_name) and not final_data.get("reservation_name"):
        final_data["reservation_name"] = resolved_name

    return {
        "reply": message,
        "voice_action": "navigate" if tool_output.get("url") else None,
        "voice_action_payload": (
            {"url": tool_output.get("url")} if tool_output.get("url") else None
        ),
        "final_data": {
            **final_data,
            "last_booking_status": tool_output.get("success"),
            "needs_input": needs_input,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# § 9. HANDLERS AUXILIARES (sin cambios estructurales relevantes)
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_conversational(intent_name, project_config, conversation_id, _resp, final_data, context):
    """Maneja intents conversacionales (greeting, farewell, identity, capabilities)."""
    from orchestrator.response_engine import generate_response

    template_map = {
        "identity": "identity",
        "capabilities": "capabilities",
        "greeting": "conversation",
        "conversation": "conversation",
        "farewell": "farewell",
    }
    template = template_map.get(intent_name, "conversation")
    reply = None

    if _resp:
        reply = _resp(project_config, conversation_id, template)
    else:
        personality = (
            context.get("personality")
            or project_config.get("assistant_name", "lyra").lower()
        )
        if personality not in ("lyra", "nexo"):
            personality = "lyra"
        reply = generate_response(
            conversation_id=conversation_id,
            personality=personality,
            intent=template,
            scenario="default",
        )

    defaults = {
        "farewell": "¡Hasta luego! 👋",
        "identity": "Soy Nexo, tu asistente de NexiService.",
        "capabilities": "Puedo ayudarte a buscar negocios y agendar citas.",
    }
    return {
        "reply": reply or defaults.get(intent_name, "¡Hola! ¿En qué puedo ayudarte?"),
        "voice_action": None,
        "final_data": final_data,
    }


async def _handle_navigate_to_company(args: Dict, context: Dict, final_data: Dict) -> Dict:
    from tools.nexiservice import search_businesses

    messages = context.get("messages", [])
    name = args.get("business_name")
    city = args.get("city")

    if not name:
        return {"reply": "¿Qué negocio deseas ver?", "final_data": final_data}

    tool_output = await search_businesses(category=name, city=city)
    if not tool_output.get("success"):
        return None

    businesses = tool_output.get("businesses", [])
    if not businesses:
        return {"reply": f"No encontré '{name}'.", "final_data": final_data}

    b = businesses[0]
    final_data["_last_businesses"] = businesses
    final_data["selected_business"] = b
    final_data["selected_business_id"] = b["id"]
    final_data["_pending_biz_id"] = b["id"]

    reply = (
        f"**{b['name']}**\n"
        f"📍 {b.get('address', 'Sin dirección')}\n\n"
        "¿Deseas ver servicios o reservar?"
        f"\n[BIZ:{b['id']}]"
    )
    return {
        "reply": reply,
        "voice_action": "navigate",
        "voice_action_payload": {"url": f"/empresa/{b['id']}"},
        "final_data": final_data,
    }


async def _handle_confirm_appointment(args: Dict, messages: List, user_data: Dict, final_data: Dict) -> Dict:
    from tools.nexiservice import confirm_appointment

    ext_id  = user_data.get("external_user_id")
    is_anon = not ext_id or str(ext_id).startswith("anon_") or ext_id == "unknown"

    res_name = args.get("reservation_name") or final_data.get("reservation_name")
    if not res_name:
        res_name = _extract_name_from_messages(messages)
    if not res_name:
        res_ctx  = _recover_last_reservation_context_from_history(messages)
        res_name = res_ctx.get("reservation_name")

    if is_anon and not _is_valid_name(res_name):
        # Activar estado WAITING_NAME en lugar de bloquear sin estado
        set_booking_state(final_data, BookingState.WAITING_NAME)
        logger.info("[CONFIRM] Blocking → activating WAITING_NAME state")
        return {
            "reply": "Para confirmar la cita, necesito tu nombre completo.",
            "final_data": {
                **final_data,
                "awaiting_field": "reservation_name",
            },
            "voice_action": "ask_name",
        }

    args["reservation_name"] = res_name
    tool_output = await confirm_appointment(**args, user_data=user_data)

    if tool_output.get("success"):
        clear_booking_state(final_data)

    return {
        "reply": tool_output.get("message"),
        "final_data": {**final_data, "booking_success": tool_output.get("success")},
    }


async def _handle_search_businesses(args: Dict, context: Dict, final_data: Dict) -> Dict:
    from tools.nexiservice import search_businesses

    category = args.get("category")
    city = args.get("city") or context.get("active_city")

    tool_output = await search_businesses(category=category, city=city)
    if not tool_output.get("success"):
        return None

    businesses = tool_output.get("businesses", [])
    final_data["_last_businesses"] = businesses

    if not businesses:
        reply = f"No encontré '{category}' en {city}. ¿Deseas buscar en otra ciudad?"
    elif len(businesses) == 1:
        b = businesses[0]
        reply = f"Encontré **{b['name']}**. ¿Te gustaría ver sus servicios o agendar?"
        final_data["voice_action"] = "map_highlight"
        final_data["voice_action_payload"] = {"business_id": b["id"]}
    else:
        reply = f"Encontré {len(businesses)} opciones de '{category}' en {city}."
        final_data["voice_action"] = "fit_all_businesses"

    return {"reply": reply, "final_data": final_data}


async def _handle_get_business_services(args: Dict, messages: List, context: Dict, final_data: Dict) -> Dict:
    from tools.nexiservice import get_business_services

    biz_name = args.get("business_name")
    biz_id   = args.get("business_id")

    if _is_generic_query(biz_name) and not biz_id:
        biz_id = _find_anchored_id_in_messages(messages)

    tool_output = await get_business_services(business_name=biz_name, business_id=biz_id)
    if not tool_output.get("success"):
        return None

    b_name   = tool_output["business_name"]
    services = tool_output["services"]
    biz_id   = tool_output["business_id"]

    reply = f"### {b_name}\n"
    if not services:
        reply += "No tienen servicios registrados en este momento."
    else:
        for s in services:
            val = s.get("valor", 0)
            reply += f"- **{s['nombre']}**: ${val:,.0f}\n"

    reply += f"\n[BIZ:{biz_id}]"
    return {
        "reply": reply,
        "voice_action": "navigate",
        "voice_action_payload": {"url": f"/empresa/{biz_id}#servicios"},
        "final_data": {
            **final_data,
            "_last_businesses": [{"id": biz_id, "name": b_name}],
        },
    }


async def _handle_get_business_reviews(args: Dict, messages: List, final_data: Dict) -> Dict:
    from tools.nexiservice import get_business_reviews

    biz_id = args.get("business_id") or _find_anchored_id_in_messages(messages)
    tool_output = await get_business_reviews(
        business_id=biz_id, business_name=args.get("business_name")
    )
    if not tool_output.get("success"):
        return None

    reply = f"### Reseñas de {tool_output['business_name']}\n"
    reply += f"Promedio: ⭐ **{tool_output['average_rating']}** ({tool_output['total_reviews']} opiniones)\n\n"
    for r in tool_output.get("reviews", [])[:3]:
        reply += f"- \"{r['comentario']}\" ({r['rating']}⭐)\n"

    return {"reply": reply}


async def _handle_get_business_availability(args: Dict, messages: List, final_data: Dict) -> Dict:
    from tools.nexiservice import get_business_availability

    biz_id = args.get("business_id") or _find_anchored_id_in_messages(messages)
    tool_output = await get_business_availability(
        business_id=biz_id, date=args.get("date")
    )
    return {"reply": tool_output.get("message")}


async def _resolve_logged_user_name(ext_id, user_data: Dict) -> Optional[str]:
    name = (
        user_data.get("name") or user_data.get("full_name")
        or user_data.get("username") or user_data.get("nombre")
        or user_data.get("display_name")
    )
    logger.info("[INTERCEPTOR] user_data keys: %s", list(user_data.keys()))
    if name:
        return name
    try:
        from core.database import get_connection
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.nombre
                    FROM usuario u
                    JOIN tercero t ON u.idpersona = t.id
                    WHERE u.id = %s LIMIT 1
                    """,
                    (ext_id,)
                )
                row = cur.fetchone()
                if row and row.get("nombre"):
                    return row["nombre"]
    except Exception as e:
        logger.warning("[INTERCEPTOR] No pudo resolver nombre: %s", e)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# § 10. POST-EXECUTION INTERCEPTOR
# ═══════════════════════════════════════════════════════════════════════════════

async def post_execution_interceptor(
    tool_name: str,
    args: Dict[str, Any],
    output: Dict[str, Any],
    context: Dict[str, Any],
) -> None:
    final_data = context.get("final_data", {})

    if tool_name == "search_businesses" and output.get("success"):
        final_data["_last_businesses"] = output.get("businesses", [])
        final_data["filters_applied"] = {
            "city": output.get("city"),
            "search": output.get("category"),
        }

    if tool_name == "confirm_appointment":
        if output.get("needs_input"):
            final_data["needs_input"] = True
            final_data["needs_clarification"] = True
            final_data["reply"] = output.get("message")
            final_data["voice_action"] = "request_reservation_name"

        if output.get("action") == "navigate":
            final_data["voice_action"] = "navigate"
            final_data["voice_action_payload"] = {"url": output.get("url")}