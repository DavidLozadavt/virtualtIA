"""
tests/test_geocoder_pipeline.py — Task 2 (bug item 7).

Verifica que run_pipeline() NUNCA auto-acepte resultados de baja precisión
(GEOMETRIC_CENTER / APPROXIMATE / NOMINATIM_LOW), aunque el número de calle
coincida textualmente, y que siga aceptando ROOFTOP / RANGE_INTERPOLATED.
También cubre el flujo de re-consulta tras la desambiguación.
"""

import asyncio

import pytest

import core.geocoder_service as gs
from core.geo_types import (
    GeoCandidate,
    GeoResolution,
    LocationType,
    ResolutionStatus,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candidate(loc_type: LocationType, display: str, neighborhood=None) -> GeoCandidate:
    # Coordenadas dentro del POPAYAN_URBAN_BBOX (2.38–2.52, -76.72–-76.54).
    return GeoCandidate(
        lat=2.44,
        lng=-76.61,
        display_name=display,
        source="google",
        location_type=loc_type,
        confidence=0.9 if loc_type in (LocationType.ROOFTOP, LocationType.RANGE_INTERPOLATED) else 0.5,
        neighborhood=neighborhood,
    )


@pytest.fixture
def patched_pipeline(monkeypatch):
    """
    Aísla run_pipeline de red y DB. El test inyecta los candidatos que devuelve
    el Geocoding API vía la lista mutable `geocoding_result`.
    """
    state = {"geocoding": []}

    async def _autocomplete(query):
        return []

    async def _geocoding(query):
        return list(state["geocoding"])

    async def _places(query):
        return []

    async def _nominatim(query):
        return []

    monkeypatch.setattr(gs, "_google_autocomplete_candidates", _autocomplete)
    monkeypatch.setattr(gs, "_google_get_candidates", _geocoding)
    monkeypatch.setattr(gs, "_google_places_search", _places)
    monkeypatch.setattr(gs, "_nominatim_get_candidates", _nominatim)
    # Sin cache ni DB.
    monkeypatch.setattr(gs, "_mem_get", lambda k: None)
    monkeypatch.setattr(gs, "_db_get", lambda k: None)
    monkeypatch.setattr(gs, "_mem_set", lambda k, v: None)
    monkeypatch.setattr(gs, "_db_set", lambda k, v: None)
    return state


# ── (a) GEOMETRIC_CENTER nunca se auto-acepta, aunque el número coincida ──────

def test_geometric_center_never_autoaccepted_even_when_numbers_match(patched_pipeline):
    # display_name contiene 4 y 26 → _result_matches_query daría True…
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.GEOMETRIC_CENTER, "Cl. 4 # 26, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Cl. 4 # 26"))

    assert res.status == ResolutionStatus.CONTEXT_GATHERING
    assert res.selected is None
    # Pide número de casa / referencia, no solo "barrio".
    assert "número de la casa" in res.disambiguation_question


def test_approximate_never_autoaccepted(patched_pipeline):
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.APPROXIMATE, "Cl. 4 # 26, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Cl. 4 # 26"))
    assert res.status == ResolutionStatus.CONTEXT_GATHERING
    assert res.selected is None


def test_nominatim_low_never_autoaccepted(patched_pipeline):
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.NOMINATIM_LOW, "Cl. 4 # 26, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Cl. 4 # 26"))
    assert res.status == ResolutionStatus.CONTEXT_GATHERING
    assert res.selected is None


# ── (b) ROOFTOP / RANGE_INTERPOLATED siguen aceptándose normal ───────────────

def test_rooftop_still_accepted(patched_pipeline):
    cand = _candidate(LocationType.ROOFTOP, "Cl. 4 # 26, Camilo Torres, Popayán")
    patched_pipeline["geocoding"] = [cand]
    res = asyncio.run(gs.run_pipeline("Cl. 4 # 26"))
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected is not None
    assert res.selected.location_type == LocationType.ROOFTOP


def test_range_interpolated_still_accepted(patched_pipeline):
    cand = _candidate(LocationType.RANGE_INTERPOLATED, "Cl. 4 # 26, Popayán")
    patched_pipeline["geocoding"] = [cand]
    res = asyncio.run(gs.run_pipeline("Cl. 4 # 26"))
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected.location_type == LocationType.RANGE_INTERPOLATED


