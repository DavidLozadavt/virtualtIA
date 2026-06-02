# Geocodificación — Arquitectura Técnica

## Separación de Responsabilidades

### Verdad Técnica: `location_cache`

Responde: **¿Qué coordenadas tiene esta query exacta?**

- La key es la `canonical_query` normalizada
- Queries bare y enriched son entradas **distintas e independientes**
- Ejemplo: `"Cl. 16 # 3CE-41"` y `"Cl. 16 # 3CE-41, Santa Teresa"` son dos rows separadas

### Verdad Humana: `geo_human_aliases` *(Fase 2 — diferida)*

Responde: **¿Cómo describe la gente esta dirección?**

- Aprende de las respuestas de los usuarios durante CONTEXT_GATHERING
- No es fuente de coordenadas, es fuente de contexto para enriquecer queries
- Ver `03-phase2-deferred.md` para diseño completo

---

## Tipos de Datos Core (`core/geo_types.py`)

```python
class LocationType(Enum):
    ROOFTOP             # Google: dirección exacta confirmada
    RANGE_INTERPOLATED  # Google: interpolada entre rooftops conocidos
    GEOMETRIC_CENTER    # Google: centroide de zona — NO auto-aceptar
    APPROXIMATE         # Google: estimación gruesa — NO auto-aceptar
    NOMINATIM_HIGH      # Nominatim: importance >= 0.75
    NOMINATIM_LOW       # Nominatim: importance < 0.75
    MANUAL              # Entrada manual por operador
    CACHE               # Vino de cache, type original desconocido

class ResolutionStatus(Enum):
    RESOLVED            # Coordenadas aceptadas
    NEEDS_DISAMBIGUATION # Hay opciones históricas — usuario debe elegir
    CONTEXT_GATHERING   # No hay info suficiente — preguntar al usuario
    FAILED              # Inresolvable — escalar a operador

@dataclass
class GeoCandidate:
    lat: float
    lng: float
    display_name: str
    neighborhood: Optional[str]   # de address_components de Google
    confidence: float             # 0.0–1.0
    source: str                   # "google" | "nominatim" | "cache"
    location_type: LocationType
    raw: dict                     # respuesta cruda de la API

@dataclass
class GeoResolution:
    status: ResolutionStatus
    query: str                    # query exacta usada
    candidates: list[GeoCandidate]
    selected: Optional[GeoCandidate]
    disambiguation_question: Optional[str]
    attempt: int
```

---

## Máquina de Estados

```
INPUT (dirección del usuario)
    │
    ▼
NORMALIZING
  normalize_colombian_address() + _strip_preamble()
    │
    ▼
CACHE_LOOKUP
  memory cache → DB cache
    │
  ┌─┴──────────────┐
HIT               MISS
  │                  │
  ▼                  ▼
RESOLVED          RESOLVING
(coordenadas     Google → Nominatim
 del cache)            │
                ┌──────┼──────────────┐
                │      │              │
            ROOFTOP  GEO_CTR      ZERO_RES
            RANGE_I  APPROX       NOM_LOW
                │      │              │
                │      └──────┬───────┘
                │             ▼
                │         CHECK_ALIASES ← interno, sin UX
                │         (Fase 1: siempre → CONTEXT_GATHERING)
                │         (Fase 2: si aliases → NEEDS_DISAMBIGUATION)
                │             │
                │     ┌───────┴──────────┐
                │     │                  │
                │  aliases            sin aliases
                │  activos             │
                │     │                  │
                │  NEEDS_DISAMBIG   CONTEXT_GATHERING
                │  "¿Es Santa       "¿En qué barrio
                │   Teresa o         o referencia?"
                │   Berlín?"         │
                │     │              │
                │  usuario        usuario
                │  elige alias    da texto
                │     │              │
                │  query_enriched = query_base + ", " + respuesta
                │     │              │
                └─────┴──────────────┘
                       │
                   RESOLVING (attempt + 1, max 3)
                       │
                  ┌────┴────┐
                RESUELVE   FALLA
                  │          │
             CONFIRMING   attempt >= 3?
             (WhatsApp:      │
              "¿Es en X?")  FAILED
                  │           │
               usuario    OPERATOR_ESCALATION
               confirma
                  │
               RESOLVED
               + write cache
               + write alias (Fase 2)
```

---

## Reglas de Auto-Aceptación

```
AUTO-ACEPTAR (sin preguntar al usuario):
  source == "google"
  AND location_type IN (ROOFTOP, RANGE_INTERPOLATED)
  AND lat/lng dentro de POPAYAN_URBAN_BBOX
    min_lat: 2.38, max_lat: 2.52
    min_lng: -76.72, max_lng: -76.54

NUNCA AUTO-ACEPTAR:
  GEOMETRIC_CENTER — centroide de zona, error típico 100-500m
  APPROXIMATE      — estimación gruesa, inaceptable para taxi
  NOMINATIM_LOW    — baja confianza

CONFIRMAR ANTES DE ACEPTAR (WhatsApp):
  Todo resultado auto-aceptado pasa por CONFIRMING
  "¿El taxi irá a [dirección]. ¿Correcto?"
```

---

## Construcción de enriched_query

```python
enriched_query = query_base + ", " + alias_text
```

**Regla por alias_type:**

| Tipo | Incluir en enriched_query |
|------|--------------------------|
| GOOGLE_INFERRED | Siempre |
| NEIGHBORHOOD | Siempre |
| LANDMARK | **NO** — Google puede resolverlo como el landmark, no la dirección |
| SECTOR | NO |
| FREE_TEXT | NO |

LANDMARK se usa solo para construir la pregunta de desambiguación al usuario,
no para enviar a Google.

---

## Umbrales Popayán

```python
POPAYAN_BBOX_WIDE = {
    "min_lat": 2.32, "max_lat": 2.58,
    "min_lng": -76.82, "max_lng": -76.42,
}

POPAYAN_URBAN_BBOX = {
    "min_lat": 2.38, "max_lat": 2.52,
    "min_lng": -76.72, "max_lng": -76.54,
}

MAX_ATTEMPTS = 3
```

Verificación: resultado debe estar en `POPAYAN_URBAN_BBOX` para auto-aceptar.
Si está en WIDE pero no en URBAN → sospechoso → CONTEXT_GATHERING.

---

## Verificación Post-Resolución

Antes de aceptar un resultado como válido, verificar que el `formatted_address`
retornado por Google contenga los componentes numéricos del `query_base`.

Ejemplo:
```
query_base:       "Cl. 16 # 3CE-41"
formatted_address: "Calle 16 #3ce-41, Popayán, Cauca, Colombia"
→ contiene "16" y "41" → válido ✓

query_base:       "Cl. 16 # 3CE-41"
formatted_address: "D1, Carrera 8 #18-24, Popayán, Cauca, Colombia"
→ no contiene "3CE" ni "41" → sospechoso → no guardar alias ✗
```

Esto previene el escenario donde Google ignora la dirección y resuelve el alias
como punto de referencia independiente.
