-- 004_geo_aliases.sql
-- Tabla geo_human_aliases — Fase 2 (DIFERIDA)
--
-- NO ejecutar hasta cumplir criterios de activación de Fase 2.
-- Ver docs/geocoding/03-phase2-deferred.md y docs/geocoding/02-phase1-plan.md
--
-- Criterios de activación (revisar tras 60 días de Fase 1 en producción):
--   1. > 40% de addresses ya están en cache al día 30
--   2. < 10% de requests llegan a FAILED
--   3. Tasa de resolución directa (ROOFTOP/RANGE_INTERPOLATED) > 30%
--
-- Propósito:
--   Almacenar cómo describen los usuarios cada dirección base.
--   "Cl. 16 # 3CE-41" → ["Santa Teresa", "Berlín", "frente al D1"]
--   Permite NEEDS_DISAMBIGUATION basada en historial real de usuarios.
--
-- Lo que NO hace esta tabla:
--   - No es fuente de coordenadas
--   - No reemplaza location_cache
--   - selection_count NUNCA se muestra al usuario

CREATE TABLE IF NOT EXISTS geo_human_aliases (
    id               INT AUTO_INCREMENT PRIMARY KEY,

    -- Dirección base sin contexto adicional
    -- Ejemplo: "Cl. 16 # 3CE-41"
    query_base       VARCHAR(500)  NOT NULL,
    query_base_hash  CHAR(64)      GENERATED ALWAYS AS (SHA2(query_base, 256)) STORED,

    -- Cómo el usuario describió la ubicación (forma limpia para display y enrichment)
    -- Ejemplo: "Santa Teresa", "D1", "polideportivo"
    alias_text       VARCHAR(255)  NOT NULL,
    alias_normalized VARCHAR(255)  NOT NULL,  -- lowercase, sin acentos, para dedup

    -- Tipo de alias (3 tipos operacionales):
    --   GOOGLE_INFERRED: viene de sublocality_level_1 de Google (más confiable)
    --   NEIGHBORHOOD:    barrio informado por usuario, sirve para enrichment de query
    --   LANDMARK:        referencia cercana — solo para UI, NO para enrichment de Google
    alias_type       ENUM('GOOGLE_INFERRED', 'NEIGHBORHOOD', 'LANDMARK') NOT NULL,

    -- Contadores separados (popularidad vs efectividad)
    selection_count  INT           DEFAULT 0,  -- veces que el usuario eligió este alias
    success_count    INT           DEFAULT 0,  -- veces que llevó a ROOFTOP/RANGE_INTERPOLATED
    failure_count    INT           DEFAULT 0,  -- veces que falló con este alias

    -- Estado (0 = degradado/stale, puede rehabilitarse)
    is_active        TINYINT(1)    DEFAULT 1,

    -- Temporalidad para degradación por recencia
    first_seen_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    last_confirmed_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_base_alias  (query_base_hash, alias_normalized(200)),
    INDEX      idx_base_hash  (query_base_hash),
    INDEX      idx_active     (is_active),
    INDEX      idx_type       (alias_type),
    INDEX      idx_confirmed  (last_confirmed_at)
);

-- Índice para ranking de aliases (Fase 2):
-- ORDER BY success_count/(success_count+failure_count) DESC, last_confirmed_at DESC
-- No materializar el score — calcularlo en aplicación con la fórmula de docs/geocoding/03-phase2-deferred.md
