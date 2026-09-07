# core/address_utils.py
"""
Utilidades de texto, STT y extracción de direcciones.

Responsabilidades de este módulo:
  - Normalización de nomenclatura colombiana
  - Eliminación de preámbulos coloquiales ("hola, me regala un taxi en...")
  - Corrección de errores STT frecuentes para Popayán
  - Parsing de intención (sí/no, corrección, repetición)
  - Extracción básica de dirección desde texto libre

Lo que NO hace este módulo (desde refactor 2026-06-01):
  - Geocodificación → ver core/geocoder_service.py
  - Catálogos de barrios → eliminado (era popayan_geodata)
  - Resolución de nombres canónicos → eliminado

Ver docs/geocoding/04-files-changed.md para detalle de cambios.
"""

import re
import logging
import httpx
import time
import threading
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timezone, timedelta
from collections import OrderedDict

from core.logger import setup_logger
from core.llm_utils import get_openai_client, get_model, extract_json_object, call_llm
from core.config import settings

logger = setup_logger("lyra.core.address_utils")

# ── FILLER WORDS & PREAMBLE PATTERNS ─────────────────────────────────────────

_FILLER_WORDS = {
    "me regala", "me daría", "necesito", "por favor", "un taxi", "un móvil",
    "un carro", "para", "en", "desde", "estoy en", "me encuentro en",
    "sería para", "quiero", "podría", "amiga", "amigo", "mija", "mijo",
    "tío", "tio", "tía", "tia", "vecina", "vecino", "hola", "buenas",
    "buenos días", "buenas tardes", "buenas noches", "qué hubo", "qhubo",
    "mira", "vea", "un favor",
}

_PREAMBLE_PATTERNS = [
    r'^(?:hola\s+)?(?:buenas\s+noches|buenas\s+tardes|buen\s+día|buenas|hola|qhubo|qué\s+hubo|amiga|amigo|vecina|vecino|mija|mijo|señor|señora),?\s*',
    r'^(?:por favor|un favor|oiga|mire|oye|disculpe|disculpa),?\s*',
    r'^(?:me\s+regala|me\s+daría|necesito|quiero|podría\s+solicitar|pídame|pídeme|solicito)\s*(?:un|el)?\s*(?:taxi|móvil|movil|carro|servicio|carrito)\s*',
    r'^(?:por\s+aquí\s+en|aquí\s+en|acá\s+en|estoy\s+en|me\s+encuentro\s+en|ubicad[ao]\s+en|estamos\s+en|en|desde)\s*',
    r'^(?:sería\s+para|es\s+para|para)\s*',
    r'\s+(?:por\s+favor|porfavor|porfa|gracias|please)\.?\s*$',
]

# ── CORRECCIONES STT ──────────────────────────────────────────────────────────
# Solo para corrección de texto transcrito. No son fuentes de geocodificación.

_SPEECH_CORRECTIONS: Dict[str, str] = {
    # ── "los sauces" ──
    "entonces": "los sauces",
    "en sauce": "los sauces",
    "en sauces": "los sauces",
    "lo sauce": "los sauces",
    "las sauces": "los sauces",
    "ensauces": "los sauces",
    "el sauce": "los sauces",
    "los sauce": "los sauces",
    "lo sauces": "los sauces",
    # ── "valle del ortigal" ──
    "valle vertical": "valle del ortigal",
    "valle del vertical": "valle del ortigal",
    "valle de ortigal": "valle del ortigal",
    "valle ortigal": "valle del ortigal",
    "balle del ortigal": "valle del ortigal",
    "va del ortigal": "valle del ortigal",
    "vale del ortigal": "valle del ortigal",
    "valle ordinal": "valle del ortigal",
    "valle el ortigal": "valle del ortigal",
    "valle original": "valle del ortigal",
    # ── "maría oriente" ──
    "mari oriente": "maría oriente",
    "maria de oriente": "maría oriente",
    "maria oriente": "maría oriente",
    "maría de oriente": "maría oriente",
    "la maría oriente": "maría oriente",
    # ── "maría occidente" ──
    "maria occidente": "maría occidente",
    "mari occidente": "maría occidente",
    "maría de occidente": "maría occidente",
    # ── "la esmeralda" ──
    "la esmerada": "la esmeralda",
    "esmerada": "la esmeralda",
    "esmeranda": "la esmeralda",
    "la esmeralda se": "la esmeralda",
    # ── "pandiguando" ──
    "pandi cuando": "pandiguando",
    "pan de cuando": "pandiguando",
    "pandi guando": "pandiguando",
    "pandiguandos": "pandiguando",
    # ── "yanaconas" ──
    "yanaco más": "yanaconas",
    "yanacona": "yanaconas",
    "jana con as": "yanaconas",
    "janacona": "yanaconas",
    "yanacones": "yanaconas",
    # ── "campanario" ──
    "campana río": "campanario",
    "campana rio": "campanario",
    "el campana rio": "campanario",
    "el campanarios": "campanario",
    # ── "belalcázar" ──
    "bella alcázar": "belalcázar",
    "bella alcazar": "belalcázar",
    "belal cázar": "belalcázar",
    "belga azar": "belalcázar",
    # ── "los comuneros" ──
    "lo comunero": "los comuneros",
    "lo comuneros": "los comuneros",
    "los comunero": "los comuneros",
    # ── "alfonso lópez" ──
    "alfonzo lópez": "alfonso lópez",
    "alfonzo lopez": "alfonso lópez",
    "alfonso lope": "alfonso lópez",
    "alfonso lópe": "alfonso lópez",
    # ── "pueblillo" ──
    "pueblo illo": "pueblillo",
    "pueblito": "pueblillo",
    "pueblo ijo": "pueblillo",
    # ── "yambitará" ──
    "jambitará": "yambitará",
    "jambitara": "yambitará",
    "jan bitara": "yambitará",
    # ── "loma de la virgen" ──
    "forma de la virgen": "loma de la virgen",
    "roma de la virgen": "loma de la virgen",
    "loma la virgen": "loma de la virgen",
    # ── "terminal" ──
    "la terminal": "terminal",
    "el terminal": "terminal",
    # ── "lomas de granada" ──
    "loma de granada": "lomas de granada",
    "lomas granada": "lomas de granada",
    # ── "la sombrilla" ──
    "la sombilla": "la sombrilla",
    "la sombrija": "la sombrilla",
    # ── "cinco de abril" ──
    "5 de abril": "cinco de abril",
    "sinco de abril": "cinco de abril",
    # ── "la pamba" ──
    "la pampa": "la pamba",
    "la bamba": "la pamba",
    # ── "parque caldas" ──
    "parque calda": "parque caldas",
    "parque de caldas": "parque caldas",
    # ── "valparaíso" ──
    "balparaíso": "valparaíso",
    "valpa raíso": "valparaíso",
    "valpariso": "valparaíso",
    # ── "primero de mayo" ──
    "1 de mayo": "primero de mayo",
    "primer de mayo": "primero de mayo",
    # ── varios ──
    "kennedy": "kennedy",
    "retiro al sol": "retiro alto",
    "santa en elena": "santa helena",
    "la campiñas": "la campiña",
    "el triunfos": "el triunfo",
    "la florida": "la florida",
}

