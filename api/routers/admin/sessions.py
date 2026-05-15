import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime

from core.database import get_connection

logger = logging.getLogger("lyra.admin.sessions")
router = APIRouter(prefix="/sessions", tags=["admin-sessions"])

class SessionUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None

@router.get("")
async def list_sessions(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    """List conversations (sessions) with pagination."""
    try:
        offset = (page - 1) * per_page
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as total FROM lyra_conversations")
                total = cur.fetchone()["total"]

                cur.execute(
                    """SELECT c.id as session_id, c.project_slug, c.started_at as created_at,
                              c.last_message_at as updated_at, u.external_user_id,
                              (SELECT COUNT(*) FROM lyra_messages WHERE conversation_id = c.id) as messages_count
                       FROM lyra_conversations c
                       JOIN lyra_users u ON c.user_id = u.id
                       ORDER BY c.last_message_at DESC
                       LIMIT %s OFFSET %s""",
                    (per_page, offset),
                )
                sessions = []
                for row in cur.fetchall():
                    sessions.append({
                        "session_id": row["session_id"],
                        "user_id": None,
                        "status": "active",
                        "last_intent": "lyra_chat",
                        "city": None,
                        "messages_count": row["messages_count"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                        "messages": [],
                        "metadata": {},
                        "user": {
                            "id": 0,
                            "name": row["external_user_id"],
                            "email": "",
                            "initials": (row["external_user_id"] or "?")[0].upper(),
                        },
                    })

        return {
            "success": True,
            "data": sessions,
            "meta": {
                "total": total,
                "current_page": page,
                "last_page": max(1, (total + per_page - 1) // per_page),
            },
        }
    except Exception as e:
        logger.error(f"Sessions list error: {e}")
        return {"success": True, "data": [], "meta": {"total": 0, "current_page": 1, "last_page": 1}}

@router.get("/stats")
async def session_stats():
    """Session aggregate stats."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as total FROM lyra_conversations")
                total = cur.fetchone()["total"]

                today = datetime.now().replace(hour=0, minute=0, second=0)
                cur.execute(
                    "SELECT COUNT(*) as today FROM lyra_conversations WHERE started_at >= %s",
                    (today,),
                )
                today_count = cur.fetchone()["today"]

        return {
            "success": True,
            "data": {
                "total": total,
                "today": today_count,
                "active": 0,
                "flagged": 0,
                "blocked": 0,
            },
        }
    except Exception as e:
        logger.error(f"Session stats error: {e}")
        return {"success": True, "data": {"total": 0, "today": 0}}

@router.get("/export")
async def export_sessions(format: str = Query("json")):
    """Export sessions (basic JSON export)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT c.id, c.started_at, c.last_message_at, u.external_user_id
                       FROM lyra_conversations c
                       JOIN lyra_users u ON c.user_id = u.id
                       ORDER BY c.last_message_at DESC
                       LIMIT 1000"""
                )
                data = cur.fetchall()
                for row in data:
                    if row.get("started_at"):
                        row["started_at"] = row["started_at"].isoformat()
                    if row.get("last_message_at"):
                        row["last_message_at"] = row["last_message_at"].isoformat()

        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Export error: {e}")
        return {"success": True, "data": []}

@router.get("/{session_id}")
async def session_detail(session_id: str):
    """Get full session with messages."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT c.id as session_id, c.project_slug,
                              c.started_at as created_at, c.last_message_at as updated_at,
                              u.external_user_id
                       FROM lyra_conversations c
                       JOIN lyra_users u ON c.user_id = u.id
                       WHERE c.id = %s""",
                    (session_id,),
                )
                session = cur.fetchone()
                if not session:
                    raise HTTPException(404, "Session not found")

                cur.execute(
                    """SELECT role, content, created_at
                       FROM lyra_messages
                       WHERE conversation_id = %s
                       ORDER BY created_at ASC""",
                    (session_id,),
                )
                messages = [
                    {
                        "id": str(i),
                        "role": m["role"],
                        "content": m["content"],
                        "created_at": m["created_at"].isoformat() if m["created_at"] else None,
                    }
                    for i, m in enumerate(cur.fetchall())
                ]

        return {
            "success": True,
            "data": {
                "session_id": session["session_id"],
                "user_id": None,
                "status": "active",
                "last_intent": "lyra_chat",
                "messages_count": len(messages),
                "created_at": session["created_at"].isoformat() if session["created_at"] else None,
                "updated_at": session["updated_at"].isoformat() if session["updated_at"] else None,
                "messages": messages,
                "metadata": {},
                "user": {
                    "id": 0,
                    "name": session["external_user_id"],
                    "email": "",
                    "initials": (session["external_user_id"] or "?")[0].upper(),
                },
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session detail error: {e}")
        raise HTTPException(500, "Error loading session")

@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and its messages."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM lyra_messages WHERE conversation_id = %s", (session_id,))
                cur.execute("DELETE FROM lyra_conversations WHERE id = %s", (session_id,))
        return {"success": True, "message": "Session deleted"}
    except Exception as e:
        logger.error(f"Delete session error: {e}")
        raise HTTPException(500, "Error deleting session")
