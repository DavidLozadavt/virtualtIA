"""
core/stt_enhancer.py — Motor de mejora de STT para audio telefónico colombiano.

Resuelve:
- Errores fonéticos de Twilio/Whisper
- Acentos regionales colombianos (payanés, pastuso, caucano)
- Audio degradado, ruido vehicular, manos libres
- Habla rápida/lenta, fusión de palabras, cortes
- Slang y localismos de Popayán
"""

from __future__ import annotations

import re
import threading
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional


# ── Normalización base ────────────────────────────────────────────────────────

def strip_accents(text: str) -> str:
    """Quita tildes para comparaciones fonéticas."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


@lru_cache(maxsize=4096)
def phonetic_key(text: str) -> str:
    """
    Clave fonética para español colombiano.
    Agrupa sonidos equivalentes que el STT confunde:
      - b/v, s/c/z, ll/y, h muda, qu/k, g/j (ante e,i), rr/r
      - vocales dobles, consonantes repetidas
      - terminaciones nasales (-n/-m)
    """
    t = strip_accents(text.lower().strip())

    # Equivalencias fonéticas colombianas
    replacements = [
        (r"v",          "b"),
        (r"z",          "s"),
        (r"c(?=[ei])",  "s"),      # ce→se, ci→si
        (r"qu",         "k"),
        (r"q",          "k"),
        (r"ll",         "y"),
        (r"h",          ""),       # h muda
        (r"g(?=[ei])",  "j"),      # ge→je, gi→ji
        (r"x",          "ks"),
        (r"ph",         "f"),
        (r"rr",         "r"),
        (r"ck",         "k"),
        (r"nk",         "nk"),
        (r"mp",         "np"),     # "campanario" → "kanpanario"
        (r"gua",        "wa"),     # Yanaconas / Guamba
        (r"gue",        "ge"),
        (r"gui",        "gi"),
        (r"ue",         "we"),
        (r"ui",         "wi"),
        (r"ñ",          "ny"),
        (r"(.)\1+",     r"\1"),    # letras dobles → una
        (r"\s+",        ""),       # eliminar espacios
    ]

    for pat, repl in replacements:
        t = re.sub(pat, repl, t)

    return t


def bigram_similarity(a: str, b: str) -> float:
    """Similitud por bigramas (0.0–1.0)."""
    if not a or not b:
        return 0.0
    a_bg = {a[i:i+2] for i in range(len(a)-1)}
    b_bg = {b[i:i+2] for i in range(len(b)-1)}
    if not a_bg or not b_bg:
        return 1.0 if a == b else 0.0
    return len(a_bg & b_bg) / len(a_bg | b_bg)


def combined_score(input_text: str, candidate: str) -> float:
    """
    Score combinado: bigrama + fonético + ratio de longitud.
    Calibrado para nombres de barrios/calles colombianos.
    """
    i_norm = strip_accents(input_text.lower())
    c_norm = strip_accents(candidate.lower())

    bg   = bigram_similarity(i_norm, c_norm)
    ph   = bigram_similarity(phonetic_key(input_text), phonetic_key(candidate))
    seq  = SequenceMatcher(None, i_norm, c_norm).ratio()
    lr   = min(len(i_norm), len(c_norm)) / max(len(i_norm), len(c_norm), 1)

    return bg * 0.30 + ph * 0.35 + seq * 0.20 + lr * 0.15


# ── Correcciones STT específicas para Popayán ────────────────────────────────

# Correcciones exactas de errores comunes de Twilio/Whisper
# Formato: { "error_stt": "nombre_correcto" }
POPAYAN_STT_CORRECTIONS: dict[str, str] = {
    # Barrios mal reconocidos frecuentemente
    "campanaryo":        "campanario",
    "campanaro":         "campanario",
    "kampanaryo":        "campanario",
    "campana rio":       "campanario",
    "pubensa":           "pubenza",
    "pubenza":           "pubenza",
    "pubensas":          "pubenza",
    "pubencia":          "pubenza",
    "povensa":           "pubenza",
    # Mishears reales observados en logs de producción para "Pubenza" sobre
    # audio PSTN degradado ("...para la fuerza" / "...la prensa" = "para Pubenza").
    # En este IVR de taxi el referente es siempre un barrio, no la acepción común.
    "la fuerza":         "pubenza",
    "la prensa":         "pubenza",
    "yanaconaz":         "yanaconas",
    "yanakonas":         "yanaconas",
    "llanaconas":        "yanaconas",
    "yanakona":          "yanaconas",
    "pandeguando":       "pandiguando",
    "pandigando":        "pandiguando",
    "pandi guando":      "pandiguando",
    "pandeiguando":      "pandiguando",
    "esmeraldaaa":       "la esmeralda",
    "esmeraldas":        "la esmeralda",
    "esmeralda":         "la esmeralda",
    "mosqueraa":         "mosquera",
    "belalcasar":        "belalcázar",
    "belalcazar":        "belalcázar",
    "belal casar":       "belalcázar",
    "belal cazar":       "belalcázar",
    "valle del ortigal": "valle del ortigal",   # self-entry → exact match exits early, no subcadena cascade
    "ortigal":           "valle del ortigal",
    "hortigal":          "valle del ortigal",    # mishear Whisper ("hortigal"); el colapso de duplicados limpia "valle del valle del ortigal"
    "el ortigal":        "valle del ortigal",
    "valle ortigal":     "valle del ortigal",
    "valle del hostiga": "valle del ortigal",   # STT mishear
    "valle hostiga":     "valle del ortigal",
    "valle del ostiga":  "valle del ortigal",
    "valle del osti":    "valle del ortigal",
    "valle del ortiga":  "valle del ortigal",
    "polidepor":         "polideportivo",
    "polidepotivo":      "polideportivo",
    "los sause":         "los sauces",
    "los sauses":        "los sauces",
    "maria oriente":     "maría oriente",
    "maría del oriente": "maría oriente",
    "comuneros":         "los comuneros",
    "alfonsol opez":     "alfonso lópez",
    "alfonso lopes":     "alfonso lópez",
    "camilo tor":        "camilo torres",
    "yambitara":         "yambitará",
    "jambitara":         "yambitará",
    "jambitará":         "yambitará",
    "yanbitara":         "yambitará",
    "loma linda":        "loma linda",
    "berlín":            "berlín",
    "berling":           "berlín",
    "berlin":            "berlín",
    "veinte de julio":   "20 de julio",
    "primero mayo":      "primero de mayo",
    "1 mayo":            "primero de mayo",
    "cinco abril":       "cinco de abril",
    "5 abril":           "cinco de abril",
    "jorge eli":         "jorge eliécer gaitán",
    "jorge eliecer":     "jorge eliécer gaitán",

    # Landmarks mal reconocidos
    "parque cal das":    "parque caldas",
    "parque callas":     "parque caldas",
    "torre reloj":       "torre del reloj",
    "catredal":          "catedral",
    "unicauca":          "universidad del cauca",
    "uni cauca":         "universidad del cauca",
    "u de cauca":        "universidad del cauca",
    "terminal trans":    "terminal de transportes",
    "galeria":           "galería",
    "colisea":           "coliseo",
    "anarkos":           "anarkos",
    "terra plaza":       "terra plaza",
    "morro tulcan":      "morro de tulcán",
    "morro de tulcan":   "morro de tulcán",
    "el morro":          "morro de tulcán",
    "la ermita":         "ermita",
    "san jose":          "hospital san josé",
    "la estancia":       "clínica la estancia",
    "hopital":           "hospital",
    "ospital":           "hospital",

    # Calles con errores frecuentes de STT
    "calle seis":        "calle 6",
    "calle siete":       "calle 7",
    "calle ocho":        "calle 8",
    "calle nueve":       "calle 9",
    "calle dise":        "calle 10",
    "calle onse":        "calle 11",
    "calle dose":        "calle 12",
    "calle katorse":     "calle 14",
    "calle kince":       "calle 15",
    "calle beinte":      "calle 20",
    "calle treinta":     "calle 30",
    "carrera seis":      "carrera 6",
    "carrera siete":     "carrera 7",
    "carrera ocho":      "carrera 8",
    "carrera nueve":     "carrera 9",
    "carrera dies":      "carrera 10",
    "carrera dose":      "carrera 12",
    "carrera katorce":   "carrera 14",
    "carrera kince":     "carrera 15",

    # Localismos / referencias humanas
    "por el exito":       "Éxito (supermercado)",
    "por la olimpica":    "Olímpica",
    "por olimpica":       "Olímpica",
    "la olimpica":        "Olímpica",
    "la bomba texaco":    "estación Texaco",
    "la bomba terpel":    "estación Terpel",
    "la bomba":           "estación de gasolina",
    "el obelisco":        "obelisco",
    "las palmas":         "las palmas",
    "el sena":            "SENA",
    "en el sena":         "SENA",
    "la alameda":         "la alameda",

    # Corregimientos
    "juli mito":         "julumito",
    "julimiito":         "julumito",
    "la iunga":          "la yunga",
    "calibio":           "calibío",
    "poblason":          "poblazón",
    "guacas":            "las guacas",
    "huacas":            "las guacas",
    "las huacas":        "las guacas",
    "pisohe":            "pisojé",
    "pisoje":            "pisojé",

    # Comfacauca y otros landmarks
    "compa cauca":       "comfacauca",
    "comfa cauca":       "comfacauca",
    "confacauca":        "comfacauca",
    "confa cauca":       "comfacauca",

    # Contracciones payanesas comunes
    "pa campanario":     "para campanario",
    "pal centro":        "para el centro",
    "pa la 15":          "para la calle 15",
    "pa la universidad": "para la universidad del cauca",
}


def correct_stt_errors(text: str) -> str:
    """
    Aplica correcciones STT en orden de especificidad:
    1. Match exacto
    2. Match de subcadenas (barrio dentro de frase)
    3. Normalización de abreviaturas de calles
    """
    if not text:
        return text

    t = text.strip()
    t_lower = t.lower()

    # 1. Match exacto completo
    if t_lower in POPAYAN_STT_CORRECTIONS:
        return POPAYAN_STT_CORRECTIONS[t_lower]

    # 2. Subcadenas — reemplaza la parte errónea dentro de una frase más larga.
    #    Solo se reemplaza en LÍMITE DE PALABRA (\b...\b): así "ortigal" no
    #    matchea dentro de "hortigal" ni "ospital" dentro de "hospital", que
    #    causaba texto corrupto ("valle del hvalle del ortigal", "hhospital").
    #    Guard: saltar si la forma correcta YA está en el resultado — evita
    #    doble-reemplazo en cascada (ej: "el ortigal" ⊂ "valle del ortigal").
    result = t_lower
    for wrong, right in sorted(
        POPAYAN_STT_CORRECTIONS.items(), key=lambda x: len(x[0]), reverse=True
    ):
        # Guard también en límite de palabra: la forma correcta se considera
        # "ya presente" solo si aparece como palabra completa, no como subcadena
        # (sin esto, "ortigal" ⊂ "hortigal" bloqueaba la corrección de "hortigal").
        if re.search(r'\b' + re.escape(right.lower()) + r'\b', result):
            continue
        pattern = r'\b' + re.escape(wrong) + r'\b'
        if re.search(pattern, result):
            result = re.sub(pattern, right, result)

    # 2b. Red de seguridad: colapsar frases idénticas adyacentes que un reemplazo
    #     auto-expansivo (wrong ⊂ right) haya podido duplicar.
    result = _collapse_adjacent_duplicate_phrases(result)

    # 3. Normalización de abreviaturas de calle
    result = _normalize_street_abbreviations(result)

    return result if result != t_lower else t


def _collapse_adjacent_duplicate_phrases(text: str) -> str:
    """
    Colapsa frases idénticas adyacentes:
      "valle del valle del ortigal" → "valle del ortigal"
      "popayán popayán"             → "popayán"

    Red de seguridad contra reemplazos auto-expansivos (wrong ⊂ right) que
    dupliquen palabras vecinas ya presentes en el texto.
    """
    words = text.split()
    changed = True
    while changed:
        changed = False
        for size in range(min(4, len(words) // 2), 0, -1):
            i = 0
            while i + 2 * size <= len(words):
                if words[i:i + size] == words[i + size:i + 2 * size]:
                    del words[i + size:i + 2 * size]
                    changed = True
                else:
                    i += 1
            if changed:
                break
    return " ".join(words)


def _normalize_street_abbreviations(text: str) -> str:
    """
    Normaliza abreviaturas de calle a forma canónica.
    cl, cll, c/ → calle | cra, cr, kra, kr, k → carrera
    """
    t = text
    t = re.sub(r'\b(cl|cll|c/)(\s*)(\d)', r'calle \3', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(cra|cr|kra|kr|k)(\s*)(\d)', r'carrera \3', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(av|avda|avd)(\s*)(\d|[a-z])', r'avenida \3', t, flags=re.IGNORECASE)
    # Normalizar el signo # para direcciones
    t = re.sub(r'\bnum\b\.?\s*', '# ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bnúmero\b\.?\s*', '# ', t, flags=re.IGNORECASE)
    return t


def fuzzy_match_location(
    user_input: str,
    candidates: list[str],
    threshold: float = 0.52,
) -> Optional[str]:
    """
    Busca el candidato más similar al input usando score combinado.
    Retorna el mejor match si supera el umbral, o None.

    Threshold calibrado para:
    - 0.52: acepta "campanaryo" → "campanario" (~0.61)
    - 0.52: acepta "yanakonaz" → "yanaconas" (~0.55)
    - 0.52: rechaza "calle" → "la esmeralda" (~0.10)
    """
    if not user_input or not candidates:
        return None

    input_clean = strip_accents(user_input.lower().strip())
    if len(input_clean) < 3:
        return None

    best_candidate = None
    best_score = 0.0

    for candidate in candidates:
        cand_clean = strip_accents(candidate.lower().strip())

        # Si el input está contenido en el candidato o viceversa → boost
        if input_clean in cand_clean or cand_clean in input_clean:
            score = 0.75 + combined_score(input_clean, cand_clean) * 0.25
        else:
            score = combined_score(input_clean, cand_clean)

        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_score >= threshold and best_candidate:
        return best_candidate

    return None


# ── Landmarks / referencias humanas de Popayán ───────────────────────────────

# Referencias informales → nombre canónico + coordenadas aproximadas
HUMAN_REFERENCES: dict[str, dict] = {
    # Supermercados / Comercio
    "por el éxito": {
        "canonical": "Éxito Popayán",
        "lat": 2.4448, "lon": -76.6072,
        "aliases": ["por el exito", "éxito", "el exito", "al exito"],
    },
    "olimpica": {
        "canonical": "Olímpica",
        "lat": 2.4396, "lon": -76.6113,
        "aliases": ["olimpica", "la olimpica", "por olimpica", "por la olimpica"],
    },
    "anarkos": {
        "canonical": "Anarkos Centro Comercial",
        "lat": 2.4442, "lon": -76.6095,
        "aliases": ["anarkos", "el anarkos"],
    },
    "terra plaza": {
        "canonical": "Terra Plaza",
        "lat": 2.4489, "lon": -76.5983,
        "aliases": ["terra", "terra plaza", "el terra"],
    },
    "campanario cc": {
        "canonical": "Centro Comercial Campanario",
        "lat": 2.459635441153488, "lon": -76.59421007333673,
        "aliases": ["campanario cc", "campanario mall", "centro comercial campanario", "campanario", "el campanario"],
    },

    # Referencias a combustible / servicios
    "la bomba": {
        "canonical": "estación de servicio",
        "lat": None, "lon": None,
        "aliases": ["la bomba", "la gasolinera", "la estacion", "la estación"],
        "note": "Referencia ambigua — confirmar cuál estación",
    },

    # Instituciones
    "unicauca": {
        "canonical": "Universidad del Cauca",
        "lat": 2.4417, "lon": -76.6080,
        "aliases": ["unicauca", "la u", "la universidad", "uni cauca", "u del cauca"],
    },
    "sena": {
        "canonical": "SENA Popayán",
        "lat": 2.4381, "lon": -76.6144,
        "aliases": ["sena", "el sena", "en el sena"],
        "needs_disambiguation": True,
    },
    "hospital san jose": {
        "canonical": "Hospital San José",
        "lat": 2.4350, "lon": -76.6080,
        "aliases": ["hospital san jose", "san jose", "el hospital"],
    },
    "hospital susana": {
        "canonical": "Hospital Susana López de Valencia",
        "lat": 2.4398, "lon": -76.6111,
        "aliases": ["hospital susana", "susana lopez", "susana", "hospital susana lopez"],
    },
    "valle del ortigal": {
        "canonical": "Valle del Ortigal",
        "lat": 2.4603913005798788, "lon": -76.63971248137291,
        "aliases": ["valle del ortigal", "ortigal", "el ortigal", "valle ortigal"],
    },
    "sena norte": {
        "canonical": "SENA Norte",
        "lat": 2.4829669540145356, "lon": -76.56233437579733,
        "aliases": ["sena norte", "sena del norte"],
    },
    "sena centro": {
        "canonical": "SENA Centro De Comercio Y Servicios",
        "lat": 2.441584217876181, "lon": -76.6028230716416,
        "aliases": ["sena centro", "sena del centro", "senacentro", "el senacentro"],
    },
    # Dos "La Paz" en Popayán → ambiguo, como SENA. "la paz" (barrio) tiene
    # dirección fija Cra. 4 #70AN-09 (override en voice_call_engine); "la paz sur"
    # geocodifica bien por su nombre. lat/lon de "la paz" aproximados: la creación
    # del servicio usa la dirección override, no estas coords.
    "la paz": {
        "canonical": "La Paz",
        "lat": 2.4775, "lon": -76.6095,
        "aliases": ["la paz", "barrio la paz", "la paz barrio", "barrio paz"],
        "needs_disambiguation": True,
    },
    "la paz sur": {
        "canonical": "La Paz Sur",
        "lat": 2.4321, "lon": -76.6111,
        "aliases": ["la paz sur", "lapaz sur", "barrio la paz sur"],
    },
    "la estancia clinica": {
        "canonical": "Clínica La Estancia",
        "lat": 2.4528, "lon": -76.5960,
        "aliases": ["la estancia", "clinica la estancia", "clínica estancia"],
    },

    # Referencias geográficas / lugares emblemáticos
    "comfacauca": {
        "canonical": "Comfacauca",
        "lat": 2.4480, "lon": -76.6000,
        "aliases": ["comfacauca", "piscinas comfacauca", "las torres comfacauca"],
    },
    "morro de tulcan": {
        "canonical": "Morro de Tulcán",
        "lat": 2.4453, "lon": -76.6064,
        "aliases": ["morro", "el morro", "morro tulcan", "subiendo al morro", "bajando del morro"],
    },
    "parque caldas": {
        "canonical": "Parque Caldas",
        "lat": 2.4418, "lon": -76.6066,
        "aliases": ["parque caldas", "el parque", "parque central"],
    },
    "torre reloj": {
        "canonical": "Torre del Reloj",
        "lat": 2.4420, "lon": -76.6062,
        "aliases": ["torre del reloj", "la torre", "el reloj"],
    },
    "catedral": {
        "canonical": "Catedral de Popayán",
        "lat": 2.4416, "lon": -76.6063,
        "aliases": ["catedral", "la catedral", "iglesia catedral"],
    },
    "puente humilladero": {
        "canonical": "Puente del Humilladero",
        "lat": 2.4430, "lon": -76.6038,
        "aliases": ["puente humilladero", "el humilladero", "puente del humilladero", "después del puente"],
    },
    "galeria": {
        "canonical": "Galería Centenario",
        "lat": 2.4440, "lon": -76.6090,
        "aliases": ["galeria", "la galería", "la galeria", "frente a la galería", "frente a la galeria"],
    },
    "terminal": {
        "canonical": "Terminal de Transportes",
        "lat": 2.4304, "lon": -76.6108,
        "aliases": ["terminal", "el terminal", "la terminal", "terminal de buses"],
    },
    "aeropuerto": {
        "canonical": "Aeropuerto Guillermo León Valencia",
        "lat": 2.4544, "lon": -76.6098,
        "aliases": ["aeropuerto", "el aeropuerto"],
    },
    "estadio": {
        "canonical": "Estadio de Popayán",
        "lat": 2.4563, "lon": -76.6003,
        "aliases": ["estadio", "el estadio", "cancha"],
    },
    "polideportivo": {
        "canonical": "Polideportivo",
        "lat": 2.4498, "lon": -76.5945,
        "aliases": ["polideportivo", "el polideportivo", "polidepor"],
    },
}

# Grupos de desambiguación: una entidad "base" ambigua → sus sedes concretas.
# Las claves y valores son claves de HUMAN_REFERENCES. Data-driven: agregar una
# entidad multi-sede futura es solo añadir aquí (sin tocar el flujo de Twilio).
DISAMBIGUATION_GROUPS: dict[str, list[str]] = {
    "sena": ["sena norte", "sena centro"],
    "la paz": ["la paz", "la paz sur"],
}


# Partículas de relleno que NO aportan identidad de lugar. Si tras quitar el
# alias matcheado y estas partículas todavía queda una palabra de contenido
# (≥4 chars), el alias NO cubre el input → es OTRO lugar más específico.
# Ej: "el parque de las garzas" ≠ "el parque" (Parque Caldas).
_COVERAGE_STOPWORDS = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "en", "por", "al", "a", "y", "o", "para", "pa",
    "hacia", "cerca", "frente", "junto", "sobre", "aqui", "aca",
    "alla", "alli", "ahi", "estoy", "estamos", "esta", "queda", "es",
    "voy", "vamos", "me", "mi", "barrio", "sector", "que",
})


def _alias_covers_input(alias_norm: str, input_norm: str) -> bool:
    """True si el alias cubre el input sin dejar palabras de contenido sueltas.

    Solo aplica cuando el input tiene MÁS palabras que el alias (caso "palabra
    extra"). Para misspellings de igual longitud ("campanaryo"→"campanario")
    devuelve True y no interfiere con el fuzzy.
    """
    alias_tokens = alias_norm.split()
    input_tokens = input_norm.split()
    if len(input_tokens) <= len(alias_tokens):
        return True
    alias_set = set(alias_tokens)
    leftover = [
        tok for tok in input_tokens
        if tok not in alias_set
        and tok not in _COVERAGE_STOPWORDS
        and len(tok) >= 4
    ]
    return not leftover


def resolve_human_reference(text: str) -> Optional[dict]:
    """
    Convierte una referencia humana informal a datos estructurados.
    Retorna dict con canonical, lat, lon o None.

    Adaptador de compatibilidad sobre el resolver tipado
    (core.location_match.resolve_location_entity). Solo devuelve un dict cuando
    la decisión es ACCEPT o AMBIGUOUS (needs_disambiguation); para coincidencias
    de confianza media (CONFIRM) o nulas devuelve None — los callers que
    requieran la lógica CONFIRM deben usar el resolver directamente.

    Ejemplos:
      "frente a la galería" → Galería Centenario (2.4440, -76.6090)
      "por el éxito"        → Éxito Popayán (2.4448, -76.6072)
      "en el"               → None  (relleno puro, ya no mapea a SENA)
    """
    from core.location_match import resolve_location_entity, decide, Decision

    m = resolve_location_entity(text)
    d = decide(m)
    if d not in (Decision.ACCEPT, Decision.AMBIGUOUS) or not m.canonical:
        return None

    note = None
    for data in HUMAN_REFERENCES.values():
        if data.get("canonical") == m.canonical:
            note = data.get("note")
            break

    return {
        "canonical":             m.canonical,
        "lat":                   m.lat,
        "lon":                   m.lon,
        "note":                  note,
        "matched_alias":         m.evidence,
        "needs_disambiguation":  m.needs_disambiguation,
        "disambiguation_candidates": list(m.disambiguation_candidates),
        "confidence":            m.confidence,
        "match_type":            m.match_type.name,
        "decision":              d.value,
    }


# ── Limpieza de intención de dirección (ruido conversacional) ──────────────────
#
# Capa ANTERIOR a los clasificadores (is_street / looks_like_place). El usuario
# suele anteponer cortesía y nombres propios a la dirección real:
#   "buenas tardes osvaldo valle del ortigal"  →  "valle del ortigal"
# Quitar ese ruido ANTES de clasificar evita que el gate anti-basura rechace una
# dirección perfectamente válida por culpa de tokens iniciales que no aportan.
#
# Conservador por diseño: si el texto ya parece un lugar, se devuelve intacto; si
# tras limpiar queda vacío o sin contenido de lugar, se devuelve el original.
# Nunca degrada a algo peor que la entrada.

# Saludos a eliminar al inicio (admite coma/punto/espacios después). Se aplica en
# bucle, así "hola buenas tardes ..." se limpia por completo.
_GREETING_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"buen[oa]s?\s+(?:tardes|noches|d[ií]as|d[ií]a)"
    r"|buen\s+d[ií]a"
    r"|buen[oa]s?"
    r"|hola"
    r"|al[oó]"
    r"|ola"
    r"|hey"
    r"|qu[ieé]\s*hubo|quihubo|qhubo"
    r"|diga|d[ií]game"
    r")"
    r"[\s,.;:!¡¿?\-]*",
    re.IGNORECASE,
)

# Palabras-clave de vía/nomenclatura. Si el primer token tras el saludo es una de
# estas, es parte de la dirección (no un nombre propio suelto) → no se descarta.
_VIA_KEYWORDS = frozenset({
    "calle", "cl", "cll", "carrera", "cra", "cr", "kr", "kra", "k",
    "avenida", "av", "avda", "avd", "diagonal", "diag", "dg",
    "transversal", "tv", "tr", "via", "autopista", "anillo", "circunvalar",
    "manzana", "mz", "numero", "no", "nro", "calle.", "cra.",
})

_STREET_NUM_RE = re.compile(
    r"\b(?:calle|carrera|cra|cr|cl|cll|kr|kra|av|avenida|avda|diagonal|diag|"
    r"transversal|tv|tr)\s*\.?\s*\d",
    re.IGNORECASE,
)


def _is_street(text: str) -> bool:
    """True si `text` tiene nomenclatura de vía con número (calle/cra/av + dígito)."""
    return bool(text) and bool(_STREET_NUM_RE.search(strip_accents(text.lower())))


def strip_conversational_prefix(text: str) -> str:
    """Limpia ruido conversacional al inicio de un transcript de dirección.

    1. Quita saludos iniciales ("buenas tardes", "hola", "aló"...).
    2. Quita un nombre propio suelto al inicio que precede a una dirección
       reconocible (ej. "osvaldo valle del ortigal" → "valle del ortigal").

    Reglas de seguridad:
    - Si el texto (sin saludo) ya pasa looks_like_place o is_street, se devuelve
      tal cual — no se toca lo que ya funciona.
    - Si tras limpiar el resultado queda vacío o sin contenido de lugar, se
      devuelve el original. Nunca degrada.
    """
    if not text or not text.strip():
        return text
    original = text.strip()

    # Import perezoso: address_utils importa de este módulo (ciclo si fuera top).
    try:
        from core.address_utils import looks_like_place
    except Exception:
        return text

    def _is_place(s: str) -> bool:
        return bool(s and s.strip()) and (looks_like_place(s) or _is_street(s))

    # 1) Quitar saludos iniciales (posiblemente varios).
    work = original
    while True:
        nxt = _GREETING_PREFIX_RE.sub("", work, count=1).strip()
        if nxt == work or not nxt:
            if not nxt:
                work = ""
            else:
                work = nxt
            break
        work = nxt

    # Saludo se comió todo el texto → no había dirección. Original intacto.
    if not work:
        return original

    # 2) Si lo que queda ya parece un lugar, devolverlo (limpio de saludo).
    if _is_place(work):
        return work

    # 3) Nombre propio suelto al inicio: si el primer token no es vía ni lugar
    #    conocido y el resto SÍ es una dirección reconocible, descartarlo.
    tokens = work.split()
    if len(tokens) >= 2:
        first_norm = strip_accents(tokens[0].lower()).strip(".,;:")
        rest = " ".join(tokens[1:]).strip()
        if (
            first_norm not in _VIA_KEYWORDS
            and not _is_place(tokens[0])
            and _is_place(rest)
        ):
            return rest

    # 4) Nada seguro que limpiar → nunca degradar.
    return original


# ── Reparación fonética de transcripción de ubicaciones ────────────────────────
#
# Capa ANTERIOR al resolver. Repara la GRAFÍA de nombres de lugar mal transcritos
# por el STT usando los catálogos (BARRIO_ALIASES, LANDMARKS, HUMAN_REFERENCES),
# para que el resolver reciba texto limpio y matchee a EXACT/ALIAS (alta
# precisión). NO relaja el resolver: ataca el problema en la etapa de
# transcripción. Generaliza el dict literal POPAYAN_STT_CORRECTIONS a los ~600
# lugares del catálogo sin enumerarlos a mano.
#
# Guardas estrictas para no corromper texto normal ni colisionar lugares
# parecidos (ej. "valle"↔"villa"): snap SOLO si la similitud fonética ≥ 0.90 Y
# el match apunta a UNA sola entidad (sin ambigüedad).

_REPAIR_MIN_SIM = 0.90       # similitud fonética mínima para reparar
_REPAIR_MIN_LEN = 4          # longitud mínima (sin espacios) del span candidato

_PHON_REPAIR_LOCK = threading.Lock()
# bucket por primer char de la clave fonética → [(phon_key, alias_norm, canonical)]
_PHON_REPAIR_INDEX: Optional[dict] = None


def _build_phonetic_repair_index() -> None:
    global _PHON_REPAIR_INDEX
    if _PHON_REPAIR_INDEX is not None:
        return
    with _PHON_REPAIR_LOCK:
        if _PHON_REPAIR_INDEX is not None:
            return

        # bucket → [(phon_key, alias_norm, canonical, word_count)]
        index: dict[str, list[tuple[str, str, str, int]]] = {}

        def _add(alias: str, canonical: str) -> None:
            a = strip_accents(alias.lower().strip())
            if len(a.replace(" ", "")) < _REPAIR_MIN_LEN:
                return
            pk = phonetic_key(a)
            if not pk:
                return
            wc = len(a.split())
            index.setdefault(pk[0], []).append((pk, a, canonical, wc))

        # HUMAN_REFERENCES (canónico + aliases)
        for data in HUMAN_REFERENCES.values():
            canonical = data["canonical"]
            _add(canonical, canonical)
            for alias in data.get("aliases", []):
                _add(alias, canonical)

        # Catálogo local de barrios + landmarks
        try:
            from tools.popayan_geodata import BARRIO_ALIASES, LANDMARKS
            for canonical, aliases in BARRIO_ALIASES.items():
                _add(canonical, canonical)
                for alias in aliases:
                    _add(alias, canonical)
            for name in LANDMARKS:
                _add(name, name)
        except ImportError:
            pass

        _PHON_REPAIR_INDEX = index


def _best_catalog_snap(span_norm: str) -> Optional[str]:
    """Devuelve la grafía CORRECTA del alias (mismo número de palabras) si
    `span_norm` matchea fonéticamente, de forma ALTA y ÚNICA, una entidad del
    catálogo. None si no hay match seguro, si es ambiguo entre entidades
    distintas, o si el span ya está bien escrito.

    Repara la GRAFÍA preservando el número de palabras (no expande un token a un
    nombre completo — de eso ya se encarga el resolver vía alias→canónico). Así
    'villa del karmen' → 'villa del carmen', pero 'carmen' dentro de 'villa del
    carmen' no se toca (ya es un alias correcto)."""
    pk = phonetic_key(span_norm)
    if not pk:
        return None
    span_wc = len(span_norm.split())
    bucket = _PHON_REPAIR_INDEX.get(pk[0], ())
    best_sim = 0.0
    best_alias = None
    entities: set[str] = set()
    for cand_pk, alias_norm, canonical, wc in bucket:
        # Solo alias del MISMO número de palabras: evita que un token suelto
        # ("carmen") matchee un sub-fragmento de un alias largo y se duplique.
        if wc != span_wc:
            continue
        sim = bigram_similarity(pk, cand_pk)
        if sim >= _REPAIR_MIN_SIM:
            entities.add(canonical)
            if sim > best_sim:
                best_sim, best_alias = sim, alias_norm
    if not best_alias or len(entities) != 1:
        return None  # sin match o ambiguo entre entidades → no reparar
    if span_norm == best_alias:
        return None  # ya está bien escrito
    return best_alias


def repair_location_transcription(text: str) -> str:
    """Repara la grafía de nombres de lugar en `text` usando el catálogo, vía
    similitud fonética con guardas estrictas. Reemplaza spans (n-gramas 3→2→1,
    sin solapamiento, más largos primero) que matcheen una entidad de forma alta
    y única. Devuelve el texto reparado (o el original si no hay reparación
    segura)."""
    if not text:
        return text
    _build_phonetic_repair_index()
    if not _PHON_REPAIR_INDEX:
        return text

    words = text.split()
    n = len(words)
    if n == 0:
        return text
    norm_words = [strip_accents(w.lower()) for w in words]

    used = [False] * n
    # start → (size, canonical)
    repls: dict[int, tuple[int, str]] = {}

    for size in (3, 2, 1):
        for start in range(0, n - size + 1):
            if any(used[start:start + size]):
                continue
            span_norm = " ".join(norm_words[start:start + size]).strip()
            if len(span_norm.replace(" ", "")) < _REPAIR_MIN_LEN:
                continue
            # No tocar spans que son puro relleno (sin token de contenido).
            if all(tok in _COVERAGE_STOPWORDS for tok in span_norm.split()):
                continue
            corrected = _best_catalog_snap(span_norm)
            if corrected:
                repls[start] = (size, corrected)
                for k in range(start, start + size):
                    used[k] = True

    if not repls:
        return text

    out: list[str] = []
    i = 0
    while i < n:
        if i in repls:
            size, corrected = repls[i]
            out.append(corrected)
            i += size
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


# ── Expansión de número-palabras ──────────────────────────────────────────────

_NUM_WORDS_MAP = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidós": 22,
    "veintidos": 22, "veintitrés": 23, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiséis": 26, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
}