# ── (c) Re-consulta tras desambiguación ──────────────────────────────────────

def test_requery_after_disambiguation_upgrades_to_rooftop(patched_pipeline):
    pending = GeoResolution(
        status=ResolutionStatus.CONTEXT_GATHERING,
        query="Cl. 4 # 26",
        attempt=1,
        candidates=[],  # sin candidatos persistidos → ir directo a enriquecer
        disambiguation_question=gs._build_house_number_question(),
    )
    # Tras pedir el dato, el geocoder ahora resuelve con precisión de casa.
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.ROOFTOP, "Cl. 4 # 26, Camilo Torres, Popayán")
    ]

    res = asyncio.run(
        gs.handle_user_context(
            user_text="Camilo Torres",
            pending=pending,
            original_query="Cl. 4 # 26",
            attempt=1,
        )
    )
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected.location_type == LocationType.ROOFTOP
    assert res.attempt == 2


def test_requery_still_low_precision_keeps_asking(patched_pipeline):
    pending = GeoResolution(
        status=ResolutionStatus.CONTEXT_GATHERING,
        query="Cl. 4 # 26",
        attempt=1,
        candidates=[],
        disambiguation_question=gs._build_house_number_question(),
    )
    # El re-query sigue dando GEOMETRIC_CENTER → no se acepta, se vuelve a pedir.
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.GEOMETRIC_CENTER, "Cl. 4 # 26, Popayán")
    ]
    res = asyncio.run(
        gs.handle_user_context(
            user_text="Camilo Torres",
            pending=pending,
            original_query="Cl. 4 # 26",
            attempt=1,
        )
    )
    assert res.status == ResolutionStatus.CONTEXT_GATHERING
    assert res.selected is None


# ── Caso límite: intentos agotados, sigue baja precisión → NO auto-acepta ────

def test_low_precision_failed_at_max_attempts_no_silent_accept(patched_pipeline):
    from core.geo_types import MAX_PIPELINE_ATTEMPTS

    patched_pipeline["geocoding"] = [
        _candidate(LocationType.GEOMETRIC_CENTER, "Cl. 4 # 26, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Cl. 4 # 26", attempt=MAX_PIPELINE_ATTEMPTS))

    # Agotados los intentos: FAILED, jamás RESOLVED con baja precisión.
    assert res.status == ResolutionStatus.FAILED
    assert res.selected is None
    assert res.status != ResolutionStatus.RESOLVED


def test_handle_user_context_exhaustion_returns_failed_not_accept(patched_pipeline):
    from core.geo_types import MAX_PIPELINE_ATTEMPTS

    pending = GeoResolution(
        status=ResolutionStatus.CONTEXT_GATHERING,
        query="Cl. 4 # 26",
        attempt=MAX_PIPELINE_ATTEMPTS - 1,
        candidates=[],
        disambiguation_question=gs._build_house_number_question(),
    )
    # La re-consulta del último intento sigue dando baja precisión.
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.GEOMETRIC_CENTER, "Cl. 4 # 26, Popayán")
    ]
    res = asyncio.run(
        gs.handle_user_context(
            user_text="Camilo Torres",
            pending=pending,
            original_query="Cl. 4 # 26",
            attempt=MAX_PIPELINE_ATTEMPTS - 1,
        )
    )
    assert res.status == ResolutionStatus.FAILED
    assert res.selected is None


