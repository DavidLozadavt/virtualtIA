import time
import uuid
import json
import logging
from typing import Optional, Dict, Any, List

from fastapi import HTTPException
from api.schemas import ChatRequest, ChatResponse
from core.config import Settings
from orchestrator.context_builder import build_system_prompt, load_project_config
from orchestrator.intent_router import detect_intent
from orchestrator.tool_runner import run_agent_loop
from orchestrator.memory_manager import (
    get_or_create_user,
    get_or_create_conversation,
    save_message,
    get_conversation_history,
    get_conversation_message_count,
    update_conversation_timestamp,
    update_trust_level,
    update_user_personality,
)
from core.logger import setup_logger

logger = setup_logger("lyra.services.chat")
import re

def _strip_debug_markers(text: str) -> str:
    """Removes technical markers like [BIZ:123], [ANALIZANDO DATOS], etc. from the final output."""
    if not text:
        return text
    # Strip all technical markers in brackets (e.g. [BIZ:123], [ANALIZANDO DATOS], [CONFIRMACIÓN NECESARIA])
    text = re.sub(r"\[[A-ZÁÉÍÓÚÑ\s]{3,}(?::.*?)?\]", "", text, flags=re.IGNORECASE)

    # Clean up multiple newlines or trailing spaces left by stripping
    text = re.sub(r"\n\s*\n", "\n", text)
    return text.strip()

