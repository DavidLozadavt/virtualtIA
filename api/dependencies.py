from fastapi import Depends, Request
from core.database import get_connection
from core.config import settings, Settings
from services.chat_service import ChatService
from services.whatsapp_service import WhatsappService
def get_db():
    try:
        with get_connection() as conn:
            yield conn
    except Exception:
        yield None

def get_settings() -> Settings:
    return settings

def get_llm(request: Request):
    return request.app.state.llm_engine

def get_tool_registries(request: Request):
    return getattr(request.app.state, "tool_registries", {})

async def get_chat_service(
    db=Depends(get_db),
    config: Settings = Depends(get_settings),
    llm=Depends(get_llm),
    registries=Depends(get_tool_registries)
) -> ChatService:
    return ChatService(db, config, llm, registries)

async def get_whatsapp_service(
    db=Depends(get_db),
    config: Settings = Depends(get_settings)
) -> WhatsappService:
    return WhatsappService(db, config)
