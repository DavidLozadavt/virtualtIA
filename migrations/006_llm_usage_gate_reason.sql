-- 006_llm_usage_gate_reason.sql
-- Motivo con que la compuerta de escalamiento decidió el turno (orchestrator/escalation.py):
--   llm_calls > 0  -> motivo por el que se fue al modelo
--   llm_calls = 0  -> motivo por el que se resolvió local ("noise", "too_short"),
--                     o "+motivo" si habría escalado pero el modelo está apagado.
-- NULL = el turno lo resolvió un interceptor sin pasar por la compuerta.
-- Columna opcional: el código funciona sin ella (core/llm_usage.end_turn).

ALTER TABLE lyra_llm_usage ADD COLUMN gate_reason VARCHAR(40) NULL AFTER path;
