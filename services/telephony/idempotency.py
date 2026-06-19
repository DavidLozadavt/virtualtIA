"""
Idempotencia de creación de servicio por call_uuid (evita duplicados en reintentos).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from core.config import settings

logger = logging.getLogger("lyra.telephony.idempotency")

_PREFIX = "lyra:submitted:"
_TTL = 86400  # 24h


class SubmissionGuard:
    """Marca call_uuid como ya enviado al backend."""

    def __init__(self):
        self._memory: dict[str, float] = {}
        self._redis = None
        if settings.REDIS_URL:
            try:
                import redis

                self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
            except Exception as e:
                logger.warning("[idempotency] Redis unavailable: %s", e)

    def already_submitted(self, call_uuid: Optional[str]) -> bool:
        if not call_uuid or call_uuid.startswith("test-"):
            return False
        if self._redis:
            return bool(self._redis.get(f"{_PREFIX}{call_uuid}"))
        ts = self._memory.get(call_uuid)
        if ts and time.time() - ts < _TTL:
            return True
        return False

    def mark_submitted(self, call_uuid: Optional[str]) -> None:
        if not call_uuid:
            return
        if self._redis:
            self._redis.setex(f"{_PREFIX}{call_uuid}", _TTL, "1")
        else:
            self._memory[call_uuid] = time.time()
        logger.info("[idempotency] marked submitted call_uuid=%s", call_uuid)


_guard: Optional[SubmissionGuard] = None


def get_submission_guard() -> SubmissionGuard:
    global _guard
    if _guard is None:
        _guard = SubmissionGuard()
    return _guard
