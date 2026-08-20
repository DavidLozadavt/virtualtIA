"""
tests/test_context_isolation.py — Lo que la conversación recuerda, y lo que no.

Tres cosas se estaban confundiendo en el mismo saco:

  1. La MEMORIA de la conversación (qué negocio está en foco, qué se acordó).
  2. La SALIDA de un turno (el texto, las fichas de negocios, la acción de voz).
  3. Una reserva a la espera de que el usuario entre a su cuenta.

Al guardarse todo junto y sin caducidad, un chat que el usuario daba por nuevo
arrancaba con lo de días atrás: "hola, ¿cómo estás?" recibía "tu cita ya ha sido
agendada", "agendar cita" recibía "no encontré ese servicio", y las tarjetas de
una búsqueda vieja acompañaban a cada mensaje posterior.

    python -m pytest tests/test_context_isolation.py -q
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic.lexicon import names_nothing_concrete
from core.semantic.speech_act import analyze as analyze_speech_act
from core.semantic.dialogue import read_answer
from core.semantic.types import ConversationState, Disposition
from orchestrator.memory_manager import (
    STALE_CONTEXT_SECONDS,
    carry_over_context,
)
from orchestrator.interceptors.nexiservice import (
    BookingState,
    _offer_pending_reservation,
    _present_businesses,
    _remember_pending_reservation,
    get_booking_state,
)


PENDIENTE = {
    "business_id": 15,
    "business_name": "Auto Doctor Sur Popayán",
    "service_name": "cambio de aceite",
    "professional_name": "Valentina García",
    "time": "09:00",
    "date": "tomorrow",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. UN HILO ABANDONADO NO SE RETOMA
# ═══════════════════════════════════════════════════════════════════════════════

def test_el_contexto_caduca_tras_una_larga_inactividad():
    """El caso del log: una reserva del día 16 revivida el día 20."""
    guardado = {"_booking_state": BookingState.WAITING_AUTH,
                "_pending_reservation": dict(PENDIENTE)}
    hace_cuatro_dias = datetime.now() - timedelta(days=4)

    assert carry_over_context(guardado, hace_cuatro_dias) == {}


def test_dentro_de_la_misma_sesion_el_contexto_sigue_en_pie():
    """Lo contrario también importa: el contexto SÍ se guarda mientras se habla."""
    guardado = {"selected_business_id": 15, "_pending_service": "cambio de aceite"}
    hace_un_minuto = datetime.now() - timedelta(minutes=1)

    vigente = carry_over_context(guardado, hace_un_minuto)

    assert vigente["selected_business_id"] == 15
    assert vigente["_pending_service"] == "cambio de aceite"


def test_el_limite_se_mide_en_segundos_de_silencio():
    justo_dentro = datetime.now() - timedelta(seconds=STALE_CONTEXT_SECONDS - 60)
    justo_fuera = datetime.now() - timedelta(seconds=STALE_CONTEXT_SECONDS + 60)

    assert carry_over_context({"focus": 1}, justo_dentro) == {"focus": 1}
    assert carry_over_context({"focus": 1}, justo_fuera) == {}


def test_sin_fecha_conocida_el_contexto_no_se_tira():
    assert carry_over_context({"focus": 1}, None) == {"focus": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LA RESPUESTA DE UN TURNO NO ES MEMORIA
# ═══════════════════════════════════════════════════════════════════════════════

def test_la_salida_del_turno_anterior_no_entra_en_el_siguiente():
    guardado = {
        "reply": "Encontré 6 opciones",
        "properties": [{"businesses": [{"id": 1, "name": "Vida Sana"}]}],
        "voice_action": "fit_all_businesses",
        "needs_input": True,
        "llm_error": True,
        "selected_business_id": 15,          # esto SÍ es memoria
    }

    vigente = carry_over_context(guardado, datetime.now())

    assert vigente == {"selected_business_id": 15}


def test_las_fichas_solo_acompanan_al_turno_que_las_produjo():
    """Una búsqueda pinta tarjetas; el saludo siguiente no las repite."""
    final_data = {}
    _present_businesses(final_data, [{"id": 1, "name": "Vida Sana"}])
    assert final_data["properties"] == [{"businesses": [{"id": 1, "name": "Vida Sana"}]}]

    siguiente_turno = carry_over_context(final_data, datetime.now())

    assert "properties" not in siguiente_turno
    # El recuerdo de lo mostrado sí viaja: "el primero" sigue resolviéndose.
    assert siguiente_turno["_last_businesses"] == [{"id": 1, "name": "Vida Sana"}]


def test_una_busqueda_sin_resultados_no_deja_fichas():
    final_data = {}
    _present_businesses(final_data, [])
    assert final_data["properties"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. UN SALUDO NO CREA UNA CITA
# ═══════════════════════════════════════════════════════════════════════════════

def test_al_volver_con_sesion_se_pregunta_antes_de_agendar():
    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)

    salida = _offer_pending_reservation(final_data)

    assert salida is not None
    assert "?" in salida["reply"]
    assert "cambio de aceite" in salida["reply"]
    # Nada se ha creado todavía: sigue esperando.
    assert get_booking_state(final_data) == BookingState.WAITING_AUTH
    assert final_data["_pending_reservation"]["business_id"] == 15


def test_el_recordatorio_no_se_repite_en_cada_mensaje():
    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)

    assert _offer_pending_reservation(final_data) is not None
    assert _offer_pending_reservation(final_data) is None


def test_sin_reserva_guardada_no_hay_nada_que_ofrecer():
    final_data = {"_booking_state": BookingState.WAITING_AUTH}

    assert _offer_pending_reservation(final_data) is None
    assert get_booking_state(final_data) == BookingState.IDLE


def test_el_saludo_no_dispara_la_confirmacion(monkeypatch):
    """Reproduce el turno del log: intent=greeting con state=waiting_auth."""
    from orchestrator.interceptors import nexiservice

    async def no_debe_llamarse(**kwargs):
        raise AssertionError("una cita no se crea sin que el usuario la pida")

    monkeypatch.setattr(nexiservice, "_call_confirm_appointment", no_debe_llamarse)

    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)

    salida = asyncio.run(nexiservice.pre_llm_interceptor(
        "nexiservice", "greeting", {},
        {
            "messages": [],
            "final_data": final_data,
            "user_data": {"external_user_id": "55"},
            "project_config": {},
            "conversation_id": "c1",
            "user_text": "hola como estas",
        },
    ))

    assert salida is not None
    assert "agendada" not in salida["reply"].lower()


def test_pedir_la_cita_si_la_confirma(monkeypatch):
    """La otra mitad: quien vuelve a lo suyo no tiene que repetirlo."""
    from orchestrator.interceptors import nexiservice

    llamadas = []

    async def fake_confirm(**kwargs):
        llamadas.append(kwargs)
        return {"success": True, "message": "¡Listo! Tu cita quedó agendada.",
                "url": "/perfil/mis-reservas"}

    monkeypatch.setattr(nexiservice, "_call_confirm_appointment", fake_confirm)

    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)

    salida = asyncio.run(nexiservice.pre_llm_interceptor(
        "nexiservice", "confirm_appointment", {},
        {
            "messages": [],
            "final_data": final_data,
            "user_data": {"external_user_id": "55"},
            "project_config": {},
            "conversation_id": "c1",
            "user_text": "sí, confírmala",
        },
    ))

    assert llamadas and llamadas[0]["business_id"] == 15
    assert "Listo" in salida["reply"]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. "AGENDAR CITA" NO ES EL NOMBRE DE UN SERVICIO
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("texto", [
    "agendar cita",
    "agendar",
    "cita",
    "reservar una cita",
    "quiero agendar una cita para mañana",
    "separar un turno",
    "quiero un servicio",
])
def test_una_peticion_de_cita_no_nombra_ningun_servicio(texto):
    assert names_nothing_concrete(texto) is True


@pytest.mark.parametrize("texto", [
    "consulta de medicina general",
    "cambio de aceite",
    "corte de cabello",
    "limpieza dental",
])
def test_un_servicio_de_verdad_si_nombra_algo(texto):
    assert names_nothing_concrete(texto) is False


def test_pedir_cita_no_se_guarda_como_respuesta_a_que_servicio():
    """
    Con la ranura 'service_name' abierta, "agendar cita" repetía la pregunta en
    vez de contestarla; guardarlo producía el "no encontré el servicio
    'agendar cita'" del turno siguiente.
    """
    estado = ConversationState()
    estado.booking = {"business_id": 103, "business_name": "Vida Sana"}
    estado.expect("service_name", "¿Qué servicio deseas agendar?", goal="booking")

    resultado = read_answer("agendar cita", analyze_speech_act("agendar cita"), estado)

    assert resultado is None or resultado.disposition != Disposition.ACT
    assert estado.booking.get("service_name") is None


def test_un_servicio_dicho_por_su_nombre_si_responde():
    estado = ConversationState()
    estado.booking = {"business_id": 103, "business_name": "Vida Sana"}
    estado.expect("service_name", "¿Qué servicio deseas agendar?", goal="booking")

    texto = "consulta de medicina general"
    resultado = read_answer(texto, analyze_speech_act(texto), estado)

    assert resultado is not None
    assert resultado.disposition == Disposition.ACT
    assert resultado.args["service_name"] == texto


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LO QUE EL USUARIO PIDE AHORA MANDA SOBRE LO QUE PIDIÓ ANTES
# ═══════════════════════════════════════════════════════════════════════════════

from core.semantic import llm_resolver                       # noqa: E402
from core.semantic.catalog import (                          # noqa: E402
    Concept, SemanticCatalog, _legacy_category_concepts,
)
from core.semantic.engine import understand                  # noqa: E402
from core.semantic.types import ConceptKind                  # noqa: E402


CATALOGO = SemanticCatalog([
    Concept(kind=ConceptKind.BUSINESS, label="Auto Doctor Sur Popayán",
            entity_id=15, search_term="Auto Doctor Sur Popayán"),
    Concept(kind=ConceptKind.SERVICE, label="Cambio de Aceite",
            search_term="Cambio de Aceite"),
    Concept(kind=ConceptKind.SERVICE, label="Consulta Médica General",
            search_term="Consulta Médica General"),
    # Los rubros con sus nombres populares, igual que en producción: sin ellos
    # el doble entiende peor que el catálogo real y la prueba mide otra cosa.
    *_legacy_category_concepts(),
])


@pytest.fixture
def _sin_llm():
    llm_resolver.set_completion_fn(lambda prompt: '{"categorias": [], "confianza": 0.0}')
    yield
    llm_resolver.set_completion_fn(None)


def _reserva_en_curso() -> ConversationState:
    estado = ConversationState()
    estado.booking = {
        "business_id": 15,
        "business_name": "Auto Doctor Sur Popayán",
        "service_name": "cambio de aceite",
        "time": "09:00",
        "date": "tomorrow",
    }
    return estado


def test_un_servicio_nuevo_reemplaza_al_que_quedo_en_memoria(_sin_llm):
    """
    Del log: "reservar un medicina general" creó una cita de cambio de aceite,
    porque la ranura ya venía llena de la conversación anterior.
    """
    estado = _reserva_en_curso()

    resultado = understand("reservar una consulta médica general",
                           state=estado, catalog=CATALOGO)

    assert resultado.intent == "request_appointment"
    assert resultado.args["service_name"] == "Consulta Médica General"
    assert any(c["slot"] == "service_name" for c in resultado.corrections)


def test_pedir_cita_sin_nombrar_nada_conserva_lo_acordado(_sin_llm):
    """El reverso: no nombrar servicio no borra el que ya estaba."""
    estado = _reserva_en_curso()

    resultado = understand("quiero reservar", state=estado, catalog=CATALOGO)

    assert resultado.args["service_name"] == "cambio de aceite"
    assert resultado.corrections == []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. UN "SÍ" ES UN SÍ, AUNQUE VENGA CON UNA ERRATA DETRÁS
# ═══════════════════════════════════════════════════════════════════════════════

def _reserva_ofrecida() -> ConversationState:
    """Lo que queda tras "¿Te agendo en Óptica Claridad Este Popayán?"."""
    estado = ConversationState()
    estado.set_focus(ConceptKind.BUSINESS, 12, "Óptica Claridad Este Popayán")
    estado.booking = {"business_id": 12, "business_name": "Óptica Claridad Este Popayán"}
    return estado


@pytest.mark.parametrize("mensaje", [
    "si por favot",     # el caso del chat: una letra de más y se perdía la cita
    "si porfavot",
    "si por fabor",
])
def test_una_errata_detras_del_si_no_borra_el_si(mensaje, _sin_llm):
    """
    "Sí por favot" dejaba de agotarse en fórmulas sociales por culpa de la
    errata, se leía como un sintagma nominal y el usuario que acababa de aceptar
    una cita recibía "¿cuál es tu solicitud?".
    """
    resultado = understand(mensaje, state=_reserva_ofrecida(),
                           mentioned_city="Popayán", catalog=CATALOGO)

    assert resultado.disposition == Disposition.ACT
    assert resultado.intent == "request_appointment"
    assert resultado.args["business_id"] == 15 or resultado.args["business_id"] == 12


@pytest.mark.parametrize("mensaje", ["claro que si", "si por favor", "si claro"])
def test_las_formulas_con_relleno_siguen_siendo_afirmaciones(mensaje):
    """"Claro QUE sí": la conjunción no convierte un sí en otra cosa."""
    assert analyze_speech_act(mensaje).act == "affirm"


def test_un_si_con_contenido_real_no_se_traga_el_contenido(_sin_llm):
    """"Sí, un restaurante" dice dos cosas; la segunda no se puede perder."""
    resultado = understand("si, un restaurante", state=_reserva_ofrecida(),
                           mentioned_city="Popayán", catalog=CATALOGO)

    assert resultado.intent == "search_businesses"


def test_sin_pregunta_abierta_un_si_con_ruido_no_agenda_nada(_sin_llm):
    """Sin nada pendiente nadie está afirmando: no hay reserva que continuar."""
    resultado = understand("si por favot", state=ConversationState(),
                           mentioned_city="Popayán", catalog=CATALOGO)

    assert resultado.intent != "request_appointment"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. LA CITA QUEDA A NOMBRE DE QUIEN LA PIDIÓ
# ═══════════════════════════════════════════════════════════════════════════════
#
# La aplicación tiene tres tablas de identidad —`usuario` (la cuenta),
# `persona` (quién es) y `tercero` (su ficha de cliente)— y cada parte del
# sistema leía una distinta. El chat recibe `persona.id`; se interpretaba como
# `usuario.id` y se unía contra `tercero`. Los tres rangos de ids se solapan,
# así que no fallaba: acertaba a otra persona, y la cita salía a nombre de una
# empresa ajena y archivada bajo un tercero que no era el suyo, con lo que no
# aparecía en "Mis Reservas" de quien la había pedido.

import tools.nexiservice as _tools                            # noqa: E402


class _CursorFalso:
    """Responde a las tres consultas de identidad como lo haría la base."""

    def __init__(self, filas):
        self.filas = filas
        self.ultima = None

    def execute(self, sql, params=()):
        if "FROM persona p" in sql:
            self.ultima = self.filas["persona"] if params[0] == 55 else None
        elif "FROM usuario u" in sql:
            self.ultima = self.filas["usuario"] if params[0] == 125 else None
        elif "FROM tercero" in sql:
            self.ultima = self.filas["tercero"] if params[0] == "yo@ejemplo.com" else None

    def fetchone(self):
        return self.ultima

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ConexionFalsa:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


FILAS = {
    "persona": {"persona_id": 55, "nombre": "Sofía  Restrepo",
                "email": "yo@ejemplo.com", "usuario_id": 125},
    "usuario": {"persona_id": 55, "nombre": "Sofía  Restrepo",
                "email": "yo@ejemplo.com", "usuario_id": 125},
    "tercero": {"id": 452},
}


@pytest.fixture
def _base_falsa(monkeypatch):
    cursor = _CursorFalso(FILAS)
    monkeypatch.setattr(
        "core.database.get_connection", lambda *a, **k: _ConexionFalsa(cursor)
    )
    return cursor


def test_el_id_del_chat_se_lee_como_persona(_base_falsa):
    """El frontend manda `persona.id`; leerlo como cuenta apuntaba a otro."""
    identidad = asyncio.run(_tools.resolve_booking_identity(55))

    assert identidad["via"] == "persona"
    assert identidad["persona_id"] == 55
    assert identidad["usuario_id"] == 125


def test_la_reserva_se_archiva_donde_el_perfil_la_lee(_base_falsa):
    """"Mis Reservas" busca por `tercero.email`: ahí tiene que quedar."""
    identidad = asyncio.run(_tools.resolve_booking_identity(55))

    assert identidad["tercero_id"] == 452


def test_el_nombre_sale_limpio(_base_falsa):
    """Un segundo nombre vacío dejaba dos espacios en mitad del saludo."""
    identidad = asyncio.run(_tools.resolve_booking_identity(55))

    assert identidad["nombre"] == "Sofía Restrepo"


def test_los_canales_que_mandan_la_cuenta_siguen_funcionando(_base_falsa):
    """Voz y WhatsApp no pasan por el login web: se conserva esa lectura."""
    identidad = asyncio.run(_tools.resolve_booking_identity(125))

    assert identidad["via"] == "usuario"
    assert identidad["persona_id"] == 55


def test_un_id_desconocido_no_inventa_una_identidad(_base_falsa):
    assert asyncio.run(_tools.resolve_booking_identity(999999)) is None
    assert asyncio.run(_tools.resolve_booking_identity(None)) is None
