"""
core/semantic/dialogue.py — Etapa 0: leer el turno como parte de un diálogo.

Todo lo demás en esta capa analiza el mensaje por sí mismo: qué acto de habla
es, a qué concepto del catálogo se ancla. Eso basta cuando el usuario abre un
tema, y no basta en absoluto cuando está respondiendo.

    Lyra: ¿A qué hora te gustaría?
    Usuario: 9 am

Analizado en solitario, "9 am" no pide nada: no nombra un negocio, no expresa
una necesidad, no pregunta. Analizado dentro del diálogo es una respuesta
completa, y la única lectura razonable. La diferencia no está en el mensaje —
está en que Lyra había dejado una pregunta abierta.

Este módulo hace esa lectura, y sólo esa. Se consulta ANTES que cualquier otra
regla porque una pregunta abierta es la evidencia contextual más fuerte que
existe: mientras esté en pie, el mensaje siguiente le pertenece.

La regla que lo gobierna, y que evita que se coma conversaciones ajenas:

    Una ranura abierta se queda con el mensaje sólo si el mensaje contiene un
    valor DEL TIPO que esa ranura espera. Si no lo contiene, este módulo se
    aparta y el mensaje sigue su camino normal — que es como debe ser cuando el
    usuario cambia de tema.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from core.semantic import lexicon as lx
from core.semantic import reference, temporal
from core.semantic.morphology import normalize
from core.semantic.speech_act import Analysis
from core.semantic.types import (
    Act,
    ConceptKind,
    ConversationState,
    Disposition,
    Understanding,
)

logger = logging.getLogger("lyra.semantic.dialogue")


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. RANURAS
# ═══════════════════════════════════════════════════════════════════════════════

class Slot:
    """Datos que una reserva necesita, con el nombre que ya usa el orquestador."""
    BUSINESS     = "business"
    SERVICE      = "service_name"
    PROFESSIONAL = "professional_name"
    DATE         = "date"
    TIME         = "time"
    NAME         = "reservation_name"


#: Orden en que se piden. Es el orden natural de una conversación de mostrador:
#: dónde, qué, cuándo y con quién.
BOOKING_SLOTS = (Slot.BUSINESS, Slot.SERVICE, Slot.TIME, Slot.DATE)

GOAL_BOOKING = "booking"


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. ENTRADA PÚBLICA
# ═══════════════════════════════════════════════════════════════════════════════

def read_answer(
    message: str,
    analysis: Analysis,
    state: ConversationState,
    temporal_slots: Optional[Dict[str, Any]] = None,
) -> Optional[Understanding]:
    """
    Lee el mensaje como respuesta a la pregunta que Lyra dejó abierta.

    Devuelve None cuando no hay pregunta abierta, o cuando el mensaje no
    contiene nada que la responda. Ese None es deliberado y es la mitad
    importante del contrato: significa "esto no me toca", y deja que el mensaje
    se interprete como lo que sea que realmente es.
    """
    slot = state.pending_slot
    if not slot:
        return None

    reader = _READERS.get(slot)
    if reader is None:
        return None

    result = reader(message, analysis, state, temporal_slots or {})
    if result is not None and result.disposition == Disposition.ACT:
        logger.info("respuesta a la ranura '%s' → %s", slot, result.args)
        return result

    # El usuario no siempre contesta lo que se le pregunta, y no por eso deja de
    # estar hablando de su reserva: mientras Lyra pide el día, él corrige la
    # hora. Ese dato es válido y hay que recogerlo; la pregunta pendiente se
    # vuelve a hacer sola, porque el dato que falta sigue faltando.
    #
    # Va por delante de una aclaración —"¿a qué hora entonces?"— justamente
    # porque una aclaración significa que el lector no obtuvo nada, y un dato
    # real siempre vale más que volver a preguntar.
    fuera_de_turno = _read_out_of_turn(message, analysis, state)
    if fuera_de_turno is not None:
        logger.info("dato de otra ranura dicho fuera de turno → %s", fuera_de_turno.args)
        return fuera_de_turno

    if result is not None:
        return result

    logger.debug("ranura '%s' abierta pero el mensaje no la responde", slot)
    return None


def _read_out_of_turn(
    message: str, analysis: Analysis, state: ConversationState,
) -> Optional[Understanding]:
    """
    Un dato de reserva dicho cuando se preguntaba por otro.

    Sólo se aceptan valores inequívocos —una hora o una fecha explícitas—, que
    no pueden confundirse con un cambio de tema. Un nombre suelto no entra aquí:
    sin la pregunta correspondiente delante, no hay forma de saber si nombra un
    servicio, un profesional o un negocio.
    """
    updates: Dict[str, Any] = {}

    hora = temporal.read_time(message)
    if hora.exact and hora.value != state.booking.get(Slot.TIME):
        updates[Slot.TIME] = hora.value

    fecha = temporal.read_date(message)
    if fecha and fecha != state.booking.get(Slot.DATE):
        updates[Slot.DATE] = fecha

    if not updates:
        return None

    corrections = _apply(state, updates)
    u = _accept(state, updates, f"dato fuera de turno: {updates}", corrections)
    # La pregunta sigue abierta: el dato que faltaba no lo ha dado todavía.
    u.expects = state.pending_slot
    return u


def booking_args(state: ConversationState, **overrides) -> Dict[str, Any]:
    """
    Ranuras de reserva acumuladas: lo que el usuario ya dijo no se le vuelve a
    preguntar.

    Los `overrides` sólo pisan cuando traen valor, de modo que un turno que
    aporta la hora no borra el negocio ni el servicio acordados antes.
    """
    args: Dict[str, Any] = {
        "business_id": state.booking.get("business_id") or (
            state.focus_id if state.focus_kind == ConceptKind.BUSINESS else None
        ),
        "business_name": state.booking.get("business_name") or (
            state.focus_label if state.focus_kind == ConceptKind.BUSINESS else None
        ),
        "service_name": state.booking.get(Slot.SERVICE),
        "professional_name": state.booking.get(Slot.PROFESSIONAL),
        "time": state.booking.get(Slot.TIME),
        "date": state.booking.get(Slot.DATE),
        "reservation_name": state.booking.get(Slot.NAME),
    }
    for key, value in overrides.items():
        if value is not None:
            args[key] = value
    return args


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. LECTORES POR RANURA
# ═══════════════════════════════════════════════════════════════════════════════

def _accept(state: ConversationState, updates: Dict[str, Any],
            note: str, corrections: List[Dict[str, Any]]) -> Understanding:
    """Construye el resultado de una ranura respondida."""
    u = Understanding(
        act=Act.TEMPORAL if Slot.TIME in updates or Slot.DATE in updates else Act.REFERENCE,
        disposition=Disposition.ACT,
        intent="request_appointment",
        args=booking_args(state, **updates),
        confidence=0.95,
        corrections=corrections,
    )
    return u.note(note)


def _ask_again(question: str, slot: str, note: str) -> Understanding:
    """La ranura sigue abierta: se afina la pregunta en vez de reiniciar."""
    u = Understanding(
        act=Act.TEMPORAL,
        disposition=Disposition.CLARIFY,
        clarification=question,
        expects=slot,
        confidence=0.8,
    )
    return u.note(note)


def _is_denial(analysis: Analysis, message: str) -> bool:
    """¿El mensaje corrige algo? ("no, mejor a las 10")"""
    if analysis.act == Act.DENY:
        return True
    first = normalize(message).split()
    return bool(first) and first[0] in lx.NEGATIVE_FORMS


#: Un número solo, sin nada alrededor.
_BARE_NUMBER = re.compile(r"^\s*(\d{1,2})\s*$")


def _read_time(message, analysis, state, temporal_slots) -> Optional[Understanding]:
    reading = temporal.read_time(message)

    # "9" a secas. Fuera de contexto ese numeral es una posición —"el 9º"— y así
    # debe seguir leyéndose; pero justo después de "¿a qué hora?" no hay otra
    # lectura posible, y devolverle un saludo por ser un mensaje corto era lo
    # que obligaba al usuario a repetir el dato.
    if not reading.exact:
        solo = _BARE_NUMBER.match(normalize(message))
        if solo:
            hora = int(solo.group(1))
            if 0 <= hora <= 23:
                # Una cita a las 3 es de la tarde: nadie pide consulta a las 3
                # de la madrugada. Las horas de atención mandan sobre el reloj.
                if 1 <= hora <= 6:
                    hora += 12
                corrections = _apply(state, {Slot.TIME: f"{hora:02d}:00"})
                return _accept(
                    state, {Slot.TIME: f"{hora:02d}:00"},
                    f"numeral suelto leído como hora: {hora:02d}:00", corrections,
                )

    if reading.exact:
        updates = {Slot.TIME: reading.value}
        # Si en el mismo turno vino también la fecha, se aprovecha: obligar a
        # decirla en un mensaje aparte es trámite, no conversación.
        fecha = temporal_slots.get(Slot.DATE) or temporal.read_date(message)
        if fecha:
            updates[Slot.DATE] = fecha
        corrections = _apply(state, updates)
        return _accept(state, updates, f"hora recibida: {reading.value}", corrections)

    if reading.vague:
        centro = temporal.DAYPARTS.get(reading.daypart, (None, None))[1]
        if centro:
            alterna = centro + 1
            return _ask_again(
                f"¿Te sirve alrededor de las {centro}:00, o prefieres las {alterna}:00?",
                Slot.TIME,
                f"franja '{reading.daypart}' sin hora exacta",
            )

    if analysis.act == Act.AFFIRM and state.booking.get(Slot.TIME):
        return _accept(state, {}, "hora ya acordada, confirmada", [])

    if _is_denial(analysis, message):
        return _ask_again(
            "Claro, la cambiamos. ¿A qué hora te viene mejor?",
            Slot.TIME,
            "rechaza la hora sin proponer otra",
        )

    return None


def _read_date(message, analysis, state, temporal_slots) -> Optional[Understanding]:
    fecha = temporal_slots.get(Slot.DATE) or temporal.read_date(message)
    if fecha:
        updates = {Slot.DATE: fecha}
        hora = temporal.read_time(message)
        if hora.exact:
            updates[Slot.TIME] = hora.value
        corrections = _apply(state, updates)
        return _accept(state, updates, f"fecha recibida: {fecha}", corrections)

    if analysis.act == Act.AFFIRM and state.booking.get(Slot.DATE):
        return _accept(state, {}, "fecha ya acordada, confirmada", [])

    if _is_denial(analysis, message):
        return _ask_again(
            "Sin problema. ¿Para qué día lo dejamos?",
            Slot.DATE,
            "rechaza la fecha sin proponer otra",
        )

    return None


def _read_from_list(kind: str, slot: str):
    """
    Lector de ranuras que se responden eligiendo de una lista mostrada.

    Sirve igual para servicios, profesionales y negocios: cambia contra qué
    lista se resuelve, no cómo. "el segundo", "esa", "con Lina" y el nombre
    escrito entero son la misma operación.
    """
    def reader(message, analysis, state, temporal_slots) -> Optional[Understanding]:
        target = reference.resolve(analysis, state, message)
        if target and (target.kind == kind or kind == ConceptKind.BUSINESS):
            updates: Dict[str, Any] = {}
            if kind == ConceptKind.BUSINESS:
                updates["business_id"] = target.entity_id
                updates["business_name"] = target.label
            else:
                updates[slot] = target.label
            corrections = _apply(state, updates)
            return _accept(state, updates, f"elige de la lista: {target.label}", corrections)

        # Nombre escrito directamente. Se acepta sólo si el mensaje es un
        # sintagma corto y no una pregunta: quien escribe "¿y qué precios
        # manejan?" no está nombrando un servicio, está preguntando otra cosa.
        if _looks_like_a_name(message, analysis):
            valor = message.strip()
            if kind == ConceptKind.BUSINESS:
                updates = {"business_name": valor}
            else:
                updates = {slot: valor}
            corrections = _apply(state, updates)
            return _accept(state, updates, f"valor dicho literalmente: {valor}", corrections)

        return None

    return reader


def _looks_like_a_name(message: str, analysis: Analysis) -> bool:
    """
    ¿El mensaje es el nombre de algo, y no otra cosa?

    Se exige brevedad y ausencia de interrogación. Sin este filtro, cualquier
    pregunta hecha mientras hay una ranura abierta se guardaría como si fuera el
    dato pedido — que es exactamente el error que producía servicios llamados
    "el primero" o "quiénes trabajan ahí".
    """
    texto = (message or "").strip()
    if not texto or "?" in texto:
        return False
    if analysis.markers.get("interrogative"):
        return False
    palabras = normalize(texto).split()
    if not 1 <= len(palabras) <= 6:
        return False
    # Si todas las palabras son andamiaje gramatical, no nombra nada.
    return any(not lx.is_function_word(p) for p in palabras)


def _apply(state: ConversationState, updates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Escribe las ranuras y devuelve los reemplazos que produjo.

    Una corrección toca SÓLO la ranura nombrada: cambiar la hora no borra el día
    ni el negocio. Ése es todo el mecanismo de "corrección parcial".
    """
    antes = len(state.corrections)
    for slot, value in updates.items():
        state.fulfil(slot, value)
    return state.corrections[antes:]


