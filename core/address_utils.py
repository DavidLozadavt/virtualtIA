# core/address_utils.py
"""
Hardened 'Fast Brain' utilities for address extraction, speech correction, and preamble stripping.
Consolidates logic from Twilio and WhatsApp routers to ensure deterministic, low-latency performance.
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

# ── CONSTANTS ────────────────────────────────────────────────────────────────

_FILLER_WORDS = {
    "me regala", "me daría", "necesito", "por favor", "un taxi", "un móvil",
    "un carro", "para", "en", "desde", "estoy en", "me encuentro en",
    "sería para", "quiero", "podría", "amiga", "amigo", "mija", "mijo",
    "tío", "tio", "tía", "tia", "vecina", "vecino", "hola", "buenas", "buenos días", "buenas tardes",
    "buenas noches", "qué hubo", "qhubo", "mira", "vea", "un favor",
}

_PREAMBLE_PATTERNS = [
    # Greetings — LONGER alternatives first so 'buenas noches' beats 'buenas'
    r'^(?:hola\s+)?(?:buenas\s+noches|buenas\s+tardes|buen\s+día|buenas|hola|qhubo|qué\s+hubo|amiga|amigo|vecina|vecino|mija|mijo|señor|señora),?\s*',
    r'^(?:por favor|un favor|oiga|mire|oye|disculpe|disculpa),?\s*',
    r'^(?:me\s+regala|me\s+daría|necesito|quiero|podría\s+solicitar|pídame|pídeme|solicito)\s*(?:un|el)?\s*(?:taxi|móvil|movil|carro|servicio|carrito)\s*',
    r'^(?:por\s+aquí\s+en|aquí\s+en|acá\s+en|estoy\s+en|me\s+encuentro\s+en|ubicad[ao]\s+en|estamos\s+en|en|desde)\s*',
    r'^(?:sería\s+para|es\s+para|para)\s*',
    # Trailing filler after address
    r'\s+(?:por\s+favor|porfavor|porfa|gracias|please)\.?\s*$',
]

_SPEECH_CORRECTIONS: Dict[str, str] = {
    # ── "los sauces" misheard as: ──
    "entonces": "los sauces",
    "en sauce": "los sauces",
    "en sauces": "los sauces",
    "lo sauce": "los sauces",
    "las sauces": "los sauces",
    "ensauces": "los sauces",
    "el sauce": "los sauces",
    "los sauce": "los sauces",
    "lo sauces": "los sauces",
    # ── "valle del ortigal" misheard as: ──
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
    # ── "maría oriente" misheard as: ──
    "mari oriente": "maría oriente",
    "maria de oriente": "maría oriente",
    "maria oriente": "maría oriente",
    "maría de oriente": "maría oriente",
    "la maría oriente": "maría oriente",
    # ── "maría occidente" misheard as: ──
    "maria occidente": "maría occidente",
    "mari occidente": "maría occidente",
    "maría de occidente": "maría occidente",
    # ── "la esmeralda" misheard as: ──
    "la esmerada": "la esmeralda",
    "esmerada": "la esmeralda",
    "esmeranda": "la esmeralda",
    "la esmeralda se": "la esmeralda",
    # ── "pandiguando" misheard as: ──
    "pandi cuando": "pandiguando",
    "pan de cuando": "pandiguando",
    "pandi guando": "pandiguando",
    "pandiguandos": "pandiguando",
    # ── "yanaconas" misheard as: ──
    "yanaco más": "yanaconas",
    "yanacona": "yanaconas",
    "jana con as": "yanaconas",
    "janacona": "yanaconas",
    "yanacones": "yanaconas",
    # ── "campanario" misheard as: ──
    "campana río": "campanario",
    "campana rio": "campanario",
    "el campana rio": "campanario",
    "el campanarios": "campanario",
    # ── "belalcázar" misheard as: ──
    "bella alcázar": "belalcázar",
    "bella alcazar": "belalcázar",
    "belal cázar": "belalcázar",
    "belga azar": "belalcázar",
    # ── "los comuneros" misheard as: ──
    "lo comunero": "los comuneros",
    "lo comuneros": "los comuneros",
    "los comunero": "los comuneros",
    # ── "alfonso lópez" misheard as: ──
    "alfonzo lópez": "alfonso lópez",
    "alfonzo lopez": "alfonso lópez",
    "alfonso lope": "alfonso lópez",
    "alfonso lópe": "alfonso lópez",
    # ── "pueblillo" misheard as: ──
    "pueblo illo": "pueblillo",
    "pueblito": "pueblillo",
    "pueblo ijo": "pueblillo",
    # ── "yambitará" misheard as: ──
    "jambitará": "yambitará",
    "jambitara": "yambitará",
    "jan bitara": "yambitará",
    # ── "loma de la virgen" misheard as: ──
    "forma de la virgen": "loma de la virgen",
    "roma de la virgen": "loma de la virgen",
    "loma la virgen": "loma de la virgen",
    # ── "terminal" misheard as: ──
    "la terminal": "terminal",
    "el terminal": "terminal",
    # ── "lomas de granada" misheard as: ──
    "loma de granada": "lomas de granada",
    "lomas granada": "lomas de granada",
    # ── "la sombrilla" misheard as: ──
    "la sombilla": "la sombrilla",
    "la sombrija": "la sombrilla",
    # ── "cinco de abril" misheard as: ──
    "5 de abril": "cinco de abril",
    "sinco de abril": "cinco de abril",
    # ── "la pamba" misheard as: ──
    "la pampa": "la pamba",
    "la bamba": "la pamba",
    # ── "parque caldas" misheard as: ──
    "parque calda": "parque caldas",
    "parque de caldas": "parque caldas",
    # ── "valpaíso" / "valparaíso" ──
    "balparaíso": "valparaíso",
    "valpa raíso": "valparaíso",
    "valpariso": "valparaíso",
    # ── "primero de mayo" ──
    "1 de mayo": "primero de mayo",
    "primer de mayo": "primero de mayo",
    # ── Various ──
    "kennedy": "kennedy",  # ensure it doesn't get corrected
    "retiro al sol": "retiro alto",
    "santa en elena": "santa helena",
    "la campiñas": "la campiña",
    "el triunfos": "el triunfo",
    "la florida": "la florida",
}

# ── GEOCODING (Nominatim) ────────────────────────────────────────────────────
GEOCODE_SUFFIX = "Popayán, Cauca, Colombia"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_COUNTRYCODES = "co"
GEOCODE_VIEWBOX = "-76.82,2.58,-76.42,2.32"
GEOCODE_USER_AGENT = "lyra-intellitaxi/1.0 (contact: admin)"

POPAYAN_MIN_LAT, POPAYAN_MAX_LAT = 2.32, 2.58
POPAYAN_MIN_LNG, POPAYAN_MAX_LNG = -76.82, -76.42

_GEOCODE_CACHE: OrderedDict = OrderedDict()
_GEOCODE_CACHE_LOCK = threading.Lock()
_NOMINATIM_LOCK = threading.Lock()
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
    """Geocode a query via Nominatim only (no fallback)."""
    global _NOMINATIM_LAST_REQ
    q = (query or "").strip()
    if not q: return None

    if GEOCODE_SUFFIX and GEOCODE_SUFFIX.lower() not in q.lower():
        q = f"{q}, {GEOCODE_SUFFIX}"

    cached = _geocode_cache_get(q)
    if cached is not None: return cached

    params = {
        "q": q, "format": "json", "limit": 8, "addressdetails": 0,
        "countrycodes": GEOCODE_COUNTRYCODES, "viewbox": GEOCODE_VIEWBOX, "bounded": "1",
    }
    headers = {"User-Agent": GEOCODE_USER_AGENT, "Accept": "application/json"}

    try:
        for attempt in range(3):
            with _NOMINATIM_LOCK:
                now = time.monotonic()
                wait = _NOMINATIM_LAST_REQ + GEOCODE_MIN_INTERVAL - now
                if wait > 0: time.sleep(wait)
                try:
                    r = httpx.get(NOMINATIM_URL, params=params, headers=headers, timeout=5.0)
                finally:
                    _NOMINATIM_LAST_REQ = time.monotonic()

            if r.status_code == 200: break
            if r.status_code == 429 and attempt < 2:
                time.sleep(min(2.0 ** attempt, 10.0))
                continue
            return None

        data = r.json()
        if not isinstance(data, list) or not data: return None

        for row in data:
            lat, lon = float(row.get("lat", 0)), float(row.get("lon", 0))
            if _in_popayan_bbox(lat, lon):
                name = str(row.get("display_name", ""))
                result = (lat, lon, name)
                _geocode_cache_set(q, result)
                return result
        return None
    except Exception as exc:
        logger.error(f"Geocode error: {exc}")
        return None

def _nominatim_geocode(query: str) -> Optional[Tuple[float, float, str]]:
    """Geocode with local fallback (Local first, then Nominatim)."""
    q = (query or "").strip()
    if not q: return None
    
    # 1. Local geodata fallback (Fast & accurate for Popayán)
    try:
        from tools.popayan_geodata import geocode_local
        local = geocode_local(q)
        if local:
            _geocode_cache_set(q, local)
            return local
    except (ImportError, Exception) as e:
        logger.warning(f"Local geodata not available or error: {e}")

    # 2. Nominatim API (Fallback for unknown places)
    result = _nominatim_geocode_raw(q)
    if result: return result

    return None


async def _nominatim_geocode_async(query: str) -> Optional[Tuple[float, float, str]]:
    """Non-blocking geocode wrapper. Runs the sync geocoder in a thread pool
    so it doesn't block Uvicorn's event loop with time.sleep() and httpx.get()."""
    import asyncio
    return await asyncio.to_thread(_nominatim_geocode, query)

def _nominatim_reverse_geocode_raw(lat: float, lng: float) -> Optional[str]:
    """Reverse geocode coordinates via Nominatim."""
    global _NOMINATIM_LAST_REQ
    
    cache_key = f"rev_geo_{lat}_{lng}"
    cached = _geocode_cache_get(cache_key)
    if cached is not None: return cached

    params = {
        "lat": lat, "lon": lng, "format": "json", "addressdetails": 0
    }
    headers = {"User-Agent": GEOCODE_USER_AGENT, "Accept": "application/json"}

    try:
        for attempt in range(3):
            with _NOMINATIM_LOCK:
                now = time.monotonic()
                wait = _NOMINATIM_LAST_REQ + GEOCODE_MIN_INTERVAL - now
                if wait > 0: time.sleep(wait)
                try:
                    r = httpx.get("https://nominatim.openstreetmap.org/reverse", params=params, headers=headers, timeout=5.0)
                finally:
                    _NOMINATIM_LAST_REQ = time.monotonic()

            if r.status_code == 200: break
            if r.status_code == 429 and attempt < 2:
                time.sleep(min(2.0 ** attempt, 10.0))
                continue
            return None

        data = r.json()
        if "display_name" in data:
            name = str(data["display_name"])
            name_short = name.replace(", Popayán, Cauca, Colombia", "").replace(", Centro", "").strip(", ")
            _geocode_cache_set(cache_key, name_short)
            return name_short
        return None
    except Exception as exc:
        logger.error(f"Reverse geocode error: {exc}")
        return None

async def _nominatim_reverse_geocode_async(lat: float, lng: float) -> Optional[str]:
    import asyncio
    return await asyncio.to_thread(_nominatim_reverse_geocode_raw, lat, lng)

POPAYAN_PLACES: dict = {
    # ── Centros Comerciales ──
    "Centro Comercial Campanario": [
        "centro comercial campanario", "campanario", "el campanario",
        "cc campanario", "c.c. campanario", "mall campanario",
    ],
    "Centro Comercial Terra Plaza": [
        "centro comercial terra plaza", "terra plaza", "terraplaza",
        "cc terra plaza", "terra", "c.c. terra plaza",
    ],
    "Centro Comercial Anarkos": [
        "centro comercial anarkos", "anarkos", "cc anarkos",
    ],
    "Centro Comercial Plaza Colonial": [
        "plaza colonial", "cc plaza colonial", "centro comercial plaza colonial",
    ],
    "Éxito": ["éxito", "exito", "almacén éxito", "almacen exito", "el éxito"],

    # ── Centro Histórico y alrededores ──
    "Centro Histórico": [
        "centro histórico", "centro historico", "el centro histórico",
        "el centro historico", "casco histórico", "casco antiguo",
    ],
    "Centro": [
        "el centro", "centro de popayán", "centro de popayan",
        "centro de la ciudad", "al centro", "por el centro",
    ],
    "Parque Caldas": [
        "parque caldas", "parque de caldas", "el parque caldas",
        "plaza de caldas", "caldas", "la plaza principal",
        "el parque principal", "parque central",
    ],
    "Torre del Reloj": [
        "torre del reloj", "la torre del reloj", "el reloj",
    ],
    "Puente del Humilladero": [
        "puente del humilladero", "el humilladero", "puente humilladero",
        "el puente del humilladero",
    ],
    "Iglesia San Francisco": [
        "iglesia san francisco", "san francisco", "iglesia de san francisco",
        "templo san francisco",
    ],
    "Iglesia Santo Domingo": [
        "iglesia santo domingo", "santo domingo", "templo santo domingo",
    ],
    "Catedral Basílica": [
        "catedral", "la catedral", "catedral basílica", "catedral basilica",
        "iglesia catedral",
    ],
    "Pandiguando": ["pandiguando", "el pandiguando", "estatua pandiguando"],
    "Morro de Tulcán": ["morro de tulcán", "morro de tulcan", "el morro", "tulcán", "tulcan"],
    "Pueblito Patojo": ["pueblito patojo", "el pueblito patojo", "rincón payanés", "rincon payanes"],

    # ── Universidades ──
    "Universidad del Cauca": [
        "universidad del cauca", "unicauca", "la unicauca",
        "u del cauca", "la universidad del cauca",
    ],
    "Universidad Autónoma": [
        "universidad autónoma", "universidad autonoma", "uniautónoma",
        "uniautonoma", "la autónoma", "la autonoma",
    ],
    "Fundación Universitaria de Popayán": [
        "fundación universitaria", "fundacion universitaria", "fup", "la fup",
    ],
    "SENA Popayán": ["sena", "el sena", "sena popayán", "sena popayan"],
    "SENA Norte": ["sena norte", "el sena norte", "sena del norte"],
    "SENA Centro De Comercio Y Servicios, Cl. 4 #2-80, Centro, Popayán, Cauca": ["sena centro", "el sena centro", "sena del centro"],
    "Colegio Mayor del Cauca": [
        "colegio mayor", "colegio mayor del cauca", "unimayor",
    ],
    "Universidad Antonio Nariño": [
        "universidad antonio nariño", "universidad antonio narino",
        "antonio nariño universidad",
    ],
    "Fundación Universitaria María Cano": [
        "maría cano", "maria cano", "universidad maría cano",
        "universidad maria cano", "fundación maría cano",
    ],

    # ── Hospitales / Clínicas ──
    "Hospital Universitario San José": [
        "hospital universitario san josé", "hospital universitario san jose",
        "hospital universitario", "hospital san josé", "hospital san jose",
        "el hospital", "san josé hospital",
    ],
    "Clínica La Estancia": [
        "clínica la estancia", "clinica la estancia", "la estancia clínica",
        "clínica estancia",
    ],
    "Clínica San Rafael": [
        "clínica san rafael", "clinica san rafael", "san rafael clínica",
    ],
    "Clínica Santa Gracia": [
        "clínica santa gracia", "clinica santa gracia", "santa gracia",
    ],
    "Hospital Susana López de Valencia": [
        "hospital susana", "hospital susana lópez", "hospital susana lopez",
        "susana lópez", "susana lopez", "hospital susana lópez de valencia",
    ],
    "Hospital María Occidente": [
        "hospital maría occidente", "hospital maria occidente",
    ],
    "Cruz Roja Popayán": ["cruz roja", "la cruz roja"],

    # ── Terminal / Aeropuerto ──
    "Terminal de Transporte": [
        "terminal de transporte", "terminal de transportes",
        "la terminal", "terminal", "el terminal",
    ],
    "Aeropuerto Guillermo León Valencia": [
        "aeropuerto guillermo león valencia", "aeropuerto guillermo leon valencia",
        "aeropuerto", "el aeropuerto", "aeropuerto de popayán",
    ],

    # ── Parques / Plazas / Ríos ──
    "Parque de las Aves": ["parque de las aves", "las aves"],
    "Río Molino": ["río molino", "rio molino", "el río molino", "el rio molino"],
    "Río Ejido": ["río ejido", "rio ejido"],
    "Río Cauca": ["río cauca", "rio cauca"],
    "Estadio Ciro López": [
        "estadio ciro lópez", "estadio ciro lopez", "el estadio",
        "estadio", "ciro lópez", "ciro lopez",
    ],
    "Coliseo": ["coliseo", "el coliseo", "coliseo de popayán"],

    # ── Galerías / Mercados ──
    "Galería La Esmeralda": [
        "galería la esmeralda", "galeria la esmeralda",
        "galería", "galeria", "la galería", "la galeria",
        "plaza de mercado", "la plaza de mercado",
    ],
    "Galería de Bolívar": [
        "galería bolívar", "galeria bolivar", "galería de bolívar",
    ],

    # ── Entidades públicas ──
    "Gobernación del Cauca": ["gobernación", "gobernacion", "gobernación del cauca"],
    "Alcaldía de Popayán": ["alcaldía", "alcaldia", "alcaldía de popayán"],
    "Fiscalía": ["fiscalía", "fiscalia", "la fiscalía"],
    "Registraduría": ["registraduría", "registraduria"],
    "Bomberos Popayán": ["bomberos", "los bomberos", "estación de bomberos"],

    # ── Barrios especiales / urbanizaciones ──
    "Valle del Ortigal": [
        "valle del ortigal", "el ortigal", "ortigal",
        "barrio valle del ortigal", "urbanización valle del ortigal",
        "conjunto valle del ortigal",
    ],
    "Villa del Viento": ["villa del viento", "barrio villa del viento", "villas del viento"],
    "El Jardín": ["el jardín", "el jardin", "barrio el jardín"],
    "Torres del Río": ["torres del río", "torres del rio", "barrio torres del río"],
    "Rincón de la Estancia": ["rincón de la estancia", "rincon de la estancia"],
    "Provitec": ["provitec", "barrio provitec"],
    "Zaguan": ["zaguan", "barrio zaguan", "el zaguan"],

    # ── BARRIOS COMUNA 1 (Norte / Noroccidente) ──
    "Modelo": ["modelo", "barrio modelo", "el modelo"],
    "Loma Linda": ["loma linda", "barrio loma linda"],
    "Prados del Norte": ["prados del norte", "barrio prados del norte"],
    "La Cabaña": ["la cabaña", "la cabana", "barrio la cabaña"],
    "Santa Clara": ["santa clara", "barrio santa clara"],
    "Casas Fiscales": ["casas fiscales", "barrio casas fiscales"],
    "Nueva Granada": ["nueva granada", "barrio nueva granada"],
    "Machángara": ["machángara", "machangara", "barrio machángara"],
    "La Playa": ["la playa", "barrio la playa"],
    "Campamento": ["campamento", "barrio campamento"],
    "Puerta de Hierro": ["puerta de hierro", "barrio puerta de hierro"],
    "Pubenza": ["pubenza", "barrio pubenza"],
    "Antonio Nariño": ["antonio nariño", "antonio narino", "barrio antonio nariño"],
    "Campobello": ["campobello", "barrio campobello"],
    "El Recuerdo": ["el recuerdo", "barrio el recuerdo"],
    "Belalcázar": ["belalcázar", "belalcazar", "barrio belalcázar"],
    "Los Laureles": ["los laureles", "barrio los laureles"],
    "Los Rosales": ["los rosales", "barrio los rosales"],
    "Alcalá": ["alcalá", "alcala", "barrio alcalá"],
    "Monterrosales": ["monterrosales", "barrio monterrosales"],
    "Ciudad Capri": ["ciudad capri", "capri", "barrio capri"],
    "Puerta del Sol": ["puerta del sol", "barrio puerta del sol"],

    # ── BARRIOS COMUNA 2 (Norte) ──
    "Pino Pardo": ["pino pardo", "barrio pino pardo"],
    "Balcón del Norte": ["balcón del norte", "balcon del norte"],
    "María Paz": ["maría paz", "maria paz", "barrio maría paz"],
    "Zuldemaida": ["zuldemaida", "barrio zuldemaida"],
    "Santiago de Cali": ["santiago de cali", "barrio santiago de cali"],
    "Morinda": ["morinda", "barrio morinda"],
    "El Tablazo": ["el tablazo", "barrio el tablazo"],
    "La Florida": ["la florida", "barrio la florida"],
    "La Primavera": ["la primavera", "barrio la primavera"],
    "Villa del Norte": ["villa del norte", "barrio villa del norte"],
    "El Placer": ["el placer", "barrio el placer"],
    "Bello Horizonte": ["bello horizonte", "bellohorizonte", "barrio bello horizonte"],
    "Cruz Roja (barrio)": ["barrio cruz roja", "sector cruz roja"],
    "El Bambú": ["el bambú", "el bambu", "barrio el bambú"],
    "Bella Vista": ["bella vista", "barrio bella vista", "bellavista"],
    "San Ignacio": ["san ignacio", "barrio san ignacio"],
    "La Arboleda": ["la arboleda", "barrio la arboleda"],
    "La Esperanza": ["la esperanza", "barrio la esperanza"],
    "Canterbury": ["canterbury"],
    "Villa del Viento": ["villa del viento", "barrio villa del viento"],
    "Los Cámbulos": ["los cámbulos", "los cambulos", "barrio los cámbulos"],
    "El Pinar": ["el pinar", "barrio el pinar"],
    "Guayacanes del Río": ["guayacanes del río", "guayacanes del rio", "guayacanes"],
    "Minuto de Dios": ["minuto de dios", "barrio minuto de dios"],
    "Chamizal": ["chamizal", "barrio chamizal", "el chamizal"],
    "Matamoros": ["matamoros", "barrio matamoros"],
    "Los Ángeles": ["los ángeles", "los angeles", "barrio los ángeles"],
    "Pinares": ["pinares", "barrio pinares"],
    "San Fernando": ["san fernando", "barrio san fernando"],
    "Luna Blanca": ["luna blanca", "barrio luna blanca"],
    "Urbanización La Aldea": ["la aldea", "urbanización la aldea"],

    # ── BARRIOS COMUNA 3 (Oriente) ──
    "Bolívar": ["bolívar", "bolivar", "barrio bolívar", "barrio bolivar"],
    "Ciudad Jardín": ["ciudad jardín", "ciudad jardin", "barrio ciudad jardín"],
    "Periodistas": ["periodistas", "barrio periodistas"],
    "Sotará": ["sotará", "sotara", "barrio sotará"],
    "Deportistas": ["deportistas", "barrio deportistas"],
    "Los Hoyos": ["los hoyos", "barrio los hoyos"],
    "Yambitará": ["yambitará", "yambitara", "barrio yambitará"],
    "Villa Mercedes": ["villa mercedes", "barrio villa mercedes"],
    "Yanaconas": ["yanaconas", "barrio yanaconas"],
    "La Ximena": ["la ximena", "barrio la ximena", "ximena"],
    "Pueblillo": ["pueblillo", "el pueblillo", "barrio pueblillo"],
    "José Antonio Galán": ["josé antonio galán", "jose antonio galan", "galán", "galan"],
    "Torres del Río": ["torres del río", "torres del rio"],
    "Galicia": ["galicia", "barrio galicia"],
    "La Estancia": ["la estancia", "barrio la estancia", "estancia"],
    "Moravia": ["moravia", "barrio moravia"],
    "Alicante": ["alicante", "barrio alicante"],
    "Acacias": ["acacias", "barrio acacias", "las acacias"],

    # ── BARRIOS COMUNA 4 (Centro) ──
    "Santa Teresita": ["santa teresita", "barrio santa teresita"],
    "Vásquez Cobo": ["vásquez cobo", "vasquez cobo", "barrio vásquez cobo"],
    "El Prado": ["el prado", "barrio el prado"],
    "Siglo XX": ["siglo veinte", "siglo xx", "barrio siglo xx"],
    "Los Álamos": ["los álamos", "los alamos", "barrio los álamos"],
    "San Rafael Viejo": ["san rafael viejo", "barrio san rafael viejo"],
    "El Refugio": ["el refugio", "barrio el refugio", "refugio"],
    "Liceo": ["liceo", "barrio liceo", "el liceo"],
    "La Pamba": ["la pamba", "barrio la pamba", "pamba"],
    "Loma de Cartagena": ["loma de cartagena", "barrio loma de cartagena"],
    "El Empedrado": ["el empedrado", "barrio el empedrado", "empedrado"],
    "San Camilo": ["san camilo", "barrio san camilo"],
    "Hernando Lora": ["hernando lora", "barrio hernando lora"],

    # ── BARRIOS COMUNA 5 (Oriente / Sur-Oriente) ──
    "Avelino Ull": ["avelino ull", "barrio avelino ull", "avelino"],
    "Los Braceros": ["los braceros", "barrio los braceros"],
    "El Lago": ["el lago", "barrio el lago"],
    "Berlín": ["berlín", "berlin", "barrio berlín"],
    "Suizo": ["suizo", "barrio suizo", "el suizo"],
    "Las Ferias": ["las ferias", "barrio las ferias"],
    "La Campiña": ["la campiña", "la campina", "barrio la campiña"],
    "María Oriente": ["maría oriente", "maria oriente", "barrio maría oriente", "barrio maria oriente"],
    "Los Sauces": ["los sauces", "barrio los sauces", "sauces"],
    "Santa Mónica": ["santa mónica", "santa monica", "barrio santa mónica"],
    "La Floresta": ["la floresta", "barrio la floresta", "floresta"],
    "Los Andes": ["los andes", "barrio los andes"],
    "La Alameda": ["la alameda", "barrio la alameda", "alameda"],
    "El Plateado": ["el plateado", "barrio el plateado", "plateado"],
    "Villa Oriente": ["villa oriente", "barrio villa oriente"],
    "San Andrés": ["san andrés", "san andres", "barrio san andrés"],
    "Altos Sauces": ["altos sauces", "poblado de los altos sauces", "altos de los sauces"],
    "Portal de Santa Mónica": ["portal de santa mónica", "portal de santa monica", "portal santa mónica"],

    # ── BARRIOS COMUNA 6 (Sur / Sur-Occidente) ──
    "Alfonso López": ["alfonso lópez", "alfonso lopez", "barrio alfonso lópez", "barrio alfonso lopez"],
    "Valparaíso": ["valparaíso", "valparaiso", "barrio valparaíso"],
    "Primero de Mayo": ["primero de mayo", "barrio primero de mayo", "1 de mayo"],
    "Los Comuneros": ["los comuneros", "barrio los comuneros", "comuneros"],
    "Loma de la Virgen": ["loma de la virgen", "barrio loma de la virgen", "la virgen"],
    "Sindical": ["sindical", "barrio sindical"],
    "Calicanto": ["calicanto", "barrio calicanto"],
    "Deán Bajo": ["deán bajo", "dean bajo", "barrio deán bajo"],
    "Gabriel García Márquez": [
        "gabriel garcía márquez", "gabriel garcia marquez",
        "barrio garcía márquez", "barrio garcia marquez", "garcía márquez",
    ],
    "Jorge Eliécer Gaitán": [
        "jorge eliécer gaitán", "jorge eliecer gaitan",
        "barrio gaitán", "barrio gaitan", "gaitán", "gaitan",
    ],
    "Limonar": ["limonar", "barrio limonar", "el limonar"],
    "La Paz Sur": ["la paz sur", "barrio la paz sur", "la paz"],
    "La Gran Victoria": ["la gran victoria", "barrio la gran victoria", "gran victoria"],
    "Versalles": ["versalles", "barrio versalles"],
    "La Ladera": ["la ladera", "barrio la ladera", "ladera"],
    "La Colina": ["la colina", "barrio la colina", "colina"],
    "Nuevo Japón": ["nuevo japón", "nuevo japon", "barrio nuevo japón"],
    "Tejares de Otón": ["tejares de otón", "tejares de oton", "barrio tejares"],
    "Las Veraneras": ["las veraneras", "veraneras", "barrio las veraneras"],
    "Panamericano": ["panamericano", "barrio panamericano"],
    "Camino Real": ["camino real", "barrio camino real"],

    # ── BARRIOS COMUNA 7 (Occidente) ──
    "Nazaret": ["nazaret", "barrio nazaret"],
    "Isabela": ["isabela", "barrio isabela"],
    "Las Palmas": ["las palmas", "barrio las palmas"],
    "Colombia II Etapa": ["colombia segunda etapa", "colombia dos"],
    "Los Campos": ["los campos", "barrio los campos"],
    "Treinta y Uno de Marzo": ["treinta y uno de marzo", "31 de marzo"],
    "El Mirador": ["el mirador", "barrio el mirador", "mirador"],
    "Las Vegas": ["las vegas", "barrio las vegas"],
    "Solidaridad": ["solidaridad", "barrio solidaridad"],
    "Chapinero": ["chapinero", "barrio chapinero"],
    "Retiro Alto": ["retiro alto", "barrio retiro alto"],
    "Nuevo Popayán": ["nuevo popayán", "nuevo popayan", "barrio nuevo popayán"],
    "La Unión": ["la unión", "la union", "barrio la unión"],
    "La Libertad": ["la libertad", "barrio la libertad"],
    "La Conquista": ["la conquista", "barrio la conquista"],
    "Las Brisas": ["las brisas", "barrio las brisas"],
    "Independencia": ["independencia", "barrio independencia"],
    "Santa Librada": ["santa librada", "barrio santa librada"],
    "Corsocial": ["corsocial", "barrio corsocial"],
    "Villa Occidente": ["villa occidente", "barrio villa occidente"],
    "Villa España": ["villa españa", "villa espana", "barrio villa españa"],

    # ── BARRIOS COMUNA 8 (Noroccidente) ──
    "Pandiguando (barrio)": ["barrio pandiguando"],
    "El Libertador": ["el libertador", "barrio el libertador", "libertador"],
    "El Triunfo": ["el triunfo", "barrio el triunfo", "triunfo"],
    "Popular": ["popular", "barrio popular", "el popular"],
    "La Cañada": ["la cañada", "la canada", "barrio la cañada"],
    "Llano Largo": ["llano largo", "barrio llano largo"],
    "José María Obando": ["josé maría obando", "jose maria obando", "obando"],
    "Guayabal": ["guayabal", "barrio guayabal", "el guayabal"],
    "La Isla": ["la isla", "barrio la isla"],
    "Esperanza Sur": ["esperanza sur", "barrio esperanza sur"],
    "Camilo Torres": ["camilo torres", "barrio camilo torres"],
    "Junín": ["junín", "junin", "barrio junín"],
    "Santa Helena": ["santa helena", "barrio santa helena"],
    "Lomas de Granada": ["lomas de granada", "barrio lomas de granada", "granada"],
    "Mis Ranchitos": ["mis ranchitos", "barrio mis ranchitos"],
    "La Capitana": ["la capitana", "barrio la capitana"],
    "San Antonio de Padua": ["san antonio de padua", "san antonio", "barrio san antonio"],
    "Kennedy": ["kennedy", "barrio kennedy"],
    "San José (barrio)": ["barrio san josé", "barrio san jose"],
    "La Sombrilla": ["la sombrilla", "barrio la sombrilla"],
    "Carlos Primero": ["carlos primero", "barrio carlos primero"],
    "Cinco de Abril": ["cinco de abril", "5 de abril", "barrio cinco de abril"],
    "María Occidente": ["maría occidente", "maria occidente", "barrio maría occidente"],
    "Los Naranjos": ["los naranjos", "barrio los naranjos"],
    "Nuevo Hogar": ["nuevo hogar", "barrio nuevo hogar"],
    "La Esmeralda": ["la esmeralda", "esmeralda", "barrio la esmeralda"],
    "Santa Lucía": ["santa lucía", "santa lucia", "santa luca", "barrio santa lucía", "urbanización santa lucía", "urbanizacion santa lucia", "organización santa lucía", "organizacion santa lucia"],

    # ── BARRIOS COMUNA 9 (Sur-Occidente) ──
    "Pomona": ["pomona", "barrio pomona"],
    "Lomas de Pomona": ["lomas de pomona", "barrio lomas de pomona"],
    "Bosques de Pomona": ["bosques de pomona", "barrio bosques de pomona"],
    "El Uvo": ["el uvo", "barrio el uvo"],
    "Las Américas": ["las américas", "las americas", "barrio las américas"],
    "Santa Rosa": ["santa rosa", "barrio santa rosa"],
    "Los Tejares": ["los tejares", "barrio los tejares", "tejares"],
}

# Add all barrios from geodata if available
try:
    from tools.popayan_geodata import ALL_BARRIOS
    for b in ALL_BARRIOS:
        if b not in POPAYAN_PLACES:
            POPAYAN_PLACES[b] = [b.lower(), f"barrio {b.lower()}"]
except ImportError:
    pass

# ── BASIC UTILS ─────────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Standard normalization: lowercase, remove accents, strip."""
    if not text: return ""
    t = text.lower()
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = t.replace('ñ', 'n')
    # Remove punctuation
    t = re.sub(r'[^\w\s]', '', t)
    return t.strip()

