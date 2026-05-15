"""
services/geo.py — Servicio de geocodificación centralizado para Lyra AI.

Consolida TODAS las llamadas HTTP a APIs de geocodificación:
  - reverse_geocode()       ← de tools/nexiservice.py (Google Maps + Nominatim)
  - forward_geocode()       ← de gateway/router.py geocode_api() logic
  - forward_geocode_city()  ← de tools/nexiservice.py search_businesses() inline

NO incluye:
  - geocode_local()  → queda en tools/popayan_geodata.py (lookup local, sin HTTP)
  - haversine()      → queda en tools/shared/utils.py (math puro)

Criterio de decisión:
  - Hace llamadas HTTP a Google/Nominatim → services/geo.py  ✅
  - Busca en diccionario local de barrios  → popayan_geodata.py (no HTTP)
  - Calcula distancia entre coordenadas    → utils.py (math puro)
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("lyra.services.geo")

# ═══════════════════════════════════════════════════════════════════════════════
# CACHÉ DE GEOCODIFICACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

# Registro local de ciudades principales para evitar rate-limits
COLOMBIA_CITIES_COORDS: dict[str, tuple[float, float, str]] = {
    "popayan": (2.4411, -76.6063, "Popayán"),
    "cali": (3.4516, -76.5320, "Cali"),
    "bogota": (4.7110, -74.0721, "Bogotá"),
    "medellin": (6.2442, -75.5812, "Medellín"),
    "cartagena": (10.3910, -75.5144, "Cartagena"),
    "barranquilla": (10.9685, -74.7813, "Barranquilla"),
    "bucaramanga": (7.1254, -73.1198, "Bucaramanga"),
    "pasto": (1.2136, -77.2811, "Pasto"),
    "manizales": (5.0689, -75.5174, "Manizales"),
    "pereira": (4.8133, -75.6961, "Pereira"),
    "neiva": (2.9273, -75.2819, "Neiva"),
    "ibague": (4.4389, -75.2322, "Ibagué"),
}

GEO_CACHE: dict[str, tuple[float, float, str]] = {}

_USER_AGENT = "lyra-ai/2.0 (geocoding-service)"


# ═══════════════════════════════════════════════════════════════════════════════
# REVERSE GEOCODE — Coordenadas → Ciudad
# ═══════════════════════════════════════════════════════════════════════════════

async def reverse_geocode(lat: float, lng: float) -> dict:
    """
    Convierte coordenadas GPS en un nombre de ciudad.
    Intenta Google Maps primero, fallback a Nominatim.

    Returns:
        {"success": True, "city": "Popayán"} o
        {"success": False, "message": "..."}
    """
    # Intento 1: Google Maps (requiere API key)
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if api_key:
        try:
            url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{lng}&key={api_key}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "OK":
                        city = _extract_city_from_google(data)
                        if city:
                            return {"success": True, "city": city}
        except Exception as e:
            logger.error(f"reverse_geocode Google error: {e}")

    # Intento 2: Nominatim OSM (sin API key)
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {"lat": lat, "lon": lng, "format": "json", "addressdetails": 1}
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code == 200:
                data = r.json()
                addr = data.get("address", {})
                city = (
                    addr.get("city")
                    or addr.get("town")
                    or addr.get("village")
                    or addr.get("county")
                    or "Ubicación detectada"
                )
                return {"success": True, "city": city}
    except Exception as e:
        logger.error(f"reverse_geocode Nominatim error: {e}")

    return {"success": False, "message": "No se pudo determinar la ciudad."}


# ═══════════════════════════════════════════════════════════════════════════════
# FORWARD GEOCODE — Texto → Coordenadas (para API proxy)
# ═══════════════════════════════════════════════════════════════════════════════

async def forward_geocode(query: str) -> list[dict]:
    """
    Geocodifica una dirección o lugar a coordenadas.
    Intenta Google Maps primero, fallback a Nominatim.
    Retorna lista de resultados en formato compatible con Nominatim/Frontend.
    """
    # Intento 1: Google Maps
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if api_key:
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {"address": query, "key": api_key, "region": "co"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "OK" and data.get("results"):
                        return _format_google_results(data["results"][:5])
        except Exception as e:
            logger.error(f"forward_geocode Google error: {e}")

    # Intento 2: Nominatim OSM
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": query, "format": "json", "limit": 3, "countrycodes": "co"}
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                logger.error(f"Nominatim error: {r.status_code} - {r.text}")
                return []
            return r.json()
    except Exception as e:
        logger.error(f"forward_geocode Nominatim error: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# FORWARD GEOCODE CITY — Nombre de ciudad → Coordenadas (para search_businesses)
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_city_coords(
    city_name: str,
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Resuelve nombre de ciudad a coordenadas, usando:
    1. Registro local de ciudades principales (sin HTTP)
    2. Caché de geocodificación (sin HTTP)

    Para Nominatim (con HTTP), usar resolve_city_coords_async().

    Returns: (lat, lng, official_name) o (None, None, None)
    """
    from tools.shared.utils import normalize_text
    city_norm = normalize_text(city_name)

    if city_norm in COLOMBIA_CITIES_COORDS:
        lat, lng, name = COLOMBIA_CITIES_COORDS[city_norm]
        logger.info(f"Ciudad local: {name}")
        return lat, lng, name

    if city_norm in GEO_CACHE:
        lat, lng, name = GEO_CACHE[city_norm]
        logger.info(f"Ciudad cache: {name}")
        return lat, lng, name

    return None, None, None


