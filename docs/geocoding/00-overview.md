# Geocodificación — Overview del Refactor

**Rama:** `refactor/geocoding-cache-google-maps`
**Inicio:** 2026-06-01
**Estado:** Fase 1 en implementación

---

## Por qué se hizo este refactor

El sistema anterior dependía de `tools/popayan_geodata.py`, un catálogo manual de ~800 barrios,
aliases y coordenadas hardcodeadas. Esto generaba:

- Mantenimiento constante cuando aparecían nuevos barrios o cambios de nombre
- Errores cuando el catálogo no tenía la referencia exacta del usuario
- Acoplamiento rígido entre la capa de geocodificación y el conocimiento local
- Imposibilidad de aprender automáticamente de los usuarios

## Qué se construyó en cambio

Un pipeline de geocodificación en capas:

```
Dirección del usuario
    │
    ▼
[1] CACHE LOCAL (memoria RAM + MySQL)
    │
    ▼ miss
[2] GOOGLE GEOCODING API
    │
    ▼ falla o resultado débil
[3] NOMINATIM (fallback)
    │
    ▼ resultado débil o sin resultado
[4] CONTEXT_GATHERING — preguntar al usuario barrio/referencia
    │
    ▼ usuario responde
[5] Reintentar pipeline con query enriquecida (max 3 intentos)
    │
    ▼ sigue fallando
[6] FAILED → escalar a operador
```

## Hipótesis central (validada en revisión arquitectónica)

Google NO devuelve múltiples candidatos útiles diferenciados por barrio para
direcciones colombianas completas en Popayán. La nomenclatura colombiana
(`Cl. 16 # 3CE-41`) es matemáticamente única dentro de una ciudad.

El flujo principal es **B** (solicitar contexto cuando el resultado es débil),
no **A** (elegir entre múltiples candidatos de Google).

## Decisión sobre popayan_geodata.py

`tools/popayan_geodata.py` **se conserva en el repositorio pero se elimina del flujo
de geocodificación**. El archivo puede usarse para:

- Correcciones STT (nombres mal transcritos de barrios conocidos)
- Referencia histórica
- Futura Fase 2 si se decide poblar el alias table manualmente

Lo que **no debe hacer** nunca más:
- Ser fuente de coordenadas
- Ser consultado antes de Google/Nominatim
- Inventar ubicaciones para direcciones que no tiene

---

## Estructura de archivos del sistema nuevo

```
core/
  geo_types.py          ← dataclasses y enums (nuevo)
  geocoder_service.py   ← pipeline completo (reescrito)
  address_utils.py      ← limpiado, sin dependencias de popayan_geodata

migrations/
  003_location_cache.sql  ← tabla de cache técnico (actualizada)
  004_geo_aliases.sql     ← tabla de aliases humanos (Fase 2, no ejecutar aún)

docs/geocoding/
  00-overview.md          ← este archivo
  01-architecture.md      ← diseño técnico completo
  02-phase1-plan.md       ← tareas de Fase 1 y estado
  03-phase2-deferred.md   ← diseño de Fase 2 (geo_human_aliases)
  04-files-changed.md     ← qué se cambió y por qué
```
