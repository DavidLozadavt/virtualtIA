"""
services/aspirantes/aspirantes_whatsapp_service.py — Message sending service for the Aspirantes
WhatsApp module.

Nueva arquitectura autónoma: envía a través del backend SchoolSena, que a su vez llama
directamente a WhatsApp Cloud API (Meta). Ya NO usa un "Laravel Telecom proxy" ni company_id.
"""

import httpx
import logging
from config.aspirantes_config import aspirantes_settings

logger = logging.getLogger("lyra.aspirantes.whatsapp_service")

class AspirantesWhatsappService:
    def __init__(self):
        self.config = aspirantes_settings

    async def send_message(self, to_phone: str, text: str) -> bool:
        return await self._send_schoolsena(to_phone, text)

    async def send_interactive_buttons(self, to_phone: str, text: str, buttons: list) -> bool:
        # La API directa de Meta requiere payload interactivo propio; por ahora se envía el
        # texto del cuerpo. (Los botones se manejan vía plantillas aprobadas en SchoolSena.)
        return await self._send_schoolsena(to_phone, text)

    async def send_location_request(self, to_phone: str, text: str) -> bool:
        return await self._send_schoolsena(to_phone, text)

    async def _send_schoolsena(self, to_phone: str, text: str) -> bool:
        url = f"{self.config.api_base}/sena/aspirante/send"
        payload = {"to": to_phone, "message": text}
        logger.info(f"Enviando mensaje a {to_phone} vía SchoolSena | URL: {url}")
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code not in (200, 201):
                    logger.error(f"[Aspirantes] Error SchoolSena [{resp.status_code}]: {resp.text}")
                    return False
                return True
            except Exception as e:
                logger.error(f"[Aspirantes] Connection error SchoolSena: {e}")
                return False
