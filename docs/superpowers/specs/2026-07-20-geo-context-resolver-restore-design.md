# Design — Geographic Context Resolver (restore + light hardening) — 2026-07-20

## Problem (real production call)

A **valid, correctly normalized** address (`Cl. 17 #6E-12`) sits on the border of two
barrios. Lyra correctly asks which barrio. The caller answers with a **natural
reference** — `"María Oriente segundo puente"` — not the official barrio name, because
that is how locals refer to the place. The current code
(`services/voice/orchestrator.py::_handle_geo_context`, ~L681-684) does:

```python
enriched = f"{orig_q}, {context_text}".strip(", ")
geo_result = await self.geocoder.resolve(enriched, attempt=session.geo_attempt + 1)
```

i.e. it **concatenates the natural answer onto the address and re-geocodes**, sending
Google `Cl. 17 #6E-12, María Oriente segundo puente`. The address was already resolved;
the concatenation contaminates the query.

## Root cause (not a new feature)

This exact resolver **already existed and was tested green**. Commit `2d9febc` added:
- `core/geo_context_resolver.py` (203 lines)
- `tests/test_geo_context_resolver.py` (54 lines, 6 tests)
- orchestrator wiring (98 lines: `_serialize_geo_candidates`, `_dedup_named`,
  `_join_options`, import, 2 candidate-store branches, resolver block)
- `session_store.CallSession.geo_candidates` field (4 lines)

Commit `af5750b` (titled only *"session management with Redis/Memory"*) deleted all 359
lines as collateral damage — an accidental clobber, not an intentional removal. The
concatenation bug is the pre-resolver behavior left exposed.

## Scope guardrails (hard — do NOT cross)

- No changes to: parser (`co_address_parser`), STT, geocoder engine, architecture,
  normalization. No closed decision reopened.
- Only the **conversational resolution layer** between "geocoder returned multiple
  candidates for a valid address" and "final geocoder query".
- The resolver **never** issues a Google query, **never** replaces or concatenates the
  address, **never** opens a new search. The candidate list is a **closed universe**; it
  only *selects*.
- The user's natural answer is **evidence to pick a candidate**, never a new query.

## Approach: restore + light hardening

Recover the 359 deleted lines from `2d9febc`, adapt to current line numbers, then apply
targeted hardening and extra edge tests before finalizing.

### Component: `core/geo_context_resolver.py`

`select_candidate(reference_text: str, candidates: list) -> Optional[Selection]`.
Pure, deterministic, no I/O / no network / no LLM. `candidates` is a list of dicts
`{neighborhood, display_name, lat, lng, confidence}`. **Five** ordered selection methods,
most confident first; each runs **only** when the previous is inconclusive, and the first
valid selection short-circuits the rest (see § Selection priority):

1. **DIRECT** — answer explicitly names a candidate's barrio → score 1.0.
2. **PROXIMITY** — reference resolves to a known **barrio** (`address_utils._try_local_match`
   → `popayan_geodata.ALL_BARRIOS`); pick geographically nearest candidate via
   `popayan_geodata._haversine`, require `nearest ≤ PROXIMITY_MAX_ACCEPT_KM` and margin
   over runner-up `≥ PROXIMITY_MIN_MARGIN_KM`.
3. **LANDMARK** — runs only when DIRECT and PROXIMITY are inconclusive **and** the
   reference is not itself an address. Resolve the reference against the local **landmark**
   catalog (`popayan_geodata.LANDMARKS`: bridges/malls/SENA/churches/parks/D1 etc.); on a
   known landmark, take its coords and pick the nearest stored candidate applying the
   **exact same conservative gates** as PROXIMITY (`PROXIMITY_MAX_ACCEPT_KM`,
   `PROXIMITY_MIN_MARGIN_KM`). No landmark match over threshold → `None`. Never invents a
   landmark→barrio relationship, never opens a new search. (This makes explicit — as its
   own ordered stage after PROXIMITY — the landmark branch that the original
   `_reference_coords` folded in; catalog and thresholds are unchanged.)
4. **COMUNA** — reference barrio's comuna (`BARRIO_TO_COMUNA`) uniquely matches one
   candidate.
5. **TOKEN** — token-overlap (Jaccard) between answer and a candidate name, needs
   `≥ TOKEN_MIN_MARGIN` over runner-up.