def test_freeswitch_geo_context_exhaustion_barrio_handoff(monkeypatch):
    """
    FreeSWITCH: cuando run_pipeline devuelve FAILED (intentos agotados, aún baja
    precisión), el motor NO reinicia la captura (eso genera bucle). Toma el
    barrio / referencia que el usuario acaba de dar y crea el servicio con ese
    barrio: el conductor llama al usuario para afinar el punto exacto.
    """
    import services.telephony.voice_call_engine as vce
    from services.telephony.session_store import CallSession, STATE_CREATING_SERVICE

    async def _failed_pipeline(query, attempt=1):
        return GeoResolution(
            status=ResolutionStatus.FAILED, query=query, attempt=attempt
        )

    monkeypatch.setattr(vce, "run_pipeline", _failed_pipeline)

    engine = vce.VoiceCallEngine()
    session = CallSession(call_uuid="test-exhaust")
    session.geo_original_query = "Cl. 4 # 26"
    session.geo_attempt = 2

    res = asyncio.run(engine._handle_geo_context(session, "Camilo Torres"))

    # Sin bucle: crea el servicio con el barrio dado y cuelga (conductor llama).
    assert res.action == vce.VoiceAction.CREATE_SERVICE
    assert session.state == STATE_CREATING_SERVICE
    assert session.origen_barrio == "Camilo Torres"
    assert session.origen_text == "Cl. 4 # 26"
    assert "barrio" in res.speak_text.lower()
    assert "conductor" in res.speak_text.lower()


# ── Etiqueta de barrio: la intención del usuario manda sobre Google ───────────

def test_barrio_from_query_detects_known_barrio():
    # Barrio nombrado explícitamente en una dirección con número.
    assert gs._barrio_from_query("Calle 8c # 17-55, La Esmeralda") == "La Esmeralda"
    assert gs._barrio_from_query("la esmeralda") == "La Esmeralda"
    assert gs._barrio_from_query("Cra 9 # 20-30 pandiguando") == "Pandiguando"
    # Sin barrio nombrado → None (no inventar).
    assert gs._barrio_from_query("Cl. 8c # 17-55") is None


def test_resolved_overrides_missing_neighborhood():
    # Google no dio barrio (None) pero el usuario nombró La Esmeralda.
    cand = _candidate(LocationType.ROOFTOP, "Cl. 8c #17-55, Popayán", neighborhood=None)
    res = gs._resolved("Calle 8c # 17-55, La Esmeralda", 1, cand)
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected.neighborhood == "La Esmeralda"


def test_resolved_overrides_wrong_street_neighborhood():
    # Google devolvió una calle como "barrio"; el usuario dijo La Esmeralda.
    cand = _candidate(LocationType.GEOMETRIC_CENTER, "La Esmeralda, Popayán", neighborhood="Carrera 17")
    res = gs._resolved("la esmeralda", 1, cand)
    assert res.selected.neighborhood == "La Esmeralda"


def test_resolved_keeps_neighborhood_when_no_barrio_stated():
    # Sin barrio en la consulta → no se toca el neighborhood de Google.
    cand = _candidate(LocationType.ROOFTOP, "Cl. 8c #17-55, Popayán", neighborhood="Pomona")
    res = gs._resolved("Cl. 8c # 17-55", 1, cand)
    assert res.selected.neighborhood == "Pomona"


# ── Cache no debe ser un bypass del guard _NEVER_AUTOACCEPT ───────────────────

def test_low_precision_mem_cache_hit_not_served(patched_pipeline, monkeypatch):
    # Entrada vieja (pre-fix) en cache de memoria: 'Cl. 4' → GEOMETRIC_CENTER.
    stale = _candidate(LocationType.GEOMETRIC_CENTER, "Cl. 4, Popayán")
    monkeypatch.setattr(gs, "_mem_get", lambda k: stale)
    patched_pipeline["geocoding"] = []  # re-geocode no encuentra nada preciso

    res = asyncio.run(gs.run_pipeline("Cl. 4"))
    # NO se sirve la entrada de baja precisión desde cache.
    assert res.status != ResolutionStatus.RESOLVED
    assert res.selected is None


def test_low_precision_db_cache_hit_not_served(patched_pipeline, monkeypatch):
    stale = _candidate(LocationType.GEOMETRIC_CENTER, "Cl. 4, Popayán")
    monkeypatch.setattr(gs, "_db_get", lambda k: stale)
    patched_pipeline["geocoding"] = []

    res = asyncio.run(gs.run_pipeline("Cl. 4"))
    assert res.status != ResolutionStatus.RESOLVED
    assert res.selected is None


