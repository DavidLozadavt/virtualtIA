"""
core/location_match.py — Resolución de ubicaciones tipada y precision-first.

Reemplaza la cadena de matchers ad-hoc (cada uno con su umbral hardcodeado y que
devolvía solo un string) por un único primitivo tipado `LocationMatch` y una
política de decisión central `decide()`.

Distingue explícitamente el TIPO de coincidencia (exacta / alias / substring /
fonética / fuzzy) y aplica umbrales por tipo. La prioridad de tipo impide que una
coincidencia fonética sobreescriba una coincidencia textual (anti-falso-positivo).
Coincidencias de confianza media → CONFIRM (verificar con el usuario) en vez de
asumir. Entidades multi-sede → AMBIGUOUS (data-driven, sin reglas hardcodeadas).

Reutiliza las primitivas de scoring y los catálogos existentes; no reimplementa
similitud.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Optional

from core.stt_enhancer import (
    strip_accents,
    phonetic_key,
    bigram_similarity,
    combined_score,
    _alias_covers_input,
    _COVERAGE_STOPWORDS,
    HUMAN_REFERENCES,
    POPAYAN_STT_CORRECTIONS,
    DISAMBIGUATION_GROUPS,
)


# ── Tipos ──────────────────────────────────────────────────────────────────────

class MatchType(IntEnum):
    """Orden = prioridad. Un tipo superior siempre vence a uno inferior aunque
    el escalar de confianza del inferior sea mayor (regla anti-override)."""
    NONE        = 0
    FUZZY       = 1   # similitud combinada (bigrama+fonético+secuencia)
    PHONETIC    = 2   # clave fonética coincide
    SUBSTRING   = 3   # alias es span de palabra completa del input, con cobertura
    ALIAS_EXACT = 4   # == alias curado / corrección STT exacta
    EXACT       = 5   # == nombre canónico normalizado


class Decision(Enum):
    ACCEPT    = "accept"      # fijar ubicación (trusted)
    CONFIRM   = "confirm"     # preguntar "¿Te refieres a X?"
    AMBIGUOUS = "ambiguous"   # ofrecer opciones (ej. Norte/Centro)
    REJECT    = "reject"      # caer a LLM / context gathering


@dataclass
class LocationMatch:
    canonical: Optional[str]
    match_type: MatchType = MatchType.NONE
    confidence: float = 0.0
    evidence: str = ""
    score_breakdown: dict = field(default_factory=dict)
    runner_up: Optional[str] = None
    margin: float = 1.0
    needs_disambiguation: bool = False
    disambiguation_candidates: list[str] = field(default_factory=list)
    lat: Optional[float] = None
    lon: Optional[float] = None


# ── Política (precision-first, calibrada con casos reales de prod) ──────────────

PHONETIC_ACCEPT = 0.85
PHONETIC_CONFIRM = 0.70
FUZZY_ACCEPT = 0.85
FUZZY_CONFIRM = 0.62
MIN_MARGIN = 0.08          # ventaja mínima sobre la 2da entidad para auto-aceptar
MIN_CONTENT_LEN = 4        # un token de "contenido" real
SUBSTRING_CONF = 0.90
COVERAGE_TOKEN_SIM = 0.70  # umbral para considerar "cubierto" un token de input


_APPROX_RANK = 1  # PHONETIC y FUZZY comparten rango: entre sí gana la confianza.


def _rank(mt: MatchType) -> int:
    """Rango de prioridad para comparar candidatos. Los tipos textuales
    (SUBSTRING/ALIAS_EXACT/EXACT) dominan a los aproximados; entre PHONETIC y
    FUZZY no hay prioridad de tipo, decide la confianza."""
    return int(mt) if mt >= MatchType.SUBSTRING else _APPROX_RANK


def _key(mt: MatchType, conf: float) -> tuple[int, float]:
    return (_rank(mt), conf)


def _base_decision(m: LocationMatch) -> Decision:
    """Decisión por tipo+confianza, SIN considerar desambiguación. Una
    coincidencia fonética/fuzzy débil es REJECT aunque la entidad sea ambigua —
    así un fragmento como 'buenas' (fuzzy 0.44 contra 'sena') NUNCA escala a
    AMBIGUOUS ni a una ubicación."""
    if m is None or m.match_type == MatchType.NONE or not m.canonical:
        return Decision.REJECT
    if m.match_type in (MatchType.EXACT, MatchType.ALIAS_EXACT, MatchType.SUBSTRING):
        return Decision.ACCEPT
    if m.match_type == MatchType.PHONETIC:
        if m.confidence >= PHONETIC_ACCEPT and m.margin >= MIN_MARGIN:
            return Decision.ACCEPT
        if m.confidence >= PHONETIC_CONFIRM:
            return Decision.CONFIRM
        return Decision.REJECT
    # FUZZY
    if m.confidence >= FUZZY_ACCEPT and m.margin >= MIN_MARGIN:
        return Decision.ACCEPT
    if m.confidence >= FUZZY_CONFIRM:
        return Decision.CONFIRM
    return Decision.REJECT


def decide(m: LocationMatch) -> Decision:
    """Mapea un LocationMatch a la acción del flujo. Precisión sobre recall.

    La desambiguación SOLO se ofrece cuando el match base ya es suficientemente
    fuerte (ACCEPT/CONFIRM). Si el match base es REJECT, gana REJECT aunque la
    entidad esté marcada needs_disambiguation."""
    base = _base_decision(m)
    if base == Decision.REJECT:
        return Decision.REJECT
    if m.needs_disambiguation:
        return Decision.AMBIGUOUS
    return base


# ── Catálogo unificado de entidades ────────────────────────────────────────────

@dataclass
class _Entity:
    canonical: str
    aliases: set[str] = field(default_factory=set)   # normalizados
    lat: Optional[float] = None
    lon: Optional[float] = None
    needs_disambiguation: bool = False
    group_members: list[str] = field(default_factory=list)  # canónicos de sedes


_LOCK = threading.Lock()
_ENTITIES: Optional[dict[str, _Entity]] = None         # key: canonical normalizado
_CORRECTIONS_NORM: Optional[dict[str, str]] = None     # wrong_norm → canonical
_HR_KEY_TO_CANONICAL: dict[str, str] = {}              # HR key → canonical
_ALIAS_TO_CANONICAL: Optional[dict[str, str]] = None   # alias_norm → canonical (match exacto)


def _norm(s: str) -> str:
    return strip_accents(s.lower().strip())


def _build_catalog() -> None:
    global _ENTITIES, _CORRECTIONS_NORM, _ALIAS_TO_CANONICAL
    if _ENTITIES is not None:
        return
    with _LOCK:
        if _ENTITIES is not None:
            return

        entities: dict[str, _Entity] = {}

        def _get(canonical: str) -> _Entity:
            key = _norm(canonical)
            ent = entities.get(key)
            if ent is None:
                ent = _Entity(canonical=canonical)
                ent.aliases.add(key)
                entities[key] = ent
            return ent

        # 1. HUMAN_REFERENCES (lat/lon + grupos de desambiguación)
        for hr_key, data in HUMAN_REFERENCES.items():
            canonical = data["canonical"]
            _HR_KEY_TO_CANONICAL[hr_key] = canonical
            ent = _get(canonical)
            ent.lat = data.get("lat", ent.lat)
            ent.lon = data.get("lon", ent.lon)
            for alias in data.get("aliases", []):
                ent.aliases.add(_norm(alias))
            if data.get("needs_disambiguation"):
                ent.needs_disambiguation = True

        # 1b. Resolver miembros de grupos (HR keys → canónicos)
        for base_hr_key, member_keys in DISAMBIGUATION_GROUPS.items():
            base_canonical = _HR_KEY_TO_CANONICAL.get(base_hr_key)
            if not base_canonical:
                continue
            ent = entities.get(_norm(base_canonical))
            if not ent:
                continue
            ent.needs_disambiguation = True
            ent.group_members = [
                _HR_KEY_TO_CANONICAL[m] for m in member_keys
                if m in _HR_KEY_TO_CANONICAL
            ]

        # 2. Catálogo local de barrios + landmarks
        try:
            from tools.popayan_geodata import BARRIO_ALIASES, LANDMARKS
            for canonical, aliases in BARRIO_ALIASES.items():
                ent = _get(canonical)
                for alias in aliases:
                    ent.aliases.add(_norm(alias))
            for name, coords in LANDMARKS.items():
                ent = _get(name)
                if isinstance(coords, (list, tuple)) and len(coords) == 2:
                    ent.lat = ent.lat if ent.lat is not None else coords[0]
                    ent.lon = ent.lon if ent.lon is not None else coords[1]
        except ImportError:
            pass

        # 3. Correcciones STT exactas → canónico (si el destino mapea a entidad)
        corrections: dict[str, str] = {}
        for wrong, right in POPAYAN_STT_CORRECTIONS.items():
            right_norm = _norm(right)
            target = entities.get(right_norm)
            corrections[_norm(wrong)] = target.canonical if target else right
        _CORRECTIONS_NORM = corrections

        # Índice de alias exacto → canónico. Permite que un nombre corto pero
        # real (p. ej. "la paz", "la paz sur") supere el filtro _has_content
        # (MIN_CONTENT_LEN=4 mata tokens de 3 letras como "paz"/"sur").
        alias_index: dict[str, str] = {}
        for ent in entities.values():
            for a in ent.aliases:
                alias_index.setdefault(a, ent.canonical)
        _ALIAS_TO_CANONICAL = alias_index

        _ENTITIES = entities


# ── Cobertura por token (penaliza inflación por prefijo compartido) ─────────────

# Cortesía / relleno / muletillas / afirmaciones. Si TODOS los tokens de
# contenido de un input están aquí, no hay lugar reconocible → NONE. Nunca deben
# generar una ubicación (requisito explícito). Normalizados (sin tildes).
_FILLER_TOKENS = frozenset({
    "buenas", "buenos", "buen", "buena", "bueno",
    "hola", "alo", "olis", "oiga", "oigan", "hey",
    "gracias", "favor", "porfa", "porfavor", "amable", "cordial",
    "muchas", "mucho", "mucha", "muchos",
    "tardes", "dias", "noches", "saludos", "saludo",
    "ok", "oka", "si", "no", "ya", "va", "aja", "eh", "ah", "mmm", "uy",
    "listo", "vale", "dale", "okey", "okay", "claro", "exacto",
    "correcto", "perfecto", "afirmativo", "negativo", "negado",
    "este", "pues", "entonces", "bueeno", "ajam", "ajah",
    "señor", "senor", "senora", "señora", "senorita", "joven", "muchacho",
    "quiero", "quisiera", "necesito", "deseo", "favorr",
    "taxi", "carro", "movil", "servicio", "viaje", "carrera",
}) | _COVERAGE_STOPWORDS


def _is_all_filler(norm_text: str) -> bool:
    """True si el texto solo contiene cortesía/relleno (ningún token de lugar).
    'buenos aires' sobrevive (aires no es relleno); 'buenas tardes' no."""
    toks = [t for t in norm_text.split() if t]
    if not toks:
        return True
    return all(t in _FILLER_TOKENS for t in toks)


def is_filler(text: str) -> bool:
    """API pública: True si `text` es solo cortesía/relleno/afirmación y no
    contiene ninguna señal de ubicación (saludo, gracias, sí/no, etc.). Los
    callers la usan para NO invocar extractores/LLM sobre saludos."""
    if not text:
        return True
    return _is_all_filler(_norm(text))


def _content_tokens(norm_text: str) -> list[str]:
    return [
        t for t in norm_text.split()
        if t not in _COVERAGE_STOPWORDS and len(t) >= 3
    ]


def _token_coverage(input_norm: str, cand_norm: str) -> float:
    """Fracción de tokens de contenido del input que encuentran un equivalente
    fuerte en el candidato. Para input de 1 token devuelve 1.0 (no penaliza
    misspellings de una palabra como 'campanaryo'). Para multi-token penaliza
    'villa del viento' vs 'villa del carmen' (token 'viento' sin equivalente)."""
    in_tokens = _content_tokens(input_norm)
    if len(in_tokens) <= 1:
        return 1.0
    cand_tokens = [t for t in cand_norm.split() if len(t) >= 3]
    if not cand_tokens:
        return 1.0
    matched = 0
    for it in in_tokens:
        best = max(
            (1.0 if it == ct else combined_score(it, ct)) for ct in cand_tokens
        )
        if best >= COVERAGE_TOKEN_SIM:
            matched += 1
    return matched / len(in_tokens)


# ── Resolución principal ────────────────────────────────────────────────────────

def _has_content(norm_text: str) -> bool:
    return any(len(t) >= MIN_CONTENT_LEN for t in _content_tokens(norm_text))


def catalog_terms(limit: int = 60) -> list[str]:
    """Nombres canónicos del catálogo local (barrios + landmarks de Popayán).

    Pensado para sesgar el STT (parámetro `prompt` de Whisper/gpt-4o-transcribe)
    con vocabulario propio de la ciudad. Devuelve canónicos deduplicados, sin
    importar el catálogo más de una vez (build cacheado)."""
    _build_catalog()
    if not _ENTITIES:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for ent in _ENTITIES.values():
        name = (ent.canonical or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def resolve_location_entity(
    text: str,
    scope: Optional[list[str]] = None,
) -> LocationMatch:
    """Resuelve `text` a una entidad del catálogo con tipo y confianza.

    scope: lista de nombres canónicos a los que restringir la búsqueda (ej. las
    sedes de un grupo de desambiguación). Cuando se da, NO se vuelve a marcar
    needs_disambiguation y se usan además los tokens distintivos de cada sede
    (data-driven) para resolver respuestas como "el del norte" / "centro".
    """
    _build_catalog()
    if not text:
        return LocationMatch(canonical=None)

    try:
        from core.address_utils import _strip_preamble
        base = _strip_preamble(text)
    except Exception:
        base = text
    t = _norm(base)
    if len(t) < 2:
        return LocationMatch(canonical=None)

    # Cortesía / relleno → ninguna ubicación (mata "buenas"→SENA, "hola"→…).
    if _is_all_filler(t):
        return LocationMatch(canonical=None, evidence=t)

    if scope:
        return _resolve_scoped(t, scope)

    # Rechazar relleno puro (sin token de contenido) — mata "en el" → SENA.
    # Excepción: si el input completo es un alias exacto del catálogo, es un
    # lugar real aunque sus tokens sean cortos ("la paz", "la paz sur").
    if not (_ALIAS_TO_CANONICAL and t in _ALIAS_TO_CANONICAL) and not _has_content(t):
        return LocationMatch(canonical=None, evidence=t)

    # 1. Corrección STT exacta → ALIAS_EXACT
    if _CORRECTIONS_NORM and t in _CORRECTIONS_NORM:
        canonical = _CORRECTIONS_NORM[t]
        ent = _ENTITIES.get(_norm(canonical))
        return LocationMatch(
            canonical=canonical,
            match_type=MatchType.ALIAS_EXACT,
            confidence=1.0,
            evidence=t,
            lat=ent.lat if ent else None,
            lon=ent.lon if ent else None,
            needs_disambiguation=ent.needs_disambiguation if ent else False,
            disambiguation_candidates=list(ent.group_members) if ent else [],
        )

    # 2. Mejor match por entidad
    best: Optional[LocationMatch] = None
    second_conf = 0.0
    for ent in _ENTITIES.values():
        cand = _best_for_entity(t, ent)
        if cand is None:
            continue
        if best is None or _key(cand.match_type, cand.confidence) > _key(best.match_type, best.confidence):
            if best is not None and best.canonical != cand.canonical:
                second_conf = max(second_conf, best.confidence)
            best = cand
        elif cand.canonical != best.canonical:
            second_conf = max(second_conf, cand.confidence)

    if best is None:
        return LocationMatch(canonical=None, evidence=t)

    best.margin = round(best.confidence - second_conf, 4)
    return best


def _best_for_entity(t: str, ent: _Entity) -> Optional[LocationMatch]:
    """Mejor coincidencia (tipo, confianza) de un input contra los alias de una
    entidad. Solo coincidencia hacia adelante (alias dentro del input); la rama
    inversa insegura (input ⊂ alias) se elimina a propósito."""
    best_type = MatchType.NONE
    best_conf = 0.0
    best_alias = ""
    breakdown: dict = {}

    for alias in ent.aliases:
        if not alias:
            continue

        # EXACT / ALIAS_EXACT
        if t == alias:
            mt = MatchType.EXACT if alias == _norm(ent.canonical) else MatchType.ALIAS_EXACT
            if _key(mt, 1.0) > _key(best_type, best_conf):
                best_type, best_conf, best_alias = mt, 1.0, alias
            continue

        # SUBSTRING (alias = span de palabra completa del input, con cobertura)
        if len(alias) >= MIN_CONTENT_LEN and re.search(r"\b" + re.escape(alias) + r"\b", t):
            if _alias_covers_input(alias, t):
                if _key(MatchType.SUBSTRING, SUBSTRING_CONF) > _key(best_type, best_conf):
                    best_type, best_conf, best_alias = MatchType.SUBSTRING, SUBSTRING_CONF, alias
                continue

        cov = _token_coverage(t, alias)

        # PHONETIC
        ph_scaled = bigram_similarity(phonetic_key(t), phonetic_key(alias)) * cov
        if ph_scaled > 0 and _key(MatchType.PHONETIC, ph_scaled) > _key(best_type, best_conf):
            best_type, best_conf, best_alias = MatchType.PHONETIC, ph_scaled, alias
            breakdown = {"phonetic": round(ph_scaled, 3), "coverage": round(cov, 3)}

        # FUZZY
        fz_scaled = combined_score(t, alias) * cov
        if fz_scaled > 0 and _key(MatchType.FUZZY, fz_scaled) > _key(best_type, best_conf):
            best_type, best_conf, best_alias = MatchType.FUZZY, fz_scaled, alias
            breakdown = {"fuzzy": round(fz_scaled, 3), "coverage": round(cov, 3)}

    if best_type == MatchType.NONE:
        return None

    return LocationMatch(
        canonical=ent.canonical,
        match_type=best_type,
        confidence=round(best_conf, 4),
        evidence=best_alias,
        score_breakdown=breakdown,
        needs_disambiguation=ent.needs_disambiguation,
        disambiguation_candidates=list(ent.group_members),
        lat=ent.lat,
        lon=ent.lon,
    )


def _resolve_scoped(t: str, scope: list[str]) -> LocationMatch:
    """Resuelve una respuesta de desambiguación contra un conjunto cerrado de
    sedes. Usa alias normales + los tokens DISTINTIVOS de cada canónico (los que
    no comparte con las otras sedes del grupo). Generalizable a cualquier grupo."""
    members = [_ENTITIES.get(_norm(c)) for c in scope]
    members = [m for m in members if m]
    if not members:
        return LocationMatch(canonical=None, evidence=t)

    # Tokens de contenido por sede y los compartidos por todas.
    per_member_tokens = []
    for m in members:
        toks = set(_content_tokens(_norm(m.canonical)))
        per_member_tokens.append(toks)
    shared = set.intersection(*per_member_tokens) if per_member_tokens else set()

    reply_tokens = set(_content_tokens(t)) | set(
        w for w in t.split() if w not in _COVERAGE_STOPWORDS
    )

    # 1. Token distintivo presente en la respuesta → ALIAS_EXACT
    for m, toks in zip(members, per_member_tokens):
        distinctive = toks - shared
        if distinctive & reply_tokens:
            return LocationMatch(
                canonical=m.canonical,
                match_type=MatchType.ALIAS_EXACT,
                confidence=0.95,
                evidence=" ".join(distinctive & reply_tokens),
                lat=m.lat, lon=m.lon,
            )

    # 2. Match normal restringido a las sedes
    best: Optional[LocationMatch] = None
    for m in members:
        cand = _best_for_entity(t, m)
        if cand and (best is None or _key(cand.match_type, cand.confidence) > _key(best.match_type, best.confidence)):
            best = cand
    if best:
        best.needs_disambiguation = False
        best.disambiguation_candidates = []
        best.margin = 1.0
        return best

    return LocationMatch(canonical=None, evidence=t)
