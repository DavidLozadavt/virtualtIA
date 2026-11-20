"""
tests/test_fase4_contexto_school.py — Refinar lo mostrado y capacidades de School.

1. "De esos cuatro, ¿cuál tiene pescado?" opera sobre lo que el usuario vio;
   no es una búsqueda de "cuatros pescado".
2. School decide por PERMISOS efectivos (backend), no por el nombre del rol, y
   distingue consultar, contar, leer y navegar.

    python -m pytest tests/test_fase4_contexto_school.py -q
"""

import asyncio
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic.refinement import CATALOG_REQUIREMENT, read_set_request

# ── 1. alcance, operación y requisito ───────────────────────────────────────


@pytest.mark.parametrize("frase,op,reqs,count", [
    ("De esos cuatros quiero que me recomiendes uno que ofrezca pescado en su menú, por favor", "filter", ["pescado"], 4),
    ("De esos cuatro, recomiéndame uno", "recommend", [], 4),
    ("De los anteriores, ¿cuál tiene pescado?", "filter", ["pescado"], None),
    ("Entre esos, quiero el más barato", "cheapest", [], None),
    ("De los que me mostraste, ¿cuál queda más cerca?", "nearest", [], None),
    ("De esos restaurantes, dime cuáles abren hoy", "open_now", [], None),
    ("Mejor uno que tenga comida de mar", "filter", ["comida de mar"], None),
    ("De los anteriores, descarta los que no tengan menú", "exclude", [CATALOG_REQUIREMENT], None),
    ("cuál de ellos tiene pescado y carne", "filter", ["pescado", "carne"], None),
    ("de los 4 cuál es el más económico", "cheapest", [], 4),
])
def test_alcance_operacion_y_requisito(frase, op, reqs, count):
    p = read_set_request(frase)
    assert p and p.operation == op and p.requirements == reqs and p.count == count


def test_el_ordinal_con_requisito_elige_dentro_de_lo_mostrado():
    p = read_set_request("El segundo, pero que tenga servicio domicilio", ordinal=2)
    assert (p.operation, p.ordinal, p.requirements) == ("pick", 2, ["domicilio"])


@pytest.mark.parametrize("frase", ["busca restaurantes con pescado", "restaurantes que tengan pescado y carne",
                                   "quiero ver esos", "hola"])
def test_una_busqueda_nueva_no_es_un_refinamiento(frase):
    assert read_set_request(frase) is None


def test_sin_lista_mostrada_no_hay_refinamiento():
    assert read_set_request("de esos, el más barato", has_presented=False) is None


def test_un_numeral_no_es_contenido_que_buscar():
    from core.semantic.speech_act import analyze
    assert analyze("a las 11").content_terms == []
    assert analyze("mejor a las 13").content_terms == []


# ── el manejador sólo afirma lo que consta ──────────────────────────────────

from core.semantic.types import ConceptKind, ConversationState  # noqa: E402
from orchestrator.interceptors import nexiservice as icpt  # noqa: E402

CUATRO = [{"id": i, "name": n, "category": "Restaurantes y Gastronomía", "distance_km": d}
          for i, n, d in ((2, "Fogón Norte", 2.9), (5, "Fogón Oeste", 3.6), (4, "Parrilla Este", 3.6), (3, "Sazón Sur", 4.0))]


def _evaluados(con_pescado=()):
    return {"success": True, "businesses": [
        {"id": b["id"], "name": b["name"], "category": b["category"], "lat": None, "lng": None, "address": None,
         "evidence": {"pescado": "Mojarra frita"} if b["id"] in con_pescado else {},
         "missing": [] if b["id"] in con_pescado else ["pescado"],
         "n_services": 8, "min_price": 20000.0, "avg_price": 50000.0 + b["id"] * 1000,
         "rating": {2: 3.9, 5: 4.8, 4: 3.5, 3: 4.5}[b["id"]], "reviews": 2} for b in CUATRO]}


def _refinar(monkeypatch, args, con_pescado=()):
    async def fake_rank(kind, reqs, city=None, limit=6, only_ids=None):
        assert sorted(only_ids) == [2, 3, 4, 5], "sólo se evalúa lo mostrado"
        return _evaluados(con_pescado)
    monkeypatch.setattr("tools.nexiservice.rank_with_constraints", fake_rank)
    st = ConversationState()
    st.remember_list(ConceptKind.BUSINESS, CUATRO)
    fd = {}
    st.save(fd)
    return asyncio.run(icpt._handle_refine_presented(args, {}, fd, ConversationState.load(fd)))


def test_ninguno_lo_tiene_se_dice_y_no_se_inventa(monkeypatch):
    out = _refinar(monkeypatch, {"operation": "filter", "requirements": ["pescado"]})
    assert "ninguno tiene **pescado** registrado" in out["reply"]
    assert "cuatros" not in out["reply"]


