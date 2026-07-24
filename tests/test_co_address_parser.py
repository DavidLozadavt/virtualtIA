"""Acceptance battery for the Colombian address parser (spec §10).

Single authority for address interpretation: core.co_address_parser.parse_co_address.
These tests lock the spec's valid/repairable/invalid/classification cases, the repair
taxonomy, the flags, and idempotency. No network.
"""

import pytest

from core.co_address_parser import (
    parse_co_address,
    ParsedAddress,
    AddressState,
    RepairKind,
)


# ── recuperación de placa pegada por STT (task 3) ─────────────────────────────

def test_glued_numeric_placa_recovered():
    r = parse_co_address("Calle 8C #1725")
    assert r.state == AddressState.STREET_ADDRESS
    assert r.canonical == "Cl. 8C #17-25"
    assert any(rp.kind == RepairKind.PLACA_SEPARATOR_RECOVERY for rp in r.repairs)


def test_glued_numeric_placa_recovered_variant():
    assert parse_co_address("Calle 8C #1728").canonical == "Cl. 8C #17-28"


def test_glued_placa_preserves_letters():
    # Placas con letra NO se tocan (17A-25, 6E-20).
    assert parse_co_address("Cl 4 #17A-25").canonical == "Cl. 4 #17A-25"
    assert parse_co_address("Cra 17 #6E-20").canonical == "Cra. 17 #6E-20"


def test_dashed_placa_not_altered():
    r = parse_co_address("Calle 8C #17-25")
    assert r.canonical == "Cl. 8C #17-25"
    assert r.confidence == 1.0
    assert not any(rp.kind == RepairKind.PLACA_SEPARATOR_RECOVERY for rp in r.repairs)


def test_short_glued_not_over_recovered():
    # 2-3 dígitos: baja confianza → NO se inventa separador.
    assert parse_co_address("Cl 4 #100").state == AddressState.INVALID_ADDRESS_STRUCTURE
    assert parse_co_address("Cl 4 #10").state == AddressState.INVALID_ADDRESS_STRUCTURE


# ── §10.1 valid door addresses ────────────────────────────────────────────────

VALID_DOORS = [
    ("Carrera 52 calle número 3 C 6", "Cra. 52 #3C-6"),
    ("Carrera 17 calle 5 número 28", "Cra. 17 #5-28"),
    ("Calle 5 carrera 17 28", "Cl. 5 #17-28"),
    ("Calle 25 número 8 A 14", "Cl. 25 #8A-14"),
    ("Carrera 6 número 4 B 35", "Cra. 6 #4B-35"),
    ("Diagonal 12 número 18 35", "Diag. 12 #18-35"),
    ("Transversal 9 número 7 Bis 21", "Tr. 9 #7 Bis-21"),
    ("Calle 5 # 17-28", "Cl. 5 #17-28"),
    ("Cra 9 #5-28", "Cra. 9 #5-28"),
]


@pytest.mark.parametrize("raw,expected", VALID_DOORS)
def test_valid_door_canonical(raw, expected):
    p = parse_co_address(raw)
    assert p.state == AddressState.STREET_ADDRESS, (raw, p.state, p.invalid_reason)
    assert p.canonical == expected, (raw, p.canonical)


# ── §10.2 repairable glued / spaced ───────────────────────────────────────────

# kind = the costly repair required; None = already-attached form (no costly repair).
@pytest.mark.parametrize("frag,expected,kind", [
    ("3C6",  "Cra. 52 #3C-6",  RepairKind.TOKEN_SPLIT),
    ("3 C 6", "Cra. 52 #3C-6", RepairKind.LETTER_ATTACHMENT),
    ("3 c 6", "Cra. 52 #3C-6", RepairKind.LETTER_ATTACHMENT),
    ("3C 6",  "Cra. 52 #3C-6", None),   # 3C already glued; no costly repair needed
    ("8A14",  "Cra. 52 #8A-14", RepairKind.TOKEN_SPLIT),
    ("8 A 14", "Cra. 52 #8A-14", RepairKind.LETTER_ATTACHMENT),
])
def test_repairable_placa(frag, expected, kind):
    p = parse_co_address(f"Carrera 52 número {frag}")
    assert p.state == AddressState.STREET_ADDRESS, (frag, p.state, p.invalid_reason)
    assert p.canonical == expected, (frag, p.canonical)
    if kind is not None:
        assert p.confidence < 1.0
        assert kind in {r.kind for r in p.repairs}, (frag, [r.kind for r in p.repairs])


# ── §10.3 production regressions ───────────────────────────────────────────────

def test_prod_case_1_filler_and_glued():
    p = parse_co_address("Carrera 52, calle número 3C6")
    assert p.state == AddressState.STREET_ADDRESS
    assert p.canonical == "Cra. 52 #3C-6"
    kinds = {r.kind for r in p.repairs}
    assert RepairKind.REMOVED_FILLER in kinds
    assert RepairKind.TOKEN_SPLIT in kinds


