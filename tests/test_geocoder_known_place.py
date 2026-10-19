"""Etiqueta de barrio en lugares conocidos (POI) — consistencia geográfica.

Un lugar conocido (centro comercial, hospital, universidad, terminal, parque,
iglesia…) NUNCA debe asociarse a un barrio por similitud LÉXICA en su nombre
("Centro Comercial Campanario" no está en el barrio Centro). El barrio debe salir
de la ubicación REAL del lugar (proximidad geográfica).
"""

from core.geo_types import GeoCandidate, LocationType, ResolutionStatus
from core.geocoder_service import (
    _barrio_by_proximity,
    _is_known_place,
    _resolved,
)


def _cand(lat, lng, neighborhood=None, display="X"):
    return GeoCandidate(
        lat=lat, lng=lng, display_name=display, source="google",
        location_type=LocationType.GEOMETRIC_CENTER, confidence=0.5,
        neighborhood=neighborhood,
    )


def test_is_known_place_detection():
    assert _is_known_place("Centro Comercial Campanario")
    assert _is_known_place("Hospital Susana López")
    assert _is_known_place("Universidad del Cauca")
    assert _is_known_place("Terminal de Transporte")
    assert not _is_known_place("Cra. 17 #6E-20")
    assert not _is_known_place("María Oriente")


def test_known_place_not_labeled_by_lexical_centro():
    # CC Campanario (norte) NO debe quedar en barrio "Centro" por la palabra
    # "centro" del nombre; el barrio se infiere por la ubicación real.
    mall = _cand(2.459635, -76.594210, neighborhood=None,
                 display="Centro Comercial Campanario, Popayán")
    res = _resolved("Centro Comercial Campanario", 1, mall)
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected.neighborhood not in (None, "", "Centro", "el centro")


def test_known_place_keeps_google_neighborhood():
    # Si Google ya dio un barrio (derivado de coords), se respeta — no se
    # sobreescribe por proximidad ni por léxico.
    mall = _cand(2.459635, -76.594210, neighborhood="Ciudad Capri", display="CC Campanario")
    res = _resolved("Centro Comercial Campanario", 1, mall)
    assert res.selected.neighborhood == "Ciudad Capri"


def test_street_address_lexical_barrio_override_still_works():
    # No-regresión: para una dirección de vía, el barrio nombrado por el usuario
    # sigue mandando sobre lo que diga Google (no es un lugar conocido).
    c = _cand(2.4307, -76.6012, neighborhood="Carrera 17", display="Cra. 17 #6E-20")
    res = _resolved("Cra. 17 #6E-20, María Oriente", 1, c)
    assert res.selected.neighborhood == "María Oriente"


def test_barrio_by_proximity_none_on_missing_coords():
    assert _barrio_by_proximity(None, None) is None
    assert _barrio_by_proximity(2.4419, None) is None
