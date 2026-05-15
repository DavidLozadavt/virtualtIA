-- ══════════════════════════════════════════════════════════════
-- Lyra Microservice — Initial Schema
-- Database: lyra_db (MySQL / InnoDB / utf8mb4)
-- ══════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS lyra_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE lyra_db;

-- ── Projects ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lyra_projects (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    slug        VARCHAR(50) NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL,
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Users ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lyra_users (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    project_slug      VARCHAR(50) NOT NULL,
    external_user_id  VARCHAR(100) NOT NULL,
    trust_level       TINYINT NOT NULL DEFAULT 1,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_user (project_slug, external_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Conversations ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lyra_conversations (
    id              CHAR(36) PRIMARY KEY,
    user_id         INT NOT NULL,
    project_slug    VARCHAR(50) NOT NULL,
    started_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_message_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES lyra_users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Messages ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lyra_messages (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    conversation_id  CHAR(36) NOT NULL,
    role             ENUM('user', 'assistant', 'tool') NOT NULL,
    content          TEXT NOT NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES lyra_conversations(id) ON DELETE CASCADE,
    INDEX idx_conv_time (conversation_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Seed: Rentus project ──────────────────────────────────────
INSERT IGNORE INTO lyra_projects (slug, name, is_active)
VALUES ('rentus', 'Rentus', 1);
