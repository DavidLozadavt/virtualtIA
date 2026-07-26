# Colombian Address Parser — Definitive Technical Design

> **Status:** APPROVED-FOR-IMPLEMENTATION (design phase only — no code written yet).
> **Date:** 2026-07-19 · **Domain:** Popayán, Cauca, Colombia (exclusive).
> **Owner module (new):** `core/co_address_parser.py`.
> **Scope:** the structural normalization + validation stage that sits **between the
> semantic pickup span and the geocoder**. Nothing else changes.

This document is the single source of truth for the implementation. Every technical
decision needed to build a correct implementation is resolved here. There are no open
questions, no pending alternatives, no "decide later" items, and no undefined behaviors.

---

## 0. Hard scope boundary (what is and is not touched)

**In scope — the ONLY thing this project changes:** the transformation of an already-
transcribed, already-semantically-extracted pickup string into a structurally valid,
canonical Colombian address (or an explicit non-address classification) immediately
before the geocoder is called.

**Explicitly OUT of scope — must not be modified:**

| System | Where it lives | Why untouched |
|---|---|---|
| STT engine | `services/voice/stt_stream.py` (`OpenAIRealtimeSTT`) | Works; frozen by directive. |
| STT text enhancer (ASR concerns) | `core/stt_enhancer.py` `correct_stt_errors`, fused-word split, barge-in strip | Distinct responsibility (mishear correction feeding NLU); frozen. Its **address-reconstruction** heuristic `repair_mangled_street_address` is the exception: stripped of authority now + retired in F9 (§3.3), so no second address normalizer survives. |
| NLU / semantic extraction | `services/voice/nlu.py` | Produces `best_pickup`; frozen. |
| LLM / prompts | `services/voice/nlu.py` LLM calls, prompt strings | Frozen. |
| Conversational flow / state machine | `services/voice/orchestrator.py` dialogue states | Only a **minimal per-state branch** is added (mandated by the spec: each parser state needs exactly one orchestrator behavior). No dialogue redesign, no new conversation states. |
| Geocoding engine | `core/geocoder_service.py` (`run_pipeline`, Google/Nominatim calls, precision gating, caches) | Frozen. The parser feeds it a better string; it does not change how geocoding works. |
| Location catalog matching | `core/location_match.py`, `tools/popayan_geodata.py` | Consumed read-only; not modified (no duplicated barrio data). |

**Single-authority mandate (binding).** The parser is the **sole** authority for
interpreting Colombian addresses. **No other module may reconstruct, complete, repair,
interpret, reorder, or otherwise modify an address.** Every consumer receives a
`ParsedAddress` and reads from it; nothing re-derives address structure on its own. There
is exactly **one** normalizer implementation.

**Integration edits (the complete list):**
1. `services/voice/orchestrator.py` — the two existing call sites (`:294` prewarm, `:565`
   capture) call `parse_co_address()` and branch on `ParsedAddress.state` (§5).
2. `core/address_utils.py` — the old address-structuring functions are **re-expressed as
   thin consumers/wrappers of the parser**, holding no independent reconstruction logic:
   - `normalize_colombian_address()` → returns `parse_co_address(x).canonical` (abbreviated
     render).
   - `normalize_address()` → also delegates to `parse_co_address(x)` and renders the
     **full-word** style from `ParsedAddress.components` (its legacy contract), so it is **no
     longer a second normalizer** — same authority, different render style (§7.1).
   - `reattach_address_details()` → becomes a **consumer**: it compares
     `parse_co_address(raw).components` vs `parse_co_address(extracted).components` to detect
     a dropped placa/house-number, instead of re-normalizing strings itself (§7.2).
   - `_compound_num_replace()` and the number-word maps → their single home moves to the
     parser lexicon (§4.2); any residual caller imports from the parser (no second copy).
3. `core/stt_enhancer.py` — its address-shaped heuristic
   `repair_mangled_street_address` is **stripped of authority** immediately (the parser
   re-derives structure from tokens and overrides whatever stt_enhancer guessed, §3.3) and
   is **physically retired** in a gated, reversible, NLU-test-guarded phase (F9, §12) so that
   at end-state no second address reconstructor exists. `stt_enhancer`'s **non-address** ASR
   concerns (mishear correction `correct_stt_errors`, fused-word split, barge-in strip) are a
   distinct responsibility and remain.

No other production file changes. STT engine, NLU, LLM, prompts, dialogue state machine, and
the geocoding engine are untouched.

---

## 1. Investigación (verified inputs)

Four citation-backed research/audit documents were produced and are the factual basis of
this design. This spec does not restate opinions; every rule below traces to one of them.

| Doc | Path | What it establishes |
|---|---|---|
| Nomenclature reference | `docs/geocoding/CO_ADDRESS_NOMENCLATURE_REFERENCE.md` | Canonical grammar, road types, modifiers, validity rules, spoken variants. Sources: IGAC, DIAN/MUISCA, OSM-CO, Wikipedia *Nomenclatura urbana*, GeoPostcodes, Casacol, Popayán centro histórico. |
| Popayán catalog | `docs/geocoding/POPAYAN_CATALOG.md` | Verified barrios (9 comunas), universities, health, malls, public entities, landmarks — for classification only. Sources: Alcaldía POT, Unicauca, official sites. |
| Google format | `docs/geocoding/GOOGLE_GEOCODING_FORMAT.md` | Canonical query per case, params, and reconciliation with current repo query-building. |
| Pipeline audit | `docs/geocoding/PIPELINE_AUDIT.md` | Complete raw→Google data flow, transformation inventory, every degradation point, caller/test inventory (all `file:line`). |

### 1.1 Verified facts that drive the design

1. **Grammar (verified — nomenclature ref §Canonical grammar).**
   `via_generadora "#" placa`, where `via_generadora = tipo_via numero_core`,
   `placa = numero_cruce "-" distancia`, and
   `numero_core = digits [letter] [WS "Bis" [letter]] [WS cardinal]`.
   Example verbatim from source: `Carrera 10 #56-25` = "on Carrera 10, 25 m from Calle 56".
2. **Letter is glued, uppercase, no space** (`4A`, `3C`); **Bis** is space-separated and may
   carry a letter (`Bis A`/`Bis B`); **cardinal** (`Norte/Sur/Este/Oeste`) is applied **last**.
   (OSM-CO guide, mirrors IGAC.)
3. **Validity — a complete door needs all of:** tipo de vía + número principal + `#` +
   número secundario (cruce) + `-` + placa (distancia). Missing tipo is a **hard error**
   (Calle↔Carrera swap changes location entirely). `Calle 5` alone or `Calle 5 # 17`
   (no placa) are **incomplete**, not doors. (Casacol, Wikipedia.)
4. **Intersection** (`con`, e.g. `Cra 9 con Calle 5`) is a **valid coarse** location (a
   corner) — must be reconstructed and geocoded, never rejected. (Casacol + user directive.)
5. **Google-preferred canonical:** abbreviated-with-period road type, letter glued, placa
   hyphenated, `#` literal (never spell "número"); the pipeline appends
   `, Popayán, Cauca, Colombia` **exactly once** itself — so the parser output carries **no
   city suffix** (avoids the double-suffix bug, audit §3c / Google-format C1).
