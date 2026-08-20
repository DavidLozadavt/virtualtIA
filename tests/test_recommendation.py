"""
tests/test_recommendation.py — "Recomiéndame uno para medicina general, el mejor".

Recomendar no es buscar. Una persona a la que le preguntan eso hace dos cosas
antes de mojarse: mira QUIÉN presta ese servicio y mira QUÉ dijeron los que ya
fueron. Sólo entonces se decide, y dice por qué.

El sistema hacía media cosa: el intent `recommend_businesses` se detectaba pero
no lo atendía nadie, así que la pregunta se caía hasta el mensaje de último
recurso —"¿buscas un negocio, servicios o agendar?"—, que es justo lo que el
usuario acababa de contestar. Y la herramienta que existía sólo miraba el nombre
y el rubro de la empresa, nunca sus servicios.

    python -m pytest tests/test_recommendation.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic import llm_resolver
from core.semantic.catalog import Concept, SemanticCatalog, _legacy_category_concepts
from core.semantic.engine import understand
from core.semantic.types import ConceptKind, ConversationState, Disposition
from orchestrator.interceptors import nexiservice
from orchestrator.interceptors.nexiservice import _handle_recommend_businesses
from tools.nexiservice import _significant_words, _word_matches


CATALOGO = SemanticCatalog([
    Concept(kind=ConceptKind.BUSINESS_CATEGORY, label="Restaurantes y Gastronomía",
            entity_id=1, search_term="Restaurantes y Gastronomía",
            aliases=["restaurante"]),
    Concept(kind=ConceptKind.SERVICE, label="Consulta de Medicina General",
            search_term="Consulta de Medicina General"),
    Concept(kind=ConceptKind.BUSINESS, label="Óptica Claridad Este Popayán",
            entity_id=12, search_term="Óptica Claridad Este Popayán"),
    # Los rubros con sus nombres populares, igual que en producción: sin ellos
    # el doble entiende peor que el catálogo real y la prueba mide otra cosa.
    *_legacy_category_concepts(),
])


@pytest.fixture(autouse=True)
def _sin_llm():
    """Se mide lo que el sistema entiende por sí mismo, sin modelo externo."""
    llm_resolver.set_completion_fn(lambda prompt: '{"categorias": [], "confianza": 0.0}')
    yield
    llm_resolver.set_completion_fn(None)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LA PREGUNTA LLEGA A DONDE TIENE QUE LLEGAR
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje", [
    "recomiendame uno para medicina general, el mejor",
    "recomiéndame el mejor para medicina general",
    "cuál es el mejor para medicina general",
])
def test_pedir_una_recomendacion_se_entiende_como_tal(mensaje):
    u = understand(mensaje, mentioned_city="Popayán", catalog=CATALOGO)

    assert u.disposition == Disposition.ACT
    assert u.intent == "recommend_businesses"
    assert u.args["category"] == "Consulta de Medicina General"


def test_un_superlativo_no_lo_secuestra_lo_que_hubiera_en_pantalla():
    """
    Con un negocio en foco, "¿cuál es el mejor restaurante?" se leía como una
    propiedad de ESE negocio y salía por conversación: el usuario recibía un
    "no estoy seguro de haberte entendido" por una pregunta clarísima.
    """
    estado = ConversationState()
    estado.set_focus(ConceptKind.BUSINESS, 12, "Óptica Claridad Este Popayán")

    u = understand("cual es el mejor restaurante", state=estado,
                   mentioned_city="Popayán", catalog=CATALOGO)

    assert u.intent == "recommend_businesses"
    # El término que viaja a la base es el del catálogo, no la frase del usuario.
    assert u.args["category"] == "restaurante"


def test_sin_contenido_propio_el_superlativo_usa_el_rubro_abierto():
    """"¿Y cuál es el mejor?" justo después de una lista de médicos."""
    estado = ConversationState()
    estado.active_domain = "medico"
    estado.set_focus(ConceptKind.BUSINESS, 12, "Óptica Claridad Este Popayán")

    u = understand("cual es el mejor de esos", state=estado,
                   mentioned_city="Popayán", catalog=CATALOGO)

    assert u.intent == "recommend_businesses"
    assert u.args["category"] == "medico"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EL TÉRMINO SE COMPARA COMO SE HABLA, NO COMO SE ESCRIBE
# ═══════════════════════════════════════════════════════════════════════════════

def test_las_palabras_vacias_no_cuentan_como_contenido():
    assert _significant_words("para medicina general") == ["medicina", "general"]
    assert _significant_words("de la") == []


@pytest.mark.parametrize("palabra,texto,esperado", [
    ("medicina", {"consulta", "medica", "general"}, True),   # medicina ≈ médica
    ("general",  {"consulta", "medica", "general"}, True),
    ("medicina", {"mecanica", "rapida"}, False),
    ("medicina", {"consulta", "pediatrica"}, False),
])
def test_una_palabra_se_reconoce_aunque_este_dicha_de_otra_forma(palabra, texto, esperado):
    """'medicina general' y 'Consulta Médica General' son lo mismo al hablar."""
    assert _word_matches(palabra, texto) is esperado


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LA RESPUESTA DICE A QUIÉN Y POR QUÉ
# ═══════════════════════════════════════════════════════════════════════════════

CANDIDATOS = [
    {"id": 12, "name": "Óptica Claridad Este Popayán", "rating": 4.6, "reviews": 3,
     "matched_service": "Consulta Médica General", "match_score": 2,
     "comment": "Una joya escondida, el trato es inmejorable."},
    {"id": 10, "name": "Fisioterapia Total Norte Popayán", "rating": 4.5, "reviews": 2,
     "matched_service": "Consulta Médica General", "match_score": 2, "comment": None},
    {"id": 103, "name": "Consultorio Médico Vida Sana Popayán", "rating": None,
     "reviews": 0, "matched_service": "Consulta de Medicina General",
     "match_score": 2, "comment": None},
]


def _recomendar(candidatos, monkeypatch, final_data=None):
    async def fake_rank(term, city=None, **kwargs):
        return {"success": True, "city": "Popayán", "category": term,
                "businesses": candidatos}

    monkeypatch.setattr("tools.nexiservice.rank_businesses_for", fake_rank)
    final_data = final_data if final_data is not None else {}
    salida = asyncio.run(_handle_recommend_businesses(
        {"category": "Consulta de Medicina General", "city": "Popayán"},
        {"active_city": "Popayán"}, final_data,
    ))
    return salida


def test_la_recomendacion_nombra_al_elegido_y_da_la_razon(monkeypatch):
    salida = _recomendar(CANDIDATOS, monkeypatch)
    reply = salida["reply"]

    assert "Óptica Claridad Este Popayán" in reply
    assert "4.6" in reply and "3 reseñas" in reply
    # El porqué, con palabras de un cliente real.
    assert "joya escondida" in reply
    # Y una salida hacia la acción siguiente.
    assert "¿Te agendo" in reply
    assert "[BIZ:12]" in reply


def test_las_alternativas_van_detras_no_desaparecen(monkeypatch):
    reply = _recomendar(CANDIDATOS, monkeypatch)["reply"]

    assert "Fisioterapia Total Norte Popayán" in reply


def test_el_recomendado_queda_en_foco_para_el_si_que_viene_despues(monkeypatch):
    final_data = {}
    salida = _recomendar(CANDIDATOS, monkeypatch, final_data)
    fd = salida["final_data"]

    assert fd["selected_business_id"] == 12
    estado = ConversationState.load(fd)
    assert estado.booking["business_id"] == 12
    # Y las fichas de los candidatos acompañan a ESTA respuesta.
    assert fd["properties"] == [{"businesses": CANDIDATOS}]


def test_sin_resenas_no_se_inventa_un_mejor(monkeypatch):
    """Decir 'el mejor' sin nada que lo respalde es opinar, no informar."""
    sin_nota = [dict(CANDIDATOS[2])]
    reply = _recomendar(sin_nota, monkeypatch)["reply"]

    assert "reseñas" in reply
    assert "no puedo decirte cuál es el mejor" in reply
    assert "Consultorio Médico Vida Sana Popayán" in reply


def test_si_nadie_lo_presta_se_dice_y_se_ofrece_salida(monkeypatch):
    async def fake_rank(term, city=None, **kwargs):
        return {"success": True, "city": "Popayán", "category": term, "businesses": []}

    monkeypatch.setattr("tools.nexiservice.rank_businesses_for", fake_rank)
    salida = asyncio.run(_handle_recommend_businesses(
        {"category": "criogenia", "city": "Popayán"}, {}, {},
    ))

    assert "No encontré" in salida["reply"]
    assert "?" in salida["reply"]


def test_una_recomendacion_sin_tema_pregunta_por_el(monkeypatch):
    salida = asyncio.run(_handle_recommend_businesses({"category": ""}, {}, {}))

    assert "¿Sobre qué" in salida["reply"]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Y LLEGA HASTA EL USUARIO
# ═══════════════════════════════════════════════════════════════════════════════

def test_el_interceptor_atiende_el_intent(monkeypatch):
    """
    La regresión concreta: el intent existía, el enrutado existía, y no había
    handler. Sin esta prueba vuelve a caerse al mensaje de último recurso.
    """
    async def fake_rank(term, city=None, **kwargs):
        return {"success": True, "city": "Popayán", "category": term,
                "businesses": CANDIDATOS}

    monkeypatch.setattr("tools.nexiservice.rank_businesses_for", fake_rank)

    salida = asyncio.run(nexiservice.pre_llm_interceptor(
        "nexiservice", "recommend_businesses",
        {"category": "Consulta de Medicina General", "city": "Popayán"},
        {
            "messages": [], "final_data": {}, "user_data": {"external_user_id": "55"},
            "project_config": {}, "conversation_id": "c1",
            "user_text": "recomiendame uno para medicina general, el mejor",
            "active_city": "Popayán",
        },
    ))

    assert salida is not None, "el interceptor tiene que responder, no dejarlo pasar"
    assert "Óptica Claridad Este Popayán" in salida["reply"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LA PETICIÓN DE CRITERIO GANA AL CONTEXTO QUE HUBIERA ABIERTO
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mensaje", ["cual es el mejor", "el mejor", "recomiendame uno"])
def test_un_superlativo_pelado_recomienda_sobre_lo_ya_mostrado(mensaje):
    """
    "¿Cuál es el mejor?" justo después de una lista de seis médicos respondía
    con el catálogo de capacidades del asistente: el análisis lo leía como una
    pregunta sobre el propio Nexo porque, solo, no nombra nada.
    """
    estado = ConversationState()
    estado.active_domain = "medico"
    estado.remember_list(ConceptKind.BUSINESS, [
        {"id": 12, "name": "Óptica Claridad Este Popayán", "category": "medico"},
    ])

    u = understand(mensaje, state=estado, mentioned_city="Popayán", catalog=CATALOGO)

    assert u.intent == "recommend_businesses"
    assert u.args["category"] == "medico"


def test_sin_nada_mostrado_el_superlativo_no_se_inventa_un_rubro():
    u = understand("cual es el mejor", state=ConversationState(),
                   mentioned_city="Popayán", catalog=CATALOGO)

    assert u.intent != "recommend_businesses"


def test_pedir_recomendacion_no_se_convierte_en_reserva(monkeypatch):
    """
    Con un negocio en foco y una reserva a medias, "recomiéndame el mejor para
    corte de cabello" acababa agendando ese servicio en el negocio anterior: el
    usuario pedía una opinión y recibía una cita.
    """
    estado = ConversationState()
    estado.set_focus(ConceptKind.BUSINESS, 5, "Fogón Criollo Oeste Popayán")
    estado.booking = {"business_id": 5, "business_name": "Fogón Criollo Oeste Popayán"}

    u = understand("recomiendame el mejor para restaurante", state=estado,
                   mentioned_city="Popayán", catalog=CATALOGO)

    assert u.intent == "recommend_businesses"
    assert "service_name" not in u.args


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LO QUE SE PREGUNTÓ NO SE VUELVE A PREGUNTAR
# ═══════════════════════════════════════════════════════════════════════════════

def test_el_servicio_recomendado_viaja_con_la_reserva(monkeypatch):
    """
    Quien pide "el mejor para medicina general" YA dijo qué quiere. El flujo
    llegaba hasta el final —negocio, hora, día— y remataba con "¿qué servicio
    deseas agendar?" enseñando nueve opciones, incluida la que el usuario había
    dado en su primer mensaje.
    """
    final_data = {}
    salida = _recomendar(CANDIDATOS, monkeypatch, final_data)
    fd = salida["final_data"]

    estado = ConversationState.load(fd)
    assert estado.booking["service_name"] == "Consulta Médica General"
    assert fd["_pending_service"] == "Consulta Médica General"
    # Y se dice en voz alta, para que el usuario pueda corregirlo si no era eso.
    assert "Consulta Médica General" in salida["reply"]


def test_recomendar_un_rubro_no_inventa_un_servicio(monkeypatch):
    """"El mejor restaurante" no nombra ningún servicio: no hay nada que anotar."""
    por_rubro = [{
        "id": 5, "name": "Fogón Criollo Oeste Popayán", "rating": 4.8, "reviews": 3,
        "matched_service": None, "match_score": 1, "comment": None,
    }]
    final_data = {}
    salida = _recomendar(por_rubro, monkeypatch, final_data)

    estado = ConversationState.load(salida["final_data"])
    assert "service_name" not in estado.booking
    assert "¿Te agendo en **Fogón Criollo Oeste Popayán**?" in salida["reply"]