def _clean_stt_text(text: str) -> str:
    """Cleans Twilio/WhatsApp artifacts and filler words."""
    if not text: return ""
    t = text.strip()
    # Remove leading/trailing punctuation
    t = re.sub(r'^[.?!,;:\s]+', '', t)
    t = re.sub(r'[.?!]+$', '', t)

    # Remove common filler words at the beginning
    for filler in sorted(_FILLER_WORDS, key=len, reverse=True):
        pattern = r'^' + re.escape(filler) + r'[,.]?\s*'
        t = re.sub(pattern, '', t, flags=re.IGNORECASE).strip()

    # Remove repeated words ("la la esmeralda" → "la esmeralda")
    t = re.sub(r'\b(\w+)\s+\1\b', r'\1', t, flags=re.IGNORECASE)

    # Normalize whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def _spanish_phonetic_key(text: str) -> str:
    """Generate a crude Spanish phonetic key for fuzzy matching."""
    t = _normalize_text(text)
    t = t.replace('v', 'b')
    t = re.sub(r'c(?=[ei])', 's', t)
    t = t.replace('z', 's')
    t = t.replace('ll', 'y')
    t = t.replace('h', '')
    t = re.sub(r'g(?=[ei])', 'j', t)
    t = t.replace('qu', 'k').replace('q', 'k')
    t = re.sub(r'(.)\1+', r'\1', t)
    t = t.replace(' ', '')
    return t

