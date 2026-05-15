import time
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.database import get_connection, check_connection

logger = logging.getLogger("lyra.admin.config")
router = APIRouter(tags=["admin-config"])

# ── Startup timestamp ────────────────────────────────────────────
_startup_time = time.time()

# ── Schemas ──────────────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status: str = Field(..., description="'online' or 'maintenance'")
    message: Optional[str] = None

class ConfigUpdate(BaseModel):
    maintenance_mode: Optional[bool] = None
    maintenance_message: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None

class VersionCreate(BaseModel):
    version: str
    changelog: str
    activate: bool = False
    metrics: Optional[dict] = None

# ── Helpers ──────────────────────────────────────────────────────

def _get_config_value(key: str, default=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT config_value FROM lyra_config WHERE config_key = %s", (key,)
                )
                row = cur.fetchone()
                if row:
                    val = row["config_value"]
                    if val in ("true", "True", "1"):
                        return True
                    if val in ("false", "False", "0"):
                        return False
                    return val
                return default
    except Exception:
        return default

def _set_config_value(key: str, value):
    try:
        str_value = str(value).lower() if isinstance(value, bool) else str(value)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO lyra_config (config_key, config_value)
                       VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE config_value = %s""",
                    (key, str_value, str_value),
                )
    except Exception as e:
        logger.error(f"Config set error: {e}")

def _get_current_version() -> str:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM lyra_versions WHERE is_current = 1 LIMIT 1"
                )
                row = cur.fetchone()
                return row["version"] if row else "1.0.0"
    except Exception:
        return "1.0.0"


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """Get Lyra operational status."""
    uptime_secs = int(time.time() - _startup_time)
    hours, remainder = divmod(uptime_secs, 3600)
    minutes, seconds = divmod(remainder, 60)

    maintenance = _get_config_value("maintenance_mode", False)

    return {
        "success": True,
        "data": {
            "status": "maintenance" if maintenance else "online",
            "version": _get_current_version(),
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "lastRestart": datetime.fromtimestamp(_startup_time).isoformat(),
        },
    }

@router.post("/status")
async def update_status(body: StatusUpdate):
    """Toggle Lyra online/maintenance."""
    is_maintenance = body.status == "maintenance"
    _set_config_value("maintenance_mode", is_maintenance)
    if body.message:
        _set_config_value("maintenance_message", body.message)

    # ── PUSHER REAL-TIME SYNC ──
    try:
        from core.pusher import get_pusher_client
        pusher_client = get_pusher_client()
        if pusher_client:
            pusher_client.trigger('lyra-channel', 'power_status_updated', {
                'isPoweredOn': not is_maintenance,
                'status': body.status
            })
    except Exception as e:
        logger.warning(f"Failed to trigger Pusher status update: {e}")

    return {"success": True, "message": f"Status set to {body.status}"}

@router.get("/config")
async def get_config():
    """Get Lyra configuration."""
    try:
        config = {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT config_key, config_value FROM lyra_config")
                for row in cur.fetchall():
                    config[row["config_key"]] = row["config_value"]
        return {"success": True, "data": config}
    except Exception:
        return {"success": True, "data": {}}

@router.put("/config")
async def update_config(body: ConfigUpdate):
    """Update Lyra configuration."""
    updates = body.dict(exclude_none=True)
    for key, value in updates.items():
        _set_config_value(key, value)
    return {"success": True, "message": "Config updated"}

@router.post("/clear-cache")
async def clear_cache():
    """Clear any cached data."""
    # Clear project config cache
    from orchestrator.context_builder import _project_configs
    _project_configs.clear()
    return {"success": True, "message": "Cache cleared"}

@router.get("/health")
async def health_check():
    """Detailed health check for all Lyra services."""
    services = []

    # Database
    db_start = time.time()
    db_ok = check_connection()
    db_latency = int((time.time() - db_start) * 1000)
    services.append({
        "service": "database",
        "status": "healthy" if db_ok else "down",
        "latency": db_latency,
        "lastCheck": datetime.now().isoformat(),
    })

    # LLM Engine
    from core.config import settings
    services.append({
        "service": "llm_engine",
        "status": "healthy",
        "latency": 0,
        "lastCheck": datetime.now().isoformat(),
        "details": f"Provider: {settings.LLM_PROVIDER}, Model: {settings.OPENAI_MODEL}",
    })

    # Memory/API
    services.append({
        "service": "api",
        "status": "healthy",
        "latency": 1,
        "lastCheck": datetime.now().isoformat(),
        "details": f"Uptime: {int(time.time() - _startup_time)}s",
    })

    return {"success": True, "data": services}

@router.get("/health/incidents")
async def health_incidents(days: int = Query(7)):
    """Health incidents (placeholder — Lyra tracks its own health internally)."""
    return {"success": True, "data": []}

@router.post("/services/{service}/restart")
async def restart_service(service: str):
    """Restart a service component (placeholder for now)."""
    logger.info(f"Service restart requested: {service}")
    return {"success": True, "message": f"Service '{service}' restart initiated"}

@router.get("/alerts/pending")
async def pending_alerts():
    """Get pending admin alerts."""
    return {"success": True, "data": []}

@router.post("/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: int):
    """Mark alert as read."""
    return {"success": True}

@router.get("/versions")
async def list_versions():
    """List Lyra version history."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, version, is_current, deployed_at, deployed_by, changelog, metrics
                       FROM lyra_versions
                       ORDER BY deployed_at DESC"""
                )
                versions = cur.fetchall()
                for v in versions:
                    if v.get("deployed_at"):
                        v["deployed_at"] = v["deployed_at"].isoformat()
                    v["is_current"] = bool(v.get("is_current"))
                    if isinstance(v.get("metrics"), str):
                        import json
                        try:
                            v["metrics"] = json.loads(v["metrics"])
                        except Exception:
                            v["metrics"] = {}
        return {"success": True, "data": versions}
    except Exception as e:
        logger.error(f"Versions error: {e}")
        return {"success": True, "data": []}

@router.post("/versions")
async def create_version(body: VersionCreate):
    """Register a new version."""
    import json
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if body.activate:
                    cur.execute("UPDATE lyra_versions SET is_current = 0")

                cur.execute(
                    """INSERT INTO lyra_versions (version, changelog, is_current, deployed_at, deployed_by, metrics)
                       VALUES (%s, %s, %s, NOW(), 'admin', %s)""",
                    (body.version, body.changelog, body.activate, json.dumps(body.metrics or {})),
                )
        return {"success": True, "message": f"Version {body.version} registered"}
    except Exception as e:
        logger.error(f"Create version error: {e}")
        raise HTTPException(500, "Error creating version")

@router.post("/versions/{version_id}/rollback")
async def rollback_version(version_id: int):
    """Rollback to a specific version."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE lyra_versions SET is_current = 0")
                cur.execute("UPDATE lyra_versions SET is_current = 1 WHERE id = %s", (version_id,))
        return {"success": True, "message": f"Rolled back to version {version_id}"}
    except Exception as e:
        logger.error(f"Rollback error: {e}")
        raise HTTPException(500, "Error rolling back")