_NUM_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(_NUM_WORDS_MAP.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_STREET_NUM_CONTEXT_RE = re.compile(
    r"\b(calle|carrera|cl|cra|kr|kra|k)\s+(" + "|".join(sorted(_NUM_WORDS_MAP.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def expand_number_words_in_streets(text: str) -> str:
    """
    Convierte palabras-número a dígitos solo cuando están en contexto de calle/carrera.
    'calle quince' → 'calle 15'
    'carrera nueve' → 'carrera 9'
    NO convierte: 'en cinco minutos', 'la una' etc.
    """
    def replace_street_num(m: re.Match) -> str:
        street = m.group(1)
        num_word = m.group(2).lower()
        num = _NUM_WORDS_MAP.get(num_word, num_word)
        return f"{street} {num}"

    return _STREET_NUM_CONTEXT_RE.sub(replace_street_num, text)


_STREET_LETTER_BLACKLIST = frozenset({
    # palabras españolas comunes que NO son códigos de nomenclatura
    "numero", "número", "norte", "sur", "este", "oeste", "entre", "con",
    "de", "del", "la", "el", "los", "las", "por", "barrio", "esquina",
    "bis", "interior", "edificio", "torre", "piso", "apto", "apartamento",
    "local", "oficina", "casa", "bloque", "manzana", "lote",
})


def repair_mangled_street_address(text: str) -> str:
    """
    Repara direcciones callejeras mangled por STT.
    'carrera 4 a eb 1728' → 'carrera 4a # 17b 28'
    'calle 5 a 12 34' → 'calle 5a # 12 34'
    Detecta: (calle|carrera) + número + letra(s) cortas + número(s) mangled
    NO aplica si la "letra" es una palabra española común (número, norte, etc.)
    """
    t = text.strip()

    # Patrón: (calle|carrera) + número + espacio? + letra(s) + espacio + número(s) mangled
    pattern = r'((?:calle|carrera|cl|cra|cr|kr|kra|k)\s+\d+)\s+([a-záéíóú]+(?:\s+[a-záéíóú]+)*)\s+(\d+)'
    match = re.search(pattern, t, re.IGNORECASE)

    if match:
        letters_raw = match.group(2).strip().lower()
        # Si la "letra" es una palabra española, no es código de nomenclatura — ignorar
        if letters_raw in _STREET_LETTER_BLACKLIST or len(letters_raw) > 4:
            return t

        prefix = match.group(1)
        letters = match.group(2)
        numbers = match.group(3)

        # Limpiar letras: "a eb" → "ae", "a e b" → "ae"
        # "eb" es STT mangling de "b" (letra del apartamento) o parte de "ae"
        # Primero: unir espacios → "aeb"
        clean_letters = re.sub(r'\s+', '', letters.lower())
        # Si es "aeb" → "ae" (STT separó "ae" en "a eb")
        if clean_letters == "aeb":
            clean_letters = "ae"
        elif clean_letters.endswith("eb"):
            # "xeb" → "xb" (eb es mangling de b)
            clean_letters = clean_letters[:-2] + "b"
        elif clean_letters.startswith("e"):
            # "eb" → "b"
            clean_letters = clean_letters[1:]

        # Intentar separar números mangled: "1728" → "17b 28" o "17 28"
        # Si hay 4+ dígitos, probablemente son 2 números
        if len(numbers) >= 4:
            # Dividir a la mitad: "1728" → "17" y "28"
            mid = len(numbers) // 2
            num1 = numbers[:mid]
            num2 = numbers[mid:]
            repaired = f"{prefix}{clean_letters} # {num1}b {num2}"
        elif len(numbers) == 3:
            # "172" → "17" y "2"
            repaired = f"{prefix}{clean_letters} # {numbers[:2]} {numbers[2:]}"
        else:
            repaired = f"{prefix}{clean_letters} # {numbers}"

        t = t[:match.start()] + repaired + t[match.end():]

    return t


# ── Detección de calidad de audio ─────────────────────────────────────────────

class AudioQualityProfile:
    """
    Perfil de calidad de audio de una llamada.
    Se actualiza turn a turn para adaptar parámetros de VAD/endpointing.
    """

    def __init__(self):
        self.confidence_history: list[float] = []
        self.word_count_history: list[int]   = []
        self.silence_count:      int          = 0
        self.retry_count:        int          = 0
        self.total_turns:        int          = 0

    def update(self, confidence: float, text: str) -> None:
        self.total_turns += 1
        self.confidence_history.append(confidence)
        self.word_count_history.append(len(text.split()) if text else 0)
        # Mantener solo las últimas 5 muestras
        if len(self.confidence_history) > 5:
            self.confidence_history.pop(0)
        if len(self.word_count_history) > 5:
            self.word_count_history.pop(0)

    @property
    def avg_confidence(self) -> float:
        if not self.confidence_history:
            return 1.0
        return sum(self.confidence_history) / len(self.confidence_history)

    @property
    def avg_word_count(self) -> float:
        if not self.word_count_history:
            return 5.0
        return sum(self.word_count_history) / len(self.word_count_history)

    @property
    def is_noisy_call(self) -> bool:
        """True si la llamada tiene calidad consistentemente baja."""
        return self.avg_confidence < 0.40 and self.total_turns >= 2

    @property
    def is_fast_speaker(self) -> bool:
        """True si el usuario habla en frases largas (muchas palabras por turno)."""
        return self.avg_word_count > 8

    @property
    def is_slow_speaker(self) -> bool:
        """True si el usuario usa frases muy cortas."""
        return self.avg_word_count < 3 and self.total_turns >= 2

    def recommended_speech_timeout(self) -> str:
        """
        Recomienda el speechTimeout de Twilio basado en el perfil del usuario.
        - Rápido: más tiempo para no cortar frases
        - Lento: tiempo estándar (Twilio detecta silencio bien)
        - Ruidoso: más tiempo para acumular contexto
        """
        if self.is_noisy_call or self.is_fast_speaker:
            return "2.0"
        if self.is_slow_speaker:
            return "1.2"
        return "1.5"  # default mejorado vs "1.0" original

    def recommended_gather_timeout(self) -> int:
        """Timeout total de <Gather> en segundos."""
        if self.is_slow_speaker:
            return 30
        return 25

    def quality_label(self) -> str:
        if self.avg_confidence >= 0.65:
            return "high"
        if self.avg_confidence >= 0.35:
            return "medium"
        return "low"