def test_prod_case_2_bare_trailing_number():
    p = parse_co_address("Calle 5 carrera 17 28")
    assert p.state == AddressState.STREET_ADDRESS
    assert p.canonical == "Cl. 5 #17-28"


# ── §10.4 intersection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["Carrera 9 con Calle 5", "Cra 9 con calle 5"])
def test_intersection(raw):
    p = parse_co_address(raw)
    assert p.state == AddressState.INTERSECTION, (raw, p.state)
    assert p.canonical == "Cra. 9 con Cl. 5"


# ── §10.5 modifiers / cardinals preserved ──────────────────────────────────────

def test_avenida_carrera_cardinal_letter():
    p = parse_co_address("Avenida Carrera 73 B Sur número 4 10")
    assert p.state == AddressState.STREET_ADDRESS
    assert p.canonical == "Av. Cra. 73B Sur #4-10"


# ── §10.6 invalid structures ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,reason", [
    ("Calle 5", "segment_without_placa"),
    ("Calle 5 # 17", "missing_placa_distance"),
    ("número 3 C 6", "missing_tipo_via"),
    ("carrera calle 5", "ambiguous_multiple_via"),
])
def test_invalid_structure(raw, reason):
    p = parse_co_address(raw)
    assert p.state == AddressState.INVALID_ADDRESS_STRUCTURE, (raw, p.state, p.canonical)
    assert p.canonical is None
    assert p.invalid_reason == reason, (raw, p.invalid_reason)


# ── §10.7 non-street classification (delegated) ────────────────────────────────

def test_classify_neighborhood():
    assert parse_co_address("Yanaconas").state == AddressState.NEIGHBORHOOD


def test_classify_landmark():
    assert parse_co_address("Morro de Tulcán").state == AddressState.LANDMARK


def test_classify_place_name():
    assert parse_co_address("Centro Comercial Campanario").state == AddressState.PLACE_NAME


def test_classify_unknown():
    p = parse_co_address("asdfqwer")
    assert p.state == AddressState.UNKNOWN
    assert p.canonical is None


# ── §10.8 idempotency ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", VALID_DOORS + [
    ("Carrera 9 con Calle 5", "Cra. 9 con Cl. 5"),
    ("Avenida Carrera 73 B Sur número 4 10", "Av. Cra. 73B Sur #4-10"),
])
def test_idempotent(raw, expected):
    once = parse_co_address(raw).canonical
    assert once == expected
    twice = parse_co_address(once).canonical
    assert twice == once, (once, twice)


# ── §10.9 non-mutation guarantees ──────────────────────────────────────────────

def test_canonical_input_zero_repairs():
    p = parse_co_address("Cra. 52 #3C-6")
    assert p.state == AddressState.STREET_ADDRESS
    assert p.canonical == "Cra. 52 #3C-6"
    assert p.repaired is False
    assert p.repairs == []


# ── §10.13 repair taxonomy & flags ─────────────────────────────────────────────

def test_repaired_flag_mirrors_repairs():
    for raw, _ in VALID_DOORS:
        p = parse_co_address(raw)
        assert p.repaired == bool(p.repairs)


def test_invalid_reason_only_on_invalid():
    valid = parse_co_address("Cra 9 #5-28")
    assert valid.invalid_reason is None
    invalid = parse_co_address("Calle 5")
    assert invalid.invalid_reason is not None


def test_all_repairs_are_known_kinds():
    p = parse_co_address("Calle 5 carrera 17 28")
    assert all(isinstance(r.kind, RepairKind) for r in p.repairs)
    kinds = {r.kind for r in p.repairs}
    assert RepairKind.STREET_ORDER_REBUILD in kinds


# ── §10.12 single-authority wrappers (address_utils delegates to the parser) ────

def test_normalize_address_full_word_render():
    from core.address_utils import normalize_address
    assert normalize_address("cra 5 número 12-34") == "Carrera 5 #12-34"


def test_normalize_address_nonstreet_identity():
    from core.address_utils import normalize_address
    assert normalize_address("Yanaconas") == "Yanaconas"


def test_normalize_colombian_address_delegates():
    from core.address_utils import normalize_colombian_address
    assert normalize_colombian_address("Carrera 52, calle número 3C6") == "Cra. 52 #3C-6"
    assert normalize_colombian_address("Yanaconas") == "Yanaconas"


def test_reattach_recovers_dropped_placa():
    # Prod Case 2 (audit §3b): NLU trimmed the span to "Calle 5"; the full raw
    # parses to a complete door, so the dropped placa is recovered.
    from core.address_utils import reattach_address_details
    out = reattach_address_details("Calle 5 carrera 17 28", "Calle 5")
    assert out == "Cl. 5 #17-28"


