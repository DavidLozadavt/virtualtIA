# Handoff — Colombian Address Parser (structural normalization before geocoder) — 2026-07-20

Session goal: replace the ad-hoc regex address normalization that ran before the geocoder
with a single, structural, repair-first parser for Colombian (Popayán, Cauca) addresses.
STT/NLU/LLM/prompts/conversational-flow/geocoder-engine were **out of scope and untouched**.

> **No commits** (per standing request). All changes are local, ready for the user to commit.
> Final state: `python -m pytest tests/` → **126 passed**, 0 regressions.

---

## 1. What was delivered

### Frozen spec (single source of truth)
`docs/superpowers/specs/colombian_address_parser_design.md` — 2 design iterations + 3
self-review passes, then implemented verbatim. Contains architecture, grammar, AST, states,
repair taxonomy, integration, tests, risks, phased plan (F0–F9), reconciliations (§15),
implementation status (§16).

### Research/audit inputs (citation-backed) in `docs/geocoding/`
- `CO_ADDRESS_NOMENCLATURE_REFERENCE.md` — canonical grammar, road types, modifiers,
  validity rules, spoken variants (IGAC / DIAN-MUISCA / OSM-CO / Wikipedia / geocoding guides).
- `POPAYAN_CATALOG.md` — barrios (9 comunas incl. Comuna 9), universities, health/IPS,
  malls, public entities, monuments, parks, colegios, landmarks — for classification only.
- `GOOGLE_GEOCODING_FORMAT.md` — canonical query per case + reconciliation with the repo's
  existing query building (found the double-suffix risk C1, missing `components=country:CO` C2).
- `PIPELINE_AUDIT.md` — full raw-STT→Google data flow, transformation inventory, every
  degradation point, caller/test inventory, all with `file:line`.

### New production module
`core/co_address_parser.py` — the **single authority** for interpreting Colombian addresses.
- Entry: `parse_co_address(text: str) -> ParsedAddress`. Pure, deterministic, no I/O/LLM/network.
- Pipeline (mandatory order): `preprocess → tokenizer → lexical → parser → AST → repair →
  validate → reconstruct`. Regex is confined to the tokenizer; reconstruction reads the AST
  only.
- `render_full(parsed)` — full-word render for the legacy WhatsApp/Nominatim path.

---

## 2. Contract

```python
class AddressState(str, Enum):
    STREET_ADDRESS, INTERSECTION, NEIGHBORHOOD, LANDMARK,
    PLACE_NAME, INVALID_ADDRESS_STRUCTURE, UNKNOWN

class RepairKind(str, Enum):   # closed taxonomy
    TOKEN_SPLIT, TOKEN_MERGE, LETTER_ATTACHMENT, NUMBER_ATTACHMENT,
    REMOVED_FILLER, NORMALIZED_SEPARATOR, ABBREVIATION_NORMALIZATION, STREET_ORDER_REBUILD

@dataclass
class ParsedAddress:
    state, canonical, confidence, repaired, repairs,
    ast, tokens, components, invalid_reason
```

- **Canonical** = period-abbreviated, letter glued, placa hyphenated, `#` literal,
  cardinal last, **no city suffix** (the geocoder appends `, Popayán, Cauca, Colombia`
  once). Example: `Cra. 52 #3C-6`. **Idempotent** (re-parsing canonical → same string;
  required because the geocode cache key is the normalized string).
- **`invalid_reason`** closed set: `missing_tipo_via`, `segment_without_placa`,
  `missing_placa_distance`, `unresolvable_glued_number`, `ambiguous_multiple_via`.
- **Transversal is emitted as `Tr.`** (not `Tv.`) because the frozen
  `geocoder_service._to_google_address_format` only expands `Cl./Cra./Av./Tr./Diag.`.

### State → orchestrator behavior (one each)
| State | Behavior |
|---|---|
| STREET_ADDRESS / INTERSECTION | `origen = canonical` → geocode |
| NEIGHBORHOOD / LANDMARK / PLACE_NAME | existing name flow (geocode by name) |
| INVALID_ADDRESS_STRUCTURE | **never geocoded** → re-ask (retry message), stay WAITING_ORIGIN |
| UNKNOWN | existing "not a place" repair path |

---

## 3. Single-authority refactor (no second normalizer)

All address-structuring logic now delegates to the parser:
- `core/address_utils.py`:
  - `normalize_colombian_address()` → `parse_co_address(x).canonical or x` (abbrev render).
  - `normalize_address()` → `render_full(parse_co_address(x))` (full-word render; legacy
    WhatsApp/Nominatim contract preserved, incl. via-only "cra 5" → "Carrera 5").
  - `reattach_address_details()` → **component consumer**: compares `components` of raw vs
    extracted; recovers a dropped placa only when the raw parses to a complete door.
  - `_compound_num_replace()` and the number-word maps removed (single home = parser lexicon).
- `core/stt_enhancer.py` (**F9**): `repair_mangled_street_address` removed from the
  `preprocess_stt` chain **and** the function deleted — it was a second address
  reconstructor that guessed letter/digit splits (`1728`→`17b 28`). ASR mishear correction
  (`correct_stt_errors`, fused-word split, barge-in strip) is untouched.

