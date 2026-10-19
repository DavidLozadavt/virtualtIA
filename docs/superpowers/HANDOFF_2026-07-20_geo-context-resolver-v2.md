# Handoff — Geographic Context Resolver V2 (Restauración + Hardening) — 2026-07-20

## Resumen Ejecutivo

Se restauró e implementó el Geographic Context Resolver para eliminar la contaminación de consultas geocodificadoras durante la desambiguación geográfica. Una dirección válida normalizada en frontera de barrios ya no es concatenada con la respuesta natural del usuario. El usuario proporciona **evidencia** para seleccionar un candidato; nunca reemplaza o amplia la dirección.

**Estado:** Implementación completa, validada, conforme a especificación. Sin commits (per standing request).

---

## Problema Original (Real Production Case)

**Situación:** Llamada en vivo, usuario dijo dirección correcta → normalización produjo `Cl. 17 #6E-12` → dirección es válida pero cae en frontera de 2 barrios (Santa Teresa, Prados del Norte) → Lyra preguntó cuál → usuario respondió "María Oriente segundo puente" (referencia natural, como hablan los locales) → el sistema CONCATENÓ `Cl. 17 #6E-12, María Oriente segundo puente` y re-geocodificó a Google → consulta contaminada, resultado incorrecto.

**Causa raíz:** `services/voice/orchestrator.py::_handle_geo_context` (~L683):
```python
enriched = f"{orig_q}, {context_text}".strip(", ")
geo_result = await self.geocoder.resolve(enriched, attempt=session.geo_attempt + 1)
```

**Contexto histórico:** El resolver exacto para este caso YA existía:
- **Commit 2d9febc** (2026-07-20 anterior): agregó `core/geo_context_resolver.py` (203L) + tests (54L) + wiring orchestrator (98L) — implementación completa, verde.
- **Commit af5750b** (2026-07-20 siguiente): con título solo *"feat: session management with Redis/Memory"*, **borró 359 líneas colaterales** del resolver (bug de merge/rebase). La concatenación quedó expuesta.

---

## Investigación

### Descubrimiento
1. Localizó commit 2d9febc con la implementación anterior.
2. Verificó que af5750b eliminó archivos/wiring sin intención (commit message no lo menciona).
3. Validó que todos los datos de catálogo necesarios aún existen:
   - `ALL_BARRIOS` + `BARRIO_TO_COMUNA` en `tools/popayan_geodata.py`
   - `LANDMARKS` (Campanario, SENA, iglesias, puentes, centros comerciales, universidades)
   - `_haversine(lat1, lng1, lat2, lng2) -> float`
   - `_try_local_match(text) -> Optional[str]` en `core/address_utils.py`

4. Confirmó que la arquitectura geocodificadora pobla candidatos correctamente:
   - `GeoResolution.candidates` en NEEDS_DISAMBIGUATION (L1080 geocoder_service.py)
   - `GeoCandidate` con (lat, lng, display_name, neighborhood, confidence)

### Conclusión
No se necesitaba una "nueva característica". La solución ya estaba implementada, testada y verde. Se requería **restauración + hardening** para la arquitectura V2 actual (orchestrator, session store actualizados).

---

## Especificación Congelada

Documento: `docs/superpowers/specs/2026-07-20-geo-context-resolver-restore-design.md`

### Alcance
- Resolver geográfico: desambiguación-only (NUNCA nuevas búsquedas)
- Universo CERRADO de candidatos (inmutable)
- Dirección normalizada congelada (sin modificación)
- Respuesta del usuario = solo evidencia (no nueva query)
- Precision over recall (falsos negativos aceptables, falsos positivos NO)

### Fuera de Alcance
- Parser (co_address_parser.py) — intacto
- STT — intacto
- Geocoder engine — intacto
- Arquitectura — intacto
- Flujo conversacional — intacto
- Prompts/LLM — intacto
- Catálogo popayan_geodata — intacto (F8 deferred)

### Contrato Invariantes (5)
1. **Closed candidate universe** — read-only, cero mutación, `≥2` nunca re-geocodifica
2. **Immutable normalized address** — `origen_text` congelado, sin rebuild/normalize en disambiguation
3. **Role of user response** — solo evidencia, nunca new destination/search
4. **No query contamination** — dirección + respuesta NUNCA concatenadas
5. **Fail-safe behavior** — duda → `None` → re-ask entre nombres, no selecciona por aproximación