Inconclusive → `None` (caller re-asks between candidate names — still never concatenating).
Returns a `Selection` carrying the full decision trace (see § Explainable decisions):
`Selection(neighborhood, lat, lng, method, score, confidence, margin, matched_reference,
distances, discarded, reason)`.

### Component: `services/telephony/session_store.py`

Re-add `geo_candidates: Optional[list] = None` on `CallSession` — the serialized closed
universe for the resolver. Serializable dicts (survives Redis/Memory session store). Add
`geo_decision_trace: Optional[dict] = None` — the auditable trace of the winning selection
(§ invariant 9), serializable, cleared on the same four terminal events as
`geo_candidates`. No other session change.

### Component: `services/voice/orchestrator.py`

- Restore import `from core.geo_context_resolver import select_candidate` and helpers
  `_serialize_geo_candidates`, `_dedup_named`, `_join_options`.
- In the two branches that enter `STATE_WAITING_GEO_CONTEXT` for a valid-but-ambiguous
  address (current ~L614, ~L651): also set
  `session.geo_candidates = _serialize_geo_candidates(geo_result)`.
- Replace the concatenation at the top of `_handle_geo_context` with the resolver block:
  - `candidates = session.geo_candidates or []`; if `len(candidates) >= 2`:
    - `sel = select_candidate(context_text, candidates)`.
    - **hit** → `session.origen_text = orig_q` (address intact),
      `session.origen_barrio = sel.neighborhood`, clear `geo_candidates`,
      `STATE_CONFIRMING_ORIGIN`, confirm `"¿{orig_q}, barrio {sel.neighborhood}, es correcto?"`.
    - **inconclusive** → `geo_attempt += 1`; if `≤3` and ≥2 named candidates → re-ask
      `"¿La dirección queda en {A o B}?"` (candidate names only, never the raw answer).
    - **exhausted** → barrio handoff on first candidate name, address intact,
      `STATE_CREATING_SERVICE`.
  - Only when `len(candidates) < 2` does the **existing** enriched-search fallback run,
    unchanged (legit weak-address context gathering).

### Data flow

```
normalized valid address ─► geocoder.resolve ─► NEEDS_DISAMBIGUATION / CONTEXT_GATHERING
   with candidates=in_bbox (≥2)  ─► session.geo_candidates = serialize(candidates)
                                     state = WAITING_GEO_CONTEXT, ask barrio
caller natural reference ─► _handle_geo_context
   len(candidates) ≥ 2 ─► select_candidate(reference, candidates)
        hit         ─► address intact + official barrio ─► confirm
        inconclusive─► re-ask between candidate NAMES (no concat)
        exhausted   ─► barrio handoff, address intact
   len(candidates) < 2 ─► existing enriched-search fallback (unchanged)
```

Verified: `geocoder_service` populates `candidates=in_bbox` on NEEDS_DISAMBIGUATION
(L1080) and some CONTEXT_GATHERING (L1142/L1161); weak-result CONTEXT_GATHERING
(L1047/1060/1214/1182) leaves candidates empty → resolver skipped → fallback preserved.

## Hardening deltas (over the verbatim 2d9febc code)

Reviewed while restoring; apply only if they do not change the green-path behavior:

1. **Confirm thresholds are still conservative for real Popayán candidate spacing** —
   border barrios can be <300 m apart, so `PROXIMITY_MIN_MARGIN_KM = 0.30` may reject
   valid-but-close pairs into a (safe) re-ask. Keep conservative (re-ask is safe), but
   document the tradeoff and cover it with a test.
2. **`_cand_name` fallback** when `neighborhood` is `None` (Google often omits it in
   Popayán) — parse first meaningful `display_name` segment (already in old code); add a
   test for the `neighborhood=None, display_name="…, Santa Teresa, Popayán"` shape.
3. **Accent/casing normalization** on both reference and candidate names (already via
   `_norm`) — add a test with accented input (`"María"`).
4. **Empty / single-candidate / no-coords** inputs → `None` (never raise). Test each.

No threshold is loosened in a way that could pick a wrong candidate; when in doubt the
resolver returns `None` and Lyra re-asks between the known names.

## Contract invariants (binding — strengthen the resolver contract only)

These invariants add no new decision, scope, architecture, parser, geocoder, or
normalization change. They formalize the guarantees the restored resolver must uphold.

### 1. Closed candidate universe

