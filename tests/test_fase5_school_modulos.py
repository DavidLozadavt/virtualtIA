"""
tests/test_fase5_school_modulos.py — Nexo y los módulos reales de School.

Permisos (qué está autorizado), integraciones (qué sabe ejecutar Nexo) e
intención (qué pide ahora) resueltos juntos sobre el inventario generado del
código de School (`projects/school_inventario.json`).

    python -m pytest tests/test_fase5_school_modulos.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orchestrator.interceptors.schoolsena_modules as sm  # noqa: E402
from orchestrator.interceptors.schoolsena_nlu import Reading  # noqa: E402

MODS = sm.modules()


def test_el_inventario_sale_del_codigo():
    rutas = {m["ruta"] for m in MODS}
    assert {"/regionales", "/gestion-eventos", "/formularios", "/carpetas-viajeras",
            "/pagos/configuracion-pagos", "/informeInstructor"} <= rutas
    # Las pantallas protegidas con una constante del front quedan resueltas.
    assert not any(p.startswith("...") for m in MODS for p in m["permisos"])


@pytest.mark.parametrize("frase,ruta", [
    ("muéstrame la gestión de informes de instructores", "/informeInstructor"),
    ("¿qué regionales están registradas?", "/regionales"),
    ("carpetas viajeras", "/carpetas-viajeras"),
    ("configuración de pagos", "/pagos/configuracion-pagos"),
    ("quiero crear un nuevo evento", "/gestion-eventos"),
    ("dime las sedes", "/sedesSena"),
])
def test_el_modulo_que_mas_explica_el_mensaje(frase, ruta):
    assert sm.match_module(frase, MODS)["ruta"] == ruta


@pytest.mark.parametrize("frase", ["cuanto cuesta el servicio", "que hora es en general", "busco trabajo"])
def test_una_palabra_comun_no_es_un_modulo(frase):
    m = sm.match_module(frase, MODS)
    assert m is None or not sm.strong(m, frase)


@pytest.mark.parametrize("frase,acto", [
    ("¿qué permisos tengo?", "permisos"), ("¿tengo permiso para crear eventos?", "permisos_puedo"),
    ("¿por qué no puedo entrar a nómina?", "permisos_por_que"), ("muéstrame los permisos que no tengo", "permisos_faltan"),
    ("quiero crear un evento", "crear"), ("quiero modificar una regional", "editar"),
    ("llévame a regionales", "navegar"), ("¿cuántas hay?", "contar"), ("léemelos", "leer"),
    ("¿qué puedo hacer en esta plataforma?", None),
])
def test_que_quiere_hacer(frase, acto):
    assert sm.read_act(frase) == acto


def test_alternativas_se_preguntan():
    tipo, a, b = sm.split_compound("Llévame a formularios o cuáles tengo activos")
    assert tipo == "alternativas" and "formularios" in a and "activos" in b


CAPS_ADMIN = {"consultas": ["regionales", "eventos", "crear_evento", "formularios"], "secciones": [],
              "permisos": ["GESTION_REGIONAL", "GESTION_USUARIO", "GESTION_ROL_PERMISOS"], "catalogo": None}
CAPS_APRENDIZ = {"consultas": ["clases", "notas", "periodos_propios"], "secciones": [],
                 "permisos": ["AULA_VIRTUAL_APRENDIZ_CLASES"], "catalogo": None}


def _go(texto, caps, state=None, reading=None, monkey=None):
    return asyncio.run(sm.handle(texto, reading or Reading(kind="none"), state or {}, caps, {"final_data": {}}))


def _fake_rows(monkeypatch, filas):
    async def fake(query, context, params=None):
        return filas, None, None
    monkeypatch.setattr(sm, "run_query", fake)


def test_sin_permiso_se_dice_sin_datos():
    out = _go("dame las regionales", CAPS_APRENDIZ)
    assert out["final_data"]["school_outcome"] == "denied" and "GESTION_REGIONAL" in out["reply"]


def test_consulta_contexto_y_ordinal(monkeypatch):
    _fake_rows(monkeypatch, [{"id": 1, "nombre": "Cauca"}, {"id": 2, "nombre": "Valle"}])
    out = _go("¿qué regionales hay?", CAPS_ADMIN)
    assert "**2 regionales:**" in out["reply"]
    st = out["final_data"]["_school"]
    out = _go("¿cuántas hay?", CAPS_ADMIN, st)
    assert out["reply"] == "Hay 2 regionales."
    out = _go("¿cuál es la segunda?", CAPS_ADMIN, out["final_data"]["_school"])
    assert "Valle" in out["reply"]
    out = _go("llévame allá", CAPS_ADMIN, out["final_data"]["_school"])
    assert out["final_data"]["voice_action_payload"]["url"] == "/regionales"


def test_sin_estado_no_se_inventa_el_filtro(monkeypatch):
    _fake_rows(monkeypatch, [{"id": 1, "nombre": "Cauca"}])
    out = _go("¿cuáles regionales están activas?", CAPS_ADMIN)
    assert "no tienen un estado registrado" in out["reply"]


def test_modulo_sin_integracion_lo_reconoce():
    caps = {**CAPS_ADMIN, "permisos": CAPS_ADMIN["permisos"] + ["GESTION_MEDIO_PAGO"]}
    out = _go("muéstrame los medios de pago", caps)
    assert out["final_data"]["school_outcome"] == "not_integrated"


def test_escritura_no_integrada_no_se_ejecuta():
    out = _go("quiero eliminar una regional", CAPS_ADMIN)
    assert out["final_data"]["school_outcome"] == "not_integrated" and "Por seguridad" in out["reply"]


def test_crear_evento_pide_lo_que_falta_y_confirma():
    out = _go("quiero crear un evento", CAPS_ADMIN)
    assert out["reply"] == "¿Cómo se va a llamar el evento?"
    st = out["final_data"]["_school"]
    out = _go("se llama Feria de Ciencia", CAPS_ADMIN, st)
    assert "¿Qué día" in out["reply"]
    out = _go("el 20 de diciembre de 3 a 5 pm", CAPS_ADMIN, out["final_data"]["_school"])
    assert "Feria de Ciencia" in out["reply"] and "15:00" in out["reply"] and "¿Lo creo?" in out["reply"]
    out = _go("listo", CAPS_ADMIN, out["final_data"]["_school"])
    assert "sí" in out["reply"] and "no" in out["reply"]


def test_una_pregunta_no_es_el_nombre_del_evento(monkeypatch):
    _fake_rows(monkeypatch, [{"id": 7, "nombre": "Inscripción", "estado": "publicado"}])
    st = _go("quiero crear un evento", CAPS_ADMIN)["final_data"]["_school"]
    out = _go("cuáles formularios tengo activos", CAPS_ADMIN, st)
    # Se atiende la pregunta y la creación queda guardada como borrador (sin tomar la pregunta por nombre).
    st = out["final_data"]["_school"]
    assert "formulario" in out["reply"] and "op" not in st
    assert st["borradores"]["eventos"]["campos"].get("nombre") is None


def test_permisos_explicados_por_modulo():
    out = _go("¿tengo permiso para crear eventos?", CAPS_ADMIN)
    assert out["reply"].startswith("Sí") and "aquí mismo" in out["reply"]
    out = _go("¿tengo permiso para crear eventos?", CAPS_APRENDIZ)
    assert out["reply"].startswith("No")


def test_los_permisos_que_faltan_solo_con_catalogo():
    out = _go("muéstrame los permisos que no tengo", CAPS_APRENDIZ)
    assert "No puedo mostrarte" in out["reply"]
    caps = {**CAPS_ADMIN, "catalogo": [{"permiso": "GESTION_NOMINA", "nombre": "Nómina", "ruta": "/nomina", "grupo": "Laboral"}]}
    out = _go("muéstrame los permisos que no tengo", caps)
    assert "Nómina" in out["reply"]


def test_lo_propio_del_nlu_no_se_lo_quita_el_modulo():
    r = Reading(kind="capability", capability="schedule", timeframe="week")
    assert _go("horario instructor", CAPS_ADMIN, reading=r) is None
