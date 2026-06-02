# Geocodificación — Archivos Modificados

## Archivos NUEVOS

### `core/geo_types.py`
Dataclasses y enums del pipeline. No existía antes.
Contenido: `LocationType`, `ResolutionStatus`, `GeoCandidate`, `GeoResolution`.

### `docs/geocoding/` (esta carpeta)
Documentación del refactor. No existía antes.

### `migrations/004_geo_aliases.sql`
Tabla `geo_human_aliases` para Fase 2. Creada pero NO ejecutar hasta Fase 2.

---

## Archivos REESCRITOS

### `core/geocoder_service.py`
**Antes:** Pipeline de 4 capas con `resolve_canonical()` de popayan_geodata,
Nominatim primero, Google segundo.

**Después:** Pipeline limpio Cache→Google→Nominatim→CONTEXT_GATHERING.
Sin dependencia de popayan_geodata. Google primero (más completo para Colombia),
Nominatim como fallback. Toma todos los resultados de Google (`results[:6]`),
no solo `results[0]`. Decide por `location_type`, no por número mágico de confidence.

**Funciones eliminadas:**
- Import de `tools.popayan_geodata.resolve_canonical`
- Lógica que retornaba solo `results[0]`

**Funciones nuevas:**
- `run_pipeline(query, attempt)` → `GeoResolution`
- `_google_get_candidates(query)` → `list[GeoCandidate]`
- `_nominatim_get_candidates(query)` → `list[GeoCandidate]`
- `_decide(candidates, query, attempt)` → `GeoResolution`
- `_verify_result_matches_query(formatted_address, query_base)` → `bool`
- `should_auto_accept(candidate)` → `bool`

---

### `core/address_utils.py`
**Antes:** Contenía `POPAYAN_PLACES` (catálogo manual de ~200 barrios),
`_try_local_match()` que llamaba a `resolve_canonical()`,
import de `tools.popayan_geodata`.

**Después:** Limpiado. Conserva solo utilidades de NLP/STT.

**Eliminado:**
- `POPAYAN_PLACES` dict (~350 líneas) — catálogo manual de barrios
- `_try_local_match()` — llamaba a popayan_geodata
- Import de `tools.popayan_geodata.ALL_BARRIOS`
- Import de `tools.popayan_geodata.geocode_local` en `_nominatim_geocode()`

**Conservado:**
- `_SPEECH_CORRECTIONS` — correcciones STT (no geocodificación, solo texto)
- `normalize_colombian_address()` — normalización de nomenclatura
- `_strip_preamble()` — eliminar saludos/relleno
- `_parse_si_no()` — interpretar afirmativo/negativo
- `_is_correction_request()`, `_is_repeat_request()`
- `extract_pickup_address()`, `extract_destination_address()` — adaptados

---

## Archivos ACTUALIZADOS

### `migrations/003_location_cache.sql`
**Añadido:** columnas `confidence DECIMAL(4,3)` y `location_type ENUM(...)`.
La tabla existía antes, solo se añaden columnas.

### `.env.example`
**Añadido (si aplica):** cualquier nueva variable de entorno para el pipeline.

---

## Archivos NO MODIFICADOS (pero relacionados)

### `tools/popayan_geodata.py`
**Estado:** Conservado en repositorio. **No eliminado.**
**Uso permitido:** `_SPEECH_CORRECTIONS` puede seguir usando datos de STT de este archivo.
**Uso prohibido:** Geocodificación, coordenadas, resolución de ubicaciones.

### `core/stt_enhancer.py`
Puede seguir usando `POPAYAN_STT_CORRECTIONS` de popayan_geodata si es necesario.
No usar `geocode_local()`, `resolve_canonical()`.

---

## Imports eliminados en cada archivo

### `core/geocoder_service.py`
```python
# ELIMINAR:
from tools.popayan_geodata import resolve_canonical
from core.address_utils import normalize_colombian_address  # ahora importa directo
```

### `core/address_utils.py`
```python
# ELIMINAR:
from tools.popayan_geodata import resolve_canonical
from tools.popayan_geodata import ALL_BARRIOS
from tools.popayan_geodata import geocode_local
```

---

## Notas para futuros cambios

- Si se añade una nueva fuente de geocodificación (ej. HERE Maps, Mapbox),
  añadir en `core/geocoder_service.py` siguiendo el patrón de `_google_get_candidates`
- Si se cambia el esquema de `location_cache`, crear `migrations/005_...sql`
- Los tests de geocodificación deben mockear `_google_get_candidates` y `_nominatim_get_candidates`,
  no el cliente HTTP directamente
- `POPAYAN_URBAN_BBOX` y `POPAYAN_BBOX_WIDE` están definidos en `core/geo_types.py`,
  no duplicar en otros módulos
