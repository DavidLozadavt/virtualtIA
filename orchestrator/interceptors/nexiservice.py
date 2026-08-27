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
from core.semantic.dialogue import next_missing_slot
from core.speech_format import format_price
from core.wording import (
    count_phrase,
    each_one,
    is_feminine as _feminine,
    natural_list,
    pluralize_es,
    one_line as _one_line,
    them,
    user_facing_label,
)
from core.semantic.types import ConceptKind, ConversationState

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
    WAITING_AUTH      = "waiting_auth"       # Todo acordado; falta que el usuario entre
    WAITING_DATE      = "waiting_date"       # Tenemos hora y servicio, falta fecha
    WAITING_PROF      = "waiting_prof"       # Hay múltiples profesionales, el usuario elige
    COMPLETE          = "complete"           # Reserva confirmada


#: Ranura que la herramienta declara estar pidiendo → estado del flujo. Antes el
#: estado se deducía buscando subcadenas en el texto ya redactado, y sólo se
#: llegaba a asignar dentro de la rama del nombre: cuando Lyra preguntaba la
#: hora, el flujo seguía marcado como IDLE y nada recordaba que había una
#: pregunta en el aire.
_SLOT_TO_STATE: Dict[str, str] = {
    "time":              BookingState.WAITING_TIME,
    "date":              BookingState.WAITING_DATE,
    "service_name":      BookingState.WAITING_SERVICE,
    "professional_name": BookingState.WAITING_PROF,
    "reservation_name":  BookingState.WAITING_NAME,
    "business":          BookingState.IDLE,
}

#: Intenciones que continúan una reserva en curso. Cualquier otra es un cambio
#: de tema y cierra la pregunta abierta, pero nunca lo ya acordado.
_CONTINUES_BOOKING = frozenset({
    "request_appointment", "confirm_appointment", "semantic_clarify",
})


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
                "booking_time", "booking_date", "booking_service",
                "_pending_reservation", "_pending_reservation_offered", "needs_auth"):
        final_data.pop(key, None)


# ═══════════════════════════════════════════════════════════════════════════════
# § 1b. SESIÓN DEL USUARIO
# ═══════════════════════════════════════════════════════════════════════════════

#: Identificadores que la aplicación usa cuando NO hay nadie autenticado.
_ANONYMOUS_IDS = frozenset({"", "user_client_demo", "unknown", "guest", "none", "null"})


def is_authenticated(user_data: Dict) -> bool:
    """¿Hay una sesión real detrás de este mensaje?"""
    ext_id = str((user_data or {}).get("external_user_id") or "").strip()
    if not ext_id or ext_id.lower() in _ANONYMOUS_IDS:
        return False
    return not ext_id.startswith("anon_")


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

#: Señales de que las horas de un mensaje son las que NO están libres.
_BUSY_LISTING_MARKERS = ("ocupad", "no disponible", "ya reservad", "agenda llena")


def _recover_booking_datetime_from_history(messages: List[Dict]) -> Dict:
    """
    Extrae hora y fecha de los mensajes del asistente.

    Se saltan los mensajes que enumeran horarios OCUPADOS. Ahí las horas
    significan justo lo contrario de lo que se busca, y tomarlas como la hora
    elegida agendaba la cita encima de una existente: el usuario pedía las 8:30
    y acababa citado a las 07:00, que era el primer hueco ocupado de la lista.
    """
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
        content_lower = content.lower()

        if not any(marker in content_lower for marker in _BUSY_LISTING_MARKERS):
            time_match = TIME_PATTERN.search(content)
            if time_match:
                result["time"] = time_match.group(1)

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

    # Memoria semántica de la conversación: qué se mostró, qué está en foco y
    # qué ranuras de reserva ya se llenaron. Se lee al entrar y se guarda al
    # salir, de modo que el turno siguiente entienda "el segundo" o "ahí".
    sem_state = ConversationState.load(final_data)

    # La empresa que el usuario tiene abierta en pantalla es el contexto por
    # defecto de todo lo que pregunte. Viajaba en el contexto desde siempre,
    # pero sólo llegaba al prompt del modelo: con el modelo apagado, un
    # empresario dentro de su propio panel preguntaba "¿qué servicios tengo?" y
    # Lyra le respondía que de qué negocio hablaba.
    _seed_focus_from_screen(context, final_data, sem_state)

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORIDAD ABSOLUTA: reserva esperando que el usuario entre a su cuenta
    # Si ya inició sesión, se confirma sola: no se le vuelve a preguntar nada.
    # ══════════════════════════════════════════════════════════════════════════
    # Sólo se cierra sola cuando el usuario vuelve A LO SUYO. Antes bastaba
    # cualquier mensaje: quien entraba a su cuenta y escribía "hola, ¿cómo
    # estás?" recibía "tu cita ya ha sido agendada" por una reserva que había
    # dejado a medias días atrás. Una cita no se crea sin que el usuario lo pida
    # en ese turno.
    if current_state == BookingState.WAITING_AUTH and is_authenticated(user_data):
        if intent_name in _CONTINUES_BOOKING:
            return await _resume_pending_reservation(user_data, final_data)
        oferta = _offer_pending_reservation(final_data)
        if oferta:
            return oferta

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
    # EL USUARIO CANCELÓ LA RESERVA: SE TIRA ENTERA
    # ══════════════════════════════════════════════════════════════════════════
    # Distinto de un cambio de tema. Aquí el usuario no se desvía: dice que no
    # quiere agendar. Conservar las ranuras "por si vuelve" era lo que hacía que
    # dos turnos después reapareciera la cita que acababa de rechazar.
    if args.pop("_cancel_booking", False):
        logger.info(
            "[DIÁLOGO] el usuario canceló el agendamiento; se descarta: %s",
            {k: v for k, v in sem_state.booking.items() if v},
        )
        sem_state.clear_booking()
        sem_state.save(final_data)
        clear_booking_state(final_data)

    # ══════════════════════════════════════════════════════════════════════════
    # CAMBIO DE TEMA: SE CIERRA LA PREGUNTA, NO LA RESERVA
    # ══════════════════════════════════════════════════════════════════════════
    # El usuario tiene derecho a preguntar otra cosa en mitad de una reserva
    # ("antes de eso, ¿qué restaurantes hay cerca?"). Lo que caduca es la
    # pregunta abierta, no lo acordado: el negocio, el servicio y la hora siguen
    # en pie para cuando vuelva. Reiniciar el contexto ante cualquier desvío era
    # justo lo que obligaba a repetirlo todo desde el principio.
    if intent_name not in _CONTINUES_BOOKING and sem_state.is_collecting:
        logger.info(
            "[DIÁLOGO] cambio de tema (intent=%s): se cierra la ranura '%s'; "
            "la reserva se conserva: %s",
            intent_name, sem_state.pending_slot, sorted(sem_state.booking),
        )
        sem_state.suspend()
        sem_state.save(final_data)

    # ══════════════════════════════════════════════════════════════════════════
    # ROUTING ESTÁNDAR
    # ══════════════════════════════════════════════════════════════════════════

    # — Comprendido, pero sin correspondencia en el catálogo —
    # Distinto de "no hay resultados": aquí el concepto ni siquiera existe en
    # NexiService, y decírselo así al usuario le permite reformular.
    if intent_name == "semantic_clarify":
        return await _handle_semantic_clarify(args, final_data, sem_state, context)

    # — Órdenes a la interfaz —
    # Son deterministas y no necesitan al modelo. Dependían de que el LLM
    # devolviera la etiqueta correcta, así que con el modelo caído "ver mapa"
    # respondía que no se podía atender.
    if intent_name in _UI_ACTIONS:
        return _handle_ui_action(intent_name, final_data, sem_state)

    # — Conversación general / identidad / capacidades —
    if intent_name in ("greeting", "conversation", "identity", "capabilities"):
        return _handle_conversational(intent_name, project_config, conversation_id, _resp, final_data, context)

    if intent_name == "farewell":
        return _handle_conversational("farewell", project_config, conversation_id, _resp, final_data, context)

    # — Flujo de reserva —
    if intent_name == "request_appointment":
        return await _handle_request_appointment(args, messages, user_data, final_data, sem_state)

    if intent_name == "confirm_appointment":
        return await _handle_confirm_appointment(args, messages, user_data, final_data)

    # — Búsqueda y navegación —
    if intent_name == "search_businesses":
        return await _handle_search_businesses(args, context, final_data, sem_state)

    if intent_name == "get_business_services":
        return await _handle_get_business_services(args, messages, context, final_data, sem_state)

    if intent_name == "navigate_to_company":
        return await _handle_navigate_to_company(args, context, final_data, sem_state)

    if intent_name == "get_business_professionals":
        return await _handle_get_business_professionals(args, messages, final_data, sem_state, context)

    if intent_name == "recommend_businesses":
        return await _handle_recommend_businesses(args, context, final_data, sem_state)

    if intent_name == "get_business_reviews":
        return await _handle_get_business_reviews(args, messages, final_data)

    if intent_name == "get_business_availability":
        return await _handle_get_business_availability(args, messages, final_data)

    # — Capacidades que el router ya sabía nombrar y nadie ejecutaba ─────────
    #
    # Cada una de éstas terminaba en el bucle del agente, que para NexiService
    # no tiene herramientas registradas y (con el modelo externo apagado)
    # responde siempre lo mismo: "no estoy seguro de haberte entendido". Eran
    # peticiones perfectamente comprendidas muriendo en el último paso.
    if intent_name == "fly_to_business":
        return await _handle_fly_to_business(args, context, final_data, sem_state)

    if intent_name == "compare_businesses":
        return await _handle_compare_businesses(args, context, final_data, sem_state)

    if intent_name == "get_business_mission_vision":
        return await _handle_business_identity(args, messages, final_data, sem_state)

    if intent_name == "open_business_web":
        return await _handle_open_business_web(args, messages, final_data, sem_state)

    if intent_name == "get_service_info":
        return await _handle_get_service_info(args, messages, final_data, sem_state)

    if intent_name == "get_professional_info":
        return await _handle_get_professional_info(args, messages, final_data, sem_state)

    if intent_name == "get_general_info":
        return await _handle_general_info(args, context, final_data)

    if intent_name == "admin_navigate":
        return _handle_admin_navigate(args, final_data)

    if intent_name == "set_city_manual":
        return _handle_set_city(args, final_data)

    if intent_name in _GPS_REPLIES:
        return _handle_gps(intent_name, context, final_data)

    # — "Sí", "dale", "vamos" cuando la comprensión no llegó a leerlos ───────
    #
    # Estas dos etiquetas las produce el router por palabras clave, después de
    # la compuerta semántica. Nadie las atendía, así que un "dale" en mitad de
    # una reserva salía por la frase de último recurso.
    if intent_name in ("confirm_general", "confirm_navigation"):
        return await _handle_bare_confirmation(
            intent_name, args, messages, context, user_data, final_data, sem_state
        )

    if intent_name == "spam":
        return {
            "reply": "¿Me lo cuentas otra vez? No logré leer eso.",
            "final_data": final_data,
        }

    # — La comprensión entendió el mensaje pero prefiere conversarlo ─────────
    #
    # Ocurre con "¿y cuál me conviene?", "gracias, buenísimo", "no sé qué
    # buscar": el motor semántico devuelve CONVERSE sin intención concreta. Con
    # el modelo externo apagado eso caía en la frase de último recurso del bucle
    # —"¿buscas un negocio, servicios o agendar?"— justo después de que el
    # usuario acabara de decir qué quería. Conversar es una capacidad de Lyra,
    # no un hueco: se atiende como tal, con el hilo de la conversación delante.
    if intent_name in (None, ""):
        return _handle_conversational(
            "conversation", project_config, conversation_id, _resp, final_data, context
        )

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# § 6a. HANDLER: ÓRDENES A LA INTERFAZ
# ═══════════════════════════════════════════════════════════════════════════════

