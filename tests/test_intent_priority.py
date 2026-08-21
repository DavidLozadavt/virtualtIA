"""
tests/test_intent_priority.py — La intención de AHORA manda sobre el flujo de antes.

El fallo que originó este paquete no era de anclaje ni de búsqueda: era de
prioridad. El sistema leía el estado primero y el mensaje después, así que
cualquier cosa dicha con una reserva a medias se interpretaba como parte de esa
reserva.

    Usuario: no, yo no quiero agendar, yo quiero saber qué negocios
             ofrecen medicina general
    Lyra:    Claro, ¿a qué hora te viene mejor?

Aquí se fija el orden correcto: primero qué está haciendo el usuario con ESTE
mensaje, después qué aporta el contexto. Y se fija su consecuencia más
importante — que tener datos en memoria no es tener una intención de reservar.

    python -m pytest tests/test_intent_priority.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic import llm_resolver, polarity
from core.semantic.dialogue import GOAL_BOOKING
from core.semantic.engine import understand
from core.semantic.speech_act import analyze
from core.semantic.types import Act, ConceptKind, ConversationState, Disposition

from test_semantic_understanding import CATALOG

NEGOCIO = "Consultorio Médico Vida Sana Popayán"
BIZ_ID = 101
RUBRO = "Consultorios y Centros Médicos"
SERVICIO = "Consulta Médica General"


@pytest.fixture(autouse=True)
def _no_live_llm():
    """Sin resolutor externo: se mide lo que el sistema entiende por sí mismo."""
    llm_resolver.set_completion_fn(lambda prompt: '{"categorias": [], "confianza": 0.0}')
    yield
    llm_resolver.set_completion_fn(None)


def tras_una_busqueda() -> ConversationState:
    """Estado después de que Lyra mostrara UN negocio. No hay reserva ninguna."""
    st = ConversationState()
    st.remember_list(ConceptKind.BUSINESS, [{"id": BIZ_ID, "name": NEGOCIO, "category": RUBRO}])
    st.set_focus(ConceptKind.BUSINESS, BIZ_ID, NEGOCIO)
    st.active_domain = RUBRO
    return st


def reservando(pending: str = "time") -> ConversationState:
    """Estado con una reserva realmente en curso y una pregunta abierta."""
    st = tras_una_busqueda()
    st.booking = {"business_id": BIZ_ID, "business_name": NEGOCIO, "service_name": SERVICIO}
    st.goal = GOAL_BOOKING
    st.pending_slot = pending
    return st


def lee(mensaje: str, estado: ConversationState):
    return understand(mensaje, state=estado, catalog=CATALOG, allow_llm=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. POLARIDAD: RECHAZAR NO ES PREGUNTAR
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje", [
    "no quiero agendar",
    "no quiero reservar",
    "no, yo no quiero agendar",
    "no necesito una cita",
    "eso no, no quiero una cita",
])
def test_negar_el_agendamiento_retira_el_marco_de_cita(mensaje):
    """"No quiero agendar" nombra la cita para descartarla, no para pedirla."""
    a = analyze(mensaje)
    assert a.corrective
    assert a.rejects_booking
    assert "appointment" not in a.frames


@pytest.mark.parametrize("mensaje", [
    "no tienen citas manana?",
    "no hay citas disponibles?",
])
def test_negar_un_verbo_de_existencia_sigue_siendo_una_pregunta(mensaje):
    """
    "¿No tienen citas?" pregunta por el mundo, no corrige a Lyra.

    Es la distinción que hace segura toda la capa: sin ella, cualquier "no"
    borraría el contenido del mensaje.
    """
    a = analyze(mensaje)
    assert not a.rejects_booking
    assert "appointment" in a.frames


def test_la_mitad_afirmativa_es_la_que_manda():
    reading = polarity.read(
        "no, yo no quiero agendar, yo quiero saber que negocios ofrecen medicina general"
    )
    assert reading.corrective
    assert "agendar" in reading.rejected
    assert reading.affirmed.startswith("yo quiero saber")
    assert "agendar" not in reading.affirmed


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LA CONVERSACIÓN OBLIGATORIA
# ═══════════════════════════════════════════════════════════════════════════════

def test_conversacion_completa_de_consulta():
    """
    La conversación entera del informe, turno por turno.

    Ninguno de los cuatro mensajes pide una cita, y en ninguno debe abrirse una.
    """
    st = ConversationState()

    saludo = lee("hola como estas", st)
    assert saludo.act == Act.GREET
    assert saludo.disposition == Disposition.CONVERSE

    busqueda = lee("quisiera saber que negocios ofrecen medicina general", st)
    assert busqueda.intent == "search_businesses"

    # Lyra muestra el único resultado que tiene.
    st.remember_list(ConceptKind.BUSINESS, [{"id": BIZ_ID, "name": NEGOCIO, "category": RUBRO}])
    st.set_focus(ConceptKind.BUSINESS, BIZ_ID, NEGOCIO)
    st.active_domain = RUBRO

    mas = lee("pero es el unico el que tiene medicina general o hay mas?", st)
    assert mas.intent == "search_businesses"
    assert not st.booking

    correccion = lee(
        "no, yo no quiero agendar, yo quiero saber que negocios ofrecen "
        "el servicio de medicina general",
        st,
    )
    assert correccion.intent == "search_businesses"
    assert not st.booking
    assert st.pending_slot is None


def test_la_correccion_cancela_la_reserva_en_curso():
    """Con la reserva abierta y la hora pedida, "no quiero agendar" la termina."""
    st = reservando()
    u = lee(
        "no, yo no quiero agendar, yo quiero saber que negocios ofrecen "
        "el servicio de medicina general",
        st,
    )
    assert u.intent == "search_businesses"
    assert u.cancels_goal
    assert st.booking == {}
    assert st.goal is None
    assert st.pending_slot is None


def test_rechazo_sin_recambio_tambien_cancela():
    """"No quiero agendar" a secas no pide nada nuevo, pero cierra lo abierto."""
    st = reservando()
    u = lee("no quiero agendar", st)
    assert u.intent != "request_appointment"
    assert u.cancels_goal
    assert st.booking == {}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TENER CONTEXTO NO ES QUERER RESERVAR
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje", [
    "que otros negocios ofrecen medicina general?",
    "hay mas negocios con medicina general?",
    "que otras opciones hay de medicina general?",
])
def test_pedir_mas_opciones_no_abre_una_reserva(mensaje):
    """
    Un negocio en foco viene de haberlo mostrado, no de haberlo elegido.

    Con la condición anterior —"hay negocio en foco y se nombra un servicio"—
    cualquier pregunta posterior sobre el servicio se convertía en una cita.
    """
    st = tras_una_busqueda()
    u = lee(mensaje, st)
    assert u.intent == "search_businesses"


def test_preguntar_por_la_disponibilidad_no_es_reservar():
    st = tras_una_busqueda()
    u = lee("tienen citas manana?", st)
    assert u.intent == "get_business_availability"
    assert not st.booking


def test_preguntar_el_precio_no_es_reservar():
    st = tras_una_busqueda()
    u = lee("cuanto cuesta?", st)
    assert u.intent == "get_business_services"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. UNA PREGUNTA ABIERTA NO SE QUEDA CON CUALQUIER MENSAJE
# ═══════════════════════════════════════════════════════════════════════════════

def test_la_ranura_abierta_no_captura_un_cambio_de_tema():
    """
    Estado: se pidió la hora. El usuario pregunta otra cosa.

    Ni se lee como hora, ni se vuelve a preguntar la hora: se atiende lo que
    preguntó. La reserva sigue viva para cuando vuelva.
    """
    st = reservando(pending="time")
    u = lee("que otros negocios ofrecen medicina general?", st)
    assert u.intent == "search_businesses"
    assert st.booking.get("business_id") == BIZ_ID


def test_una_pregunta_de_precio_no_responde_a_la_hora():
    st = reservando(pending="time")
    u = lee("no, primero quiero saber cuanto cuesta", st)
    assert u.intent != "request_appointment"
    assert "hora" not in (u.clarification or "")


def test_preguntar_si_hay_citas_manana_no_fija_la_fecha():
    """
    "¿Tienen citas mañana?" consulta la agenda; no compromete el día.

    Recoger ese "mañana" como fecha de la reserva era convertir una pregunta en
    un dato que el usuario nunca dio.
    """
    st = reservando(pending="time")
    lee("tienen citas manana?", st)
    assert not st.booking.get("date")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LO QUE NO DEBE ROMPERSE
# ═══════════════════════════════════════════════════════════════════════════════

def test_una_correccion_de_hora_sigue_corrigiendo_la_hora():
    """"No, mejor a las 10" niega el valor, no el objetivo: la reserva sigue."""
    st = reservando(pending="time")
    u = lee("no, mejor a las 10", st)
    assert u.intent == "request_appointment"
    assert u.args.get("time") == "10:00"
    assert not u.cancels_goal


def test_pedir_cita_explicitamente_sigue_agendando():
    st = tras_una_busqueda()
    u = lee("quiero agendar una cita de medicina general", st)
    assert u.intent == "request_appointment"


def test_el_marcador_de_discurso_no_viaja_como_contenido():
    """"antes de eso" ordena el turno; no es nada que buscar en el catálogo."""
    a = analyze("antes de eso, que restaurantes hay cerca?")
    assert "antes" not in a.content_terms


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LA RESPUESTA TIENE QUE CONTESTAR LO QUE SE PREGUNTÓ
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_repetir_la_busqueda_responde_que_no_hay_mas(monkeypatch):
    """
    "¿Es el único?" devuelve el mismo negocio. Eso no es un hallazgo nuevo.

    Volver a decir "Encontré X. ¿Quieres agendar?" es dejar sin contestar la
    pregunta: lo que el usuario quiere saber es la cantidad.
    """
    import tools.nexiservice as tools_nexi
    from orchestrator.interceptors.nexiservice import _handle_search_businesses

    encontrado = [{"id": BIZ_ID, "name": NEGOCIO, "category": RUBRO}]

    async def _fake(**kwargs):
        return {"success": True, "businesses": encontrado, "city": "Popayán"}

    monkeypatch.setattr(tools_nexi, "find_businesses_offering", _fake)
    monkeypatch.setattr(tools_nexi, "search_businesses", _fake)

    st = ConversationState()
    args = {"category": SERVICIO, "city": "Popayán",
            "_grounded_kind": ConceptKind.SERVICE, "_grounded_terms": [SERVICIO]}

    primera = await _handle_search_businesses(dict(args), {}, {}, st)
    assert "Encontré" in primera["reply"]

    segunda = await _handle_search_businesses(dict(args), {}, primera["final_data"], st)
    assert "único" in segunda["reply"]
    assert "Encontré" not in segunda["reply"]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. LO QUE EL USUARIO YA DIJO NO SE LE VUELVE A PREGUNTAR
# ═══════════════════════════════════════════════════════════════════════════════

def test_el_servicio_buscado_entra_en_la_reserva():
    """
    Buscar por un servicio y agendar después es UNA conversación.

    El usuario abrió con "medicina general"; preguntarle el servicio al agendar
    —y encima con una lista de cuatro— es hacerle repetir lo único que pidió.
    """
    st = tras_una_busqueda()
    st.topic_service = SERVICIO

    u = lee("quisiera agendar", st)
    assert u.intent == "request_appointment"
    assert u.args.get("service_name") == SERVICIO


def test_el_servicio_del_tema_no_abre_por_si_solo_una_reserva():
    """Tenerlo en memoria no es querer reservarlo: sigue siendo un criterio."""
    st = tras_una_busqueda()
    st.topic_service = SERVICIO

    u = lee("que otros negocios ofrecen medicina general?", st)
    assert u.intent == "search_businesses"
    assert not st.booking


def test_lo_que_el_usuario_nombra_ahora_manda_sobre_el_tema():
    """Si al agendar pide otro servicio, ése es el que vale."""
    st = tras_una_busqueda()
    st.topic_service = SERVICIO

    u = lee("quiero agendar un masaje relajante", st)
    assert u.intent == "request_appointment"
    assert u.args.get("service_name") == "Masaje Relajante"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. UNA OFERTA NO ES UNA PREGUNTA
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje", ["es el unico?", "es el unico", "son todos?", "hay mas?"])
def test_preguntar_por_la_cantidad_repite_la_busqueda(mensaje):
    """
    "¿Es el único?" pregunta si la lista está completa.

    No nombra nada nuevo, así que la consulta es el tema que ya estaba sobre la
    mesa. Buscar en el directorio entero devolvía cualquier cosa.
    """
    st = tras_una_busqueda()
    st.topic_service = SERVICIO

    u = lee(mensaje, st)
    assert u.intent == "search_businesses"
    assert u.args.get("category") == SERVICIO


def test_el_ordinal_que_ordena_el_turno_no_selecciona():
    """"Primero quiero saber…" dice en qué orden, no "el primero de la lista"."""
    a = analyze("primero quiero saber cuanto cuesta")
    assert a.ordinal is None


def test_una_oferta_no_abre_la_ranura_de_servicio():
    """
    El atajo del router leía el markdown del asistente para saber qué se pidió.

    "¿Te gustaría ver sus servicios o agendar?" es una oferta y contiene las dos
    palabras que ese raspado buscaba, así que "es el único?" se guardaba como el
    nombre de un servicio. La ranura abierta la declara la herramienta, no el
    texto.
    """
    from orchestrator.intent_router import detect_intent

    st = tras_una_busqueda()
    st.topic_service = SERVICIO

    r = detect_intent("es el unico?", "nexiservice", current_context={
        "semantic_state": st.to_dict(),
        "last_assistant_msg": "Encontré **X**. ¿Te gustaría ver sus servicios o agendar?",
    })
    assert r["intent"] != "request_appointment"
    assert r["args"].get("service_name") != "es el unico?"
