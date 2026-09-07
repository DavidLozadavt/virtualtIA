"""
tests/test_whatsapp_location.py — La ubicación de WhatsApp debe llegar exacta.

Regresión del bug introducido en d8735d0: una ubicación compartida se
reemplazaba por el landmark más cercano (radio 300 m) o por el barrio más
cercano (radio 2 km), y las coordenadas de ciertos pines se sobrescribían con
un valor fijo. El servicio se creaba en un punto de referencia, no donde estaba
el usuario.

Reglas que se verifican aquí:
  1. Las coordenadas que envía el usuario viajan al backend sin modificarse.
  2. Toda ubicación compartida resuelve a la DIRECCIÓN del punto exacto; el
     nombre del barrio o del sitio es solo el respaldo cuando no hay vía.
  3. Ciudad, departamento, comuna, país y código postal se recortan del texto;
     la nomenclatura y el número de casa se conservan completos.
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


def test_format_address_deja_solo_la_direccion():
    """Casos reportados: la jerarquía administrativa de Nominatim sobra."""
    assert wp.format_address(
        "Calle 3C, Villa Colombia, Comuna 9, Popayán, Cauca, RAP Pacífico, Colombia"
    ) == "Cl 3C"
    assert wp.format_address(
        "Carrera 9 # 24AN-21, Comuna 1, Popayán, Cauca"
    ) == "Cra 9 # 24AN-21"


def test_format_address_descarta_el_nombre_del_sitio():
    """Con dirección presente, el nombre del sitio no se manda."""
    assert wp.format_address(
        "Centro Comercial Campanario - Carrera 9 #24AN-21, Popayán, Cauca"
    ) == "Cra 9 #24AN-21"


def test_format_address_sin_via_conserva_el_barrio():
    """Sin nomenclatura de vía, el barrio es la mejor referencia que queda."""
    assert wp.format_address(
        "Ciudad Jardín, Comuna 3, Perímetro Urbano Popayán, Popayán, Cauca, RAP Pacífico"
    ) == "Ciudad Jardín"


def test_format_address_limpia_y_abrevia():
    assert wp.format_address(
        "Calle 5 Norte # 22AN-45, Popayán, Cauca, Colombia"
    ) == "Cl 5 Norte # 22AN-45"


# ── 2. Texto de la ubicación compartida ───────────────────────────────────────

def _mock_reverse(monkeypatch, via=None, barrio=None):
    """Fija la vía y el barrio que el geocoder reporta para el punto."""
    async def _partes(lat, lng):
        return {"street": via, "barrio": barrio}

    monkeypatch.setattr(wp, "reverse_geocode_location", _partes)


def test_pin_con_direccion_se_usa_tal_cual(monkeypatch):
    """La dirección del pin manda sobre la que arme el geocoder."""
    _mock_reverse(monkeypatch, via="Carrera 52 # 3C-6")

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
    _mock_reverse(monkeypatch, via="Carrera 52 # 3C-6")

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4400, -76.6100, None, "Ubicación compartida GPS"
        )
    )
    assert texto == "Cra 52 # 3C-6"


def test_direccion_lleva_el_barrio_del_punto(monkeypatch):
    """Lo que pidieron los conductores: dirección + barrio, del mismo punto."""
    _mock_reverse(monkeypatch, via="Carrera 26 # 2-45", barrio="Ciudad Jardín")

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4500, -76.6000, None, "Ubicación compartida GPS"
        )
    )
    assert texto == "Cra 26 # 2-45, Ciudad Jardín"


def test_pin_con_direccion_tambien_lleva_barrio(monkeypatch):
    """La vía la manda el pin; el barrio lo aporta el geocoder del punto."""
    _mock_reverse(monkeypatch, via="Carrera 99 # 1-1", barrio="Ciudad Jardín")

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4500, -76.6000, "Cra 52 # 3C-6, Popayán", "Ubicación compartida GPS"
        )
    )
    assert texto == "Cra 52 # 3C-6, Ciudad Jardín"


def test_sin_barrio_reportado_la_direccion_va_sola(monkeypatch):
    """Sin barrio del geocoder no se inventa ninguno: mejor sin barrio que
    con uno aproximado."""
    _mock_reverse(monkeypatch, via="Carrera 26 # 2-45", barrio=None)

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4500, -76.6000, None, "Ubicación compartida GPS"
        )
    )
    assert texto == "Cra 26 # 2-45"


def test_pin_con_nombre_de_barrio_resuelve_la_direccion(monkeypatch):
    """Caso reportado: el pin dice 'Ciudad Jardín' pero es una ubicación
    compartida → debe salir la dirección del punto, no el nombre del barrio."""
    _mock_reverse(monkeypatch, via="Carrera 26 # 2-45")

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4500, -76.6000, "Ciudad Jardín", "Ubicación compartida GPS"
        )
    )
    assert texto == "Cra 26 # 2-45"


def test_pin_de_sitio_comercial_resuelve_la_direccion(monkeypatch):
    """Un POI compartido también viaja como dirección, no como nombre."""
    _mock_reverse(monkeypatch, via="Carrera 9 # 24AN-21")

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4600, -76.5900, "Centro Comercial Campanario",
            "Ubicación compartida GPS",
        )
    )
    assert texto == "Cra 9 # 24AN-21"


def test_sin_via_en_el_punto_usa_el_barrio(monkeypatch):
    """Solo cuando el punto no tiene vía se manda el barrio solo."""
    _mock_reverse(monkeypatch, via=None, barrio="Ciudad Jardín")

    texto = asyncio.run(
        wp._resolve_shared_location(
            2.4500, -76.6000, None, "Ubicación compartida GPS"
        )
    )
    assert texto == "Ciudad Jardín"


def test_sin_reverse_geocoding_devuelve_enlace_con_coordenadas(monkeypatch):
    """Si todo el reverse falla, se manda el enlace con las coordenadas EXACTAS
    — nunca un landmark o barrio cercano."""
    _mock_reverse(monkeypatch, via=None, barrio=None)

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
    _mock_reverse(monkeypatch, via="Calle 4 # 12-30", barrio="Bella Vista")

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
    assert payload["origen"] == "Cl 4 # 12-30, Bella Vista"


def test_destino_compartido_conserva_sus_coordenadas(monkeypatch, backend_capturado):
    _mock_reverse(monkeypatch, via="Calle 4 # 12-30")

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
    assert payload["destino"] == "Cl 4 # 12-30"
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
    _mock_reverse(monkeypatch, via="Calle 4 # 12-30")

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


# ── Reverse geocoding: la dirección primero ───────────────────────────────────

def _fake_http_get(monkeypatch, payload):
    """Sustituye la llamada HTTP del reverse geocoding por una respuesta fija."""
    import core.address_utils as au

    class _Resp:
        status_code = 200

        def json(self):
            return payload

    monkeypatch.setattr(au.httpx, "get", lambda *a, **kw: _Resp())
    au._GEOCODE_CACHE.clear()
    return au


def test_reverse_geocode_devuelve_via_y_numero(monkeypatch):
    au = _fake_http_get(monkeypatch, {
        "display_name": "12-34, Calle 3C, Villa Colombia, Comuna 9, Popayán, "
                        "Cauca, RAP Pacífico, Colombia",
        "address": {
            "house_number": "12-34",
            "road": "Calle 3C",
            "neighbourhood": "Villa Colombia",
            "city_district": "Comuna 9",
            "city": "Popayán",
            "state": "Cauca",
            "country": "Colombia",
        },
    })
    assert au._nominatim_reverse_geocode_raw(2.4400, -76.6100) == "Calle 3C # 12-34"


def test_reverse_geocode_sin_numero_de_casa(monkeypatch):
    au = _fake_http_get(monkeypatch, {
        "display_name": "Carrera 9, Centro, Popayán, Cauca, Colombia",
        "address": {"road": "Carrera 9", "suburb": "Centro", "city": "Popayán"},
    })
    assert au._nominatim_reverse_geocode_raw(2.4401, -76.6101) == "Carrera 9"


def test_reverse_geocode_sin_via_usa_barrio(monkeypatch):
    au = _fake_http_get(monkeypatch, {
        "display_name": "Ciudad Jardín, Comuna 3, Popayán, Cauca, Colombia",
        "address": {"neighbourhood": "Ciudad Jardín", "city_district": "Comuna 3"},
    })
    assert au._nominatim_reverse_geocode_raw(2.4402, -76.6102) == "Ciudad Jardín"


def test_google_reverse_compone_via_y_numero(monkeypatch):
    import core.address_utils as au

    monkeypatch.setattr(au.settings, "GOOGLE_MAPS_API_KEY", "test-key")
    _fake_http_get(monkeypatch, {
        "status": "OK",
        "results": [{
            "types": ["street_address"],
            "address_components": [
                {"long_name": "#2-45", "types": ["street_number"]},
                {"long_name": "Cra. 26", "types": ["route"]},
                {"long_name": "Ciudad Jardín", "types": ["neighborhood", "political"]},
                {"long_name": "Popayán", "types": ["locality", "political"]},
            ],
        }],
    })
    assert au._google_reverse_geocode_raw(2.4500, -76.6000) == "Cra. 26 # 2-45"


def test_google_reverse_sin_api_key_no_llama(monkeypatch):
    import core.address_utils as au

    monkeypatch.setattr(au.settings, "GOOGLE_MAPS_API_KEY", "")

    def _no_llamar(*a, **kw):
        raise AssertionError("no debe llamarse a Google sin API key")

    monkeypatch.setattr(au.httpx, "get", _no_llamar)
    assert au._google_reverse_geocode_raw(2.45, -76.60) is None


def _mock_partes(monkeypatch, google, nominatim=None):
    """Fija lo que devuelve cada proveedor de reverse geocoding."""
    import core.address_utils as au

    async def _google(lat, lng):
        return dict(google)

    async def _nominatim(lat, lng):
        if nominatim is None:
            raise AssertionError("no debía consultarse Nominatim")
        return dict(nominatim)

    monkeypatch.setattr(au, "_google_reverse_parts_async", _google)
    monkeypatch.setattr(au, "_nominatim_reverse_parts_async", _nominatim)
    return au


def test_location_prefiere_google(monkeypatch):
    """Con vía y barrio de Google, Nominatim ni se consulta."""
    au = _mock_partes(monkeypatch, {"street": "Cra. 26 # 2-45", "barrio": "Ciudad Jardín"})

    assert asyncio.run(au.reverse_geocode_location(2.45, -76.60)) == {
        "street": "Cra. 26 # 2-45",
        "barrio": "Ciudad Jardín",
    }


def test_location_completa_el_barrio_con_nominatim(monkeypatch):
    """Google resolvió la vía pero no el barrio → lo aporta OSM, mismo punto."""
    au = _mock_partes(
        monkeypatch,
        {"street": "Cra. 26 # 2-45", "barrio": None},
        {"street": None, "barrio": "Ciudad Jardín", "fallback": None},
    )

    assert asyncio.run(au.reverse_geocode_location(2.45, -76.60)) == {
        "street": "Cra. 26 # 2-45",
        "barrio": "Ciudad Jardín",
    }


def test_location_sin_via_devuelve_solo_barrio(monkeypatch):
    """Un barrio no es una dirección: 'street' queda en None."""
    au = _mock_partes(
        monkeypatch,
        {"street": None, "barrio": None},
        {"street": None, "barrio": "Ciudad Jardín", "fallback": "Ciudad Jardín"},
    )

    partes = asyncio.run(au.reverse_geocode_location(2.45, -76.60))
    assert partes["street"] is None
    assert partes["barrio"] == "Ciudad Jardín"


def test_street_address_devuelve_solo_la_via(monkeypatch):
    au = _mock_partes(monkeypatch, {"street": "Cra. 26 # 2-45", "barrio": "Ciudad Jardín"})

    assert asyncio.run(au.reverse_geocode_street_address(2.45, -76.60)) == "Cra. 26 # 2-45"


# ── El barrio debe ser exacto, no aproximado ──────────────────────────────────

def test_clean_barrio_descarta_jerarquia_administrativa():
    """Comuna, perímetro urbano, RAP, ciudad y departamento NO son barrios."""
    import core.address_utils as au

    for ruido in [
        "Comuna 9", "Perímetro Urbano Popayán", "RAP Pacífico", "Popayán",
        "Cauca", "Colombia", "Corregimiento de Cajete", "Zona Urbana", "9",
    ]:
        assert au._clean_barrio(ruido) is None, ruido


def test_clean_barrio_normaliza_el_nombre():
    import core.address_utils as au

    assert au._clean_barrio("Barrio Ciudad Jardín") == "Ciudad Jardín"
    assert au._clean_barrio("  Villa Colombia ,") == "Villa Colombia"
    assert au._clean_barrio(None) is None


def test_google_reverse_extrae_barrio_del_mismo_punto(monkeypatch):
    """El barrio sale de address_components del punto, no de un catálogo."""
    import core.address_utils as au

    monkeypatch.setattr(au.settings, "GOOGLE_MAPS_API_KEY", "test-key")
    _fake_http_get(monkeypatch, {
        "status": "OK",
        "results": [{
            "types": ["street_address"],
            "address_components": [
                {"long_name": "#2-45", "types": ["street_number"]},
                {"long_name": "Cra. 26", "types": ["route"]},
                {"long_name": "Ciudad Jardín", "types": ["neighborhood", "political"]},
                {"long_name": "Comuna 3", "types": ["sublocality", "political"]},
                {"long_name": "Popayán", "types": ["locality", "political"]},
            ],
        }],
    })

    assert au._google_reverse_parts_raw(2.4500, -76.6000) == {
        "street": "Cra. 26 # 2-45",
        "barrio": "Ciudad Jardín",
    }


def test_nominatim_reverse_parts_descarta_comuna_como_barrio(monkeypatch):
    au = _fake_http_get(monkeypatch, {
        "display_name": "Calle 3C, Comuna 9, Popayán, Cauca, Colombia",
        "address": {"road": "Calle 3C", "city_district": "Comuna 9", "city": "Popayán"},
    })

    partes = au._nominatim_reverse_parts_raw(2.4400, -76.6100)
    assert partes["street"] == "Calle 3C"
    assert partes["barrio"] is None
