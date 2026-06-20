"""
tests/test_address_recovery.py — Task 1 (bug item 7).

Verifica que reattach_address_details() impida la pérdida del número de casa y
del landmark cuando un extractor intermedio (LLM / catálogo local / referencia
humana) recorta la dirección a solo el nombre de la vía.

Caso real del bug: el usuario dictó "Calle Cuarta, número 26, Camilo Torres";
el extractor devolvió solo "Calle Cuarta" y al geocoder llegó "Cl. 4".
"""

from core.address_utils import reattach_address_details, normalize_colombian_address


# (a) dirección con número de casa simple
def test_recovers_simple_house_number():
    # Extractor recortó a la vía; el número "12" debe re-adjuntarse.
    out = reattach_address_details("carrera 5 numero 12", "Carrera 5")
    assert "#" in out
    assert "12" in out
    assert out == "Cra. 5 # 12"


# (b) dirección con número + landmark
def test_recovers_number_and_landmark():
    out = reattach_address_details(
        "Calle Cuarta, número 26, Camilo Torres", "Calle Cuarta"
    )
    assert out == "Cl. 4, # 26, Camilo Torres"
    assert "26" in out and "Camilo Torres" in out


# (c) dirección sin número (caso legítimo) → no se rompe
def test_no_house_number_left_untouched():
    assert reattach_address_details("barrio Modelo", "Modelo") == "Modelo"
    assert reattach_address_details("los sauces", "Los Sauces") == "Los Sauces"


# (d) el caso EXACTO del bug — incluso si el extractor devolvió el landmark
#     canónico en vez de la vía, la dirección completa se recupera.
def test_exact_bug_case_from_any_extractor_output():
    full = "Calle Cuarta, número 26, Camilo Torres"
    # Extractor devolvió la vía
    assert reattach_address_details(full, "Calle Cuarta") == "Cl. 4, # 26, Camilo Torres"
    # Extractor devolvió el landmark (match de catálogo local)
    assert reattach_address_details(full, "Camilo Torres") == "Cl. 4, # 26, Camilo Torres"


# El candidato que YA conserva un número de casa se respeta sin cambios.
def test_existing_house_number_preserved():
    extracted = normalize_colombian_address("carrera 5 # 12-34 barrio Modelo")
    out = reattach_address_details("carrera 5 # 12-34 barrio Modelo", extracted)
    assert out == extracted
    assert "#" in out


# Entradas vacías → no-op seguro.
def test_empty_inputs_are_safe():
    assert reattach_address_details("", "Cl. 4") == "Cl. 4"
    assert reattach_address_details("Cl. 4 # 26", "") == ""