6. **Repo compatibility constraint:** `geocoder_service._to_google_address_format`
   (`geocoder_service.py:261-283`) expects the internal form to be period-abbreviated
   (`Cl.`, `Cra.`, `Av.`, `Tr.`, `Diag.`) and expands it to full words for the Geocoding
   call. Therefore the parser's canonical **must** use period-abbreviated road types.
7. **Idempotency constraint (audit §1 "key structural fact"):** `normalize_colombian_address`
   is applied twice (`orchestrator.py:565` then `geocoder_service.py:952`) and the geocode
   **cache key is the normalized string**. Re-parsing a canonical output must return it
   unchanged, or cache keys split and the `_NEVER_AUTOACCEPT` guard behaves inconsistently.
8. **Spoken `#` variants (usage):** `número/numero/numeral/almohadilla/gato/nro/no.` →
   `#`. **Spoken `-` variants:** `guion/raya/menos` → `-`. (Nomenclature ref §Spoken variants.)
9. **Two prod failure cases (audit §3a/§3b):**
   `raw "Carrera 52, calle número 3C6"` → today `"Cra. 52 calle # 3c6"` (filler `calle` kept,
   glued `3c6` ambiguous); and `"Calle 5 carrera 17 28"` → today `"Cl. 5 Cra. 17 28"` (bare
   trailing `28` never bound to a `#`). Both are repairable and are acceptance cases.

### 1.2 Conflicts found and how each was resolved

| Conflict | Sources | Resolution (final) |
|---|---|---|
| Carrera abbrev `CR` (DIAN) vs `KR` (IGAC/UAECD) | DIAN S1 vs IGAC S5/S9 | **Accept all** on input (`cra/kra/kr/cr/k`); **emit `Cra.`** (Google display + existing repo form). |
| Abbreviated vs full road type for Google | multiple, ranking UNVERIFIED | Emit **abbreviated period form**; repo's `_to_google_address_format` expands to full for the Geocoding call anyway, and Autocomplete prefers abbreviated. Matches constraint #6. |
| Canonical includes city suffix (nomenclature ref recommends) vs repo appends it | Google-format C1 | Parser emits **no suffix**; repo owns the single suffix. Prevents double-suffix. |
| Cardinal↔road-type pairing (Norte→Calle etc.) | Wikipedia tendency vs DIAN generic | Treat cardinal as a **free suffix** on either road type (not enforced). |
| `stt_enhancer.repair_mangled_street_address` overlaps parser | audit §2 step 7 | Duplicate responsibility resolved by **boundary** (§3.3): stt_enhancer stays a coarse pre-NLU cleanup; the parser is the authoritative pre-geocoder normalizer and must be robust to whatever stt_enhancer emitted. stt_enhancer is **not modified** (it feeds NLU/flow). Future retirement is a migration note, out of scope now. |

---

## 2. Decisiones técnicas (final, numbered)

- **D1.** New module `core/co_address_parser.py` owns **all** structural address
  normalization, repair, validation, classification, and canonical reconstruction. Single
  responsibility; no address-structuring logic is added anywhere else.
- **D2.** Public entry: `parse_co_address(text: str) -> ParsedAddress`. Pure function: no
  I/O, no network, no LLM, no global mutation. Deterministic.
