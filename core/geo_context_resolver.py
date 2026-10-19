# core/geo_context_resolver.py
"""
Geographic context resolver — disambiguation-only.

Responsibility (conversational resolution layer, BEFORE the final geocoder query):
when a VALID address already produced several compatible candidates and the user
answers the disambiguation question with a NATURAL reference (a barrio nickname, a
bridge, a landmark) instead of the official barrio name, decide which of the
**already-found candidates** best matches — WITHOUT starting a new search and
WITHOUT ever replacing or concatenating the address.

Hard boundaries (do NOT cross — spec §"Contract invariants"):
  - Never modifies the address / normalization / parser / STT / geocoder engine.
  - Never issues a Google query. Proximity/landmark use the LOCAL Popayán/Cauca
    catalog only.
  - The universe is exactly the candidate list passed in (closed & immutable). It
    only *selects*; it never invents a candidate, mutates, reorders, nor opens a
    new search.
  - The user's answer is EVIDENCE to pick among candidates, never a new query.
  - PRECISION over recall: false negatives are acceptable, false positives are not.
    On any reasonable doubt → return None (caller re-asks between candidate names).

Selection priority (fixed; each stage runs only if the previous is inconclusive;
the first valid selection short-circuits the rest):
  1. DIRECT     — the answer explicitly names a candidate's neighborhood.
  2. PROXIMITY  — the reference resolves to a known BARRIO (ALL_BARRIOS); pick the
                  candidate geographically nearest (haversine) within conservative
                  gates.
  3. LANDMARK   — the reference resolves to a known LANDMARK (LANDMARKS) and is not
                  itself an address; pick the nearest candidate with the SAME gates.
  4. COMUNA     — the reference barrio's comuna uniquely matches one candidate.
  5. TOKEN      — token overlap between the answer and a candidate's name.
If none is decisive, returns None and the caller re-asks between the candidate
names (still never concatenating the raw answer).

Every automatic selection is auditable: the returned Selection carries the full
decision trace (method, score, confidence, margin, per-candidate distances,
discarded candidates, evidence and exact reason) and `to_trace()` serializes it
so the decision is reproducible from session data alone.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger("lyra.core.geo_context_resolver")

# Tunables (km). Kept conservative so an ambiguous tie returns None → re-ask.
PROXIMITY_MIN_MARGIN_KM = 0.30   # nearest must beat the 2nd candidate by this
PROXIMITY_MAX_ACCEPT_KM = 4.00   # beyond this the reference is too far to trust
TOKEN_MIN_MARGIN = 0.20

# Cheap guard: a reference that is itself a street address must not enter the
# LANDMARK stage (spec §"Landmark-based disambiguation"). Detection only — never
# parses/normalizes/reconstructs the address (that is the parser's sole job).
_ADDRESS_LIKE_RE = re.compile(
    r"(?:\bcalle\b|\bcarrera\b|\bcra\b|\bcl\b|\bkr\b|\bkra\b|\bdiag\b|\btrans\b|"
    r"\bavenida\b|\bav\b|#|\bnro\b|\bnúmero\b|\bnumero\b)\s*\.?\s*\d",
    re.IGNORECASE,
)

# A display_name segment that is a street line (via abbrev, "#", or a bare number)
# rather than a barrio name — used to skip it when deriving a candidate's name.
_STREET_SEGMENT_RE = re.compile(
    r"(?:^|\s)(?:cl|cra|cr|kr|kra|av|ave|avenida|calle|carrera|diag|diagonal|"
    r"trans|transv|transversal|tr)\.?\s*\d|#|\d+\s*-\s*\d",
    re.IGNORECASE,
)


@dataclass
class Selection:
    neighborhood: str          # official barrio of the chosen candidate
    lat: Optional[float]
    lng: Optional[float]
    method: str                # "direct_name" | "proximity" | "landmark" | "comuna" | "token"
    score: float               # 0..1
    confidence: float          # 0..1 — decision confidence level
    margin: float              # separation from the runner-up (km or token units)
    matched_reference: str     # what in the answer drove the choice (evidence)
    distances: dict = field(default_factory=dict)   # {candidate_name: km} when computed
    discarded: list = field(default_factory=list)   # names of candidates not selected
    reason: str = ""           # exact human-readable reason for the selection

    def to_trace(self) -> dict:
        """Fully serializable decision trace (reproducible from session data)."""
        return asdict(self)


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    return " ".join(s.split())


def _cand_name(c: dict) -> str:
    nb = c.get("neighborhood")
    if nb:
        return nb
    disp = c.get("display_name") or ""
    parts = [p.strip() for p in disp.split(",") if p.strip()]
    # Prefer the first segment that is neither a city/region nor a street line —
    # the candidate NAME must be a barrio, not the door address.
    for p in parts:
        if _norm(p) in ("popayan", "cauca", "colombia"):
            continue
        if _STREET_SEGMENT_RE.search(p):
            continue
        return p
    # Fallback: first non-city segment (even if street-like), else truncated disp.
    for p in parts:
        if _norm(p) not in ("popayan", "cauca", "colombia"):
            return p
    return disp[:40]


def _others(candidates: list, chosen: dict) -> list:
    """Names of the candidates that were NOT selected (for the audit trace)."""
    return [_cand_name(c) for c in candidates if c is not chosen]


def _resolve_in_catalog(reference_text: str, catalog: dict):
    """Resolve a natural reference to LOCAL coords + canonical name, no Google.

    Restricted to a single catalog (ALL_BARRIOS for PROXIMITY, LANDMARKS for
    LANDMARK) so the two stages stay cleanly separated. First tries the shared
    typed matcher (`address_utils._try_local_match`); if its canonical belongs to
    this catalog it wins, otherwise falls back to the longest catalog name that
    appears as a substring of the reference.
    """
    try:
        from core.address_utils import _try_local_match
    except Exception:  # pragma: no cover - defensive
        _try_local_match = None  # type: ignore

    if _try_local_match is not None:
        canon = _try_local_match(reference_text)
        if canon and canon in catalog:
            return catalog[canon], canon

    refn = _norm(reference_text)
    best = None
    best_len = 0
    for name, co in catalog.items():
        nn = _norm(name)
        if len(nn) >= 4 and nn in refn and len(nn) > best_len:
            best, best_len = (co, name), len(nn)
    if best:
        return best[0], best[1]
    return None, None


def _nearest_with_gate(ref_co, candidates: list):
    """Shared conservative nearest-candidate selector (PROXIMITY & LANDMARK).

    Returns (chosen, nearest_km, margin_km, distances) or None when no candidate
    passes the gates. PRECISION-first: a tie or a sub-margin separation → None.
    """
    coord_cands = [
        c for c in candidates
        if c.get("lat") is not None and c.get("lng") is not None
    ]
    if not ref_co or len(coord_cands) < 1:
        return None
    from tools.popayan_geodata import _haversine
    dists = sorted(
        ((_haversine(ref_co[0], ref_co[1], c["lat"], c["lng"]), c) for c in coord_cands),
        key=lambda x: x[0],
    )
    nearest_d, nearest_c = dists[0]
    second_d = dists[1][0] if len(dists) > 1 else float("inf")
    margin = second_d - nearest_d
    if nearest_d > PROXIMITY_MAX_ACCEPT_KM or margin < PROXIMITY_MIN_MARGIN_KM:
        return None
    distances = {_cand_name(c): round(d, 3) for d, c in dists}
    return nearest_c, nearest_d, margin, distances


def select_candidate(reference_text: str, candidates: list) -> Optional[Selection]:
    """Pick the candidate that best matches the user's natural reference.

    `candidates` is a list of dicts: {neighborhood, display_name, lat, lng, confidence}.
    The list is a CLOSED, IMMUTABLE universe — this function never mutates, appends,
    removes, or reorders it. Returns a Selection or None (inconclusive → caller
    re-asks between the candidate names).
    """
    if not reference_text or not candidates:
        return None
    ref = _norm(reference_text)

    # 1. DIRECT — the answer explicitly names a candidate's neighborhood.
    for c in candidates:
        nb = _norm(_cand_name(c))
        if nb and len(nb) >= 3 and (nb in ref or set(nb.split()) <= set(ref.split())):
            sel = Selection(
                neighborhood=_cand_name(c), lat=c.get("lat"), lng=c.get("lng"),
                method="direct_name", score=1.0, confidence=1.0, margin=1.0,
                matched_reference=_cand_name(c), distances={},
                discarded=_others(candidates, c),
                reason=f"answer explicitly named candidate barrio {_cand_name(c)!r}",
            )
            _log(reference_text, candidates, sel)
            return sel

    # 2. PROXIMITY — the reference resolves to a known BARRIO; nearest candidate.
    barrio_co, barrio_name = _resolve_in_catalog(reference_text, _all_barrios())
    if barrio_co:
        gated = _nearest_with_gate(barrio_co, candidates)
        if gated is not None:
            chosen, nearest_d, margin, distances = gated
            score = max(0.0, 1.0 - nearest_d / (PROXIMITY_MAX_ACCEPT_KM * 2))
            sel = Selection(
                neighborhood=_cand_name(chosen), lat=chosen["lat"], lng=chosen["lng"],
                method="proximity", score=round(score, 3), confidence=round(score, 3),
                margin=round(margin, 3), matched_reference=barrio_name or reference_text,
                distances=distances, discarded=_others(candidates, chosen),
                reason=(
                    f"reference resolved to barrio {barrio_name!r} "
                    f"({nearest_d:.3f} km to nearest, margin {margin:.3f} km)"
                ),
            )
            _log(reference_text, candidates, sel)
            return sel

    # 3. LANDMARK — the reference resolves to a known LANDMARK (and is not an
    #    address); nearest candidate with the SAME conservative gates.
    if not _ADDRESS_LIKE_RE.search(reference_text):
        lm_co, lm_name = _resolve_in_catalog(reference_text, _landmarks())
        if lm_co:
            gated = _nearest_with_gate(lm_co, candidates)
            if gated is not None:
                chosen, nearest_d, margin, distances = gated
                score = max(0.0, 1.0 - nearest_d / (PROXIMITY_MAX_ACCEPT_KM * 2))
                sel = Selection(
                    neighborhood=_cand_name(chosen), lat=chosen["lat"], lng=chosen["lng"],
                    method="landmark", score=round(score, 3), confidence=round(score, 3),
                    margin=round(margin, 3), matched_reference=lm_name or reference_text,
                    distances=distances, discarded=_others(candidates, chosen),
                    reason=(
                        f"reference resolved to landmark {lm_name!r} "
                        f"({nearest_d:.3f} km to nearest, margin {margin:.3f} km)"
                    ),
                )
                _log(reference_text, candidates, sel)
                return sel

    # 4. COMUNA — reference barrio's comuna uniquely matches one candidate.
    ref_comuna = _comuna_of(barrio_name) if barrio_name else None
    if ref_comuna is not None:
        matches = [c for c in candidates if _comuna_of(_cand_name(c)) == ref_comuna]
        if len(matches) == 1:
            c = matches[0]
            sel = Selection(
                neighborhood=_cand_name(c), lat=c.get("lat"), lng=c.get("lng"),
                method="comuna", score=0.7, confidence=0.7, margin=1.0,
                matched_reference=barrio_name or reference_text, distances={},
                discarded=_others(candidates, c),
                reason=(
                    f"reference barrio {barrio_name!r} is in comuna {ref_comuna}, "
                    f"which uniquely matches candidate {_cand_name(c)!r}"
                ),
            )
            _log(reference_text, candidates, sel)
            return sel

    # 5. TOKEN overlap — weakest signal, needs a clear margin.
    scored = sorted(
        ((_token_overlap(ref, _norm(_cand_name(c))), c) for c in candidates),
        key=lambda x: x[0], reverse=True,
    )
    if scored and scored[0][0] > 0:
        best_s, best_c = scored[0]
        second_s = scored[1][0] if len(scored) > 1 else 0.0
        if best_s - second_s >= TOKEN_MIN_MARGIN:
            sel = Selection(
                neighborhood=_cand_name(best_c), lat=best_c.get("lat"), lng=best_c.get("lng"),
                method="token", score=round(best_s, 3), confidence=round(best_s, 3),
                margin=round(best_s - second_s, 3), matched_reference=reference_text,
                distances={}, discarded=_others(candidates, best_c),
                reason=(
                    f"token overlap {best_s:.3f} with {_cand_name(best_c)!r} "
                    f"beat runner-up by {best_s - second_s:.3f}"
                ),
            )
            _log(reference_text, candidates, sel)
            return sel

    logger.info("[geo_ctx] inconclusive reference=%r candidates=%s -> re-ask",
                reference_text, [_cand_name(c) for c in candidates])
    return None


# ── catalog accessors (defensive lazy import; single source = popayan_geodata) ──

def _all_barrios() -> dict:
    try:
        from tools.popayan_geodata import ALL_BARRIOS
        return ALL_BARRIOS
    except Exception:  # pragma: no cover - defensive
        return {}


def _landmarks() -> dict:
    try:
        from tools.popayan_geodata import LANDMARKS
        return LANDMARKS
    except Exception:  # pragma: no cover - defensive
        return {}


def _comuna_of(name: str):
    try:
        from tools.popayan_geodata import BARRIO_TO_COMUNA
    except Exception:  # pragma: no cover
        return None
    if not name:
        return None
    target = _norm(name)
    for bn, cn in BARRIO_TO_COMUNA.items():
        if _norm(bn) == target:
            return cn
    return None


def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _log(reference_text, candidates, sel: Selection):
    logger.info(
        "[geo_ctx] selected=%r method=%s score=%.2f confidence=%.2f margin=%.2f "
        "reference=%r discarded=%s universe=%s",
        sel.neighborhood, sel.method, sel.score, sel.confidence, sel.margin,
        reference_text, sel.discarded, [_cand_name(c) for c in candidates],
    )


__all__ = ["select_candidate", "Selection"]
