"""
Cliente HTTP para crear solicitudes de taxi en el backend Laravel.

Mantiene el contrato existente de POST /taxi/solicitud-telefonica.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

import httpx

from core.config import settings

logger = logging.getLogger("lyra.telephony.backend")


class TelephonyBackendClient:
    """Envía solicitudes telefónicas al backend IntelliTaxi."""

    DEFAULT_CHANNEL = "PHONE_AI_CALL"
    FREESWITCH_CHANNEL = "FREESWITCH_AI_CALL"

    def __init__(
        self,
        api_base: Optional[str] = None,
        timeout: httpx.Timeout | float = httpx.Timeout(connect=2.0, read=8.0, write=5.0, pool=5.0),
    ):
        self.api_base = (api_base or settings.INTELLITAXI_API_BASE).rstrip("/")
        self.timeout = timeout

    def build_payload(
        self,
        *,
        celular: Optional[str],
        origen: str,
        destino: Optional[str] = None,
        origen_lat: float = 0.0,
        origen_lng: float = 0.0,
        destino_lat: float = 0.0,
        destino_lng: float = 0.0,
        canal_origen: Optional[str] = None,
        call_uuid: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pasajero_id": 1,
            "celular": celular,
            "telefonoLlamada": celular,
            "telefono_cliente_final": celular,
            "pasajero_nombre": "Usuario Telefónico",
            "canal_origen": canal_origen or self.DEFAULT_CHANNEL,
            "origen": origen,
            "origen_lat": float(origen_lat),
            "origen_lng": float(origen_lng),
            "destino": (destino or "").strip(),
            "destino_lat": float(destino_lat),
            "destino_lng": float(destino_lng),
            "clase_vehiculo": "TAXI",
            "precio_estimado": 0.0,
        }
        if call_uuid:
            payload["call_uuid"] = call_uuid
        return payload

    async def create_service(
        self,
        payload: dict[str, Any],
        http_client: Optional[httpx.AsyncClient] = None,
        *,
        skip_idempotency: bool = False,
    ) -> Tuple[bool, str, Optional[dict]]:
        """
        POST al backend Laravel.

        Returns: (success, user_message, response_json)
        """
        from services.telephony.idempotency import get_submission_guard

        call_uuid = payload.get("call_uuid")
        guard = get_submission_guard()
        if not skip_idempotency and guard.already_submitted(call_uuid):
            logger.warning(
                "[backend] duplicate blocked call_uuid=%s",
                call_uuid,
            )
            return (
                True,
                (
                    "Tu solicitud ya fue registrada. "
                    "En un momento el conductor se comunica contigo."
                ),
                None,
            )

        url = f"{self.api_base}/taxi/solicitud-telefonica"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        logger.info(
            "[backend] create_service call_uuid=%s canal=%s origen=%r",
            payload.get("call_uuid"),
            payload.get("canal_origen"),
            payload.get("origen"),
        )

        client = http_client
        owns_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            owns_client = True

        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.error(
                    "[backend] HTTP %s call_uuid=%s body=%s",
                    resp.status_code,
                    payload.get("call_uuid"),
                    resp.text[:300],
                )
                return (
                    False,
                    "Tuvimos un problema registrando tu servicio. Inténtalo de nuevo.",
                    None,
                )

            data = None
            try:
                data = resp.json()
            except Exception:
                pass

            if not skip_idempotency:
                guard.mark_submitted(call_uuid)

            return (
                True,
                (
                    "Te enviaremos los datos del conductor por WhatsApp "
                    "y en un momento él se comunica contigo. "
                    "¡Que tengas un excelente viaje!"
                ),
                data,
            )
        except httpx.TimeoutException:
            logger.warning("[backend] timeout call_uuid=%s", payload.get("call_uuid"))
            return False, "Se demoró el servidor. Inténtalo de nuevo, porfa.", None
        except Exception as e:
            logger.error("[backend] error call_uuid=%s err=%s", payload.get("call_uuid"), e)
            return False, "Problemita técnico. Intenta de nuevo o pide el taxi por la app.", None
        finally:
            if owns_client and client is not None:
                await client.aclose()

    async def create_service_from_geocoded(
        self,
        *,
        celular: Optional[str],
        origen: str,
        destino: Optional[str],
        call_uuid: Optional[str] = None,
        use_freeswitch_channel: bool = True,
        http_client: Optional[httpx.AsyncClient] = None,
        origen_barrio: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Geocodifica origen/destino y crea el servicio en Laravel.
        Usado por test-create-service y por el motor conversacional.
        """
        from core.geocoder_service import geocode

        # Pasar el barrio resuelto mejora la precisión de Google para
        # nomenclaturas colombianas (paridad con el gateway Twilio).
        g_o = await geocode(origen, barrio=origen_barrio)
        if not g_o:
            return False, (
                "No me aparece esa ubicación en Popayán. "
                "¿Me la dices de otra forma? Prueba con un barrio o una calle."
            )

        olat, olng, _ = g_o
        dlat = dlng = 0.0
        if destino:
            g_d = await geocode(destino)
            if not g_d:
                return False, (
                    "El destino no me aparece en Popayán. "
                    "¿Me lo dices de otra forma?"
                )
            dlat, dlng, _ = g_d

        channel = (
            self.FREESWITCH_CHANNEL if use_freeswitch_channel else self.DEFAULT_CHANNEL
        )
        payload = self.build_payload(
            celular=celular,
            origen=origen,
            destino=destino,
            origen_lat=olat,
            origen_lng=olng,
            destino_lat=dlat,
            destino_lng=dlng,
            canal_origen=channel,
            call_uuid=call_uuid,
        )
        ok, msg, _ = await self.create_service(payload, http_client=http_client)
        return ok, msg
