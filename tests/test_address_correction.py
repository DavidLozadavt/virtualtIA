"""
tests/test_address_correction.py — Correcciones parciales de dirección.

La gente no repite la dirección completa: corrige el pedazo malo ("no, es 3C-6",
"no es #3C"). Estas pruebas fijan que la vía se conserva, que la distancia se
conserva cuando solo cambia el cruce, y que nada de esto se dispara con
direcciones nuevas ni con un "no" a secas.
"""

import pytest

from core.address_correction import (
    apply_cruce_correction,
    apply_placa_correction,
    correct_address,
    extract_cruce_correction,
    extract_placa_correction,
)


# ── Placa completa: "no, es 3C-6" ───────────────────────────────────────────

@pytest.mark.parametrize("texto,esperado", [
    ("no, es 3C-6", "3C-6"),
    ("No 17-25", "17-25"),
    ("perdón, 17A-25", "17A-25"),
    ("o sea el 18-25", "18-25"),
])
def test_extrae_placa_de_una_correccion_pura(texto, esperado):
    assert extract_placa_correction(texto) == esperado


@pytest.mark.parametrize("texto", [
    "cra 5 #17-25",        # trae vía → dirección nueva, no corrección
    "17-25 en el centro",  # queda contenido → no es corrección pura
    "no",
    "",
])
def test_no_extrae_placa_cuando_no_es_correccion_pura(texto):
    assert extract_placa_correction(texto) is None


def test_correccion_de_placa_conserva_la_via():
    assert correct_address("Cra. 52 #3B-6", "no, es 3C-6") == "Cra. 52 #3C-6"


# ── Solo el cruce: "no es #3C" (la distancia se conserva) ───────────────────

@pytest.mark.parametrize("texto,esperado", [
    ("no es #3c", "3C"),
    ("no es 3c", "3C"),
    ("no, 17A", "17A"),
    ("perdón, #12", "12"),
])
def test_extrae_cruce_suelto(texto, esperado):
    assert extract_cruce_correction(texto) == esperado


@pytest.mark.parametrize("texto", [
    "no, 5",          # número pelado sin "#" ni letra → demasiado ambiguo
    "no",
    "calle 5",        # trae vía
    "no, es 3C-6",    # placa completa → la maneja el otro extractor
])
def test_no_extrae_cruce_cuando_es_ambiguo(texto):
    assert extract_cruce_correction(texto) is None


def test_correccion_de_cruce_conserva_via_y_distancia():
    assert correct_address("Cra. 52 #3B-6", "no es #3c") == "Cra. 52 #3C-6"
    assert correct_address("Cra. 52 #3B-6", "no, 17A") == "Cra. 52 #17A-6"


# ── Precision-first: cuándo NO se corrige nada ──────────────────────────────

@pytest.mark.parametrize("stored,texto", [
    ("Cra. 52 #3B-6", "no"),                            # negativa a secas
    ("Cra. 52 #3B-6", "sí"),                            # confirmación
    ("Cra. 52 #3B-6", "no, es en la calle 5 #12-20"),   # dirección nueva completa
    ("Cra. 52 #3B-6", "no, es en el barrio Bolívar"),   # barrio nuevo
    ("Barrio Modelo", "no es #3c"),                     # previa sin vía que corregir
    ("Centro Comercial Campanario", "no, es 3C-6"),     # previa es un POI
    (None, "no, es 3C-6"),                              # sin dirección previa
    ("", "no, es 3C-6"),
])
def test_no_corrige_fuera_de_su_alcance(stored, texto):
    assert correct_address(stored, texto) is None


def test_apply_placa_devuelve_none_sin_via_previa():
    assert apply_placa_correction("Modelo", "3C-6") is None
    assert apply_placa_correction(None, "3C-6") is None
    assert apply_placa_correction("Cra. 52 #3B-6", "") is None


def test_apply_cruce_devuelve_none_sin_distancia_previa():
    # Sin placa previa no hay distancia que conservar.
    assert apply_cruce_correction("Cra. 52", "3C") is None
    assert apply_cruce_correction(None, "3C") is None


# ── El orquestador de voz usa exactamente esta lógica, no una copia ─────────

def test_el_orquestador_de_voz_delega_en_este_modulo():
    from services.voice import orchestrator

    assert orchestrator._extract_placa_correction is extract_placa_correction
