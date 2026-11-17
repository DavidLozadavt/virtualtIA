"""
tests/test_fase6_continuidad.py — Nexo recuerda lo que propuso y lo que estaba haciendo.

    python -m pytest tests/test_fase6_continuidad.py -q
"""

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orchestrator.interceptors.schoolsena_modules as sm  # noqa: E402
import orchestrator.interceptors.schoolsena_ops as ops  # noqa: E402
from orchestrator.interceptors.schoolsena_nlu import Reading  # noqa: E402


@pytest.mark.parametrize("frase,tipo", [
    ("Sí", "afirma"), ("sí, por favor", "afirma"), ("claro", "afirma"), ("dale", "afirma"), ("listo", "afirma"),
    ("bueno", "afirma"), ("de una", "afirma"), ("hazlo", "afirma"),
    ("No", "niega"), ("mejor no", "niega"), ("después", "niega"), ("no gracias", "niega"),
    ("llévame", "navega"), ("llévame allá", "navega"), ("muéstrame", "muestra"),
    ("muéstrame los eventos de diciembre", None), ("Encuentro de instructores", None),
])
def test_respuesta_breve(frase, tipo):
    assert ops.respuesta_breve(frase) == tipo


@pytest.mark.parametrize("frase,firme", [("sí", True), ("dale", True), ("claro que sí", False), ("hazlo", True),
                                         ("listo", False), ("ok", False), ("bueno", False)])
def test_solo_un_si_inequivoco_autoriza_escribir(frase, firme):
    assert ops.afirma_firme(frase) is firme


def test_la_propuesta_vale_solo_en_el_turno_siguiente():
    st = ops.proponer({}, 4, "navegar", ruta="/x")
    assert ops.oferta_vigente(st, 5)
    assert ops.oferta_vigente(st, 6) is None
    st["oferta"]["ts"] = time.time() - 3600
    assert ops.oferta_vigente(st, 5) is None


CAPS = {"consultas": ["eventos", "crear_evento", "formularios", "crear_formulario"], "secciones": [],
        "permisos": ["GESTION_USUARIO", "GESTION_MEDIO_PAGO"], "catalogo": None}


def _go(texto, state=None, turno=1, reading=None):
    ctx = {"final_data": {"_turn": turno}}
    out = asyncio.run(sm.handle(texto, reading or Reading(kind="none"), state or {}, CAPS, ctx))
    if out and isinstance(out["final_data"]["_school"].get("oferta"), dict):
        out["final_data"]["_school"]["oferta"]["turn"] = turno  # lo hace el interceptor (_sellar)
    return out


def test_el_si_ejecuta_lo_ofrecido():
    out = _go("muéstrame los medios de pago", turno=1)
    assert "¿Te llevo" in out["reply"]
    out = _go("sí", out["final_data"]["_school"], turno=2)
    assert out["final_data"]["voice_action_payload"]["url"] == "/pagos/medio-pagos"


def test_el_no_descarta_lo_ofrecido():
    out = _go("muéstrame los medios de pago", turno=1)
    out = _go("mejor no", out["final_data"]["_school"], turno=2)
    assert out["final_data"]["school_outcome"] == "offer_declined"


def test_una_propuesta_vieja_no_se_acepta():
    out = _go("muéstrame los medios de pago", turno=1)
    assert _go("sí", out["final_data"]["_school"], turno=3) is None


def test_el_dato_pedido_no_es_navegacion():
    st = _go("quiero crear un evento")["final_data"]["_school"]
    out = _go("Eventos", st, turno=2)
    assert out["final_data"]["_school"]["op"]["campos"]["nombre"] == "Eventos"
    assert "voice_action" not in out["final_data"]


def test_pausar_y_retomar(monkeypatch):
    async def filas(query, context, params=None):
        return [{"id": 1, "nombre": "Feria"}], None, None
    monkeypatch.setattr(sm, "run_query", filas)
    st = _go("quiero crear un evento")["final_data"]["_school"]
    st = _go("Encuentro de instructores", st, 2)["final_data"]["_school"]
    out = _go("antes dime qué eventos tengo programados", st, 3)
    assert "Feria" in out["reply"] and "op" not in out["final_data"]["_school"]
    out = _go("continuemos con el evento", out["final_data"]["_school"], 4)
    assert "Encuentro de instructores" in out["reply"] and "¿Qué día" in out["reply"]


def test_cambiar_un_dato_en_el_resumen():
    st = _go("quiero crear un evento")["final_data"]["_school"]
    st = _go("Feria", st, 2)["final_data"]["_school"]
    out = _go("el 20 de diciembre a las 9", st, 3)
    assert "¿Lo creo?" in out["reply"] and "09:00" in out["reply"]
    out = _go("mejor a las 11", out["final_data"]["_school"], 4)
    assert "11:00" in out["reply"] and "¿Lo creo?" in out["reply"]


def test_compuesta_atiende_las_dos(monkeypatch):
    async def filas(query, context, params=None):
        return [{"id": 3, "nombre": "Encuesta", "estado": "publicado"}], None, None
    monkeypatch.setattr(sm, "run_query", filas)
    out = _go("quiero crear un nuevo formulario y también preguntarte cuáles tengo activos")
    assert "formulario activo" in out["reply"] and "¿Qué título" in out["reply"]


def test_redes_no_es_planeacion():
    m = sm.match_module("Redes", sm.modules())
    assert m["ruta"] == "/redes"