# ── NOMINATIM (geocodificación directa — uso limitado) ───────────────────────
# Estas funciones son wrappers síncronos para casos donde geocoder_service.py
# no es apropiado (ej. código síncrono en stt_enhancer.py).
# Para el pipeline principal, usar core/geocoder_service.py.

GEOCODE_SUFFIX       = "Popayán, Cauca, Colombia"
NOMINATIM_URL        = "https://nominatim.openstreetmap.org/search"
GEOCODE_COUNTRYCODES = "co"
GEOCODE_VIEWBOX      = "-76.82,2.58,-76.42,2.32"
GEOCODE_USER_AGENT   = "lyra-intellitaxi/1.0 (contact: admin)"
GOOGLE_GEOCODE_URL   = "https://maps.googleapis.com/maps/api/geocode/json"

POPAYAN_MIN_LAT, POPAYAN_MAX_LAT = 2.32, 2.58
POPAYAN_MIN_LNG, POPAYAN_MAX_LNG = -76.82, -76.42

_GEOCODE_CACHE: OrderedDict = OrderedDict()
_GEOCODE_CACHE_LOCK = threading.Lock()
_NOMINATIM_LOCK     = threading.Lock()
_NOMINATIM_LAST_REQ = 0.0
GEOCODE_MIN_INTERVAL = 1.1


# Nomenclatura de vía: distingue una dirección de un nombre de barrio o sitio.
_VIA_SIGNAL_RE = re.compile(
    r'\b(calle|carrera|cra|cr|cl|kr|kra|avenida|av|diagonal|diag|'
    r'transversal|tr|autopista|anillo|v[íi]a)\b',
    re.IGNORECASE,
)


def _in_popayan_bbox(lat: float, lng: float) -> bool:
    return POPAYAN_MIN_LAT <= lat <= POPAYAN_MAX_LAT and POPAYAN_MIN_LNG <= lng <= POPAYAN_MAX_LNG


def _geocode_cache_get(key: str):
    with _GEOCODE_CACHE_LOCK:
        if key in _GEOCODE_CACHE:
            _GEOCODE_CACHE.move_to_end(key)
            return _GEOCODE_CACHE[key]
    return None


def _geocode_cache_set(key: str, val):
    with _GEOCODE_CACHE_LOCK:
        _GEOCODE_CACHE[key] = val
        _GEOCODE_CACHE.move_to_end(key)
        while len(_GEOCODE_CACHE) > 256:
            _GEOCODE_CACHE.popitem(last=False)


def _nominatim_geocode_raw(query: str) -> Optional[Tuple[float, float, str]]:
    """Geocodifica via Nominatim. Sin fallback a catálogos locales."""
    global _NOMINATIM_LAST_REQ
    q = (query or "").strip()
    if not q:
        return None

    if GEOCODE_SUFFIX and GEOCODE_SUFFIX.lower() not in q.lower():
        q = f"{q}, {GEOCODE_SUFFIX}"

    cached = _geocode_cache_get(q)
    if cached is not None:
        return cached

    params = {
        "q": q, "format": "json", "limit": 8, "addressdetails": 0,
        "countrycodes": GEOCODE_COUNTRYCODES,
        "viewbox": GEOCODE_VIEWBOX, "bounded": "1",
    }
    headers = {"User-Agent": GEOCODE_USER_AGENT, "Accept": "application/json"}

    try:
        for attempt in range(3):
            with _NOMINATIM_LOCK:
                now  = time.monotonic()
                wait = _NOMINATIM_LAST_REQ + GEOCODE_MIN_INTERVAL - now
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = httpx.get(NOMINATIM_URL, params=params, headers=headers, timeout=5.0)
                finally:
                    _NOMINATIM_LAST_REQ = time.monotonic()

            if r.status_code == 200:
                break
            if r.status_code == 429 and attempt < 2:
                time.sleep(min(2.0 ** attempt, 10.0))
                continue
            return None

        data = r.json()
        if not isinstance(data, list) or not data:
            return None

        for row in data:
            lat, lon = float(row.get("lat", 0)), float(row.get("lon", 0))
            if _in_popayan_bbox(lat, lon):
                name   = str(row.get("display_name", ""))
                result = (lat, lon, name)
                _geocode_cache_set(q, result)
                return result
        return None

    except Exception as exc:
        logger.error(f"Geocode error: {exc}")
        return None


async def _nominatim_geocode_async(query: str) -> Optional[Tuple[float, float, str]]:
    """Wrapper async del geocoder síncrono."""
    import asyncio
    return await asyncio.to_thread(_nominatim_geocode_raw, query)