### Orchestrator integration (`services/voice/orchestrator.py`)
- Import: `from core.co_address_parser import AddressState, parse_co_address`.
- `prewarm_origin` (~L294): parse `origen`; skip speculative geocode on
  INVALID_ADDRESS_STRUCTURE; else prewarm `parsed.canonical`.
- `_handle_waiting_origin` (~L565): parse `origen`; on INVALID_ADDRESS_STRUCTURE →
  `retry_count += 1`, re-ask via existing `get_progressive_retry_message`/`get_repair_message`,
  return (no geocode); else `origen = parsed.canonical` and continue the existing flow.

---

## 4. Bugs fixed (the reason this work existed)

- **Prod Case 1** — `"Carrera 52, calle número 3C6"` used to reach Google as
  `"Cra. 52 calle # 3c6"` (filler `calle` kept, glued `3c6` ambiguous). Now → **`Cra. 52 #3C-6`**
  (REMOVED_FILLER + TOKEN_SPLIT).
- **Prod Case 2** — `"Calle 5 carrera 17 28"` used to reach Google as `"Cl. 5 Cra. 17 28"`
  (bare `28` never bound to a `#`). Now → **`Cl. 5 #17-28`** (STREET_ORDER_REBUILD +
  NUMBER_ATTACHMENT). `reattach_address_details` recovers it even when NLU trims the span to
  `"Calle 5"`.
- **Ambiguous glued** like `"...1728"` → **INVALID_ADDRESS_STRUCTURE → re-ask** instead of
  guessing a wrong split/coordinate (spec risk R6).

---

## 5. Phases executed

| Phase | Result |
|---|---|
| F0 tests first (battery red) | ✓ |
| F1–F5 parser module | 43/43 parser tests green |
| F6 wrappers → single authority | full suite green |
| F7 orchestrator integration | full suite green |
| F8 catalog augmentation into `popayan_geodata` | **DEFERRED** — spec marks it additive/skippable; classification already delegates to `popayan_geodata`; not required for correctness |
| F9 retire `repair_mangled_street_address` | NLU+filters green before/after; full suite green |

Every phase ran its tests before advancing; regressions were fixed before moving on.

---

## 6. Tests

`tests/test_co_address_parser.py` (52 new tests): valid doors, repairable glued/spaced,
prod regressions, intersection, modifiers/cardinals, invalid structures (+reason),
non-street classification, idempotency, non-mutation, repair taxonomy/flags, wrapper
compatibility (§10.12), orchestrator integration (§10.11).

**Final: `python -m pytest tests/` → 126 passed** (74 pre-existing + 52 new), 0 regressions.
`python -m compileall` clean; `import main` OK. `graphify update .` run (graph current).

---

## 7. Objective contradictions found & resolved (spec §15)

- **R-A** — §10.2 grouped `"3C 6"` with costly-repair cases, but `3C` is already glued
  (NUM_LETTER) so no costly repair applies. Test asserts only canonical+state for that input.
- **R-B** — §7.2 example `"Calle Cuarta número 26"` is itself incomplete (`Calle 4 #26`, no
  distance) → INVALID; nothing valid to recover. `reattach` recovers only when the raw parses
  to a complete door (prod Case 2). Test uses the real case.

No architecture/state/grammar/integration decision was reopened.

---

## 8. Files touched

New: `core/co_address_parser.py`, `tests/test_co_address_parser.py`,
`docs/superpowers/specs/colombian_address_parser_design.md`,
`docs/geocoding/{CO_ADDRESS_NOMENCLATURE_REFERENCE,POPAYAN_CATALOG,GOOGLE_GEOCODING_FORMAT,PIPELINE_AUDIT}.md`,
this handoff.
Modified: `core/address_utils.py` (3 functions → parser wrappers; removed
`_compound_num_replace`), `services/voice/orchestrator.py` (import + 2 state branches),
`core/stt_enhancer.py` (F9 retirement).

---

## 9. Pending / next steps

1. **Commit** — nothing is committed yet (per request). Suggested split by phase for
   reversibility (F1–F5 module, F6 wrappers, F7 orchestrator, F9 stt_enhancer).
2. **Validate in a real production call** — confirm a Popayán door address (e.g. the
   `Carrera 52 ... 3C6` case) now geocodes cleanly end-to-end on `prelyra`.
3. **F8 (optional)** — augment `tools/popayan_geodata` with verified names/aliases from
   `POPAYAN_CATALOG.md` (names only, never coordinates); improves classification recall.
   Comuna 9 and some colegios are UNVERIFIED in the catalog (Alcaldía PDF / SEM XLS were
   binary/unreadable) — see that file's "Preguntas abiertas".
4. **Geocoder-engine items (documented, intentionally NOT fixed — out of scope):** the
   triple-geocode divergence at service creation (`backend_client.py:177` re-geocodes
   `f"{origen}, {barrio}"`), and the missing `components=country:CO` on the Geocoding call
   (Google-format C2). Mitigated by the idempotent canonical; revisit if precision issues
   appear.

## 10. References
- Spec: `docs/superpowers/specs/colombian_address_parser_design.md`
- Prior handoff (STT): `docs/voice/HANDOFF_2026-07-19_stt-openai-migration.md`
- Module: `core/co_address_parser.py`; tests: `tests/test_co_address_parser.py`