_READERS = {
    Slot.TIME:         _read_time,
    Slot.DATE:         _read_date,
    Slot.SERVICE:      _read_from_list(ConceptKind.SERVICE, Slot.SERVICE),
    Slot.PROFESSIONAL: _read_from_list("professional", Slot.PROFESSIONAL),
    Slot.BUSINESS:     _read_from_list(ConceptKind.BUSINESS, Slot.BUSINESS),
}


# ═══════════════════════════════════════════════════════════════════════════════
# § 4. QUÉ FALTA
# ═══════════════════════════════════════════════════════════════════════════════

#: Cómo se le pide cada dato al usuario. La herramienta redacta sus propios
#: mensajes con los datos reales delante; esto es el respaldo mínimo.
SLOT_QUESTIONS = {
    Slot.BUSINESS:     "¿En qué negocio te gustaría agendar?",
    Slot.SERVICE:      "¿Qué servicio quieres agendar?",
    Slot.TIME:         "¿A qué hora te gustaría?",
    Slot.DATE:         "¿Para qué día lo dejamos?",
    Slot.PROFESSIONAL: "¿Con quién prefieres que te agende?",
    Slot.NAME:         "¿A nombre de quién dejo la reserva?",
}


def next_missing_slot(state: ConversationState) -> Optional[str]:
    """Primer dato que falta para cerrar la reserva, en orden de conversación."""
    for slot in BOOKING_SLOTS:
        if slot == Slot.BUSINESS:
            if not (state.booking.get("business_id") or state.booking.get("business_name")):
                return Slot.BUSINESS
            continue
        if not state.booking.get(slot):
            return slot
    return None
