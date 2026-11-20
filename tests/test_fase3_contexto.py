"""
Fase 3: estado de la conversación, propuestas y confirmaciones.

Lo que se comprueba es la regla, no frases sueltas:

  * Un "sí" / "no" / "eso" / "por favor" sólo acepta o rechaza lo que Lyra
    PROPUSO en la respuesta inmediatamente anterior (`live_offer`).
  * Lo que sigue en memoria (una reserva vieja, el negocio en foco) no es una
    propuesta: sin propuesta vigente, se pregunta.
  * Buscar otra cosa estaciona la reserva en curso; no se vuelve a ejecutar
    sola, pero se puede retomar pidiéndolo.
  * El estado de un turno superado no pisa al de uno más reciente.
"""

import asyncio
import uuid

import pytest

from core.semantic import ConversationState, understand
from core.semantic.types import ConceptKind, Disposition


@pytest.fixture
def _sin_llm(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "LLM_EXTERNAL_ENABLED", False)


def _estado_con_restaurante_en_memoria() -> ConversationState:
    """Lo que dejaba una recomendación antes de la fase 3: foco + ranuras de reserva."""
    st = ConversationState()
    st.begin_turn(3)
    st.remember_list(ConceptKind.BUSINESS, [
        {"id": 5, "name": "Fogón Criollo Oeste Popayán", "category": "Restaurantes y Gastronomía"},
        {"id": 3, "name": "Sazón de la Abuela Sur Popayán", "category": "Restaurantes y Gastronomía"},
    ])
    st.set_focus(ConceptKind.BUSINESS, 5, "Fogón Criollo Oeste Popayán")
    st.booking = {"business_id": 5, "business_name": "Fogón Criollo Oeste Popayán"}
    return st


# ── La propuesta caduca con el turno ─────────────────────────────────────────

def test_la_propuesta_solo_vale_en_el_turno_siguiente():
    st = ConversationState()
    st.begin_turn(4)
    st.propose("search_care", {"level": "concern"}, label="buscar atención médica")
    st.begin_turn(5)
    assert st.live_offer()["intent"] == "search_care"
    st.begin_turn(6)
    assert st.live_offer() is None


def test_la_caducidad_no_depende_de_begin_turn():
    """Aunque una ruta no limpie el estado, una propuesta vieja no es vigente."""
    st = ConversationState()
    st.turn = 4
    st.propose("search_care", {}, label="x")
    st.turn = 7
    assert st.live_offer() is None


def test_la_pregunta_abierta_tambien_caduca():
    st = ConversationState()
    st.begin_turn(2)
    st.expect("time", "¿A qué hora?", goal="booking")
    st.begin_turn(3)
    assert st.is_collecting
    st.begin_turn(5)
    assert not st.is_collecting


def test_el_estado_sobrevive_la_serializacion():
    st = ConversationState()
    st.begin_turn(9)
    st.propose("request_appointment", {"business_id": 1}, label="agendar")
    st.parked_booking = {"business_id": 2}
    otro = ConversationState.from_dict(st.to_dict())
    assert otro.offer == st.offer and otro.turn == 9 and otro.parked_booking == {"business_id": 2}


# ── Confirmaciones ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("respuesta", ["sí", "sí, por favor", "claro", "dale", "por favor", "eso", "listo", "ok"])
def test_el_si_acepta_la_propuesta_vigente_y_no_lo_que_hay_en_memoria(respuesta, _sin_llm):
    """El caso real: restaurante recomendado, luego "¿te busco centros médicos?", luego "sí"."""
    st = _estado_con_restaurante_en_memoria()
    st.propose("search_care", {"level": "concern"}, label="buscar atención médica")
    st.begin_turn(4)

    r = understand(respuesta, state=st, mentioned_city="Popayán")

    assert r.intent == "search_care", r.trace
    assert r.intent != "request_appointment"


@pytest.mark.parametrize("respuesta", ["sí", "sí, por favor", "dale", "claro que sí"])
def test_un_si_sin_propuesta_pregunta_en_vez_de_agendar(respuesta, _sin_llm):
    st = _estado_con_restaurante_en_memoria()
    st.begin_turn(4)  # nada propuesto en el turno 3

    r = understand(respuesta, state=st, mentioned_city="Popayán")

    assert r.disposition == Disposition.CLARIFY and r.final
    assert r.intent != "request_appointment"
    assert "?" in (r.clarification or "")


@pytest.mark.parametrize("respuesta", ["no", "mejor no", "no gracias", "ahora no"])
def test_el_no_rechaza_la_propuesta(respuesta, _sin_llm):
    st = ConversationState()
    st.begin_turn(1)
    st.propose("search_care", {}, label="buscar atención médica")
    st.begin_turn(2)

    r = understand(respuesta, state=st, mentioned_city="Popayán")

    assert r.intent == "decline_offer", r.trace


def test_un_si_a_una_pregunta_con_alternativas_pregunta_cual(_sin_llm):
    st = ConversationState()
    st.begin_turn(1)
    ref = {"business_id": 2, "business_name": "Fogón Criollo Norte Popayán"}
    st.propose("get_business_services", ref, options=[
        {"intent": "get_business_services", "args": ref, "label": "ver sus servicios"},
        {"intent": "request_appointment", "args": ref, "label": "agendar una cita"},
    ])
    st.begin_turn(2)

    r = understand("sí", state=st, mentioned_city="Popayán")

    assert r.disposition == Disposition.CLARIFY
    assert "ver sus servicios" in r.clarification and "agendar una cita" in r.clarification


