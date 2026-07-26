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

POPAYAN_MIN_LAT, POPAYAN_MAX_LAT = 2.32, 2.58
POPAYAN_MIN_LNG, POPAYAN_MAX_LNG = -76.82, -76.42

_GEOCODE_CACHE: OrderedDict = OrderedDict()
_GEOCODE_CACHE_LOCK = threading.Lock()
_NOMINATIM_LOCK     = threading.Lock()
_NOMINATIM_LAST_REQ = 0.0
GEOCODE_MIN_INTERVAL = 1.1


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


def _nominatim_reverse_geocode_raw(lat: float, lng: float) -> Optional[str]:
    """Reverse geocode via Nominatim."""
    global _NOMINATIM_LAST_REQ

    cache_key = f"rev_geo_{lat}_{lng}"
    cached = _geocode_cache_get(cache_key)
    if cached is not None:
        return cached

    params  = {"lat": lat, "lon": lng, "format": "json", "addressdetails": 0}
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
            return None

        data = r.json()
        if "display_name" in data:
            name = str(data["display_name"])
            name_short = (
                name.replace(", Popayán, Cauca, Colombia", "")
                    .replace(", Centro", "")
                    .strip(", ")
            )
            _geocode_cache_set(cache_key, name_short)
            return name_short
        return None

    except Exception as exc:
        logger.error(f"Reverse geocode error: {exc}")
        return None


async def _nominatim_reverse_geocode_async(lat: float, lng: float) -> Optional[str]:
    import asyncio
    return await asyncio.to_thread(_nominatim_reverse_geocode_raw, lat, lng)


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

def normalize_colombian_address(address: str) -> str:
    """Forma canónica abreviada colombiana (`Cra. 52 #3C-6`).

    Wrapper de compatibilidad: delega en la ÚNICA autoridad de direcciones,
    `core.co_address_parser.parse_co_address` (spec
    docs/superpowers/specs/colombian_address_parser_design.md §7). No contiene
    lógica de reconstrucción propia. Para vía/intersección devuelve la canónica;
    para no-dirección devuelve el texto original (identidad).
    """
    if not address:
        return ""
    from core.co_address_parser import parse_co_address
    return parse_co_address(address).canonical or address.strip()


def normalize_address(address: str) -> str:
    """Render en palabras completas (`Carrera 5 #12-34`) para la ruta legacy de
    WhatsApp/Nominatim. Delega en el mismo parser (misma autoridad, otro estilo
    de render; spec §7.1). Sin lógica de parsing propia."""
    if not address:
        return ""
    from core.co_address_parser import parse_co_address, render_full
    parsed = parse_co_address(address)
    full = render_full(parsed)
    return full or address.strip()


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

    Implementación (spec §7.2): CONSUMIDOR del parser, no reconstructor. Compara
    los `components` de la dirección original vs. la extraída; si el original tiene
    una placa (`distancia`) que la extraída perdió, devuelve la canónica del
    original. La detección por componente también recupera el caso "número de casa
    suelto al final" (audit §3b) que la vieja sonda por '#' no veía.

    Returns:
        El candidato enriquecido, o el `extracted` original si no hay placa que
        proteger o si el candidato ya la conserva.
    """
    if not original_user_text or not extracted:
        return extracted

    from core.co_address_parser import parse_co_address

    raw_p = parse_co_address(original_user_text)
    ext_p = parse_co_address(extracted)

    raw_has_placa = raw_p.components.get("distancia") is not None
    ext_has_placa = ext_p.components.get("distancia") is not None

    if raw_has_placa and not ext_has_placa and raw_p.canonical:
        logger.info(
            "[ADDR_RECOVER] extracted %r dropped house number; recovering %r",
            extracted, raw_p.canonical,
        )
        return raw_p.canonical
    return extracted


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
