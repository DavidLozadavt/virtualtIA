-- 005_llm_usage.sql
-- Uso del modelo externo por turno de chat: llamadas, tokens reportados por el
-- proveedor, costo estimado y herramientas ejecutadas. Sin texto de mensajes.

CREATE TABLE IF NOT EXISTS lyra_llm_usage (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_slug      VARCHAR(50)  NOT NULL,
    conversation_id   VARCHAR(36)  NOT NULL,
    path              VARCHAR(20)  NOT NULL,
    llm_calls         SMALLINT     NOT NULL DEFAULT 0,
    prompt_tokens     INT          NOT NULL DEFAULT 0,
    cached_tokens     INT          NOT NULL DEFAULT 0,
    completion_tokens INT          NOT NULL DEFAULT 0,
    cost_usd          DECIMAL(12, 6) NOT NULL DEFAULT 0,
    models            VARCHAR(120) NULL,
    tools             VARCHAR(255) NULL,
    errors            VARCHAR(255) NULL,
    latency_ms        INT          NOT NULL DEFAULT 0,
    created_at        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_created (created_at),
    INDEX idx_project_created (project_slug, created_at),
    INDEX idx_conversation (conversation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
