# Google Geocoding / Places Query Format — Popayán, Cauca (Colombia)

**Scope:** strictly Colombia, tuned for **Popayán, Cauca**. This document (a) records EXACTLY how
`core/geocoder_service.py` calls Google today, (b) states Google Maps Platform best practice with
cited doc URLs, (c) gives the recommended canonical query per case, and (d) lists conflicts between
the two and how they are resolved.

**Builds on:** `docs/geocoding/CO_ADDRESS_NOMENCLATURE_REFERENCE.md` (nomenclature grammar + the
`Cra 9 #5-28, Popayán, Cauca, Colombia` recommendation, whose live Google ranking it flagged
UNVERIFIED). That flag still stands — see [UNVERIFIED](#unverified).

**Verification policy:** every Google-behavior claim is tied to a [Source](#sources) URL. Every repo
claim is tied to `file:line`. I **could not call the live Google Geocoding/Places API** from this
environment, so any *relative reliability ranking* between formats is **UNVERIFIED** and marked as
such — I do not fabricate rankings.

---

## 1. Current repo behavior (exact, with file:line)

All line numbers are `core/geocoder_service.py` unless noted. The pipeline order is
Autocomplete → Geocoding → Places Text Search → Nominatim (`run_pipeline`, lines 999–1041).

### 1.1 Address string normalizer — `_to_google_address_format` (lines 261–283)

Applied to the query **before** it is sent to the **Geocoding** and **Places Text Search** calls
(NOT to Autocomplete). It does two things:

- **Expands abbreviations to FULL words** (lines 275–280):
  `Cl.`→`Calle `, `Cra.`→`Carrera `, `Kr.`→`Carrera `, `Av.`→`Avenida `,
  `Tr.`→`Transversal `, `Diag.`→`Diagonal `.
- **Removes the space between `#` and the number** (line 282): `# 3CE-41` → `#3CE-41`.

Example in the docstring (lines 267–268): `"Cl. 16 # 3CE-41"` → `"Calle 16 #3CE-41"`.

### 1.2 Geocoding API call — `_google_get_candidates` (lines 286–358)

- Endpoint `https://maps.googleapis.com/maps/api/geocode/json` (line 48).
- **`address`** (line 301): `f"{google_query}, Popayán, Cauca, Colombia"` where `google_query =
  _to_google_address_format(query)` (line 298). So the string sent is the **full-word** road type
  plus the hard-appended suffix **`, Popayán, Cauca, Colombia`**.
- **`language`**: `"es"` (line 302).
- **`region`**: `"co"` (line 303).
- **`bounds`**: `"2.32,-76.82|2.58,-76.42"` (line 304) — SW `|` NE, biasing the Popayán area.
- **NO `components` filter is sent.** There is no `components=country:CO` on the Geocoding call.
- Takes `results[:6]`, keeps only those in the wide bbox (lines 326–354).

### 1.3 Places Autocomplete call — `_google_autocomplete` (lines 403–432)

- Endpoint `.../place/autocomplete/json` (line 375).
- **`input`** (line 410): the **normalized query as-is** — i.e. the **abbreviated** internal form
  (`Cl.`/`Cra.`), **with NO city suffix appended** (unlike Geocoding/Places). This is the primary
  resolver (`run_pipeline` line 999).
- **`components`**: `"country:co"` (line 413) — the only Google call that sends a components filter.
- **`language`**: `"es"` (line 412).
- **`location`**: `f"{_POPAYAN_CENTER[0]},{_POPAYAN_CENTER[1]}"` = `2.4419,-76.6063` (Parque Caldas)
  (line 414; `POPAYAN_CENTER` at `core/geo_types.py:28`).
- **`radius`**: `20000` m (line 415).
- Address-level predictions are identified by `types` in `{subpremise, premise, street_address}`
  (line 380, used at 398/516). Place Details then fetched for coords (`_google_place_details`,
  lines 435–485), requesting `fields=formatted_address,geometry,name,address_component` (line 445).

### 1.4 Places Text Search call — `_google_places_search` (lines 569–662)

- Endpoint `.../place/textsearch/json` (line 567). Used only when Geocoding returns weak
  (GEOMETRIC_CENTER) results (`run_pipeline` lines 1005–1037).
- **`query`** (line 584): `f"{google_query}, Popayán, Colombia"` where `google_query =
  _to_google_address_format(query)` (line 582). **NOTE: this suffix omits "Cauca"** — it is
  `Popayán, Colombia`, not `Popayán, Cauca, Colombia` (inconsistent with the Geocoding call).
- **`language`**: `"es"` (line 586); **`region`**: `"co"` (line 587).
- **`locationbias`**: `f"circle:20000@{_POPAYAN_CENTER[0]},{_POPAYAN_CENTER[1]}"` (line 588).
- No `components` (Text Search has no components param).

### 1.5 Nominatim fallback — `_nominatim_get_candidates` (lines 667–696)

- **`q`** (line 676): `f"{query}, Popayan, Cauca, Colombia"` (note: **`Popayan` without accent**, and
  the **raw query**, not `_to_google_address_format`'d).
- `countrycodes=co`, `viewbox=-76.82,2.58,-76.42,2.32`, `bounded=1` (lines 682–684).

### 1.6 Where the suffix / enrichment comes from (double-suffix risk)

- The `, Popayán, Cauca, Colombia` (Geocoding) / `, Popayán, Colombia` (Places) suffix is **appended
  inside the service** (lines 301, 584). Callers are expected to pass the bare address.
- `handle_user_context` enriches as `f"{original_query}, {geo_context}"` (line 1369) and the public
  `geocode()` appends `f"{query}, {barrio}"` (line 1423). If a caller (or a copy-pasted "canonical"
  string from the nomenclature doc) already ends in `, Popayán, Cauca, Colombia`, the Geocoding path
  would emit a **doubled** `…, Popayán, Cauca, Colombia, Popayán, Cauca, Colombia`. See
  [Conflict C1](#4-conflicts--resolutions).

---

## 2. Google Maps Platform best practice (cited)

### 2.1 Geocoding API `address` string

> "Specify addresses in accordance with the format used by the national postal service of the
> country concerned." Street-address elements are "delimited by spaces." [S-GEO]

Google Geocoding **does not resolve non-postal strings**:

> "Address geocoding does not resolve … unstructured strings that don't represent an address."
> Unsupported: **business names**, ambiguous queries, `P.O. Box`/`C/O` notations. [S-GEO]

**Implication:** barrios and landmarks (e.g. "Yanaconas", "Morro de Tulcán") are *place names*, not
postal addresses → they belong in **Places** (Text Search / Autocomplete), not the Geocoding API.

### 2.2 `components` filter (Geocoding)

- `components=country:CO` is a **strict, enforced filter** — country accepts a name or a two-letter
  ISO 3166-1 code. [S-GEO][S-COMP]
- `administrative_area` (e.g. Cauca) is an **influence-only** filter: it "may be used to influence
  results, but will not be enforced." [S-GEO][S-COMP]
- Do **not** repeat `country`/`postal_code`/`route` component filters or the API returns
  `INVALID_REQUEST`. [S-GEO]

### 2.3 `region`, `language`, `bounds` (Geocoding)

- `region=co` is a **ccTLD bias** only: "biasing only *prefers* results for a specific domain." [S-GEO]
- `language=es`: results returned in the preferred language; falls back to `Accept-Language`. [S-GEO]
- `bounds` is a **viewport bias** only — it "doesn't guarantee that all or any result(s) will be
  contained by it." [S-GEO]

So `region`, `bounds`, and the text suffix are all **biases**; only `components=country` is
**enforced**.

### 2.4 Places Autocomplete vs Geocoding for door addresses

- Autocomplete `input` is required and ranks candidates by relevance; `components=country:co` is a
  two-letter ISO filter; `location`+`radius` (or `locationbias`) biases toward an area; `types`
  restricts result kinds. [S-AC]
- Google's own guidance: **fall back to the Geocoding API when you expect only address input**, and
  Autocomplete "performs poorly" for **subpremise** addresses. [S-AC][S-AC2]

**Implication:** the repo's Autocomplete-first-then-Geocoding order is consistent with Google's
guidance (Autocomplete for indexed/typed places, Geocoding as the address fallback).

### 2.5 Colombian `#`, `-`, and road-type abbreviations

- Google Maps renders Popayán roads **abbreviated with a trailing period** — `Cra.`, `Cl.`, `Diag.`,
  `Tv.`, `Av.`, cardinal `Nte.` (observed display style). [S-NOM-DOC §Google Maps preferred format]
- Both abbreviated (`Cra`, `Cl`) and full (`Carrera`, `Calle`) road types are accepted by Colombian
  geocoders/guides. [S-CASACOL][S-MAPBOG]
- Keep the letter glued (`3C`, not `3 C`) and the placa hyphenated (`#3C-6`); a **Calle↔Carrera swap
  changes the location entirely** and is a hard error. [S-CASACOL]
- **Which of abbreviated vs full geocodes *best* through Google specifically is UNVERIFIED** (no live
  API call here) — see [UNVERIFIED](#unverified).

---

## 3. Recommended canonical query per case

These are the strings to **hand to the pipeline** (`run_pipeline` / `geocode`). Because the service
appends the city suffix itself (§1.6), **callers pass the bare address WITHOUT `, Popayán, Cauca,
Colombia`** to avoid the double-suffix bug. The "string that reaches Google" column shows what the
service will actually send after `_to_google_address_format` + suffix.

| Case | Pass to pipeline | String that reaches Google Geocoding | Rationale / source |
|---|---|---|---|
| **Full door address** (`Cra 52 #3C-6`) | `Cra. 52 #3C-6` (abbreviated internal form) | `Carrera 52 #3C-6, Popayán, Cauca, Colombia` (full word + suffix, line 298/301) | Postal-format string with space-delimited elements + enforced country context. [S-GEO] Letter glued, placa hyphenated. [S-CASACOL] |
| **Intersection** (`Cra 9 con Calle 5`) | `Cra. 9 con Calle 5` | `Carrera 9 con Calle 5, Popayán, Cauca, Colombia` | Resolves to a corner, not a door (no placa). [S-NOM-DOC] Google intersection syntax historically uses `and`/`&`; whether `con` or `&` geocodes better is **UNVERIFIED**. |
| **Barrio** (`Yanaconas`) | `Yanaconas` | (should route to **Places**, not Geocoding) `Yanaconas, Popayán, Colombia` via Text Search (line 584) | Geocoding "does not resolve … business names / unstructured strings." [S-GEO] Place names → Places. Autocomplete-first path (line 999) handles this. |
| **Named place / landmark** (`Morro de Tulcán`) | `Morro de Tulcán` | (Places) `Morro de Tulcán, Popayán, Colombia` | Same as barrio — a POI, not a postal address → Places, expect GEOMETRIC_CENTER centroid, which the pipeline accepts for named places (lines 1116–1125). [S-GEO] |

**City suffix rule:** append **`, Popayán, Cauca, Colombia`** exactly once, and let
`geocoder_service` add it. Do **not** pre-append it to the query. Bare numeric addresses collide
across dozens of Colombian towns; the city/dept/country context is what disambiguates Popayán.
[S-NOM-DOC][S-CASACOL]

**Abbreviated vs full road type:** the repo's hedge is reasonable — it sends **abbreviated** to
Autocomplete (matches Google's `Cra.`/`Cl.` display) and **full words** to Geocoding/Places. Keep it
until a live A/B says otherwise. Do **not** spell out `número` — use the literal `#`. [S-NOM-DOC]

---

## 4. Conflicts + resolutions

**C1 — Double city-suffix.** The nomenclature doc's canonical `Cra 9 #5-28, Popayán, Cauca,
Colombia` is the *full string to send to Google directly*, but `geocoder_service` **appends the
suffix itself** (lines 301, 584). Passing the doc's full string into the pipeline yields a doubled
suffix.
**Resolution — the repo wins.** Inside this project, callers pass the **bare** address
(`Cra. 9 #5-28`); the service owns the suffix. The nomenclature doc's canonical remains correct for
*direct* Google calls outside this pipeline. (Documented here so the two docs don't contradict.)

**C2 — Geocoding call omits `components=country:CO`.** Google says `country` is the only **enforced**
filter (§2.2); the Geocoding call currently relies on `region=co` + `bounds` + text suffix, all of
which are **bias-only** (lines 301–304).
**Resolution — the doc wins; recommend a code change.** Add `"components": "country:CO"` to the
Geocoding params (line 299 block) to hard-guarantee Colombia and cut cross-border collisions. Safe:
`administrative_area:Cauca` may be added too but is influence-only, so keep the text `Cauca` in the
suffix. (Autocomplete already sends `components=country:co`, line 413 — Geocoding just doesn't.)

**C3 — Places Text Search suffix drops "Cauca".** Line 584 sends `Popayán, Colombia`; Geocoding
sends `Popayán, Cauca, Colombia` (line 301).
**Resolution — align to include Cauca.** Low risk (Popayán is fairly unique), but making the Text
Search suffix `, Popayán, Cauca, Colombia` removes an inconsistency for free.

**C4 — Autocomplete gets abbreviated + no suffix; Geocoding gets full word + suffix.** Not a bug —
Autocomplete has `components=country:co` + `location`/`radius` bias (lines 413–415), and abbreviated
`Cra.`/`Cl.` matches Google's display strings. **No change**; note it so the asymmetry is intentional
and documented.

**C5 — Intersection `con` passed verbatim.** The pipeline does not translate `con` to Google's
`and`/`&` intersection syntax.
**Resolution — leave as-is pending a live test.** Whether `con` vs `&` geocodes better in Popayán is
**UNVERIFIED**; do not change blind.

---

## 5. Recommended `components` / params summary

| Call | `components` | `region` | `language` | area bias | Change? |
|---|---|---|---|---|---|
| Geocoding | **ADD `country:CO`** (currently none) | `co` (keep) | `es` (keep) | `bounds` (keep) | **C2** |
| Autocomplete | `country:co` (keep) | n/a | `es` (keep) | `location`+`radius` (keep) | none |
| Places Text Search | n/a | `co` (keep) | `es` (keep) | `locationbias` (keep) | suffix +Cauca (**C3**) |
| Nominatim | `countrycodes=co` (keep) | n/a | n/a | `viewbox`+`bounded` (keep) | none |

---

## 6. Sources

- **[S-GEO]** Google Maps Platform — *Geocoding API: address, components, region, language, bounds*.
  https://developers.google.com/maps/documentation/geocoding/requests-geocoding
  (address = national-postal format, space-delimited; does not resolve business names / unstructured
  strings; `components` country/postal_code enforced, administrative_area influence-only; `region`
  and `bounds` are bias-only.)
- **[S-COMP]** Google Maps Platform — *Geocoding component filtering* (same doc, Component Filtering
  section). country = ISO 3166-1 two-letter; administrative_area not enforced.
  https://developers.google.com/maps/documentation/geocoding/requests-geocoding#component-filtering
- **[S-AC]** Google Maps Platform — *Place Autocomplete: input, components (ISO 3166-1 country),
  language, location/radius/locationbias, types*.
  https://developers.google.com/maps/documentation/places/web-service/autocomplete
- **[S-AC2]** Google guidance that Autocomplete performs poorly on subpremise addresses and to fall
  back to the Geocoding API when only address input is expected (Autocomplete overview / best
  practices). https://developers.google.com/maps/documentation/places/web-service/autocomplete
- **[S-CASACOL]** Casacol — *A Quick Guide to Colombian Postal Addresses* (abbreviated + full both
  accepted; Calle/Carrera swap changes location; "con" intersection usage).
  https://en.casacol.co/colombian-postal-addresses/
- **[S-MAPBOG]** MapasBogotá (IDECA) — *Geocodificar* help (accepted road-type abbreviations).
  https://mapasbogota.gitbook.io/ayuda/geocodificar
- **[S-NOM-DOC]** In-repo `docs/geocoding/CO_ADDRESS_NOMENCLATURE_REFERENCE.md` — nomenclature
  grammar, Google display style `Cl. 18 Nte.`, canonical `Cra 9 #5-28, Popayán, Cauca, Colombia`,
  and its own UNVERIFIED-ranking flag.
- **Repo:** `core/geocoder_service.py` (lines cited inline in §1); `core/geo_types.py:28`
  (`POPAYAN_CENTER = (2.4419, -76.6063)`).

---

## UNVERIFIED

1. **Relative Google reliability ranking of abbreviated vs full road type vs `número` spelled out.**
   The live Google Geocoding/Places API could not be called from this environment. All format
   recommendations are grounded in Google docs [S-GEO][S-AC], Colombian guides [S-CASACOL][S-MAPBOG],
   and the in-repo nomenclature doc — **not** in a live A/B. Validate with real Popayán addresses
   before hard-coding a single "winning" format. (Carries forward the flag from
   `CO_ADDRESS_NOMENCLATURE_REFERENCE.md` §Open questions #1.)
2. **Intersection syntax:** whether `Carrera 9 con Calle 5` vs `Carrera 9 & Calle 5` vs
   `Carrera 9 and Calle 5` geocodes better through Google in Popayán — untested (C5).
3. **Whether adding `components=country:CO` to the Geocoding call measurably improves Popayán
   results** — recommendation is grounded in the enforced-filter doc [S-GEO][S-COMP], not measured
   here. Low risk (country is unambiguous), but the *magnitude* of improvement is unverified.
4. **Whether Autocomplete's abbreviated, suffix-less input outperforms a full-word, suffixed input**
   for Popayán door addresses (C4) — intentional asymmetry, not empirically compared.