def test_el_que_lo_tiene_sale_con_la_evidencia(monkeypatch):
    out = _refinar(monkeypatch, {"operation": "filter", "requirements": ["pescado"]}, con_pescado=(3,))
    assert "**Sazón Sur**" in out["reply"] and "Mojarra frita" in out["reply"]


def test_el_mas_barato_por_su_carta(monkeypatch):
    out = _refinar(monkeypatch, {"operation": "cheapest", "requirements": []})
    assert "**Fogón Norte**" in out["reply"]


def test_horario_de_atencion_no_registrado_se_reconoce(monkeypatch):
    out = _refinar(monkeypatch, {"operation": "open_now", "requirements": []})
    assert "No tengo registrado el horario" in out["reply"]


# ── 2. School: capacidades por permiso ──────────────────────────────────────

from orchestrator.interceptors import schoolsena_caps as caps  # noqa: E402
from orchestrator.interceptors import schoolsena_nlu as nlu  # noqa: E402

BASICO = {"consultas": ["clases", "horario", "notas", "pendientes", "periodos_propios"], "secciones": [], "permisos": []}
PLUS = {**BASICO, "consultas": BASICO["consultas"] + ["centros", "sedes", "ambientes"],
        "secciones": [{"permiso": "LYRA_PRUEBA_NUEVA", "nombre": "Sección nueva de prueba", "ruta": "/x", "grupo": None}]}


def test_mismo_rol_distintos_permisos_distintas_capacidades():
    assert caps.allows(BASICO, "centers") is False
    assert caps.allows(PLUS, "centers") is True
    assert caps.allows(None, "centers") is None  # sin datos: decide el backend


def test_periodos_propios_sin_gestion():
    assert caps.query_for(BASICO, "periods") == "periodos_propios"


def test_un_permiso_nuevo_es_navegable_aunque_no_consultable():
    assert caps.section_for(PLUS, ["seccion", "nueva", "prueba"])["ruta"] == "/x"
    assert caps.allowed_tools(PLUS) == {"get_clases_hoy", "get_horario", "get_notas", "get_actividades_pendientes",
                                        "get_periodos", "get_centros_formacion", "get_sedes", "get_ambientes"}


def test_la_oferta_sale_de_los_permisos():
    frase, _ = caps.offer(PLUS)
    assert "centros de formación" in frase
    assert "nómina" not in caps.offer(BASICO)[0]


AHORA = datetime(2026, 11, 17, 10, 0)  # martes


@pytest.mark.parametrize("frase,cap,act", [
    ("Dame los ambientes", "rooms", "list"),
    ("¿Qué centros de formación hay?", "centers", "list"),
    ("cuántos centros de formación existen", "centers", "count"),
    ("léeme los centros de formación", "centers", "read"),
    ("¿Qué períodos tengo?", "periods", "list"),
    ("¿Qué planeación?", "planning", "list"),
    ("qué jornadas hay", "shifts", "list"),
    ("muéstrame las sedes", "sites", "list"),
])
def test_school_distingue_consultar_contar_y_leer(frase, cap, act):
    r = nlu.read(frase, {}, (True, False, False), now=AHORA)
    assert (r.capability, r.act) == (cap, act)


def test_lee_lo_anterior_en_voz_alta():
    st = nlu.remember({}, nlu.read("qué centros de formación hay", {}, (True, False, False)))
    r = nlu.read("Pero léemelos tú", st, (True, False, False))
    assert (r.capability, r.act, r.from_context) == ("centers", "read", True)


@pytest.mark.parametrize("frase", ["¿Cómo me fue en el examen de ayer?", "qué saqué en el parcial de ayer",
                                   "cómo me fue en la prueba"])
def test_un_examen_que_ya_paso_es_una_nota_no_un_horario(frase):
    r = nlu.read(frase, {}, (False, False, True), now=AHORA)
    assert r.capability == "grades"


def test_un_examen_futuro_es_una_actividad():
    assert nlu.read("tengo examen el viernes", {}, (False, False, True), now=AHORA).capability == "activities"


def test_ayer_es_ayer_no_el_proximo_lunes():
    r = nlu.read("qué clases tuve ayer", {}, (False, False, True), now=AHORA)
    assert r.on_date.isoformat() == "2026-11-16"
    assert nlu.day_label(r.day, now=AHORA, on_date=r.on_date) == "Lunes 16/11"


def test_permisos_dan_forma_no_autorizan():
    from orchestrator.interceptors.schoolsena import _roles_from_permissions
    assert _roles_from_permissions(["AULA_VIRTUAL_APRENDIZ_CLASES"], (True, True, True)) == (False, False, True)
    assert _roles_from_permissions([], (True, False, False)) == (True, False, False)
