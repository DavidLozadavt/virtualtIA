"""Geographic context resolver — disambiguation-only selection among candidates.

The resolver never searches anew and never replaces the address; it only picks the
best of the already-found candidates from the user's natural reference.
"""

from core.geo_context_resolver import select_candidate, Selection


def _cand(nb, lat, lng):
    return {"neighborhood": nb, "display_name": nb, "lat": lat, "lng": lng, "confidence": 0.5}


def test_direct_name_match():
    cands = [_cand("Santa Teresa", 2.44, -76.60), _cand("Bella Vista", 2.45, -76.61)]
    sel = select_candidate("eso es en santa teresa", cands)
    assert sel is not None
    assert sel.neighborhood == "Santa Teresa"
    assert sel.method == "direct_name"


def test_proximity_picks_nearest_to_reference():
    # "María Oriente" resolves to real local coords (2.4307,-76.6012); the near
    # candidate must win over the far one — no new Google search involved.
    near = _cand("Santa Teresa", 2.4310, -76.6010)
    far = _cand("Prados del Norte", 2.4830, -76.5620)
    sel = select_candidate("María Oriente segundo puente", [near, far])
    assert sel is not None
    assert sel.neighborhood == "Santa Teresa"


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
