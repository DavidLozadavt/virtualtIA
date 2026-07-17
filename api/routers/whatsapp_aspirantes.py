"""
api/routers/whatsapp_aspirantes.py — Universal inbound webhook for the Sena Aspirantes WhatsApp campaign processor.
"""

import logging
from collections import OrderedDict
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from config.aspirantes_config import aspirantes_settings
from services.aspirantes.aspirantes_handler import AspirantesHandler

logger = logging.getLogger("lyra.whatsapp_aspirantes")
whatsapp_aspirantes_router = APIRouter(prefix="/wh/whatsapp_aspirantes", tags=["whatsapp_aspirantes"])

class MessageCache:
    def __init__(self, capacity: int = 1000):
        self.cache = OrderedDict()
        self.capacity = capacity

    def is_processed(self, msg_id: str) -> bool:
        if not msg_id:
            return False
        if msg_id in self.cache:
            self.cache.move_to_end(msg_id)
            return True
        self.cache[msg_id] = True
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
        return False

PROCESSED_MESSAGES = MessageCache()
aspirantes_handler = AspirantesHandler()

# ✅ RECIBIR MENSAJES (arquitectura autónoma; sin Telecom Manager)
@whatsapp_aspirantes_router.post("/universal")
async def receive_universal_message(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    company_id = int(body.get("company_id", 0))
    sender_phone = body.get("from")
    message_content = body.get("body", "")
    message_id = body.get("message_id")
    button_id = body.get("button_id")

    # 1. Filtro por company_id OPCIONAL: solo se aplica si está configurado (> 0).
    #    Por defecto (arquitectura autónoma) no se exige ningún company_id.
    expected_company = aspirantes_settings.ASPIRANTES_COMPANY_ID
    if expected_company > 0 and company_id != expected_company:
        logger.warning(f"Ignored webhook with company_id={company_id}. Expected {expected_company}")
        return {"status": "ignored_wrong_company"}

    # 2. Ignorar duplicados
    if message_id and PROCESSED_MESSAGES.is_processed(message_id):
        logger.info(f"[Aspirantes] Mensaje universal duplicado ignorado: {message_id}")
        return {"status": "ignored_duplicate"}

    # 3. Validar contenido básico
    if not sender_phone or (not message_content and not button_id):
        return {"status": "ignored_empty"}

    logger.info(f"[Aspirantes] Mensaje universal recibido de {sender_phone}: Content='{message_content}', ButtonID='{button_id}'")
    
    # 4. Procesar en segundo plano
    background_tasks.add_task(
        aspirantes_handler.process_message,
        sender_phone=sender_phone,
        message_content=message_content,
        message_id=message_id,
        company_id=company_id,
        button_id=button_id
    )

    return {"status": "success"}
