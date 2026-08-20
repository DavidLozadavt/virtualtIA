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
                """SELECT id, user_id, project_slug, final_data, last_message_at
                   FROM lyra_conversations WHERE id = %s""",
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
                "final_data": {},
                "last_message_at": datetime.now(),
            }


# ─── Vigencia del contexto ───────────────────────────────────────────────

#: Tras este tiempo sin hablar, lo que quedó a medias deja de estar vigente.
#: Un servicio elegido, una hora propuesta o una reserva a la espera de sesión
#: sólo tienen sentido dentro de la conversación que los produjo; recuperarlos
#: días después hace que el asistente conteste a algo que el usuario ya no
#: recuerda haber dicho.
STALE_CONTEXT_SECONDS = 2 * 60 * 60

#: Claves que son la RESPUESTA de un turno, no la memoria de la conversación.
#: Se persistían junto al resto y volvían pegadas al turno siguiente: por eso
#: las fichas de una búsqueda antigua acompañaban a cada mensaje posterior.
TURN_OUTPUT_KEYS = (
    "reply", "properties", "filters_applied", "map_center",
    "voice_action", "voice_action_payload",
    "needs_clarification", "clarification_question", "needs_input",
    "needs_auth", "pending_reservation", "llm_error", "_fallback_reply",
)


def is_stale(last_message_at=None, now=None) -> bool:
    """¿El hilo lleva tanto tiempo parado que ya no es la misma conversación?"""
    if last_message_at is None:
        return False
    try:
        inactivo = ((now or datetime.now()) - last_message_at).total_seconds()
    except TypeError:          # naive vs aware: no vale la pena adivinar
        return False
    if inactivo <= STALE_CONTEXT_SECONDS:
        return False
    logger.info(
        "Hilo reanudado tras %.0f s de silencio (límite %s s): empieza de cero.",
        inactivo, STALE_CONTEXT_SECONDS,
    )
    return True


def carry_over_context(final_data: dict, last_message_at=None, now=None) -> dict:
    """
    Lo que de la conversación anterior sigue valiendo para este turno.

    Devuelve un diccionario NUEVO: el turno empieza sin la respuesta del
    anterior, y sin nada en absoluto si el hilo lleva demasiado tiempo parado.
    """
    if not final_data:
        return {}
    if is_stale(last_message_at, now):
        return {}
    return {k: v for k, v in final_data.items() if k not in TURN_OUTPUT_KEYS}


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
    """
    Persist final_data JSON in the conversation.

    Guardar el estado es un efecto secundario del turno, no el turno. Si algún
    valor no es serializable —los precios llegan de MySQL como `Decimal`— se
    convierte en su representación textual en vez de dejar caer la petición: el
    usuario no debe perder su respuesta porque no se pudo escribir el contexto.
    """
    try:
        payload = json.dumps(final_data, default=str)
    except (TypeError, ValueError) as exc:
        logger.error("No se pudo serializar final_data (%s); se guarda vacío.", exc)
        payload = "{}"

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE lyra_conversations SET final_data = %s WHERE id = %s",
                    (payload, conversation_id),
                )
            conn.commit()
    except Exception as exc:
        logger.error("No se pudo persistir el contexto de la conversación: %s", exc)


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
