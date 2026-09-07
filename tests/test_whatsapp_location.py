"""
tests/test_whatsapp_location.py — La ubicación de WhatsApp debe llegar exacta.

Regresión del bug introducido en d8735d0: una ubicación compartida se
reemplazaba por el landmark más cercano (radio 300 m) o por el barrio más
cercano (radio 2 km), y las coordenadas de ciertos pines se sobrescribían con
un valor fijo. El servicio se creaba en un punto de referencia, no donde estaba
el usuario.

Reglas que se verifican aquí:
  1. Las coordenadas que envía el usuario viajan al backend sin modificarse.
  2. El texto de la ubicación es la dirección exacta (pin o reverse geocoding),
     nunca un landmark o barrio cercano.
  3. Ciudad, departamento, país y código postal se recortan del texto; la
     nomenclatura y el número de casa se conservan completos.
  4. La nomenclatura de vía se escribe siempre igual: 'Cl' y 'Cra'.
"""

import asyncio
import json

import pytest

import api.routers.whatsapp as wp


# ── 3. Limpieza de ciudad / departamento / país ───────────────────────────────

def test_clean_map_location_quita_ciudad_departamento_pais():
    assert wp.clean_map_location(
        "Cra 52 # 3C-6, Popayán, Cauca, 190003, Colombia"
    ) == "Cra 52 # 3C-6"


def test_clean_map_location_sin_comas():
    """Como lo escribe el usuario a mano, sin comas."""
    assert wp.clean_map_location("cra 52 # 3c-6 popayan cauca") == "cra 52 # 3c-6"


def test_clean_map_location_preserva_direccion_completa():
    """El número de casa y la nomenclatura NO se recortan."""
    limpio = wp.clean_map_location("Calle 5 Norte # 22AN-45, Popayán, Colombia")
    assert limpio == "Calle 5 Norte # 22AN-45"


def test_clean_map_location_preserva_nombre_de_sitio_con_direccion():
    limpio = wp.clean_map_location(
        "Centro Comercial Campanario - Cra 9 #24AN-21, Popayán, Cauca"
    )
    assert limpio == "Centro Comercial Campanario - Cra 9 #24AN-21"


def test_clean_map_location_vacio():
    assert wp.clean_map_location("") == ""
    assert wp.clean_map_location(None) == ""


# ── 4. Nomenclatura de vía ────────────────────────────────────────────────────

def test_abreviatura_calle_y_carrera():
    assert wp.abbreviate_street_type("Calle 5 Norte # 22AN-45") == "Cl 5 Norte # 22AN-45"
    assert wp.abbreviate_street_type("carrera 52 # 3c-6") == "Cra 52 # 3c-6"


def test_abreviatura_acepta_variantes_escritas():
    for entrada, esperado in [
        ("cll 16 # 3ce-41", "Cl 16 # 3ce-41"),
        ("Cra. 9 #24AN-21", "Cra 9 #24AN-21"),
        ("Kr 8 con Cl 20", "Cra 8 con Cl 20"),
        ("CR 6 # 10-15", "Cra 6 # 10-15"),
    ]:
        assert wp.abbreviate_street_type(entrada) == esperado


def test_abreviatura_es_idempotente():
    una = wp.abbreviate_street_type("Calle 5 # 3-20")
    assert wp.abbreviate_street_type(una) == una == "Cl 5 # 3-20"


def test_abreviatura_no_toca_otras_palabras():
    """'Callejón' no es 'calle'; un nombre de sitio se respeta."""
    assert wp.abbreviate_street_type("Callejón del Río") == "Callejón del Río"
    assert wp.abbreviate_street_type("Centro Comercial Campanario") == "Centro Comercial Campanario"


def test_format_address_limpia_y_abrevia():
    assert wp.format_address(
        "Calle 5 Norte # 22AN-45, Popayán, Cauca, Colombia"
    ) == "Cl 5 Norte # 22AN-45"


# ── 2. Texto de la ubicación compartida ───────────────────────────────────────

def test_pin_con_direccion_se_usa_tal_cual(monkeypatch):
    """Si el pin ya trae nomenclatura, no se consulta nada más."""
    async def _no_llamar(lat, lng):
        raise AssertionError("no debe hacerse reverse geocoding")

    monkeypatch.setattr(wp, "_nominatim_reverse_geocode_async", _no_llamar)

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4400, -76.6100,
            "Cra 52 # 3C-6, Popayán, Cauca",
            "Ubicación compartida GPS",
        )
    )
    assert texto == "Cra 52 # 3C-6"


def test_pin_sin_nombre_usa_reverse_geocoding(monkeypatch):
    """Ubicación actual compartida (sin name/address de Meta) → dirección real."""
    async def _reverse(lat, lng):
        assert (lat, lng) == (2.4400, -76.6100)
        return "Carrera 52 # 3C-6, Comuna 8, Popayán, Cauca, Colombia"

    monkeypatch.setattr(wp, "_nominatim_reverse_geocode_async", _reverse)

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4400, -76.6100, None, "Ubicación compartida GPS"
        )
    )
    assert texto == "Cra 52 # 3C-6, Comuna 8"


