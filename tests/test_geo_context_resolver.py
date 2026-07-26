# -*- coding: utf-8 -*-
"""Geographic context resolver — disambiguation-only selection among candidates.

The resolver never searches anew and never replaces the address; it only picks the
best of the already-found candidates from the user's natural reference. Precision
over recall: on any reasonable doubt it returns None so the caller re-asks between
the official candidate names.
"""

from core.geo_context_resolver import Selection, select_candidate


def _cand(nb, lat, lng):
    return {"neighborhood": nb, "display_name": nb, "lat": lat, "lng": lng, "confidence": 0.5}


# ── restored original battery ────────────────────────────────────────────────

def test_direct_name_match():
    cands = [_cand("Santa Teresa", 2.44, -76.60), _cand("Bella Vista", 2.45, -76.61)]
    sel = select_candidate("eso es en santa teresa", cands)
    assert sel is not None
    assert sel.neighborhood == "Santa Teresa"
    assert sel.method == "direct_name"


def test_proximity_picks_nearest_to_reference():
    # "María Oriente" resolves to real local barrio coords (2.4307,-76.6012); the
    # near candidate must win over the far one — no new Google search involved.
    near = _cand("Santa Teresa", 2.4310, -76.6010)
    far = _cand("Prados del Norte", 2.4830, -76.5620)
    sel = select_candidate("María Oriente segundo puente", [near, far])
    assert sel is not None
    assert sel.neighborhood == "Santa Teresa"
    assert sel.method == "proximity"


def test_selection_is_always_within_universe():
    cands = [_cand("Santa Teresa", 2.4310, -76.6010), _cand("Prados del Norte", 2.4830, -76.5620)]
    sel = select_candidate("María Oriente segundo puente", cands)
    assert sel is None or sel.neighborhood in {"Santa Teresa", "Prados del Norte"}


def test_never_returns_the_raw_answer():
    cands = [_cand("Santa Teresa", 2.4310, -76.6010), _cand("Prados del Norte", 2.4830, -76.5620)]
    sel = select_candidate("María Oriente segundo puente", cands)
    assert sel is None or sel.neighborhood != "María Oriente segundo puente"


def test_inconclusive_returns_none():
    cands = [
        {"neighborhood": "Zzz Uno", "display_name": "Zzz Uno", "lat": None, "lng": None, "confidence": 0.5},
        {"neighborhood": "Zzz Dos", "display_name": "Zzz Dos", "lat": None, "lng": None, "confidence": 0.5},
    ]
    assert select_candidate("qwerty referencia totalmente inexistente", cands) is None


def test_empty_inputs_return_none():
    assert select_candidate("", [_cand("Santa Teresa", 2.44, -76.60)]) is None
    assert select_candidate("algo", []) is None


# ── LANDMARK strategy ────────────────────────────────────────────────────────

def test_landmark_selects_nearest_candidate():
    # "Campanario" → CC Campanario (2.4596,-76.5942). The near candidate wins.
    near = _cand("Valle del Ortigal", 2.4604, -76.5945)
    far = _cand("Centro", 2.4419, -76.6063)
    sel = select_candidate("frente al Campanario", [near, far])
    assert sel is not None
    assert sel.method == "landmark"
    assert sel.neighborhood == "Valle del Ortigal"
    assert sel.matched_reference  # the landmark that drove the choice
    assert sel.distances  # per-candidate distances recorded


def test_landmark_out_of_area_returns_none():
    # Landmark resolves, but every candidate is > PROXIMITY_MAX_ACCEPT_KM away.
    a = _cand("Lejano Uno", 2.40, -76.70)
    b = _cand("Lejano Dos", 2.41, -76.71)
    assert select_candidate("frente al Campanario", [a, b]) is None


def test_landmark_tie_returns_none():
    # Two candidates equidistant from the landmark → margin below threshold → None.
    a = _cand("Simetrico A", 2.4596, -76.5842)
    b = _cand("Simetrico B", 2.4596, -76.6042)
    assert select_candidate("frente al Campanario", [a, b]) is None


def test_unresolved_natural_references_return_none():
    # References that do not resolve to a single catalog barrio/landmark must be
    # inconclusive — precision over recall (no false positives).
    cands = [_cand("Valle del Ortigal", 2.4604, -76.5945), _cand("Centro", 2.4419, -76.6063)]
    for ref in ["segundo puente", "al lado del SENA", "por la iglesia", "por el D1"]:
        assert select_candidate(ref, cands) is None, ref


# ── selection priority (DIRECT > PROXIMITY > LANDMARK > COMUNA > TOKEN) ───────

def test_priority_direct_beats_proximity():
    # Proximity alone (María Oriente) would pick Santa Teresa; but the answer also
    # NAMES a candidate directly, so DIRECT wins and short-circuits proximity.
    santa = _cand("Santa Teresa", 2.4310, -76.6010)
    prados = _cand("Prados del Norte", 2.4830, -76.5620)
    sel = select_candidate("estoy en prados del norte cerca de María Oriente", [santa, prados])
    assert sel is not None
    assert sel.method == "direct_name"
    assert sel.neighborhood == "Prados del Norte"


