"""
tests/test_whatsapp_aspirantes.py — Unit and integration tests for the stateless Aspirantes campaign response processor.
"""

import sys
import os
import unittest
from unittest.mock import AsyncMock, patch

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from config.aspirantes_config import aspirantes_settings
from api.routers.whatsapp_aspirantes import PROCESSED_MESSAGES

class TestWhatsappAspirantes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.phone = "573001234567"
        # Reset message cache before each test
        PROCESSED_MESSAGES.cache.clear()

    def test_ignored_wrong_company(self):
        # Sending message for company_id = 1 should be ignored by Aspirantes router
        payload = {
            "company_id": 1,
            "from": self.phone,
            "body": "Sí",
            "message_id": "msg_wrong_company"
        }
        response = self.client.post("/wh/whatsapp_aspirantes/universal", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ignored_wrong_company"})

    @patch("httpx.AsyncClient.post")
    def test_processing_positive_response_text(self, mock_post):
        # Setup mock response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        payload = {
            "company_id": 2,
            "from": self.phone,
            "body": "Sí, confirmo",
            "message_id": "msg_pos_text"
        }
        response = self.client.post("/wh/whatsapp_aspirantes/universal", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

        # Verify that Laravel was notified of a "SI" response
        mock_post.assert_called_once()
        called_url = mock_post.call_args[0][0]
        called_json = mock_post.call_args[1]["json"]

        self.assertEqual(called_url, f"{aspirantes_settings.INTELLITAXI_API_BASE}/sena/aspirante/update-response")
        self.assertEqual(called_json["phone"], self.phone)
        self.assertEqual(called_json["response"], "SI")
        self.assertEqual(called_json["message_id"], "msg_pos_text")
        self.assertEqual(called_json["company_id"], 2)

    @patch("httpx.AsyncClient.post")
    def test_processing_negative_response_button(self, mock_post):
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        payload = {
            "company_id": 2,
            "from": self.phone,
            "body": "",
            "button_id": "no_button",
            "message_id": "msg_neg_btn"
        }
        response = self.client.post("/wh/whatsapp_aspirantes/universal", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

        # Verify that Laravel was notified of a "NO" response
        mock_post.assert_called_once()
        called_json = mock_post.call_args[1]["json"]
        self.assertEqual(called_json["response"], "NO")

    @patch("httpx.AsyncClient.post")
    def test_ignore_unrecognized_response(self, mock_post):
        payload = {
            "company_id": 2,
            "from": self.phone,
            "body": "Hola, ¿cómo estás?",
            "message_id": "msg_unrecognized"
        }
        response = self.client.post("/wh/whatsapp_aspirantes/universal", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

        # Response cannot be parsed into SI/NO, so Laravel update should NOT be called
        mock_post.assert_not_called()

    @patch("httpx.AsyncClient.post")
    def test_ignore_duplicate_message(self, mock_post):
        payload = {
            "company_id": 2,
            "from": self.phone,
            "body": "Sí",
            "message_id": "msg_dup"
        }
        # First send
        response1 = self.client.post("/wh/whatsapp_aspirantes/universal", json=payload)
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response1.json(), {"status": "success"})

        # Second send (duplicate message ID)
        response2 = self.client.post("/wh/whatsapp_aspirantes/universal", json=payload)
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json(), {"status": "ignored_duplicate"})

        # Laravel should only be updated once
        self.assertEqual(mock_post.call_count, 1)

if __name__ == "__main__":
    unittest.main()