#: Intent → (acción que escucha el frontend, acuse para el usuario).
_UI_ACTIONS: Dict[str, tuple] = {
    "show_map":           ("show_map", "Aquí tienes el mapa. 🗺️"),
    "zoom_in":            ("zoom_in", "Acercando."),
    "zoom_out":           ("zoom_out", "Alejando."),
    "fit_all_businesses": ("fit_all_businesses", "Te muestro todo lo que hay."),
    "locate_me":          ("locate_me", "Buscando tu ubicación."),
}


def _handle_ui_action(
    intent_name: str, final_data: Dict, sem_state: ConversationState = None
) -> Dict:
    """
    Ejecuta una orden de pantalla sin consultar al modelo.

    Las órdenes que tienen que ver con el mapa vuelven a mandar lo que la
    conversación ya encontró. Antes se limitaban a mover la vista: quien pedía
    "vuelve al mapa" después de una búsqueda encontraba el mapa vacío, porque
    las fichas viajan con el turno que las produjo y ese turno ya había pasado.
    """
    action, reply = _UI_ACTIONS[intent_name]
    logger.info("[INTERCEPTOR] orden de interfaz → %s", action)
    final_data = {**final_data, "voice_action": action}

    if intent_name in ("show_map", "fit_all_businesses") and sem_state is not None:
        conocidos = _known_businesses(final_data, sem_state)
        if conocidos:
            _present_businesses(final_data, conocidos)
            _show_on_map(final_data, conocidos, {})
            action = final_data["voice_action"]
            reply = (
                f"Ahí lo tienes: **{conocidos[0].get('name')}** en el mapa."
                if len(conocidos) == 1 else
                f"Ahí tienes las {len(conocidos)} opciones marcadas en el mapa."
            )

    return {
        "reply": reply,
        "voice_action": action,
        "final_data": final_data,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# § 6b. HANDLER: CONCEPTO NO EXISTENTE EN NEXISERVICE
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_semantic_clarify(
    args: Dict, final_data: Dict, sem_state: ConversationState, context: Dict = None
) -> Dict:
    """
    El sistema entendió la forma del mensaje pero no encontró a qué se refiere.

    Nunca debe salir de aquí un "No encontré 'tu frase'": eso mezcla dos cosas
    muy distintas —no entender y no tener resultados— y deja al usuario sin
    saber si reformular o rendirse.
    """
    message = args.get("message") or (
        "No estoy seguro de qué necesitas. ¿Me cuentas qué quieres resolver?"
    )
    cierre = ""
    # Si la aclaración afina una ranura concreta ("¿te sirve a las 15:00 o a las
    # 16:00?"), esa ranura sigue abierta: la respuesta del usuario debe caer
    # dentro de la reserva, no empezar una conversación nueva.
    expects = args.get("_expects")

    # Cuando no se está afinando una ranura, el usuario pidió algo que aquí no
    # existe. Decirle sólo "no manejo eso" lo deja sin salida: la respuesta útil
    # es qué SÍ hay, que además le dice si vale la pena reformular.
    if not expects:
        sugerencia = await _what_there_is((context or {}).get("active_city"))
        if sugerencia:
            cierre = f" {sugerencia} ¿Alguna de ésas te sirve?"
    message = f"{message.rstrip()}{cierre}"

    sem_state.expect(expects, message)
    sem_state.save(final_data)
    logger.info("[INTERCEPTOR] Sin correspondencia en el catálogo → aclaración con alternativas")
    return {
        "reply": message,
        "voice_action": None,
        "final_data": {**final_data, "needs_clarification": True},
    }


async def _what_there_is(city: Optional[str]) -> str:
    """Los rubros que sí existen, para que el "no lo tengo" venga con una salida."""
    from tools.nexiservice import directory_overview

    resumen = await directory_overview(city=city)
    rubros = [r["name"].lower() for r in (resumen.get("categories") or [])][:5]
    if not rubros:
        return ""
    donde = f" en {city}" if city else ""
    return f"Lo que sí tengo{donde} es {natural_list(rubros)}, entre otros."


# ═══════════════════════════════════════════════════════════════════════════════
# § 6c. HANDLER: RESERVA A LA ESPERA DE SESIÓN
# ═══════════════════════════════════════════════════════════════════════════════

def _remember_pending_reservation(pending: Dict, final_data: Dict) -> Dict:
    """
    Guarda una reserva ya acordada que sólo espera a que el usuario entre.

    Se conserva entera —negocio, servicio, profesional, día y hora— para que al
    volver no haya que preguntarle nada otra vez.
    """
    set_booking_state(final_data, BookingState.WAITING_AUTH)
    final_data["_pending_reservation"] = dict(pending or {})
    final_data["needs_auth"] = True

    sem_state = ConversationState.load(final_data)
    sem_state.booking.update({k: v for k, v in (pending or {}).items() if v})
    sem_state.save(final_data)
    logger.info("[STATE] Reserva guardada a la espera de sesión: %s", pending)
    return final_data


def _offer_pending_reservation(final_data: Dict) -> Optional[Dict]:
    """
    Le recuerda al usuario la reserva que dejó a medias, y le deja decidir.

    Se pregunta UNA vez: repetirlo en cada mensaje convierte el recordatorio en
    un obstáculo. Si no contesta, la conversación sigue su curso y la reserva
    queda guardada hasta que él la retome.
    """
    pending = final_data.get("_pending_reservation") or {}
    if not pending.get("business_id"):
        clear_booking_state(final_data)
        return None
    if final_data.get("_pending_reservation_offered"):
        return None

    final_data["_pending_reservation_offered"] = True
    partes = [p for p in (
        pending.get("service_name"),
        f"en {pending['business_name']}" if pending.get("business_name") else None,
        f"a las {pending['time']}" if pending.get("time") else None,
    ) if p]
    detalle = " ".join(partes) or "tu reserva"
    logger.info("[STATE] Reserva pendiente ofrecida al usuario: %s", pending)

    sem_state = ConversationState.load(final_data)
    sem_state.expect("confirm_pending", "¿Confirmo la reserva que dejaste pendiente?",
                     goal="booking")
    sem_state.save(final_data)

    return {
        "reply": (
            f"Antes de seguir: dejaste pendiente **{detalle}**. "
            "¿Quieres que la confirme?"
        ),
        "voice_action": None,
        "final_data": final_data,
    }


async def _resume_pending_reservation(user_data: Dict, final_data: Dict) -> Optional[Dict]:
    """Confirma la reserva que quedó esperando, ahora que hay sesión."""
    pending = final_data.get("_pending_reservation") or {}
    if not pending.get("business_id"):
        clear_booking_state(final_data)
        return None

    logger.info("[STATE] Sesión detectada → confirmando reserva pendiente: %s", pending)
    tool_output = await _call_confirm_appointment(
        business_id=pending.get("business_id"),
        service_name=pending.get("service_name"),
        time=pending.get("time"),
        date=pending.get("date"),
        reservation_name=None,          # el nombre sale de la cuenta
        user_data=user_data,
    )

    if tool_output.get("success"):
        clear_booking_state(final_data)
        return {
            "reply": tool_output.get("message"),
            "voice_action": "navigate" if tool_output.get("url") else None,
            "voice_action_payload": (
                {"url": tool_output.get("url")} if tool_output.get("url") else None
            ),
            "final_data": {**final_data, "last_booking_status": True},
        }

    # No se pudo cerrar: se deja el proceso abierto y se explica.
    return {
        "reply": tool_output.get("message")
        or "Ya entraste, pero no pude cerrar la reserva. ¿Lo intentamos otra vez?",
        "final_data": final_data,
    }


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

    if tool_output.get("needs_auth"):
        _remember_pending_reservation(tool_output.get("pending_reservation") or {}, final_data)
        return {
            "reply": tool_output.get("message"),
            "voice_action": "require_auth",
            "voice_action_payload": {
                "reason": "reservation",
                "pending": tool_output.get("pending_reservation") or {},
            },
            "final_data": final_data,
        }

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

async def _handle_request_appointment(
    args: Dict, messages: List, user_data: Dict, final_data: Dict,
    sem_state: ConversationState = None,
) -> Dict:
    from tools.nexiservice import request_appointment

    sem_state = sem_state if sem_state is not None else ConversationState.load(final_data)
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
        # El estado semántico va primero: es memoria explícita de lo que la
        # conversación estableció. El raspado del historial queda como respaldo
        # para conversaciones que empezaron antes de existir este estado.
        slots = sem_state.booking

        biz_id      = biz_id      or slots.get("business_id") or final_data.get("selected_business_id") or final_data.get("_pending_biz_id")
        time_arg    = time_arg    or slots.get("time")    or final_data.get("booking_time")  or res_ctx.get("time")  or dt_ctx.get("time")
        date_arg    = date_arg    or slots.get("date")    or final_data.get("booking_date")  or res_ctx.get("date")  or dt_ctx.get("date")
        service_arg = service_arg or slots.get("service_name") or final_data.get("booking_service") or res_ctx.get("service_name")
        prof_name   = prof_name   or slots.get("professional_name")

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

    # Las ranuras se acumulan turno a turno: lo que el usuario ya dijo no se le
    # vuelve a preguntar, aunque lo haya dicho en mensajes separados.
    for key, value in (
        ("business_id", biz_id), ("business_name", biz_name),
        ("service_name", service_arg), ("professional_name", prof_name),
        ("time", time_arg), ("date", date_arg), ("reservation_name", res_name),
    ):
        if value:
            sem_state.booking[key] = value

    # Reemplazos que hizo el usuario en este turno ("no, mejor a las 10"). Se
    # anotan para no volver a proponerle el valor que acaba de descartar.
    for cambio in (args.get("_corrections") or []):
        sem_state.corrections.append(cambio)
        logger.info(
            "[DIÁLOGO] corrección: %s '%s' → '%s'",
            cambio.get("slot"), cambio.get("from"), cambio.get("to"),
        )
    del sem_state.corrections[:-ConversationState.MAX_CORRECTIONS]
    sem_state.goal = "booking"
    sem_state.save(final_data)

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

    # ── 6a. Anotar QUÉ quedó preguntado ──────────────────────────────────────
    # La herramienta lo declara (`asking`), no se adivina leyendo el texto que
    # acaba de redactar. Es lo que permite que el turno siguiente —"9 am", "el
    # viernes", "la segunda"— se lea como la respuesta a esta pregunta concreta
    # y no como un mensaje suelto que reinicia la conversación.
    asking = tool_output.get("asking")
    if asking:
        sem_state.expect(asking, message, goal="booking")
        set_booking_state(final_data, _SLOT_TO_STATE.get(asking, BookingState.IDLE))
    else:
        sem_state.expect(None)
    sem_state.save(final_data)

    logger.info(
        "[DIÁLOGO] turno | objetivo=%s ranura_pedida=%s | conocido=%s | falta=%s",
        sem_state.goal, asking,
        {k: v for k, v in sem_state.booking.items() if v},
        next_missing_slot(sem_state),
    )

    # La reserva está completa pero hace falta una cuenta para dejarla a nombre
    # de alguien. Se guarda entera y se espera a que el usuario entre.
    if tool_output.get("needs_auth"):
        _remember_pending_reservation(tool_output.get("pending_reservation") or {}, final_data)
        return {
            "reply": message,
            "voice_action": "require_auth",
            "voice_action_payload": {
                "reason": "reservation",
                "pending": tool_output.get("pending_reservation") or {},
            },
            "final_data": {**final_data, "needs_input": True},
        }

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
            sem_state.clear_booking()
            sem_state.save(final_data)

    # Si la herramienta ofreció profesionales a elegir, esa lista pasa a ser
    # contexto: "quiero el segundo" debe poder resolverse contra ella.
    if tool_output.get("professionals"):
        sem_state.remember_list(
            "professional",
            [{"name": name} for name in tool_output["professionals"]],
        )
        sem_state.save(final_data)

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
    """
    Conversación: saludos, agradecimientos, preguntas sobre el propio asistente.

    Aquí NO se responde con una plantilla y se corta. Lyra es un asistente, no
    un buscador con saludo incorporado: quien escribe "hola, ¿cómo estás?" o
    "¿esto cómo funciona?" espera una respuesta escrita para él, que tenga en
    cuenta lo que ya se habló. Eso sólo lo puede dar el modelo.

    Lo que sí se prepara es una salida digna para cuando el modelo no esté
    disponible: la plantilla queda guardada como respaldo y el bucle del agente
    la usa en lugar de un mensaje de error.
    """
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
        "capabilities": "Puedo ayudarte a buscar negocios, ver servicios y agendar citas.",
    }
    local_reply = reply or defaults.get(intent_name, "¡Hola! ¿En qué puedo ayudarte?")

    # Un empresario que pregunta qué puede hacer no está preguntando cómo buscar
    # restaurantes. Las plantillas describen la experiencia del cliente, y
    # dárselas a quien administra su negocio es contestarle a otra persona.
    if intent_name == "capabilities" and _is_business_owner(context):
        local_reply = _ADMIN_CAPABILITIES
    final_data["_fallback_reply"] = local_reply

    # Con el modelo externo apagado, Lyra responde con lo suyo. La respuesta no
    # es una plantilla ciega: se le añade el hilo de la conversación, que es lo
    # que hacía falta para que no pareciera que empieza de cero en cada turno.
    from core.config import settings
    if not settings.LLM_EXTERNAL_ENABLED:
        sem_state = ConversationState.load(final_data)
        return {
            "reply": _with_conversational_context(local_reply, intent_name, sem_state),
            "voice_action": None,
            "final_data": final_data,
        }

    # Despedirse no necesita al modelo: es un cierre, no una conversación.
    if intent_name == "farewell":
        return {
            "reply": local_reply,
            "voice_action": None,
            "final_data": final_data,
        }

    logger.info("[INTERCEPTOR] %s → respuesta conversacional del modelo", intent_name)
    return None


