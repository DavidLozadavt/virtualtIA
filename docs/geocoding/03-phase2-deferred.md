# Geocodificación — Fase 2: geo_human_aliases (DIFERIDA)

**Estado:** Diseñada, NO implementar hasta criterios de Fase 1 cumplidos
**Condición de activación:** 60 días de datos de Fase 1 confirman valor del aprendizaje

---

## Por qué se difiere

Fase 2 es un sistema de aprendizaje que requiere datos para ser útil.
Sin datos reales, es complejidad sin valor. Los 60 días de Fase 1 proveen:

1. Evidencia de qué porcentaje de addresses son repetidas con ambigüedad
2. Distribución real de alias_types que los usuarios proveen
3. Tasa de éxito real de CONTEXT_GATHERING como baseline

Si los criterios de Fase 1 no se cumplen, Fase 2 no se implementa.

---

## Diseño de la tabla

```sql
CREATE TABLE geo_human_aliases (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    query_base       VARCHAR(500)  NOT NULL,
    query_base_hash  CHAR(64)      GENERATED ALWAYS AS (SHA2(query_base, 256)) STORED,
    alias_text       VARCHAR(255)  NOT NULL,
    alias_normalized VARCHAR(255)  NOT NULL,
    alias_type       ENUM(
                       'GOOGLE_INFERRED',
                       'NEIGHBORHOOD',
                       'LANDMARK'
                     ) NOT NULL,
    selection_count  INT DEFAULT 0,
    success_count    INT DEFAULT 0,
    failure_count    INT DEFAULT 0,
    is_active        TINYINT(1) DEFAULT 1,
    first_seen_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_base_alias (query_base_hash, alias_normalized(200)),
    INDEX idx_base_hash (query_base_hash),
    INDEX idx_active (is_active)
);
```

---

## alias_type: 3 tipos operacionales

| Tipo | Origen | Uso en query enrichment | Ejemplo |
|------|--------|------------------------|---------|
| `GOOGLE_INFERRED` | `sublocality_level_1` de Google | Siempre | "Santa Teresa" |
| `NEIGHBORHOOD` | Usuario informa barrio | Siempre | "Berlín", "Belalcázar" |
| `LANDMARK` | Usuario da referencia cercana | **NO** — solo para UI | "D1", "polideportivo" |

**SECTOR y FREE_TEXT eliminados del flujo operacional.**
Si se quieren registrar para análisis, van a tabla de logs separada.

---

## Reglas de aprendizaje

### Crear alias (primera vez)

```
Condición: pipeline resolvió ROOFTOP o RANGE_INTERPOLATED
           Y el usuario había provisto texto adicional en esta sesión
           Y enriched_query.formatted_address contiene números del query_base

alias_text = texto del usuario, limpiado de preposiciones
alias_type = detectado por reglas (ver abajo)
success_count = 1
failure_count = 0
last_confirmed_at = now()
```

### No crear alias

```
- Pipeline falló con ese texto como enrichment
- texto < 3 chars o > 100 chars
- texto es "no sé", "aquí", vacío
- LANDMARK Y Google falló
- formatted_address no contiene números del query_base
  (Google resolvió el landmark, no la dirección)
```

### Incrementar success_count

```
Trigger: pipeline con enriched_query resolvió ROOFTOP o RANGE_INTERPOLATED
         Y verificación post-resolución pasó
```

### Incrementar failure_count

```
Trigger: pipeline con enriched_query devolvió ZERO_RESULTS,
         APPROXIMATE, o resultado fuera de urban_bbox
```

### Degradar alias (is_active = 0)

```
Condición A: failure_count / (success + failure) > 0.6 Y total >= 5
Condición B: días desde last_confirmed_at > umbral por tipo:
  GOOGLE_INFERRED: 730 días
  NEIGHBORHOOD:    365 días
  LANDMARK:         90 días
```

---

## Ranking de aliases (para NEEDS_DISAMBIGUATION)

```
alias_score = 0.50 × success_rate
            + 0.35 × recency_score
            + 0.15 × type_weight

recency_score = exp(−ln(2) × días_desde_confirmación / half_life)

half_life:
  GOOGLE_INFERRED: 365 días
  NEIGHBORHOOD:    180 días
  LANDMARK:         60 días

type_weight:
  GOOGLE_INFERRED: 1.00
  NEIGHBORHOOD:    0.85
  LANDMARK:        0.55
```

**selection_count NUNCA se muestra al usuario.**
Solo se usa para ordenamiento interno cuando success_rate es igual o cercano.

---

## Riesgos a mitigar en implementación de Fase 2

1. **Envenenamiento por LANDMARK en enriched_query**
   → LANDMARK nunca entra en enriched_query, solo en UI

2. **Alias correcto técnicamente pero incorrecto operacionalmente**
   → Requiere feedback del lado del driver (fuera de scope de geocodificación)
   → Aceptar como limitación documentada

3. **Concurrencia en writes**
   → Usar `INSERT ... ON DUPLICATE KEY UPDATE` con operaciones atómicas

4. **Sesgo de anclaje al mostrar opciones**
   → No mostrar selection_count, no destacar visualmente ninguna opción
   → Ordenar por alias_score, presentar como lista simple
