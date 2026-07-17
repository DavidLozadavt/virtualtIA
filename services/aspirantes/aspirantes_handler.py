"""
services/aspirantes/aspirantes_handler.py — Stateless processor for Aspirantes WhatsApp button clicks.
"""

import httpx
import logging
import unicodedata
from config.aspirantes_config import aspirantes_settings

logger = logging.getLogger("lyra.aspirantes.aspirantes_handler")

class AspirantesHandler:
    def __init__(self):
        self.config = aspirantes_settings

    def _normalize(self, text: str) -> str:
        if not text:
            return ""
        # Normalize Unicode accents and convert to lowercase
        nfkd = unicodedata.normalize("NFKD", str(text))
        return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()

    def parse_response(self, text: str, button_id: str = None) -> str | None:
        """
        Parses button click or text response into either 'SI' or 'NO'.
        Returns None if the response doesn't match positive or negative intents.
        """
        combined = f"{button_id or ''} {text or ''}".strip()
        norm = self._normalize(combined)

        if not norm:
            return None

        # Positive keywords
        positives = ["si", "sii", "yes", "confirmar", "aceptar", "si_button", "true"]
        # Negative keywords
        negatives = ["no", "cancelar", "rechazar", "no_button", "false"]

        # Check exact matches or prefixes
        tokens = norm.split()
        for t in tokens:
            if t in positives:
                return "SI"
            if t in negatives:
                return "NO"

        # Fallback substring checks
        for pos in positives:
            if pos in norm:
                return "SI"
        for neg in negatives:
            if neg in norm:
                return "NO"

        return None

    async def process_message(self, sender_phone: str, message_content: str, message_id: str, company_id: int, button_id: str = None):
        """
        Stateless entry point. Parses the button clicked and updates the database record in Laravel ERP.
        """
        logger.info(f"Processing message from {sender_phone} (msg_id: {message_id}, company_id: {company_id})")

        parsed_response = self.parse_response(message_content, button_id)
        if not parsed_response:
            logger.warning(f"Response from {sender_phone} could not be resolved to SI/NO: '{message_content}' (button_id: {button_id})")
            return

        logger.info(f"Parsed response for {sender_phone} as '{parsed_response}'")

        # Actualiza la respuesta directamente en el backend autónomo SchoolSena
        url = f"{self.config.api_base}/sena/aspirante/update-response"
        payload = {
            "phone": sender_phone,
            "response": parsed_response,
            "message_id": message_id,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    logger.info(f"Successfully updated response for {sender_phone} in Laravel ERP")
                else:
                    logger.error(f"Failed to update response in Laravel [{resp.status_code}]: {resp.text}")
            except Exception as e:
                logger.error(f"Connection error when calling Laravel ERP update: {e}")