#: Lo que un empresario puede pedirle a Lyra HOY, sin inventar nada: las
#: secciones del panel a las que sabe llevar y los datos de su propio negocio
#: que sabe consultar. Si mañana aparece una capacidad nueva, se añade aquí.
_ADMIN_CAPABILITIES = (
    "Trabajo contigo sobre tu negocio, así que no tienes que buscar nada en el menú. "
    "Puedo llevarte directo a **tu agenda**, a **productos e inventario**, a "
    "**gestión de personal**, al **punto de venta**, a **medios de pago** o a la "
    "**configuración de tu empresa** — sólo dime a dónde quieres ir.\n\n"
    "Y también puedo contarte lo que ya está publicado de tu negocio: qué "
    "**servicios** tienes y a qué **precio**, cómo está tu **disponibilidad**, "
    "quién aparece en tu **equipo** y qué dicen tus clientes en las **reseñas**.\n\n"
    "¿Por dónde empezamos?"
)


def _is_business_owner(context: Dict) -> bool:
    """¿Quien escribe administra un negocio, o lo está buscando?"""
    rol = str((context.get("user_data") or {}).get("role") or "").lower()
    return rol in ("admin", "administrador", "empresario", "owner")


def _with_conversational_context(reply: str, intent_name: str, sem_state: ConversationState) -> str:
    """
    Engancha la respuesta con lo que la conversación ya tenía abierto.

    Un asistente que acaba de mostrar seis consultorios y recibe un "gracias" no
    debería contestar como si acabara de conocerte. Retomar el hilo es lo que
    convierte una plantilla en una respuesta.
    """
    if intent_name == "farewell":
        return reply

    if sem_state.focus_label:
        return f"{reply}\n\nSeguimos con **{sem_state.focus_label}**, por cierto."

    pendientes = sem_state.presented
    if pendientes:
        cuantos = len(pendientes)
        if cuantos == 1:
            return f"{reply}\n\nTe había mostrado **{pendientes[0].label}**, si quieres seguimos por ahí."
        return (
            f"{reply}\n\nTe había mostrado {cuantos} opciones — dime un número "
            "y seguimos con esa."
        )

    return reply


async def _handle_navigate_to_company(
    args: Dict, context: Dict, final_data: Dict, sem_state: ConversationState = None
) -> Dict:
    from tools.nexiservice import search_businesses

    sem_state = sem_state if sem_state is not None else ConversationState.load(final_data)
    name = args.get("business_name")
    city = args.get("city")
    biz_id = args.get("business_id")

    # Si la comprensión ya identificó la empresa (por anclaje al catálogo o por
    # referencia a algo mostrado), no hace falta volver a buscarla por nombre.
    if biz_id and name:
        ficha = {"id": biz_id, "name": name}
        _focus_business(final_data, sem_state, ficha)
        # Y queda registrada como lo que hay sobre la mesa: sin esto, un "¿y el
        # más cercano?" a continuación no tenía nada que comparar y se iba a
        # buscar al directorio entero.
        sem_state.remember_list(ConceptKind.BUSINESS, [ficha])
        _present_businesses(final_data, [ficha])
        sem_state.save(final_data)
        return {
            "reply": (
                f"**{name}**\n\n¿Deseas ver sus servicios o agendar una cita?"
                f"\n[BIZ:{biz_id}]"
            ),
            "voice_action": "navigate",
            "voice_action_payload": {"url": f"/empresa/{biz_id}"},
            "final_data": final_data,
        }

    if not name:
        return {"reply": "¿Qué negocio deseas ver?", "final_data": final_data}

    tool_output = await search_businesses(category=name, city=city, grounded=True)
    if not tool_output.get("success"):
        return _tool_failed(tool_output, final_data, f"buscar **{name}**")

    businesses = tool_output.get("businesses", [])
    if not businesses:
        # Se reconoció el nombre pero la empresa no está disponible aquí.
        return {
            "reply": (
                f"**{name}** no aparece disponible en este momento. "
                "¿Quieres que busque opciones parecidas?"
            ),
            "final_data": final_data,
        }

    b = businesses[0]
    _present_businesses(final_data, businesses)
    sem_state.remember_list(ConceptKind.BUSINESS, businesses)
    _focus_business(final_data, sem_state, b)
    sem_state.save(final_data)

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


def _present_businesses(final_data: Dict, businesses: List[Dict]) -> None:
    """
    Deja una lista de negocios lista para pintarse Y disponible como contexto.

    Son dos cosas distintas y hay que escribirlas por separado: `properties` es
    lo que se dibuja EN ESTE turno, `_last_businesses` es lo que la conversación
    recuerda haber mostrado. Reconstruir lo primero a partir de lo segundo era
    lo que hacía que las mismas fichas reaparecieran en cada respuesta.
    """
    final_data["_last_businesses"] = businesses
    final_data["properties"] = [{"businesses": businesses}] if businesses else []


def _focus_business(final_data: Dict, sem_state: ConversationState, business: Dict) -> None:
    """Fija una empresa como el centro de la conversación."""
    biz_id = business.get("id")
    final_data["selected_business"] = business
    final_data["selected_business_id"] = biz_id
    final_data["_pending_biz_id"] = biz_id
    sem_state.set_focus(ConceptKind.BUSINESS, biz_id, business.get("name") or "")
    sem_state.booking["business_id"] = biz_id
    sem_state.booking["business_name"] = business.get("name")


async def _handle_get_business_professionals(
    args: Dict, messages: List, final_data: Dict,
    sem_state: ConversationState = None, context: Dict = None,
) -> Dict:
    """
    Equipo que atiende en un negocio.

    Además de responder, deja la lista registrada como contexto para que el
    usuario pueda decir después "con la segunda" o "con ella".
    """
    from tools.nexiservice import get_business_professionals

    sem_state = sem_state if sem_state is not None else ConversationState.load(final_data)
    context = context or {}
    biz_id = args.get("business_id") or _find_anchored_id_in_messages(messages)
    if not biz_id:
        return {
            "reply": "¿De qué negocio quieres ver el equipo?",
            "final_data": final_data,
        }

    tool_output = await get_business_professionals(business_id=biz_id)
    if not tool_output.get("success"):
        return _tool_failed(tool_output, final_data, "ver el equipo de ese negocio")

    professionals = tool_output.get("professionals", [])
    if not professionals:
        return {
            "reply": (
                f"**{tool_output.get('business_name') or 'Este negocio'}** todavía no "
                "tiene profesionales publicados. ¿Quieres ver sus servicios?"
            ),
            "final_data": final_data,
        }

    sem_state.remember_list("professional", professionals)
    sem_state.save(final_data)

    lines = []
    for idx, p in enumerate(professionals, start=1):
        perfil = f" — {_one_line(p['perfil'], 90)}" if p.get("perfil") else ""
        lines.append(f"{idx}. **{p['name']}**{perfil}")

    cierre = (
        "Ése es el equipo que aparece publicado. ¿Quieres que te lleve a gestión "
        "de personal?"
        if _is_business_owner(context) else
        "¿Con quién te gustaría agendar?"
    )
    reply = (
        f"### Equipo de {tool_output.get('business_name') or 'este negocio'}\n"
        + "\n".join(lines)
        + f"\n\n{cierre}\n[BIZ:{biz_id}]"
    )
    return {"reply": reply, "final_data": final_data}


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

    args["reservation_name"] = res_name
    tool_output = await confirm_appointment(**args, user_data=user_data)

    # Sin sesión no se cierra la reserva: se guarda y se pide entrar.
    if tool_output.get("needs_auth"):
        _remember_pending_reservation(tool_output.get("pending_reservation") or {}, final_data)
        return {
            "reply": tool_output.get("message"),
            "voice_action": "require_auth",
            "voice_action_payload": {
                "reason": "reservation",
                "pending": tool_output.get("pending_reservation") or {},
            },
            "final_data": final_data,
        }

    if tool_output.get("success"):
        clear_booking_state(final_data)

    return {
        "reply": tool_output.get("message"),
        "final_data": {**final_data, "booking_success": tool_output.get("success")},
    }


