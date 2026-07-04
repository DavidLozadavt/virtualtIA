"""
Almacén de sesiones de llamada por call_uuid.

Soporta memoria (desarrollo) y Redis (producción / concurrencia).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from core.config import settings

logger = logging.getLogger("lyra.telephony.sessions")

# ── Estados (alineados con el motor Twilio legacy) ───────────────────────────

STATE_WAITING_ORIGIN = "waiting_origin"
STATE_WAITING_GEO_CONTEXT = "waiting_geo_context"
# Confirmación de un match DUDOSO (Decision.CONFIRM) antes de fijar el origen:
# "¿Te refieres a X?" → sí/no. Distinto de CONFIRMING_ORIGIN, que confirma un
# origen ya fijado ("¿Te recogemos ahí?").
STATE_CONFIRMING_MATCH = "confirming_match"
STATE_CONFIRMING_ORIGIN = "confirming_origin"
STATE_WAITING_DEST_OR_SKIP = "waiting_dest_or_skip"
STATE_CONFIRMING_DEST = "confirming_dest"
STATE_CREATING_SERVICE = "creating_service"
STATE_SERVICE_CREATED = "service_created"
STATE_FINISHED = "finished"


@dataclass
class GeoSessionSnapshot:
    """Snapshot serializable del estado geo de una sesión."""

    original_query: Optional[str] = None
    attempt: int = 0


@dataclass
class CallSession:
    """Estado de una llamada activa — identificada por call_uuid (FreeSWITCH)."""

    call_uuid: str
    caller_phone: Optional[str] = None
    destination_number: Optional[str] = None
    state: str = STATE_WAITING_ORIGIN
    origen_text: Optional[str] = None
    origen_barrio: Optional[str] = None
    destino_text: Optional[str] = None
    service_created: bool = False
    silence_count: int = 0
    retry_count: int = 0
    last_message: str = ""
    updated_at: float = field(default_factory=time.time)
    sip_metadata: Dict[str, Any] = field(default_factory=dict)
    pending_disambiguation: Optional[dict] = None
    # Candidato de un match dudoso pendiente de confirmar ("¿Te refieres a X?").
    # {"canonical": str}. Se consume en STATE_CONFIRMING_MATCH.
    pending_match_confirmation: Optional[dict] = None
    geo_original_query: Optional[str] = None
    geo_attempt: int = 0

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CallSession":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


class _MemoryBackend:
    def __init__(self, ttl_sec: int):
        self._sessions: Dict[str, CallSession] = {}
        self._ttl = ttl_sec

    def get(self, call_uuid: str) -> Optional[CallSession]:
        self._prune()
        return self._sessions.get(call_uuid)

    def set(self, session: CallSession) -> None:
        self._prune()
        session.touch()
        self._sessions[session.call_uuid] = session

    def delete(self, call_uuid: str) -> None:
        self._sessions.pop(call_uuid, None)

    def count(self) -> int:
        self._prune()
        return len(self._sessions)

    def _prune(self) -> None:
        now = time.time()
        dead = [
            k for k, s in self._sessions.items() if now - s.updated_at > self._ttl
        ]
        for k in dead:
            self._sessions.pop(k, None)


class _RedisBackend:
    def __init__(self, redis_url: str, ttl_sec: int):
        import redis  # type: ignore

        self._client = redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_sec
        self._prefix = "lyra:call:"

    def _key(self, call_uuid: str) -> str:
        return f"{self._prefix}{call_uuid}"

    def get(self, call_uuid: str) -> Optional[CallSession]:
        raw = self._client.get(self._key(call_uuid))
        if not raw:
            return None
        try:
            return CallSession.from_dict(json.loads(raw))
        except Exception as e:
            logger.warning("[redis] corrupt session %s: %s", call_uuid, e)
            return None

    def set(self, session: CallSession) -> None:
        session.touch()
        self._client.setex(
            self._key(session.call_uuid),
            self._ttl,
            json.dumps(session.to_dict(), ensure_ascii=False),
        )

    def delete(self, call_uuid: str) -> None:
        self._client.delete(self._key(call_uuid))

    def count(self) -> int:
        return len(self._client.keys(f"{self._prefix}*"))


class SessionStore:
    """Fachada de sesiones — memoria o Redis según VOICE_SESSION_STORE."""

    def __init__(self):
        store_type = (settings.VOICE_SESSION_STORE or "memory").lower()
        ttl = settings.CALL_SESSION_TTL_SEC

        if store_type == "redis" and settings.REDIS_URL:
            try:
                self._backend = _RedisBackend(settings.REDIS_URL, ttl)
                logger.info("[sessions] Redis backend active ttl=%ss", ttl)
            except Exception as e:
                logger.warning(
                    "[sessions] Redis unavailable (%s), falling back to memory", e
                )
                self._backend = _MemoryBackend(ttl)
        else:
            self._backend = _MemoryBackend(ttl)
            if store_type == "redis":
                logger.warning(
                    "[sessions] VOICE_SESSION_STORE=redis but REDIS_URL empty — using memory"
                )

    def get_or_create(
        self,
        call_uuid: str,
        caller_phone: Optional[str] = None,
        destination_number: Optional[str] = None,
        sip_metadata: Optional[dict] = None,
    ) -> CallSession:
        existing = self._backend.get(call_uuid)
        if existing:
            existing.touch()
            if caller_phone and not existing.caller_phone:
                existing.caller_phone = caller_phone
            return existing

        session = CallSession(
            call_uuid=call_uuid,
            caller_phone=caller_phone,
            destination_number=destination_number,
            sip_metadata=sip_metadata or {},
        )
        self._backend.set(session)
        logger.info(
            "[sessions] created call_uuid=%s caller=%s",
            call_uuid,
            caller_phone,
        )
        return session

    def get(self, call_uuid: str) -> Optional[CallSession]:
        return self._backend.get(call_uuid)

    def save(self, session: CallSession) -> None:
        self._backend.set(session)

    def delete(self, call_uuid: str) -> None:
        self._backend.delete(call_uuid)
        logger.info("[sessions] deleted call_uuid=%s", call_uuid)

    def active_count(self) -> int:
        return self._backend.count()


# Singleton lazy
_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
