"""
tests/test_fase7_formularios.py — Constructor conversacional de formularios y vínculo con eventos.

    python -m pytest tests/test_fase7_formularios.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orchestrator.interceptors.schoolsena_formdraft as d  # noqa: E402
import orchestrator.interceptors.schoolsena_forms as forms  # noqa: E402
import orchestrator.interceptors.schoolsena_modules as sm  # noqa: E402
from orchestrator.interceptors.schoolsena_nlu import Reading  # noqa: E402


def _aplicar(fe, msg):
    ins = d.interpretar(msg, fe)
    assert ins, msg
    return d.aplicar(fe, ins)


def test_varios_campos_en_un_mensaje_con_tipos_del_constructor():
    fe, _, _ = _aplicar(d.nuevo("R"), "Agrégale nombre completo, documento, correo y teléfono")
    assert [p["titulo"] for p in fe["preguntas"]] == ["Nombre completo", "Número de documento", "Correo electrónico", "Teléfono"]
    assert fe["preguntas"][3]["tipo"] == "texto_corto" and fe["preguntas"][3]["configuracion"] == {"soloNumeros": True}
    assert all(p["tipo"] in d.TIPOS for p in fe["preguntas"])


def test_obligatorias_menos_una_y_ordinales_sobre_la_version_vigente():
    fe, _, _ = _aplicar(d.nuevo("R"), "agrega nombre, documento, correo y teléfono")
    fe, _, _ = _aplicar(fe, "que todos sean obligatorios menos el teléfono")
    assert [p["esObligatoria"] for p in fe["preguntas"]] == [True, True, True, False]
    fe, _, _ = _aplicar(fe, "quiero que la pregunta del documento aparezca primero")
    fe, _, _ = _aplicar(fe, "la segunda mejor que sea opcional")
    assert fe["preguntas"][0]["titulo"] == "Número de documento"
    assert fe["preguntas"][1]["titulo"] == "Nombre completo" and not fe["preguntas"][1]["esObligatoria"]


def test_cambio_de_tipo_pide_opciones_y_no_se_puede_guardar_sin_ellas():
    fe, _, _ = _aplicar(d.nuevo("R"), "agrega una pregunta sobre el programa de formación")
    fe, _, espera = _aplicar(fe, "mejor que sea una lista desplegable")
    assert fe["preguntas"][0]["tipo"] == "desplegable" and espera == {"campo": "opciones", "indice": 0}
    assert d.sin_opciones(fe) == 0
    fe, _, _ = _aplicar(fe, "las opciones son ADSI, Contabilidad y Gestión")
    assert [o["texto"] for o in fe["preguntas"][0]["opciones"]] == ["ADSI", "Contabilidad", "Gestión"]
    assert d.sin_opciones(fe) is None


def test_deshacer_y_eliminar_la_ultima():
    fe, _, _ = _aplicar(d.nuevo("R"), "agrega nombre y correo")
    fe, _, _ = _aplicar(fe, "elimina la última")
    assert len(fe["preguntas"]) == 1
    fe, h, _ = _aplicar(fe, "deshaz el último cambio")
    assert len(fe["preguntas"]) == 2 and "Deshice" in h[0]


def test_insertar_despues_de_una_posicion_y_cambiar_a_numero():
    fe, _, _ = _aplicar(d.nuevo("R"), "agrega nombre, documento y correo")
    fe, _, _ = _aplicar(fe, "agrega otra después de la segunda: ¿Cómo te enteraste?")
    assert fe["preguntas"][2]["titulo"] == "¿Cómo te enteraste?"
    fe, _, _ = _aplicar(fe, "cambia la tercera a número")
    assert (fe["preguntas"][2]["configuracion"] or {}).get("soloNumeros")


def test_secciones_no_existen_en_school_y_se_dice():
    fe, h, _ = _aplicar(d.nuevo("R"), "agrega una sección de observaciones")
    assert fe["preguntas"][0]["tipo"] == "texto_largo" and "no tiene secciones" in h[0]


def test_propuesta_sin_datos_sensibles():
    tipo, objeto, aprendices = d.proposito("Necesito un formulario para inscribir aprendices a una capacitación")
    titulo, pregs = d.plantilla(tipo, objeto, aprendices)
    titulos = [p["titulo"] for p in pregs]
    assert "Número de ficha" in titulos and "Fecha de nacimiento" not in titulos and "Género" not in titulos
    assert not next(p for p in pregs if p["titulo"] == "Teléfono")["esObligatoria"]


@pytest.mark.parametrize("msg", ["quiero ver las regionales", "¿qué eventos hay?", "muéstrame los formularios",
                                 "llévame a formularios"])
def test_no_secuestra_otras_peticiones(msg):
    assert d.interpretar(msg, d.nuevo("R", preguntas=[{"tipo": "texto_corto", "titulo": "Nombre", "opciones": []}])) is None


def test_payload_conserva_ids_y_cumple_el_contrato():
    fe = d.desde_estructura({"id": 9, "titulo": "T", "estado": "borrador", "version": "v", "preguntas": [
        {"id": 5, "tipo": "desplegable", "titulo": "P", "esObligatoria": True, "orden": 1, "configuracion": None,
         "opciones": [{"id": 7, "texto": "A", "orden": 1}]}]})
    fe, _, _ = _aplicar(fe, "agrega correo")
    p = d.payload(fe)
    assert p[0]["id"] == 5 and p[0]["opciones"][0] == {"id": 7, "texto": "A", "orden": 1}
    assert "id" not in p[1] and p[1]["orden"] == 2
    assert d.sucio(fe)


# ── Conversación (herramientas simuladas) ───────────────────────────────────

CAPS = {"consultas": ["eventos", "crear_evento", "formularios", "crear_formulario", "editar_formulario",
                      "publicar_formulario", "vincular_formulario"], "secciones": [], "permisos": [], "catalogo": None}


@pytest.fixture
def backend(monkeypatch):
    import tools.schoolsena as ts
    llamadas = []
    eventos = [{"id": 727, "nombre": "nu event", "fechaInicial": "2026-11-27", "hora": "22:00:00"}]
    form = {"id": 18, "titulo": "nu event register", "estado": "borrador", "slug": "x", "version": "v1", "respuestas": 0,
            "eventos": [], "preguntas": []}
    ev = {"id": 727, "nombre": "nu event", "fechaInicial": "2026-11-27", "hora": "22:00:00", "formulario": None,
          "inscritos": None}

    async def h(nombre, params, ctx):
        llamadas.append(nombre)
        if nombre == "get_eventos":
            return {"result": eventos, "error": None}
        if nombre == "get_formularios":
            return {"result": [{"id": 18, "nombre": "nu event register", "estado": "borrador"}], "error": None}
        if nombre == "get_formulario":
            return {"result": form, "error": None}
        if nombre == "get_evento":
            return {"result": ev, "error": None}
        if nombre == "vincular_formulario":
            ev["formulario"] = {"id": 18, "titulo": "nu event register", "estado": "borrador", "slug": "x", "preguntas": 0}
            return {"result": ev, "error": None}
        return {"result": None, "error": "no", "error_kind": "server"}

    for n in ("get_eventos", "get_formularios", "get_formulario", "get_evento", "vincular_formulario"):
        monkeypatch.setitem(ts.TOOLS_REGISTRY, n, {"schema": None, "handler": (lambda n_: lambda p, c: h(n_, p, c))(n)})
    return llamadas


def _go(texto, state, turno, caps=CAPS):
    ctx = {"final_data": {"_turn": turno}}
    out = asyncio.run(sm.handle(texto, Reading(kind="none"), state, caps, ctx))
    if out is None:  # este nivel no lo atiende (lo resuelve el resto del interceptor)
        return None, state
    st = out["final_data"]["_school"]
    if isinstance(st.get("oferta"), dict):
        st["oferta"]["turn"] = turno
    return out, st


def _memoria():
    st = forms.recordar({}, "evento", 727, "nu event", "creado")
    return forms.recordar(st, "formulario", 18, "nu event register", "creado")


def test_vincular_ese_formulario_con_el_evento_de_arriba(backend):
    out, st = _go("Quiero vincular ese formulario con el evento que creamos más arriba", _memoria(), 5)
    assert out["final_data"]["school_outcome"] == "link_confirm"
    assert "nu event register" in out["reply"] and "**nu event**" in out["reply"] and "vincular_formulario" not in backend
    out, st = _go("sí", st, 6)
    assert out["final_data"]["school_outcome"] == "link_done" and "vincular_formulario" in backend


def test_una_confirmacion_vieja_no_autoriza(backend):
    _, st = _go("vincula ese formulario con el evento anterior", _memoria(), 5)
    st["oferta"]["turn"] = 3  # la propuesta es de un turno que ya pasó
    out, _ = _go("sí", st, 6)
    assert "vincular_formulario" not in backend


def test_sin_permiso_no_se_ofrece_vincular(backend):
    caps = {**CAPS, "consultas": ["eventos", "formularios"]}
    out, _ = _go("vincula ese formulario con el evento anterior", _memoria(), 5, caps)
    assert out["final_data"].get("access_denied") and "vincular_formulario" not in backend


def test_titulo_prepara_borrador_y_no_crea_vacio():
    out, st = _go("quiero crear un formulario", {}, 1)
    out, st = _go("Registro de participantes", st, 2)
    assert out["final_data"]["school_outcome"] == "form_prepared" and st["fe"]["id"] is None
    out, st = _go("agrégale nombre completo y correo", st, 3)
    assert [p["titulo"] for p in out["final_data"]["form_draft"]["preguntas"]] == ["Nombre completo", "Correo electrónico"]
    assert out["final_data"]["form_draft"]["guardado"] is False


# ── Varias operaciones en un mensaje ────────────────────────────────────────

import orchestrator.interceptors.schoolsena_plan as plan_mod  # noqa: E402

PEDIDO = ("Créame un evento para el próximo viernes a las 10 de la mañana, hazme un formulario de inscripción con "
          "nombre, documento, correo y ficha, deja el teléfono opcional y vincula el formulario con el evento")


def test_detecta_los_tres_pasos_y_el_ajuste():
    pasos = plan_mod.detectar(PEDIDO)
    assert [c for c, _ in pasos] == ["evento", "formulario", "ajuste", "vincular"]


@pytest.mark.parametrize("msg", ["quiero crear un evento", "crea un formulario con nombre, documento y correo",
                                 "vincula el formulario con el evento", "¿qué eventos y formularios hay?"])
def test_un_solo_paso_no_es_plan(msg):
    assert plan_mod.detectar(msg) is None


def test_plan_pide_el_nombre_y_resume_sin_tocar_school(backend):
    out, st = _go(PEDIDO, {}, 1)
    assert out["final_data"]["school_outcome"] == "plan_flow" and "llamar" in out["reply"]
    out, st = _go("Jornada de bienvenida", st, 2)
    assert out["final_data"]["school_outcome"] == "plan_confirm"
    assert "Teléfono (opcional)" in out["reply"] and "Número de ficha (obligatoria)" in out["reply"]
    assert not {"crear_evento", "crear_formulario", "vincular_formulario"} & set(backend)


def test_hora_corregida_se_lee_de_la_tarde():
    assert plan_mod._hora_de_tarde("mejor a las 4", {"hora": "15:00"}, {"hora": "04:00"})["hora"] == "16:00"
    assert plan_mod._hora_de_tarde("a las 9 de la mañana", {"hora": "15:00"}, {"hora": "09:00"})["hora"] == "09:00"