async def resolve_city_coords_async(
    city_name: str,
) -> tuple[float, float, str]:
    """
    Resuelve nombre de ciudad a coordenadas con fallback HTTP a Nominatim.
    Siempre retorna coordenadas (fallback a Popayán si todo falla).

    Returns: (lat, lng, official_name)
    """
    from tools.shared.utils import normalize_text
    city_norm = normalize_text(city_name)

    # 1. Local + Cache (sin HTTP)
    lat, lng, name = resolve_city_coords(city_name)
    if lat is not None:
        return lat, lng, name

    # 2. Nominatim (HTTP)
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": f"{city_name}, Colombia",
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if data:
                    addr = data[0].get("address", {})
                    official = (
                        addr.get("city")
                        or addr.get("town")
                        or addr.get("village")
                        or city_name
                    )
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    GEO_CACHE[city_norm] = (lat, lng, official)
                    logger.info(f"Ciudad Nominatim: {official} ({lat}, {lng})")
                    return lat, lng, official
            elif r.status_code == 429:
                logger.warning(f"Nominatim 429: {city_name}")
    except Exception as e:
        logger.error(f"Error geocodificando {city_name}: {e}")

    # 3. Fallback: Popayán
    fallback_lat, fallback_lng = 2.4411, -76.6063
    GEO_CACHE[city_norm] = (fallback_lat, fallback_lng, city_name)
    return fallback_lat, fallback_lng, city_name


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS PRIVADOS
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_city_from_google(data: dict) -> Optional[str]:
    """Extrae el nombre de ciudad desde la respuesta de Google Geocoding API."""
    for result in data.get("results", []):
        for comp in result.get("address_components", []):
            if "locality" in comp.get("types", []):
                return comp.get("long_name")
    return None


def _format_google_results(results: list[dict]) -> list[dict]:
    """Convierte resultados de Google Geocoding al formato compatible con Nominatim/Frontend."""
    formatted = []
    for res in results:
        loc = res["geometry"]["location"]
        primary_type = res.get("types", ["locality"])[0]
        formatted.append({
            "lat": str(loc["lat"]),
            "lon": str(loc["lng"]),
            "display_name": res.get("formatted_address", ""),
            "type": primary_type,
            "addresstype": primary_type,
            "class": "place" if "locality" in res.get("types", []) else "boundary",
        })
    return formatted