# ── CORRECTION & PREAMBLE ──────────────────────────────────────────────────

def _correct_speech(text: str) -> str:
    """Apply STT corrections and fuzzy matching for Popayán places."""
    if not text: return text
    t = _clean_stt_text(text)
    t_lower = t.lower().strip()

    # 1. Exact correction
    if t_lower in _SPEECH_CORRECTIONS:
        return _SPEECH_CORRECTIONS[t_lower]

    # 2. Phonetic fuzzy match
    t_phonetic = _spanish_phonetic_key(t_lower)
    if len(t_lower) >= 4:
        for canonical, aliases in POPAYAN_PLACES.items():
            for alias in aliases:
                if _spanish_phonetic_key(alias) == t_phonetic:
                    return canonical
    return t

def _strip_preamble(text: str) -> str:
    """Removes 'Hola, me regala un taxi en...' type of headers."""
    if not text: return ""
    t = text.strip()
    changed = True
    while changed:
        changed = False
        for pattern in _PREAMBLE_PATTERNS:
            new_t = re.sub(pattern, '', t, flags=re.IGNORECASE).strip()
            if new_t != t:
                t = new_t
                changed = True
    return t if len(t) >= 2 else text.strip()

# ── INTENT PARSING ──────────────────────────────────────────────────────────