def _compose_street(via: str, numero: Optional[str]) -> str:
    """'Cra. 26' + '#2-45' → 'Cra. 26 # 2-45'. Sin número, solo la vía."""
    via = (via or "").strip()
    num = (numero or "").strip().lstrip("#").strip()
    return f"{via} # {num}" if num else via


# Nombres que NO son un barrio: jerarquía administrativa, ciudad, departamento.
_BARRIO_NOISE_RE = re.compile(
    r'^(?:comuna\b.*|per[íi]metro\s+urbano.*|rap\b.*|regi[óo]n\b.*|'
    r'provincia\b.*|municipio\b.*|localidad\b.*|corregimiento\b.*|'
    r'zona\s+(?:urbana|rural)\b.*|popay[áa]n|cauca|colombia|\d+)$',
    re.IGNORECASE,
)


def _clean_barrio(nombre: Optional[str]) -> Optional[str]:
    """Valida el barrio que reportó el geocoder para ESE punto.

    Descarta comuna, perímetro urbano, RAP, ciudad, departamento y números
    sueltos: no son barrios y confunden al conductor. No hay aproximación por
    cercanía — si el geocoder no reporta barrio, no hay barrio.
    """
    b = (nombre or "").strip().strip(",")
    b = re.sub(r'^barrio\s+', '', b, flags=re.IGNORECASE).strip()
    if len(b) < 3 or _BARRIO_NOISE_RE.match(b):
        return None
    return b


def _google_reverse_parts_raw(lat: float, lng: float) -> Dict[str, Optional[str]]:
    """Vía y barrio del punto EXACTO, via Google Geocoding (reverse).

    Google tiene mejor cobertura de nomenclatura urbana en Colombia que OSM:
    donde Nominatim solo conoce el barrio, Google suele devolver la carrera o
    calle con número. Ambos datos salen de la respuesta para esas coordenadas
    —nunca de un catálogo por cercanía—, así que el barrio es el que contiene
    al punto, no el más próximo.
    """
    vacio: Dict[str, Optional[str]] = {"street": None, "barrio": None}

    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return vacio

    cache_key = f"rev_goo_parts_{lat}_{lng}"
    cached = _geocode_cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    params = {
        "latlng": f"{lat},{lng}",
        "key": api_key,
        "language": "es",
        "region": "co",
    }

    try:
        r = httpx.get(GOOGLE_GEOCODE_URL, params=params, timeout=6.0)
        if r.status_code != 200:
            return vacio

        data = r.json()
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            logger.warning(f"Google reverse geocode status: {status}")
            return vacio

        street: Optional[str] = None
        barrio: Optional[str] = None

        for result in data.get("results", []):
            comps = result.get("address_components") or []

            if street is None:
                via = _google_component(comps, "route")
                if via:
                    street = _compose_street(via, _google_component(comps, "street_number"))

            if barrio is None:
                for tipo in ("neighborhood", "sublocality_level_1", "sublocality"):
                    barrio = _clean_barrio(_google_component(comps, tipo))
                    if barrio:
                        break

            if street and barrio:
                break

        partes: Dict[str, Optional[str]] = {"street": street, "barrio": barrio}
        _geocode_cache_set(cache_key, dict(partes))
        return partes

    except Exception as exc:
        logger.error(f"Google reverse geocode error: {exc}")
        return vacio


def _google_reverse_geocode_raw(lat: float, lng: float) -> Optional[str]:
    """Solo la vía del punto exacto (compatibilidad)."""
    return _google_reverse_parts_raw(lat, lng).get("street")


def _google_component(components: list, tipo: str) -> Optional[str]:
    for comp in components:
        if tipo in (comp.get("types") or []):
            valor = (comp.get("long_name") or "").strip()
            if valor:
                return valor
    return None


# Precisiones de Google que corresponden a una dirección concreta. APPROXIMATE
# es el centroide del barrio o de la ciudad: sirve para pintar un mapa, no para
# mandar un taxi ni para decidir en qué barrio queda el punto.
_GOOGLE_PRECISION_EXACTA = ("ROOFTOP", "RANGE_INTERPOLATED")


def _google_geocode_raw(direccion: str) -> Optional[Tuple[float, float]]:
    """Coordenadas de una dirección ESCRITA, solo si Google la ubica exacta.

    Devuelve None cuando Google solo alcanza a dar una precisión aproximada
    (centro del barrio o de la ciudad): con eso no se puede resolver el barrio
    ni despachar un conductor.
    """
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    consulta = (direccion or "").strip()
    if not api_key or len(consulta) < 4:
        return None

    cache_key = f"geo_goo_{consulta.lower()}"
    cached = _geocode_cache_get(cache_key)
    if cached is not None:
        return tuple(cached) if cached else None

    params = {
        "address": f"{consulta}, Popayán, Cauca, Colombia",
        "components": "country:CO",
        "key": api_key,
        "language": "es",
        "region": "co",
    }

    try:
        r = httpx.get(GOOGLE_GEOCODE_URL, params=params, timeout=6.0)
        if r.status_code != 200:
            return None

        data = r.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            logger.warning(f"Google geocode status: {data.get('status')}")
            return None

        for result in data.get("results", []):
            geo = result.get("geometry") or {}
            if geo.get("location_type") not in _GOOGLE_PRECISION_EXACTA:
                continue
            loc = geo.get("location") or {}
            lat, lng = float(loc.get("lat", 0)), float(loc.get("lng", 0))
            if _in_popayan_bbox(lat, lng):
                _geocode_cache_set(cache_key, (lat, lng))
                return (lat, lng)

        _geocode_cache_set(cache_key, ())
        return None

    except Exception as exc:
        logger.error(f"Google geocode error: {exc}")
        return None


