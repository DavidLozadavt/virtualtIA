# core/geo_context_resolver.py
"""
Geographic context resolver — disambiguation-only.

Responsibility (conversational resolution layer, BEFORE the final geocoder query):
when a VALID address already produced several compatible candidates and the user
answers the disambiguation question with a NATURAL reference (a barrio nickname, a
bridge, a landmark) instead of the official barrio name, decide which of the
**already-found candidates** best matches — WITHOUT starting a new search and
WITHOUT ever replacing or concatenating the address.

Hard boundaries (do NOT cross):
  - Never modifies the address / normalization / parser / STT / geocoder engine.
  - Never issues a Google query. Proximity uses the LOCAL Popayán catalog only.
  - The universe is exactly the candidate list passed in. It only *selects*; it
    never invents a candidate nor opens a new search.
  - The user's answer is EVIDENCE to pick among candidates, never a new query.

Selection methods, in order of confidence:
  1. DIRECT     — the answer explicitly names a candidate's neighborhood.
  2. PROXIMITY  — resolve the answer to local coords (catalog barrio/landmark),
                  pick the candidate geographically nearest (haversine), with margin.
  3. COMUNA     — the reference barrio's comuna uniquely matches one candidate.
  4. TOKEN      — token overlap between the answer and a candidate's name.
If none is decisive, returns None and the caller re-asks between the candidate
names (still never concatenating the raw answer).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("lyra.core.geo_context_resolver")

# Tunables (km). Kept conservative so an ambiguous tie returns None → re-ask.
PROXIMITY_MIN_MARGIN_KM = 0.30   # nearest must beat the 2nd candidate by this
PROXIMITY_MAX_ACCEPT_KM = 4.00   # beyond this the reference is too far to trust
TOKEN_MIN_MARGIN = 0.20


@dataclass
class Selection:
    neighborhood: str          # official barrio of the chosen candidate
    lat: Optional[float]
    lng: Optional[float]
    method: str                # "direct_name" | "proximity" | "comuna" | "token"
    score: float               # 0..1
    margin: float              # separation from the runner-up (km or token units)
    matched_reference: str     # what in the answer drove the choice


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
    # first meaningful segment of the display name
    for part in disp.split(","):
        p = part.strip()
        if p and _norm(p) not in ("popayan", "cauca", "colombia"):
            return p
    return disp[:40]


def _reference_coords(reference_text: str):
    """Resolve a natural reference to LOCAL coords + canonical name, no Google."""
    try:
        from core.address_utils import _try_local_match
        from tools.popayan_geodata import ALL_BARRIOS, LANDMARKS
    except Exception:  # pragma: no cover - defensive
        return None, None

    canon = _try_local_match(reference_text)
    if canon:
        co = ALL_BARRIOS.get(canon) or LANDMARKS.get(canon)
        if co:
            return co, canon

    # Fallback: any catalog barrio/landmark name that appears in the reference.
    refn = _norm(reference_text)
    best = None
    best_len = 0
    for name, co in list(ALL_BARRIOS.items()) + list(LANDMARKS.items()):
        nn = _norm(name)
        if len(nn) >= 4 and nn in refn and len(nn) > best_len:
            best, best_len = (co, name), len(nn)
    if best:
        return best[0], best[1]
    return None, None


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


def select_candidate(reference_text: str, candidates: list) -> Optional[Selection]:
    """Pick the candidate that best matches the user's natural reference.

    `candidates` is a list of dicts: {neighborhood, display_name, lat, lng, confidence}.
    Returns a Selection or None (inconclusive → caller re-asks between the options).
    """
    if not reference_text or not candidates:
        return None
    ref = _norm(reference_text)

    # 1. DIRECT — the answer explicitly names a candidate's neighborhood.
    for c in candidates:
        nb = _norm(_cand_name(c))
        if nb and len(nb) >= 3 and (nb in ref or set(nb.split()) <= set(ref.split())):
            sel = Selection(_cand_name(c), c.get("lat"), c.get("lng"),
                            "direct_name", 1.0, 1.0, _cand_name(c))
            _log(reference_text, candidates, sel)
            return sel

    # 2. PROXIMITY — nearest candidate to the reference's local coords.
    ref_co, ref_name = _reference_coords(reference_text)
    coord_cands = [c for c in candidates
                   if c.get("lat") is not None and c.get("lng") is not None]
    if ref_co and coord_cands:
        from tools.popayan_geodata import _haversine
        dists = sorted(
            ((_haversine(ref_co[0], ref_co[1], c["lat"], c["lng"]), c) for c in coord_cands),
            key=lambda x: x[0],
        )
        nearest_d, nearest_c = dists[0]
        second_d = dists[1][0] if len(dists) > 1 else float("inf")
        margin = second_d - nearest_d
        if nearest_d <= PROXIMITY_MAX_ACCEPT_KM and margin >= PROXIMITY_MIN_MARGIN_KM:
            score = max(0.0, 1.0 - nearest_d / (PROXIMITY_MAX_ACCEPT_KM * 2))
            sel = Selection(_cand_name(nearest_c), nearest_c["lat"], nearest_c["lng"],
                            "proximity", round(score, 3),
                            round(margin, 3) if margin != float("inf") else 99.0,
                            ref_name or reference_text)
            _log(reference_text, candidates, sel)
            return sel

    # 3. COMUNA — reference barrio's comuna uniquely matches one candidate.
    ref_comuna = _comuna_of(ref_name) if ref_name else None
    if ref_comuna is not None:
        matches = [c for c in candidates if _comuna_of(_cand_name(c)) == ref_comuna]
        if len(matches) == 1:
            c = matches[0]
            sel = Selection(_cand_name(c), c.get("lat"), c.get("lng"),
                            "comuna", 0.7, 1.0, ref_name or reference_text)
            _log(reference_text, candidates, sel)
            return sel

    # 4. TOKEN overlap — weakest signal, needs a clear margin.
    scored = sorted(
        ((_token_overlap(ref, _norm(_cand_name(c))), c) for c in candidates),
        key=lambda x: x[0], reverse=True,
    )
    if scored and scored[0][0] > 0:
        best_s, best_c = scored[0]
        second_s = scored[1][0] if len(scored) > 1 else 0.0
        if best_s - second_s >= TOKEN_MIN_MARGIN:
            sel = Selection(_cand_name(best_c), best_c.get("lat"), best_c.get("lng"),
                            "token", round(best_s, 3), round(best_s - second_s, 3),
                            reference_text)
            _log(reference_text, candidates, sel)
            return sel

    logger.info("[geo_ctx] inconclusive reference=%r candidates=%s → re-ask",
                reference_text, [_cand_name(c) for c in candidates])
    return None


def _log(reference_text, candidates, sel: Selection):
    logger.info(
        "[geo_ctx] selected=%r method=%s score=%.2f margin=%.2f "
        "reference=%r universe=%s",
        sel.neighborhood, sel.method, sel.score, sel.margin,
        reference_text, [_cand_name(c) for c in candidates],
    )


__all__ = ["select_candidate", "Selection"]