def test_high_precision_cache_hit_still_served(patched_pipeline, monkeypatch):
    good = _candidate(LocationType.ROOFTOP, "Cl. 4 # 26, Camilo Torres, Popayán")
    monkeypatch.setattr(gs, "_mem_get", lambda k: good)

    res = asyncio.run(gs.run_pipeline("Cl. 4 # 26"))
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected.location_type == LocationType.ROOFTOP


# ── Caso 2: nombre propio (landmark/barrio) sin keyword de vía ────────────────

def test_named_place_geometric_accepted_no_house_number_question(patched_pipeline):
    # "Universidad del Cauca" no tiene número de casa: GEOMETRIC urbano se acepta.
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.GEOMETRIC_CENTER, "Universidad del Cauca, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Universidad del Cauca"))
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected is not None
    assert res.selected.location_type == LocationType.GEOMETRIC_CENTER


def test_named_place_terminal_geometric_accepted(patched_pipeline):
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.GEOMETRIC_CENTER, "Terminal de Transportes, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Terminal de Transportes"))
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected.location_type == LocationType.GEOMETRIC_CENTER


def test_named_place_coarse_asks_general_not_house_number(patched_pipeline):
    # Nombre propio pero precisión más gruesa (APPROXIMATE) → preguntar barrio
    # general, NO "número de casa".
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.APPROXIMATE, "Valle del Ortigal, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Valle del Ortigal"))
    assert res.status == ResolutionStatus.CONTEXT_GATHERING
    assert res.selected is None
    assert "número de la casa" not in res.disambiguation_question
    assert res.disambiguation_question == gs._build_context_question()


def test_via_without_number_still_asks_house_number(patched_pipeline):
    # Caso 1 sin cambios: vía sin número → pedir número de casa/referencia.
    patched_pipeline["geocoding"] = [
        _candidate(LocationType.GEOMETRIC_CENTER, "Cl. 4, Popayán")
    ]
    res = asyncio.run(gs.run_pipeline("Cl. 4"))
    assert res.status == ResolutionStatus.CONTEXT_GATHERING
    assert "número de la casa" in res.disambiguation_question


def test_named_place_geometric_served_from_cache(patched_pipeline, monkeypatch):
    # Un landmark GEOMETRIC SÍ se sirve desde cache (no es stale): consistencia
    # entre el camino de cache y el de geocodificación fresca.
    cand = _candidate(LocationType.GEOMETRIC_CENTER, "Terminal de Transportes, Popayán")
    monkeypatch.setattr(gs, "_mem_get", lambda k: cand)
    res = asyncio.run(gs.run_pipeline("Terminal de Transportes"))
    assert res.status == ResolutionStatus.RESOLVED
    assert res.selected.location_type == LocationType.GEOMETRIC_CENTER


# ── Clasificación de la muestra real revisada con el usuario ──────────────────

def test_via_classification_matches_reviewed_plan():
    # Caso 1 (vía): piden número de casa.
    assert gs._is_via_query("Cl. 4") is True
    assert gs._is_via_query("Cra. 3") is True
    # Caso 2 (nombre propio): no son vías.
    assert gs._is_via_query("Terminal de Transportes") is False
    assert gs._is_via_query("Universidad del Cauca") is False
    assert gs._is_via_query("Santa Clara") is False
    assert gs._is_via_query("Valle del Ortigal") is False
    assert gs._is_via_query("Estadio de Popayán") is False


def test_accept_low_precision_classification():
    geo = _candidate(LocationType.GEOMETRIC_CENTER, "x, Popayán")  # urbano
    # Caso 2 nombre propio GEOMETRIC urbano → aceptable.
    assert gs._accept_low_precision(geo, "Terminal de Transportes") is True
    assert gs._accept_low_precision(geo, "Universidad del Cauca") is True
    assert gs._accept_low_precision(geo, "Santa Clara") is True
    # Caso 1 vía → no aceptable (pide número).
    assert gs._accept_low_precision(geo, "Cl. 4") is False
    assert gs._accept_low_precision(geo, "Cra. 3") is False
    # Nombre propio pero precisión gruesa (no GEOMETRIC) → no aceptable tal cual.
    approx = _candidate(LocationType.APPROXIMATE, "x, Popayán")
    assert gs._accept_low_precision(approx, "Valle del Ortigal") is False