class ChatService:
    def __init__(self, db_conn, config: Settings, llm_engine, registries: Dict[str, Any] = None):
        self.db = db_conn
        self.config = config
        self.llm = llm_engine
        self.registries = registries or {}

    async def process_message(self, payload: ChatRequest, session_id: str = None, auth_header: str = None, app_state: Any = None) -> ChatResponse:
        """Processes a chat message from the user."""
        start_time = time.time()

        # Get or create user
        user = get_or_create_user(payload.project_id, payload.user_id)
        
        # Handle personality override
        active_personality = payload.personality or user.get("active_personality") or "nexo"
        logger.info(f"PERSONALITY CHECK | Payload: {payload.personality} | DB: {user.get('active_personality')} | Active: {active_personality}")
        
        if active_personality and active_personality != user.get("active_personality"):
            logger.info(f"UPDATING PERSONALITY to: {active_personality}")
            update_user_personality(user["id"], active_personality)
            user["active_personality"] = active_personality

        # Load project configuration
        project_config = load_project_config(payload.project_id, active_personality)
        if not project_config:
            raise HTTPException(status_code=404, detail=f"Project '{payload.project_id}' not found")

        # Handle conversation id
        conversation_id = session_id or payload.conversation_id or str(uuid.uuid4())

        # --- AUTO-VOICE INITIALIZATION (0 TOKENS) ---
        if payload.init_voice:
            bot_name = project_config.get("assistant_name", "Lyra")
            greeting = project_config.get("greeting") or f"¡Hola! Soy {bot_name}. ¿Cómo puedo ayudarte hoy?"
            
            get_or_create_conversation(conversation_id, user["id"], payload.project_id)
            save_message(conversation_id, "assistant", greeting)
            
            return ChatResponse(
                reply=greeting,
                conversation_id=conversation_id,
                trust_level=user["trust_level"],
                latency_ms=1
            )

        # Manage trust level
        trust_level = user["trust_level"]

        # Get or create conversation
        conversation = get_or_create_conversation(
            conversation_id=conversation_id,
            user_id=user["id"],
            project_slug=payload.project_id,
        )
        final_data = conversation.get("final_data") or {}

        # Fetch conversation history
        history_limit = 14 if payload.project_id == "nexiservice" else 20
        history = get_conversation_history(conversation_id, limit=history_limit)

        # Save user message
        save_message(conversation_id, "user", payload.message)

        # Trust level progression
        message_count = get_conversation_message_count(conversation_id)
        new_trust_level = min(5, max(1, (message_count // 6) + 1))
        if new_trust_level != trust_level:
            update_trust_level(user["id"], new_trust_level)
            trust_level = new_trust_level

        # Build system prompt + context messages
        user_with_role = {**user, "role": payload.role}
        
        # Context for intent router
        last_assistant_msg = ""
        for m in reversed(history):
            if m.get("role") == "assistant":
                last_assistant_msg = m.get("content", "")
                break
                
        # FIX 3: Unify intent detection to avoid duplicate logs
        local_intent = detect_intent(
            payload.message, 
            payload.project_id, 
            mentioned_city=payload.active_city,
            current_context={"last_assistant_msg": last_assistant_msg}
        )
        
        messages = build_system_prompt(
            project_config=project_config,
            user_trust_level=trust_level,
            user_data=user_with_role,
            conversation_history=history,
            user_message=payload.message,
            active_company_id=payload.active_company_id,
            local_intent=local_intent,
        )

        logger.info(f"PROMPT DEBUG | User: {payload.message} | History count: {len(history)} | Provider: {self.llm.provider}")

        # Run agent loop
        agent_data = await run_agent_loop(
            engine=self.llm,
            messages=messages,
            project_id=payload.project_id,
            project_config=project_config,
            auth_header=auth_header,
            user_data=user_with_role,
            user_lat=payload.lat,
            user_lng=payload.lng,
            active_city=payload.active_city,
            active_company_id=payload.active_company_id,
            conversation_id=conversation_id,
            tool_registry=self.registries.get(payload.project_id),
            local_intent=local_intent,
            final_data=final_data,
        )

        # Update and persist final_data
        # run_agent_loop can return final_data directly or a dict with final_data key
        if "final_data" in agent_data:
            new_final_data = agent_data["final_data"]
        else:
            new_final_data = agent_data

        from orchestrator.memory_manager import update_conversation_final_data
        update_conversation_final_data(conversation_id, new_final_data)
        final_data = new_final_data

        # Persist tool results
        for m in messages:
            is_tool_result = m["role"] == "tool"
            is_assistant_with_tool_calls = m["role"] == "assistant" and bool(m.get("tool_calls"))
            if not (is_tool_result or is_assistant_with_tool_calls):
                continue
            
            candidate_content = m.get("content") or json.dumps(m.get("tool_calls") or {})
            is_new = not any(
                h["role"] == m["role"] and h.get("content") == candidate_content
                for h in history
            )
            if is_new:
                save_message(conversation_id, m["role"], candidate_content)

        reply = agent_data["reply"]
        
        # Save assistant response (with markers for context)
        save_message(conversation_id, "assistant", reply)

        # Update conversation timestamp
        update_conversation_timestamp(conversation_id)

        # Clean reply for the user output
        clean_reply = _strip_debug_markers(reply)

        latency_ms = int((time.time() - start_time) * 1000)

        _fd = agent_data.get("final_data") or {}
        _last_biz = _fd.get("_last_businesses") or []
        _properties = agent_data.get("properties") or _fd.get("properties") or []

        if _last_biz and not _properties:
            _properties = [{"businesses": _last_biz}]

        audio_url = None
        if getattr(payload, "voice", False) and app_state:
            from core.voice_engine import get_voice_engine
            import asyncio
            engine = get_voice_engine()
            audio_id = str(uuid.uuid4())
            active_p = project_config.get("active_personality", "lyra")
            personality_config = project_config.get("personalities", {}).get(active_p, {})
            voice_id = (
                personality_config.get("tts_voice") or 
                personality_config.get("voice") or 
                (project_config.get("voice") if isinstance(project_config.get("voice"), str) else project_config.get("voice", {}).get("tts_voice")) or
                "es-CO-SalomeNeural"
            )
            audio_bytes = await engine.synthesize_to_bytes(clean_reply, voice=voice_id)
            
            if audio_bytes:
                if not hasattr(app_state, "tts_cache"):
                    app_state.tts_cache = {}
                app_state.tts_cache[audio_id] = audio_bytes
                
                async def delete_after_ttl(aid: str):
                    await asyncio.sleep(60)
                    app_state.tts_cache.pop(aid, None)
                
                asyncio.create_task(delete_after_ttl(audio_id))
                audio_url = f"/tts/{audio_id}"

        return ChatResponse(
            reply=clean_reply,
            conversation_id=conversation_id,
            trust_level=trust_level,
            latency_ms=latency_ms,
            properties=_properties,
            filters_applied=agent_data.get("filters_applied") or _fd.get("filters_applied", {}),
            map_center=agent_data.get("map_center") or _fd.get("map_center"),
            voice_action=agent_data.get("voice_action") or _fd.get("voice_action"),
            voice_action_payload=agent_data.get("voice_action_payload") or _fd.get("voice_action_payload"),
            needs_clarification=(agent_data.get("needs_clarification") or _fd.get("needs_clarification", False) or _fd.get("needs_input", False)),
            needs_input=(_fd.get("needs_input", False)),
            clarification_question=agent_data.get("clarification_question") or _fd.get("clarification_question"),
            audio_url=audio_url,
        )


    async def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Returns the conversation history for a given session."""
        return get_conversation_history(session_id)
