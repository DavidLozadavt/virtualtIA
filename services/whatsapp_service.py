# services/whatsapp.py
import re
import time
import httpx
from typing import Dict, Optional

from core.logger import setup_logger
from core.address_utils import (
    _nominatim_geocode,
    normalize_address,
    _try_local_match,
    _parse_si_no,
    _is_correction_request,
    extract_pickup_address,
    extract_destination_address,
)

logger = setup_logger("lyra.services.whatsapp")

# ── Estados de la conversación ────────────────────────────────────────────────

STATE_NEW                  = "new"
STATE_WAITING_TIPO_SERVICIO = "waiting_tipo_servicio"
STATE_WAITING_HORA_PROG    = "waiting_hora_prog"
STATE_WAITING_ORIGIN       = "waiting_origin"
STATE_CONFIRMING_ORIGIN    = "confirming_origin"
STATE_WAITING_DEST_OR_SKIP = "waiting_dest_or_skip"
STATE_FINISHED             = "finished"

_GREETING_WORDS = {
    "hola", "holas", "buen", "buenos", "buenas", "dia", "dias",
    "tarde", "tardes", "noche", "noches", "qhubo", "que", "mas",
    "saludos", "ola", "holi", "holis", "tal", "mija", "amiga",
}

_CANCEL_WORDS = {"cancelar", "salir", "reiniciar", "adios", "adiós", "no más"}


# ── Sesión ────────────────────────────────────────────────────────────────────

class WpSession:
    __slots__ = (
        "phone", "state", "tipo_servicio", "fecha_hora_prog",
        "origen_text", "origen_barrio", "destino_text", "updated_at",
    )

    def __init__(self, phone: str):
        self.phone           = phone
        self.state           = STATE_NEW
        self.tipo_servicio:  Optional[str] = None
        self.fecha_hora_prog:Optional[str] = None
        self.origen_text:    Optional[str] = None
        self.origen_barrio:  Optional[str] = None
        self.destino_text:   Optional[str] = None
        self.updated_at:     float         = time.time()


# ── Store de sesiones (en memoria — reemplazable por Redis) ───────────────────

class SessionStore:
    """
    Almacenamiento de sesiones en memoria.
    Reemplazar por RedisSessionStore cuando se requiera persistencia.
    """
    def __init__(self):
        self._store: Dict[str, WpSession] = {}

    def get(self, phone: str) -> WpSession:
        if phone not in self._store:
            self._store[phone] = WpSession(phone)
        session = self._store[phone]
        session.updated_at = time.time()
        return session

    def reset(self, phone: str) -> None:
        self._store.pop(phone, None)


# ── Servicio principal ────────────────────────────────────────────────────────

