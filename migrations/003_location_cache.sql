-- 003_location_cache.sql
-- Tabla de cache de geocodificación para Popayán
-- Actualización 2026-06-01: añadidos confidence y location_type para pipeline nuevo

CREATE TABLE IF NOT EXISTS location_cache (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    canonical_query VARCHAR(500) NOT NULL,
    query_hash     CHAR(64)     GENERATED ALWAYS AS (SHA2(canonical_query, 256)) STORED,
    lat            DECIMAL(10, 7) NOT NULL,
    lng            DECIMAL(10, 7) NOT NULL,
    display_name   VARCHAR(500),
    neighborhood   VARCHAR(150),
    source         ENUM('google', 'nominatim', 'manual') DEFAULT 'google',
    location_type  ENUM(
                     'ROOFTOP',
                     'RANGE_INTERPOLATED',
                     'GEOMETRIC_CENTER',
                     'APPROXIMATE',
                     'NOMINATIM_HIGH',
                     'NOMINATIM_LOW',
                     'MANUAL'
                   ) DEFAULT 'ROOFTOP',
    confidence     DECIMAL(4, 3) DEFAULT 0.800,
    is_valid       TINYINT(1) DEFAULT 1,
    query_count    INT DEFAULT 1,
    last_used_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_hash   (query_hash),
    INDEX idx_query      (canonical_query(200)),
    INDEX idx_valid      (is_valid),
    INDEX idx_source     (source)
);

-- Si la tabla ya existe, añadir columnas nuevas sin romper datos existentes:
-- ALTER TABLE location_cache
--   CHANGE COLUMN canonical_name canonical_query VARCHAR(500) NOT NULL,
--   ADD COLUMN query_hash CHAR(64) GENERATED ALWAYS AS (SHA2(canonical_query, 256)) STORED AFTER canonical_query,
--   ADD COLUMN neighborhood VARCHAR(150) AFTER display_name,
--   ADD COLUMN location_type ENUM('ROOFTOP','RANGE_INTERPOLATED','GEOMETRIC_CENTER','APPROXIMATE','NOMINATIM_HIGH','NOMINATIM_LOW','MANUAL') DEFAULT 'ROOFTOP' AFTER source,
--   ADD COLUMN confidence DECIMAL(4,3) DEFAULT 0.800 AFTER location_type,
--   ADD UNIQUE KEY uq_hash (query_hash);

-- Migración incremental para bases ya creadas con 003 sin neighborhood:
-- ALTER TABLE location_cache ADD COLUMN neighborhood VARCHAR(150) AFTER display_name;
