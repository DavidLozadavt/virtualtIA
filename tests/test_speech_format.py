"""
tests/test_speech_format.py — Un precio se escribe de una forma y se dice de otra.

El motor de voz recibía "$120.000" y lo leía "ciento veinte, cero, cero, cero".
No es un fallo de formato: es que nadie distinguía entre la forma escrita de una
cifra y su lectura. Estas pruebas fijan las dos, y sobre todo fijan lo que NO se
debe convertir —un teléfono, un número de documento— donde leer dígito a dígito
es justamente lo correcto.

    python -m pytest tests/test_speech_format.py -q
"""

import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.speech_format import (
    format_price,
    humanize_for_speech,
    money_to_words,
    spell_number_es,
    to_amount,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. NÚMEROS EN LETRAS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("numero,esperado", [
    (0, "cero"),
    (15, "quince"),
    (21, "veintiuno"),
    (31, "treinta y uno"),
    (100, "cien"),
    (101, "ciento uno"),
    (500, "quinientos"),
    (1_000, "mil"),
    (21_000, "veintiún mil"),
    (50_000, "cincuenta mil"),
    (120_000, "ciento veinte mil"),
    (250_000, "doscientos cincuenta mil"),
    (1_200_000, "un millón doscientos mil"),
    (2_000_000, "dos millones"),
])
def test_los_numeros_se_dicen_como_los_diria_una_persona(numero, esperado):
    assert spell_number_es(numero) == esperado


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DINERO
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("valor,esperado", [
    (120_000, "ciento veinte mil pesos"),
    (50_000, "cincuenta mil pesos"),
    (250_000, "doscientos cincuenta mil pesos"),
    (1_200_000, "un millón doscientos mil pesos"),
    (1, "un peso"),
])
def test_los_precios_se_leen_en_palabras(valor, esperado):
    assert money_to_words(valor) == esperado


def test_los_millones_redondos_piden_la_preposicion():
    """«Dos millones pesos» delata la plantilla; se dice «de pesos»."""
    assert money_to_words(2_000_000) == "dos millones de pesos"
    assert money_to_words(1_200_000) == "un millón doscientos mil pesos"


def test_el_precio_escrito_usa_el_formato_colombiano():
    assert format_price(120_000) == "$120.000"
    assert format_price(Decimal("50000.00")) == "$50.000"


def test_un_precio_ausente_no_se_inventa():
    """Un «$0» en pantalla se lee como gratis, y no lo es: no se sabe."""
    assert format_price(None) == "Precio a consultar"
    assert format_price(0) == "Precio a consultar"
    assert format_price(None, fallback="") == ""


@pytest.mark.parametrize("crudo,esperado", [
    ("120000", Decimal("120000")),
    ("$120.000", Decimal("120000")),
    ("120,000.50", Decimal("120000.50")),
    ("120.000,50", Decimal("120000.50")),
    (Decimal("35000.00"), Decimal("35000.00")),
])
def test_el_importe_se_reconoce_venga_como_venga(crudo, esperado):
    """Los precios llegan de MySQL como Decimal, del modelo como texto."""
    assert to_amount(crudo) == esperado


# ═══════════════════════════════════════════════════════════════════════════════
# 3. UN TEXTO ENTERO, LISTO PARA DECIRSE
# ═══════════════════════════════════════════════════════════════════════════════

def test_los_precios_dentro_de_una_frase_se_convierten():
    dicho = humanize_for_speech("El corte cuesta $120.000 y el tinte $50.000.")
    assert "ciento veinte mil pesos" in dicho
    assert "cincuenta mil pesos" in dicho
    assert "120" not in dicho


def test_la_puntuacion_de_la_frase_sobrevive():
    """«$50.000.» se llevaba el punto final y pegaba dos oraciones."""
    dicho = humanize_for_speech("Vale $50.000. ¿Te sirve?")
    assert dicho == "Vale cincuenta mil pesos. ¿Te sirve?"


@pytest.mark.parametrize("crudo,esperado", [
    ("a las 09:00", "a las nueve de la mañana"),
    ("a las 13:30", "a la una y media de la tarde"),
    ("a las 12:00", "a las doce del mediodía"),
    ("a las 20:15", "a las ocho y cuarto de la noche"),
    ("a las 16:45", "a las cinco menos cuarto de la tarde"),
])
def test_las_horas_se_dicen_como_una_hora(crudo, esperado):
    """La hora es el dato que hay que retener de una cita."""
    assert humanize_for_speech(crudo) == esperado


def test_un_telefono_no_es_una_cantidad():
    """Un celular SÍ se lee dígito a dígito. Convertirlo sería el error opuesto."""
    dicho = humanize_for_speech("Escríbeles al 3001234567.")
    assert "3001234567" in dicho


def test_un_ano_no_es_una_cantidad():
    dicho = humanize_for_speech("Abrieron en 2019 y siguen ahí.")
    assert "2019" in dicho


def test_una_duracion_no_lleva_moneda():
    dicho = humanize_for_speech("Dura 30 min y cuesta $20.000.")
    assert "30 min" in dicho
    assert "veinte mil pesos" in dicho


def test_el_texto_vacio_no_rompe_nada():
    assert humanize_for_speech("") == ""
    assert humanize_for_speech(None) is None