def test_una_hora_con_negocio_en_foco_pero_sin_reserva_se_propone(_sin_llm):
    st = _estado_con_restaurante_en_memoria()
    st.booking = {}
    st.begin_turn(4)

    r = understand("mañana a las 9 am", state=st, mentioned_city="Popayán",
                   temporal={"date": "2026-11-18", "time": "09:00"})

    assert r.intent != "request_appointment"
    assert r.propose and r.propose["intent"] == "request_appointment"


def test_si_con_contenido_no_se_toma_como_aceptacion(_sin_llm):
    st = ConversationState()
    st.begin_turn(1)
    st.propose("search_care", {}, label="x")
    st.begin_turn(2)

    r = understand("sí, un restaurante", state=st, mentioned_city="Popayán")

    assert r.intent == "search_businesses"


# ── Volver a algo anterior ───────────────────────────────────────────────────

def test_cambiar_de_lista_guarda_la_anterior():
    st = ConversationState()
    st.active_domain_label = "restaurantes"
    st.remember_list(ConceptKind.BUSINESS, [{"id": 1, "name": "A", "category": "Restaurantes y Gastronomía"}])
    st.active_domain_label = "veterinarias"
    st.remember_list(ConceptKind.BUSINESS, [{"id": 9, "name": "B", "category": "Mascotas"}])

    assert st.history[-1]["category"] == "Restaurantes y Gastronomía"
    assert [i["entity_id"] for i in st.history[-1]["items"]] == [1]


@pytest.mark.parametrize("frase", ["volvamos a los restaurantes", "regresa a lo anterior", "volvamos a lo de antes"])
def test_volver_es_recuperar_contexto(frase, _sin_llm):
    r = understand(frase, state=ConversationState(), mentioned_city="Popayán")
    assert r.intent == "restore_context", r.trace


def test_retomar_la_cita_es_volver_a_la_reserva(_sin_llm):
    r = understand("retomemos la cita", state=ConversationState(), mentioned_city="Popayán")
    assert r.intent == "restore_context" and r.args["booking"]


def test_estacionar_y_retomar_la_reserva():
    st = ConversationState()
    st.goal = "booking"
    st.booking = {"business_id": 2, "time": "09:00"}
    assert st.park_booking()
    assert st.goal is None and not st.booking and st.parked_booking["business_id"] == 2
    assert st.resume_booking()
    assert st.goal == "booking" and st.booking["time"] == "09:00"


# ── Requisitos tipados ───────────────────────────────────────────────────────

def test_un_requisito_repetido_cuenta_una_vez():
    from orchestrator.interceptors.nexiservice import request_constraints
    kind, reqs = request_constraints(
        "Recomienda un restaurante que en su menú tenga pescado, me encanta el pescado",
        {}, "Restaurantes y Gastronomía")
    assert kind == "restaurante"
    assert reqs == ["pescado"]


def test_un_tipo_de_establecimiento_mas_estrecho_que_la_categoria_es_obligatorio():
    from orchestrator.interceptors.nexiservice import request_constraints
    assert request_constraints("Recomiéndame clínica", {}, "Salud y Bienestar") == ("clinica", [])
    # El tipo que coincide con la categoría no añade exigencia.
    assert request_constraints("busca restaurantes", {}, "Restaurantes y Gastronomía") == ("", [])


@pytest.mark.parametrize("frase", ["ahora busca veterinarias", "mejor busca veterinarias"])
def test_marcadores_de_discurso_no_son_contenido(frase):
    from core.semantic.speech_act import analyze
    a = analyze(frase)
    assert a.content_terms == ["veterinarias"]
    assert "recommend" not in a.frames


# ── El turno viejo no pisa al nuevo (base local lyra_db) ─────────────────────

def test_el_estado_de_un_turno_superado_no_se_guarda():
    from core.database import get_connection
    from orchestrator.memory_manager import (
        get_or_create_conversation, get_or_create_user, next_turn, update_conversation_final_data,
    )
    user = get_or_create_user("nexiservice", "test-fase3-turnos")
    cid = str(uuid.uuid4())
    get_or_create_conversation(cid, user["id"], "nexiservice")
    try:
        viejo = next_turn(cid, {})
        nuevo = next_turn(cid, {})
        assert nuevo == viejo + 1
        update_conversation_final_data(cid, {"_turn": nuevo, "quien": "nuevo"}, turn=nuevo)
        update_conversation_final_data(cid, {"_turn": viejo, "quien": "viejo"}, turn=viejo)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT JSON_UNQUOTE(JSON_EXTRACT(final_data, '$.quien')) AS q "
                        "FROM lyra_conversations WHERE id = %s", (cid,))
            assert cur.fetchone()["q"] == "nuevo"
    finally:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM lyra_conversations WHERE id = %s", (cid,))
            conn.commit()


# ── El caso real, de punta a punta por ChatService ───────────────────────────

def test_caso_real_dolor_de_cabeza_y_si_por_favor(_sin_llm):
    from scripts.replay_turns import conversation, service

    async def run():
        return await conversation(service(), "nexiservice", [
            "Busca restaurantes",
            "Recomienda un restaurante que en su menú tenga pescado, me encanta el pescado",
            "Mira que tengo un dolor de cabeza impresionante",
            "Sí, por favor",
        ], user="test-fase3", active_city="Popayán")

    turnos = asyncio.run(run())
    _, pescado = turnos[1]
    _, si = turnos[3]
    assert "pescado pescado" not in pescado.reply
    assert si.voice_action != "navigate"
    assert "agendar" not in si.reply.lower()
    negocios = (si.voice_action_payload or {}).get("businesses") or []
    assert negocios and all("Restaurantes" not in b.get("category", "") for b in negocios)
