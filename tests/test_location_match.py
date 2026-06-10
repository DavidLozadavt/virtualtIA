"""
tests/test_location_match.py — Cobertura de la resolución de ubicaciones
precision-first (core.location_match).

Verifica que las coincidencias fonéticas/fuzzy no produzcan falsos positivos
que enruten a una entidad configurada, y que se distingan los tipos de
coincidencia con sus decisiones (ACCEPT / CONFIRM / AMBIGUOUS / REJECT).
"""

import pytest

from core.location_match import (
    resolve_location_entity,
    decide,
    Decision,
    MatchType,
)


# ── Falsos positivos: NUNCA fijar una sede en silencio ──────────────────────────

def test_villa_del_viento_not_mismatched():
    """El caso reportado: 'Villa del Viento' nunca debe mapear a OTRA entidad
    (SENA / Villa del Carmen / Banco AV Villas). Ahora está en el catálogo, así
    que resuelve a su propia entidad; si no estuviera, debería caer a REJECT —
    pero jamás a un lugar distinto en silencio."""
    m = resolve_location_entity("villa del viento")
    if decide(m) == Decision.ACCEPT:
        assert m.canonical == "Villa del Viento"
    else:
        assert m.canonical in (None, "Villa del Viento")


def test_filler_does_not_map_to_entity():
    """Relleno puro ('en el') no debe mapear a SENA por substring inverso."""
    for filler in ["en el", "en la", "por el", "de la"]:
        m = resolve_location_entity(filler)
        assert m.canonical is None, f"{filler!r} → {m.canonical!r}"
        assert decide(m) == Decision.REJECT


def test_filler_with_short_noise_window():
    m = resolve_location_entity("estoy en el viento")
    assert decide(m) != Decision.ACCEPT


# ── Verdaderos positivos preservados ────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_sub", [
    ("campanaryo", "campanario"),   # corrección STT exacta
    ("la fuerza", "pubenza"),       # mishear curado → Pubenza
    ("por el exito", "xito"),       # referencia humana
])
def test_curated_corrections_accept(text, expected_sub):
    m = resolve_location_entity(text)
    assert decide(m) == Decision.ACCEPT
    assert m.match_type == MatchType.ALIAS_EXACT
    assert expected_sub in (m.canonical or "").lower()


def test_substring_named_barrio_accepts():
    m = resolve_location_entity("barrio villa del carmen")
    assert decide(m) == Decision.ACCEPT
    assert m.match_type == MatchType.SUBSTRING
    assert "villa del carmen" in (m.canonical or "").lower()


def test_misspelling_confirms_not_silent():
    """Un misspelling de una sola palabra que no está en el dict curado debe, al
    menos, pedir confirmación (no aceptarse en silencio, no rechazarse del todo)."""
    m = resolve_location_entity("yanakonaz")
    assert decide(m) == Decision.CONFIRM
    assert "yanacona" in (m.canonical or "").lower()


# ── Anti-override: lo fonético no gana a lo textual ─────────────────────────────

def test_phonetic_does_not_override_substring():
    """Un substring cubierto (textual) debe ganar a cualquier casi-acierto
    fonético de otra entidad."""
    m = resolve_location_entity("barrio villa del carmen")
    assert m.match_type >= MatchType.SUBSTRING


# ── Desambiguación de grupos multi-sede (data-driven) ───────────────────────────

def test_sena_is_ambiguous():
    m = resolve_location_entity("sena")
    assert decide(m) == Decision.AMBIGUOUS
    assert m.needs_disambiguation
    assert len(m.disambiguation_candidates) >= 2


def test_sena_scoped_resolution_by_distinctive_token():
    scope = ["SENA Norte", "SENA Centro De Comercio Y Servicios"]
    norte = resolve_location_entity("el del norte", scope=scope)
    assert decide(norte) == Decision.ACCEPT
    assert norte.canonical == "SENA Norte"

    centro = resolve_location_entity("centro", scope=scope)
    assert decide(centro) == Decision.ACCEPT
    assert "Centro" in centro.canonical

    # 'comercio' también distingue al Centro (token distintivo del canónico)
    comercio = resolve_location_entity("el de comercio", scope=scope)
    assert comercio.canonical and "Centro" in comercio.canonical


def test_sena_scoped_rejects_unrelated():
    scope = ["SENA Norte", "SENA Centro De Comercio Y Servicios"]
    m = resolve_location_entity("villa del viento", scope=scope)
    assert decide(m) != Decision.ACCEPT


def test_villa_del_norte_not_routed_to_sena():
    """'villa del norte' es un barrio; no debe convertirse en 'SENA Norte' por
    la palabra 'norte' (el viejo substring duro hacía esto)."""
    m = resolve_location_entity("villa del norte")
    assert (m.canonical or "").upper() != "SENA NORTE"


# ── Cortesía / relleno NUNCA generan ubicación ──────────────────────────────────

@pytest.mark.parametrize("filler", [
    "hola", "buenas", "buenos días", "buenas tardes", "por favor", "gracias",
    "muchas gracias", "listo", "ok", "vale", "dale", "sí", "no", "ajá",
    "hola buenas", "quiero un taxi", "buenas tardes señor",
])
def test_courtesy_filler_never_resolves(filler):
    m = resolve_location_entity(filler)
    assert decide(m) == Decision.REJECT
    assert m.canonical is None


def test_filler_does_not_escalate_to_ambiguous():
    """Regresión: 'buenas' (fuzzy débil contra alias 'sena') NO debe escalar a
    AMBIGUOUS por needs_disambiguation. Debe ser REJECT."""
    m = resolve_location_entity("buenas")
    assert decide(m) == Decision.REJECT


def test_weak_match_to_ambiguous_entity_rejects():
    """Una coincidencia fuzzy débil a una entidad ambigua nunca da AMBIGUOUS."""
    m = resolve_location_entity("buenas")
    assert m.canonical is None or decide(m) == Decision.REJECT


def test_long_phrase_without_place_rejects():
    for t in ["muy buenas tardes como esta usted hoy",
              "necesito que me ayude con algo por favor"]:
        assert decide(resolve_location_entity(t)) == Decision.REJECT


def test_aggressive_recovery_rejects_filler():
    from services.telephony.conversation_engine import _aggressive_place_recovery
    for t in ["buenas", "hola", "gracias", "por favor", "muy buenas tardes"]:
        assert _aggressive_place_recovery(t) is None


# ── decide() es total ────────────────────────────────────────────────────────────

def test_decide_handles_none():
    m = resolve_location_entity("")
    assert decide(m) == Decision.REJECT
