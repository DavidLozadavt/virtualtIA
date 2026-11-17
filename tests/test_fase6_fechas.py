"""
tests/test_fase6_fechas.py — Fechas de calendario para operaciones de School.

    python -m pytest tests/test_fase6_fechas.py -q
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic.fechas import fecha_larga, leer_fecha  # noqa: E402

MAR = date(2026, 11, 17)   # martes
VIE = date(2026, 11, 20)   # viernes
FIN_MES = date(2026, 11, 30)  # lunes, fin de mes
FIN_ANIO = date(2026, 12, 29)  # martes, fin de año


@pytest.mark.parametrize("hoy,frase,esperada", [
    (MAR, "este viernes", "2026-11-20"),
    (MAR, "el viernes", "2026-11-20"),
    (MAR, "el próximo viernes", "2026-11-20"),
    (MAR, "el viernes de la próxima semana a las diez de la noche", "2026-11-27"),
    (MAR, "el viernes de la semana que viene", "2026-11-27"),
    (MAR, "mañana", "2026-11-18"),
    (MAR, "pasado mañana", "2026-11-19"),
    (MAR, "mañana a las 9 de la mañana", "2026-11-18"),
    (MAR, "dentro de ocho días", "2026-11-24"),
    (MAR, "dentro de 3 días", "2026-11-20"),
    (MAR, "en una semana", "2026-11-24"),
    (MAR, "el lunes siguiente", "2026-11-23"),
    (MAR, "el último viernes del mes", "2026-11-27"),
    (MAR, "el primer lunes del próximo mes", "2026-12-07"),
    (MAR, "el 20 de diciembre", "2026-12-20"),
    (MAR, "el 5 de enero", "2027-01-05"),
    (MAR, "20/12", "2026-12-20"),
    (VIE, "este viernes", "2026-11-20"),
    (VIE, "el viernes", "2026-11-27"),
    (VIE, "el viernes de la próxima semana", "2026-11-27"),
    (FIN_MES, "mañana", "2026-12-01"),
    (FIN_MES, "el primer lunes del próximo mes", "2026-12-07"),
    (FIN_ANIO, "el primer lunes del próximo mes", "2027-01-04"),
    (FIN_ANIO, "pasado mañana", "2026-12-31"),
    (FIN_ANIO, "el viernes de la próxima semana", "2027-01-08"),
    (FIN_ANIO, "el 2 de enero", "2027-01-02"),
])
def test_fecha(hoy, frase, esperada):
    assert leer_fecha(frase, hoy)[0] == esperada


def test_proximo_dia_cercano_avisa():
    fecha, aviso = leer_fecha("el próximo viernes", MAR)
    assert fecha == "2026-11-20" and aviso and "próxima semana" in aviso


def test_sin_fecha():
    assert leer_fecha("Encuentro de instructores", MAR) == (None, None)


def test_fecha_larga():
    assert fecha_larga("2026-11-27") == "viernes 27 de noviembre de 2026"