def _parse_si_no(text: str) -> Optional[bool]:
    """Interprets affirmative/negative response."""
    t = _normalize_text(text)
    positivos = {"si", "claro", "exacto", "correcto", "ok", "dale", "yes", "obvio", "afirmativo", "asi", "eso", "bien"}
    negativos = {"no", "nop", "nel", "nope", "para nada", "negativo", "incorrecto", "tampoco", "nunca", "jamas"}
    words = set(t.split())
    if words & positivos: return True
    if words & negativos: return False
    return None

def _is_correction_request(text: str) -> bool:
    """Detects if user wants to change previous info."""
    t = text.lower()
    triggers = ["corregir", "cambiar", "equivoke", "me equivoque", "no es ahi", "esta mal", "error"]
    return any(trigger in t for trigger in triggers)

def _is_repeat_request(text: str) -> bool:
    """Detects if user didn't hear/understand."""
    t = text.lower()
    triggers = ["repite", "como", "no escuche", "que dijo", "repitame"]
    return any(trigger in t for trigger in triggers)

# ── ADDRESS EXTRACTION ──────────────────────────────────────────────────────

def _try_local_match(text: str) -> Optional[str]:
    """Deterministic local matching using geodata registry and aliases.
    Returns the CANONICAL place name, not the raw input text."""
    if not text: return None
    t_clean = _strip_preamble(text)
    t_corrected = _correct_speech(t_clean)
    
    # 1. Alias lookup
    t_norm = _normalize_text(t_corrected)
    for canonical, aliases in POPAYAN_PLACES.items():
        for alias in aliases:
            if _normalize_text(alias) == t_norm:
                return canonical

    # 2. Geodata search — return CANONICAL name, not raw text
    try:
        from tools.popayan_geodata import geocode_local
        geo = geocode_local(t_corrected)
        if geo:
            # geo = (lat, lng, display_name) where display_name = "Canonical, Popayán, Cauca, Colombia"
            display_name = geo[2] if len(geo) > 2 else ""
            canonical = display_name.split(",")[0].strip() if display_name else t_corrected
            return canonical if len(canonical) >= 2 else t_corrected
    except ImportError:
        pass

    return None