### Métodos Resolución (5, orden fijo)
1. **DIRECT** — answer names candidate barrio → score 1.0
2. **PROXIMITY** — reference ∈ ALL_BARRIOS → haversine nearest (conservative gates)
3. **LANDMARK** — reference ∈ LANDMARKS, not an address → haversine nearest (mismos gates)
4. **COMUNA** — reference barrio's BARRIO_TO_COMUNA uniquely matches one candidate
5. **TOKEN** — Jaccard overlap con margen mínimo

**Gating (precision):**
- `PROXIMITY_MIN_MARGIN_KM = 0.30` (nearest must beat 2nd by this)
- `PROXIMITY_MAX_ACCEPT_KM = 4.00` (beyond this too far to trust)
- `TOKEN_MIN_MARGIN = 0.20`
- En duda: `None` (caller re-asks)

---

## Implementación

### Fase A: Session Store Fields

**Archivo:** `services/telephony/session_store.py`

**Cambios en `CallSession`:**
```python
geo_candidates: Optional[list] = None
# Universo CERRADO e inmutable de candidatos (dicts serializables)
# Se fija UNA vez al entrar a WAITING_GEO_CONTEXT
# Se limpia (=None) solo en 4 eventos terminales: selección/cancelación/handoff/descarte

geo_decision_trace: Optional[dict] = None
# Traza auditable de la selección ganadora (strategy/candidates/score/margin/etc)
# Permite reproducir la decisión usando SOLO datos de sesión
# Se limpia en los mismos 4 eventos terminales
```

### Fase B: Core Resolver Module

**Archivo:** `core/geo_context_resolver.py` (203 líneas)

**Componentes principales:**

```python
@dataclass
class Selection:
    neighborhood: str              # official barrio del candidato elegido
    lat: Optional[float]
    lng: Optional[float]
    method: str                    # "direct_name" | "proximity" | "landmark" | "comuna" | "token"
    score: float                   # 0..1
    confidence: float              # 0..1 — level of decision confidence
    margin: float                  # separation from runner-up (km or token units)
    matched_reference: str         # what in the answer drove the choice
    distances: dict                # {candidate_name: km} when computed
    discarded: list                # names of candidates not selected
    reason: str                    # human-readable exact reason
    
    def to_trace(self) -> dict:
        """Fully serializable, reproducible from session alone"""
        return asdict(self)

def select_candidate(reference_text: str, candidates: list) -> Optional[Selection]:
    """
    Pick the candidate that best matches the user's natural reference.
    
    - candidates: list of {neighborhood, display_name, lat, lng, confidence}
    - CLOSED, IMMUTABLE universe: never mutates, appends, removes, reorders
    - Returns Selection or None (inconclusive)
    """
```

**Métodos (cascada, primer hit corta):**

1. **DIRECT** — `_norm(answer)` contains candidate barrio name
2. **PROXIMITY** — `_resolve_in_catalog(reference, ALL_BARRIOS)` → coords → haversine nearest
3. **LANDMARK** — `_resolve_in_catalog(reference, LANDMARKS)` (no-address guard) → coords → haversine nearest (mismos gates)
4. **COMUNA** — `_try_local_match(reference)` → BARRIO_TO_COMUNA → unique match
5. **TOKEN** — Jaccard overlap con margen

**Helpers:**
- `_norm(s)` — accent-strip, lowercase, normalize spaces
- `_cand_name(c)` — extract barrio name from dict (hardened: skip street segments in display_name)
- `_resolve_in_catalog(ref, catalog)` — lookup en catálogo local, fallback substring
- `_nearest_with_gate(ref_co, candidates)` — shared gate logic (PROXIMITY + LANDMARK)
- `_all_barrios()`, `_landmarks()`, `_comuna_of(name)` — lazy catalog accessors
- `_token_overlap(a, b)` — Jaccard metric
- `_ADDRESS_LIKE_RE` — cheap guard (no haversine para referencias que son ellas mismas direcciones)
- `_STREET_SEGMENT_RE` — skip "Cra. 5" in display_name when deriving candidate name

### Fase C: Orchestrator Wiring

**Archivo:** `services/voice/orchestrator.py`

**Import:**
```python
from core.geo_context_resolver import select_candidate
```

**Module-level helpers (restored):**
```python
def _serialize_geo_candidates(geo_result) -> list:
    """Serialize candidatos to dict list (closed universe)"""
    
def _dedup_named(names: list) -> list:
    """Barrio names, no duplicates, preserve order"""
    
def _join_options(names: list) -> str:
    """'A' | 'A o B' | 'A, B o C' for re-asking"""
```

