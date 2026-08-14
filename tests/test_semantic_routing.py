"""
tests/test_semantic_routing.py — El router y las herramientas, ya integrados.

Las pruebas de `test_semantic_understanding.py` miden la comprensión aislada.
Aquí se comprueba lo que de verdad le llega al usuario: que `detect_intent`
enrute como es debido con el catálogo real delante, y que la herramienta de
búsqueda se niegue a recibir una frase hablada aunque alguien se la pase.

Los casos concretos de los logs viven al final, como regresión. No son la
solución —la solución es el mecanismo—, sólo la constancia de que ese
mecanismo los cubre.

    python -m pytest tests/test_semantic_routing.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic import llm_resolver
from core.semantic.catalog import get_catalog
from orchestrator.intent_router import _extract_time, detect_intent
from tools.nexiservice import _looks_like_sentence, search_businesses


def _catalog_available() -> bool:
    try:
        return len(get_catalog()) > 0
    except Exception:
        return False


needs_catalog = pytest.mark.skipif(
    not _catalog_available(),
    reason="requiere la base de datos de NexiService",
)


@pytest.fixture(autouse=True)
def _no_live_llm():
    """Sin resolutor: se mide lo que el sistema entiende por sí mismo."""
    llm_resolver.set_completion_fn(lambda prompt: '{"categorias": [], "confianza": 0.0}')
    yield
    llm_resolver.set_completion_fn(None)


def route(message, state=None):
    return detect_intent(
        message, "nexiservice",
        current_context={"semantic_state": state} if state else None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LA HERRAMIENTA DE BÚSQUEDA NO ACEPTA FRASES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("query", [
    "que me puedes ofrecer",
    "necesito que te mueras",
    "quiero saber que negocios tienen disponibles hoy",
    "hola como estas quiero algo",
])
def test_la_herramienta_rechaza_frases_habladas(query):
    assert _looks_like_sentence(query), f"{query!r} debería reconocerse como frase"


@pytest.mark.parametrize("query", [
    "barberia", "fogon criollo", "Consulta Médica General",
    "Restaurantes y Gastronomía", "optica claridad",
])
def test_la_herramienta_acepta_nombres_y_categorias(query):
    assert not _looks_like_sentence(query), f"{query!r} nombra algo, no es una frase"


def test_busqueda_con_frase_no_llega_a_la_base():
    """Aunque la invoque el LLM, una frase conversacional no se convierte en SQL."""
    out = asyncio.run(search_businesses(category="que me puedes ofrecer"))
    assert out["success"] is False
    assert out.get("unresolved_query") is True
    # Y el mensaje no le devuelve al usuario su propia frase como si fuera un rubro.
    assert "que me puedes ofrecer" not in out["message"]


def test_termino_anclado_si_pasa_la_guarda():
    """Lo que viene del catálogo entra aunque tenga varias palabras."""
    out = asyncio.run(search_businesses(category="Consulta Médica General", grounded=True))
    assert out.get("unresolved_query") is not True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EXTRACCIÓN DE HORA
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,expected", [
    ("a las 3 pm", "15:00"),
    ("a las 3 de la tarde", "15:00"),
    ("a las 10:30", "10:30"),
    ("a las 10:30 pm", "22:30"),
    ("nos vemos a las 8 am", "08:00"),
    ("quiero a las 9 de la noche", "21:00"),
    ("12 am", "00:00"),
    ("manana en la tarde", None),     # sin hora concreta
    ("quiero una cita", None),
])
def test_extraccion_de_hora(text, expected):
    assert _extract_time(text) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RUTEO: CONVERSACIÓN FRENTE A ACCIÓN
# ═══════════════════════════════════════════════════════════════════════════════

CONVERSATIONAL_INTENTS = {"greeting", "farewell", "conversation", "capabilities", "identity"}


@pytest.mark.parametrize("message", [
    "hola", "buenas tardes", "gracias", "jajaja", "ok", "estoy mirando",
    "solo estoy viendo", "qué puedes hacer", "qué me puedes ofrecer",
    "ayúdame", "cómo funciona", "chao",
])
@needs_catalog
def test_la_conversacion_no_dispara_herramientas(message):
    result = route(message)
    assert result["intent"] in CONVERSATIONAL_INTENTS, (
        f"{message!r} → {result['intent']}"
    )


@pytest.mark.parametrize("message", [
    "necesito un médico", "quiero cortarme el cabello", "qué negocios tienen",
    "muéstrame opciones", "qué hay por aquí",
])
@needs_catalog
def test_las_necesidades_si_disparan_busqueda(message):
    result = route(message)
    assert result["intent"] == "search_businesses", f"{message!r} → {result['intent']}"


@pytest.mark.parametrize("message", [
    "quiero una cita", "necesito agendar", "quiero separar una hora",
    "quiero sacar turno", "me gustaría reservar",
])
@needs_catalog
def test_las_reservas_se_enrutan_a_agendamiento(message):
    result = route(message)
    assert result["intent"] == "request_appointment", f"{message!r} → {result['intent']}"


@needs_catalog
def test_ninguna_consulta_arrastra_la_frase_del_usuario():
    """
    La comprobación de fondo: nada de lo que el usuario escribe llega crudo a
    una consulta. Lo que se busca sale siempre del catálogo.
    """
    frases = [
        "que me puedes ofrecer", "necesito que te mueras", "alguna medicina",
        "hola", "qué hay por aquí", "quiero algo cerca", "ayúdame",
        "estoy mirando", "hospital o algo?", "cómo funciona esto",
    ]
    for frase in frases:
        args = route(frase).get("args") or {}
        for campo in ("category", "business_name"):
            valor = args.get(campo)
            if not valor:
                continue
            assert not _looks_like_sentence(valor), (
                f"{frase!r} produjo {campo}={valor!r}, que es una frase"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LOS COMANDOS DE INTERFAZ SIGUEN SIENDO LITERALES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message,expected", [
    ("acercar", "zoom_in"),
    ("hacer zoom", "zoom_in"),
    ("alejar", "zoom_out"),
    ("menos zoom", "zoom_out"),
    ("ver mapa", "show_map"),
    ("mostrar todos", "fit_all_businesses"),
    ("donde estoy", "locate_me"),
])
@needs_catalog
def test_los_comandos_de_interfaz_no_pasan_por_la_semantica(message, expected):
    """Órdenes a la pantalla: deterministas, sin coste y sin interpretación."""
    assert route(message)["intent"] == expected


# ═══════════════════════════════════════════════════════════════════════════════
# 5. REGRESIÓN: LOS CASOS OBSERVADOS EN LOS LOGS
# ═══════════════════════════════════════════════════════════════════════════════

@needs_catalog
def test_regresion_saludo_sigue_siendo_conversacion():
    assert route("Hola")["intent"] == "greeting"


@needs_catalog
def test_regresion_agresion_no_es_un_negocio():
    result = route("Necesito que te mueras")
    assert result["intent"] not in ("navigate_to_company", "search_businesses")


@needs_catalog
def test_regresion_pregunta_por_capacidades_no_es_un_negocio():
    result = route("Que me puedes ofrecer")
    assert result["intent"] == "capabilities"


@needs_catalog
def test_regresion_alguna_medicina_se_interpreta_por_significado():
    result = route("Alguna medicina")
    assert result["intent"] == "search_businesses"
    # No busca la frase: busca el concepto del catálogo con el que se corresponde.
    assert (result["args"].get("category") or "").lower() != "alguna medicina"


@needs_catalog
def test_regresion_hospital_o_algo_sigue_descubriendo_salud():
    result = route("Hospital o algo?")
    assert result["intent"] == "search_businesses"
    assert result["args"].get("category")