def normalize_address(address: str) -> str:
    """Standardize nomenclature (Calle -> Cl, etc.)"""
    if not address: return ""
    replacements = {
        r'\bcl\b': 'Calle', r'\bcra?\b': 'Carrera', r'\bkra?\b': 'Carrera',
        r'\bav\b': 'Avenida', r'\btr\b': 'Transversal', r'\bdiag?\b': 'Diagonal',
        r'\bno\.\s*': '#',
    }
    result = address
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result.strip()

def extract_pickup_address(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Primary entry point for origin extraction. Tries local then LLM."""
    local = _try_local_match(text)
    if local:
        return local, None

    # Fallback to simple regex/heuristics before LLM
    t_stripped = _strip_preamble(text)
    if len(t_stripped) > 5 and any(kw in t_stripped.lower() for kw in ["calle", "carrera", "cra", "cl", "#"]):
        return t_stripped, None

    return None, None # Signal that we need Slow Brain (LLM) or more info

def extract_destination_address(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Primary entry point for destination extraction."""
    # Destination is often shorter, try local match first
    local = _try_local_match(text)
    if local:
        return local, None
    
    t_stripped = _strip_preamble(text)
    if len(t_stripped) > 3:
        return t_stripped, None
        
    return None, None

# ── DATETIME EXTRACTION ─────────────────────────────────────────────────────

def extract_datetime_local(text: str) -> Optional[Dict[str, str]]:
    """Fast brain datetime extraction for common patterns."""
    t = _normalize_text(text)
    now = datetime.now(timezone(timedelta(hours=-5))) # Popayán time
    
    # Common patterns
    if "ahora" in t or "ya" in t:
        return None # Signal immediate service
        
    # "mañana"
    if "manana" in t:
        target_date = now + timedelta(days=1)
        # Try to find time: "a las 8", "8:30", "8 y media"
        time_match = re.search(r'(\d{1,2})(?::(\d{2}))?', t)
        if time_match:
            hh = int(time_match.group(1))
            mm = int(time_match.group(2)) if time_match.group(2) else 0
            if "tarde" in t or "noche" in t or (hh < 7): hh += 12 # Simple PM heuristic
            return {
                "fecha_programada": target_date.strftime("%Y-%m-%d"),
                "hora_programada": f"{hh:02d}:{mm:02d}"
            }
            
    # "en X minutos"
    mins_match = re.search(r'en (\d+) minutos', t)
    if mins_match:
        delta = int(mins_match.group(1))
        target = now + timedelta(minutes=delta)
        return {
            "fecha_programada": target.strftime("%Y-%m-%d"),
            "hora_programada": target.strftime("%H:%M")
        }

    return None

async def extract_datetime_with_llm(user_text: str) -> dict:
    """Unified LLM datetime extraction."""
    # Try Fast Brain first
    local = extract_datetime_local(user_text)
    if local: return local

    tz = timezone(timedelta(hours=-5))
    now = datetime.now(tz)
    
    prompt = f"""Extrae la fecha y hora programada mencionada por el usuario para un servicio de taxi.
Hoy es {now.strftime('%Y-%m-%d')}, la hora actual es {now.strftime('%H:%M:%S')}.
Responde SOLO en JSON: {{"fecha_programada": "YYYY-MM-DD", "hora_programada": "HH:MM"}}
Texto: {user_text}"""
    
    content = await call_llm(prompt, "Output ONLY valid JSON. 24h format.")
    if content:
        res = extract_json_object(content)
        return res or {}
    return {}