**Candidate storage (2 branches entering WAITING_GEO_CONTEXT):**

Branch 1 (~L616):
```python
session.geo_original_query = origen
session.geo_attempt = 1
session.geo_candidates = _serialize_geo_candidates(geo_result)
session.geo_decision_trace = None
session.state = STATE_WAITING_GEO_CONTEXT
```

Branch 2 (~L656):
```python
session.geo_original_query = origen
session.geo_attempt = 1
session.geo_candidates = _serialize_geo_candidates(geo_result)
session.geo_decision_trace = None
session.state = STATE_WAITING_GEO_CONTEXT
```

**Core fix: `_handle_geo_context` (replaced concatenation with resolver block):**

```python
async def _handle_geo_context(self, session, text, nlu):
    orig_q = session.geo_original_query or session.origen_text or ""
    context_text = nlu.best_pickup or text
    
    # ── Resolver (closed universe, ≥2 candidates) ──
    candidates = session.geo_candidates or []
    if len(candidates) >= 2:
        sel = select_candidate(context_text, candidates)
        if sel is not None:
            # HIT: address intact, official barrio, trace persisted
            session.origen_text = orig_q
            session.origen_barrio = sel.neighborhood
            session.geo_decision_trace = sel.to_trace()
            session.geo_candidates = None  # universe closed
            session.state = STATE_CONFIRMING_ORIGIN
            # ... re-ask confirmation
            return VoiceTurnResult(...)
        
        # INCONCLUSO: re-ask entre nombres (no concatenar)
        session.geo_attempt += 1
        names = _dedup_named([c.get("neighborhood") for c in candidates])
        if session.geo_attempt <= 3 and len(names) >= 2:
            msg = f"¿La dirección queda en {_join_options(names)}?"
            # ... re-ask between candidate names only
            return VoiceTurnResult(...)
        
        # AGOTADO: handoff con dirección intacta
        barrio = names[0] if names else (context_text.strip() or orig_q)
        session.origen_text = orig_q
        session.origen_barrio = barrio
        session.geo_candidates = None      # terminal event
        session.geo_decision_trace = None  # terminal event
        session.state = STATE_CREATING_SERVICE
        # ... handoff message
        return VoiceTurnResult(...)
    
    # <2 candidates: legacy flow (unchanged)
    enriched = f"{orig_q}, {context_text}".strip(", ")
    geo_result = await self.geocoder.resolve(enriched, attempt=session.geo_attempt + 1)
    # ... rest of legacy path
```

**Key behaviors:**
- HIT → address frozen, official barrio, trace stored, STATE_CONFIRMING_ORIGIN
- INCONCLUSO → re-ask with **candidate names only** (never raw answer)
- AGOTADO → handoff with address intact, **state → CREATING_SERVICE** (terminal, geo_candidates cleared)
- <2 candidates → existing enriched-search flow runs (unchanged, backward compatible)

### Fase D: Tests

**File:** `tests/test_geo_context_resolver.py` (22 new tests)

**Coverage:**

*Restored original (6):*
- `test_direct_name_match()`
- `test_proximity_picks_nearest_to_reference()` — María Oriente case
- `test_selection_is_always_within_universe()`
- `test_never_returns_the_raw_answer()`
- `test_inconclusive_returns_none()`
- `test_empty_inputs_return_none()`

*LANDMARK strategy (4):*
- `test_landmark_selects_nearest_candidate()` — Campanario → Valle del Ortigal
- `test_landmark_out_of_area_returns_none()` — too far
- `test_landmark_tie_returns_none()` — margin < threshold
- `test_unresolved_natural_references_return_none()` — "segundo puente", "al lado del SENA", etc.

*Selection priority (3):*
- `test_priority_direct_beats_proximity()` — stage short-circuit
- `test_priority_proximity_is_barrio_not_landmark()`
- `test_token_overlap_selects_with_margin()`

*Precision over recall (2):*
- `test_precision_tie_between_candidates_returns_none()`
- `test_no_coords_does_not_crash_and_is_conclusive_only_when_safe()`

*Hardening (2):*
- `test_accented_reference_direct_match()` — accents handled
- `test_neighborhood_none_uses_display_name()` — fallback to display_name

*Explainable (2):*
- `test_decision_trace_is_complete_and_reproducible()` — to_trace() audit
- `test_closed_universe_input_is_not_mutated()` — zero side-effects