class WhatsappService:
    def __init__(self, db_conn, config):
        self.db      = db_conn
        self.config  = config
        self._sessions = SessionStore()

    # ── Meta API ─────────────────────────────────────────────────────────────

    async def send_message(self, to_phone: str, text: str) -> None:
        if not self._meta_configured():
            return
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": text},
        }
        await self._meta_post(to_phone, payload)

    async def send_interactive_buttons(
        self, to_phone: str, text: str, buttons: list[tuple[str, str]]
    ) -> None:
        if not self._meta_configured():
            return
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": bid, "title": btitle}}
                        for bid, btitle in buttons
                    ]
                },
            },
        }
        await self._meta_post(to_phone, payload)

    def _meta_configured(self) -> bool:
        if not self.config.WHATSAPP_API_TOKEN or not self.config.WHATSAPP_PHONE_NUMBER_ID:
            logger.warning("⚠️ Token o Phone ID no configurado")
            return False
        return True

    async def _meta_post(self, to_phone: str, payload: dict) -> None:
        url = (
            f"https://graph.facebook.com/v19.0"
            f"/{self.config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {
            "Authorization": f"Bearer {self.config.WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code not in (200, 201):
                    logger.error(f"❌ Error Meta API [{resp.status_code}]: {resp.text}")
            except Exception as exc:
                logger.error(f"❌ Error conexión Meta: {exc}")

    # ── Creación del servicio ─────────────────────────────────────────────────

    async def _create_wp_service(
        self,
        celular: Optional[str],
        origen: str,
        destino: Optional[str],
        tipo_servicio: str,
        fecha_hora_prog: Optional[str],
    ) -> tuple[bool, str]:

        origen_norm = normalize_address(origen)
        geo_origen  = _nominatim_geocode(origen_norm) or _nominatim_geocode(origen)

        if not geo_origen:
            return (
                False,
                "Ay, no me aparece esa ubicación en Popayán. "
                "¿Me la dices de otra forma? Prueba con un barrio o una calle.",
            )

        olat, olng, _ = geo_origen
        dlat, dlng    = 0.0, 0.0

        if destino:
            dest_norm  = normalize_address(destino)
            geo_destino = _nominatim_geocode(dest_norm) or _nominatim_geocode(destino)
            if not geo_destino:
                return (
                    False,
                    "Hmm, ese destino no me aparece en Popayán. "
                    "¿Me lo dices de otra forma? Una calle, barrio o sitio.",
                )
            dlat, dlng, _ = geo_destino

        # Construcción del payload
        clase_v        = "DOMICILIO" if tipo_servicio == "domicilio" else "TAXI"
        origen_payload = origen
        if fecha_hora_prog:
            origen_payload = f"{origen_payload} [Programado: {fecha_hora_prog}]"
        if tipo_servicio == "domicilio":
            origen_payload = f"[DOMICILIO] {origen_payload}"

        body = {
            "pasajero_id":      1,
            "celular":          celular,
            "pasajero_nombre":  f"WhatsApp: {tipo_servicio.title()}",
            "canal_origen":     "WHATSAPP_AI_CHAT",
            "origen":           origen_payload,
            "origen_lat":       float(olat),
            "origen_lng":       float(olng),
            "clase_vehiculo":   clase_v,
            "precio_estimado":  0.0,
            "destino":          destino.strip() if destino else "",
            "destino_lat":      float(dlat),
            "destino_lng":      float(dlng),
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.config.INTELLITAXI_API_BASE}/taxi/solicitud-telefonica",
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code >= 400:
                logger.error(f"Backend error [{resp.status_code}]: {resp.text}")
                return False, "Uy, tuvimos un problema registrando tu servicio. Inténtalo de nuevo."

            if fecha_hora_prog:
                return True, f"¡Listo! Tu {tipo_servicio} ha quedado programado para: *{fecha_hora_prog}*."
            return True, f"¡Listo! Ya te estamos buscando un {tipo_servicio}. En un momentico te contacta el conductor."

        except Exception as exc:
            logger.error(f"Backend POST WP error: {exc}")
            return False, "Tuvimos un problemita técnico. Intenta de nuevo."

    # ── Máquina de estados ────────────────────────────────────────────────────

    async def process_message(self, sender_phone: str, message: str) -> None:
        texto  = message.strip()
        t_low  = texto.lower()
        sess   = self._sessions.get(sender_phone)

        # Cancelación global
        if t_low in _CANCEL_WORDS:
            self._sessions.reset(sender_phone)
            await self.send_message(sender_phone, "Tu solicitud ha sido cancelada sin ningún problema. 😊 Cuando gustes o necesites algún servicio, solo escríbeme, ¡estaré encantada de ayudarte!")
            return

        if sess.state == STATE_FINISHED:
            sess.state = STATE_NEW

        # Saludo inicial
        if self._is_just_greeting(texto) and sess.state in (
            STATE_NEW, STATE_WAITING_ORIGIN, STATE_WAITING_TIPO_SERVICIO
        ):
            sess.state = STATE_WAITING_TIPO_SERVICIO
            await self.send_interactive_buttons(
                sender_phone,
                "¡Hola, qué gusto saludarte! 😊 Soy Lyra, tu asistente de TaxBelalcazar. Te ayudaré asistiéndote en tu servicio con el mayor de los gustos. ¿Qué tipo de servicio deseas solicitar hoy?",
                [
                    ("taxi_ahora", "Taxi Ahora"),
                    ("taxi_prog",  "Taxi Programado"),
                    ("domicilio",  "Domicilio"),
                ],
            )
            return

        if sess.state == STATE_NEW:
            sess.state = STATE_WAITING_ORIGIN

        # Dispatch por estado
        handlers = {
            STATE_WAITING_TIPO_SERVICIO: self._handle_tipo_servicio,
            STATE_WAITING_HORA_PROG:     self._handle_hora_prog,
            STATE_WAITING_ORIGIN:        self._handle_origin,
            STATE_CONFIRMING_ORIGIN:     self._handle_confirming_origin,
            STATE_WAITING_DEST_OR_SKIP:  self._handle_dest_or_skip,
        }

        handler = handlers.get(sess.state)
        if handler:
            await handler(sender_phone, texto, sess)
        else:
            logger.warning(f"Estado desconocido: {sess.state!r} para {sender_phone}")

    # ── Handlers individuales ─────────────────────────────────────────────────

    async def _handle_tipo_servicio(
        self, phone: str, texto: str, sess: WpSession
    ) -> None:
        t = texto.lower()
        if t == "taxi programado":
            sess.tipo_servicio = "taxi programado"
            sess.state         = STATE_WAITING_HORA_PROG
            await self.send_message(
                phone,
                "¡Perfecto! 📅 Has elegido la opción de *Taxi Programado*. Con muchísimo gusto te ayudaré. Cuéntame, ¿para qué fecha y a qué hora lo necesitas? Por ejemplo: mañana a las 7:00 AM. 😊",
            )
        elif t in ("taxi ahora", "domicilio"):
            sess.tipo_servicio = t
            sess.state         = STATE_WAITING_ORIGIN
            await self.send_message(
                phone,
                f"¡Excelente elección! Has solicitado *{t.title()}*. Con el mayor de los gustos te asistiré. ¿En qué parte te recogemos? Puedes decirme la dirección, calle, barrio o lugar de referencia. 😊",
            )
        else:
            # Texto libre — intentar parsear como origen directamente
            sess.state = STATE_WAITING_ORIGIN
            await self._handle_origin(phone, texto, sess)

    async def _handle_hora_prog(
        self, phone: str, texto: str, sess: WpSession
    ) -> None:
        sess.fecha_hora_prog = texto
        sess.state           = STATE_WAITING_ORIGIN
        await self.send_message(phone, f"¡Anotado con mucho gusto! 📝 Quedó programado para el *{texto}*. Ahora cuéntame, ¿en qué lugar de Popayán te recogemos? Por favor, dime la calle, barrio o lugar de referencia. 😊")

    async def _handle_origin(
        self, phone: str, texto: str, sess: WpSession
    ) -> None:
        origen_llm, hint = extract_pickup_address(texto)
        origen = normalize_address(origen_llm or texto).strip()

        if not origen or len(origen) < 2:
            await self.send_message(phone, hint or "Disculpa, ¿me podrías indicar nuevamente en dónde te recogemos? Por favor, dime la dirección, calle o barrio. 😊")
            return

        sess.origen_text = origen

        # Calle detectada → intentar confirmar barrio
        if re.search(r'(?:calle|carrera|cl|cra|kr?a?)\s*\d+', origen, re.IGNORECASE):
            await self._try_confirm_barrio(phone, origen, sess)
            return

        sess.state = STATE_WAITING_DEST_OR_SKIP
        await self.send_message(
            phone,
            f"¡Listo! 📍 Te recogeremos en *{origen}*. ¿Hacia dónde te diriges hoy? Puedes decirme tu destino o escribirme *NO* si prefieres coordinarlo directamente con el conductor. 😊",
        )

    async def _try_confirm_barrio(
        self, phone: str, origen: str, sess: WpSession
    ) -> None:
        try:
            from tools.popayan_geodata import geocode_local, get_nearby_barrios, ALL_BARRIOS, _haversine

            geo = geocode_local(origen)
            if geo:
                nearby = get_nearby_barrios(geo[0], geo[1], radius_km=5.0)
                if not nearby:
                    closest = min(ALL_BARRIOS.items(), key=lambda x: _haversine(geo[0], geo[1], x[1][0], x[1][1]))
                    nearby  = [{"name": closest[0]}]

                if nearby:
                    sess.origen_barrio = nearby[0]["name"]
                    sess.state         = STATE_CONFIRMING_ORIGIN
                    await self.send_message(
                        phone,
                        f"Entendido, veo que la dirección es en {origen}. 📍 Eso queda por el barrio *{sess.origen_barrio}*, ¿es correcto? Respóndeme *SÍ*, o por favor cuéntame el nombre de tu barrio para ubicarte de la mejor manera. 😊",
                    )
                    return
        except Exception as exc:
            logger.warning(f"Error confirmando barrio: {exc}")

        sess.state = STATE_CONFIRMING_ORIGIN
        await self.send_message(
            phone,
            f"Perfecto, {origen}. ¿Me podrías indicar en qué barrio queda? Cuéntame el nombre del barrio para poder ubicarte muchísimo mejor. 😊",
        )

    async def _handle_confirming_origin(
        self, phone: str, texto: str, sess: WpSession
    ) -> None:
        respuesta = _parse_si_no(texto)

        if respuesta is True:
            sess.state = STATE_WAITING_DEST_OR_SKIP
            await self.send_message(
                phone,
                f"¡Perfecto! ✅ Te recogeremos en *{sess.origen_text}*. ¿Hacia dónde viajas hoy? Puedes decirme tu destino o responderme *NO* si prefieres decírselo al conductor. 😊",
            )
            return

        if respuesta is False:
            sess.state = STATE_WAITING_ORIGIN
            await self.send_message(phone, "¡No te preocupes, no hay ningún problema! ¿En qué barrio estás entonces? Cuéntame el nombre para poder ubicarte muchísimo mejor. 😊")
            return

        local = _try_local_match(texto)
        if local:
            sess.origen_text = local
            sess.state       = STATE_WAITING_DEST_OR_SKIP
            await self.send_message(
                phone,
                f"¡Excelente! 📍 Te recogeremos en *{local}*. ¿Hacia dónde te diriges hoy? Puedes decirme tu destino o responderme *NO*. 😊",
            )
            return

        await self.send_message(
            phone,
            f"Disculpa, no logré entenderte muy bien. ¿Confirmas que estás por el barrio *{sess.origen_barrio or 'esa zona'}*? Respóndeme *SÍ* o cuéntame en qué barrio te encuentras. 😊",
        )

    async def _handle_dest_or_skip(
        self, phone: str, texto: str, sess: WpSession
    ) -> None:
        if _is_correction_request(texto):
            sess.state       = STATE_WAITING_ORIGIN
            sess.origen_text = None
            await self.send_message(phone, "¡Claro que sí, no te preocupes! Vamos a corregir la ubicación de inmediato. 😊 Cuéntame, ¿dónde te recogemos?")
            return

        if _parse_si_no(texto) is False:
            ok, msg = await self._create_wp_service(
                phone, sess.origen_text or "", None,
                sess.tipo_servicio or "taxi ahora", sess.fecha_hora_prog,
            )
            self._sessions.reset(phone)
            await self.send_message(phone, msg)
            return

        dest_llm, hint = extract_destination_address(texto)
        dest = normalize_address(dest_llm or texto).strip()

        if not dest or len(dest) < 2:
            await self.send_message(phone, hint or "Disculpa, ¿me indicas a dónde vas? Cuéntame el barrio, calle o sitio de destino, o escribe *NO*. 😊")
            return

        sess.destino_text = dest
        ok, msg = await self._create_wp_service(
            phone, sess.origen_text or "", dest,
            sess.tipo_servicio or "taxi ahora", sess.fecha_hora_prog,
        )
        self._sessions.reset(phone)
        await self.send_message(phone, msg)

    # ── Utilidades ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_just_greeting(text: str) -> bool:
        t     = re.sub(r'[^\w\s]', '', text.lower().strip())
        words = t.split()
        return bool(words) and all(w in _GREETING_WORDS for w in words)