def test_priority_proximity_is_barrio_not_landmark():
    # A barrio reference resolves via PROXIMITY before LANDMARK is ever tried.
    near = _cand("Santa Teresa", 2.4310, -76.6010)
    far = _cand("Prados del Norte", 2.4830, -76.5620)
    sel = select_candidate("María Oriente", [near, far])
    assert sel is not None
    assert sel.method == "proximity"


def test_comuna_unique_match_selects():
    # Reference barrio "Modelo" is comuna 1; only "Pubenza" (comuna 1) matches.
    # No coords → PROXIMITY/LANDMARK skipped, COMUNA decides.
    a = {"neighborhood": "Pubenza", "display_name": "Pubenza", "lat": None, "lng": None, "confidence": 0.5}
    b = {"neighborhood": "Centro", "display_name": "Centro", "lat": None, "lng": None, "confidence": 0.5}
    sel = select_candidate("modelo", [a, b])
    assert sel is not None
    assert sel.method == "comuna"
    assert sel.neighborhood == "Pubenza"


def test_token_overlap_selects_with_margin():
    a = _cand("Zzz Norte", 2.44, -76.60)
    b = _cand("Yyy Sur", 2.45, -76.61)
    sel = select_candidate("referencia zzz lejana", [a, b])
    assert sel is not None
    assert sel.method == "token"
    assert sel.neighborhood == "Zzz Norte"


# ── precision over recall ────────────────────────────────────────────────────

def test_precision_tie_between_candidates_returns_none():
    # Two candidates equidistant from the resolved barrio → margin too small → None.
    a = _cand("Simetrico A", 2.4307, -76.5912)
    b = _cand("Simetrico B", 2.4307, -76.6112)
    assert select_candidate("María Oriente", [a, b]) is None


def test_no_coords_does_not_crash_and_is_conclusive_only_when_safe():
    cands = [
        {"neighborhood": "Santa Teresa", "display_name": "Santa Teresa", "lat": None, "lng": None, "confidence": 0.5},
        {"neighborhood": "Prados del Norte", "display_name": "Prados del Norte", "lat": None, "lng": None, "confidence": 0.5},
    ]
    # DIRECT still works without coords.
    sel = select_candidate("es en santa teresa", cands)
    assert sel is not None and sel.neighborhood == "Santa Teresa"


# ── hardening: accents / neighborhood fallback ───────────────────────────────

def test_accented_reference_direct_match():
    cands = [_cand("Bolívar", 2.4485, -76.6081), _cand("Centro", 2.4419, -76.6063)]
    sel = select_candidate("eso queda en Bolívar", cands)
    assert sel is not None and sel.neighborhood == "Bolívar"


def test_neighborhood_none_uses_display_name():
    a = {"neighborhood": None, "display_name": "Cra. 5, Santa Teresa, Popayán", "lat": 2.4310, "lng": -76.6010, "confidence": 0.5}
    b = {"neighborhood": None, "display_name": "Cra. 9, Prados del Norte, Popayán", "lat": 2.4830, "lng": -76.5620, "confidence": 0.5}
    sel = select_candidate("María Oriente", [a, b])
    assert sel is not None
    assert sel.neighborhood == "Santa Teresa"  # extracted from display_name


# ── explainable / auditable decisions ────────────────────────────────────────

def test_decision_trace_is_complete_and_reproducible():
    near = _cand("Valle del Ortigal", 2.4604, -76.5945)
    far = _cand("Centro", 2.4419, -76.6063)
    sel = select_candidate("frente al Campanario", [near, far])
    assert sel is not None
    trace = sel.to_trace()
    # every audit field present
    for key in ("neighborhood", "method", "score", "confidence", "margin",
                "matched_reference", "distances", "discarded", "reason"):
        assert key in trace, key
    assert trace["discarded"] == ["Centro"]
    assert trace["distances"]  # distances recorded for a proximity/landmark hit
    assert trace["reason"]
    # reproducible from the stored trace alone
    rebuilt = Selection(**trace)
    assert rebuilt.method == sel.method
    assert rebuilt.neighborhood == sel.neighborhood
    assert rebuilt.distances == sel.distances


def test_closed_universe_input_is_not_mutated():
    cands = [_cand("Santa Teresa", 2.4310, -76.6010), _cand("Prados del Norte", 2.4830, -76.5620)]
    snapshot = [dict(c) for c in cands]
    order_before = [c["neighborhood"] for c in cands]
    select_candidate("María Oriente segundo puente", cands)
    assert len(cands) == 2                              # nothing added/removed
    assert [c["neighborhood"] for c in cands] == order_before  # not reordered
    assert cands == snapshot                            # candidates not modified