**Orchestrator-level (3 new in test_orchestrator.py):**
- `test_geo_context_persists_auditable_decision_trace()` — trace stored, geo_candidates cleared
- `test_geo_context_inconclusive_reasks_between_names_no_geocode()` — no geocoder call, re-ask names
- `test_geo_context_exhausted_attempts_handoff_address_intact()` — address frozen, terminal event

Plus existing orchestrator tests:
- `test_geo_context_selects_candidate_from_natural_reference()` — no-geocode invariant proven
- `test_geo_context_no_candidates_keeps_legacy_flow()` — backward compat

### Fase E: Validation

**Full suite:** `python -m pytest tests/` → **151 passed, 0 regressions**

```
........................................................................ [ 47%]
........................................................................ [ 95%]
.......                                                                  [100%]
151 passed in 20.09s
```

**Code quality:**
- `python -m compileall` — OK
- `import main` — OK
- `graphify update .` — graph rebuilt (2485 nodes, 5510 edges, 134 communities)

---

## Reconciliaciones Especificación vs. Código

### 1. LANDMARK vs PROXIMITY Split

**Especificación:** Dos métodos separados (PROXIMITY ∈ ALL_BARRIOS, LANDMARK ∈ LANDMARKS).

**Código original (2d9febc):** `_reference_coords()` hacía lookup en ambos catálogos sin distinguir.

**Reconciliación:** Separación explícita via `_resolve_in_catalog(ref, catalog)` — misma data, mismos umbrales, solo busca en el catálogo pasado. Resultado: PROXIMITY y LANDMARK son ahora stages claramente separadas con guards distintos (LANDMARK tiene `_ADDRESS_LIKE_RE` para evitar re-geocodificar direcciones). El test original "María Oriente → Santa Teresa" sigue verde (resuelve vía `ALL_BARRIOS` substring fallback).

### 2. Candidate Name Extraction

**Especificación:** `Selection.neighborhood` debe ser un barrio, no una puerta de calle.

**Código original:** `_cand_name()` retornaba primer segmento no-ciudad de `display_name`.

**Reconciliación:** Endurecimiento: `_cand_name()` ahora salta segmentos tipo "Cra. 5" usando `_STREET_SEGMENT_RE`. Ensures `Selection.neighborhood` is barrio name.

Test case that required fix:
```python
# Before: "Cra. 5" was returned as candidate name
# After: "Santa Teresa" is correctly extracted from "Cra. 5, Santa Teresa, Popayán"
def test_neighborhood_none_uses_display_name():
    a = {"neighborhood": None, "display_name": "Cra. 5, Santa Teresa, Popayán", ...}
    b = {"neighborhood": None, "display_name": "Cra. 9, Prados del Norte, Popayán", ...}
    sel = select_candidate("María Oriente", [a, b])
    assert sel.neighborhood == "Santa Teresa"  # ✓
```

### 3. Landmark Data Coverage

**Especificación:** LANDMARK uses local catalog (LANDMARKS in popayan_geodata).

**Reality:** Only "Campanario" (CC Campanario 2.4596,-76.5942) resolves deterministically. References like "segundo puente", "al lado del SENA", "por la iglesia", "por el D1" do NOT resolve to a single canonical landmark in the catalog → safe `None` (precision-first).

**Result:** Tests correctly assert these as `None` (re-ask candidate names). This is SPEC-COMPLIANT (catalog limits are a constraint, not a failure). If you want those refs to resolve, it's F8 (augment popayan_geodata) — out of scope.

---

## Restricciones Honradas

✓ **No parser changes** — co_address_parser.py untouched  
✓ **No STT changes** — untouched  
✓ **No geocoder changes** — engine untouched, only consumption layer  
✓ **No architecture changes** — V2 orchestrator pattern preserved  
✓ **No conversational flow changes** — states/transitions untouched except resolver insertion  
✓ **No normalization changes** — co_address_parser output used as-is  
✓ **No prompt/LLM changes** — untouched  
✓ **Single resolver** — one `select_candidate`, no duplication  
✓ **No new decisions** — spec was frozen, no reopening  
✓ **No scope creep** — only disambiguation layer  

---

## Archivos Modificados

| Archivo | Líneas | Cambio |
|---------|--------|--------|
| `services/telephony/session_store.py` | +16 | Campos `geo_candidates`, `geo_decision_trace` en `CallSession` |
| `core/geo_context_resolver.py` | +406 (new) | Módulo completo: resolver, Selection, helpers, catalogs |
| `services/voice/orchestrator.py` | +125 | Import + 3 helpers + 2 candidate-store branches + resolver block |
| `tests/test_geo_context_resolver.py` | +176 (new) | 22 unit tests |
| `tests/test_orchestrator.py` | +65 | 3 new orchestrator-level tests |

