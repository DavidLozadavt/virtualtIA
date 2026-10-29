"""
core/llm_usage.py — Medición y control del gasto en el modelo externo.

Cada turno de chat abre un registro (`begin_turn`). Toda llamada al modelo
durante ese turno —redactar la respuesta, elegir una herramienta o resolver una
categoría— se apunta allí con los tokens que reporta el proveedor, el modelo,
la latencia y el resultado. Al cerrar el turno (`end_turn`) queda una línea en
el registro y una fila en `lyra_llm_usage`.

Dos frenos evitan los bucles de gasto:

  * Tope por turno (`LLM_MAX_CALLS_PER_TURN`): un turno no puede llamar al
    modelo más veces que esto, haga lo que haga el orquestador.
  * Presupuesto diario (`LLM_DAILY_BUDGET_USD`): con el gasto estimado del día
    por encima del límite, el modelo deja de usarse y Lyra responde en modo
    local hasta el día siguiente.

No se guarda el texto de los mensajes ni ninguna credencial: sólo cifras.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from core.config import settings

logger = logging.getLogger("lyra.llm.usage")

#: USD por millón de tokens (entrada, entrada en caché, salida). Tarifas estándar
#: verificadas en developers.openai.com/api/docs/pricing el 2026-10-29. Se pueden
#: sustituir sin tocar código con LLM_PRICING_JSON en el .env.
_DEFAULT_PRICING = {
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
}


class LLMBudgetExceeded(RuntimeError):
    """El turno o el día ya gastaron lo permitido; no se llama al modelo."""


def _pricing() -> dict:
    table = dict(_DEFAULT_PRICING)
    raw = (settings.LLM_PRICING_JSON or "").strip()
    if raw:
        try:
            for model, prices in json.loads(raw).items():
                table[model] = tuple(float(p) for p in prices)
        except (ValueError, TypeError) as exc:
            logger.error("LLM_PRICING_JSON inválido, se usan las tarifas por defecto: %s", exc)
    return table


def estimate_cost(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> Optional[float]:
    """Costo en USD según la tarifa del modelo, o None si no hay tarifa conocida."""
    key = (model or "").split("/")[-1]
    table = _pricing()
    # El proveedor devuelve el id con fecha (gpt-4o-mini-2024-07-18): se toma
    # la tarifa del nombre base más largo que coincida.
    prices = table.get(key) or next(
        (table[k] for k in sorted(table, key=len, reverse=True) if key.startswith(k + "-")), None
    )
    if not prices:
        return None
    p_in, p_cached, p_out = prices
    fresh = max(0, prompt_tokens - cached_tokens)
    return (fresh * p_in + cached_tokens * p_cached + completion_tokens * p_out) / 1_000_000


@dataclass
class LLMCall:
    purpose: str
    model: str
    status: str
    latency_ms: int
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    retries: int = 0
    cost_usd: Optional[float] = None


@dataclass
class TurnUsage:
    project_id: str = ""
    conversation_id: str = ""
    max_calls: int = 3
    started: float = field(default_factory=time.time)
    calls: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def cached_tokens(self) -> int:
        return sum(c.cached_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)

    @property
    def cost_usd(self) -> float:
        return round(sum(c.cost_usd or 0.0 for c in self.calls), 6)

    def summary(self) -> dict:
        return {
            "llm_calls": len(self.calls),
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "models": sorted({c.model for c in self.calls}),
            "tools": list(self.tools),
            "errors": [c.status for c in self.calls if c.status != "ok"],
        }


_current: contextvars.ContextVar[Optional[TurnUsage]] = contextvars.ContextVar("lyra_turn_usage", default=None)

# Gasto del día en memoria, refrescado desde la base cada minuto: consultar
# la tabla en cada llamada sería otra consulta por turno sin necesidad.
_day_lock = threading.Lock()
_day_state = {"day": None, "spent": 0.0, "checked_at": 0.0}


def begin_turn(project_id: str, conversation_id: str) -> TurnUsage:
    turn = TurnUsage(
        project_id=project_id,
        conversation_id=conversation_id,
        max_calls=max(1, int(settings.LLM_MAX_CALLS_PER_TURN)),
    )
    _current.set(turn)
    return turn


def current_turn() -> Optional[TurnUsage]:
    return _current.get()


def note_tool(name: str) -> None:
    turn = _current.get()
    if turn is not None:
        turn.tools.append(name)


def _spent_today() -> float:
    today = date.today().isoformat()
    now = time.time()
    with _day_lock:
        if _day_state["day"] == today and now - _day_state["checked_at"] < 60:
            return _day_state["spent"]
    spent = 0.0
    try:
        from core.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM lyra_llm_usage "
                    "WHERE created_at >= CURDATE()"
                )
                row = cur.fetchone() or {}
                spent = float(row.get("s") or 0.0)
    except Exception as exc:
        # Sin tabla o sin base se sigue con lo contado en memoria.
        logger.debug("No se pudo leer el gasto del día: %s", exc)
        with _day_lock:
            spent = _day_state["spent"] if _day_state["day"] == today else 0.0
    with _day_lock:
        _day_state.update(day=today, spent=spent, checked_at=now)
    return spent


def check_budget() -> None:
    """Lanza LLMBudgetExceeded si este turno o el día ya no pueden gastar más."""
    turn = _current.get()
    if turn is not None and len(turn.calls) >= turn.max_calls:
        raise LLMBudgetExceeded(f"tope de {turn.max_calls} llamadas por turno alcanzado")
    budget = float(settings.LLM_DAILY_BUDGET_USD or 0)
    if budget > 0 and _spent_today() >= budget:
        raise LLMBudgetExceeded(f"presupuesto diario de {budget} USD agotado")


def record_call(purpose: str, model: str, status: str, latency_ms: int,
                usage: Optional[dict] = None, retries: int = 0) -> LLMCall:
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    call = LLMCall(
        purpose=purpose, model=model, status=status, latency_ms=latency_ms,
        prompt_tokens=prompt, cached_tokens=cached, completion_tokens=completion,
        retries=retries, cost_usd=estimate_cost(model, prompt, cached, completion),
    )
    turn = _current.get()
    if turn is not None:
        with turn._lock:
            turn.calls.append(call)
    if call.cost_usd:
        with _day_lock:
            if _day_state["day"] == date.today().isoformat():
                _day_state["spent"] += call.cost_usd
    logger.info(
        "LLM_CALL purpose=%s model=%s status=%s latency_ms=%d prompt=%d cached=%d completion=%d retries=%d cost_usd=%s",
        purpose, model, status, latency_ms, prompt, cached, completion, retries,
        f"{call.cost_usd:.6f}" if call.cost_usd is not None else "n/a",
    )
    return call


def end_turn(latency_ms: int, path: str) -> Optional[dict]:
    """Cierra el turno: una línea en el registro y una fila en la base."""
    turn = _current.get()
    if turn is None:
        return None
    _current.set(None)
    summary = {**turn.summary(), "path": path, "latency_ms": latency_ms}
    logger.info("LLM_TURN project=%s %s", turn.project_id, json.dumps(summary, ensure_ascii=False))
    try:
        from core.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO lyra_llm_usage (project_slug, conversation_id, path, llm_calls, "
                    "prompt_tokens, cached_tokens, completion_tokens, cost_usd, models, tools, "
                    "errors, latency_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        turn.project_id[:50], turn.conversation_id[:36], path[:20],
                        summary["llm_calls"], summary["prompt_tokens"], summary["cached_tokens"],
                        summary["completion_tokens"], summary["cost_usd"],
                        ",".join(summary["models"])[:120], ",".join(summary["tools"])[:255],
                        ",".join(summary["errors"])[:255], latency_ms,
                    ),
                )
    except Exception as exc:
        logger.warning("No se pudo guardar el uso del turno: %s", exc)
    return summary
