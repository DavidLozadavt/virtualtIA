"""
Comprensión local de SchoolSena: capacidades, ranuras, permisos y contexto.

La última prueba corre los conjuntos de evaluación (`eval/schoolsena_*.yaml`)
por el mismo camino que `/chat` y exige su nivel actual: cualquier cambio que
los empeore falla aquí.
"""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from orchestrator.interceptors import schoolsena_nlu as nlu

STUDENT = (False, False, True)
INSTRUCTOR = (False, True, False)
COORD = (True, False, False)
MONDAY = datetime(2026, 11, 16, 9, 0)


@pytest.mark.parametrize("text, cap, tf", [
    ("oe q clases tngo hoy", "schedule", "today"),
    ("tengo alguna clase para dia de mañana?", "schedule", "tomorrow"),
    ("horarioa", "schedule", "week"),
    ("actividades pendiente para el dia de hoy", "activities", "today"),  # el momento es ranura, no intención
    ("mis calificasiones", "grades", None),
    ("cuanto saque en mis notas", "grades", None),
])
def test_capacidad_y_ranura_temporal(text, cap, tf):
    r = nlu.read(text, {}, STUDENT, now=MONDAY)
    assert (r.kind, r.capability) == ("capability", cap)
    if tf:
        assert r.timeframe == tf


def test_choques_de_la_cascada_anterior_ya_no_ocurren():
    # "cuánto cuesta" iba a nómina; "general" al dashboard; "clima de hoy" a clases.
    assert nlu.read("cuanto cuesta el servicio", {}, STUDENT).kind == "out_of_scope"
    assert nlu.read("quien gano el partido ayer", {}, STUDENT).kind == "out_of_scope"
    assert nlu.read("busco trabajo", {}, COORD).kind != "capability"
    # "¿cuánto gana un instructor?" sí es nómina.
    assert nlu.read("cuanto gana un instructor", {}, COORD).capability == "payroll"
    # "horario instructor" es el horario propio, no la lista de instructores.
    assert nlu.read("horario instructor", {}, INSTRUCTOR).capability == "schedule"


def test_errata_en_capacidad_de_coordinacion():
    assert nlu.read("lista intructores", {}, COORD).capability == "instructors"
    assert nlu.read("esumen de nómina", {}, COORD).capability == "payroll"


def test_ficha_y_entregas_piden_el_numero_si_falta():
    r = nlu.read("dame los aprendices de la ficha", {}, INSTRUCTOR)
    assert (r.kind, r.reason) == ("clarify", "missing_ficha")
    r = nlu.read("quienes estan en la ficha 2558", {}, INSTRUCTOR)
    assert (r.capability, r.ficha) == ("students", "2558")


def test_seguimientos_usan_el_contexto():
    state = nlu.remember({}, nlu.Reading(kind="capability", capability="students", ficha="2558"))
    r = nlu.read("y de la 2601?", state, INSTRUCTOR)
    assert (r.capability, r.ficha, r.from_context) == ("students", "2601", True)

    state = nlu.remember({}, nlu.Reading(kind="capability", capability="schedule", timeframe="today"))
    r = nlu.read("¿y mañana?", state, STUDENT, now=MONDAY)
    assert (r.capability, r.timeframe, r.day) == ("schedule", "tomorrow", "MARTES")

    fichas = [{"type": "ficha", "id": "2558"}, {"type": "ficha", "id": "2601"}]
    state = nlu.remember({}, nlu.Reading(kind="capability", capability="fichas"), presented=fichas)
    r = nlu.read("y los estudiantes del segundo", state, INSTRUCTOR)
    assert (r.capability, r.ficha) == ("students", "2601")


def test_el_contexto_caduca():
    state = nlu.remember({}, nlu.Reading(kind="capability", capability="students", ficha="2558"))
    state["ts"] = time.time() - 3600
    assert nlu.read("y de la 2601?", state, INSTRUCTOR).from_context is False


@pytest.mark.parametrize("path, min_ok", [
    ("eval/schoolsena_dev.yaml", 0.98),
    ("eval/schoolsena_heldout.yaml", 0.96),
])
def test_conjuntos_de_evaluacion_no_empeoran(path, min_ok):
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    import eval_nlu

    results = asyncio.run(eval_nlu.evaluate([str(root / path)]))
    ok = sum(r["outcome"] in ("local-ok", "gate-expected") for r in results) / len(results)
    assert ok >= min_ok, [r for r in results if r["outcome"] != "local-ok"]
    assert not any(r["outcome"] == "gate" for r in results)
