"""
tests/test_conversational_context.py — Que la conversación no se reinicie.

La queja que originó este paquete no era un error de búsqueda ni de reserva:

    Lyra:    ¿A qué hora te gustaría?
    Usuario: Quisiera una cita a las 9 de la mañana
    Lyra:    ¿A qué hora te gustaría?          ← otra vez
    Usuario: 9 am
    Lyra:    Listo. ¿Cuál es tu solicitud?     ← empezó de cero

El usuario había dado el dato dos veces y el sistema seguía sin tenerlo. Estas
pruebas fijan el mecanismo que lo arregla —el turno se lee dentro del diálogo, y
la pregunta abierta se registra como estado— y dejan constancia de los casos
concretos que fallaban.

    python -m pytest tests/test_conversational_context.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic import llm_resolver
from core.semantic.dialogue import Slot, next_missing_slot, read_answer
from core.semantic.speech_act import analyze
from core.semantic.temporal import read_date, read_time
from core.semantic.types import ConceptKind, ConversationState
from orchestrator.intent_router import detect_intent

NEGOCIO = "Consultorio Médico Vida Sana Popayán"
BIZ_ID = 21


@pytest.fixture(autouse=True)
def _no_live_llm():
    """Sin resolutor externo: se mide lo que el sistema entiende por sí mismo."""
    llm_resolver.set_completion_fn(lambda prompt: '{"categorias": [], "confianza": 0.0}')
    yield
    llm_resolver.set_completion_fn(None)


def estado(pending=None, presented=None, **booking) -> ConversationState:
    """Estado tras haber elegido el consultorio, con la ranura que se indique."""
    st = ConversationState()
    st.remember_list(
        presented[0] if presented else ConceptKind.BUSINESS,
        presented[1] if presented else [{"id": BIZ_ID, "name": NEGOCIO}],
    )
    st.set_focus(ConceptKind.BUSINESS, BIZ_ID, NEGOCIO)
    st.booking = {"business_id": BIZ_ID, "business_name": NEGOCIO, **booking}
    st.pending_slot = pending
    st.goal = "booking" if pending else None
    return st


def turno(mensaje: str, st: ConversationState) -> dict:
    """Un turno completo del usuario, con el estado de la conversación delante."""
    return detect_intent(
        mensaje, "nexiservice",
        current_context={
            "last_assistant_msg": "¿A qué hora te gustaría?",
            "semantic_state": st.to_dict(),
        },
    )


def responde(mensaje: str, st: ConversationState):
    """Lectura dialógica aislada, sin el resto del router."""
    return read_answer(mensaje, analyze(mensaje), st)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. EL CASO PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje", [
    "Quisiera una cita a las 9 de la mañana",
    "9 am",
    "9",
    "a las 9",
    "quiero a las 9",
    "que sea a las 9 am",
])
def test_la_hora_llega_a_la_reserva(mensaje):
    """
    Todas estas frases dicen lo mismo tras "¿a qué hora?", y ninguna es una
    petición nueva. El negocio ya acordado tiene que seguir ahí.
    """
    r = turno(mensaje, estado(Slot.TIME))
    assert r["intent"] == "request_appointment", mensaje
    assert r["args"]["time"] == "09:00", mensaje
    assert r["args"]["business_id"] == BIZ_ID, mensaje


def test_nunca_termina_preguntando_cual_es_tu_solicitud():
    """
    Regresión exacta del log. Mientras haya una intención activa válida, una
    respuesta corta no puede acabar en conversación genérica.
    """
    r = turno("9 am", estado(Slot.TIME))
    assert r["intent"] not in ("conversation", "greeting", "identity", "capabilities")


def test_un_numeral_suelto_no_es_un_saludo():
    """"9" medía tres caracteres y se iba por el atajo de saludo."""
    assert turno("9", estado(Slot.TIME))["intent"] == "request_appointment"
    # Sin pregunta abierta ese mismo "9" no significa una hora.
    assert turno("9", estado(None))["intent"] != "request_appointment"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LA HORA NO ES UNA POSICIÓN
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje,esperado", [
    ("Quisiera una cita a las 9 de la mañana", None),
    ("a las 9", None),
    ("como a las 3", None),
    ("no, mejor a las 10", None),
    ("9 am", None),
    ("el primero", 1),
    ("la segunda", 2),
    ("quiero el tercero", 3),
])
def test_los_numerales_del_reloj_no_seleccionan(mensaje, esperado):
    """
    "a las 9" pedía el noveno elemento de la última lista. Como no existía, la
    comprensión se rendía y la hora se perdía por el camino.
    """
    assert analyze(mensaje).ordinal == esperado, mensaje


def test_la_peticion_con_hora_sigue_siendo_una_cita():
    assert analyze("Quisiera una cita a las 9 de la mañana").act == "booking"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. "DE LA MAÑANA" ES EL MERIDIANO, NO EL DÍA SIGUIENTE
# ═══════════════════════════════════════════════════════════════════════════════

def test_manana_como_franja_no_cambia_la_fecha():
    assert read_date("a las 9 de la mañana") is None
    assert read_date("quisiera una cita a las 9 de la mañana") is None


def test_manana_como_adverbio_si_es_el_dia_siguiente():
    assert read_date("mañana") == "tomorrow"
    assert read_date("quiero la cita mañana") == "tomorrow"
    assert read_date("mañana a las 9 de la mañana") == "tomorrow"


@pytest.mark.parametrize("texto,esperado", [
    ("a las 9 de la mañana", "09:00"),
    ("a las 9 de la noche", "21:00"),
    ("a las 3 de la tarde", "15:00"),
    ("8;30", "08:30"),
    ("a las 8:30 am", "08:30"),
    ("9 pm", "21:00"),
    ("a las ocho", "08:00"),
])
def test_lectura_de_hora(texto, esperado):
    assert read_time(texto).value == esperado


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RESPUESTAS CORTAS Y ELÍPTICAS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje,esperada", [
    ("mañana", "tomorrow"),
    ("hoy", "today"),
    ("el 30 de abril", "2026-04-30"),
    ("el primero de mayo", "2026-05-01"),
])
def test_respuestas_de_fecha(mensaje, esperada):
    r = turno(mensaje, estado(Slot.DATE, time="09:00"))
    assert r["intent"] == "request_appointment"
    assert r["args"]["date"] == esperada
    # La hora acordada antes no se toca.
    assert r["args"]["time"] == "09:00"


def test_dia_de_la_semana():
    r = turno("el viernes", estado(Slot.DATE, time="09:00"))
    assert r["args"]["date"] and r["args"]["date"] not in ("today", "tomorrow")


@pytest.mark.parametrize("mensaje", ["después del almuerzo", "en la tarde", "más tarde"])
def test_una_franja_se_afina_sin_reiniciar(mensaje):
    """
    "por ahí después del almuerzo" no da una hora exacta. Corresponde precisar,
    no responder "¿cuál es tu solicitud?".
    """
    r = turno(mensaje, estado(Slot.TIME))
    assert r["intent"] == "semantic_clarify"
    assert "15:00" in r["args"]["message"]
    # Y la ranura sigue abierta: lo que diga después es la hora.
    assert r["args"]["_expects"] == Slot.TIME


def test_afirmacion_confirma_lo_ya_acordado():
    r = turno("sí", estado(Slot.TIME, time="09:00"))
    assert r["intent"] == "request_appointment"
    assert r["args"]["time"] == "09:00"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CORRECCIONES PARCIALES
# ═══════════════════════════════════════════════════════════════════════════════

def test_corregir_la_hora_no_borra_lo_demas():
    st = estado(Slot.TIME, time="09:00", date="tomorrow", service_name="Consulta General")
    r = turno("no, mejor a las 10", st)

    assert r["args"]["time"] == "10:00"
    assert r["args"]["date"] == "tomorrow"
    assert r["args"]["service_name"] == "Consulta General"
    assert r["args"]["business_id"] == BIZ_ID


def test_la_correccion_queda_registrada():
    st = estado(Slot.TIME, time="09:00")
    r = turno("no, mejor a las 10", st)
    assert r["args"]["_corrections"] == [{"slot": "time", "from": "09:00", "to": "10:00"}]


def test_corregir_un_dato_mientras_se_pregunta_otro():
    """
    Lyra pide el día y el usuario corrige la hora. Sigue hablando de su reserva:
    el dato es válido, se recoge, y la pregunta pendiente se mantiene. Antes la
    corrección se perdía en silencio y la cita quedaba a la hora vieja.
    """
    st = estado(Slot.DATE, time="09:00", service_name="Consulta General")
    r = turno("no, mejor a las 10", st)

    assert r["intent"] == "request_appointment"
    assert r["args"]["time"] == "10:00"
    assert r["args"]["service_name"] == "Consulta General"
    assert r["args"]["_corrections"] == [{"slot": "time", "from": "09:00", "to": "10:00"}]


def test_adelantar_un_dato_que_todavia_no_se_ha_pedido():
    """"a las 3" mientras se pregunta el servicio se guarda igual."""
    r = turno("que sea a las 3 de la tarde", estado(Slot.SERVICE))
    assert r["args"]["time"] == "15:00"


def test_rechazar_sin_proponer_vuelve_a_preguntar_lo_mismo():
    r = turno("no", estado(Slot.TIME, time="09:00"))
    assert r["intent"] == "semantic_clarify"
    assert r["args"]["_expects"] == Slot.TIME


# ═══════════════════════════════════════════════════════════════════════════════
# 6. REFERENCIAS A LO MOSTRADO
# ═══════════════════════════════════════════════════════════════════════════════

def test_elegir_profesional_por_posicion():
    st = estado(Slot.PROFESSIONAL, presented=("professional", [
        {"name": "Lina Marcela Ruiz"},
        {"name": "Andrés Felipe Gómez"},
    ]), time="09:00")
    r = turno("la segunda", st)
    assert r["intent"] == "request_appointment"
    assert r["args"]["professional_name"] == "Andrés Felipe Gómez"


def test_el_servicio_dicho_entero():
    r = turno("Consulta de Medicina General", estado(Slot.SERVICE, time="09:00"))
    assert r["args"]["service_name"] == "Consulta de Medicina General"


def test_una_pregunta_no_se_guarda_como_si_fuera_el_dato():
    """
    Con una ranura abierta, cualquier texto acababa guardándose como su valor.
    Así nacían servicios llamados "quiénes trabajan ahí".
    """
    resultado = responde("¿quiénes trabajan ahí?", estado(Slot.SERVICE))
    assert resultado is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CAMBIO DE INTENCIÓN
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje", [
    "¿qué restaurantes hay cerca?",
    "hola",
    "ver mapa",
    "¿quién eres?",
])
def test_un_tema_nuevo_no_queda_atrapado_en_la_reserva(mensaje):
    """
    La ranura abierta sólo se queda con el mensaje si contiene un valor de su
    tipo. Un cambio de tema legítimo sigue su camino.
    """
    r = turno(mensaje, estado(Slot.TIME))
    assert r["intent"] != "request_appointment", mensaje


def test_preguntar_por_otro_rubro_no_lleva_al_negocio_en_foco():
    """
    "cerca" es un deíctico, y con un negocio en foco el mensaje entero se
    interpretaba como "llévame a ése" — el usuario preguntaba por restaurantes
    y aterrizaba en la clínica.
    """
    r = turno("¿qué restaurantes hay cerca?", estado(Slot.TIME))
    assert r["intent"] != "navigate_to_company"


def test_el_cambio_de_tema_no_destruye_lo_acordado():
    """Suspender no es cancelar: al volver, la reserva sigue en pie."""
    st = estado(Slot.TIME, service_name="Consulta General", date="tomorrow")
    st.suspend()

    assert st.pending_slot is None
    assert st.booking["service_name"] == "Consulta General"
    assert st.booking["business_id"] == BIZ_ID
    assert st.booking["date"] == "tomorrow"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. EL ESTADO SOBREVIVE Y SABE QUÉ FALTA
# ═══════════════════════════════════════════════════════════════════════════════

def test_el_estado_viaja_entre_turnos():
    import json

    st = estado(Slot.TIME, time="09:00", service_name="Consulta General")
    st.fulfil(Slot.DATE, "tomorrow")

    final_data = st.save({})
    recuperado = ConversationState.load(json.loads(json.dumps(final_data, default=str)))

    assert recuperado.booking["time"] == "09:00"
    assert recuperado.booking["date"] == "tomorrow"
    assert recuperado.booking["service_name"] == "Consulta General"
    assert recuperado.focus_label == NEGOCIO
    assert Slot.DATE in recuperado.confirmed


def test_saber_que_falta():
    st = estado(None)
    assert next_missing_slot(st) == Slot.SERVICE

    st.fulfil(Slot.SERVICE, "Consulta General")
    assert next_missing_slot(st) == Slot.TIME

    st.fulfil(Slot.TIME, "09:00")
    assert next_missing_slot(st) == Slot.DATE

    st.fulfil(Slot.DATE, "tomorrow")
    assert next_missing_slot(st) is None


def test_responder_cierra_la_pregunta():
    st = estado(Slot.TIME)
    st.fulfil(Slot.TIME, "09:00")
    assert st.pending_slot is None
    assert not st.is_collecting


# ═══════════════════════════════════════════════════════════════════════════════
# 9. EL LAZO COMPLETO: LA PREGUNTA SE REGISTRA Y EL TURNO SIGUIENTE LA RESPONDE
# ═══════════════════════════════════════════════════════════════════════════════

def test_la_herramienta_declara_que_esta_preguntando(monkeypatch):
    """
    Cuando Lyra pide la hora, el estado tiene que quedar marcado. Sin esta
    marca, el turno siguiente no tenía forma de saber que "9 am" respondía a
    algo: `WAITING_TIME` existía en el código pero no se asignaba nunca.
    """
    import asyncio

    import tools.nexiservice as herramientas
    from orchestrator.interceptors.nexiservice import (
        BookingState, _handle_request_appointment, get_booking_state,
    )

    async def falsa_request_appointment(**kwargs):
        assert kwargs["business_id"] == BIZ_ID
        return {
            "success": True,
            "asking": "time",
            "business_id": BIZ_ID,
            "business_name": NEGOCIO,
            "message": f"Con gusto te ayudo a agendar tu cita en {NEGOCIO}. ¿A qué hora te gustaría?",
        }

    monkeypatch.setattr(herramientas, "request_appointment", falsa_request_appointment)

    final_data: dict = {}
    st = estado(None)
    st.save(final_data)

    asyncio.run(_handle_request_appointment(
        args={"business_id": BIZ_ID, "business_name": NEGOCIO},
        messages=[], user_data={"external_user_id": ""},
        final_data=final_data, sem_state=st,
    ))

    guardado = ConversationState.load(final_data)
    assert guardado.pending_slot == Slot.TIME
    assert guardado.goal == "booking"
    assert get_booking_state(final_data) == BookingState.WAITING_TIME

    # Y con ese estado, el turno siguiente ya no se pierde.
    r = turno("9 am", guardado)
    assert r["intent"] == "request_appointment"
    assert r["args"]["time"] == "09:00"
    assert r["args"]["business_id"] == BIZ_ID


def test_al_completarse_el_dato_se_cierra_la_pregunta(monkeypatch):
    """Si la herramienta ya no pide nada, no queda ninguna ranura abierta."""
    import asyncio

    import tools.nexiservice as herramientas
    from orchestrator.interceptors.nexiservice import _handle_request_appointment

    async def falsa_request_appointment(**kwargs):
        return {"success": True, "message": "Tu cita quedó agendada.",
                "url": "/perfil/mis-reservas"}

    monkeypatch.setattr(herramientas, "request_appointment", falsa_request_appointment)

    final_data: dict = {}
    st = estado(Slot.TIME, time="09:00", date="tomorrow", service_name="Consulta General")
    st.save(final_data)

    asyncio.run(_handle_request_appointment(
        args={"business_id": BIZ_ID, "time": "09:00"}, messages=[],
        user_data={"external_user_id": ""}, final_data=final_data, sem_state=st,
    ))

    assert ConversationState.load(final_data).pending_slot is None


def test_tras_una_conversacion_larga_sigue_sabiendo_todo():
    """
    Doce turnos después, con búsquedas y charla por medio, lo acordado sigue en
    pie. No es que el historial quepa: es que el estado no depende del historial.
    """
    st = estado(Slot.SERVICE)
    st.fulfil(Slot.SERVICE, "Consulta de Medicina General")

    for mensaje in ["gracias", "¿qué restaurantes hay cerca?", "ah ok",
                    "¿y barberías?", "nada, sigamos", "¿cómo funciona esto?"]:
        r = turno(mensaje, st)
        assert r["intent"] != "request_appointment", mensaje

    st.expect(Slot.TIME, "¿A qué hora te gustaría?")
    r = turno("9 am", st)

    assert r["args"]["time"] == "09:00"
    assert r["args"]["service_name"] == "Consulta de Medicina General"
    assert r["args"]["business_id"] == BIZ_ID
