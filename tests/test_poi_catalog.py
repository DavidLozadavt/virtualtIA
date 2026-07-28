"""
tests/test_poi_catalog.py — Display names de POI.

Garantiza que el catálogo solo cambia la PRESENTACIÓN y que las direcciones
normales (nomenclatura, barrios, urbanizaciones, conjuntos, intersecciones)
siguen mostrándose exactamente igual que hoy.
"""

import json

import pytest

from core import poi_catalog
from core.poi_catalog import (
    display_for,
    is_known_poi,
    normalize_key,
    poi_display_name,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    poi_catalog.reset_cache()
    yield
    poi_catalog.reset_cache()


# ── Normalización ───────────────────────────────────────────────────────────

def test_normalize_key_quita_tildes_puntuacion_y_mayusculas():
    assert normalize_key("C.C. Campanario") == "cc campanario"
    assert normalize_key("  Aeropuerto  Guillermo León Valencia ") == (
        "aeropuerto guillermo leon valencia"
    )
    assert normalize_key(None) == ""


# ── POI del catálogo por defecto ────────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    "Campanario",
    "campanario",
    "CC Campanario",
    "C.C. Campanario",
    "Centro Comercial Campanario",
    "el campanario",
    "Recógeme en el Campanario",
    "estoy en cc campanario por favor",
])
def test_alias_de_campanario_devuelven_display_name(texto):
    assert poi_display_name(texto) == "Centro Comercial Campanario"


def test_alias_generico_solo_como_texto_completo():
    # "Centro Comercial" a secas → Campanario (exact_alias del catálogo).
    assert poi_display_name("Centro Comercial") == "Centro Comercial Campanario"
    # Pero dentro de una frase con otro POI, gana el alias más largo.
    assert poi_display_name("centro comercial anarkos") == "Centro Comercial Anarkos"


@pytest.mark.parametrize("texto", [
    "Terraplaza",
    "terraplaza",
    "Terra Plaza",
    "CC Terraplaza",
    "C.C. Terraplaza",
    "Centro Comercial Terraplaza",
    "recógeme en el terraplaza",
])
def test_alias_de_terraplaza_devuelven_display_name(texto):
    assert poi_display_name(texto) == "Centro Comercial Terraplaza"


def test_terraplaza_no_colisiona_con_campanario():
    assert poi_display_name("centro comercial terraplaza") == "Centro Comercial Terraplaza"
    assert poi_display_name("centro comercial campanario") == "Centro Comercial Campanario"


def test_otros_poi_del_catalogo():
    assert poi_display_name("terminal") == "Terminal de Transportes"
    assert poi_display_name("unicauca") == "Universidad del Cauca"
    assert poi_display_name("llévame al aeropuerto") == (
        "Aeropuerto Guillermo León Valencia"
    )


def test_is_known_poi():
    assert is_known_poi("Campanario") is True
    assert is_known_poi("Calle 5 #12-20") is False


# ── Direcciones normales: NO deben tener display name ───────────────────────

@pytest.mark.parametrize("texto", [
    "Calle 5 #12-20",
    "Carrera 9 #73N-200",
    "Cra 17 #6E-20",
    "Cl 8c # 17-55",
    "Barrio Modelo",
    "Conjunto Los Andes",
    "Urbanización La Paz",
    "Bello Horizonte",
    "María Oriente",
    "Calle 5 con Carrera 9",
    "",
    None,
])
def test_direcciones_normales_no_tienen_display_name(texto):
    assert poi_display_name(texto) is None


def test_direccion_con_nomenclatura_nunca_recibe_display_name():
    # Aunque el texto mencione el POI, si trae nomenclatura vial es una
    # dirección: se muestra tal cual (precision-first).
    assert poi_display_name("Cra 9 #73N-200, Campanario") is None


# ── display_for: contrato de presentación ───────────────────────────────────

def test_display_for_usa_display_name_solo_cuando_hay_poi():
    direccion = "Cra 9 #73N-200 Norte, Popayán"
    assert display_for("Campanario", direccion) == "Centro Comercial Campanario"
    assert display_for("Calle 5 #12-20", "Calle 5 #12-20, Popayán") == (
        "Calle 5 #12-20, Popayán"
    )


def test_display_for_sin_poi_devuelve_la_direccion_oficial_intacta():
    direccion = "Carrera 9 #73N-200 Norte, Popayán, Cauca"
    assert display_for("carrera 9 73n 200", direccion) == direccion


def test_display_for_sin_direccion_devuelve_el_texto_del_usuario():
    assert display_for("Barrio Modelo", None) == "Barrio Modelo"


# ── Catálogo configurable: crecer sin tocar código ──────────────────────────

def test_catalogo_es_configurable_por_archivo(tmp_path, monkeypatch):
    catalogo = tmp_path / "poi.json"
    catalogo.write_text(json.dumps({
        "pois": [{
            "name": "Estadio Ciro López",
            "display_name": "Estadio Ciro López",
            "aliases": ["ciro lopez", "el estadio"],
        }]
    }), encoding="utf-8")

    monkeypatch.setattr(poi_catalog, "_default_path", lambda: catalogo)
    poi_catalog.reset_cache()

    assert poi_display_name("ciro lopez") == "Estadio Ciro López"
    assert poi_display_name("vamos para el estadio") == "Estadio Ciro López"
    # El catálogo por defecto ya no aplica: solo manda el archivo configurado.
    assert poi_display_name("Campanario") is None


def test_catalogo_ausente_no_rompe(tmp_path, monkeypatch):
    monkeypatch.setattr(poi_catalog, "_default_path", lambda: tmp_path / "no_existe.json")
    poi_catalog.reset_cache()
    assert poi_display_name("Campanario") is None


def test_catalogo_invalido_no_rompe(tmp_path, monkeypatch):
    roto = tmp_path / "roto.json"
    roto.write_text("{ esto no es json", encoding="utf-8")
    monkeypatch.setattr(poi_catalog, "_default_path", lambda: roto)
    poi_catalog.reset_cache()
    assert poi_display_name("Campanario") is None


def test_entrada_sin_display_name_usa_el_nombre_oficial(tmp_path, monkeypatch):
    catalogo = tmp_path / "poi.json"
    catalogo.write_text(json.dumps({
        "pois": [{"name": "Coliseo Blanco", "aliases": ["coliseo"]}]
    }), encoding="utf-8")
    monkeypatch.setattr(poi_catalog, "_default_path", lambda: catalogo)
    poi_catalog.reset_cache()

    assert poi_display_name("coliseo") == "Coliseo Blanco"