def test_pin_solo_nombre_de_sitio_agrega_direccion(monkeypatch):
    """Un POI sin dirección conserva el nombre pero suma la dirección exacta."""
    async def _reverse(lat, lng):
        return "Carrera 9 # 24AN-21, Popayán, Cauca"

    monkeypatch.setattr(wp, "_nominatim_reverse_geocode_async", _reverse)

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4600, -76.5900, "Centro Comercial Campanario",
            "Ubicación compartida GPS",
        )
    )
    assert texto == "Centro Comercial Campanario - Cra 9 # 24AN-21"


def test_sin_reverse_geocoding_devuelve_enlace_con_coordenadas(monkeypatch):
    """Si el reverse falla, se manda el enlace con las coordenadas EXACTAS —
    nunca un landmark o barrio cercano."""
    async def _reverse(lat, lng):
        return None

    monkeypatch.setattr(wp, "_nominatim_reverse_geocode_async", _reverse)

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4321, -76.6098, None, "Ubicación compartida GPS"
        )
    )
    assert "2.4321,-76.6098" in texto
    assert "Barrio" not in texto


# ── 1. Coordenadas exactas hasta el payload del backend ───────────────────────

class _FakeResponse:
    status_code = 200
    text = "ok"

    def json(self):
        return {"ok": True}


def _fake_client_factory(capturado):
    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            capturado["url"] = url
            capturado["payload"] = json
            return _FakeResponse()

    return _FakeAsyncClient


@pytest.fixture
def backend_capturado(monkeypatch):
    capturado = {}

    async def _sin_reactivacion(phone, company_id):
        return False, None

    monkeypatch.setattr(wp, "_tiene_reactivacion_pendiente", _sin_reactivacion)
    monkeypatch.setattr(wp.httpx, "AsyncClient", _fake_client_factory(capturado))
    return capturado


def test_coordenadas_compartidas_llegan_sin_modificar(monkeypatch, backend_capturado):
    """Coordenadas dentro del radio donde antes se sobreescribían con un valor
    fijo (2.4307, -76.6012). Deben viajar tal cual las mandó el usuario."""
    async def _reverse(lat, lng):
        return "Calle 4 # 12-30, Popayán, Cauca"

    monkeypatch.setattr(wp, "_nominatim_reverse_geocode_async", _reverse)

    ok, _ = asyncio.run(
        wp._create_wp_service(
            "3001234567",
            "Ubicación en mapa: 2.4288,-76.5987",
            None,
            "taxi ahora",
            None,
            None,
        )
    )

    assert ok is True
    payload = backend_capturado["payload"]
    assert payload["origen_lat"] == 2.4288
    assert payload["origen_lng"] == -76.5987
    assert payload["origen"] == "Cl 4 # 12-30"


def test_destino_compartido_conserva_sus_coordenadas(monkeypatch, backend_capturado):
    async def _reverse(lat, lng):
        return f"Dirección de {lat}"

    monkeypatch.setattr(wp, "_nominatim_reverse_geocode_async", _reverse)

    ok, _ = asyncio.run(
        wp._create_wp_service(
            "3001234567",
            "Ubicación en mapa: 2.4400,-76.6100 | Cra 52 # 3C-6, Popayán",
            "Ubicación en mapa: 2.4301,-76.6009",
            "domicilio",
            None,
            None,
        )
    )

    assert ok is True
    payload = backend_capturado["payload"]
    assert payload["origen"] == "[DOMICILIO] Cra 52 # 3C-6"
    assert payload["origen_lat"] == 2.4400
    assert payload["origen_lng"] == -76.6100
    assert payload["destino_lat"] == 2.4301
    assert payload["destino_lng"] == -76.6009


def test_direccion_escrita_va_completa_sin_ciudad(backend_capturado):
    """Dirección escrita: sin coordenadas propias, pero el texto llega completo
    y sin la ciudad/departamento al final."""
    ok, _ = asyncio.run(
        wp._create_wp_service(
            "3001234567",
            "Cra 52 # 3C-6, Popayán, Cauca",
            None,
            "taxi ahora",
            None,
            None,
        )
    )

    assert ok is True
    payload = backend_capturado["payload"]
    assert payload["origen"] == "Cra 52 # 3C-6"
    assert payload["origen_lat"] == 0.0
    assert payload["origen_lng"] == 0.0


def test_payload_es_serializable(backend_capturado, monkeypatch):
    async def _reverse(lat, lng):
        return "Calle 4 # 12-30, Popayán"

    monkeypatch.setattr(wp, "_nominatim_reverse_geocode_async", _reverse)

    asyncio.run(
        wp._create_wp_service(
            "3001234567",
            "Ubicación en mapa: 2.4288,-76.5987",
            None,
            "taxi ahora",
            None,
            None,
        )
    )
    json.dumps(backend_capturado["payload"])
