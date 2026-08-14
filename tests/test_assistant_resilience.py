"""
tests/test_assistant_resilience.py — Qué ve el usuario cuando algo falla.

Un asistente puede quedarse sin modelo, sin crédito o sin red. Lo que no puede
es contárselo al usuario en su propio idioma técnico. Estas pruebas fijan dos
cosas observadas en producción:

  * "Error de conexión." apareció como si fuera lo que dijo el asistente, y se
    envió al sintetizador de voz para leerlo en voz alta.
  * Bajo ese mensaje de error seguían colgando las fichas de negocios de una
    búsqueda anterior.

Y fijan la decisión de fondo: Lyra funciona SIN modelo externo. Por defecto no
sale a la red para nada —conversar, buscar, agendar—, así que no hay saldo que
se agote ni servicio de terceros del que dependa. El modelo es una ayuda que se
enciende con `LLM_EXTERNAL_ENABLED`, y las pruebas que miden ese camino lo dicen
explícitamente pidiendo la fixture `with_external_llm`.

    python -m pytest tests/test_assistant_resilience.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_engine import LLMUnavailable
from orchestrator.tool_runner import run_agent_loop


class FakeEngine:
    """Motor de prueba: responde, falla, o cuenta cuántas veces lo llamaron."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply

    def generate_with_tools(self, messages, tools_schema, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return {"type": "text", "content": self.reply}


@pytest.fixture
def with_external_llm(monkeypatch):
    """
    Enciende el modelo externo para las pruebas que miden ESE camino.

    Por defecto está apagado: Lyra responde con lo suyo y no sale a la red.
    """
    from core.config import settings
    monkeypatch.setattr(settings, "LLM_EXTERNAL_ENABLED", True)
    return settings


def run(engine, message, final_data=None, project="nexiservice"):
    messages = [
        {"role": "system", "content": "Eres Nexo."},
        {"role": "user", "content": message},
    ]
    return asyncio.run(run_agent_loop(
        engine=engine,
        messages=messages,
        project_id=project,
        project_config={"slug": project, "assistant_name": "Nexo"},
        user_data={"external_user_id": "anon_test"},
        conversation_id="test",
        final_data=final_data if final_data is not None else {},
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LOS ERRORES NO SE LE CUENTAN AL USUARIO
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("failure", [
    LLMUnavailable("HTTP 402: requires more credits", status_code=402),
    LLMUnavailable("HTTP 500: upstream error", status_code=500),
    LLMUnavailable("Connection refused"),
])
def test_el_fallo_del_modelo_no_se_muestra_literal(failure, with_external_llm):
    out = run(FakeEngine(error=failure), "cómo funciona esto")
    reply = out["reply"] or ""
    for filtracion in ("Error de conexión", "HTTP", "402", "500", "credits", "refused"):
        assert filtracion not in reply, f"se filtró {filtracion!r} en: {reply!r}"
    assert reply.strip(), "el usuario debe recibir algo, no una cadena vacía"


def test_el_fallo_queda_marcado_para_el_resto_del_sistema(with_external_llm):
    out = run(FakeEngine(error=LLMUnavailable("boom", status_code=402)), "hola")
    assert out["final_data"].get("llm_error") is True


def test_un_error_no_arrastra_resultados_anteriores(with_external_llm):
    """Las fichas de una búsqueda previa no acompañan a un mensaje de fallo."""
    previo = {
        "_last_businesses": [{"id": 1, "name": "Consultorio Médico Vida Sana"}],
        "voice_action": "fit_all_businesses",
    }
    out = run(FakeEngine(error=LLMUnavailable("sin crédito", status_code=402)),
              "hola", final_data=previo)
    fd = out["final_data"]
    assert fd["properties"] == []
    assert fd["voice_action"] is None


def test_el_motor_distingue_falta_de_saldo_de_fallo_tecnico():
    assert LLMUnavailable("x", status_code=402).is_quota is True
    assert LLMUnavailable("x", status_code=429).is_quota is True
    assert LLMUnavailable("x", status_code=500).is_quota is False
    assert LLMUnavailable("x").is_quota is False


# ═══════════════════════════════════════════════════════════════════════════════
# 1b. MODO LOCAL: NINGUNA RESPUESTA DEPENDE DE UN SERVICIO EXTERNO
# ═══════════════════════════════════════════════════════════════════════════════

class ExplodingEngine:
    """Motor que falla si alguien lo toca. Detecta llamadas de red indebidas."""

    def generate(self, *args, **kwargs):
        raise AssertionError("no debía llamarse a ningún modelo externo")

    generate_with_tools = generate


@pytest.mark.parametrize("message", [
    "hola", "hola como estas", "gracias", "qué me puedes ofrecer",
    "cómo funciona esto", "necesito un médico", "quiero una cita",
    "ver mapa", "no entiendo", "chao",
])
def test_nada_sale_a_la_red_en_modo_local(message):
    """
    La comprobación de fondo del modo local: con el modelo apagado —que es el
    valor por defecto—, ningún mensaje provoca una llamada externa, y todos
    reciben respuesta igualmente.
    """
    out = run(ExplodingEngine(), message)
    assert (out["reply"] or "").strip(), f"{message!r} se quedó sin respuesta"


def test_el_modo_local_responde_saludos_con_contexto():
    """
    Sin modelo, la respuesta no puede ser una plantilla ciega: si ya se habían
    mostrado opciones, el saludo retoma el hilo en vez de empezar de cero.
    """
    from core.semantic.types import ConceptKind, ConversationState

    state = ConversationState()
    state.remember_list(ConceptKind.BUSINESS, [
        {"id": 1, "name": "Consultorio Médico Vida Sana Popayán"},
        {"id": 2, "name": "Sanar Plus Sur Popayán"},
    ])
    final_data = {}
    state.save(final_data)

    out = run(ExplodingEngine(), "hola", final_data=final_data)
    assert "2 opciones" in out["reply"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LA CONVERSACIÓN LA ESCRIBE EL MODELO (sólo si se enciende)
# ═══════════════════════════════════════════════════════════════════════════════

def test_un_saludo_llega_al_modelo(with_external_llm):
    """
    Antes se devolvía una plantilla y el turno terminaba ahí. Un asistente de
    verdad responde al saludo con algo escrito para esa conversación.
    """
    engine = FakeEngine(reply="¡Hola! ¿En qué te puedo ayudar hoy?")
    out = run(engine, "hola como estas")
    assert engine.calls == 1, "el saludo debía llegar al modelo"
    assert out["reply"] == "¡Hola! ¿En qué te puedo ayudar hoy?"


def test_una_pregunta_general_llega_al_modelo(with_external_llm):
    engine = FakeEngine(reply="NexiService es un directorio de negocios…")
    out = run(engine, "cómo funciona esto")
    assert engine.calls == 1
    assert out["reply"].startswith("NexiService")


def test_sin_modelo_el_saludo_sigue_siendo_un_saludo():
    """
    Si el modelo no está disponible, el usuario recibe la respuesta preparada,
    no una disculpa técnica: sigue siendo un saludo correcto.
    """
    engine = FakeEngine(error=LLMUnavailable("sin crédito", status_code=402))
    out = run(engine, "hola")
    reply = out["reply"] or ""
    assert reply.strip()
    assert "problema" not in reply.lower() and "error" not in reply.lower()


def test_la_respuesta_preparada_se_descarta_cuando_el_modelo_responde(with_external_llm):
    """No debe quedar guardada entre turnos una respuesta que no se usó."""
    engine = FakeEngine(reply="Respuesta real del modelo.")
    out = run(engine, "hola")
    assert "_fallback_reply" not in out["final_data"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LAS BÚSQUEDAS SIGUEN SIN GASTAR MODELO
# ═══════════════════════════════════════════════════════════════════════════════

def test_una_busqueda_no_necesita_al_modelo():
    """
    Lo que el sistema resuelve por sí mismo se sigue resolviendo sin llamar al
    modelo: la conversación pasa a costar tokens, la búsqueda no.
    """
    engine = FakeEngine(reply="no debería llamarse")
    out = run(engine, "necesito un médico")
    assert engine.calls == 0, "una búsqueda no debe consumir el modelo"
    assert out["reply"]


@pytest.mark.parametrize("message,expected_action", [
    ("ver mapa", "show_map"),
    ("acercar", "zoom_in"),
    ("alejar", "zoom_out"),
    ("mostrar todos", "fit_all_businesses"),
    ("dónde estoy", "locate_me"),
])
def test_las_ordenes_de_pantalla_funcionan_sin_modelo(message, expected_action):
    """
    Abrir el mapa o hacer zoom son órdenes a la interfaz. Dependían de que el
    modelo devolviera la etiqueta correcta, así que con el modelo caído el
    usuario recibía una disculpa en vez de ver el mapa.
    """
    engine = FakeEngine(error=LLMUnavailable("sin crédito", status_code=402))
    out = run(engine, message)
    assert engine.calls == 0, "una orden de pantalla no debe consumir el modelo"
    assert out.get("voice_action") == expected_action
    assert "problema" not in (out["reply"] or "").lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EL CONTEXTO SE GUARDA AUNQUE TRAIGA TIPOS DE LA BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════════

def test_el_contexto_con_precios_se_puede_persistir():
    """
    Los precios llegan de MySQL como `Decimal`, que no es JSON. Guardarlos sin
    convertir tumbaba la petición entera con un 500.
    """
    import json as _json
    from decimal import Decimal

    from core.semantic.types import ConceptKind, ConversationState

    state = ConversationState()
    state.remember_list(ConceptKind.SERVICE, [
        {"id": 1, "nombre": "Consulta Médica General", "valor": Decimal("120000.00")},
    ])
    final_data = {}
    state.save(final_data)

    encoded = _json.dumps(final_data)          # no debe lanzar
    assert "120000" in encoded

    restored = ConversationState.load(_json.loads(encoded))
    assert restored.presented[0].label == "Consulta Médica General"