The candidate set obtained during the **first successful resolution of a valid address**
is a **closed, immutable universe**. Once stored in `session.geo_candidates` it is the
**single source of truth** for the disambiguation phase. While disambiguation is active it
is strictly forbidden to:

- add, remove, modify, or reorder candidates;
- request additional candidates;
- run a new Google Maps search;
- re-query the geocoder to widen options.

Every decision uses only the originally stored set. No conversational turn may replace,
regenerate, or extend `session.geo_candidates` while disambiguation is active. The set is
invalidated **only** when exactly one of these occurs:

- a candidate is definitively selected;
- the user cancels;
- the flow ends in handoff;
- the session is discarded.

**Enforcement:** `select_candidate` is read-only over `candidates` (never mutates,
appends, or reorders). The orchestrator sets `session.geo_candidates` **once** at
entry to `STATE_WAITING_GEO_CONTEXT` and clears it (`= None`) **only** on the four
terminal events above. The `len(candidates) >= 2` branch of `_handle_geo_context` never
calls `self.geocoder.resolve(...)`.

### 2. Immutable normalized address

Once an address reaches `STREET_ADDRESS` and the geocoder returns valid candidates, the
normalized address is **frozen** and becomes the official service reference. No later user
response may modify: tipo de vía, número principal, número de cruce, letra, Bis, cardinal,
placa, structural order, or canonical representation. During disambiguation the normalized
address is **never** re-parsed, re-normalized, re-reconstructed, substituted by natural
language, degraded, or concatenated with free text.

**Enforcement:** the resolver path preserves `session.origen_text = orig_q` verbatim on
every outcome (hit, inconclusive, exhausted) and never passes the address back through the
parser or geocoder. `parse_co_address` is not invoked anywhere in `_handle_geo_context`.

### 3. Role of the user response

Every user response during disambiguation has exactly one function: **provide evidence to
select one of the existing candidates.** It is never a new address, query, destination, or
search. It is used **only** to compute which stored candidate best matches the description.

**Enforcement:** `context_text` flows solely into `select_candidate(context_text,
candidates)` as evidence; it is never assigned to `origen_text`, never geocoded, never
stored as a destination.

### 4. No query contamination

Building a query as `normalized address + free response` is strictly forbidden. The
forbidden example — `Cl. 17 #6E-12, María Oriente segundo puente` — must never be
constructed. The final query uses **only**: the normalized address, the officially
selected candidate, and the corresponding administrative context (Popayán, Cauca,
Colombia appended once by the geocoder).

**Enforcement:** the `enriched = f"{orig_q}, {context_text}"` concatenation is **removed**
from the `≥2`-candidate path (it survives only in the `<2` legit weak-address fallback,
where no valid candidate universe exists). A regression test asserts the raw reference
string is never present in any query issued on the multi-candidate path.

### 5. Fail-safe behavior

If the resolver cannot select a candidate with sufficient evidence it returns an
**inconclusive** resolution (`None`). It must never: select a candidate by unsafe
approximation, invent relationships, open a new search, or modify the address. Permitted
behavior only:

- re-ask using **exclusively the official names** of the stored candidates; or
- run the spec-defined **handoff** flow once max attempts (`geo_attempt > 3`) is reached.

When in doubt the system keeps the original address intact and asks for more precision —
never alters previously validated information.

**Enforcement:** the `PROXIMITY_MIN_MARGIN_KM` / `PROXIMITY_MAX_ACCEPT_KM` /
`TOKEN_MIN_MARGIN` gates already bias every ambiguous case to `None`; the re-ask message
is built from `_dedup_named([c["neighborhood"] …])` (candidate names only), never from
`context_text`. Tests assert: inconclusive→None; close-pair→None (safe re-ask); re-ask
text contains only candidate names.

### 6. Landmark-based disambiguation

The resolver adds an explicit **LANDMARK** strategy, run **only** when: no DIRECT barrio
match, PROXIMITY inconclusive, and the reference is not itself an address. It uses **only**
the local Popayán/Cauca landmark catalog (`popayan_geodata.LANDMARKS`). Procedure:

1. resolve the user reference against the local landmark catalog;
2. on a known landmark, take its coordinates;
3. compute distance from that landmark to each **stored** candidate;
4. apply the **exact** conservative PROXIMITY gates (`PROXIMITY_MAX_ACCEPT_KM`,
   `PROXIMITY_MIN_MARGIN_KM`);
5. select only the candidate that passes the thresholds.