async def _handle_search_businesses(
    args: Dict, context: Dict, final_data: Dict, sem_state: ConversationState = None
) -> Dict:
    from tools.nexiservice import search_businesses

    sem_state = sem_state if sem_state is not None else ConversationState.load(final_data)
    category = args.get("category")
    city = args.get("city") or context.get("active_city")

    # Qué se le había mostrado ANTES de esta búsqueda. Es lo que permite
    # reconocer que el usuario está preguntando por lo mismo otra vez —"¿es el
    # único?", "¿hay más?"— y contestarle eso en vez de volver a presentarle el
    # mismo negocio como si fuera un hallazgo nuevo.
    ya_mostrados = [
        p.entity_id for p in sem_state.presented
        if p.kind == ConceptKind.BUSINESS and p.entity_id
    ]

    # Cuando la necesidad se expresó por el servicio ("alguna medicina", "un
    # masaje"), se entra por la tabla de servicios: buscar sólo por nombre y
    # categoría de empresa no encontraría nada aunque el servicio exista.
    tool_output = None
    if args.get("_grounded_kind") in (ConceptKind.SERVICE, ConceptKind.SERVICE_CATEGORY):
        from tools.nexiservice import find_businesses_offering

        by_service = await find_businesses_offering(service_term=category, city=city)
        if by_service.get("success") and by_service.get("businesses"):
            tool_output = by_service

    if tool_output is None:
        tool_output = await search_businesses(
            category=category,
            city=city,
            near_me=args.get("near_me", False),
            user_lat=context.get("user_lat"),
            user_lng=context.get("user_lng"),
            grounded=bool(args.get("_grounded_terms")),
        )
    if not tool_output.get("success"):
        return _tool_failed(tool_output, final_data, "hacer esa búsqueda")

    businesses = tool_output.get("businesses", [])
    _present_businesses(final_data, businesses)

    # Lo mostrado queda registrado como contexto: el turno siguiente puede decir
    # "el segundo" o "ese" y el sistema sabrá a qué apunta.
    sem_state.remember_list(ConceptKind.BUSINESS, businesses)
    if args.get("_grounded_kind"):
        sem_state.active_domain = category
        sem_state.active_domain_label = _domain_word(args, category)
    # Cuando la búsqueda se hizo POR UN SERVICIO, ese servicio es el tema de la
    # conversación. No abre una reserva —buscar no es agendar—, pero si el
    # usuario decide agendar después, ya lo dijo: preguntárselo otra vez es
    # hacerle repetir lo único que había pedido desde el principio.
    if args.get("_grounded_kind") in (ConceptKind.SERVICE, ConceptKind.SERVICE_CATEGORY):
        sem_state.topic_service = category

    # El usuario pidió reservar y describió el rubro en el mismo mensaje. La
    # petición no se pierde por mostrarle opciones: queda anotada para que
    # elegir una de ellas retome la reserva donde iba.
    pending = args.get("_pending_booking")
    wants_professional = args.get("_wants_professional")
    is_booking = pending is not None or bool(wants_professional)
    if is_booking:
        sem_state.booking.update({k: v for k, v in (pending or {}).items() if v})
        if wants_professional:
            sem_state.booking["wants_professional"] = True

    palabra = _domain_word(args, category)
    label = f"**{palabra}**" if palabra else "opciones"
    where = tool_output.get("city") or city

    # ── La misma búsqueda otra vez: el usuario pregunta si eso es todo ───────
    #
    # "¿Es el único?", "¿hay más?", "¿qué otras opciones hay?" vuelven a
    # consultar el catálogo y devuelven exactamente lo mismo. Repetirle entonces
    # "Encontré X. ¿Quieres agendar?" es no haber contestado: el usuario
    # preguntó por la CANTIDAD, y la respuesta a eso es que no hay más.
    # Sólo cuando la pregunta era por un rubro concreto. "¿Qué puedo encontrar
    # aquí?" no pregunta si hay más de lo mismo: es una pregunta de apertura, y
    # contestarle "son esas mismas 20, no tengo más" la cierra en falso —además
    # con el número de fichas en pantalla, no con el del directorio.
    repetida = (
        bool(businesses)
        and bool(palabra)
        and not is_booking
        and ya_mostrados
        and {b["id"] for b in businesses} == set(ya_mostrados)
    )
    if repetida:
        if len(businesses) == 1:
            b = businesses[0]
            sem_state.set_focus(ConceptKind.BUSINESS, b["id"], b["name"])
            # La concordancia va con el NEGOCIO, que es de quien se habla, no
            # con la etiqueta del rubro: "**Consultorio Vida Sana** es la única
            # que tengo con consulta médica" cambia de sujeto a mitad de frase.
            reply = (
                f"Sí, **{b['name']}** es el único que tengo con {label} en {where}. "
                "Sigue marcado en el mapa. Si quieres te muestro sus servicios, "
                "sus horarios, o buscamos en otra ciudad."
            )
        else:
            reply = (
                f"Son esas mismas {count_phrase(len(businesses), palabra)}: no tengo "
                f"más en {where}. {them(palabra).capitalize()} tienes marcad"
                f"{'as' if palabra and _feminine(palabra) else 'os'} en el mapa."
            )
        _show_on_map(final_data, businesses, tool_output)
        sem_state.save(final_data)
        return {"reply": reply, "final_data": final_data}

    if not businesses:
        # El concepto se entendió; simplemente no hay nada registrado todavía.
        #
        # El mensaje se redacta aquí y no se toma el de la herramienta: aquélla
        # sólo conoce la etiqueta con la que consultó ("gym"), y decirle al
        # usuario "no encontré resultados para gym" cuando pidió un gimnasio es
        # enseñarle el nombre de la columna. Lo único que se conserva de la
        # herramienta es la ciudad alternativa, que sí aporta.
        que_buscaba = pluralize_es(palabra) if palabra else "lo que buscas"
        reply = (
            f"No tengo {que_buscaba} registrad{'a' if palabra and _feminine(palabra) else 'o'}"
            f"{'s' if palabra else ''} en {where or 'tu zona'} por ahora."
        )
        otra_ciudad = tool_output.get("suggested_next_city")
        if otra_ciudad:
            reply += f" Sí hay opciones en **{otra_ciudad}**, ¿quieres que las mire? [CITY:{otra_ciudad}]"
        else:
            reply += " ¿Quieres que busque en otra ciudad, o te muestro algo parecido?"
        _center_on_city(final_data, tool_output)
    elif len(businesses) == 1:
        b = businesses[0]
        sem_state.set_focus(ConceptKind.BUSINESS, b["id"], b["name"])
        if is_booking:
            sem_state.booking["business_id"] = b["id"]
            sem_state.booking["business_name"] = b["name"]
            reply = (
                f"Para tu cita encontré **{b['name']}**"
                + (f" — 📍 {b['address']}" if b.get("address") else "")
                + ". Ya lo tienes señalado en el mapa. "
                + ("¿Quieres ver quién puede atenderte ahí?" if wants_professional
                   else "¿Agendamos ahí?")
            )
        else:
            reply = (
                f"Encontré **{b['name']}**"
                + (f", en {b['address']}" if b.get("address") else "")
                + f"{_distance_phrase(b)}. Te lo estoy señalando en el mapa. "
                "¿Quieres ver sus servicios o agendar una cita?"
            )
        _show_on_map(final_data, businesses, tool_output)
    else:
        if is_booking:
            listado = "\n".join(
                f"{i}. **{b['name']}**{_distance_phrase(b)}"
                for i, b in enumerate(businesses[:5], start=1)
            )
            reply = (
                f"Para agendar tu cita encontré {count_phrase(len(businesses), palabra)} "
                f"en {where}:\n{listado}\n\n"
                f"{them(palabra).capitalize()} tienes en el mapa. ¿En cuál quieres que te agende?"
            )
        elif palabra:
            reply = (
                f"Encontré {count_phrase(len(businesses), palabra)} en {where}. "
                f"Te {them(palabra)} estoy mostrando en el mapa para que veas dónde "
                f"queda {each_one(palabra)}."
            )
        else:
            # "¿Qué negocios tienes?", "¿qué puedo encontrar aquí?". No hay un
            # rubro que nombrar, así que se cuenta QUÉ hay: una lista de nombres
            # sueltos no responde a esa pregunta, y los rubros sí orientan la
            # siguiente.
            reply = await _directory_overview(businesses, where, tool_output)
        _show_on_map(final_data, businesses, tool_output)

    # Enseñar opciones DENTRO de una reserva también es una pregunta: la que
    # falta por responder es en cuál agendar. Registrarla es lo que permite que
    # el turno siguiente sea "la segunda" o "esa" y no haya que repetir el
    # nombre entero.
    if is_booking and len(businesses) > 1:
        sem_state.expect("business", reply, goal="booking")
    elif is_booking:
        sem_state.goal = "booking"

    sem_state.save(final_data)
    return {"reply": reply, "final_data": final_data}


# ═══════════════════════════════════════════════════════════════════════════════
# § 9b. LO QUE SE DICE Y LO QUE SE VE, EN EL MISMO TURNO
# ═══════════════════════════════════════════════════════════════════════════════
#
# Lyra no puede decir "te los muestro en el mapa" y dejar que el usuario navegue
# a mano. Cada respuesta que nombra sitios físicos sale con la orden de pantalla
# que la acompaña, y las dos cosas llegan al frontend en el mismo turno.
#
# Las acciones son las que el frontend ya escucha (`map_actions` en
# projects/nexiservice.yaml): `fly_to_business` para uno, `fit_all_businesses`
# para varios. Aquí vivía `map_highlight`, que no está en esa lista y que por
# tanto nadie atendía: Lyra anunciaba el mapa y el mapa no se movía.


def _map_payload(businesses: List[Dict]) -> Dict:
    """Lo mínimo que el mapa necesita para pintar un resultado sin volver a pedirlo."""
    return {
        "businesses": [
            {
                "id": b.get("id"),
                "name": b.get("name"),
                "lat": b.get("lat"),
                "lng": b.get("lng"),
                "address": b.get("address"),
                "category": b.get("category"),
            }
            for b in businesses
            if b.get("lat") is not None and b.get("lng") is not None
        ],
    }