async def geocode_direccion_escrita(direccion: str) -> Optional[Tuple[float, float]]:
    """Coordenadas de una dirección que el cliente escribió, o None.

    Google primero (precisión ROOFTOP/RANGE_INTERPOLATED), Nominatim después.
    Sirve para dos cosas: resolver el barrio con `core.barrio_popayan` y mandarle
    al backend las coordenadas reales en vez de 0,0.
    """
    import asyncio

    punto = await asyncio.to_thread(_google_geocode_raw, direccion)
    if punto:
        return punto

    osm = await _nominatim_geocode_async(direccion)
    if osm and _in_popayan_bbox(osm[0], osm[1]):
        return (osm[0], osm[1])
    return None


def _nominatim_reverse_parts_raw(lat: float, lng: float) -> Dict[str, Optional[str]]:
    """Vía, barrio y respaldo textual del punto EXACTO, via Nominatim."""
    global _NOMINATIM_LAST_REQ

    vacio: Dict[str, Optional[str]] = {"street": None, "barrio": None, "fallback": None}

    cache_key = f"rev_geo_parts_{lat}_{lng}"
    cached = _geocode_cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    params  = {"lat": lat, "lon": lng, "format": "json", "addressdetails": 1}
    headers = {"User-Agent": GEOCODE_USER_AGENT, "Accept": "application/json"}

    try:
        for attempt in range(3):
            with _NOMINATIM_LOCK:
                now  = time.monotonic()
                wait = _NOMINATIM_LAST_REQ + GEOCODE_MIN_INTERVAL - now
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = httpx.get(
                        "https://nominatim.openstreetmap.org/reverse",
                        params=params, headers=headers, timeout=5.0,
                    )
                finally:
                    _NOMINATIM_LAST_REQ = time.monotonic()

            if r.status_code == 200:
                break
            if r.status_code == 429 and attempt < 2:
                time.sleep(min(2.0 ** attempt, 10.0))
                continue
            return vacio

        data = r.json()
        addr = data.get("address") or {}

        # La dirección: vía + número de casa. La jerarquía administrativa que
        # devuelve Nominatim (comuna, perímetro urbano, RAP, departamento,
        # país) no le sirve al conductor y ensucia el mensaje.
        via = (
            addr.get("road")
            or addr.get("pedestrian")
            or addr.get("residential")
            or addr.get("footway")
        )
        street = _compose_street(str(via), addr.get("house_number")) if via else None

        # Barrio que CONTIENE al punto según OSM (no el más cercano).
        barrio = _clean_barrio(
            addr.get("neighbourhood")
            or addr.get("suburb")
            or addr.get("quarter")
            or addr.get("city_district")
        )

        # Respaldo textual: el primer segmento del display_name.
        fallback = None
        if data.get("display_name"):
            fallback = str(data["display_name"]).split(",")[0].strip() or None

        partes: Dict[str, Optional[str]] = {
            "street": street,
            "barrio": barrio,
            "fallback": fallback,
        }
        _geocode_cache_set(cache_key, dict(partes))
        return partes

    except Exception as exc:
        logger.error(f"Reverse geocode error: {exc}")
        return vacio


def _nominatim_reverse_geocode_raw(lat: float, lng: float) -> Optional[str]:
    """Texto del punto: vía si la hay, si no barrio, si no el respaldo."""
    partes = _nominatim_reverse_parts_raw(lat, lng)
    return partes.get("street") or partes.get("barrio") or partes.get("fallback")


async def _nominatim_reverse_geocode_async(lat: float, lng: float) -> Optional[str]:
    import asyncio
    return await asyncio.to_thread(_nominatim_reverse_geocode_raw, lat, lng)


async def _nominatim_reverse_parts_async(lat: float, lng: float) -> Dict[str, Optional[str]]:
    import asyncio
    return await asyncio.to_thread(_nominatim_reverse_parts_raw, lat, lng)


async def _google_reverse_parts_async(lat: float, lng: float) -> Dict[str, Optional[str]]:
    import asyncio
    return await asyncio.to_thread(_google_reverse_parts_raw, lat, lng)


async def _google_reverse_geocode_async(lat: float, lng: float) -> Optional[str]:
    import asyncio
    return await asyncio.to_thread(_google_reverse_geocode_raw, lat, lng)


async def reverse_geocode_location(lat: float, lng: float) -> Dict[str, Optional[str]]:
    """Vía y barrio de unas coordenadas EXACTAS: {'street', 'barrio'}.

    Los dos datos vienen del geocoder para ESE punto —Google primero, Nominatim
    para lo que falte—, nunca de un catálogo por cercanía.

    OJO con 'barrio': medido en Popayán (2026-09-07), NINGUNO de los dos
    proveedores tiene datos de barrio usables. Google devuelve la comuna como
    sublocality ("Comuna 1") o una urbanización vecina, y en muchos puntos no
    devuelve nada; OSM devuelve el barrio colindante (en Valle del Ortigal
    responde "Villa Colombia"). Por eso el flujo de WhatsApp NO usa esta clave:
    el barrio hay que obtenerlo del cliente, no del geocoder.
    """
    partes = await _google_reverse_parts_async(lat, lng)
    street = partes.get("street")
    barrio = partes.get("barrio")

    if street and barrio:
        return {"street": street, "barrio": barrio}

    osm = await _nominatim_reverse_parts_async(lat, lng)
    street = street or (osm.get("street") if _VIA_SIGNAL_RE.search(osm.get("street") or "") else None)
    barrio = barrio or osm.get("barrio")

    return {"street": street, "barrio": barrio}


async def reverse_geocode_street_address(lat: float, lng: float) -> Optional[str]:
    """Solo la dirección con nomenclatura de vía de unas coordenadas exactas."""
    return (await reverse_geocode_location(lat, lng)).get("street")


# Alias de compatibilidad (callers antiguos importan _nominatim_geocode)
_nominatim_geocode = _nominatim_geocode_raw


