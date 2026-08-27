"""
tests/test_agenda_coherente.py — La agenda dice lo mismo en todos los turnos.

El fallo: Lyra anunciaba "ya tienen ocupado mañana de 09:00 a 10:00", el usuario
contestaba "mañana podría ser", y en el turno siguiente contestaba "tienen la
agenda libre" — sobre el mismo día. Después dejaba agendar a las 09:00, encima
de la cita que ella misma acababa de nombrar.

La causa eran dos cosas distintas y ninguna estaba en la comprensión:

  1. Cada capa traducía "mañana" a una fecha por su cuenta, y la consulta de
     agenda se olvidó: comparaba `fechaInicial = 'tomorrow'` contra la base y no
     encontraba nada nunca. Sin fecha, la misma consulta miraba hoy y mañana y
     sí veía la cita — de ahí que el primer turno acertara y el segundo no.
  2. No había ninguna comprobación de solape antes de escribir en la agenda.

    python -m pytest tests/test_agenda_coherente.py -q
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.nexiservice import _find_overlap, _free_hours, resolve_date_token


HOY = datetime.now().strftime("%Y-%m-%d")
MANANA = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. "MAÑANA" ES UNA FECHA, NO UNA PALABRA QUE SE MANDE A SQL
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("token,esperado", [
    ("today", HOY),
    ("hoy", HOY),
    ("tomorrow", MANANA),
    ("mañana", MANANA),
    ("manana", MANANA),
    ("2026-12-24", "2026-12-24"),
    (None, None),
])
def test_el_dia_se_traduce_siempre_igual(token, esperado):
    assert resolve_date_token(token) == esperado


def test_pasado_manana_tambien():
    dos_dias = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    assert resolve_date_token("day_after_tomorrow") == dos_dias


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DOS CITAS SE CRUZAN, O NO
# ═══════════════════════════════════════════════════════════════════════════════

class _CursorFalso:
    """Una agenda de mentira, para medir la regla sin tocar la base."""

    def __init__(self, filas):
        self._filas = filas

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._filas


OCUPADA_9_A_10 = [{"horaInicial": "09:00:00", "horaFinal": "10:00:00"}]


@pytest.mark.parametrize("inicio,fin,choca", [
    ("09:00:00", "10:00:00", True),   # exactamente encima
    ("09:30:00", "10:30:00", True),   # empieza dentro
    ("08:30:00", "09:30:00", True),   # termina dentro
    ("08:00:00", "11:00:00", True),   # la contiene
    ("10:00:00", "11:00:00", False),  # empieza justo al acabar la otra
    ("08:00:00", "09:00:00", False),  # termina justo al empezar la otra
    ("11:00:00", "12:00:00", False),  # sin relación
])
def test_el_solape_se_reconoce(inicio, fin, choca):
    cur = _CursorFalso(OCUPADA_9_A_10)
    resultado = _find_overlap(cur, business_id=1, date=MANANA,
                              start_time=inicio, end_time=fin)
    assert bool(resultado) is choca


def test_tocarse_por_un_extremo_no_es_cruzarse():
    """Una cita que acaba a las 10:00 deja las 10:00 libres. Es media hora de agenda."""
    cur = _CursorFalso(OCUPADA_9_A_10)
    assert _find_overlap(cur, 1, MANANA, "10:00:00", "11:00:00") is None


def test_sin_hora_de_fin_se_asume_una_hora():
    """
    Hay filas en la agenda sin `horaFinal`. Suponer que duran cero dejaba pasar
    el solape entero: la cita siguiente se escribía justo encima.
    """
    cur = _CursorFalso([{"horaInicial": "09:00:00", "horaFinal": None}])
    assert _find_overlap(cur, 1, MANANA, "09:30:00", "10:30:00") is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LO QUE SE OFRECE A CAMBIO
# ═══════════════════════════════════════════════════════════════════════════════

def test_las_horas_libres_excluyen_la_ocupada():
    cur = _CursorFalso(OCUPADA_9_A_10)
    libres = _free_hours(cur, 1, MANANA, duration_min=60)
    assert "09:00" not in libres
    assert "08:00" in libres
    assert "10:00" in libres


def test_un_servicio_largo_necesita_mas_hueco():
    """Con dos horas de duración, las 08:00 ya no caben antes de la cita de las 9."""
    cur = _CursorFalso(OCUPADA_9_A_10)
    libres = _free_hours(cur, 1, MANANA, duration_min=120)
    assert "08:00" not in libres
    assert "10:00" in libres


def test_un_dia_completo_no_ofrece_nada():
    lleno = [
        {"horaInicial": f"{h:02d}:00:00", "horaFinal": f"{h + 1:02d}:00:00"}
        for h in range(8, 19)
    ]
    assert _free_hours(_CursorFalso(lleno), 1, MANANA, duration_min=60) == []