def _centroid(businesses: List[Dict]) -> Optional[Dict]:
    """El punto donde encuadrar el mapa para que se vean todos."""
    puntos = [
        (float(b["lat"]), float(b["lng"]))
        for b in businesses
        if b.get("lat") is not None and b.get("lng") is not None
    ]
    if not puntos:
        return None
    return {
        "lat": sum(p[0] for p in puntos) / len(puntos),
        "lng": sum(p[1] for p in puntos) / len(puntos),
    }


def _show_on_map(
    final_data: Dict, businesses: List[Dict], tool_output: Dict, focus_id=None
) -> None:
    """
    Deja el turno listo para que la pantalla haga lo que la respuesta anuncia.

    Un solo resultado se enfoca; varios se encuadran. En los dos casos viajan
    también las coordenadas, para que el mapa no tenga que resolver de nuevo
    dónde está lo que acaba de recibir.
    """
    if not businesses:
        _center_on_city(final_data, tool_output)
        return

    carga = _map_payload(businesses)
    if len(businesses) == 1:
        b = businesses[0]
        final_data["voice_action"] = "fly_to_business"
        final_data["voice_action_payload"] = {
            "business_id": b.get("id"),
            "business_name": b.get("name"),
            "lat": b.get("lat"),
            "lng": b.get("lng"),
            "zoom": 16,
            **carga,
        }
    else:
        final_data["voice_action"] = "fit_all_businesses"
        final_data["voice_action_payload"] = {
            "business_ids": [b.get("id") for b in businesses],
            # Cuál de todos abrir. Encuadrar seis marcadores sin abrir ninguno
            # deja un mapa que parece no haber cambiado; con la ficha del
            # primero abierta se ve de qué está hablando Lyra. El orden ya viene
            # dado por relevancia o distancia, así que el primero es el que toca.
            "focus_business_id": focus_id or businesses[0].get("id"),
            **carga,
        }

    centro = _centroid(businesses) or (tool_output or {}).get("target_city_coords")
    if centro:
        final_data["map_center"] = centro


def _center_on_city(final_data: Dict, tool_output: Dict) -> None:
    """
    Sin resultados el mapa igual se mueve a la ciudad donde se buscó.

    Decir "no hay nada en Popayán" mientras el mapa sigue en otra parte deja al
    usuario sin saber dónde se miró.
    """
    coords = (tool_output or {}).get("target_city_coords")
    if coords:
        final_data["map_center"] = coords
        final_data["voice_action"] = "show_map"
        final_data["voice_action_payload"] = {"center": coords}


def _distance_phrase(business: Dict) -> str:
    """"a 800 metros", "a 2,3 km" — o nada, si no se sabe."""
    km = business.get("distance_km")
    if km is None:
        return ""
    try:
        km = float(km)
    except (TypeError, ValueError):
        return ""
    if km < 1:
        return f" (a {int(round(km * 1000))} metros)"
    return f" (a {km:.1f} km)".replace(".", ",")


async def _directory_overview(
    businesses: List[Dict], where: Optional[str], tool_output: Dict
) -> str:
    """
    Respuesta a "¿qué negocios tienes?" y a "¿qué puedo encontrar aquí?".

    La pregunta es por la VARIEDAD, no por una lista de nombres propios: quien
    todavía no sabe qué busca no puede elegir entre veinte razones sociales. Se
    le cuenta de qué hay, que es lo que le permite dar el siguiente paso.

    Los rubros se consultan aparte, no se cuentan sobre las fichas devueltas: la
    búsqueda trae como mucho veinte resultados ordenados por distancia, y con
    ésos Lyra informaba de dos categorías donde hay nueve.
    """
    from tools.nexiservice import directory_overview

    resumen = await directory_overview(city=where)
    rubros = [(r["name"], r["count"]) for r in (resumen.get("categories") or [])]
    total = resumen.get("total") or tool_output.get("count") or len(businesses)

    cabeza = f"Tengo {total} negocios publicados"
    if where:
        cabeza += f" en {where}"

    if not rubros:
        return (
            f"{cabeza}. Te los estoy mostrando en el mapa. Dime qué necesitas "
            "—comer, un taller, algo para tu mascota— y te muestro sólo eso."
        )

    top = rubros[:4]
    listado = natural_list([f"{nombre.lower()} ({n})" for nombre, n in top])
    resto = len(rubros) - len(top)
    cola = f", y {resto} rubros más" if resto > 0 else ""
    return (
        f"{cabeza}, y ya los tienes en el mapa. Lo que más hay es "
        f"{listado}{cola}. ¿Te muestro alguno en concreto, o prefieres que te "
        "recomiende según lo que necesites?"
    )


def _domain_word(args: Dict, category: Optional[str]) -> Optional[str]:
    """
    La palabra con la que contarle al usuario lo que se buscó.

    Manda la SUYA. La plataforma guarda los hospitales bajo la categoría
    "medico", y esa etiqueta es la correcta para consultar la base de datos y la
    equivocada para contestar: quien pidió un hospital y oye "encontré 6
    opciones de médico" entiende que no se le entendió, aunque los seis
    resultados sean exactamente los que quería.

    Sólo cuando el usuario no nombró nada —"¿qué hay por aquí?", "muéstrame
    opciones"— se recurre a la etiqueta del catálogo, que ahí sí añade algo.
    """
    return user_facing_label(
        user_terms=args.get("_user_terms"),
        catalog_terms=args.get("_grounded_terms"),
        category=category,
    )


def _domain_label(args: Dict, category: Optional[str]) -> str:
    """La misma palabra, resaltada, para intercalarla en una frase."""
    palabra = _domain_word(args, category)
    return f"**{palabra}**" if palabra else "opciones"


async def _handle_get_business_services(
    args: Dict, messages: List, context: Dict, final_data: Dict,
    sem_state: ConversationState = None,
) -> Dict:
    from tools.nexiservice import get_business_services

    sem_state = sem_state if sem_state is not None else ConversationState.load(final_data)
    biz_name = args.get("business_name")
    biz_id   = args.get("business_id")

    if _is_generic_query(biz_name) and not biz_id:
        biz_id = sem_state.focus_id or _find_anchored_id_in_messages(messages)

    tool_output = await get_business_services(business_name=biz_name, business_id=biz_id)
    if not tool_output.get("success"):
        return _tool_failed(tool_output, final_data, "ver los servicios de ese negocio")

    b_name   = tool_output["business_name"]
    services = tool_output["services"]
    biz_id   = tool_output["business_id"]

    # ¿Es la segunda vez seguida que se pide lo mismo? "¿Y cuánto cuesta?" justo
    # después del catálogo no pide el catálogo otra vez: pide el dato del
    # precio. Reimprimir dieciocho líneas idénticas es la respuesta más
    # mecánica posible, y encima esconde lo que se preguntó.
    ya_listados = {
        p.label for p in sem_state.presented if p.kind == ConceptKind.SERVICE
    }
    repite = bool(services) and ya_listados == {s.get("nombre") for s in services}

    # Los servicios listados quedan como contexto seleccionable ("el primero").
    sem_state.remember_list(ConceptKind.SERVICE, services)
    sem_state.set_focus(ConceptKind.BUSINESS, biz_id, b_name)
    sem_state.booking["business_id"] = biz_id
    sem_state.booking["business_name"] = b_name
    sem_state.save(final_data)

    if not services:
        reply = (
            f"**{b_name}** todavía no tiene servicios publicados. "
            "¿Quieres que busque algo parecido en otro sitio?"
        )
    elif repite:
        reply = _price_summary(b_name, services)
    else:
        lineas = "\n".join(_service_line(s) for s in services)
        # El dueño del negocio no viene a agendarse a sí mismo: está revisando
        # lo que tiene publicado. Ofrecerle una cita ahí es hablarle como si
        # fuera un cliente suyo.
        cierre = (
            f"Son {len(services)} servicios publicados. ¿Quieres que te lleve a "
            "productos para editarlos?"
            if _is_business_owner(context) else
            "Dime cuál te interesa y lo agendamos."
        )
        reply = f"Esto es lo que ofrece **{b_name}**:\n{lineas}\n\n{cierre}"

    reply += f"\n\n[BIZ:{biz_id}]"

    # La ficha del negocio se conserva entera. Antes se reemplazaba por
    # `{"id", "name"}`, y con eso se perdían sus coordenadas: un "¿y cuál está
    # más cerca?" después de mirar un catálogo se quedaba sin nada que medir y
    # acababa pidiéndole al usuario que activara la ubicación que ya tenía.
    ficha = next(
        (b for b in (final_data.get("_last_businesses") or []) if b.get("id") == biz_id),
        {"id": biz_id, "name": b_name},
    )
    return {
        "reply": reply,
        "voice_action": "navigate",
        "voice_action_payload": {"url": f"/empresa/{biz_id}#servicios"},
        "final_data": {**final_data, "_last_businesses": [ficha]},
    }


def _service_line(service: Dict) -> str:
    """
    Un servicio en una línea: nombre, precio y cuánto dura.

    El precio pasa por `format_price` y no por un `:,.0f` improvisado. Son dos
    cosas distintas: `$120,000` es el formato de otro país, y —lo que de verdad
    importa— la capa de voz sólo sabe leer «ciento veinte mil pesos» si el
    número llega escrito como un precio y no como una cifra suelta.
    """
    partes = [f"• **{service.get('nombre', 'Servicio')}**"]
    precio = format_price(service.get("valor"), fallback="")
    if precio:
        partes.append(f"— {precio}")
    minutos = service.get("tiempoServicio")
    if minutos:
        partes.append(f"({minutos} min)")
    return " ".join(partes)


def _price_summary(business_name: str, services: List[Dict]) -> str:
    """
    El precio contado como dato, no como catálogo repetido.

    Se dice desde cuánto hasta cuánto y se nombran los dos extremos: es lo que
    de verdad responde a "¿cuánto cuesta?" cuando el usuario todavía no ha
    elegido un servicio concreto.
    """
    from core.speech_format import to_amount

    con_precio = [(to_amount(s.get("valor")), s) for s in services]
    con_precio = [(v, s) for v, s in con_precio if v and v > 0]
    if not con_precio:
        return (
            f"**{business_name}** no tiene los precios publicados. "
            "Dime qué servicio te interesa y te digo cómo consultarlo."
        )

    con_precio.sort(key=lambda par: par[0])
    barato_v, barato = con_precio[0]
    caro_v, caro = con_precio[-1]

    if barato is caro or barato_v == caro_v:
        return (
            f"En **{business_name}** todo está en {format_price(barato_v)}. "
            "¿Cuál quieres que te agende?"
        )
    return (
        f"En **{business_name}** los precios van desde {format_price(barato_v)} "
        f"—**{barato['nombre']}**— hasta {format_price(caro_v)}, que es "
        f"**{caro['nombre']}**. ¿De cuál quieres que te dé el detalle, o te agendo alguno?"
    )