# ── POPAYAN_PLACES — ELIMINADO ────────────────────────────────────────────────
# Catálogo manual de barrios eliminado en refactor 2026-06-01.
# Ver docs/geocoding/04-files-changed.md
# La resolución de nombres de barrios ocurre via Google address_components
# y via respuestas del usuario en CONTEXT_GATHERING (geocoder_service.py).

POPAYAN_PLACES: dict = {}  # vacío intencionalmente — no añadir entradas


# ── BASIC UTILS ───────────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    for a, b in [('á','a'),('é','e'),('í','i'),('ó','o'),('ú','u'),('ñ','n')]:
        t = t.replace(a, b)
    t = re.sub(r'[^\w\s]', '', t)
    return t.strip()


def _clean_stt_text(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r'^[.?!,;:\s]+', '', t)
    t = re.sub(r'[.?!]+$', '', t)
    for filler in sorted(_FILLER_WORDS, key=len, reverse=True):
        pattern = r'^' + re.escape(filler) + r'[,.]?\s*'
        t = re.sub(pattern, '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'\b(\w+)\s+\1\b', r'\1', t, flags=re.IGNORECASE)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


# ── CORRECTION & PREAMBLE ─────────────────────────────────────────────────────

def _correct_speech(text: str) -> str:
    """Aplica correcciones STT conocidas. No usa catálogos de barrios."""
    if not text:
        return text
    t       = _clean_stt_text(text)
    t_lower = t.lower().strip()
    return _SPEECH_CORRECTIONS.get(t_lower, t)


def _strip_preamble(text: str) -> str:
    """Elimina saludos y relleno del inicio/fin del texto."""
    if not text:
        return ""
    t = text.strip()
    changed = True
    while changed:
        changed = False
        for pattern in _PREAMBLE_PATTERNS:
            new_t = re.sub(pattern, '', t, flags=re.IGNORECASE).strip()
            if new_t != t:
                t       = new_t
                changed = True
    return t if len(t) >= 2 else text.strip()


# ── INTENT PARSING ────────────────────────────────────────────────────────────

def _parse_si_no(text: str) -> Optional[bool]:
    t         = _normalize_text(text)
    # Positivos FUERTES: confirman aunque vengan en una frase más larga.
    positivos = {"si","claro","exacto","correcto","ok","okey","dale","yes","obvio","afirmativo",
                 "confirmo","confirma","confirmado","confirmar","confirmamos","listo","perfecto"}
    # Positivos DÉBILES: palabras que también aparecen en frases que NO confirman
    # ("eso queda en el norte", "así por la galería"). Solo cuentan como "sí" en
    # una respuesta corta y sin señal de lugar/dirección.
    positivos_debiles = {"asi","eso","bien","seguro","vale","bueno"}
    negativos = {"no","nop","nel","nope","negativo","incorrecto","tampoco","nunca","jamas"}

    uncertainty = {"no lo se","no se","no se bien","nose","no lo sé","no sé"}
    if t.strip() in uncertainty:
        return None
    if re.search(r'\bno\s+s[eé]\b', t):
        return None

    words = set(t.split())
    # Negativos primero: "No, sí Sena Norte" tiene "no" + "sí" → el "no"
    # inicial es la corrección; "sí" es parte del contenido que sigue.
    # Evaluar positivos primero hacía que "sí" ganara y confirmara en falso.
    if (words & negativos) or "para nada" in t:
        return False
    if words & positivos:
        return True
    # Débiles: solo si la respuesta es corta (≤2 palabras) y no parece un lugar.
    # Evita falsos "sí" cuando el usuario en realidad nombra/corrige un destino.
    if (words & positivos_debiles) and len(words) <= 2 and not _PLACE_SIGNAL_RE.search(t):
        return True
    return None


def _is_correction_request(text: str) -> bool:
    t        = text.lower()
    triggers = ["corregir","cambiar","equivoke","me equivoque","no es ahi","esta mal","error"]
    return any(trigger in t for trigger in triggers)


def _is_repeat_request(text: str) -> bool:
    t        = text.lower()
    triggers = ["repite","como","no escuche","que dijo","repitame"]
    return any(trigger in t for trigger in triggers)


# ── ADDRESS EXTRACTION ────────────────────────────────────────────────────────

def normalize_address(address: str) -> str:
    """Estandariza nomenclatura (Calle → Cl, etc.)"""
    if not address:
        return ""
    replacements = {
        r'\bcl\b': 'Calle', r'\bcra?\b': 'Carrera', r'\bkra?\b': 'Carrera',
        r'\bav\b': 'Avenida', r'\btr\b': 'Transversal', r'\bdiag?\b': 'Diagonal',
        r'\bno\.\s*': '#',
    }
    result = address
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result.strip()


def _compound_num_replace(m: re.Match) -> str:
    """Convierte 'cuarenta y uno' → '41', etc. en el contexto de una dirección."""
    tens_map  = {"veinte":20,"treinta":30,"cuarenta":40,"cincuenta":50,
                 "sesenta":60,"setenta":70,"ochenta":80,"noventa":90}
    units_map = {"un":1,"uno":1,"dos":2,"tres":3,"cuatro":4,"cinco":5,
                 "seis":6,"siete":7,"ocho":8,"nueve":9}
    t_val = tens_map.get(m.group(1).lower(), 0)
    u_val = units_map.get(m.group(2).lower(), 0)
    return str(t_val + u_val) if t_val else m.group(0)


def normalize_colombian_address(address: str) -> str:
    """
    Normaliza al formato colombiano estándar.
    'carrera cuarta a el # 17 b 28' → 'Cra. 4ae # 17B-28'
    'calle 16 # 3 ce cuarenta y uno' → 'Cl. 16 # 3CE-41'
    """
    if not address:
        return ""

    t = address.strip()

    # 0. Convertir números compuestos STT: "cuarenta y uno" → "41"
    #    Cubre el caso más común: Twilio STT deletrea el número de casa en palabras.
    t = re.sub(
        r'\b(veinte|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa)'
        r'\s+y\s+(un[o]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\b',
        _compound_num_replace,
        t, flags=re.IGNORECASE,
    )
    # Números simples como última parte del número de casa (sin combinación con decenas)
    _simple_end = [
        (r'\b(once)\b', '11'), (r'\b(doce)\b', '12'), (r'\b(trece)\b', '13'),
        (r'\b(catorce)\b', '14'), (r'\b(quince)\b', '15'),
        (r'\b(diecis[eé]is)\b', '16'), (r'\b(diecisiete)\b', '17'),
        (r'\b(dieciocho)\b', '18'), (r'\b(diecinueve)\b', '19'),
    ]
    for pat, repl in _simple_end:
        t = re.sub(pat, repl, t, flags=re.IGNORECASE)

    num_words = {
        "primera":"1","segunda":"2","tercera":"3","cuarta":"4",
        "quinta":"5","sexta":"6","septima":"7","octava":"8",
        "novena":"9","decima":"10","once":"11","doce":"12",
        "trece":"13","catorce":"14","quince":"15","dieciseis":"16",
        "diecisiete":"17","dieciocho":"18","diecinueve":"19","veinte":"20",
    }

    for word, digit in num_words.items():
        t = re.sub(
            rf'\b(calle|carrera|cl|cra|cr|kr|kra)\s+{word}\b',
            rf'\1 {digit}', t, flags=re.IGNORECASE,
        )

    t = re.sub(r'\bn[uú]mero\s*', '# ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bcarrera\s+(\d)', r'Cra. \1', t, flags=re.IGNORECASE)
    t = re.sub(r'\bcalle\s+(\d)',   r'Cl. \1',  t, flags=re.IGNORECASE)
    t = re.sub(r'\bcl\s+(\d)',      r'Cl. \1',  t, flags=re.IGNORECASE)
    t = re.sub(r'\bcra\s+(\d)',     r'Cra. \1', t, flags=re.IGNORECASE)
    t = re.sub(r'\bkr\s+(\d)',      r'Cra. \1', t, flags=re.IGNORECASE)
    t = re.sub(r'\bkra\s+(\d)',     r'Cra. \1', t, flags=re.IGNORECASE)

    t = re.sub(r'(\d+)\s+[aá]\s+el\b', r'\1ae', t, flags=re.IGNORECASE)
    t = re.sub(r'(\d+)\s+ae\b',         r'\1ae', t, flags=re.IGNORECASE)
    t = re.sub(r'(\d+)a\s+ae\b',        r'\1ae', t, flags=re.IGNORECASE)
    t = re.sub(r'(\d+)\s+a\s+b\b',      r'\1ab', t, flags=re.IGNORECASE)

    t = re.sub(
        r'#\s*(\d+)\s+([a-zA-Z]{1,3})\s*[-–]?\s*(\d+)',
        lambda m: f"# {m.group(1)}{m.group(2).upper()}-{m.group(3)}",
        t,
    )
    t = re.sub(r'#\s*(\d+)\s+de\s+(\d+)',   r'# \1-\2', t, flags=re.IGNORECASE)
    t = re.sub(r'#\s*(\d+)\s+(\d+)\s*$',    r'# \1-\2', t, flags=re.IGNORECASE)

    t = re.sub(r'^cra\.', 'Cra.', t)
    t = re.sub(r'^cl\.',  'Cl.',  t)
    t = re.sub(r'^calle(?=[.\s])', 'Cl.', t, flags=re.IGNORECASE)

    return t.strip()


# ── Recuperación de número de casa / landmark perdidos en la extracción ───────

_HOUSE_NUMBER_MARK = "#"


def reattach_address_details(original_user_text: str, extracted: str) -> str:
    """
    Garantiza que el número de casa y el landmark sobrevivan hasta la query del
    geocoder, sin importar qué función intermedia (extractor LLM, match de
    catálogo local, referencia humana) haya recortado la dirección.

    Causa raíz (item 7 del audit, "Auto-aceptación de Direcciones Incompletas"):
    el extractor de origen devuelve solo el nombre de la vía
    ("Calle Cuarta, número 26, Camilo Torres" → "Calle Cuarta") y descarta el
    número de casa ("# 26") y el landmark ("Camilo Torres"). La normalización
    posterior ya no puede recuperar lo que se descartó ANTES de ella.

    `normalize_colombian_address` SÍ preserva número + landmark cuando recibe el
    texto completo del usuario. Por eso, si la normalización del texto original
    contiene un número de casa ('#') que el candidato extraído perdió, usamos la
    normalización completa del original como fuente de verdad: es exactamente lo
    que el usuario dijo, normalizado, sin pérdida.

    Returns:
        El candidato enriquecido, o el `extracted` original si no hay número de
        casa que proteger o si el candidato ya lo conserva.
    """
    if not original_user_text or not extracted:
        return extracted

    norm_full = normalize_colombian_address(_strip_preamble(original_user_text))

    # Sin número de casa en el texto original → no hay detalle de casa que
    # proteger (ej. el usuario solo dio un barrio). No tocar el candidato.
    if _HOUSE_NUMBER_MARK not in norm_full:
        return extracted

    # El candidato extraído ya conserva un número de casa → respetarlo tal cual.
    if _HOUSE_NUMBER_MARK in normalize_colombian_address(extracted):
        return extracted

    logger.info(
        "[ADDR_RECOVER] extracted %r dropped house number/landmark; "
        "recovering full normalized address %r",
        extracted, norm_full,
    )
    return norm_full


# ── Resolución local de barrios/landmarks (catálogo popayan_geodata) ──────────
# Se usan SOLO los nombres/aliases de popayan_geodata (BARRIO_ALIASES, LANDMARKS);
# las coordenadas de ese módulo NO se usan — la geocodificación real ocurre en
# geocoder_service.run_pipeline() vía Google sobre el nombre canónico devuelto.

_LOCAL_MATCH_LOCK = threading.Lock()
_LOCAL_MATCH_INDEX: Optional[Dict[str, str]] = None   # normalized_alias → canonical
_LOCAL_MATCH_ALIAS_KEYS: Optional[list] = None        # alias keys (>=4 chars) para fuzzy/substring

# Errores STT frecuentes de Twilio/Google para barrios de Popayán que NO se
# resuelven por fuzzy (distancia fonética muy grande). Se aplican SOLO en
# contexto de lugar (palabra "barrio" presente o candidato corto ≤2 palabras),
# para no corromper texto normal (ej: "pueden" como verbo en una frase larga).
# clave = forma mal-oída (sin tildes, minúscula) → valor = nombre canónico.
_BARRIO_STT_VARIANTS: Dict[str, str] = {
    # Pubenza ("pueden", "puden" son los mishears reales observados en prod)
    "pueden": "Pubenza", "puden": "Pubenza", "puede": "Pubenza",
    "pubensa": "Pubenza", "pubensa": "Pubenza", "puebenza": "Pubenza",
    "pubenza": "Pubenza", "la pubenza": "Pubenza",
    # Yanaconas
    "anaconas": "Yanaconas", "ianaconas": "Yanaconas", "llanaconas": "Yanaconas",
    "yanaconaz": "Yanaconas", "yanakonas": "Yanaconas", "yanacones": "Yanaconas",
    # Campanario
    "campanaryo": "Campanario", "campanaro": "Campanario", "campana rio": "Campanario",
    # Pandiguando
    "pandeguando": "Pandiguando", "pandigando": "Pandiguando", "pandi guando": "Pandiguando",
    # Belalcázar
    "belalcasar": "Belalcázar", "belal casar": "Belalcázar",
    # Alfonso López
    "alfonso lopes": "Alfonso López", "alfonsol opez": "Alfonso López",
    # La Esmeralda
    "esmeraldas": "La Esmeralda", "la esmeraldaa": "La Esmeralda",
    # Valle del Ortigal
    "hostigal": "Valle del Ortigal", "ortigan": "Valle del Ortigal",
    # Otros frecuentes
    "yambitara": "Yambitará", "yanbitara": "Yambitará",
    "machagara": "Machángara", "valparaso": "Valparaíso",
    "berling": "Berlín", "modello": "Modelo",
}


def _build_local_match_index() -> None:
    global _LOCAL_MATCH_INDEX, _LOCAL_MATCH_ALIAS_KEYS
    if _LOCAL_MATCH_INDEX is not None:
        return
    with _LOCAL_MATCH_LOCK:
        if _LOCAL_MATCH_INDEX is not None:
            return
        try:
            from tools.popayan_geodata import BARRIO_ALIASES, LANDMARKS
            from core.stt_enhancer import strip_accents
        except ImportError:
            _LOCAL_MATCH_INDEX = {}
            _LOCAL_MATCH_ALIAS_KEYS = []
            return

        index: Dict[str, str] = {}

        # BARRIO_ALIASES: {"Canonical": ["alias1", "alias2", ...]}
        for canonical, aliases in BARRIO_ALIASES.items():
            key = strip_accents(canonical.lower().strip())
            if key not in index:
                index[key] = canonical
            for alias in aliases:
                akey = strip_accents(alias.lower().strip())
                if akey not in index:
                    index[akey] = canonical

        # LANDMARKS: {"Landmark Name": (lat, lng)} — canonical = la propia clave
        for name in LANDMARKS:
            nkey = strip_accents(name.lower().strip())
            if nkey not in index:
                index[nkey] = name

        # NOTA: _BARRIO_STT_VARIANTS NO se inyecta aquí a propósito. Esas formas
        # (ej: "pueden", "puede") son palabras españolas comunes; si entraran al
        # índice, el match por subcadena/fuzzy las capturaría en frases normales
        # ("no pueden venir") generando falsos positivos. Se consultan SOLO en el
        # nivel 0 de _try_local_match, que está gated por contexto de barrio.

        _LOCAL_MATCH_INDEX = index
        # Solo aliases >=4 chars para fuzzy/substring (evita ruido de "la", "el"…)
        _LOCAL_MATCH_ALIAS_KEYS = [k for k in index if len(k) >= 4]


# Partículas prepositivas/relleno que preceden a un nombre de lugar.
_PLACE_PREAMBLE_RE = re.compile(
    r'\b(en|por|al|a\s+la|hacia|cerca\s+de|frente\s+a|junto\s+a|'
    r'estoy\s+en|estamos\s+en|aqui\s+en|aca\s+en|el|la|los|las|del|de)\b\s*',
    re.IGNORECASE,
)

# Señal de que el texto contiene una ubicación procesable (calle/barrio/número…).
_PLACE_SIGNAL_RE = re.compile(
    r'\d|#|\b(calle|carrera|cra|cr|cl|kr|kra|diagonal|diag|transversal|tr|'
    r'avenida|av|barrio|sector|conjunto|urbanizaci[oó]n|manzana|vereda|'
    r'corregimiento|norte|sur|oriente|occidente)\b',
    re.IGNORECASE,
)


def _try_local_match(text: str) -> Optional[str]:
    """
    Búsqueda local en el catálogo de barrios/landmarks de Popayán
    (popayan_geodata: BARRIO_ALIASES + LANDMARKS).

    Niveles, en orden de confianza:
      0. Variantes STT curadas (solo en contexto de lugar) → canónico
      1. Match exacto del alias normalizado
      2. Subcadena (alias contenido en el input o viceversa)
      3. Fuzzy fonético (threshold alto)
      3b. Fuzzy relajado SOLO cuando hay contexto fuerte de barrio

    Retorna el nombre canónico o None si no hay match confiable.
    """
    if not text or len(text.strip()) < 3:
        return None

    _build_local_match_index()
    if not _LOCAL_MATCH_INDEX:
        return None

    from core.stt_enhancer import strip_accents

    raw = strip_accents(text.lower().strip())
    has_barrio_kw = bool(re.search(r'\bbarrio\b', raw))

    # Quitar palabra "barrio" + partículas prepositivas para aislar el candidato.
    cleaned = re.sub(r'\bbarrio\b\s*', '', raw)
    cleaned = _PLACE_PREAMBLE_RE.sub('', cleaned).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)

    if not cleaned or len(cleaned) < 3:
        return None

    word_count = len(cleaned.split())
    # Las variantes curadas solo aplican cuando es claramente un lugar:
    # hay palabra "barrio" o el candidato es dominante (≤2 palabras).
    place_context = has_barrio_kw or word_count <= 2

    # 0. Variantes STT curadas (alta precisión, context-gated)
    if place_context:
        if cleaned in _BARRIO_STT_VARIANTS:
            return _BARRIO_STT_VARIANTS[cleaned]
        for tok in cleaned.split():
            if len(tok) >= 4 and tok in _BARRIO_STT_VARIANTS:
                return _BARRIO_STT_VARIANTS[tok]

    # 1-3. Resolución tipada central (exacto / alias / substring / fonético /
    #      fuzzy con cobertura por token y umbrales por tipo). _try_local_match
    #      mantiene un contrato de ALTA precisión: solo devuelve un canónico para
    #      decisiones ACCEPT. Las coincidencias de confianza media (CONFIRM) las
    #      maneja el flujo de Twilio vía resolve_location_entity directamente.
    from core.location_match import resolve_location_entity, decide, Decision

    m = resolve_location_entity(cleaned)
    if decide(m) == Decision.ACCEPT and m.canonical:
        return m.canonical

    return None


