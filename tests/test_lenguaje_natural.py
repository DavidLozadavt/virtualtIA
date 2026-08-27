"""
tests/test_lenguaje_natural.py — Lyra busca con la palabra de la plataforma y
contesta con la del usuario.

NexiService guarda los hospitales bajo la categoría "medico". Buscar por ahí es
correcto; contestar por ahí no lo es. Quien escribe «necesito un hospital» y oye
«encontré 6 opciones de médico» entiende que no se le entendió, aunque los seis
resultados sean exactamente los que quería.

Aquí se fija esa separación, y se fija también que la respuesta y la pantalla
digan lo mismo: si Lyra anuncia el mapa, el turno tiene que salir con la orden
de pantalla que el frontend escucha.

    python -m pytest tests/test_lenguaje_natural.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.wording import (
    count_phrase,
    pluralize_es,
    singularize_es,
    user_facing_label,
)
from core.semantic.types import ConceptKind, ConversationState
from orchestrator.interceptors.nexiservice import _handle_search_businesses


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MORFOLOGÍA: CONTAR COSAS EN ESPAÑOL
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("singular,plural", [
    ("hospital", "hospitales"),
    ("veterinaria", "veterinarias"),
    ("restaurante", "restaurantes"),
    ("taller", "talleres"),
    ("bar", "bares"),
    ("centro médico", "centros médicos"),
])
def test_el_plural_es_el_que_diria_una_persona(singular, plural):
    assert pluralize_es(singular) == plural
    assert singularize_es(plural) == singular


@pytest.mark.parametrize("cantidad,palabra,esperado", [
    (6, "hospital", "6 hospitales"),
    (1, "hospital", "un hospital"),
    (1, "veterinaria", "una veterinaria"),
    (4, "veterinaria", "4 veterinarias"),
])
def test_el_numero_concuerda_con_lo_que_cuenta(cantidad, palabra, esperado):
    assert count_phrase(cantidad, palabra) == esperado


# ═══════════════════════════════════════════════════════════════════════════════
# 2. QUÉ PALABRA USAR PARA CONTESTAR
# ═══════════════════════════════════════════════════════════════════════════════

def test_manda_la_palabra_del_usuario_sobre_la_del_catalogo():
    assert user_facing_label(["hospital"], ["medico"], "medico") == "hospital"


def test_sin_palabra_propia_se_usa_la_del_catalogo():
    """«¿Qué hay por aquí?» no nombra nada: ahí la etiqueta sí aporta."""
    assert user_facing_label([], ["medico"], "medico") == "médico"


def test_una_palabra_vacia_no_cuenta_como_nombre():
    """«Muéstrame opciones» no nombra un rubro; nombra que no lo nombra."""
    assert user_facing_label(["opciones"], ["medico"], "medico") == "médico"


def test_un_verbo_no_nombra_un_rubro():
    """«Un lugar para comer» pide restaurantes, no «6 comeres»."""
    assert user_facing_label(["comer"], ["restaurante"], "restaurante") == "restaurante"


def test_la_forma_escrita_lleva_sus_tildes():
    """La base guarda «medico» sin tilde porque es como se busca."""
    assert user_facing_label([], ["barberia"], "barberia") == "barbería"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LA RESPUESTA COMPLETA: LENGUAJE + PANTALLA
# ═══════════════════════════════════════════════════════════════════════════════

CIUDAD = "Popayán"

HOSPITALES = [
    {"id": i, "name": f"Centro Médico {i}", "category": "Consultorios",
     "lat": 2.44 + i / 1000, "lng": -76.60 - i / 1000, "address": f"Calle {i}"}
    for i in range(1, 7)
]


def _busqueda_falsa(monkeypatch, resultados):
    import tools.nexiservice as tools_nexi

    async def _fake(**kwargs):
        return {
            "success": True,
            "businesses": resultados,
            "city": CIUDAD,
            "count": len(resultados),
            "target_city_coords": {"lat": 2.4411, "lng": -76.6063},
        }

    monkeypatch.setattr(tools_nexi, "search_businesses", _fake)
    monkeypatch.setattr(tools_nexi, "find_businesses_offering", _fake)


@pytest.mark.asyncio
async def test_la_respuesta_conserva_la_palabra_del_usuario(monkeypatch):
    """«Necesito un hospital» → «6 hospitales», nunca «6 opciones de médico»."""
    _busqueda_falsa(monkeypatch, HOSPITALES)

    salida = await _handle_search_businesses(
        {"category": "medico", "city": CIUDAD,
         "_grounded_kind": ConceptKind.BUSINESS_CATEGORY,
         "_grounded_terms": ["medico"], "_user_terms": ["hospital"]},
        {"active_city": CIUDAD}, {}, ConversationState(),
    )

    assert "6 hospitales" in salida["reply"]
    assert "médico" not in salida["reply"]
    assert "medico" not in salida["reply"]


@pytest.mark.asyncio
async def test_la_busqueda_sale_con_el_mapa_en_el_mismo_turno(monkeypatch):
    """
    Lyra no puede decir «te los muestro en el mapa» y dejar que el usuario
    navegue a mano. La orden de pantalla viaja con la respuesta.
    """
    _busqueda_falsa(monkeypatch, HOSPITALES)

    salida = await _handle_search_businesses(
        {"category": "medico", "city": CIUDAD, "_user_terms": ["hospital"]},
        {"active_city": CIUDAD}, {}, ConversationState(),
    )
    final = salida["final_data"]

    assert final["voice_action"] == "fit_all_businesses"
    assert final["map_center"] is not None
    assert len(final["properties"][0]["businesses"]) == 6
    assert "mapa" in salida["reply"].lower()


@pytest.mark.asyncio
async def test_un_solo_resultado_se_enfoca_en_el_mapa(monkeypatch):
    """
    Un resultado se centra; varios se encuadran. Aquí vivía `map_highlight`,
    que no está entre las acciones que el frontend escucha: Lyra anunciaba el
    mapa y el mapa no se movía.
    """
    _busqueda_falsa(monkeypatch, HOSPITALES[:1])

    salida = await _handle_search_businesses(
        {"category": "medico", "city": CIUDAD, "_user_terms": ["hospital"]},
        {"active_city": CIUDAD}, {}, ConversationState(),
    )
    final = salida["final_data"]

    assert final["voice_action"] == "fly_to_business"
    assert final["voice_action_payload"]["business_id"] == 1
    assert final["voice_action_payload"]["lat"] is not None


@pytest.mark.asyncio
async def test_sin_resultados_no_se_nombra_la_categoria_interna(monkeypatch):
    """«No encontré resultados para gym» es enseñarle el nombre de la columna."""
    import tools.nexiservice as tools_nexi

    async def _vacio(**kwargs):
        return {
            "success": True, "businesses": [], "city": CIUDAD, "count": 0,
            "message": "No encontré resultados para **gym** en **Popayán**.",
            "target_city_coords": {"lat": 2.4411, "lng": -76.6063},
        }

    monkeypatch.setattr(tools_nexi, "search_businesses", _vacio)
    monkeypatch.setattr(tools_nexi, "find_businesses_offering", _vacio)

    salida = await _handle_search_businesses(
        {"category": "gym", "city": CIUDAD, "_user_terms": ["gimnasio"]},
        {"active_city": CIUDAD}, {}, ConversationState(),
    )

    assert "gimnasios" in salida["reply"]
    assert "gym" not in salida["reply"]
    # El mapa se mueve igual: un "no hay nada" sin ciudad delante no dice dónde.
    assert salida["final_data"]["map_center"] is not None