def _rating_label(business: Dict) -> str:
    """Cómo se dice en voz alta la nota de un negocio."""
    if not business.get("rating"):
        return "todavía sin reseñas"
    reseñas = business.get("reviews") or 0
    plural = "reseña" if reseñas == 1 else "reseñas"
    return f"⭐ {business['rating']} de {reseñas} {plural}"


async def _handle_recommend_businesses(
    args: Dict, context: Dict, final_data: Dict, sem_state: ConversationState = None
) -> Optional[Dict]:
    """
    Recomienda de verdad: mira quién presta lo pedido y qué dicen sus clientes.

    Este intent estaba enrutado pero no atendido, así que "recomiéndame uno para
    medicina general" caía al final del bucle y salía por el mensaje de último
    recurso —"¿buscas un negocio, servicios o agendar?"—, que es justo la
    pregunta que el usuario acababa de contestar.

    La respuesta dice también POR QUÉ ese y no otro. Una recomendación sin
    motivo no se puede discutir, y el usuario no tiene forma de saber si le
    sirve.
    """
    from tools.nexiservice import rank_businesses_for

    sem_state = sem_state if sem_state is not None else ConversationState.load(final_data)
    termino = (args.get("category") or "").strip()
    ciudad = args.get("city") or context.get("active_city")
    # Con qué palabra contarlo. La búsqueda se hace con `termino` —la etiqueta
    # real— y la respuesta se escribe con la del usuario: "miré las opciones de
    # Mascotas" delata la categoría interna a quien preguntó por veterinarias.
    # El orden importa: primero lo que el usuario dijo AHORA, luego la etiqueta
    # con la que se consultó, y sólo al final el tema que traía la conversación.
    # Al revés, "quiero ir a comer, ¿qué me recomiendas?" —donde la única
    # palabra propia es un verbo y no sirve para nombrar el rubro— heredaba el
    # tema anterior y anunciaba "miré hospitales" sobre una lista de restaurantes.
    palabra = (
        user_facing_label(
            user_terms=args.get("_user_terms"),
            catalog_terms=[termino] if termino else None,
        )
        or sem_state.active_domain_label
        or termino
    )

    if not termino:
        return {
            "reply": "¿Sobre qué quieres que te recomiende? Dime el servicio o el tipo de negocio.",
            "final_data": final_data,
        }

    salida = await rank_businesses_for(termino, city=ciudad)
    if not salida.get("success"):
        return {"reply": salida.get("message"), "final_data": final_data}

    negocios = salida.get("businesses") or []
    ciudad_real = salida.get("city") or ciudad or "tu zona"

    if not negocios:
        return {
            "reply": (
                f"No encontré quién preste **{palabra or termino}** en **{ciudad_real}**. "
                "¿Quieres que busque algo parecido o en otra ciudad?"
            ),
            "final_data": final_data,
        }

    # Si se sabe dónde está el usuario, la cercanía entra en la recomendación.
    # "El mejor" y "el que te queda al lado" no son lo mismo, y decirlo es lo
    # que convierte una lista ordenada en un consejo.
    negocios = _with_distances(negocios, context)

    con_nota = [b for b in negocios if b.get("rating")]
    # El recomendado es el mejor valorado. Si nadie tiene reseñas no se elige a
    # ciegas: se dice que no hay con qué elegir y se nombra lo que sí hay.
    elegido = con_nota[0] if con_nota else negocios[0]
    servicio = elegido.get("matched_service")

    # El más cercano se nombra aparte cuando no coincide con el recomendado: son
    # dos criterios legítimos y el usuario tiene derecho a elegir entre ellos.
    con_distancia = sorted(
        [b for b in negocios if b.get("distance_km") is not None],
        key=lambda b: b["distance_km"],
    )
    mas_cercano = con_distancia[0] if con_distancia else None

    _present_businesses(final_data, negocios)
    sem_state.remember_list(ConceptKind.BUSINESS, negocios)
    # El recomendado queda en foco: el "sí" que suele venir después continúa la
    # reserva sobre él en vez de empezar una conversación nueva.
    _focus_business(final_data, sem_state, elegido)

    # Y el servicio por el que se preguntó queda anotado con él.
    #
    # La recomendación ERA sobre un servicio concreto: quien pide "el mejor para
    # medicina general" ya dijo qué quiere. Sin anotarlo, el flujo llegaba hasta
    # el final —negocio, hora, día— y remataba con "¿qué servicio deseas
    # agendar?" enseñando un catálogo de nueve, incluida la respuesta que el
    # usuario había dado en su primer mensaje.
    # El rubro recomendado pasa a ser el tema, con las dos formas: la que se
    # consulta y la que se dice. Sin la segunda, un "¿y qué me recomiendas?"
    # posterior respondía "miré Restaurantes y Gastronomía", que es el nombre de
    # la fila en la tabla de categorías.
    sem_state.active_domain = termino
    sem_state.active_domain_label = palabra

    if servicio:
        sem_state.booking["service_name"] = servicio
        final_data["_pending_service"] = servicio
        final_data["booking_service"] = servicio
        logger.info("[RECOMENDAR] servicio arrastrado a la reserva: '%s'", servicio)

    sem_state.save(final_data)

    partes = [
        f"Miré {pluralize_es(palabra)} de **{ciudad_real}** y lo que dicen sus clientes.\n"
        if palabra else
        f"Miré las opciones en **{ciudad_real}** y lo que dicen sus clientes.\n"
    ]

    if not con_nota:
        partes.append(
            "Ninguno tiene reseñas todavía, así que no puedo decirte cuál es el mejor "
            f"sin inventármelo. El que lo ofrece es **{elegido['name']}**"
            + (f", con su **{servicio}**" if servicio else "")
            + "."
        )
    else:
        razon = (
            f"la mejor nota de las {len(negocios)} opciones que encontré"
            if len(negocios) > 1 else "el único que encontré por aquí"
        )
        partes.append(
            f"Me quedo con **{elegido['name']}** — {_rating_label(elegido)}, {razon}"
            + (f", y sí ofrece **{servicio}**" if servicio else "")
            + "."
        )
        if elegido.get("comment"):
            partes.append(f"\n> «{elegido['comment']}»")

        otros = con_nota[1:3]
        if otros:
            listado = " y ".join(
                f"**{b['name']}** ({_rating_label(b)})" for b in otros
            )
            partes.append(f"\nSi ese no te sirve, después van {listado}.")

    if mas_cercano and mas_cercano["id"] != elegido["id"]:
        partes.append(
            f"\nSi lo que buscas es lo más a mano, el más cercano a ti es "
            f"**{mas_cercano['name']}**{_distance_phrase(mas_cercano)}."
        )
    elif mas_cercano:
        partes.append(f"\nAdemás es el que te queda más cerca{_distance_phrase(elegido)}.")

    partes.append(
        f"\nTe {them(None)} estoy marcando en el mapa para que compares."
        if len(negocios) > 1 else
        "\nYa lo tienes marcado en el mapa."
    )

    if servicio:
        partes.append(f"\n¿Te agendo la **{servicio}** en **{elegido['name']}**?")
    else:
        partes.append(f"\n¿Te agendo en **{elegido['name']}**?")
    partes.append(f"\n[BIZ:{elegido['id']}]")

    # Las opciones se ven mientras se explican: la recomendación pierde la mitad
    # de su sentido si el usuario no puede situar de qué está hablando Lyra.
    _show_on_map(
        final_data,
        negocios if len(negocios) > 1 else [elegido],
        {},
        focus_id=elegido.get("id"),
    )

    mejor = elegido
    logger.info(
        "[RECOMENDAR] '%s' en %s → %s (nota=%s, reseñas=%s) entre %d candidatos",
        termino, ciudad_real, mejor["name"], mejor.get("rating"),
        mejor.get("reviews"), len(negocios),
    )

    return {
        "reply": "\n".join(partes),
        "voice_action": None,
        "final_data": final_data,
    }


async def _handle_get_business_reviews(args: Dict, messages: List, final_data: Dict) -> Dict:
    from tools.nexiservice import get_business_reviews

    biz_id = args.get("business_id") or _find_anchored_id_in_messages(messages)
    tool_output = await get_business_reviews(
        business_id=biz_id, business_name=args.get("business_name")
    )
    if not tool_output.get("success"):
        return _tool_failed(tool_output, final_data, "leer las reseñas")

    nombre = tool_output["business_name"]
    reseñas = tool_output.get("reviews") or []
    total = tool_output.get("total_reviews") or 0

    # "Promedio: ⭐ 0 (0 opiniones)" es un formulario vacío, no una respuesta.
    if not total:
        return {
            "reply": (
                f"**{nombre}** todavía no tiene reseñas. En cuanto alguien "
                "califique, te lo puedo contar aquí mismo."
            ),
            "final_data": final_data,
        }

    reply = (
        f"### Reseñas de {nombre}\n"
        f"Promedio: ⭐ **{tool_output['average_rating']}** "
        f"({total} {'opinión' if total == 1 else 'opiniones'})\n\n"
    )
    for r in reseñas[:3]:
        reply += f"- \"{r['comentario']}\" ({r['rating']}⭐)\n"

    return {"reply": reply, "final_data": final_data}


