# Colombian Urban Address Nomenclature — Verified Reference

**Purpose:** Single source of truth for the text parser / tokenizer used by the Lyra taxi voice
assistant. Scope is **strictly Colombia**, tuned for **Popayán, Cauca**. All other countries and
address formats are out of scope.

**Verification policy:** Every rule below is tied to a numbered source in the [Sources](#sources)
section. Claims that could not be confirmed against an official/primary source are marked
**UNVERIFIED** inline and listed in [Open questions](#open-questions--unverified). Do not treat
UNVERIFIED items as fact in the parser without further checking.

**Two official standards exist and disagree on some abbreviations.** Colombia has more than one
government-issued abbreviation table:
- **DIAN / MUISCA** (national tax authority, RUT addresses) — uses `CR` for Carrera, `CIR` for
  Circular. [S1]
- **IGAC / UAECD** (cadastre — Instituto Geográfico Agustín Codazzi and Bogotá's cadastral unit) —
  uses `KR` for Carrera, `CQ` for Circular. [S5][S6][S9]

The parser must **accept both** (plus common informal variants) on input and should **normalize to
one** canonical internal form. Recommended canonical output for geocoding is the
Google-display style (abbreviated, see [Google Maps preferred format](#google-maps-preferred-format)).

---

## Canonical grammar

A Colombian urban address has two logical halves separated by `#` (the "numeral"):

1. **Vía generadora** (generating road) — the road the property's entrance faces:
   `<tipo de vía> <número principal>`.
2. **Placa** (address plate) — locates the door relative to the nearest cross street:
   `<número secundario (the cross road)> - <distancia en metros al acceso>`. [S3][S4][S7]

> "la placa de cualquier inmueble está compuesta por el número del cruce y el número de la distancia
> aproximada en metros que hay desde este hasta el acceso del inmueble." — Wikipedia,
> *Nomenclatura urbana* [S3]

So in **`Calle 5 # 17-28`**:
- `Calle 5` = vía generadora — the entrance is on Calle 5.
- `17` = número secundario — the nearest cross road is **Carrera 17**.
- `28` = placa/distance — the door is ~28 m from that Carrera-17 crossing. [S3][S4][S7]

Example from source: **`Carrera 10 #56-25`** = "on Carrera 10, 25 m from Calle 56". [S4][S7]

### EBNF-style grammar (for tokenizer/parser)

```ebnf
address        = via_generadora , "#" , placa , [ complemento ] ;

via_generadora = tipo_via , WS , numero_via ;
placa          = numero_cruce , "-" , distancia ;

tipo_via       = ( "Calle" | "Carrera" | "Avenida" | "Avenida Calle"
                 | "Avenida Carrera" | "Diagonal" | "Transversal"
                 | "Circular" | "Circunvalar" | "Autopista" | "Callejón"
                 | "Pasaje" | "Carretera" | "Variante" | "Vía"
                 | "Manzana" | tipo_via_abbrev ) ;

(* número on the vía generadora: digits, optional letter, optional Bis, optional cardinal *)
numero_via     = numero_core ;
numero_cruce   = numero_core ;
numero_core    = digitos , [ letra ] , [ WS , bis ] , [ WS , cardinal ] ;

digitos        = DIGIT , { DIGIT } ;              (* 1..3 typical *)
letra          = "A" | "B" | … | "Z" ;            (* uppercase, glued to digits, NO space *)
bis            = "Bis" , [ WS , letra ] ;          (* "Bis", "Bis A", "Bis B" *)
cardinal       = "Norte" | "Sur" | "Este" | "Oeste"
               | "Oriente" | "Occidente" | cardinal_abbrev ;

distancia      = DIGIT , { DIGIT } ;              (* metres; may itself carry a letter, e.g. 3C *)

complemento    = { WS , ( unidad_kw , WS , token ) } ; (* Apto/Torre/Bloque/Interior/Piso… *)

WS             = " " , { " " } ;
```

**Attachment / ordering rules** (confirmed by the OSM Colombia mapping guide, which mirrors IGAC
practice) [S2]:
- The **letter** is uppercase and glued to the number with **no space**: `4A`, `8C`, `25B`. [S2][S4]
- **Bis** is a separate token (space-separated), capitalized, and may be followed by a letter:
  `Bis`, `Bis A`, `Bis B`. [S2]
- The **cardinal** point is applied **last**, after any letter and Bis:
  correct order → `Calle 4A Bis B Sur`. [S2]
- The distance side (the placa's second number) **can also carry a letter**: `#3C-6`, `# 25B-10`.
  This is why STT strings like `3C6` are ambiguous (see [Spoken variants](#spoken-variants)). [S4][S7]

---

## Road types table

Official abbreviations. **DIAN** column = DIAN/MUISCA standard [S1]. **IGAC/UAECD** column = cadastral
standard [S5][S6][S9]. **Google display** = form Google Maps renders in Colombia (abbreviated with a
trailing period) — see notes and UNVERIFIED flag in [Google Maps preferred format](#google-maps-preferred-format).

| Spanish name | Meaning / orientation | DIAN abbr [S1] | IGAC/UAECD abbr | Google display (typical) | Common informal variants |
|---|---|---|---|---|---|
| Calle | Runs E–W; number rises going N [S3][S4] | `CL` | `CL` [S5][S9] | `Cl.` | Cll, Clle, Cra-confusion |
| Carrera | Runs N–S; number rises going W [S3][S4] | `CR` | `KR` [S5][S9] | `Cra.` | Cra, Kra, Kr, Cr, K |
| Avenida | Major thoroughfare | `AV` | `AV` [S5] | `Av.` | Avda, Ave |
| Avenida Calle | Avenue that behaves as a Calle | `AC` | `AC` [S5][S9] | `Av. Cl.` | Av Calle |
| Avenida Carrera | Avenue that behaves as a Carrera | `AK` | `AK` [S5][S9] | `Av. Cra.` | Av Carrera |
| Diagonal | Oblique to Calles (SW↔NE) [S3] | `DG` | `DG` [S5][S9] | `Diag.` | Diag, Dg |
| Transversal | Oblique to Carreras (NW↔SE) [S3] | `TV` | `TV` [S5][S9] | `Transv.` / `Tv.` | Trans, Tranv, Trvs, Tv, Tr |
| Circular | Curved/ring road | `CIR` | `CQ` [S5] | UNVERIFIED | Circ |
| Circunvalar | Ring / bypass road | `CRV` | UNVERIFIED | UNVERIFIED | Circunv |
| Autopista | Highway | `AUT` | UNVERIFIED | `Autop.` (UNVERIFIED) | Auto |
| Callejón | Small alley | `CLJ` | UNVERIFIED | UNVERIFIED | Callejon |
| Pasaje | Narrow/short passage [S3] | `PJ` | UNVERIFIED | UNVERIFIED | Pje, Pas |
| Carretera | Road (rural/intercity) | `CRT` | UNVERIFIED | UNVERIFIED | Ctra, Carr |
| Variante | Bypass variant | `VTE` | UNVERIFIED | UNVERIFIED | Var |
| Vía | Generic "way" | (none in [S1]) | UNVERIFIED | UNVERIFIED | Via |
| Manzana | Block (NOT a through-road; used in barrios/urbanizaciones) [S3] | `MZ` | UNVERIFIED | `Mz.` (UNVERIFIED) | Mza, Mzn |
| Anillo vial | Ring road | `AVIAL` | UNVERIFIED | UNVERIFIED | — |
| Kilómetro | Rural km marker | `KM` | UNVERIFIED | `Km` | — |

Notes:
- DIAN publishes the fullest single official list; every DIAN code above is verbatim from [S1].
- **The Carrera abbreviation is the biggest cross-standard conflict:** DIAN `CR` vs cadastre `KR`.
  Both are official; informal usage overwhelmingly writes `Cra` / `Cra.`. Accept all. [S1][S5][S8]
- The OSM Colombia guide (mirrors IGAC intent) recommends **writing road types in full**, not
  abbreviated, in the canonical data record. [S2]

---

## Modifiers table

| Modifier | Meaning | Attaches to | Position | Abbrev | Source |
|---|---|---|---|---|---|
| Letter `A`–`Z` | Subdivides a road inserted between two numbered roads (e.g. Calle 4A sits between Calle 4 and Calle 5) | número principal **and/or** número secundario | Immediately after digits, **uppercase, no space**: `4A`, `#3C-…` | (n/a — literal letter) | [S2][S3][S4] |
| `Bis` | An additional road immediately succeeding one of the same number (immediate succession) | número principal (and can appear on secundario) | After the letter (if any), space-separated: `29 Bis`, `4A Bis` | `Bis` | [S2][S3] |
| `Bis A` / `Bis B` | Bis roads further subdivided by a letter | número principal | After `Bis`, space-separated: `4A Bis B` | `Bis A` / `Bis B` | [S2] |
| `Norte` | Northern sector of the grid (N of the central axis) | applies to **Calle** number (typically) | Last, after letter/Bis: `Calle 4 Norte` | `Nte.` (Google) | [S2][S3][S10] |
| `Sur` | Southern sector | applies to **Calle** number (typically) | Last: `Calle 78 Sur` | `Sur` | [S2][S3][S4] |
| `Este` / `Oriente` | Eastern sector | applies to **Carrera** number (typically) | Last: `Carrera 5 Este` | `Este` / `Or.` (UNVERIFIED) | [S1][S3] |
| `Oeste` / `Occidente` | Western sector | applies to **Carrera** number (typically) | Last: `Carrera 5 Oeste` | `Oeste` / `Occ.` (UNVERIFIED) | [S1][S3] |

Cardinal abbreviations from DIAN [S1]: `NORTE`=Norte, `SUR`=Sur, `ESTE`=Este, `OESTE`=Oeste,
`O`=Oriente, `OCC`=Occidente. Google Maps in Popayán renders Norte as **`Nte.`** (observed: "Cl. 18
Nte."). The Norte/Sur↔Calle and Este/Oeste↔Carrera pairing is the common convention per [S3] but is a
**usage tendency, not an absolute rule** — treat the cardinal as a free suffix on either road type.

---

## Validity rules

A string is a **complete domiciliary address** (points to a specific door) only if it has **all** of:

1. A **tipo de vía** (Calle/Carrera/Av/Diagonal/Transversal/…). [S1][S3]
2. A **número principal** (digits, with optional letter/Bis/cardinal). [S3][S4]
3. The **`#` separator** (may be spoken/written as `#`, `No.`, `Nº`, `número`, `numeral`; see spoken
   variants). [S3][S4]
4. A **número secundario** (the cross road). [S3][S4]
5. A **`-` (hyphen)** and a **distancia/placa** number. [S3][S4][S7]

Minimal valid pattern:
```
<tipo> <num_principal> # <num_secundario> - <placa>
e.g.  Calle 5 # 17-28   |   Cra 52 #3C-6   |   Av. Cra 73B Sur # 4-10
```

**Structurally INVALID / incomplete (a road segment, not a door):**
- `Calle 5` alone → only the vía generadora; no placa. Geocodes to a whole street, not a house. [S3]
- `Calle 5 # 17` (no hyphen/distance) → cross-street known, door distance missing → ambiguous.
- Anything missing the tipo de vía → cannot disambiguate Calle vs Carrera, which **changes the
  location entirely**. Swapping Calle↔Carrera is a hard error, not a soft one. [S8]

**Weak-but-usable (partial):** `Cra 9 con Calle 5` / `Calle 5 con Carrera 17` — an *intersection*
(the word "con" = "with"). No placa, so it resolves to a corner, not a door. Acceptable as a
coarse pickup point; flag as approximate. [S8] (the "con" intersection form is **USAGE**, widely
used in speech; see spoken variants.)

**Letter placement validity:**
- A letter must be glued to a number (`4A`), never standalone (`Calle A` is invalid as a numbered
  road). [S2]
- `Bis` must follow a number (optionally a lettered number); `Calle Bis` alone is invalid. [S2]

---

## Google Maps preferred format

**What was actually observed / tested:**

1. **Nominatim (OpenStreetMap) geocoder, tested live** with query
   `Carrera 9 Calle 5 Popayan Cauca Colombia` → returned road field **`"Carrera 9"`** (full,
   unabbreviated), `city: "Perímetro Urbano Popayán"`, `county: "Popayán"`, `state: "Cauca"`,
   postcodes `190001/190002/190003`, organized by `Comuna 1–6`. This confirms Popayán's grid is
   fully mapped and that appending `Popayán Cauca Colombia` disambiguates cleanly. [S11]
2. **Google Maps display form** for Popayán renders **abbreviated with a trailing period and the
   cardinal abbreviated**: e.g. **`Cl. 18 Nte.`** (this exact form was supplied in the task and is
   consistent with Google's Colombia rendering of `Cra.`, `Cl.`, `Diag.`, `Tv.`, `Av.`). [S10 / task-observed]

**Recommended query format to send to the Google geocoder** (best-supported, based on documented
behavior — see UNVERIFIED note):
```
Cra 9 #5-28, Popayán, Cauca, Colombia
```
Rationale:
- Google (and Colombian geocoding guides) accept **both** abbreviated and full road types; the
  abbreviated `Cra`/`Cl`/`Diag`/`Tv`/`Av` matches Google's own display strings. [S4][S8][S12]
- **Always append `, Popayán, Cauca, Colombia`.** Bare numeric addresses collide across every
  Colombian city (the same `Calle 5 # 17-28` exists in dozens of towns). The city/dept/country
  suffix is what makes Popayán geocode unambiguously. [S8][S11]
- Keep the letter glued (`3C`, not `3 C`) and the placa hyphenated (`#3C-6`), matching canonical
  form. Mixing up Calle/Carrera is the single most damaging error. [S8]

**UNVERIFIED:** I could **not** run the live Google Maps Geocoding API from this environment, so the
relative reliability ranking of "abbreviated vs full vs `número` spelled out" for Google
specifically is **not empirically measured here**. The recommendation above is grounded in (a) the
observed Google display style `Cl. 18 Nte.`, (b) Colombian geocoding guides stating both forms are
accepted [S8][S12], and (c) the live Nominatim test [S11]. Validate against the live Google geocoder
with real Popayán addresses before hard-coding a single "winning" format.

---

## Spoken variants

Spanish speech → canonical token. **OFFICIAL** = documented in a nomenclature standard;
**USAGE** = real spoken/STT usage, not in an official spec (still must be handled by the parser).

| Spoken form (what STT hears) | Canonical token | Tag |
|---|---|---|
| "calle" | Calle | OFFICIAL [S1][S3] |
| "carrera" | Carrera | OFFICIAL [S1][S3] |
| "kra" / "cra" / "carrera" | Carrera | USAGE (informal abbr) [S8] |
| "avenida" | Avenida | OFFICIAL [S1] |
| "avenida carrera" | Avenida Carrera (AK) | OFFICIAL [S1] |
| "diagonal" | Diagonal | OFFICIAL [S1] |
| "transversal" | Transversal | OFFICIAL [S1] |
| "número" / "numero" | `#` separator | USAGE (most common spoken form of `#`) |
| "numeral" | `#` separator | USAGE |
| "almohadilla" | `#` separator | USAGE |
| "gato" | `#` separator | USAGE |
| "con" (e.g. "carrera 9 con calle 5") | intersection (corner, no placa) | USAGE [S8] |
| "guion" / "raya" / "menos" | `-` (placa hyphen) | USAGE |
| "a" / "be" / "ce" (letter names) | uppercase letter A/B/C glued to number | USAGE (spelled letters) |
| "cuatro a" → "4 a" | `4A` (glue, drop space) | USAGE |
| "bis" | Bis | OFFICIAL [S2][S3] |
| "bis a" / "bis be" | Bis A / Bis B | OFFICIAL [S2] |
| "norte" | Norte (→ `Nte.` for Google) | OFFICIAL [S1][S3] |
| "sur" | Sur | OFFICIAL [S1][S3] |
| "este" / "oriente" | Este / Oriente | OFFICIAL [S1][S3] |
| "oeste" / "occidente" | Oeste / Occidente | OFFICIAL [S1][S3] |

**Known glued/ambiguous STT hazards (USAGE — must be normalized):**
- `"3C6"` / `"tres ce seis"` → intended `3C - 6` (letter C on cross road, placa 6). Split
  digit-letter-digit as `<cruce=3C>-<placa=6>`. [derived from S4/S7 placa grammar]
- `"5 17 28"` (no `#`/`-` heard) → `Calle 5 # 17-28`: rebuild separators positionally
  (principal, cruce, placa). [S3][S4]
- `"cra nueve cinco veintiocho"` → `Cra 9 #5-28`.
- Spelled letters ("ce", "be") vs the cardinal "sur"/"este": disambiguate a trailing single spoken
  letter (attach to number) from a cardinal word (append as suffix).

---

## Popayán notes

- **Grid:** Popayán's historic center is a colonial **orthogonal (hipodámico) grid**: **Carreras run
  N–S, Calles run E–W**, same logic as the rest of Colombia. Center carreras are numbered ~2–11 and
  calles ~1–11. [S10]
- **Historic center:** Declared **Monumento Nacional (30 Dec 1959)**. Streets also carry traditional
  colonial names (e.g. *Calle del Comercio*, *Calle de la Herrería*, *Calle de Tulcán*), but modern
  **numeric grid addressing is what geocoders use**; the traditional names are cultural labels, not
  the addressing system. Do **not** rely on colonial street names for geocoding. [S10]
- **"Norte" is real in Popayán.** There is a central dividing axis; roads north of it take a
  **`Norte`** designation (Google renders it `Nte.`, e.g. `Cl. 18 Nte.`). So `Norte` in Popayán is a
  **sector suffix on the road number**, not a separate street. The parser must keep `Norte`/`Nte.`
  attached to the road it modifies. [S2][S3][S10 / task-observed]
- **Administrative structure:** Popayán is divided into **9 comunas** containing many **barrios**
  (e.g. Comuna 1: Modelo, Loma Linda, Prados del Norte). Barrio names are common in speech and on
  informal addresses (e.g. `CL 4 #7-59 Barrio Centro`) and are useful as a geocoding fallback /
  disambiguator, but a barrio alone is not a precise door. [S13][S14][S15]
- **Postal codes:** Urban Popayán uses `1900xx` (observed `190001`, `190002`, `190003`). [S11]
- **Address example seen in official-adjacent data:** `CL 4 #7-59 BARRIO CENTRO` (Supergiros Cauca
  agency list) — confirms the `CL <n> #<n>-<n> <Barrio>` pattern is used locally. [S15]

---

## Sources

1. **[S1]** DIAN — *Nomenclatura* (MUISCA abbreviation table, 2012). Complete official abbreviation
   list (AC, AK, AUT, AV, CIR, CL, CLJ, CR, CRT, CRV, DG, MZ, PJ, TV, NORTE, SUR, ESTE, OESTE, O,
   OCC…). https://www.dian.gov.co/atencionciudadano/formulariosinstructivos/Formularios/2012/Nomenclatura_2012.pdf
2. **[S2]** OpenStreetMap Wiki — *ES:Colombia/Guía para mapear/nomenclatura para calles*. Modifier
   rules: uppercase glued letter, `Bis`, cardinal applied last (`Calle 4A Bis B Sur`).
   https://wiki.openstreetmap.org/wiki/ES:Colombia/Gu%C3%ADa_para_mapear/nomenclatura_para_calles
3. **[S3]** Wikipedia (ES) — *Nomenclatura urbana*. Placa definition (cruce + distancia en metros),
   road-type orientations, Bis/letter/cardinal meaning.
   https://es.wikipedia.org/wiki/Nomenclatura_urbana
4. **[S4]** GeoPostcodes — *The full guide to Colombian address format*. `Carrera 10 #56-25`
   breakdown; CL/KR/CRA/AK/DG/TV; lettered numbers; normalization examples.
   https://www.geopostcodes.com/blog/the-full-guide-to-colombian-address-format/
5. **[S5]** IGAC / geospatial standards summary via search — IGAC normalized abbreviations
   (CL, KR, TV, DG, AV, AC, AK, CQ). (Secondary summary; primary IGAC instructive below at S9.)
6. **[S6]** Unidad Administrativa Especial de Catastro Distrital (UAECD, Bogotá) — *Nomenclatura*.
   Cadastral abbreviation practice (AK, KR, AC, CL, DG, TV).
   https://www.catastrobogota.gov.co/recurso/nomenclatura
7. **[S7]** Medellín Guru — *Street Addresses in Colombian Cities*. "Carrera 10 #56-25 = 25 m from
   Calle 56"; Cra/Kr/Kra/Ak variants. https://medellinguru.com/street-addresses-in-colombian-cities/
8. **[S8]** Casacol — *A Quick Guide to Colombian Postal Addresses*. Both abbreviated and full forms
   recognized; Calle/Carrera swap changes location; intersection ("con") usage.
   https://en.casacol.co/colombian-postal-addresses/
9. **[S9]** IGAC — *Instructivo para Direcciones / Estandarización de Abreviaturas de Dirección*
   (also mirrored at sistemamatriculas.gov.co/ayuda/direccciones.pdf — CL, KR, DG, TV standard).
   https://es.scribd.com/document/505876900/IGAC-Instructivo-para-Direcciones
10. **[S10]** Wikipedia (ES) — *Nomenclatura urbana del Centro Histórico de Popayán*. Colonial
    orthogonal grid, carreras N–S / calles E–W, `Norte` designation, colonial street names,
    Monumento Nacional 1959.
    https://es.wikipedia.org/wiki/Nomenclatura_urbana_del_Centro_Hist%C3%B3rico_de_Popay%C3%A1n
11. **[S11]** OpenStreetMap Nominatim — live query
    `Carrera 9 Calle 5 Popayan Cauca Colombia` → road `"Carrera 9"`, `Perímetro Urbano Popayán`,
    Comuna 1–6, postcodes 190001–190003.
    https://nominatim.openstreetmap.org/search?q=Carrera+9+Calle+5+Popayan+Cauca+Colombia&format=json&addressdetails=1
12. **[S12]** MapasBogotá (IDECA) — *Geocodificar* help. Accepted road-type abbreviations for
    geocoding (Av Cra, Cra, Av Cll, Cll, Diag, Trans; UAECD: AK, KR, AC, CL, DG, TV).
    https://mapasbogota.gitbook.io/ayuda/geocodificar
13. **[S13]** Alcaldía de Popayán — *Comunas de Popayán* (comuna/barrio structure).
    https://popayan.gov.co/MiMunicipio/Territorios/Comunas%20Popay%C3%A1n.pdf
14. **[S14]** Alcaldía de Popayán — *Plan de Ordenamiento Territorial (POT)*.
    https://www.popayan.gov.co/NuestraAlcaldia/MetasObjetivosDesempeo/Plan%20de%20Ordenamiento%20Territorial%20de%20Popay%C3%A1n.pdf
15. **[S15]** Supergiros Cauca — agency address list showing local format
    `CL 4 #7-59 BARRIO CENTRO`.
    https://supergiroscauca.com.co/wp-content/uploads/2024/09/AGENCIAS-HABILITADAS-NOMINAS-GUBERNAMENTALES-POPAYAN-SUR.pdf

---

## Open questions / UNVERIFIED

1. **Google Geocoding API reliability ranking (abbreviated vs full vs "número" spelled out).** Not
   empirically tested here — the live Google geocoder could not be called from this environment. The
   recommended `Cra 9 #5-28, Popayán, Cauca, Colombia` is evidence-based but must be validated live.
   [see Google Maps preferred format]
2. **IGAC/UAECD abbreviations for Circunvalar, Autopista, Callejón, Pasaje, Carretera, Variante,
   Manzana, Vía.** DIAN codes are confirmed [S1]; the cadastral (KR-standard) equivalents were not
   individually confirmed against a primary IGAC table. Marked UNVERIFIED in the road-types table.
3. **Google display abbreviations for Circular, Autopista, Transversal (Transv. vs Tv.), Este/Oeste
   (Or./Occ.?).** `Cl.`, `Cra.`, `Diag.`, `Av.`, `Nte.` are consistent with observation; the rest are
   inferred and marked UNVERIFIED.
4. **Exact Popayán "Norte/Sur" dividing axis.** Confirmed that a central axis exists and `Norte` is
   a sector suffix [S10], but the precise street/river that divides Norte from the rest, and whether
   a `Sur` suffix is also actively used in Popayán, was not pinned to a primary POT map.
5. **Cardinal↔road-type pairing as a hard rule.** [S3] states Norte/Sur tend to go with Calles and
   Este/Oeste with Carreras, but this is a tendency; DIAN [S1] lists all four cardinals generically.
   Parser should treat the cardinal as a free suffix, not enforce the pairing.
6. **DANE georreferenciador canonical string.** DANE runs an official address geocoder
   (geoportal.dane.gov.co) whose exact normalized output string was not captured; could be a better
   canonical target than Google display form. Worth a follow-up.
