"""
orchestrator/memory_manager.py — CRUD historial en MySQL, compresión de contexto.

Manages users, conversations, and messages in the lyra_db MySQL database.
"""

import uuid
import json
import logging
from datetime import datetime

from core.database import get_connection

logger = logging.getLogger("lyra.memory")


# ─── Users ───────────────────────────────────────────────────────

def get_or_create_user(project_slug: str, external_user_id: str) -> dict:
    """
    Get existing user or create a new one.
    Updates last_seen timestamp on each access.
    Returns dict with id, project_slug, external_user_id, trust_level.
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT id, project_slug, external_user_id, trust_level, active_personality
                   FROM lyra_users
                   WHERE project_slug = %s AND external_user_id = %s""",
                (project_slug, external_user_id),
            )
            user = cursor.fetchone()

            if user:
                cursor.execute(
                    "UPDATE lyra_users SET last_seen = NOW() WHERE id = %s",
                    (user["id"],),
                )
                conn.commit()
                return user

            # Create new user
            cursor.execute(
                """INSERT INTO lyra_users (project_slug, external_user_id, trust_level, created_at, last_seen)
                   VALUES (%s, %s, 1, NOW(), NOW())""",
                (project_slug, external_user_id),
            )
            conn.commit()
            new_id = cursor.lastrowid
            return {
                "id": new_id,
                "project_slug": project_slug,
                "external_user_id": external_user_id,
                "trust_level": 1,
                "active_personality": None,
            }


def update_trust_level(user_id: int, new_level: int) -> None:
    """Update user trust level (1-5)."""
    level = max(1, min(5, new_level))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE lyra_users SET trust_level = %s WHERE id = %s",
                (level, user_id),
            )
        conn.commit()


def update_user_personality(user_id: int, personality: str) -> None:
    """Update active_personality for user."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE lyra_users SET active_personality = %s WHERE id = %s",
                (personality, user_id),
            )
        conn.commit()


# ─── Conversations ───────────────────────────────────────────────

def get_or_create_conversation(
    conversation_id: str,
    user_id: int,
    project_slug: str,
) -> dict:
    """
    Get existing conversation or create a new one.
    Returns dict with id, user_id, project_slug, final_data.
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, user_id, project_slug, final_data FROM lyra_conversations WHERE id = %s",
                (conversation_id,),
            )
            conv = cursor.fetchone()

            if conv:
                raw_fd = conv.get("final_data")
                if raw_fd:
                    if isinstance(raw_fd, str):
                        try:
                            conv["final_data"] = json.loads(raw_fd)
                        except Exception:
                            conv["final_data"] = {}
                    elif isinstance(raw_fd, dict):
                        conv["final_data"] = raw_fd
                    else:
                        conv["final_data"] = {}
                else:
                    conv["final_data"] = {}
                return conv

            # Create new conversation
            cursor.execute(
                """INSERT INTO lyra_conversations (id, user_id, project_slug, started_at, last_message_at, final_data)
                   VALUES (%s, %s, %s, NOW(), NOW(), %s)""",
                (conversation_id, user_id, project_slug, json.dumps({})),
            )
            conn.commit()
            return {
                "id": conversation_id,
                "user_id": user_id,
                "project_slug": project_slug,
                "final_data": {}
            }


def update_conversation_timestamp(conversation_id: str) -> None:
    """Update last_message_at for a conversation."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE lyra_conversations SET last_message_at = NOW() WHERE id = %s",
                (conversation_id,),
            )
        conn.commit()


def update_conversation_final_data(conversation_id: str, final_data: dict) -> None:
    """Persist final_data JSON in the conversation."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE lyra_conversations SET final_data = %s WHERE id = %s",
                (json.dumps(final_data), conversation_id),
            )
        conn.commit()


# ─── Messages ────────────────────────────────────────────────────

def save_message(conversation_id: str, role: str, content: str) -> int:
    """
    Save a message to the conversation history.
    role: 'user', 'assistant', or 'tool'
    Returns the message id.
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO lyra_messages (conversation_id, role, content, created_at)
                   VALUES (%s, %s, %s, NOW())""",
                (conversation_id, role, content),
            )
            conn.commit()
            return cursor.lastrowid


def get_conversation_history(
    conversation_id: str,
    limit: int = 20,
) -> list[dict]:
    """
    Fetch recent messages for a conversation, ordered chronologically.
    Returns list of {role, content} dicts.
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT role, content FROM (
                       SELECT role, content, created_at FROM lyra_messages
                       WHERE conversation_id = %s
                       ORDER BY created_at DESC
                       LIMIT %s
                   ) AS sub
                   ORDER BY created_at ASC""",
                (conversation_id, limit),
            )
            return cursor.fetchall()


def get_conversation_message_count(conversation_id: str) -> int:
    """Count messages in a conversation (for trust level calculation)."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM lyra_messages WHERE conversation_id = %s",
                (conversation_id,),
            )
            row = cursor.fetchone()
            return row["cnt"] if row else 0
