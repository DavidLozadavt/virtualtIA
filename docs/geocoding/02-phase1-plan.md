# Geocodificación — Plan Fase 1

**Estado:** En implementación (2026-06-01)
**Objetivo:** Sistema funcional en producción sin popayan_geodata, sin aliases

---

## Qué incluye Fase 1

Fase 1 es un sistema completo y operable. No requiere geo_human_aliases para funcionar.
Cubre el 100% de los casos: resuelve bien o escala a humano.

### Componentes

| Componente | Archivo | Estado |
|---|---|---|
| Tipos y dataclasses | `core/geo_types.py` | ✅ Implementado |
| Migración location_cache | `migrations/003_location_cache.sql` | ✅ Implementado |
| Pipeline geocodificación | `core/geocoder_service.py` | ✅ Implementado |
| Limpieza address_utils | `core/address_utils.py` | ✅ Implementado |
| Migración aliases (skeleton) | `migrations/004_geo_aliases.sql` | ✅ Creado (no ejecutar) |

### Estados implementados en Fase 1

- ✅ NORMALIZING
- ✅ CACHE_LOOKUP (memoria + DB)
- ✅ RESOLVING (Google multi-result + Nominatim fallback)
- ✅ Auto-accept: ROOFTOP + RANGE_INTERPOLATED únicamente
- ✅ CONTEXT_GATHERING (preguntar barrio/referencia al usuario)
- ✅ CONFIRMING (mostrar dirección resuelta, pedir confirmación)
- ✅ RESOLVED (escribir en cache)
- ✅ FAILED con intento máximo
- ⏳ OPERATOR_ESCALATION (comportamiento en FAILED)

### Estados diferidos a Fase 2

- ⏸️ CHECK_ALIASES (consultar geo_human_aliases)
- ⏸️ NEEDS_DISAMBIGUATION (desde aliases históricos)
- ⏸️ Escritura de geo_human_aliases
- ⏸️ Ranking de aliases

---

## Routers pendientes de migrar al nuevo pipeline

Los siguientes archivos aún usan `_try_local_match`, `geocode_local` o `resolve_canonical`.
Tienen un stub de compatibilidad en `address_utils.py` que retorna `None` para no romper imports.
**Deben actualizarse para usar `geocoder_service.run_pipeline()` y `handle_user_context()`.**

| Archivo | Estado |
|---|---|
| `api/routers/twilio.py` | ✅ Migrado — geocode_local → run_pipeline(), STATE_WAITING_GEO_CONTEXT añadido |
| `api/routers/whatsapp.py` | ✅ Migrado — run_pipeline() en STATE_WAITING_ORIGIN, GPS via Nominatim reverse |
| `services/whatsapp_service.py` | ⏳ Pendiente — usa geocode_local (servicio secundario) |
| `services/twilio/speech_processor.py` | ⏳ Pendiente — usa geocode_local (servicio secundario) |
| `services/twilio/twilio_service.py` | ⏳ Pendiente — usa POPAYAN_PLACES |
| `services/twilio/constants.py` | ⏳ Pendiente — tiene POPAYAN_PLACES propio |
| `tools/intellitaxi.py` | ⏳ Pendiente — usa geocode_local |

**Importante:** `_try_local_match` ya retorna `None` → los callers caen a LLM o pipeline anterior.
Esto puede degradar ligeramente la resolución para nombres de lugares conocidos hasta que
los routers se migren. No afecta la resolución de nomenclaturas de calle.

---

## Lo que NO hace Fase 1

- No consulta `popayan_geodata.py` para coordenadas
- No usa catálogos manuales de barrios
- No mantiene geo_human_aliases (tabla existe pero vacía)
- No muestra popularidad de opciones al usuario

---

## Comportamiento esperado en Fase 1

```
Caso A — Google resuelve ROOFTOP:
  Usuario: "Cl. 16 # 3CE-41"
  Google: ROOFTOP en urban bbox
  Sistema: "¿El taxi irá a Calle 16 #3ce-41, Popayán. ¿Correcto?"
  Usuario: "Sí"
  → RESOLVED, coordenadas en cache

Caso B — Google no resuelve:
  Usuario: "Cl. 16 # 3CE-41"
  Google: ZERO_RESULTS
  Sistema: "¿En qué barrio o referencia cercana queda?"
  Usuario: "Santa Teresa"
  Google: ROOFTOP para "Cl. 16 # 3CE-41, Santa Teresa"
  Sistema: "¿El taxi irá a Calle 16, Santa Teresa, Popayán. ¿Correcto?"
  Usuario: "Sí"
  → RESOLVED, "Cl. 16 # 3CE-41, Santa Teresa" en cache

Caso C — Sigue fallando:
  [3 intentos fallidos]
  Sistema: "No logré ubicar esa dirección. Un operador te contactará."
  → OPERATOR_ESCALATION
```

---

## Criterios de Éxito para pasar a Fase 2

Después de 60 días en producción, revisar:

1. **Tasa de resolución directa** (ROOFTOP/RANGE_INTERPOLATED sin CONTEXT_GATHERING)
   - Meta: > 30% de requests resuelven sin preguntar
   - Si < 20%: Google no tiene buena cobertura para Popayán, revisar hipótesis

2. **Tasa de FAILED**
   - Meta: < 10% llegan a FAILED
   - Si > 20%: revisar qué tipos de queries fallan, posible gap en el pipeline

3. **Frecuencia de addresses repetidas**
   - Si > 40% de addresses ya están en cache en el día 30: Fase 2 tiene valor
   - Si < 15%: Fase 2 tiene menos justificación, evaluar si implementar

4. **Distribución de location_type en cache**
   - Qué porcentaje son ROOFTOP vs GEOMETRIC_CENTER vs ZERO_RESULTS
   - Informa si Google tiene buena cobertura para Popayán

---

## Notas de Implementación

### Google API — Tomar TODOS los resultados, no solo results[0]

```python
for r in data["results"][:6]:  # no solo results[0]
    ...
```

Aunque múltiples candidatos son raros para direcciones completas,
el caso sí puede ocurrir para búsquedas parciales o names de lugares.

### Nominatim — addressdetails=1

Necesario para extraer `address.suburb` y `address.neighbourhood`
que permiten detectar el barrio sin catálogo local.

### Cache key = query normalizada exacta

La normalización debe ser determinística. Dos strings que representan
la misma dirección deben producir el mismo cache key. Usar
`normalize_colombian_address()` antes de calcular el key.