- **D3.** Canonical output form: period-abbreviated road type, letter glued uppercase,
  `#` literal, placa hyphenated, cardinal last, **no** city suffix. Example:
  `Cra. 52 #3C-6`. (Constraints #5, #6.)
- **D4.** Output is **idempotent**: `parse_co_address(p.canonical).canonical == p.canonical`
  for any `STREET_ADDRESS`/`INTERSECTION` result. (Constraint #7.) Enforced by a test.
- **D5.** **Repair-first**: the parser attempts every deterministic repair before it may
  return an invalid state. It **never** invents digits, letters, or components, never
  reorders a already-valid address, never changes meaning. Every repair is recorded.
- **D6.** Regular expressions are permitted **only** inside the tokenizer for lexical
  category recognition. All reconstruction is done by the structural parser over the token
  stream / AST — never by regex string-rewriting.
- **D7.** Explicit states (§5), each with exactly one orchestrator behavior. No downstream
  heuristics decide flow.
- **D8.** Non-street classification (NEIGHBORHOOD / LANDMARK / PLACE_NAME) **delegates** to
  the existing catalog infrastructure (`core.address_utils.looks_like_place`,
  `core.location_match.resolve_location_entity`, which read `tools/popayan_geodata.py`).
  The parser does **not** re-implement barrio matching (no duplicated data/responsibility).
  The verified `POPAYAN_CATALOG.md` is reference/seed data, consulted read-only.
- **D9.** **One normalizer, one authority.** Every address-structuring function in
  `core/address_utils.py` becomes a consumer/wrapper of `parse_co_address` with **no
  independent reconstruction logic**: `normalize_colombian_address` (abbreviated render),
  `normalize_address` (full-word render from `components`, §7.1), `reattach_address_details`
  (component comparison, §7.2). The number-word maps have a single home in the parser
  lexicon. `stt_enhancer.repair_mangled_street_address` holds no authority and is retired in
  F9. After F9 there is exactly one address normalizer in the codebase.
- **D10.** Orchestrator integration is limited to the two existing normalization call sites;
  behavior is driven by `ParsedAddress.state`. `INVALID_ADDRESS_STRUCTURE` re-asks using the
  **existing** retry/repair messaging; no new dialogue state is introduced.
- **D11.** Full trace logging (§8): `RAW → TOKENS → AST → REPAIRS → CANONICAL → STATE`,
  under logger `lyra.core.co_address_parser`, at `DEBUG` (with a one-line `INFO` summary),
  so every transformation is inspectable without changing behavior.

---

## 3. Arquitectura

### 3.1 Module layout

```
core/co_address_parser/
    __init__.py            # exports: parse_co_address, ParsedAddress, AddressState
    types.py               # AddressState, Token, TokenKind, AST nodes, Repair, ParsedAddress
    lexicon.py             # verified data: via-type aliases→canonical, cardinals, separators,
                           #   number-words, bis, filler; sourced from the research docs
    preprocess.py          # stage 1: fold/clean, map spoken separators & number-words
    tokenizer.py           # stage 2: regex lexing → Token stream
    lexical.py             # stage 3: classify tokens into TokenKind (categoría léxica)
    parser.py              # stage 4: token stream → AST (recursive-descent / slot FSM)
    repair.py              # stage 5: structural repair over the AST (repair-first)
    validate.py            # stage 6: structural validation → AddressState
    reconstruct.py         # stage 7: AST → canonical string
    classify.py            # non-street classification (delegates to catalog infra)
    engine.py              # orchestrates stages 1..7 + logging; defines parse_co_address
```

> A single-file `core/co_address_parser.py` is acceptable if the package is judged
> overkill during implementation, **provided** each stage remains a separately-testable
> function with the responsibilities above. The package layout is the default because it
> keeps each responsibility in one small, independently-testable unit (§ isolation).

### 3.2 Responsibilities (one per unit — no duplication)

| Unit | Sole responsibility | Depends on |
|---|---|---|
| `lexicon` | Verified static data only (no logic) | — |
| `preprocess` | Normalize casing/whitespace, map spoken `#`/`-`/number-words to literals; strip courtesy filler (reuse `address_utils._strip_preamble`) | `lexicon` |
| `tokenizer` | Split text into raw tokens (regex lexing only) | `lexicon` |
| `lexical` | Tag each token with a `TokenKind` | `lexicon` |
| `parser` | Build AST from tagged tokens (structure only, no repair) | `types` |
| `repair` | Deterministic structural repairs on the AST; log each | `types`, `lexicon` |
| `validate` | Decide `AddressState` from the repaired AST | `types` |
| `reconstruct` | Emit canonical string from a valid AST | `lexicon` |
| `classify` | Non-street name → NEIGHBORHOOD/LANDMARK/PLACE_NAME/UNKNOWN | `address_utils`, `location_match` (read-only) |
| `engine` | Run the pipeline, assemble `ParsedAddress`, emit trace logs | all above |

### 3.3 Boundary with `stt_enhancer` (single-authority resolution)

`core/stt_enhancer.preprocess_stt` runs **before NLU** and mixes two responsibilities:
(a) **ASR mishear correction** (`correct_stt_errors`, fused-word split, barge-in strip,
number-words for spoken digits) — a legitimately distinct concern that stays; and
(b) **address reconstruction** (`repair_mangled_street_address`, e.g.
`carrera 4 a eb 1728 → carrera 4a # 17b 28`) — which is address interpretation and therefore
**belongs exclusively to the parser** under the single-authority mandate.

Resolution, in two steps so there is never a moment of two authorities **and** no end-state
duplication:

1. **Authority now (F7):** the parser re-derives address structure from tokens and is the
   only authority for the geocoder-bound string. Whatever `repair_mangled_street_address`
   produced upstream is treated as untrusted input and **overridden**. From F7 on, exactly
   one module's output governs the address.
2. **Physical retirement (F9):** `repair_mangled_street_address` is removed from
   `preprocess_stt` so no second address reconstructor exists in the tree at all. Because
   this edits the NLU-feeding path, F9 is gated behind: parser in production (F7 green) +
   the full NLU suite (`tests/test_nlu.py`, `tests/test_filters.py`) green before and after +
   single-commit revert. It removes **only** the address-reconstruction heuristic; the ASR
   mishear-correction functions are untouched.

This closes both "no duplicated responsibility" (one authority from F7) and "no two
normalizers" (one implementation from F9), with no architectural debt deferred indefinitely.

---

## 4. Gramática, léxico y AST

### 4.1 Canonical grammar (EBNF — verified, nomenclature ref §Canonical grammar)

```ebnf
address        = via_generadora , "#" , placa
               | via_generadora , "con" , via_generadora      (* intersection *)
               | via_generadora ;                             (* segment → INVALID (incomplete) *)

via_generadora = tipo_via , numero_core ;
placa          = numero_core , "-" , numero_core ;            (* cruce "-" distancia *)

numero_core    = digitos , [ letra ] , [ "Bis" , [ letra ] ] , [ cardinal ] ;
digitos        = DIGIT , { DIGIT } ;                          (* 1..3 typical *)
letra          = "A" | … | "Z" ;                              (* uppercase, glued *)
cardinal       = "Norte" | "Sur" | "Este" | "Oeste" | "Oriente" | "Occidente" ;
tipo_via       = (see lexicon table 4.2) ;
```

### 4.2 Lexicon (verified data — nomenclature ref §Road types / §Modifiers / §Spoken variants)

**Road type aliases → canonical emit (period-abbreviated):**

| Canonical emit | Accepted input aliases (case-insensitive) |
|---|---|
| `Cl.` | calle, cl, cll, clle |
| `Cra.` | carrera, cra, kra, kr, cr, k |
| `Av.` | avenida, av, avda, ave |
| `Av. Cra.` | avenida carrera, av carrera, av cra, ak |
| `Av. Cl.` | avenida calle, av calle, av cl, ac |
| `Diag.` | diagonal, diag, dg |
| `Tr.` | transversal, transv, tv, tr, trans, tranv |
| `Circ.` | circular, circ |
| `Circunv.` | circunvalar, circunv |
| `Pje.` | pasaje, pje, pas |
| `Autop.` | autopista, autop |
| `Vía` | via, vía |
| `Mz.` | manzana, mz, mza, mzn |

> **Punctuation & Transversal decision (D3 detail, reconciling the user's illustrative
> `Dg`/`Tv` examples with the frozen geocoder).** The frozen converter
> `geocoder_service._to_google_address_format` (`:275-280`) expands **only period forms**,
> and specifically recognizes `Cl.`, `Cra.`, `Kr.`, `Av.`, `Tr.`, `Diag.` — matching on a
> literal trailing dot (`\bCra\.`). Therefore the canonical **must** be period-abbreviated,
> and **Transversal must be emitted as `Tr.` (not `Tv.`)** so the frozen Geocoding path
> expands it. Emitting no-period (`Cra`) or `Tv.` would make that expansion silently miss.
> This is why the emitted forms differ from the user's illustrative punctuation — the
> project's frozen state (constraint #6) decides it. Road types the converter does **not**
> know (`Av. Cra.`, `Av. Cl.`, `Circ.`, `Circunv.`, `Pje.`, `Autop.`, `Vía`, `Mz.`) still
> resolve via the Autocomplete/display path and are emitted as-is (known limitation, §11 R4).

**Separators (spoken → literal):** `#` ← número, numero, numeral, almohadilla, gato, nro,
`no.`, `n°`, `nº`, `#`. `-` ← guion, guión, raya, menos, `-`, `–`.

**Cardinals:** norte→Norte, sur→Sur, este/oriente→Este/Oriente, oeste/occidente→Oeste/Occidente.
(Emit accent-correct; keep attached to the number it follows.)

**Intersection keyword:** `con`.

**Number-words:** ordinals (`primera..veinte` → `1..20`) and compound (`cuarenta y uno`→`41`)
reused from `address_utils` maps (`_compound_num_replace`, `num_words`) — verified in-repo.

**Filler:** reuse `address_utils._FILLER_WORDS` + `_PREAMBLE_PATTERNS` (courtesy/preamble only).

### 4.3 TokenKind (lexical categories)

`VIA_TYPE`, `NUMBER`, `LETTER`, `BIS`, `CARDINAL`, `HASH`, `DASH`, `CON`, `GLUED`
(`\d+[A-Za-z]\d+` or `\d+[A-Za-z]` fused), `FILLER`, `WORD` (unclassified — candidate
place-name), `EOF`.

### 4.4 AST node definitions (`types.py`)

```python
@dataclass
class NumeroCore:
    digits: int
    letter: str | None = None      # "A".."Z"
    bis: str | None = None         # "Bis" | "Bis A" | "Bis B"
    cardinal: str | None = None    # "Norte" | "Sur" | "Este" | "Oeste" | "Oriente" | "Occidente"

@dataclass
class Via:
    tipo: str                      # canonical emit, e.g. "Cra."
    numero: NumeroCore

@dataclass
class Placa:
    cruce: NumeroCore
    distancia: NumeroCore

@dataclass
class AddressAST:
    kind: str                      # "DOOR" | "INTERSECTION" | "SEGMENT" | "NONE"
    via: Via | None = None         # generadora
    placa: Placa | None = None     # DOOR only
    via2: Via | None = None        # INTERSECTION only
    place_text: str | None = None  # NONE (non-street candidate)
```

---

## 5. AddressState — states and orchestrator behavior (one behavior each)

```python
class AddressState(str, Enum):
    STREET_ADDRESS            = "street_address"
    INTERSECTION              = "intersection"
    NEIGHBORHOOD              = "neighborhood"
    LANDMARK                  = "landmark"
    PLACE_NAME                = "place_name"
    INVALID_ADDRESS_STRUCTURE = "invalid_address_structure"
    UNKNOWN                   = "unknown"
```

| State | Meaning | Canonical | **Exactly one** orchestrator behavior |
|---|---|---|---|
| `STREET_ADDRESS` | Complete door (tipo+principal+#+cruce+-+placa) after repair | `Cra. 52 #3C-6` | `origen = canonical`; geocode via `geocoder.resolve`. |
| `INTERSECTION` | `con` corner, two vías, no placa | `Cra. 9 con Cl. 5` | `origen = canonical`; geocode (coarse, valid). |
| `NEIGHBORHOOD` | Barrio name (catalog) | catalog canonical or original | Existing name flow: geocode by name (unchanged path). |
| `LANDMARK` | Landmark (catalog) | catalog canonical or original | Existing name flow: geocode by name (unchanged path). |
| `PLACE_NAME` | Named institution/place (catalog or `looks_like_place`) | original | Existing name flow: geocode by name (unchanged path). |
| `INVALID_ADDRESS_STRUCTURE` | Looks like via nomenclature but a required part is missing and irreparable | `None` | **Never geocoded.** Re-ask with the existing repair/retry message; stay in `WAITING_ORIGIN`, `retry_count += 1`. |
| `UNKNOWN` | No via nomenclature and not a recognizable place | `None` | Existing "not a place" repair path (today's `looks_like_place`-false branch). |

**Guarantee:** the geocoder receives a query only for `STREET_ADDRESS`, `INTERSECTION`,
`NEIGHBORHOOD`, `LANDMARK`, `PLACE_NAME`. `INVALID_ADDRESS_STRUCTURE` and `UNKNOWN` never
produce a geocoder call from the voice path.

---

## 6. Flujo (stage pipeline)

```
                         parse_co_address(text)
  text
   │
   ▼ ── stage 1  preprocess ─ fold ws/case-for-matching, map spoken #/-/number-words,
   │                          strip courtesy filler (address_utils._strip_preamble)
   ▼ ── stage 2  tokenizer ── regex lexing → [raw tokens]
   ▼ ── stage 3  lexical ──── tag → [Token(kind, value)]
   ▼ ── stage 4  parser ───── build AST (structure as-heard; may be incomplete)
   ▼ ── stage 5  repair ───── repair-first passes over AST; append Repair records
   ▼ ── stage 6  validate ─── AST → AddressState (+ invalid_reason)
   ▼ ── stage 7a reconstruct  valid street/intersection → canonical string (idempotent)
   ▼ ── stage 7b classify ─── if not street → NEIGHBORHOOD/LANDMARK/PLACE_NAME/UNKNOWN
   ▼
  ParsedAddress(state, canonical, confidence, repaired, repairs, ast, tokens,
                components, invalid_reason)
```

**Mandatory invariants (non-negotiable):**
- **AST-before-validation:** the AST is built (stage 4) before any validation. Validation
  (stage 6) reads the repaired AST; it is **never** the first step.
- **Order is Parse → Repair → Validate**, always. Repair-first: a recoverable address is
  repaired; only when no meaning-preserving repair exists is `INVALID_ADDRESS_STRUCTURE`
  returned.
- **Reconstruction is from the AST only** (stage 7a reads `AddressAST`, never the raw token
  list or the input string). Regex is confined to lexical recognition in the tokenizer (D6).
- **No transformation is invisible:** every stage output is logged (§8).

### 6.1 Structural parser (stage 4) — slot logic (deterministic, no regex)

Recursive-descent over the token stream, filling slots:

1. First `VIA_TYPE` + following `NUMBER`(+LETTER/BIS/CARDINAL) → **via generadora**.
2. Then, in order:
   - `CON` + `VIA_TYPE` + `NUMBER` → **intersection** (`via2`); kind = INTERSECTION.
   - A **second** `VIA_TYPE` + `NUMBER` **before** any `HASH` → its number is the **cruce**
     (Colombian "Calle 5 carrera 17" pattern); the type word itself is structural, not
     emitted. (Handles prod Case 2 and `Carrera 17 calle 5 número 28`.)
   - A **second** `VIA_TYPE` with **no** following `NUMBER` → `FILLER`, dropped.
     (Handles prod Case 1 `Cra 52 calle número 3C6`.)
   - `HASH` → begin placa side.
   - On the placa side: `NUMBER [LETTER]` = cruce; `DASH`; `NUMBER [LETTER]` = distancia.
   - A single `GLUED` token on the placa side (`3C6`) → split into cruce `3C` + distancia `6`
     (digit-letter | digits). If `\d+-\d+` present, split on the hyphen instead.
   - Bare `NUMBER NUMBER` on the placa side with no `#`/`-` heard (`... 17 28`) → cruce 17,
     distancia 28 (positional rebuild).
3. Leftover unclassified `WORD` tokens with no VIA_TYPE anywhere → `place_text` (kind NONE).

### 6.2 Structural repair (stage 5) — repair-first, classified taxonomy (each logged)

Every repair is one of the eight `RepairKind` values (§7 contract). Repairs act on the
**AST**, never by regex string-rewriting (D6).

| RepairKind | Trigger | Action on AST | Never does |
|---|---|---|---|
| `NORMALIZED_SEPARATOR` | spoken `#`/`-` word (`número`, `guion`…) | tag as HASH/DASH boundary | — |
| `ABBREVIATION_NORMALIZATION` | road-type alias / number-word / cardinal spelling | canonicalize (`carrera`→`Cra.`, `cuarenta y uno`→`41`, `norte`→`Norte`) | change the road type meaning |
| `REMOVED_FILLER` | courtesy word, or 2nd VIA_TYPE with **no** following number | drop node | drop a numbered component |
| `LETTER_ATTACHMENT` | `NUMBER` + standalone `LETTER` in number context | glue uppercase into `NumeroCore.letter` (`3 C`→`3C`) | attach a cardinal word as a letter |
| `NUMBER_ATTACHMENT` | placa side present without a `#` boundary, or a bare trailing number after a cruce | bind it as `Placa.distancia` and materialize the `#`/`-` structure | fabricate a cruce/placa value not spoken |
| `TOKEN_SPLIT` | `GLUED` `\d+[A-Za-z]?\d+` on placa side (`3C6`, `8A14`) | split into cruce `\d+[A-Za-z]` + distancia trailing `\d+` | guess a letter not present |
| `TOKEN_MERGE` | fragmented number pieces that are one number (`1 7`→`17` only with adjacency evidence) | merge | merge across a real boundary |
| `STREET_ORDER_REBUILD` | 2nd VIA_TYPE+NUMBER before `#`, or positional `NUM NUM` placa | assign 2nd via's number → cruce; positional cruce/distancia | reorder an already-valid generadora |

**Ambiguity rule (repair-first floor):** if the only way forward would invent a value (a
placa distance never spoken, a letter not present, a split with no digit/letter/hyphen
evidence), **no** repair is applied and validation returns `INVALID_ADDRESS_STRUCTURE`.
Repairing is always preferred over rejecting, but never over guessing.

### 6.3 Validation (stage 6)

```
kind == DOOR         and via.tipo and via.numero and placa.cruce and placa.distancia
                     → STREET_ADDRESS
kind == INTERSECTION and via and via2                     → INTERSECTION
kind == DOOR/SEGMENT with a VIA_TYPE present but placa incomplete/irreparable
                     → INVALID_ADDRESS_STRUCTURE (invalid_reason set)
kind == SEGMENT (tipo+numero only, no placa, not repairable)
                     → INVALID_ADDRESS_STRUCTURE  (reason="segment_without_placa")
kind == NONE         → classify() → NEIGHBORHOOD | LANDMARK | PLACE_NAME | UNKNOWN
```

`invalid_reason` values (closed set): `missing_tipo_via`, `segment_without_placa`,
`missing_placa_distance`, `unresolvable_glued_number`, `ambiguous_multiple_via`.

### 6.4 Reconstruction (stage 7a) — canonical assembly

`{tipo} {principal_core} #{cruce_core}-{distancia_core}` for DOOR;
`{tipo1} {core1} con {tipo2} {core2}` for INTERSECTION. `core` renders as
`digits[letter][ Bis[ X]][ Cardinal]`. No city suffix. Single spaces. Deterministic →
idempotent (D4).

### 6.5 Confidence

`confidence = max(0.0, 1.0 − Σ cost(kind))`. Costs (fixed, by `RepairKind`):
`NORMALIZED_SEPARATOR` 0.0, `ABBREVIATION_NORMALIZATION` 0.0, `REMOVED_FILLER` 0.0,
`LETTER_ATTACHMENT` 0.05, `NUMBER_ATTACHMENT` 0.05, `STREET_ORDER_REBUILD` 0.05,
`TOKEN_MERGE` 0.10, `TOKEN_SPLIT` 0.15. Reported only (logging + available to the
orchestrator's existing implicit-confirm threshold); it does **not** gate geocoding —
validity does.

---

## 7. Contratos (public API)

```python
class RepairKind(str, Enum):                 # closed taxonomy (§6.2)
    TOKEN_SPLIT               = "TOKEN_SPLIT"
    TOKEN_MERGE               = "TOKEN_MERGE"
    LETTER_ATTACHMENT         = "LETTER_ATTACHMENT"
    NUMBER_ATTACHMENT         = "NUMBER_ATTACHMENT"
    REMOVED_FILLER            = "REMOVED_FILLER"
    NORMALIZED_SEPARATOR      = "NORMALIZED_SEPARATOR"
    ABBREVIATION_NORMALIZATION= "ABBREVIATION_NORMALIZATION"
    STREET_ORDER_REBUILD      = "STREET_ORDER_REBUILD"

@dataclass
class Repair:
    kind: RepairKind
    before: str
    after: str
    reason: str

@dataclass
class ParsedAddress:
    state: AddressState
    canonical: str | None            # None for INVALID/UNKNOWN; abbreviated render
    confidence: float                # 0.0–1.0
    repaired: bool                   # True iff len(repairs) > 0
    repairs: list[Repair]            # every repair applied, classified (§6.2)
    ast: AddressAST
    tokens: list[Token]
    components: dict                  # {tipo, numero, cruce, distancia, letter, bis, cardinal}
    invalid_reason: str | None       # closed set (§6.3), names the exact failing rule; else None

def parse_co_address(text: str) -> ParsedAddress: ...
```

`repaired` is a convenience mirror of `bool(repairs)`; both are always populated.
`invalid_reason` is set **iff** `state == INVALID_ADDRESS_STRUCTURE`, and it names the exact
structural rule that failed (§6.3) — never a generic message.

**All wrappers delegate to the one authority (D9), in `core/address_utils.py`:**

```python
def normalize_colombian_address(address: str) -> str:      # abbreviated render
    if not address:
        return ""
    from core.co_address_parser import parse_co_address
    parsed = parse_co_address(address)
    return parsed.canonical or address.strip()             # non-street → original
```

### 7.1 `normalize_address` — full-word render (legacy contract, same authority)

`normalize_address` historically expands to full words (`"cra 5"`→`"Carrera 5"`) for the
WhatsApp/Nominatim path. It stops being a second normalizer and becomes a **render style**
over the same parser output:

```python
def normalize_address(address: str) -> str:
    if not address:
        return ""
    from core.co_address_parser import parse_co_address, render_full
    parsed = parse_co_address(address)
    if parsed.state in (AddressState.STREET_ADDRESS, AddressState.INTERSECTION):
        return render_full(parsed)     # from parsed.components: "Carrera 5 #12-34"
    return address.strip()             # non-street → original (unchanged contract)
```

`render_full(parsed)` is a pure formatter in the parser package (`reconstruct.py`) that
emits the full-word road type from `parsed.components` — **no parsing logic outside the
parser.** This keeps a single interpreter with two output styles.

### 7.2 `reattach_address_details` — consumer, not reconstructor

Reframed to read `components` instead of re-normalizing strings:

```python
def reattach_address_details(original_user_text: str, extracted: str) -> str:
    if not original_user_text or not extracted:
        return extracted
    from core.co_address_parser import parse_co_address
    raw_p = parse_co_address(original_user_text)
    ext_p = parse_co_address(extracted)
    raw_has_placa = raw_p.components.get("distancia") is not None
    ext_has_placa = ext_p.components.get("distancia") is not None
    if raw_has_placa and not ext_has_placa and raw_p.canonical:
        return raw_p.canonical         # recover the door the extractor dropped
    return extracted
```

This fixes prod Case 2 for free: the bare trailing number now yields a `distancia`
component in `raw_p`, so a dropped house number is detected even when the old `#`-string
probe would have missed it (audit §3b).

Caller-contract preservation (audit §4): every caller of these functions
(`orchestrator.py:294,565`; `geocoder_service.run_pipeline:952`;
`whatsapp_service.py:156,171,308,409`; `api/routers/whatsapp.py:*`) receives the same
string shape (street → structured; non-street → original text; ≥3 chars for real inputs).

---

## 8. Logging (full traceability)

Logger `lyra.core.co_address_parser`. **No transformation is invisible** — every stage
emits its output.

- One `INFO` line per parse:
  `[co_addr] state=STREET_ADDRESS conf=0.85 repaired=True repairs=3 raw=%r canonical=%r`
- `DEBUG` block (guarded by `logger.isEnabledFor(DEBUG)` to avoid overhead) printing the
  full mandated chain `RAW → PREPROCESS → TOKENS → AST → REPAIRS → VALIDATION → CANONICAL →
  STATE`:
  ```
  RAW       : "Carrera 52, calle número 3C6"
  PREPROCESS: "carrera 52 calle # 3c6"
  TOKENS    : VIA(Cra.) NUM(52) FILLER(calle) HASH(#) GLUED(3c6)
  AST       : DOOR via=Cra.52 placa=None
  REPAIRS   : REMOVED_FILLER(calle) · TOKEN_SPLIT(3c6→3C|6) · NUMBER_ATTACHMENT(#…-…)
  VALIDATION: DOOR ok — tipo✓ principal✓ cruce✓ distancia✓
  CANONICAL : "Cra. 52 #3C-6"
  STATE     : STREET_ADDRESS (conf=0.85)
  ```
  On rejection the `VALIDATION` line names the failing rule, e.g.
  `VALIDATION: INVALID — segment_without_placa (no cruce/distancia)`, and `CANONICAL: —`.
- `ParsedAddress.repairs` / `.repaired` are machine-readable; the same data is emitted so
  production logs (journalctl) show exactly why a query was reshaped or rejected. No PII
  beyond the address text already logged today by the orchestrator/geocoder.

---

## 9. Catálogo Popayán

- **Source of truth (data):** `docs/geocoding/POPAYAN_CATALOG.md` — verified,
  citation-backed, covering the mandated category set: **barrios, conjuntos,
  urbanizaciones, universidades, hospitales, clínicas, IPS, colegios, centros comerciales,
  entidades públicas, monumentos, parques, lugares históricos, puntos de referencia
  frecuentes.** (Colegios, monumentos, parques, extra clínicas/IPS, and Comuna 9 barrios are
  being appended by the catalog-extension research pass; gaps until then are flagged
  UNVERIFIED and filled in F8, additive.)
- **Usage is limited to three purposes only — classification, validation, normalization —
  and NEVER to invent, complete, or substitute an address the user did not say.**
- **Runtime classification:** `classify.py` decides NEIGHBORHOOD/LANDMARK/PLACE_NAME by
  **delegating** to `address_utils.looks_like_place` and
  `location_match.resolve_location_entity`, which already read `tools/popayan_geodata.py`.
  The parser adds **no** competing barrio table (no duplicated responsibility, D8).
- **Augmentation (optional, additive, out of the critical path):** where
  `POPAYAN_CATALOG.md` contains verified names missing from `popayan_geodata`, they may be
  contributed to `popayan_geodata` in a **separate, additive** change (names/aliases only,
  never coordinates for door precision). This is listed in the implementation plan as an
  independent, reversible phase and is **not** required for the core parser to function.
- **Hard rule:** the catalog is used only to **recognize/classify/normalize** a name the
  user actually said. It is never used to invent, complete, or substitute an address.

---

## 10. Pruebas (complete battery)

New file `tests/test_co_address_parser.py` (pure unit tests, no network). Existing suite
must stay green (audit §6): `tests/test_orchestrator.py`, `tests/test_filters.py`,
`tests/test_runtime_flow.py`, `tests/test_nlu.py`.

**10.1 Valid door addresses (STREET_ADDRESS + exact canonical):**

| Input | Expected canonical |
|---|---|
| `Carrera 52 calle número 3 C 6` | `Cra. 52 #3C-6` |
| `Carrera 17 calle 5 número 28` | `Cra. 17 #5-28` |
| `Calle 5 carrera 17 28` | `Cl. 5 #17-28` |
| `Calle 25 número 8 A 14` | `Cl. 25 #8A-14` |
| `Carrera 6 número 4 B 35` | `Cra. 6 #4B-35` |
| `Diagonal 12 número 18 35` | `Diag. 12 #18-35` |
| `Transversal 9 número 7 Bis 21` | `Tr. 9 #7 Bis-21` |
| `Calle 5 # 17-28` | `Cl. 5 #17-28` |
| `Cra 9 #5-28` | `Cra. 9 #5-28` |

**10.2 Repairable glued/spaced (TOKEN_SPLIT / LETTER_ATTACHMENT):**
`3C6`, `3 C 6`, `3 c 6`, `3C 6`, `8A14`, `8 A 14` → in context
`Carrera 52 número <x>` yield `Cra. 52 #3C-6` / `Cra. 52 #8A-14` respectively; assert the
`TOKEN_SPLIT` (glued) or `LETTER_ATTACHMENT` (spaced letter) repair is recorded and
confidence < 1.0.

**10.3 Prod regressions (from audit §3, must now pass):**
- `Carrera 52, calle número 3C6` → `Cra. 52 #3C-6` (filler dropped, glued split).
- `Calle 5 carrera 17 28` → `Cl. 5 #17-28` (bare trailing number bound to `#`).

**10.4 Intersection:**
- `Carrera 9 con Calle 5` → `INTERSECTION`, `Cra. 9 con Cl. 5`.
- `Cra 9 con calle 5` → same.

**10.5 Modifiers/cardinals preserved (never altered):**
`4A`, `4B`, `4C`, `10 Bis`, `10 Bis A`, `10 Bis B`, `12A Sur`, `17 Norte` inside full
addresses (e.g. `Avenida Carrera 73 B Sur número 4 10` → `Av. Cra. 73B Sur #4-10`); assert
letter/Bis/cardinal survive verbatim in the correct position.

**10.6 Invalid (INVALID_ADDRESS_STRUCTURE + reason, no canonical, no geocode):**

| Input | Reason |
|---|---|
| `Calle 5` | `segment_without_placa` |
| `Calle 5 # 17` | `missing_placa_distance` |
| `número 3 C 6` (no tipo) | `missing_tipo_via` |
| `carrera calle 5` (dup via, no numbers/placa) | `ambiguous_multiple_via` |

**10.7 Non-street classification (delegated):**
`Yanaconas`→NEIGHBORHOOD; `Morro de Tulcán`→LANDMARK; `Centro Comercial Campanario`→
PLACE_NAME; `asdfqwer`→UNKNOWN. (Assert `canonical is None` for INVALID/UNKNOWN; name flow
untouched for the place states.)

**10.8 Idempotency (D4):** for every 10.1/10.2/10.3/10.4/10.5 case,
`parse_co_address(p.canonical).canonical == p.canonical`.

**10.9 Non-mutation guarantees:** an already-canonical valid address produces **zero**
repairs; no test input ever gains a digit/letter that was not present in the input.

**10.10 Wrapper compatibility:** `normalize_colombian_address` returns the canonical for
street inputs (contains `#`) and the original for non-street inputs; run the existing
orchestrator tests unchanged.

**10.11 Orchestrator integration tests (added to `tests/test_orchestrator.py`):**
- Street span → `STREET_ADDRESS` → `geocoder.resolve` called with canonical.
- Invalid-structure span → geocoder **not** called; retry message; `retry_count == 1`;
  state stays `WAITING_ORIGIN`.
- Barrio span → name flow unchanged (existing test still passes).

**10.12 Single-authority wrappers:**
- `normalize_address("cra 5 número 12-34")` → `"Carrera 5 #12-34"` (full-word render via the
  parser; assert no independent logic — same `components` as `parse_co_address`).
- `normalize_address("Yanaconas")` → `"Yanaconas"` (non-street identity).
- `reattach_address_details("Calle Cuarta número 26", "Calle Cuarta")` recovers the placa
  (`distancia` present in raw, absent in extracted) → returns the full canonical; and
  `reattach_address_details("Yanaconas", "Yanaconas")` returns `"Yanaconas"` unchanged (no
  placa to protect).
- Prod Case 2 via reattach: raw `"Calle 5 carrera 17 28"` yields a `distancia` component →
  a dropped house number is detected (guards audit §3b).

**10.13 Repair taxonomy & flags:**
- Every repair recorded uses a `RepairKind` from the closed enum; assert the expected kind
  per case (e.g. `3c6` → `TOKEN_SPLIT`; `3 C 6` → `LETTER_ATTACHMENT`; `Calle 5 carrera 17
  28` → `STREET_ORDER_REBUILD` + `NUMBER_ATTACHMENT`; `carrera` → `Cra.` →
  `ABBREVIATION_NORMALIZATION`; `calle` filler → `REMOVED_FILLER`).
- `repaired == bool(repairs)` for every case; an already-canonical input has
  `repaired is False` and `repairs == []`.
- Every `INVALID_ADDRESS_STRUCTURE` result has a non-null `invalid_reason` from the closed
  set (§6.3); every valid result has `invalid_reason is None`.

**Success metric:** normalization coverage on the labeled set (10.1–10.5) rises from the
current behavior (2 of the prod/example cases fail today) to 100% of the labeled valid set,
with 0 regressions in the existing suite.

---

## 11. Riesgos y mitigaciones

| # | Risk | Mitigation |
|---|---|---|
| R1 | Idempotency break → cache-key split / guard drift (audit §1) | D4 + test 10.8; canonical is the fixed point of the parser. |
| R2 | Wrapper changes a string an existing caller relied on | Caller inventory (audit §4) enumerated; wrapper preserves shape; run full suite (10.10, 10.11). |
| R3 | Over-aggressive filler drop removes a real token | `REMOVED_FILLER` only removes courtesy words and a 2nd VIA_TYPE **with no following number**; never a numbered component (test 10.9). |
| R4 | `_to_google_address_format` doesn't expand `Av. Cra./Av. Cl./Circ./Circunv./Pje./Autop./Mz.` (constraint #6) | Known limitation, documented; those still resolve via Autocomplete/display. `Cl./Cra./Av./Tr./Diag.` **are** expanded, so the common types round-trip. Geocoding engine is frozen; not a regression (today those rarer types aren't produced canonically at all). |
| R5 | Double-suffix if canonical carried a city suffix | Parser emits **no** suffix (D3); repo appends once. |
| R6 | Ambiguous glued split guesses wrong (`1728`→`17b 28`?) | Parser only splits on a **present** letter/hyphen or positional two-number; never invents a letter. Truly ambiguous → `INVALID_ADDRESS_STRUCTURE`, re-ask (safer than wrong coordinate — user directive). |
| R7 | Overlap with `stt_enhancer.repair_mangled_street_address` (two normalizers) | §3.3: authority moves to the parser at F7 (its output overridden); the heuristic is physically retired at F9. No indefinitely-deferred duplication. |
| R8 | Classification delegation returns different results than a bespoke table | Intentional — single source of barrio truth (`popayan_geodata`). No new table to drift. |
| R9 | Triple-geocode divergence at service creation (audit §3c, `backend_client.py:177`) | Out of scope (geocoder engine); **mitigated** because idempotent canonical means capture-query and creation-query normalize identically. Documented, not fixed here. |
| R10 | `normalize_address` now routes through the parser — could change WhatsApp behavior | It renders full-word from the same AST; non-street inputs return the original unchanged. Locked by 10.12 + running the WhatsApp-path callers in the existing suite. Revert = restore old function (F6). |
| R11 | F9 retirement of `repair_mangled_street_address` regresses NLU/semantic extraction | F9 is gated behind the NLU + filters suites green before/after and single-commit revert; removes only address-reconstruction, not ASR mishear correction (§3.3). If any NLU test moves, F9 is reverted and the authority-based resolution (F7) still guarantees single authority. |

---

## 12. Estrategia de migración / plan de implementación (phased, verifiable, reversible)

Each phase is independently testable and revertible (single-purpose commits). No phase
touches STT/NLU/LLM/prompts/flow/geocoder internals.

- **F0 — Fixtures & harness (no prod code).** Add `tests/test_co_address_parser.py` with the
  full labeled battery (§10) as expected-failing/placeholder against a stub. Reversible:
  test-only. *Verify:* battery enumerated, runs (red).
- **F1 — `types.py` + `lexicon.py`.** Data + dataclasses only, no behavior. *Verify:* import
  + lexicon completeness unit checks.
- **F2 — `preprocess` + `tokenizer` + `lexical`.** Text → tagged tokens. *Verify:* tokenizer
  unit tests on §10 inputs (token streams asserted).
- **F3 — `parser` (stage 4).** Tagged tokens → AST (no repair). *Verify:* AST asserted for
  clean inputs (10.1 subset already canonical).
- **F4 — `repair` (stage 5).** Repair-first passes + `Repair` logging. *Verify:* 10.2, 10.3
  repair cases; repair records asserted; non-mutation (10.9).
- **F5 — `validate` + `reconstruct` + `classify` + `engine`.** Produce `ParsedAddress`;
  wire logging (§8). *Verify:* full §10.1–10.9 green, including idempotency 10.8.
- **F6 — Wrapper set (D9): one authority.** Re-implement all three `address_utils`
  functions as parser consumers: `normalize_colombian_address` (abbrev render, §7),
  `normalize_address` (full-word render via `render_full`, §7.1), `reattach_address_details`
  (component comparison, §7.2). Add `render_full` to the parser package. *Verify:* 10.10 +
  entire existing suite green (audit §6), including WhatsApp callers. Revert = restore the
  three old functions.
- **F7 — Orchestrator integration + authority.** Branch on `ParsedAddress.state` at
  `orchestrator.py:294` and `:565`; `INVALID_ADDRESS_STRUCTURE` → existing retry message, no
  geocode. From here the parser is the sole authority (stt_enhancer address guesses are
  overridden). *Verify:* 10.11 + existing orchestrator tests green. Revert = restore the two
  call sites.
- **F8 — (additive) Catalog augmentation.** Contribute verified missing names/aliases from
  `POPAYAN_CATALOG.md` (barrios incl. Comuna 9, colegios, monumentos, parques, clínicas/IPS)
  into `popayan_geodata` (**names/aliases only, never coordinates**). *Verify:* classification
  tests; no coordinate changes. Independent; skippable without affecting the parser.
- **F9 — Retire the second normalizer (`stt_enhancer`).** Remove
  `repair_mangled_street_address` from the `preprocess_stt` chain so no second address
  reconstructor exists (§3.3). Gated: F7 in production and green, **NLU suite
  (`tests/test_nlu.py`, `tests/test_filters.py`) green before and after**, single-commit
  revert. Removes only the address-reconstruction heuristic; ASR mishear correction stays.
  *Verify:* NLU + filters + orchestrator + parser suites all green; the two prod regressions
  (10.3) still pass end-to-end.

**Go-live gate (parser):** F5–F7 green, full `pytest tests/` green, idempotency proven, the
two prod regressions (10.3) fixed, zero existing-test regressions. F0–F5 carry no production
risk (new module unused until F6/F7 wire it in). **End-state gate (single normalizer):** F9
green — exactly one address normalizer remains in the tree.

---

## 13. Autorrevisión (review log)

Critical self-review performed against: inconsistencies, duplicated responsibilities,
ambiguous states, possible regressions, architectural gaps. Findings fixed inline:

- **Duplicated responsibility (barrio matching)** — initial draft implied a new catalog
  table. **Fixed:** D8 + §9 delegate classification to `popayan_geodata` via existing
  lookups; the new catalog is reference/seed data only.
- **Duplicated responsibility (structural repair vs `stt_enhancer`)** — **Fixed:** explicit
  boundary §3.3 + risk R7; stt_enhancer frozen, parser authoritative.
- **Ambiguous state contract** — ensured every `AddressState` maps to exactly one
  orchestrator behavior (§5) and that INVALID/UNKNOWN can never reach the geocoder.
- **Idempotency gap** — original design didn't state the fixed-point requirement; **Fixed:**
  D4 + constraint #7 + test 10.8 (this was a real regression risk via the geocode cache key).
- **Double-suffix regression** — **Fixed:** D3 (no suffix in canonical) + R5.
- **Wrapper contract regression** — **Fixed:** §7 preserves every caller's shape guarantee;
  10.10/10.11 lock it; reattach's `#`-probe verified to still work (and improve).
- **"Invent nothing" gap on ambiguous glued numbers** — **Fixed:** R6 + §6.2 ambiguity rule;
  irreparable → INVALID, re-ask, never guess a coordinate.
- **Canonical punctuation vs frozen converter** — third pass caught that the user's
  illustrative `Dg`/`Tv`/no-period examples would break the frozen
  `_to_google_address_format` (matches literal `Cra.` and knows `Tr.`, not `Tv.`). **Fixed:**
  §4.2 punctuation decision — emit period forms and `Tr.` for Transversal; documented the
  deviation and its rationale; test 10.1 updated to `Tr. 9 #7 Bis-21`.
- **Scope creep into geocoder** — checked: the triple-geocode divergence (R9) and missing
  `components=country:CO` (Google-format C2) are **documented, not fixed** here (frozen
  engine). No architectural gap remains inside the in-scope stage.

Second pass found no further material inconsistencies, duplicated ownership, ambiguous
states, or uncovered regressions. Design is internally consistent and implementation-ready.

### 13.1 Second-iteration review (mandatory decisions integrated)

Re-reviewed after folding in the binding decisions. Findings and fixes:

- **Two normalizers remained (`normalize_address`, `stt_enhancer` address repair).**
  **Fixed:** single-authority mandate (§0, D9) — `normalize_address` becomes a full-word
  render over the parser (§7.1), `reattach_address_details` becomes a component consumer
  (§7.2), and `stt_enhancer.repair_mangled_street_address` loses authority at F7 and is
  retired at F9 (§3.3). End-state: exactly one normalizer (R7, R10, R11).
- **Repairs were ad-hoc ids.** **Fixed:** closed `RepairKind` taxonomy (TOKEN_SPLIT,
  TOKEN_MERGE, LETTER_ATTACHMENT, NUMBER_ATTACHMENT, REMOVED_FILLER, NORMALIZED_SEPARATOR,
  ABBREVIATION_NORMALIZATION, STREET_ORDER_REBUILD) in the contract (§7), repair table
  (§6.2), costs (§6.5), logging (§8), tests (§10.13).
- **`repaired` field missing from the mandated contract.** **Fixed:** added to
  `ParsedAddress` (§7), asserted in 10.13.
- **Logging chain lacked PREPROCESS and VALIDATION.** **Fixed:** §8 now emits
  `RAW → PREPROCESS → TOKENS → AST → REPAIRS → VALIDATION → CANONICAL → STATE`; nothing is
  invisible.
- **AST-before-validation / reconstruct-from-AST not stated as invariants.** **Fixed:**
  mandatory invariants block in §6 (AST before validate; Parse→Repair→Validate; reconstruct
  reads AST only; regex confined to tokenizer).
- **Google isolation.** Confirmed and restated: the parser never appends city/dept/country
  and never shapes a Google query (D3, §0, §6.4). The geocoder alone owns the query + suffix.
- **Catalog category coverage.** **Fixed:** §9 enumerates the full mandated set (adds
  colegios, monumentos, parques, hospitales, clínicas, IPS, Comuna 9); augmentation is F8,
  additive, classification-only.
- **Invalidation specificity.** Confirmed: `invalid_reason` is a closed set naming the exact
  failing structural rule (§6.3), asserted in 10.13; no generic invalid state.

Third pass: no duplicated responsibilities (one authority; F9 removes the last overlap), no
ambiguous states (7 states, one behavior each, INVALID/UNKNOWN never geocoded), no open
decisions, no contradictions (punctuation/`Tr.`, suffix ownership, and idempotency all
reconciled), no undefined behavior (every state, every repair kind, every invalid reason
enumerated), and no evident architectural debt (F9 closes the only deferred item, gated and
reversible). Document is definitive.

---

## 15. Implementation reconciliations (objective contradictions found)

Two objective contradictions between the spec's illustrative examples and its own strict
rules surfaced during implementation. Both were resolved keeping the spec's spirit; the
architecture/decisions were **not** reopened.

- **R-A · §10.2 grouped `"3C 6"` with costly-repair cases.** `"3C 6"` tokenizes as an
  already-attached `NUM_LETTER` (`3C`) + `NUMBER` (`6`); with the `#` present ("número"),
  the placa forms with **no** costly repair (no split, no letter-attachment). The blanket
  "confidence < 1.0 / specific RepairKind" assertion cannot hold for it. **Resolution:** for
  `"3C 6"` the test asserts only the correct canonical + `STREET_ADDRESS`; the
  cost/kind assertions remain on the genuinely-repaired inputs (`3C6`, `3 C 6`, `8A14`, …).
  Spirit preserved: repairs are recorded only when a real repair happens.
- **R-B · §7.2 example `"Calle Cuarta número 26"` is itself incomplete.** Under the strict
  door rules it is `Calle 4 #26` — a single number after `#`, no cruce-distancia pair →
  `INVALID_ADDRESS_STRUCTURE`. There is no valid door to "recover" from it, so
  `reattach_address_details` correctly returns it unchanged (it will be re-asked).
  **Resolution:** `reattach` recovers a dropped placa only when the **raw** parses to a
  *complete* door (prod Case 2: `"Calle 5 carrera 17 28"` extracted down to `"Calle 5"` →
  recovers `"Cl. 5 #17-28"`); the test uses that real case. Spirit preserved (audit §3b): a
  dropped house number is recovered; a genuinely-incomplete address is not fabricated.

Both are test/example-level clarifications; no state, repair kind, grammar, or integration
decision changed.

## 16. Implementation status (phases executed)

| Phase | Status | Gate result |
|---|---|---|
| F0 tests | done | battery red (module absent) |
| F1–F5 parser module | done | 43/43 parser tests green |
| F6 wrappers (single authority) | done | full suite green |
| F7 orchestrator integration | done | full suite green (parser state drives flow) |
| F8 catalog augmentation | **deferred** (spec-declared additive/skippable; classification already delegates to `popayan_geodata`; not required for correctness) | n/a |
| F9 retire `repair_mangled_street_address` | done | NLU+filters green before/after; full suite green |

Final: `python -m pytest tests/` → **126 passed**, zero regressions (74 pre-existing + 52
new). `compileall` clean; `import main` OK. Not committed (no request).

## 14. Definition of done (this phase)

1. This document — complete, consistent, no open decisions. ✅
2. Self-review completed (§13). ✅
3. Phased, reversible implementation plan (§12). ✅

No code is written in this phase. Implementation begins only on explicit go-ahead, starting
at F0.
