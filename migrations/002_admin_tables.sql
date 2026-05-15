-- ══════════════════════════════════════════════════════════════
-- Lyra Microservice — Admin Tables Migration
-- Database: lyra_db (MySQL / InnoDB / utf8mb4)
-- ══════════════════════════════════════════════════════════════

USE lyra_db;

-- ── Config (key-value store for runtime configuration) ────────
CREATE TABLE IF NOT EXISTS lyra_config (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    config_key  VARCHAR(100) NOT NULL UNIQUE,
    config_value TEXT,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Versions (deployment history) ─────────────────────────────
CREATE TABLE IF NOT EXISTS lyra_versions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    version     VARCHAR(50) NOT NULL,
    changelog   TEXT,
    is_current  TINYINT(1) NOT NULL DEFAULT 0,
    deployed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deployed_by VARCHAR(100) DEFAULT 'system',
    metrics     JSON,
    INDEX idx_current (is_current)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Seed initial version ──────────────────────────────────────
INSERT IGNORE INTO lyra_versions (version, changelog, is_current, deployed_by)
VALUES ('1.0.0', 'Initial Lyra microservice release — Groq LLM, tool calling, memory management', 1, 'system');

-- ── Seed default config ───────────────────────────────────────
INSERT IGNORE INTO lyra_config (config_key, config_value) VALUES ('maintenance_mode', 'false');
INSERT IGNORE INTO lyra_config (config_key, config_value) VALUES ('maintenance_message', 'Lyra está en mantenimiento. Vuelve pronto.');
