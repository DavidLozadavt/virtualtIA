"""
Fase 3, SchoolSena: lo que se le dice al usuario distingue "no hay datos" de
"no aplica a tu rol", "no tienes permiso", "no existe" y "no pude consultar".
Los payloads son los del LyraController actualizado (validados contra el
backend local con usuarios de prueba; ver scripts/fixtures/school_local_lyra.php).
"""

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from orchestrator.interceptors.schoolsena_present import (
    present_fichas, present_instructores, present_nomina, present_notas, present_pendientes,
)


def test_instructores_separa_vigentes_vencidos_e_indefinidos():
    data = [
        {"id": 1, "nombre": "Ana Ruiz", "tipo": "Contratista", "fecha_fin": "2030-01-31", "indefinido": False, "vigente": True},
        {"id": 2, "nombre": "Luis Paz", "tipo": "TERMINO INDEFINIDO", "fecha_fin": None, "indefinido": True, "vigente": True},
        {"id": 3, "nombre": "Eva Sol", "tipo": "Contratista", "fecha_fin": "2026-01-01", "indefinido": False, "vigente": False},
    ]
    txt = present_instructores(data, {})
    assert "2 instructores con contrato vigente" in txt
    assert "término indefinido" in txt and "2070" not in txt
    assert "Eva Sol" not in txt and "1 contrato sigue marcado como activo" in txt


def test_instructores_resume_listas_largas_y_da_la_completa_si_se_pide():
    data = [{"id": i, "nombre": f"Persona {i:02d}", "tipo": "Contratista", "fecha_fin": "2030-01-01",
             "indefinido": False, "vigente": True} for i in range(25)]
    corto = present_instructores(data, {})
    assert corto.count("\n- **") == 10 and "15 más" in corto
    completo = present_instructores(data, {"full_list": True})
    assert "Persona 24" in completo and "más" not in completo


def test_pendientes_separa_lo_vencido():
    manana = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    pasado = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    txt = present_pendientes([
        {"tituloActividad": "Proyecto", "fechaLimite": manana, "nombreMateria": "Algoritmos"},
        {"tituloActividad": "Lectura", "fechaLimite": pasado, "nombreMateria": "Algoritmos"},
    ], {})
    assert "Por entregar (1)" in txt and "plazo vencido (1)" in txt
    assert txt.index("Proyecto") < txt.index("Lectura")


def test_un_examen_es_un_cuestionario_cuando_el_backend_lo_dice():
    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d 10:00:00")
    data = [
        {"tituloActividad": "Parcial", "tipoActividad": "cuestionario", "calificacionNumerica": "4.2",
         "fechaCalificacion": ayer, "nombreMateria": "592376 - 03  DESARROLLAR ALGORITMOS"},
        {"tituloActividad": "Taller", "tipoActividad": "con evidencia", "calificacionNumerica": "3.0",
         "fechaCalificacion": ayer, "nombreMateria": "x"},
    ]
    txt = present_notas(data, {"grades_filter": {"date": None, "words": [], "mentions_exam": True}})
    assert "Parcial" in txt and "Taller" not in txt
    assert "Desarrollar algoritmos" in txt and "592376" not in txt
    assert "no marca qué actividades son exámenes" not in txt


def test_nomina_sin_salarios_no_dice_cero():
    txt = present_nomina({"total_nomina_mensual": 0, "total_contratos": 38}, {})
    assert "$0" not in txt and "ninguno tiene salario registrado" in txt


def test_fichas_registradas_pero_ninguna_activa():
    txt = present_fichas([{"id": 1, "codigo": "2558", "activa": False}], {})
    assert "ninguna activa" in txt


# ── Tipos de error de la API ─────────────────────────────────────────────────

def _con_respuesta(monkeypatch, status, body=None, exc=None):
    import tools.schoolsena as ss

    class Cliente:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            if exc:
                raise exc
            req = httpx.Request("GET", url)
            if isinstance(body, (dict, list)):
                return httpx.Response(status, json=body, request=req)
            return httpx.Response(status, text=body or "", request=req)

    monkeypatch.setattr(ss.httpx, "AsyncClient", Cliente)
    return ss


@pytest.mark.parametrize("status,body,exc,kind", [
    (404, {"error": "Ficha no encontrada"}, None, "not_found"),
    (404, "<html>Not Found</html>", None, "no_route"),
    (403, {"error": "x"}, None, "forbidden"),
    (401, {"error": "x"}, None, "auth"),
    (500, {"error": "x"}, None, "server"),
    (None, None, httpx.ConnectError("x"), "unreachable"),
    (None, None, httpx.ReadTimeout("x"), "timeout"),
])
def test_cada_fallo_se_clasifica(monkeypatch, status, body, exc, kind):
    ss = _con_respuesta(monkeypatch, status, body, exc)
    out = asyncio.run(ss._api_get("lyra/fichas/activas", "Bearer x"))
    assert out["error_kind"] == kind


def test_un_fallo_de_comunicacion_no_se_presenta_como_vacio(monkeypatch):
    ss = _con_respuesta(monkeypatch, None, None, httpx.ConnectError("x"))
    from orchestrator.interceptors.schoolsena import _run
    r = asyncio.run(_run("get_fichas_activas", {}, {"auth_header": "Bearer x"}, "", "No hay fichas"))
    assert "No es que no haya datos" in r["reply"] and "No hay fichas" not in r["reply"]


def test_ficha_por_codigo_con_respaldo_por_id(monkeypatch):
    import tools.schoolsena as ss
    llamadas = []

    async def falso(endpoint, auth, params=None):
        llamadas.append(endpoint)
        if "codigo" in endpoint:
            return {"error": "esa consulta no está disponible", "error_kind": "no_route"}
        return {"ficha_id": 7, "codigo": "2558", "total_estudiantes": 3}

    monkeypatch.setattr(ss, "_api_get", falso)
    r = asyncio.run(ss.get_estudiantes_ficha({"ficha_id": "2558"}, {"auth_header": "x"}))
    assert llamadas == ["lyra/fichas/codigo/2558/estudiantes", "lyra/fichas/2558/estudiantes"]
    assert r["result"]["total_estudiantes"] == 3


def test_examen_y_resultados_se_leen_como_notas():
    from orchestrator.interceptors import schoolsena_nlu as nlu
    r = nlu.read("dame los resultados de mi examen", {}, (False, False, True))
    assert r.capability == "grades" and r.mentions_exam and "resultados" not in r.filter_words