def test_reattach_no_placa_is_identity():
    from core.address_utils import reattach_address_details
    assert reattach_address_details("Yanaconas", "Yanaconas") == "Yanaconas"


def test_reattach_prod_case2_detects_placa():
    # Bare trailing number now yields a distancia component (audit §3b).
    p = parse_co_address("Calle 5 carrera 17 28")
    assert p.components.get("distancia") == 28


# ── §10.11 orchestrator integration (parser state drives the flow) ─────────────

def _oi_imports():
    import asyncio
    from types import SimpleNamespace
    from core.geo_types import ResolutionStatus
    from services.telephony.session_store import CallSession, STATE_WAITING_ORIGIN, STATE_CONFIRMING_ORIGIN
    from services.voice.nlu import NLUResult
    from services.voice.orchestrator import TurnOrchestrator
    return (asyncio, SimpleNamespace, ResolutionStatus, CallSession,
            STATE_WAITING_ORIGIN, STATE_CONFIRMING_ORIGIN, NLUResult, TurnOrchestrator)


def _oi_geo(SimpleNamespace, ResolutionStatus, status, barrio=None, question=None):
    selected = SimpleNamespace(neighborhood=barrio) if barrio else None
    return SimpleNamespace(status=status, selected=selected, attempt=1,
                           disambiguation_question=question)


class _OIFakeGeocoder:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []
    def prewarm(self, query, attempt=1):
        pass
    async def resolve(self, query, attempt=1):
        self.calls.append((query, attempt))
        from core.geo_types import ResolutionStatus
        from types import SimpleNamespace
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(status=ResolutionStatus.FAILED, selected=None, attempt=attempt,
                               disambiguation_question=None)


class _OIFakeBackend:
    async def create_service_from_geocoded(self, **kw):
        return True, "ok"


def _oi_nlu(NLUResult, intent, pickup=None, conf=0.9):
    return NLUResult(intent=intent, pickup_span=pickup, destination_span=None,
                     landmark_reference=None,
                     pickup_confidence=conf if pickup else 0.0,
                     destination_confidence=0.0, source="llm")


def test_orch_street_geocoded_with_canonical():
    (asyncio, SimpleNamespace, ResolutionStatus, CallSession, STATE_WAITING_ORIGIN,
     STATE_CONFIRMING_ORIGIN, NLUResult, TurnOrchestrator) = _oi_imports()
    geo = _OIFakeGeocoder([_oi_geo(SimpleNamespace, ResolutionStatus,
                                   ResolutionStatus.RESOLVED, barrio="Centro")])
    orch = TurnOrchestrator(backend=_OIFakeBackend(), geocoder=geo)
    s = CallSession(call_uuid="u", caller_phone="+573001112233")
    asyncio.run(orch.process_turn(
        s, text="estoy en la calle 16 numero 3 45",
        nlu=_oi_nlu(NLUResult, "provide_pickup", pickup="calle 16 numero 3 45")))
    assert geo.calls, "el geocoder debe llamarse para una dirección válida"
    assert geo.calls[0][0] == "Cl. 16 #3-45"


def test_orch_invalid_structure_reasks_no_geocode():
    (asyncio, SimpleNamespace, ResolutionStatus, CallSession, STATE_WAITING_ORIGIN,
     STATE_CONFIRMING_ORIGIN, NLUResult, TurnOrchestrator) = _oi_imports()
    geo = _OIFakeGeocoder()
    orch = TurnOrchestrator(backend=_OIFakeBackend(), geocoder=geo)
    s = CallSession(call_uuid="u", caller_phone="+573001112233")
    turn = asyncio.run(orch.process_turn(
        s, text="en la calle 5", nlu=_oi_nlu(NLUResult, "provide_pickup", pickup="calle 5")))
    assert geo.calls == [], "estructura inválida NUNCA debe geocodificarse"
    assert s.state == STATE_WAITING_ORIGIN
    assert s.retry_count == 1
    assert turn.speak_text


def test_orch_barrio_name_flow_unchanged():
    (asyncio, SimpleNamespace, ResolutionStatus, CallSession, STATE_WAITING_ORIGIN,
     STATE_CONFIRMING_ORIGIN, NLUResult, TurnOrchestrator) = _oi_imports()
    geo = _OIFakeGeocoder([_oi_geo(SimpleNamespace, ResolutionStatus,
                                   ResolutionStatus.RESOLVED, barrio="Pubenza")])
    orch = TurnOrchestrator(backend=_OIFakeBackend(), geocoder=geo)
    s = CallSession(call_uuid="u", caller_phone="+573001112233")
    asyncio.run(orch.process_turn(
        s, text="estoy en pubenza", nlu=_oi_nlu(NLUResult, "provide_pickup", pickup="pubenza")))
    assert geo.calls and geo.calls[0][0] == "Pubenza"
    assert s.state == STATE_CONFIRMING_ORIGIN