async def _handle_get_business_availability(args: Dict, messages: List, final_data: Dict) -> Dict:
    """
    "¿Cómo está mi agenda mañana?", "¿tienen espacio el viernes?"

    La herramienta devuelve las franjas OCUPADAS y no siempre redacta un
    mensaje; devolver ese `message` tal cual dejaba el turno sin texto, y el
    turno sin texto no se puede ni guardar ni leer en voz alta.
    """
    from tools.nexiservice import get_business_availability

    biz_id = args.get("business_id") or _find_anchored_id_in_messages(messages)
    if not biz_id:
        return {
            "reply": "¿De qué negocio quieres ver la agenda?",
            "final_data": final_data,
        }

    tool_output = await get_business_availability(business_id=biz_id, date=args.get("date"))
    if not tool_output.get("success"):
        return _tool_failed(tool_output, final_data, "consultar la agenda")

    nombre = tool_output.get("business_name") or "ese negocio"
    ocupados = tool_output.get("busy_slots") or []
    cuando = args.get("date")

    if tool_output.get("message"):
        return {"reply": tool_output["message"], "final_data": final_data}

    if not ocupados:
        dia = " para " + (cuando if cuando else "los próximos días")
        return {
            "reply": (
                f"**{nombre}** tiene la agenda libre{dia}. "
                "Puedes elegir la hora que prefieras — dime cuál y a qué servicio."
            ),
            "final_data": final_data,
        }

    from tools.nexiservice import _format_slot, _natural_list

    por_dia: Dict[str, List[str]] = {}
    for franja in ocupados:
        tramo = _format_slot(franja)
        if tramo and tramo not in por_dia.setdefault(franja.get("date_label", ""), []):
            por_dia[franja["date_label"]].append(tramo)
    detalle = _natural_list([
        f"{dia.lower()} {_natural_list(horas)}" for dia, horas in por_dia.items() if horas
    ])
    return {
        "reply": (
            f"En **{nombre}** ya está ocupado {detalle}. El resto del horario está libre. "
            "¿Te agendo en alguno de los huecos?"
        ),
        "final_data": final_data,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# § 9c. CAPACIDADES QUE ESTABAN ENRUTADAS PERO NO ATENDIDAS
# ═══════════════════════════════════════════════════════════════════════════════
#
# El router y la comprensión saben producir estas intenciones desde hace tiempo.
# El interceptor no las conocía, así que caían al bucle del agente — y ahí no hay
# nada: `tools/nexiservice.py` no declara `SCHEMAS`, de modo que el registro de
# herramientas para NexiService está vacío, y con `LLM_EXTERNAL_ENABLED=false` el
# bucle contesta con su frase de último recurso.
#
# El resultado visible era éste: "muéstrame dónde queda", "¿cuál está más
# cerca?", "¿cuánto cuesta el corte?" o "llévame a inventario" recibían todas la
# misma respuesta — "no estoy seguro de haberte entendido" — después de que el
# sistema las hubiera entendido perfectamente.


def _seed_focus_from_screen(
    context: Dict, final_data: Dict, sem_state: ConversationState
) -> None:
    """
    Lo que el usuario tiene abierto en pantalla entra como foco de la conversación.

    Sólo si la conversación no tiene ya uno propio: si estuvo mirando otro
    negocio en el chat, manda ése. La pantalla es el punto de partida, no una
    corrección de lo que se venía hablando.
    """
    if sem_state.focus_id or final_data.get("selected_business_id"):
        return
    en_pantalla = context.get("active_company_id")
    if not en_pantalla:
        return
    try:
        biz_id = int(en_pantalla)
    except (TypeError, ValueError):
        return
    sem_state.set_focus(ConceptKind.BUSINESS, biz_id, sem_state.focus_label or "")
    final_data["selected_business_id"] = biz_id
    final_data["_pending_biz_id"] = biz_id
    logger.info("[INTERCEPTOR] foco tomado de la pantalla: empresa %s", biz_id)


def _tool_failed(tool_output: Dict, final_data: Dict, que_intentaba: str) -> Dict:
    """
    Qué decir cuando la consulta no salió bien.

    Devolver `None` aquí parece inocuo y no lo es: el turno sigue hasta el bucle
    del agente, que para NexiService no tiene herramientas y —con el modelo
    externo apagado— responde "no estoy seguro de haberte entendido". El usuario
    recibe entonces un fallo de comprensión por algo que se entendió
    perfectamente y que simplemente falló al consultarse.
    """
    mensaje = (tool_output or {}).get("message")
    logger.warning("[INTERCEPTOR] la herramienta falló al %s: %s", que_intentaba, mensaje)
    return {
        "reply": mensaje or (
            f"No pude {que_intentaba} en este momento. ¿Lo intentamos otra vez?"
        ),
        "final_data": final_data,
    }


def _known_businesses(final_data: Dict, sem_state: ConversationState) -> List[Dict]:
    """Los negocios que la conversación tiene sobre la mesa ahora mismo."""
    recientes = final_data.get("_last_businesses") or []
    if recientes:
        return recientes
    return [
        {"id": p.entity_id, "name": p.label, **(p.extra or {})}
        for p in sem_state.presented
        if p.kind == ConceptKind.BUSINESS
    ]


def _with_distances(businesses: List[Dict], context: Dict) -> List[Dict]:
    """
    Añade la distancia a cada negocio cuando se sabe dónde está el usuario.

    La búsqueda sólo la calcula si se pidió "cerca de mí". Preguntar después
    "¿cuál está más cerca?" es igual de legítimo, y para responderlo basta con
    las coordenadas que ya viajan en cada resultado.
    """
    lat, lng = context.get("user_lat"), context.get("user_lng")
    if lat is None or lng is None:
        return businesses
    from tools.shared.utils import haversine

    salida = []
    for b in businesses:
        copia = dict(b)
        if copia.get("distance_km") is None and b.get("lat") and b.get("lng"):
            copia["distance_km"] = round(
                haversine(float(lat), float(lng), float(b["lat"]), float(b["lng"])), 2
            )
        salida.append(copia)
    return salida


#: Con qué criterio comparar, según lo que pidió el usuario.
_COMPARISON_CRITERIA = (
    ("distance", ("cerca", "cercan", "proxim", "cerquita", "queda mas")),
    ("price",    ("barat", "economic", "precio", "cuesta menos", "asequible")),
    ("rating",   ("mejor", "recomend", "calific", "valorad", "estrell")),
)


def _comparison_criterion(user_text: str) -> str:
    texto = _normalize(user_text or "")
    for criterio, marcas in _COMPARISON_CRITERIA:
        if any(m in texto for m in marcas):
            return criterio
    return "overview"


async def _handle_compare_businesses(
    args: Dict, context: Dict, final_data: Dict, sem_state: ConversationState
) -> Optional[Dict]:
    """
    "¿Cuál está más cerca?", "¿cuál es el más barato?", "compáralos".

    Se responde con lo que ya está en pantalla, no con una búsqueda nueva: el
    usuario está comparando LAS OPCIONES QUE VE, y traerle otras distintas es
    cambiarle la pregunta. Sin negocios sobre la mesa no hay comparación posible
    y se dice, en vez de improvisar una.
    """
    candidatos = _known_businesses(final_data, sem_state)
    if not candidatos:
        return {
            "reply": (
                "Todavía no tenemos opciones sobre la mesa para comparar. "
                "Dime qué buscas y te muestro alternativas."
            ),
            "final_data": final_data,
        }

    criterio = _comparison_criterion(context.get("user_text", ""))
    candidatos = _with_distances(candidatos, context)

    if criterio == "distance":
        con_dato = [b for b in candidatos if b.get("distance_km") is not None]
        if not con_dato:
            return {
                "reply": (
                    "Para decirte cuál te queda más cerca necesito saber dónde estás. "
                    "Activa tu ubicación y te ordeno las opciones por distancia."
                ),
                "voice_action": "locate_me",
                "final_data": {**final_data, "voice_action": "locate_me"},
            }
        con_dato.sort(key=lambda b: b["distance_km"])
        mejor = con_dato[0]
        _focus_business(final_data, sem_state, mejor)
        otros = con_dato[1:3]
        cola = (
            " Después van " + natural_list(
                [f"**{b['name']}**{_distance_phrase(b)}" for b in otros]
            ) + "."
            if otros else ""
        )
        reply = (
            f"El más cercano a ti es **{mejor['name']}**{_distance_phrase(mejor)}"
            + (f", en {mejor['address']}" if mejor.get("address") else "")
            + f".{cola} Lo estoy centrando en el mapa. ¿Te muestro sus servicios?"
        )
        _show_on_map(final_data, [mejor], {})
        sem_state.save(final_data)
        return {"reply": reply, "final_data": final_data}

    if criterio == "rating":
        # Comparar por calidad es exactamente lo que hace la recomendación, y
        # ésa sí mira las reseñas. Duplicar el criterio aquí sería tener dos
        # respuestas distintas para la misma pregunta.
        termino = sem_state.active_domain or sem_state.topic_service
        if termino:
            return await _handle_recommend_businesses(
                {"category": termino, "city": context.get("active_city")},
                context, final_data, sem_state,
            )

    # Comparación general: la tabla que la plataforma ya sabe construir.
    from tools.nexiservice import get_businesses_comparison

    ids = [b["id"] for b in candidatos[:4] if b.get("id")]
    salida = await get_businesses_comparison(ids)
    if not salida.get("success") or not salida.get("comparison"):
        return {
            "reply": "No pude reunir los datos para compararlos ahora mismo. ¿Quieres que te muestre uno por uno?",
            "final_data": final_data,
        }

    lineas = []
    for c in salida["comparison"]:
        nota = f"⭐ {c['rating']} ({c['reviews_count']})" if c.get("rating") else "sin reseñas todavía"
        servicios = ", ".join(c.get("services") or []) or "sin servicios publicados"
        lineas.append(f"• **{c['name']}** — {nota}. {servicios}")

    _present_businesses(final_data, candidatos)
    _show_on_map(final_data, candidatos, {})
    sem_state.save(final_data)
    return {
        "reply": (
            "Así se comparan:\n" + "\n".join(lineas)
            + "\n\nLas tienes todas en el mapa. ¿Cuál quieres ver de cerca?"
        ),
        "final_data": final_data,
    }


async def _handle_fly_to_business(
    args: Dict, context: Dict, final_data: Dict, sem_state: ConversationState
) -> Optional[Dict]:
    """
    "Muéstrame dónde queda", "ubícame Fogón Criollo en el mapa".

    Es la petición más literalmente visual que existe, y era la que menos
    ocurría: sin manejador, Lyra contestaba con texto y el mapa no se movía.
    """
    from tools.nexiservice import fly_to_business

    nombre = args.get("business_name")
    biz_id = args.get("business_id") or (
        sem_state.focus_id if sem_state.focus_kind == ConceptKind.BUSINESS else None
    )

    if not biz_id and (not nombre or _is_generic_query(nombre)):
        # Sin un negocio concreto, "muéstrame dónde quedan" habla de todo lo que
        # está en pantalla.
        conocidos = _known_businesses(final_data, sem_state)
        if conocidos:
            _present_businesses(final_data, conocidos)
            _show_on_map(final_data, conocidos, {})
            sem_state.save(final_data)
            return {
                "reply": (
                    f"Ahí {them(None)} tienes: te estoy marcando "
                    f"{count_phrase(len(conocidos), None, fallback='opciones')} en el mapa."
                    if len(conocidos) > 1 else
                    f"Ahí lo tienes, **{conocidos[0].get('name')}** marcado en el mapa."
                ),
                "final_data": final_data,
            }
        return {
            "reply": "¿Qué negocio quieres que te ubique en el mapa?",
            "final_data": final_data,
        }

    salida = await fly_to_business(
        business_name=nombre, business_id=biz_id, city=args.get("city") or context.get("active_city")
    )
    if not salida.get("success"):
        return {
            "reply": salida.get("message") or "No pude ubicar ese negocio en el mapa.",
            "final_data": final_data,
        }

    b = salida["business"]
    _present_businesses(final_data, [b])
    _focus_business(final_data, sem_state, b)
    _show_on_map(final_data, [b], {})
    sem_state.save(final_data)
    return {
        "reply": (
            f"**{b['name']}** está en {b.get('address') or 'la ubicación que te marco'}. "
            "Ya lo tienes centrado en el mapa. ¿Quieres ver sus servicios o agendar?"
            f"\n[BIZ:{b['id']}]"
        ),
        "final_data": final_data,
    }


async def _handle_business_identity(
    args: Dict, messages: List, final_data: Dict, sem_state: ConversationState
) -> Optional[Dict]:
    """"Háblame de este lugar" — quién es el negocio, con sus propias palabras."""
    from tools.nexiservice import get_business_mission_vision

    biz_id = args.get("business_id") or sem_state.focus_id or _find_anchored_id_in_messages(messages)
    nombre = args.get("business_name") or sem_state.focus_label
    if not biz_id and not nombre:
        return {"reply": "¿De qué negocio quieres que te cuente?", "final_data": final_data}

    salida = await get_business_mission_vision(business_name=nombre, business_id=biz_id)
    if not salida.get("success"):
        return {"reply": salida.get("message"), "final_data": final_data}

    b_name = salida["business_name"]
    partes = [f"**{b_name}**"]
    if salida.get("mision"):
        partes.append(f"\n{salida['mision']}")
    if salida.get("vision"):
        partes.append(f"\n_Hacia dónde van:_ {salida['vision']}")
    if not salida.get("mision") and not salida.get("vision"):
        partes.append(
            "\nTodavía no ha publicado su historia, pero sí puedo mostrarte sus "
            "servicios, sus horarios y lo que dicen sus clientes."
        )
    else:
        partes.append("\n¿Quieres ver sus servicios o lo que opinan sus clientes?")

    sem_state.set_focus(ConceptKind.BUSINESS, salida["business_id"], b_name)
    sem_state.save(final_data)
    return {
        "reply": "\n".join(partes) + f"\n\n[BIZ:{salida['business_id']}]",
        "voice_action": "navigate",
        "voice_action_payload": {"url": f"/empresa/{salida['business_id']}"},
        "final_data": final_data,
    }


async def _handle_open_business_web(
    args: Dict, messages: List, final_data: Dict, sem_state: ConversationState
) -> Optional[Dict]:
    """"Muéstrame sus redes" — abre el enlace que el negocio publicó."""
    from tools.nexiservice import open_business_web

    nombre = args.get("business_name")
    if not nombre or _is_generic_query(nombre):
        nombre = sem_state.focus_label
    if not nombre:
        return {"reply": "¿De qué negocio quieres ver las redes o la web?", "final_data": final_data}

    salida = await open_business_web(business_name=nombre)
    if not salida.get("success"):
        return {"reply": salida.get("message"), "final_data": final_data}

    # `open_url` es el nombre que el frontend escucha (LyraAssistant.tsx).
    # Con cualquier otro, el enlace se anunciaba y no se abría nada.
    return {
        "reply": salida.get("message"),
        "voice_action": "open_url",
        "voice_action_payload": {"url": salida.get("url")},
        "final_data": {
            **final_data,
            "voice_action": "open_url",
            "voice_action_payload": {"url": salida.get("url")},
        },
    }


async def _handle_get_service_info(
    args: Dict, messages: List, final_data: Dict, sem_state: ConversationState
) -> Optional[Dict]:
    """
    "¿Cuánto cuesta el corte?" — la ficha de un servicio, con su precio.

    El precio sale ya formateado por `format_price`, así que la capa de voz lo
    reconoce como precio y lo lee "ciento veinte mil pesos" en vez de deletrear
    los ceros.
    """
    from tools.nexiservice import get_service_info

    nombre = args.get("service_name") or sem_state.topic_service
    biz_id = args.get("business_id") or sem_state.focus_id or _find_anchored_id_in_messages(messages)

    if not nombre:
        # Sin servicio nombrado, la pregunta es por el catálogo del negocio.
        if biz_id:
            return await _handle_get_business_services(
                {"business_id": biz_id}, messages, {}, final_data, sem_state
            )
        return {
            "reply": "¿De qué servicio quieres saber el precio?",
            "final_data": final_data,
        }

    salida = await get_service_info(service_name=nombre, business_id=biz_id)
    if not salida.get("success"):
        return {"reply": salida.get("message"), "final_data": final_data}

    sem_state.topic_service = salida.get("name") or nombre
    sem_state.expect("confirm_booking", salida["message"], goal="booking")
    sem_state.booking["service_name"] = salida.get("name") or nombre
    sem_state.save(final_data)
    return {"reply": salida["message"], "final_data": final_data}


async def _handle_get_professional_info(
    args: Dict, messages: List, final_data: Dict, sem_state: ConversationState
) -> Optional[Dict]:
    """"¿Quién es Laura?" — el perfil de quien va a atender."""
    from tools.nexiservice import get_professional_info

    nombre = args.get("professional_name")
    if not nombre:
        return await _handle_get_business_professionals(args, messages, final_data, sem_state, context)

    salida = await get_professional_info(
        professional_name=nombre,
        business_id=args.get("business_id") or sem_state.focus_id,
    )
    return {"reply": salida.get("message"), "final_data": final_data}


async def _handle_general_info(args: Dict, context: Dict, final_data: Dict) -> Dict:
    """Preguntas sobre la plataforma misma: qué es, cómo se registra uno, qué módulos hay."""
    from tools.nexiservice import get_general_info

    rol = (context.get("user_data") or {}).get("role") or "client"
    salida = await get_general_info(
        topic=args.get("topic") or context.get("user_text", ""),
        role=rol,
        lat=context.get("user_lat"),
        lng=context.get("user_lng"),
    )
    return {"reply": salida.get("message"), "final_data": final_data}


def _handle_admin_navigate(args: Dict, final_data: Dict) -> Dict:
    """
    "Llévame a inventario", "abre ventas".

    Es la capacidad que el empresario usa de verdad: decir a dónde quiere ir en
    lugar de recorrer el menú. La orden ya existía en el router; lo que faltaba
    era que alguien la ejecutara.
    """
    url = args.get("url")
    nombre = args.get("name") or "esa sección"
    logger.info("[INTERCEPTOR] navegación de administración → %s", url)
    return {
        "reply": f"Te llevo a **{nombre}**.",
        "voice_action": "navigate",
        "voice_action_payload": {"url": url},
        "final_data": {
            **final_data,
            "voice_action": "navigate",
            "voice_action_payload": {"url": url},
        },
    }


def _handle_set_city(args: Dict, final_data: Dict) -> Dict:
    """El usuario dice en qué ciudad está: se anota y se usa en las búsquedas siguientes."""
    ciudad = args.get("city")
    final_data["active_city"] = ciudad
    return {
        "reply": (
            f"Listo, busco en **{ciudad}** de aquí en adelante. "
            "¿Qué necesitas encontrar por allí?"
        ),
        "voice_action": "set_city",
        "voice_action_payload": {"city": ciudad},
        "final_data": {
            **final_data,
            "voice_action": "set_city",
            "voice_action_payload": {"city": ciudad},
        },
    }


#: Respuestas a los avisos del navegador sobre la ubicación. Son acuses, no
#: conversación: cada uno dice qué pasa ahora y deja al usuario seguir.
#: Cada aviso con la acción que el frontend escucha para él. `show_city_input`
#: abre la caja de ciudad manual; sin ella, negar el permiso dejaba al usuario
#: sin forma de decir dónde está.
_GPS_REPLIES = {
    "gps_granted": (
        "Gracias, ya tengo tu ubicación. Ahora puedo ordenarte las opciones por "
        "cercanía. ¿Qué buscas?",
        "gps_granted",
        None,
    ),
    "gps_denied": (
        "Sin problema. Dime en qué ciudad estás y busco ahí.",
        "show_city_input",
        "denied",
    ),
    "gps_no_signal": (
        "No logro leer tu ubicación en este momento. Dime la ciudad y seguimos igual.",
        "show_city_input",
        "no_signal",
    ),
}


def _handle_gps(intent_name: str, context: Dict, final_data: Dict) -> Dict:
    reply, action, motivo = _GPS_REPLIES[intent_name]
    payload = {"reason": motivo} if motivo else {"city": context.get("active_city")}
    final_data["voice_action"] = action
    final_data["voice_action_payload"] = payload
    return {
        "reply": reply,
        "voice_action": action,
        "voice_action_payload": payload,
        "final_data": final_data,
    }


async def _handle_bare_confirmation(
    intent_name: str, args: Dict, messages: List, context: Dict,
    user_data: Dict, final_data: Dict, sem_state: ConversationState,
) -> Optional[Dict]:
    """
    Un "sí" pelado continúa lo que estuviera en marcha.

    Qué continúa depende de lo último que Lyra puso sobre la mesa, y en ese
    orden: una reserva a medias, un negocio en foco, o una lista sin elegir. Un
    "dale" no significa nada por sí solo; significa lo último que se preguntó.
    """
    if sem_state.goal == "booking" and sem_state.booking.get("business_id"):
        return await _handle_request_appointment(
            dict(sem_state.booking), messages, user_data, final_data, sem_state
        )

    if sem_state.focus_id and sem_state.focus_kind == ConceptKind.BUSINESS:
        if intent_name == "confirm_navigation":
            return await _handle_navigate_to_company(
                {"business_id": sem_state.focus_id, "business_name": sem_state.focus_label},
                context, final_data, sem_state,
            )
        return await _handle_get_business_services(
            {"business_id": sem_state.focus_id}, messages, context, final_data, sem_state
        )

    conocidos = _known_businesses(final_data, sem_state)
    if conocidos:
        return {
            "reply": (
                f"Tengo {count_phrase(len(conocidos), None, fallback='opciones')} "
                "en pantalla. ¿Con cuál seguimos? Puedes decirme el número o el nombre."
            ),
            "final_data": final_data,
        }

    return {
        "reply": "¿Con qué seguimos? Dime qué necesitas y te busco opciones.",
        "final_data": final_data,
    }


async def _resolve_logged_user_name(ext_id, user_data: Dict) -> Optional[str]:
    name = (
        user_data.get("name") or user_data.get("full_name")
        or user_data.get("username") or user_data.get("nombre")
        or user_data.get("display_name")
    )
    logger.info("[INTERCEPTOR] user_data keys: %s", list(user_data.keys()))
    if name:
        return name
    # La misma traducción de identidades que usa la confirmación. Aquí se leía
    # el id de la persona como si fuera el de la cuenta y se unía contra
    # `tercero`: la reserva salía a nombre de una empresa que no tenía nada que
    # ver con quien estaba escribiendo.
    from tools.nexiservice import resolve_booking_identity

    identidad = await resolve_booking_identity(ext_id)
    return identidad["nombre"] if identidad else None


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
        businesses = output.get("businesses", [])
        _present_businesses(final_data, businesses)
        final_data["filters_applied"] = {
            "city": output.get("city"),
            "search": output.get("category"),
        }
        # También cuando la búsqueda la pidió el LLM: lo mostrado es contexto
        # referenciable en el turno siguiente, venga de donde venga.
        sem_state = ConversationState.load(final_data)
        sem_state.remember_list(ConceptKind.BUSINESS, businesses)
        if len(businesses) == 1:
            b = businesses[0]
            sem_state.set_focus(ConceptKind.BUSINESS, b.get("id"), b.get("name") or "")
        sem_state.save(final_data)

    if tool_name == "confirm_appointment":
        if output.get("needs_input"):
            final_data["needs_input"] = True
            final_data["needs_clarification"] = True
            final_data["reply"] = output.get("message")
            final_data["voice_action"] = "request_reservation_name"

        if output.get("action") == "navigate":
            final_data["voice_action"] = "navigate"
            final_data["voice_action_payload"] = {"url": output.get("url")}