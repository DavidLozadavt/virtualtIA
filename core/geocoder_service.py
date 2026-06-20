"""
core/geocoder_service.py — Pipeline de geocodificación en capas.

Flujo principal (Fase 1):
  1. Cache en memoria (LRU, max 500)
  2. Cache en MySQL (location_cache)
  3. Google Geocoding API (todos los resultados, no solo results[0])
  4. Nominatim (fallback cuando no hay Google key o Google falla)
  5. CONTEXT_GATHERING si resultado débil o ausente

Regla de auto-aceptación:
  Solo ROOFTOP + RANGE_INTERPOLATED dentro de POPAYAN_URBAN_BBOX.
  GEOMETRIC_CENTER / APPROXIMATE / ZERO_RESULTS → solicitar contexto al usuario.

Fase 2 (diferida):
  geo_human_aliases — aprendizaje progresivo de contexto humano.
  Ver docs/geocoding/03-phase2-deferred.md
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Optional

import httpx

from core.config import settings
from core.geo_types import (
    GeoCandidate,
    GeoResolution,
    LocationType,
    ResolutionStatus,
    MAX_PIPELINE_ATTEMPTS,
    MAX_CANDIDATES_SHOWN,
    in_urban_bbox,
    in_wide_bbox,
    POPAYAN_CENTER as _POPAYAN_CENTER,
)

logger = logging.getLogger("lyra.geocoder")

# ── URLs de APIs ─────────────────────────────────────────────────────────────

_GOOGLE_URL    = "https://maps.googleapis.com/maps/api/geocode/json"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# ── LRU Cache en memoria ─────────────────────────────────────────────────────

_MEM_CACHE: OrderedDict[str, GeoCandidate] = OrderedDict()
_MEM_LOCK = threading.Lock()
_MEM_MAX  = 500


def _mem_get(key: str) -> Optional[GeoCandidate]:
    with _MEM_LOCK:
        if key in _MEM_CACHE:
            _MEM_CACHE.move_to_end(key)
            return _MEM_CACHE[key]
    return None


def _mem_set(key: str, candidate: GeoCandidate) -> None:
    with _MEM_LOCK:
        _MEM_CACHE[key] = candidate
        _MEM_CACHE.move_to_end(key)
        while len(_MEM_CACHE) > _MEM_MAX:
            _MEM_CACHE.popitem(last=False)


# ── Nominatim rate limiter ────────────────────────────────────────────────────

_NOM_LOCK     = threading.Lock()
_NOM_LAST_REQ = 0.0
_NOM_INTERVAL = 1.1  # seg — respeta ToS de Nominatim público


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_get(canonical_query: str) -> Optional[GeoCandidate]:
    try:
        from core.database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT lat, lng, display_name, neighborhood, source, location_type, confidence "
                    "FROM location_cache "
                    "WHERE query_hash = SHA2(%s, 256) AND is_valid = 1",
                    (canonical_query,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    "UPDATE location_cache SET query_count = query_count + 1 "
                    "WHERE query_hash = SHA2(%s, 256)",
                    (canonical_query,),
                )
                loc_type = LocationType(row["location_type"]) if row["location_type"] else LocationType.CACHE
                return GeoCandidate(
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                    display_name=row["display_name"] or canonical_query,
                    source="cache",
                    location_type=loc_type,
                    confidence=float(row["confidence"] or 0.8),
                    neighborhood=row.get("neighborhood"),
                )
    except Exception as e:
        logger.warning(f"[DB_CACHE] read error: {e}")
    return None


def _db_set(canonical_query: str, c: GeoCandidate) -> None:
    try:
        from core.database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO location_cache "
                    "(canonical_query, lat, lng, display_name, neighborhood, source, location_type, confidence, is_valid) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1) "
                    "ON DUPLICATE KEY UPDATE "
                    "lat = VALUES(lat), lng = VALUES(lng), "
                    "display_name = VALUES(display_name), "
                    "neighborhood = VALUES(neighborhood), "
                    "source = VALUES(source), "
                    "location_type = VALUES(location_type), "
                    "confidence = VALUES(confidence), "
                    "is_valid = 1",
                    (canonical_query, c.lat, c.lng, c.display_name, c.neighborhood,
                     c.source, c.location_type.value, c.confidence),
                )
    except Exception as e:
        logger.warning(f"[DB_CACHE] write error: {e}")


# ── Google Geocoding ──────────────────────────────────────────────────────────

_GOOGLE_TYPE_CONFIDENCE = {
    LocationType.ROOFTOP:            1.00,
    LocationType.RANGE_INTERPOLATED: 0.80,
    LocationType.GEOMETRIC_CENTER:   0.50,
    LocationType.APPROXIMATE:        0.30,
}


def _google_location_type(raw_type: str) -> LocationType:
    mapping = {
        "ROOFTOP":            LocationType.ROOFTOP,
        "RANGE_INTERPOLATED": LocationType.RANGE_INTERPOLATED,
        "GEOMETRIC_CENTER":   LocationType.GEOMETRIC_CENTER,
        "APPROXIMATE":        LocationType.APPROXIMATE,
    }
    return mapping.get(raw_type, LocationType.APPROXIMATE)


def _extract_neighborhood_google(components: list[dict]) -> Optional[str]:
    priority = ["sublocality_level_1", "neighborhood", "sublocality"]
    for target in priority:
        for comp in components:
            if target in comp.get("types", []):
                return comp["long_name"]
    return None


def _to_google_address_format(query: str) -> str:
    """
    Convierte la dirección normalizada interna al formato que Google Geocoding
    reconoce mejor para Colombia.

    "Cl. 16 # 3CE-41"  →  "Calle 16 #3CE-41"
    "Cra. 5 # 12-34"   →  "Carrera 5 #12-34"

    Diferencias clave respecto al formato interno:
      - Palabras completas (Calle/Carrera) en vez de abreviatura (Cl./Cra.)
      - Sin espacio entre # y el número: "#3CE-41" no "# 3CE-41"
    """
    t = query.strip()
    # Abreviaturas → palabras completas
    t = re.sub(r'\bCl\.\s*', 'Calle ', t)
    t = re.sub(r'\bCra\.\s*', 'Carrera ', t)
    t = re.sub(r'\bKr\.\s*', 'Carrera ', t)
    t = re.sub(r'\bAv\.\s*', 'Avenida ', t)
    t = re.sub(r'\bTr\.\s*', 'Transversal ', t)
    t = re.sub(r'\bDiag\.\s*', 'Diagonal ', t)
    # Eliminar espacio entre # y el número: "# 3CE-41" → "#3CE-41"
    t = re.sub(r'#\s+(\d)', r'#\1', t)
    return t.strip()


async def _google_get_candidates(query: str) -> list[GeoCandidate]:
    """
    Retorna todos los resultados de Google dentro del bbox de Popayán.
    Toma results[:6], no solo results[0], para detectar ambigüedad real.

    Convierte la query al formato que Google Geocoding reconoce mejor para
    direcciones colombianas antes de enviar ("Calle 16 #3CE-41" no "Cl. 16 # 3CE-41").
    """
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return []

    google_query = _to_google_address_format(query)
    params = {
        "address": f"{google_query}, Popayán, Cauca, Colombia",
        "key": api_key,
        "language": "es",
        "region": "co",
        "bounds": "2.32,-76.82|2.58,-76.42",
    }

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(_GOOGLE_URL, params=params)

        safe_url = str(resp.url).replace(api_key, "***")
        logger.info(f"[GOOGLE] geocoding query: {google_query!r}")
        logger.debug(f"[GOOGLE] {resp.status_code} — {safe_url}")

        if resp.status_code != 200:
            logger.warning(f"[GOOGLE] HTTP {resp.status_code}")
            return []

        data = resp.json()
        status = data.get("status", "")
        if status not in ("OK",):
            logger.info(f"[GOOGLE] status={status} for {query!r}")
            return []

        candidates = []
        for r in data.get("results", [])[:6]:
            loc  = r["geometry"]["location"]
            lat  = float(loc["lat"])
            lng  = float(loc["lng"])

            if not in_wide_bbox(lat, lng):
                continue

            raw_type  = r["geometry"].get("location_type", "APPROXIMATE")
            loc_type  = _google_location_type(raw_type)
            conf      = _GOOGLE_TYPE_CONFIDENCE.get(loc_type, 0.30)
            if not in_urban_bbox(lat, lng):
                conf = max(0.0, conf - 0.30)

            neighborhood = _extract_neighborhood_google(
                r.get("address_components", [])
            )
            candidates.append(GeoCandidate(
                lat=lat, lng=lng,
                display_name=r.get("formatted_address", query),
                source="google",
                location_type=loc_type,
                confidence=conf,
                neighborhood=neighborhood,
                raw=r,
            ))

        logger.info(f"[GOOGLE] {len(candidates)} candidate(s) for {query!r}")
        return candidates

    except Exception as e:
        logger.error(f"[GOOGLE] error: {e}")
        return []


# ── Google Places Autocomplete + Details (resolución primaria) ─────────────────
#
# Por qué este es el método principal y no Geocoding:
#   El buscador web de Google Maps usa Autocomplete + Place Details, NO la
#   Geocoding API. Para direcciones residenciales de Popayán (ej.
#   "Cl. 16 # 3CE-41, Santa Teresa") Geocoding interpola y devuelve solo el
#   nivel de calle (GEOMETRIC_CENTER), mientras que Autocomplete encuentra la
#   dirección exacta indexada por Google y Place Details da las coordenadas
#   precisas — igual que el buscador web.
#
# Bonus: cuando una misma dirección existe en varios barrios, Autocomplete
# devuelve varias predicciones con distinto `secondary_text` (barrio) →
# alimenta directamente el flujo NEEDS_DISAMBIGUATION.

_GOOGLE_AUTOCOMPLETE_URL = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
_GOOGLE_DETAILS_URL      = "https://maps.googleapis.com/maps/api/place/details/json"

# Tipos de predicción que representan una dirección específica (con número de
# casa), no solo una vía.
_ADDRESS_LEVEL_TYPES = {"subpremise", "premise", "street_address"}

_CITY_LEVEL_NAMES = {"popayán", "popayan", "cauca", "colombia"}


def _pred_barrio(pred: dict) -> Optional[str]:
    """Extrae el barrio del secondary_text de una predicción de Autocomplete."""
    sf = pred.get("structured_formatting", {})
    sec = sf.get("secondary_text", "") or ""
    first = sec.split(",")[0].strip() if sec else ""
    if not first or first.lower() in _CITY_LEVEL_NAMES:
        return None
    return first


def _pred_precision(pred: dict) -> tuple[LocationType, float]:
    """Deriva precisión de los `types` de la predicción."""
    types = set(pred.get("types", []))
    if types & _ADDRESS_LEVEL_TYPES:
        return LocationType.ROOFTOP, 0.92
    return LocationType.GEOMETRIC_CENTER, 0.50


async def _google_autocomplete(query: str) -> list[dict]:
    """Llama Places Autocomplete. Retorna lista de predicciones crudas."""
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return []

    params = {
        "input": query,
        "key": api_key,
        "language": "es",
        "components": "country:co",
        "location": f"{_POPAYAN_CENTER[0]},{_POPAYAN_CENTER[1]}",
        "radius": 20000,
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(_GOOGLE_AUTOCOMPLETE_URL, params=params)
        if resp.status_code != 200:
            logger.warning(f"[AUTOCOMPLETE] HTTP {resp.status_code}")
            return []
        data = resp.json()
        status = data.get("status", "")
        if status not in ("OK", "ZERO_RESULTS"):
            logger.info(f"[AUTOCOMPLETE] status={status} for {query!r}")
        preds = data.get("predictions", [])
        logger.info(f"[AUTOCOMPLETE] {len(preds)} prediction(s) for {query!r}")
        return preds
    except Exception as e:
        logger.error(f"[AUTOCOMPLETE] error: {e}")
        return []


async def _google_place_details(place_id: str, pred: dict) -> Optional[GeoCandidate]:
    """Obtiene coordenadas precisas de un place_id via Place Details."""
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    if not api_key or not place_id:
        return None

    params = {
        "place_id": place_id,
        "key": api_key,
        "language": "es",
        "fields": "formatted_address,geometry,name,address_component",
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(_GOOGLE_DETAILS_URL, params=params)
        if resp.status_code != 200:
            logger.warning(f"[DETAILS] HTTP {resp.status_code}")
            return None
        data = resp.json()
        if data.get("status") != "OK":
            logger.info(f"[DETAILS] status={data.get('status')}")
            return None

        result = data.get("result", {})
        loc = result.get("geometry", {}).get("location", {})
        lat = float(loc.get("lat", 0))
        lng = float(loc.get("lng", 0))
        if lat == 0 and lng == 0:
            return None

        loc_type, conf = _pred_precision(pred)
        if not in_urban_bbox(lat, lng):
            conf = max(0.0, conf - 0.30)

        neighborhood = _extract_neighborhood_google(
            result.get("address_components", [])
        ) or _pred_barrio(pred)

        display = result.get("formatted_address") or pred.get("description", "")
        return GeoCandidate(
            lat=lat, lng=lng,
            display_name=display,
            source="google",
            location_type=loc_type,
            confidence=conf,
            neighborhood=neighborhood,
            raw=result,
        )
    except Exception as e:
        logger.error(f"[DETAILS] error: {e}")
        return None


async def _google_autocomplete_candidates(query: str) -> list[GeoCandidate]:
    """
    Resolución primaria via Autocomplete + Place Details.

    Lógica:
      1. Autocomplete sobre la query.
      2. Filtrar predicciones que coincidan con los números de la query y, si la
         query trae número de casa, que sean nivel-dirección (no solo vía).
      3. Dedup por barrio (una predicción por barrio).
      4. Place Details de cada predicción elegida → coordenadas precisas.

    Retorna 1 candidato (dirección única) o varios (misma dirección en barrios
    distintos → el pipeline desambigua).
    """
    preds = await _google_autocomplete(query)
    if not preds:
        return []

    has_house = "#" in query or bool(re.search(r"\d+\s*-\s*\d+", query))
    qnums = re.findall(r"\d+", query)

    def _num_hits(pred: dict) -> int:
        desc = pred.get("description", "")
        return sum(1 for n in qnums if n in desc)

    matched: list[dict] = []
    for p in preds:
        types = set(p.get("types", []))
        is_addr = bool(types & _ADDRESS_LEVEL_TYPES)
        if has_house and not is_addr:
            continue
        if qnums and _num_hits(p) < min(2, len(qnums)):
            continue
        matched.append(p)

    # Sin coincidencias estrictas: si no había número de casa, aceptar la mejor
    # predicción (la vía). Si había número y nada coincide → ceder a Geocoding.
    if not matched:
        if has_house:
            return []
        matched = preds[:1]

    # Primario = mejor ranking de Google. Alternates reales = predicciones que
    # coinciden con TODOS los números de la query en un barrio distinto (misma
    # dirección en varios barrios → desambiguar). Esto evita gastar Place
    # Details en coincidencias parciales espurias.
    primary = matched[0]
    primary_barrio = (_pred_barrio(primary) or "").lower()
    full = len(qnums)

    chosen: list[dict] = [primary]
    if full:
        seen_barrios = {primary_barrio}
        for p in matched[1:]:
            if _num_hits(p) < full:
                continue
            b = (_pred_barrio(p) or "").lower()
            if not b or b in seen_barrios:
                continue
            seen_barrios.add(b)
            chosen.append(p)
            if len(chosen) >= MAX_CANDIDATES_SHOWN:
                break

    candidates: list[GeoCandidate] = []
    for p in chosen:
        c = await _google_place_details(p.get("place_id", ""), p)
        if c and c.in_wide_bbox():
            candidates.append(c)

    logger.info(
        f"[AUTOCOMPLETE] resolved {len(candidates)} candidate(s) with coords "
        f"for {query!r}"
    )
    return candidates


# ── Google Places Text Search ─────────────────────────────────────────────────

_GOOGLE_PLACES_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

async def _google_places_search(query: str) -> list[GeoCandidate]:
    """
    Fallback a Google Places Text Search cuando Geocoding devuelve GEOMETRIC_CENTER.
    Places API usa los mismos datos que Google Maps web — mejor cobertura de
    direcciones específicas en ciudades colombianas medianas como Popayán.

    La respuesta no incluye location_type, así que inferimos precisión por el
    tamaño del viewport: viewport pequeño → alta precisión.
    """
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return []

    google_query = _to_google_address_format(query)
    params = {
        "query": f"{google_query}, Popayán, Colombia",
        "key": api_key,
        "language": "es",
        "region": "co",
        "locationbias": f"circle:20000@{_POPAYAN_CENTER[0]},{_POPAYAN_CENTER[1]}",
    }

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(_GOOGLE_PLACES_URL, params=params)

        logger.info(f"[PLACES] query: {google_query!r}")
        if resp.status_code != 200:
            logger.warning(f"[PLACES] HTTP {resp.status_code}")
            return []

        data = resp.json()
        status = data.get("status", "")
        if status not in ("OK", "ZERO_RESULTS"):
            logger.info(f"[PLACES] status={status}")
        if status != "OK" or not data.get("results"):
            return []

        candidates = []
        for r in data["results"][:4]:
            loc = r.get("geometry", {}).get("location", {})
            lat = float(loc.get("lat", 0))
            lng = float(loc.get("lng", 0))

            if not in_wide_bbox(lat, lng):
                continue

            # Inferir precisión del viewport
            vp = r.get("geometry", {}).get("viewport", {})
            ne = vp.get("northeast", {})
            sw = vp.get("southwest", {})
            vp_lat = abs(ne.get("lat", 0) - sw.get("lat", 0))
            vp_lng = abs(ne.get("lng", 0) - sw.get("lng", 0))

            if vp_lat < 0.0008 and vp_lng < 0.0008:
                loc_type  = LocationType.ROOFTOP
                confidence = 0.90
            elif vp_lat < 0.002 and vp_lng < 0.002:
                loc_type  = LocationType.RANGE_INTERPOLATED
                confidence = 0.75
            else:
                loc_type  = LocationType.GEOMETRIC_CENTER
                confidence = 0.50

            if not in_urban_bbox(lat, lng):
                confidence = max(0.0, confidence - 0.30)

            # Extraer neighborhood del formatted_address de Places
            formatted = r.get("formatted_address", "")
            neighborhood = None
            parts = [p.strip() for p in formatted.split(",")]
            if len(parts) >= 2:
                # En Popayán: "Cl. 16 #3CE-41, Santa Teresa, Popayán, Cauca, Colombia"
                # parts[1] = "Santa Teresa" → neighborhood
                candidate_n = parts[1] if len(parts) > 1 else None
                if candidate_n and candidate_n.lower() not in {"popayán", "popayan", "cauca", "colombia"}:
                    neighborhood = candidate_n

            candidates.append(GeoCandidate(
                lat=lat, lng=lng,
                display_name=formatted,
                source="google",
                location_type=loc_type,
                confidence=confidence,
                neighborhood=neighborhood,
                raw=r,
            ))

        logger.info(f"[PLACES] {len(candidates)} candidate(s) for {query!r}")
        return candidates

    except Exception as e:
        logger.error(f"[PLACES] error: {e}")
        return []


# ── Nominatim ─────────────────────────────────────────────────────────────────

async def _nominatim_get_candidates(query: str) -> list[GeoCandidate]:
    """
    Fallback a Nominatim. Usa addressdetails=1 para extraer barrio.
    Respeta rate limit de 1 req/seg del servidor público.

    Advertencia de producción: bajo carga concurrente alta, el lock serializa
    las requests y genera latencia significativa. Considerar self-hosted Nominatim
    o eliminar como fallback si el volumen de picos es > 2 req/seg.
    """
    q = f"{query}, Popayan, Cauca, Colombia"
    params = {
        "q": q,
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
        "countrycodes": "co",
        "viewbox": "-76.82,2.58,-76.42,2.32",
        "bounded": 1,
    }
    headers = {
        "User-Agent": "lyra-intellitaxi/1.0 (contact: admin)",
        "Accept": "application/json",
    }

    try:
        result = await _nominatim_request(params, headers, query)
        return result
    except Exception as e:
        logger.error(f"[NOMINATIM] error: {e}")
        return []


async def _nominatim_request(
    params: dict, headers: dict, query: str
) -> list[GeoCandidate]:
    import asyncio

    def _sync_request() -> list[GeoCandidate]:
        global _NOM_LAST_REQ
        candidates = []

        for attempt in range(3):
            with _NOM_LOCK:
                now  = time.monotonic()
                wait = _NOM_LAST_REQ + _NOM_INTERVAL - now
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = httpx.get(_NOMINATIM_URL, params=params,
                                  headers=headers, timeout=5.0)
                finally:
                    _NOM_LAST_REQ = time.monotonic()

            if r.status_code == 200:
                break
            if r.status_code == 429 and attempt < 2:
                time.sleep(min(2.0 ** attempt, 8.0))
                continue
            logger.warning(f"[NOMINATIM] HTTP {r.status_code}")
            return []

        data = r.json()
        if not isinstance(data, list):
            return []

        for row in data:
            lat = float(row.get("lat", 0))
            lng = float(row.get("lon", 0))

            if not in_wide_bbox(lat, lng):
                continue

            importance = float(row.get("importance", 0.3))
            loc_type   = (
                LocationType.NOMINATIM_HIGH if importance >= 0.75
                else LocationType.NOMINATIM_LOW
            )
            conf = min(importance, 0.75) if in_urban_bbox(lat, lng) else max(0.0, min(importance, 0.75) - 0.3)

            addr = row.get("address", {})
            neighborhood = (
                addr.get("suburb")
                or addr.get("neighbourhood")
                or addr.get("quarter")
            )

            candidates.append(GeoCandidate(
                lat=lat, lng=lng,
                display_name=row.get("display_name", query),
                source="nominatim",
                location_type=loc_type,
                confidence=conf,
                neighborhood=neighborhood,
                raw=row,
            ))

        logger.info(f"[NOMINATIM] {len(candidates)} candidate(s) for {query!r}")
        return candidates

    return await asyncio.to_thread(_sync_request)


# ── Verificación post-resolución ──────────────────────────────────────────────

def _cache_worthy(candidate: "GeoCandidate", query: str) -> bool:
    """
    Solo cachear resultados que tengan relación semántica con la query.
    Previene almacenar geocodificaciones espurias (ej. "Valle del Hostiga" →
    "manzana 23#2a28, Popayán" que no tiene nada que ver).

    Para queries con número de casa: usa _result_matches_query.
    Para queries de texto puro (barrios, landmarks):
      al menos una palabra significativa de la query debe estar en el display_name.
    Resultados de Autocomplete (alta confianza) siempre se cachean.
    """
    # Autocomplete results are verified matches
    if candidate.location_type in (LocationType.ROOFTOP, LocationType.RANGE_INTERPOLATED):
        return True

    qnums = re.findall(r"\d+", query)
    if qnums:
        return _result_matches_query(candidate.display_name, query)

    # Text-only query: at least one meaningful word must appear in display_name
    stop = {"para", "desde", "hacia", "cerca", "por", "barrio", "sector"}
    words = [w.lower() for w in re.findall(r"[a-záéíóúñ]{4,}", query, re.IGNORECASE)
             if w.lower() not in stop]
    if not words:
        return True
    d_lower = candidate.display_name.lower()
    return any(w in d_lower for w in words)


def _result_matches_query(formatted_address: str, query_base: str) -> bool:
    """
    Verifica que el resultado de Google corresponde a la dirección del usuario,
    no al alias/landmark que se usó como enriquecimiento.

    Extrae los números del query_base y verifica que estén en formatted_address.
    Previene el caso donde Google resuelve "D1" (un supermercado) en lugar
    de "Cl. 16 # 3CE-41 cerca del D1".
    """
    numbers = re.findall(r'\d+', query_base)
    if not numbers:
        return True  # sin números → no se puede verificar → asumir válido
    addr_lower = formatted_address.lower()
    # Al menos 2 de los números deben aparecer en el resultado
    matches = sum(1 for n in numbers if n in addr_lower)
    return matches >= min(2, len(numbers))


# ── Decisión: auto-aceptar o solicitar contexto ───────────────────────────────

def _pick_best(candidates: list[GeoCandidate]) -> Optional[GeoCandidate]:
    """Retorna el candidato con mayor confidence dentro de urban bbox."""
    urban = [c for c in candidates if c.in_urban_bbox()]
    pool  = urban if urban else [c for c in candidates if c.in_wide_bbox()]
    if not pool:
        return None
    return max(pool, key=lambda c: c.confidence)


def _build_context_question(reason: str = "general") -> str:
    if reason == "address_not_found":
        return "¿En qué barrio o sector queda esa dirección?"
    return "¿En qué barrio o referencia cercana queda la dirección?"


def _build_house_number_question() -> str:
    """
    Pregunta específica para precisión de dirección: el geocoder devolvió el
    centro de la vía (sin número de casa). Pedimos número de casa o un punto de
    referencia cercano para re-consultar con mayor precisión (item 7 audit).
    """
    return (
        "Encontré la calle pero no el punto exacto. "
        "¿Cuál es el número de la casa o un punto de referencia cercano?"
    )


# Precisiones que NUNCA se auto-aceptan: el geocoder devolvió el centro de la
# vía / un nivel aproximado, sin precisión de número de casa. Siempre se pide
# número de casa o landmark y se re-consulta (item 7 audit: "Auto-aceptación de
# Direcciones Incompletas"). Decisión de producto 2026-06-20: incluye
# GEOMETRIC_CENTER + APPROXIMATE + NOMINATIM_LOW.
_NEVER_AUTOACCEPT = {
    LocationType.GEOMETRIC_CENTER,
    LocationType.APPROXIMATE,
    LocationType.NOMINATIM_LOW,
}


def _build_disambiguation_question(candidates: list[GeoCandidate]) -> str:
    """
    Construye pregunta SOLO con datos reales de los candidatos.
    Solo se llama cuando hay 2+ candidatos con neighborhoods distintos.
    """
    neighborhoods = []
    seen = set()
    for c in candidates[:MAX_CANDIDATES_SHOWN]:
        n = c.neighborhood or _short_display(c.display_name)
        if n and n.lower() not in seen:
            neighborhoods.append(n)
            seen.add(n.lower())

    if not neighborhoods:
        return _build_context_question()

    if len(neighborhoods) == 1:
        return f"¿La dirección queda en {neighborhoods[0]}?"

    if len(neighborhoods) == 2:
        return (
            f"Encontré esa dirección en {neighborhoods[0]} "
            f"y {neighborhoods[1]}. ¿Cuál corresponde a su ubicación?"
        )

    options = ", ".join(neighborhoods[:-1]) + f" o {neighborhoods[-1]}"
    return f"Encontré esa dirección en {options}. ¿Cuál es su ubicación?"


def _short_display(display_name: str) -> str:
    """Extrae la parte más útil del display_name completo."""
    parts = [p.strip() for p in display_name.split(",")]
    # Descartar "Colombia", "Cauca", "Popayán" al final
    filtered = [p for p in parts if p.lower() not in
                {"colombia", "cauca", "popayán", "popayan"}]
    return filtered[0] if filtered else display_name[:50]


# ── Pipeline principal ────────────────────────────────────────────────────────

async def run_pipeline(query: str, attempt: int = 1) -> GeoResolution:
    """
    Pipeline completo: Cache → Google → Nominatim → decisión.

    Retorna GeoResolution con status:
      resolved             → coordenadas aceptadas
      context_gathering    → preguntar barrio/referencia al usuario
      needs_disambiguation → candidatos reales para elegir (Fase 1: raro)
      failed               → inresolvable
    """
    from core.address_utils import normalize_colombian_address, _strip_preamble

    # 1. Normalizar
    normalized = normalize_colombian_address(_strip_preamble(query))
    if len(normalized) < 3:
        return GeoResolution(
            status=ResolutionStatus.FAILED,
            query=query,
            attempt=attempt,
        )

    # 2. Cache en memoria
    cached = _mem_get(normalized)
    if cached:
        logger.info(f"[PIPELINE] memory cache hit: {normalized!r}")
        return GeoResolution(
            status=ResolutionStatus.RESOLVED,
            query=normalized,
            attempt=attempt,
            selected=cached,
        )

    # 3. Cache en DB
    db_cached = _db_get(normalized)
    if db_cached:
        logger.info(f"[PIPELINE] db cache hit: {normalized!r}")
        _mem_set(normalized, db_cached)
        return GeoResolution(
            status=ResolutionStatus.RESOLVED,
            query=normalized,
            attempt=attempt,
            selected=db_cached,
        )

    # 4. Resolución primaria: Autocomplete + Place Details (igual que el
    #    buscador web de Google Maps — encuentra la dirección exacta indexada).
    candidates = await _google_autocomplete_candidates(normalized)

    # 4b. Fallback a Geocoding API si Autocomplete no resolvió.
    if not candidates:
        candidates = await _google_get_candidates(normalized)

        # 4c. Si Geocoding solo da GEOMETRIC_CENTER → intentar Places Text Search.
        has_good_google = any(
            c.location_type in (LocationType.ROOFTOP, LocationType.RANGE_INTERPOLATED)
            for c in candidates
        )
        if candidates and not has_good_google:
            places_candidates = await _google_places_search(normalized)
            if places_candidates:
                for pc in places_candidates:
                    logger.info(
                        f"[PLACES_CANDIDATE] {pc.display_name!r} "
                        f"[{pc.location_type.value}] conf={pc.confidence:.2f} "
                        f"({pc.lat:.5f},{pc.lng:.5f})"
                    )

                # Prioridad 1: Places encontró la dirección ESPECÍFICA.
                for pc in places_candidates:
                    if in_wide_bbox(pc.lat, pc.lng) and _result_matches_query(pc.display_name, normalized):
                        logger.info(f"[PLACES] specific address match: {pc.display_name!r}")
                        candidates = [pc]
                        break
                else:
                    # Prioridad 2: Places mejor confidence que Geocoding.
                    places_in_bbox = [p for p in places_candidates if in_wide_bbox(p.lat, p.lng)]
                    if places_in_bbox:
                        places_best  = max(places_in_bbox, key=lambda c: c.confidence)
                        geocode_best = max(candidates,     key=lambda c: c.confidence)
                        if places_best.confidence > geocode_best.confidence:
                            logger.info(
                                f"[PLACES] using higher-confidence result: "
                                f"{places_best.display_name!r} [{places_best.location_type.value}]"
                            )
                            candidates = places_in_bbox

    # 5. Nominatim si Google (Autocomplete + Geocoding + Places) vacío.
    if not candidates:
        candidates = await _nominatim_get_candidates(normalized)

    # 6. Sin resultados
    if not candidates:
        logger.info(f"[PIPELINE] no results for: {normalized!r}")
        return GeoResolution(
            status=ResolutionStatus.CONTEXT_GATHERING,
            query=normalized,
            attempt=attempt,
            disambiguation_question=_build_context_question(),
        )

    # 7. Filtrar candidatos dentro del bbox
    in_bbox = [c for c in candidates if c.in_urban_bbox()] \
           or [c for c in candidates if c.in_wide_bbox()]

    if not in_bbox:
        logger.info(f"[PIPELINE] all results out of bbox for: {normalized!r}")
        return GeoResolution(
            status=ResolutionStatus.CONTEXT_GATHERING,
            query=normalized,
            attempt=attempt,
            disambiguation_question=_build_context_question(),
        )

    # 8. Si hay 2+ resultados con barrios distintos → desambiguar
    #    (comportamiento exacto que el usuario pidió: preguntar entre opciones reales)
    neighborhoods = [
        c.neighborhood for c in in_bbox
        if c.neighborhood
    ]
    unique_neighborhoods = list(dict.fromkeys(neighborhoods))  # preservar orden, dedup

    if len(unique_neighborhoods) >= 2:
        logger.info(
            f"[PIPELINE] {len(unique_neighborhoods)} neighborhoods for: {normalized!r} "
            f"→ NEEDS_DISAMBIGUATION"
        )
        return GeoResolution(
            status=ResolutionStatus.NEEDS_DISAMBIGUATION,
            query=normalized,
            attempt=attempt,
            candidates=in_bbox,
            disambiguation_question=_build_disambiguation_question(in_bbox),
        )

    # 9. 1 resultado (o múltiples en mismo barrio) → decidir por precisión.
    #
    # Regla de auto-aceptación (ver arquitectura):
    #   ROOFTOP / RANGE_INTERPOLATED + urban_bbox → RESOLVED
    #   GEOMETRIC_CENTER / APPROXIMATE / Nominatim → solo si los números de la
    #   query aparecen en el resultado (sin mismatch). Si hay mismatch y baja
    #   precisión, el geocoder devolvió la calle/sector en vez de la dirección
    #   específica → pedir contexto al usuario.
    _HIGH_PRECISION = {LocationType.ROOFTOP, LocationType.RANGE_INTERPOLATED}

    best = _pick_best(in_bbox)
    if best:
        result_matches = _result_matches_query(best.display_name, normalized)

        # Regla dura (item 7 audit): GEOMETRIC_CENTER / APPROXIMATE /
        # NOMINATIM_LOW NUNCA se auto-aceptan, coincida o no el número textual.
        # El geocoder devolvió el centro de la vía o un nivel aproximado → falta
        # precisión de número de casa. Siempre pedir número de casa o landmark y
        # re-consultar: handle_user_context re-ejecuta el pipeline con el dato
        # adicional (attempt + 1), y si Google/Places ahora devuelve
        # ROOFTOP/RANGE_INTERPOLATED se acepta. El tope MAX_PIPELINE_ATTEMPTS
        # corta el ciclo si nunca mejora.
        if best.location_type in _NEVER_AUTOACCEPT:
            # Intentos agotados y sigue siendo baja precisión: NUNCA caer a
            # aceptar el resultado solo porque se acabaron los reintentos (eso
            # reabriría el bug item 7 en el caso límite). Se devuelve FAILED y el
            # caller dispara su fallback seguro. El tope se centraliza aquí para
            # que aplique a TODOS los callers (Twilio vía handle_user_context y
            # FreeSWITCH que llama run_pipeline directamente).
            if attempt >= MAX_PIPELINE_ATTEMPTS:
                logger.warning(
                    f"[PIPELINE] {best.location_type.value} still low-precision "
                    f"after {attempt} attempts: {best.display_name!r} for "
                    f"{normalized!r} → FAILED (no silent low-precision accept)"
                )
                return GeoResolution(
                    status=ResolutionStatus.FAILED,
                    query=normalized,
                    attempt=attempt,
                    candidates=in_bbox,
                )
            logger.warning(
                f"[PIPELINE] {best.location_type.value} never auto-accepted "
                f"(attempt {attempt}): {best.display_name!r} for {normalized!r} "
                f"→ CONTEXT_GATHERING (need house number/landmark)"
            )
            return GeoResolution(
                status=ResolutionStatus.CONTEXT_GATHERING,
                query=normalized,
                attempt=attempt,
                candidates=in_bbox,
                disambiguation_question=_build_house_number_question(),
            )

        # Mismatch de baja precisión SOLO en el primer intento → pedir barrio.
        # Google para direcciones residenciales de Popayán raramente alcanza
        # precisión de número de casa: el techo real es la calle a nivel
        # GEOMETRIC_CENTER (p.ej. "Cl. 16, Santa Teresa"). Por eso solo pedimos
        # contexto UNA vez; tras enriquecer con el barrio (attempt >= 2)
        # aceptamos el mejor resultado disponible — pedir de nuevo solo frustra
        # al usuario y no mejora el resultado.
        if (
            attempt == 1
            and not result_matches
            and best.location_type not in _HIGH_PRECISION
        ):
            logger.warning(
                f"[PIPELINE] mismatch + low precision → CONTEXT_GATHERING: "
                f"{best.display_name!r} for {normalized!r}"
            )
            return GeoResolution(
                status=ResolutionStatus.CONTEXT_GATHERING,
                query=normalized,
                attempt=attempt,
                disambiguation_question=_build_context_question("address_not_found"),
            )

        if not result_matches:
            # Aceptamos: o bien alta precisión con formato distinto, o ya
            # enriquecimos con el barrio y Google solo da nivel de calle.
            # Las coordenadas son del nivel de calle correcto — el conductor
            # recibe además el texto completo de la dirección.
            logger.warning(
                f"[PIPELINE] accepted street-level result (attempt {attempt}): "
                f"{best.display_name!r} for {normalized!r}"
            )

        logger.info(
            f"[PIPELINE] resolved: {normalized!r} → "
            f"({best.lat:.5f},{best.lng:.5f}) [{best.location_type.value}]"
        )
        if _cache_worthy(best, normalized):
            _mem_set(normalized, best)
            _db_set(normalized, best)
        else:
            logger.warning(
                f"[PIPELINE] skipped cache (display_name unrelated to query): "
                f"{best.display_name!r} for {normalized!r}"
            )
        return GeoResolution(
            status=ResolutionStatus.RESOLVED,
            query=normalized,
            attempt=attempt,
            candidates=in_bbox,
            selected=best,
        )

    # Fallback (no debería llegar aquí)
    return GeoResolution(
        status=ResolutionStatus.CONTEXT_GATHERING,
        query=normalized,
        attempt=attempt,
        disambiguation_question=_build_context_question(),
    )


def _extract_geo_context(text: str) -> str:
    """
    Extrae el término geográfico útil del texto libre del usuario para enrichment.

    Problema que resuelve: el usuario dice "Por aquí por el Berlín frente del
    polideportivo" y no debemos enviar esa frase completa a Google — solo "Berlín".

    Ejemplos:
      "Por aquí por el Berlín frente del polideportivo" → "Berlín"
      "barrio Santa Teresa"                             → "Santa Teresa"
      "sector norte cerca del D1"                       → "norte"
      "Santa Teresa"                                    → "Santa Teresa"
    """
    if not text:
        return ""

    t = text.strip()

    # Paso 0: eliminar coletillas de frustración/repetición ("ya te dije",
    # "ya dije", "como te dije") que el usuario añade cuando se le pregunta
    # de nuevo — no son parte del nombre del lugar.
    t = re.sub(
        r'\b(?:ya\s+)?(?:te\s+|le\s+)?dij[eo]\b.*$',
        '',
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(
        r'\b(?:como\s+(?:ya\s+)?(?:te\s+|le\s+)?dij[eo])\b.*$',
        '',
        t,
        flags=re.IGNORECASE,
    ).strip()
    if len(t) < 2:
        t = text.strip()

    # Paso 1: eliminar preposiciones y frases de ubicación al inicio (iterativo)
    location_preambles = [
        r'^(?:estoy\s+)?(?:por\s+aquí|por\s+acá)\s+(?:por\s+(?:el?\s+|la\s+))?',
        r'^(?:aquí\s+en|acá\s+en)\s+',
        r'^(?:estoy\s+en|me\s+encuentro\s+en|quedo\s+en|estamos\s+en)\s+',
        r'^(?:cerca\s+de[l]?|por\s+el?\s+|por\s+la\s+)\s*',
        r'^barrio\s+(?:el?\s+|la\s+)?',
        r'^sector\s+(?:el?\s+|la\s+)?',
        r'^zona\s+(?:el?\s+|la\s+)?',
        r'^en\s+el?\s+',
        r'^en\s+la\s+',
    ]
    changed = True
    while changed:
        changed = False
        for pat in location_preambles:
            new_t = re.sub(pat, '', t, flags=re.IGNORECASE).strip()
            if new_t != t and len(new_t) >= 2:
                t = new_t
                changed = True

    # Paso 2: cortar al llegar al primer marcador relacional
    # "berlín frente del polideportivo" → "berlín"
    relational_cut = [
        r'\s+frente\s+(?:a[l]?|de[l]?)\b.*$',
        r'\s+cerca\s+(?:a[l]?|de[l]?)\b.*$',
        r'\s+al\s+lado\s+de[l]?\b.*$',
        r'\s+junto\s+a[l]?\b.*$',
        r'\s+detrás\s+de[l]?\b.*$',
        r'\s+diagonal\s+a[l]?\b.*$',
        r'\s+a\s+(?:una\s+)?cuadra\b.*$',
    ]
    for pat in relational_cut:
        new_t = re.sub(pat, '', t, flags=re.IGNORECASE).strip()
        if len(new_t) >= 2:
            t = new_t

    # Paso 3: si todavía quedan > 4 palabras, tomar solo las primeras 3 significativas
    words = t.split()
    if len(words) > 4:
        meaningful = [w for w in words if len(w) > 2]
        if meaningful:
            t = ' '.join(meaningful[:3])

    # Paso 4: limpiar puntuación trailing
    t = re.sub(r'[.,;:!?]+$', '', t).strip()

    result = t if len(t) >= 2 else text[:80].strip()
    logger.debug(f"[GEO_CTX] {text!r} → {result!r}")
    return result


async def handle_user_context(
    user_text: str,
    pending: GeoResolution,
    original_query: str,
    attempt: int,
) -> GeoResolution:
    """
    Procesa respuesta del usuario a CONTEXT_GATHERING o NEEDS_DISAMBIGUATION.

    El texto del usuario se limpia con _extract_geo_context() antes de usarlo
    como enrichment — evita enviar frases relacionales ("frente del polideportivo")
    a Google, que las interpreta como landmarks y devuelve la dirección incorrecta.
    """
    from core.address_utils import _strip_preamble

    if attempt >= MAX_PIPELINE_ATTEMPTS:
        return GeoResolution(
            status=ResolutionStatus.FAILED,
            query=original_query,
            attempt=attempt,
        )

    # Si hay candidatos conocidos, intentar mapear respuesta a uno de ellos
    if pending.candidates:
        matched = _match_user_to_candidate(user_text, pending.candidates)
        if matched and matched.auto_acceptable():
            _mem_set(original_query, matched)
            _db_set(original_query, matched)
            return GeoResolution(
                status=ResolutionStatus.RESOLVED,
                query=original_query,
                attempt=attempt,
                selected=matched,
            )

    # Extraer solo el término geográfico útil del texto del usuario
    raw_clarification = _strip_preamble(user_text).strip()
    if not raw_clarification or len(raw_clarification) < 2:
        return GeoResolution(
            status=ResolutionStatus.CONTEXT_GATHERING,
            query=original_query,
            attempt=attempt,
            disambiguation_question=_build_context_question(),
        )

    # "no sé" → no se puede enriquecer
    no_info_phrases = {"no sé", "no se", "no lo sé", "no sé bien", "nose"}
    if raw_clarification.lower() in no_info_phrases:
        if attempt + 1 >= MAX_PIPELINE_ATTEMPTS:
            return GeoResolution(
                status=ResolutionStatus.FAILED,
                query=original_query,
                attempt=attempt,
            )
        return GeoResolution(
            status=ResolutionStatus.CONTEXT_GATHERING,
            query=original_query,
            attempt=attempt + 1,
            disambiguation_question=(
                "¿Podría indicar el nombre del barrio o una calle cercana?"
            ),
        )

    # Limpiar: extraer solo el nombre del lugar, sin frases relacionales
    geo_context = _extract_geo_context(raw_clarification)
    enriched = f"{original_query}, {geo_context}"
    logger.info(
        f"[PIPELINE] enriched: {enriched!r} "
        f"(raw={raw_clarification!r}, attempt {attempt + 1})"
    )
    return await run_pipeline(enriched, attempt=attempt + 1)


def _match_user_to_candidate(
    user_text: str,
    candidates: list[GeoCandidate],
) -> Optional[GeoCandidate]:
    """
    Intenta mapear la respuesta del usuario a uno de los candidatos conocidos.
    Compara contra neighborhood y fragmentos del display_name.
    """
    t = user_text.lower().strip()

    ordinals = {
        "primero": 0, "primera": 0, "1": 0,
        "segundo": 1, "segunda": 1, "2": 1,
        "tercero": 2, "tercera": 2, "3": 2,
        "cuarto":  3, "cuarta":  3, "4": 3,
    }
    for word, idx in ordinals.items():
        if word in t and idx < len(candidates):
            return candidates[idx]

    for c in candidates:
        if c.neighborhood and c.neighborhood.lower() in t:
            return c
        parts = [p.strip().lower() for p in c.display_name.split(",")]
        for part in parts:
            if len(part) > 4 and part in t:
                return c

    return None


# ── Función pública simplificada (compatibilidad) ─────────────────────────────

async def geocode(
    query: str,
    barrio: Optional[str] = None,
) -> Optional[tuple[float, float, str]]:
    """
    Shortcut para resolución directa (sin CONTEXT_GATHERING interactivo).
    Retorna (lat, lng, display_name) o None si no resuelve con ROOFTOP/RANGE_INTERPOLATED.

    barrio: contexto adicional conocido (ej: ya confirmado por el usuario).
    Se concatena a la query para mejorar precisión de Google en nomenclaturas colombianas.
    """
    enriched = query
    if barrio and barrio.strip() and barrio.lower() not in query.lower():
        enriched = f"{query}, {barrio.strip()}"

    result = await run_pipeline(enriched)
    if result.resolved and result.selected:
        c = result.selected
        return (c.lat, c.lng, c.display_name)
    return None
