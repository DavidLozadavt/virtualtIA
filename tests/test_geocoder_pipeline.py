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


def test_freeswitch_geo_context_exhaustion_triggers_safe_fallback(monkeypatch):
    """
    FreeSWITCH: cuando run_pipeline devuelve FAILED (intentos agotados, aún baja
    precisión), el motor NO confirma una dirección sin coordenadas — dispara el
    fallback explícito y reinicia la captura de origen.
    """
    import services.telephony.voice_call_engine as vce
    from services.telephony.session_store import CallSession, STATE_WAITING_ORIGIN

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

    # Fallback seguro: sigue escuchando, reinicia origen, no confirma ni cuelga
    # con una dirección de baja precisión.
    assert res.action == vce.VoiceAction.LISTEN
    assert session.state == STATE_WAITING_ORIGIN
    assert session.origen_text is None
    assert session.geo_attempt == 0
    assert "exacta" in res.speak_text.lower() or "de nuevo" in res.speak_text.lower()


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