def looks_like_place(text: str) -> bool:
    """
    Valida que `text` parezca una ubicación real en Popayán, para descartar
    extracciones basura del LLM (ej: "tu cuenta", "fuerza", "dos").

    True si: tiene señal de dirección (calle/carrera/número/barrio…), resuelve
    a una referencia humana conocida, o matchea el catálogo local de barrios.
    """
    if not text or len(text.strip()) < 3:
        return False

    t = text.strip()

    # 1. Señal explícita de dirección/lugar (número, calle, barrio, sector…)
    if _PLACE_SIGNAL_RE.search(t):
        return True

    # 2. Referencia humana conocida ("por el éxito", "la galería"…)
    try:
        from core.stt_enhancer import resolve_human_reference
        if resolve_human_reference(t):
            return True
    except ImportError:
        pass

    # 3. Match en el catálogo local de barrios/landmarks
    if _try_local_match(t):
        return True

    return False


def extract_pickup_address(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrae dirección de recogida del texto libre.
    Retorna (dirección, None) o (None, None) si no se puede extraer.
    La geocodificación real ocurre en geocoder_service.run_pipeline().
    """
    t_stripped = _strip_preamble(text)

    # Si contiene nomenclatura de calle → retornar normalizado
    if len(t_stripped) > 5 and any(
        kw in t_stripped.lower()
        for kw in ["calle", "carrera", "cra", "cl", "#", "transversal", "diagonal"]
    ):
        return normalize_colombian_address(t_stripped), None

    # Texto sin nomenclatura → solo aceptar si parece un lugar real
    if len(t_stripped) > 3 and looks_like_place(t_stripped):
        return t_stripped, None

    return None, None


def extract_destination_address(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extrae dirección de destino del texto libre."""
    t_stripped = _strip_preamble(text)
    if len(t_stripped) > 3 and looks_like_place(t_stripped):
        return t_stripped, None
    return None, None


# ── DATETIME EXTRACTION ───────────────────────────────────────────────────────

def extract_datetime_local(text: str) -> Optional[Dict[str, str]]:
    t   = _normalize_text(text)
    now = datetime.now(timezone(timedelta(hours=-5)))

    if "ahora" in t or "ya" in t:
        return None

    if "manana" in t:
        target_date = now + timedelta(days=1)
        time_match  = re.search(r'(\d{1,2})(?::(\d{2}))?', t)
        if time_match:
            hh = int(time_match.group(1))
            mm = int(time_match.group(2)) if time_match.group(2) else 0
            if "tarde" in t or "noche" in t or hh < 7:
                hh += 12
            return {
                "fecha_programada": target_date.strftime("%Y-%m-%d"),
                "hora_programada":  f"{hh:02d}:{mm:02d}",
            }

    mins_match = re.search(r'en (\d+) minutos', t)
    if mins_match:
        delta  = int(mins_match.group(1))
        target = now + timedelta(minutes=delta)
        return {
            "fecha_programada": target.strftime("%Y-%m-%d"),
            "hora_programada":  target.strftime("%H:%M"),
        }

    return None


async def extract_datetime_with_llm(user_text: str) -> dict:
    local = extract_datetime_local(user_text)
    if local:
        return local

    tz  = timezone(timedelta(hours=-5))
    now = datetime.now(tz)

    prompt = (
        f"Extrae la fecha y hora programada mencionada por el usuario para un servicio de taxi.\n"
        f"Hoy es {now.strftime('%Y-%m-%d')}, la hora actual es {now.strftime('%H:%M:%S')}.\n"
        f"Responde SOLO en JSON: {{\"fecha_programada\": \"YYYY-MM-DD\", \"hora_programada\": \"HH:MM\"}}\n"
        f"Texto: {user_text}"
    )
    content = await call_llm(prompt, "Output ONLY valid JSON. 24h format.")
    if content:
        res = extract_json_object(content)
        return res or {}
    return {}