No match over threshold → `None`. Never selects by unsafe approximation, never invents a
landmark→barrio relationship, never opens a new search. Catalog and thresholds are those
of the existing PROXIMITY stage — no new tunable, no new data source.

### 7. Selection priority

Definitive order: **DIRECT → PROXIMITY → LANDMARK → COMUNA → TOKEN**. Each stage runs only
when the previous is inconclusive. The **first** valid selection short-circuits: no later
stage executes once a stage returns a `Selection`.

**Enforcement:** `select_candidate` is a single linear cascade with early `return` at each
successful stage; ordering is fixed in code and asserted by a stage-order test.

### 8. Precision over recall

The objective is **maximum precision, not maximum auto-resolutions**. False negatives are
acceptable; false positives are not. On any reasonable doubt between two or more
candidates the resolver returns `None`. It must never select a candidate merely to avoid a
re-ask; asking for clarification is always preferable to assigning a wrong barrio.

**Enforcement:** every stage's margin/threshold gate returns `None` on a tie or
sub-threshold separation (no "best-effort" fallthrough that picks a nearest without
margin). A tie test and a sub-threshold test assert `None`.

### 9. Explainable / auditable decisions

Every automatic resolution must be fully reproducible from session data alone. Each
selection records at minimum: selected candidate; discarded candidates; strategy used;
score; confidence level; computed distances (when any); decision margins; evidence used
(the matched reference); and the exact reason for the selection.

**Enforcement:** `Selection` carries these fields (`method`, `score`, `confidence`,
`margin`, `distances`, `discarded`, `matched_reference`, `reason`). On a hit the
orchestrator persists the trace to the session (new serializable field
`session.geo_decision_trace: Optional[dict]`, cleared on the same four terminal events as
`geo_candidates`) so the decision can be reconstructed later using only stored data. No
automatic decision may exist whose justification cannot be audited. This adds a session
data field only — no new execution path outside disambiguation, no flow change.

## Testing

Restore the 6 original tests (renamed file `tests/test_geo_context_resolver.py`) plus new
edge tests:
- direct-name hit; proximity picks nearest to a real reference (`"María Oriente segundo
  puente"` → Santa Teresa over a far candidate); selection always within universe; never
  returns the raw answer; inconclusive → None; empty/single/no-coords → None.
- **hardening tests:** accented reference; `neighborhood=None` via `display_name`;
  close-pair (<margin) → None (safe re-ask); comuna-unique match; token-margin gate.
- **invariant tests (orchestrator-level):**
  - closed universe — on the `≥2`-candidate path, `self.geocoder.resolve` is **not**
    called (mock/spy asserts zero calls); `session.geo_candidates` is unchanged in
    length/order across an inconclusive turn.
  - immutable address — `session.origen_text == orig_q` after hit, inconclusive, and
    exhausted outcomes; `parse_co_address` not invoked in `_handle_geo_context`.
  - no contamination — the raw reference (`"María Oriente segundo puente"`) never appears
    in any query string produced on the multi-candidate path; re-ask message contains only
    candidate names.
  - fail-safe — exhausted (`geo_attempt > 3`) → handoff with address intact.
- **landmark & decision-trace tests:**
  - landmark selection with real Popayán landmarks; natural references: `"segundo puente"`,
    `"frente al Campanario"`, `"al lado del SENA"`, `"por la iglesia"`, `"por el D1"`;
  - tie between candidates → `None`;
  - landmark outside the candidates' area → `None`;
  - ambiguous landmark → `None`;
  - selection priority — DIRECT beats PROXIMITY beats LANDMARK beats COMUNA beats TOKEN
    (stage-order + short-circuit asserted);
  - decision trace complete — selected/discarded/method/score/confidence/distances/
    margin/evidence/reason all present on a hit;
  - full reproducibility — the stored `session.geo_decision_trace` alone reconstructs the
    decision (no external state needed).

Gate: `python -m pytest tests/` → **all green, 0 regressions** (126 existing + restored +
new). `python -m compileall` clean; `import main` OK. `graphify update .` after.

## Non-goals / out of scope

- Geocoder-engine items already documented in the CO-parser handoff (triple-geocode at
  `backend_client.py:177`, missing `components=country:CO`) — untouched.
- F8 popayan_geodata catalog augmentation — untouched.

## Commit

No commit (standing request). All changes local, ready for the user to commit. Suggested
single logical commit: "restore geo context resolver (disambiguation-only) + hardening".