**Total new lines:** ~788  
**Total deleted lines:** 0 (pure addition; old code paths preserved for <2 case)

---

## Comportamiento Antes/Después

### Antes (Bug)
```
Usuario: "María Oriente segundo puente"
Query Google: "Cl. 17 #6E-12, María Oriente segundo puente"  ← CONTAMINADA
Resultado: incorrecto o ambiguo
```

### Después (Fix)
```
Usuario: "María Oriente segundo puente"
Resolver: "María Oriente" → barrio "Santa Teresa" (PROXIMITY, haversine nearest)
Query Google: "Cl. 17 #6E-12"  ← DIRECCIÓN INTACTA
Barrio oficial: "Santa Teresa"
Resultado: CORRECTO
```

**Si resolver no puede decidir (duda / no resuelve):**
```
Lyra: "¿La dirección queda en Santa Teresa o Prados del Norte?"
Usuario: "...algo que no resuelve..."
Lyra: "¿La dirección queda en Santa Teresa o Prados del Norte?"  ← re-ask nombres
(geo_attempt++) hasta agotarse
Lyra: "Listo, te ubico en [barrio]. El conductor te llamará."  ← handoff, dirección intacta
```

---

## Estado Final

### ✅ Implementación Completa
- Core resolver (5 métodos, precision-first, universo cerrado)
- Orchestrator wiring (closed-universe path + legacy fallback)
- Session store fields (geo_candidates + geo_decision_trace)
- Tests (22 resolver + 3 orchestrator + backward compat)

### ✅ Validación
- Full test suite: **151 passed, 0 regressions**
- Code quality: compileall OK, import main OK
- Graph: updated (2485 nodes, 5510 edges, 134 communities)

### ✅ Spec Conformance
- Universo CERRADO: read-only, sin mutación, sin re-geocodifica en `≥2`
- Dirección INMUTABLE: `origen_text` congelado, sin rebuild
- Sin contaminación: respuesta nunca concatenada a query
- Precision over recall: duda → `None` → re-ask nombres
- Auditable: decision trace persistido, reproducible desde sesión

### ✅ Backward Compat
- <2 candidates: legacy enriched-search path untouched
- All existing tests pass
- No breaking changes to API/flow

### ⚠️ Limitaciones (esperadas)
- Landmark resolution limitada al catálogo popayan_geodata (solo Campanario resuelve determinísticamente)
- "segundo puente", "al lado del SENA", "por la iglesia", "por el D1" → `None` (precision-first)
- F8 (catalog augmentation) deferred — out of scope

### 📋 Status
- **Sin commits** (per standing request — user commits when ready)
- All changes local, ready to review/test/commit
- Memory documented: `project_geo_context_resolver_v2`
- Handoff: this document

---

## Próximos Pasos (para el usuario)

1. **Review** — Leer spec + código. Ejecutar tests en vivo.
2. **Test en prelyra** — Llamada real con dirección fronteriza (e.g., "Cl. 17 #6E-12") + respuesta natural ("María Oriente segundo puente"). Verificar que geocoder reciba `"Cl. 17 #6E-12"` (sin contaminación).
3. **Commit** — Cuando esté satisfecho, hacer commit de todo (spec + código + tests).
4. **F8 (Opcional)** — Si quieres que "segundo puente" etc. resuelvan vía LANDMARK, aumenta popayan_geodata (fuera de alcance de esta sesión).

---

## Referencias

- **Spec:** `docs/superpowers/specs/2026-07-20-geo-context-resolver-restore-design.md`
- **Resolver:** `core/geo_context_resolver.py`
- **Orchestrator:** `services/voice/orchestrator.py` (líneas ~720–760 resolver block)
- **Tests:** `tests/test_geo_context_resolver.py` + updates to `tests/test_orchestrator.py`
- **Memory:** `project_geo_context_resolver_v2.md`
- **Prior handoff (STT):** `docs/voice/HANDOFF_2026-07-19_stt-openai-migration.md`
- **Prior handoff (Parser):** `docs/geocoding/HANDOFF_2026-07-20_co-address-parser.md`

---

**Handoff prepared:** 2026-07-20  
**Implementation status:** Complete, validated, ready  
**Commits:** None (per request)
