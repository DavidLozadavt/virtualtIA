"""
routers/whatsapp.py — Webhook integration for Meta WhatsApp Cloud API.
"""

import logging
import httpx
from fastapi import APIRouter, Request, HTTPException, Response, BackgroundTasks

from core.config import settings
from orchestrator.context_builder import load_project_config
from orchestrator.tool_runner import run_agent_loop
from core.address_utils import (
    extract_pickup_address,
    extract_destination_address,
    _parse_si_no,
    normalize_address,
    _try_local_match,
    _nominatim_geocode,
    _nominatim_reverse_geocode_async,
    extract_datetime_with_llm,
    looks_like_place,
)

logger = logging.getLogger("lyra.whatsapp")
whatsapp_router = APIRouter(prefix="/wh/whatsapp", tags=["whatsapp"])

REACTIVACION_RECORDATORIO_DEFAULT = (
    "Tu solicitud está pendiente de confirmación. "
    "Utiliza los botones del mensaje anterior para indicar si deseas volver a solicitar el servicio o cancelarlo."
)


async def _tiene_reactivacion_pendiente(sender_phone: str, company_id: int) -> tuple[bool, str | None]:
    """Consulta al ERP si el teléfono tiene una reactivación WhatsApp pendiente."""
    url = f"{settings.INTELLITAXI_API_BASE}/taxi/reactivacion-whatsapp/pendiente"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                url,
                params={"telefono": sender_phone, "company_id": company_id},
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("pendiente"):
                return True, data.get("mensaje") or REACTIVACION_RECORDATORIO_DEFAULT
    except Exception as e:
        logger.warning(f"reactivacion pendiente check failed: {e}")
    return False, None


# ✅ VERIFICACIÓN WEBHOOK
@whatsapp_router.get("")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
            logger.info("✅ WhatsApp Webhook verified!")
            return Response(content=challenge, media_type="text/plain")
        else:
            raise HTTPException(status_code=403, detail="Verification token mismatch")

    raise HTTPException(status_code=400, detail="Missing parameters")


# ── DEDUPLICACIÓN DE MENSAJES ──
from collections import OrderedDict

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


# RECIBIR MENSAJES UNIVERSAL (DESDE LARAVEL TELECOM MANAGER)
@whatsapp_router.post("/universal")
async def receive_universal_message(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    company_id = body.get("company_id", 1)
    sender_phone = body.get("from")
    message_content = body.get("body", "")
    message_id = body.get("message_id")

    if message_id and PROCESSED_MESSAGES.is_processed(message_id):
        print(f"♻️ MENSAJE UNIVERSAL DUPLICADO IGNORADO: {message_id}")
        return {"status": "ignored_duplicate"}

    if not sender_phone or not message_content:
        return {"status": "ignored"}

    print(f"📩 MENSAJE UNIVERSAL [{company_id}]:", message_content)
    print("📞 FROM:", sender_phone)
    
    background_tasks.add_task(process_whatsapp_message, sender_phone, message_content, company_id)

    return {"status": "success"}


# ✅ RECIBIR MENSAJES META (MANTENIDO POR COMPATIBILIDAD)
@whatsapp_router.post("")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if body.get("object") != "whatsapp_business_account":
        return Response(content="EVENT_RECEIVED", status_code=200)

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            val = change.get("value", {})
            messages = val.get("messages", [])

            if not messages:
                continue

            for msg in messages:
                sender_phone = msg.get("from")
                msg_type = msg.get("type")

                message_content = ""

                if msg_type == "text":
                    message_content = msg.get("text", {}).get("body", "")

                elif msg_type == "location":
                    lat = msg.get("location", {}).get("latitude")
                    lng = msg.get("location", {}).get("longitude")
                    name = msg.get("location", {}).get("name", "")
                    addr = msg.get("location", {}).get("address", "")
                    
                    parts = [p for p in (name, addr) if p]
                    if parts:
                        loc_text = " - ".join(parts)
                        message_content = f"Ubicación en mapa: {lat},{lng} | {loc_text}"
                    else:
                        message_content = f"Ubicación en mapa: {lat},{lng}"

                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    if interactive.get("type") == "button_reply":
                        message_content = interactive.get("button_reply", {}).get("title", "")


                else:
                    logger.info(f"Ignored type '{msg_type}' from {sender_phone}")
                    continue

                msg_id = msg.get("id")
                if msg_id and PROCESSED_MESSAGES.is_processed(msg_id):
                    print(f"♻️ MENSAJE META DUPLICADO IGNORADO: {msg_id}")
                    continue

                if message_content and sender_phone:
                    print("📩 MENSAJE:", message_content)
                    print("📞 FROM:", sender_phone)

                    background_tasks.add_task(process_whatsapp_message, sender_phone, message_content, 1)

    return Response(content="EVENT_RECEIVED", status_code=200)


# ✅ ENVIAR MENSAJE A WHATSAPP (A través del TelecomManager en Laravel)
async def send_whatsapp_message(to_phone: str, text: str):
    sess = get_wp_session(to_phone)
    company_id = sess.company_id
    url = f"{settings.INTELLITAXI_API_BASE}/admin/telecom/send"
    
    payload = {
        "company_id": company_id,
        "to": to_phone,
        "message": text,
        "type": "text"
    }

    print("📤 ENVIANDO A:", to_phone)
    print("💬 RESPUESTA:", text)

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            print("📡 TELECOM MANAGER RESPONSE:", resp.status_code, resp.text)
            if resp.status_code not in (200, 201):
                logger.error(f"❌ Error Telecom: {resp.text}")
        except Exception as e:
            logger.error(f"❌ Error conexión Telecom Laravel: {e}")


async def send_whatsapp_interactive_buttons(to_phone: str, text: str, buttons: list):
    sess = get_wp_session(to_phone)
    company_id = sess.company_id
    url = f"{settings.INTELLITAXI_API_BASE}/admin/telecom/send"
    
    button_list = [{"id": btn_id, "title": btn_title} for btn_id, btn_title in buttons]
    
    payload = {
        "company_id": company_id,
        "to": to_phone,
        "message": text,
        "type": "interactive",
        "buttons": button_list
    }

    print("📤 ENVIANDO BOTONES A:", to_phone)

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            print("📡 TELECOM MANAGER RESPONSE:", resp.status_code, resp.text)
            if resp.status_code not in (200, 201):
                logger.error(f"❌ Error Telecom Botones: {resp.text}")
        except Exception as e:
            logger.error(f"❌ Error conexión Telecom Laravel: {e}")


async def send_whatsapp_location_request(to_phone: str, text: str):
    sess = get_wp_session(to_phone)
    company_id = sess.company_id
    url = f"{settings.INTELLITAXI_API_BASE}/admin/telecom/send"
    
    payload = {
        "company_id": company_id,
        "to": to_phone,
        "message": text,
        "type": "location_request"
    }

    print("📤 ENVIANDO SOLICITUD DE UBICACIÓN A:", to_phone)
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            print("📡 TELECOM MANAGER RESPONSE:", resp.status_code, resp.text)
            if resp.status_code not in (200, 201):
                logger.error(f"❌ Error Telecom Ubicación: {resp.text}")
        except Exception as e:
            logger.error(f"❌ Error conexión Telecom Laravel: {e}")

import time
from typing import Dict, Optional
import re

STATE_NEW = "new"
STATE_WAITING_TIPO_SERVICIO = "waiting_tipo_servicio"
STATE_WAITING_HORA_PROG = "waiting_hora_prog"
STATE_WAITING_ORIGIN = "waiting_origin"
STATE_CONFIRMING_ORIGIN = "confirming_origin"
STATE_WAITING_DOM_ORIGIN = "waiting_dom_origin"
STATE_WAITING_DOM_DEST = "waiting_dom_dest"
STATE_WAITING_DOM_OBS = "waiting_dom_obs"
STATE_FINISHED = "finished"

# Prompt para solicitar la dirección de entrega de un domicilio (único flujo con destino).
_DOM_DEST_PROMPT = (
    "📦 ¿A qué dirección llevamos el domicilio?\n\n"
    "Comparte la ubicación de entrega con el botón, o escríbela."
)

# Prompt de observación para domicilios (tras capturar origen y dirección de entrega).
_DOM_OBS_PROMPT = (
    "✅ Recogida y entrega anotadas.\n\n"
    "📝 ¿Alguna observación? (opcional)\n\n"
    "Por ejemplo:\n"
    "• Qué se recoge\n"
    "• Nombre de quien recibe\n"
    "• Teléfono de contacto\n"
    "• Instrucciones de entrega\n\n"
    "Escríbela, o responde *NO* para omitir."
)

# Marcadores de pregunta/charla conversacional — NO son direcciones.
_QUESTION_MARKERS = (
    "cuando", "cuándo", "cuanto", "cuánto", "como", "cómo",
    "por que", "porque", "por qué", "cual", "cuál",
    "que hora", "qué hora", "a que hora", "a qué hora",
    "demora", "demoran", "demoro", "tarda", "tardan", "falta", "ya viene",
    "vale", "cuesta", "precio", "tarifa", "cobran", "valor", "cuanto sale",
    "cuanto cobran", "donde esta", "dónde está", "donde va", "ya llego", "ya llegó",
)


def is_conversational_query(text: str) -> bool:
    """
    True si el texto es una pregunta/charla conversacional y NO una dirección.
    Ej: 'buenas, el servicio cuándo llega', '¿cuánto vale?', 'ya viene?'.
    """
    if "?" in text or "¿" in text:
        return True
    t = re.sub(r'[^\w\s]', ' ', text.lower().strip())
    t = re.sub(r'\s+', ' ', t).strip()
    if not t:
        return False
    return any(m in t for m in _QUESTION_MARKERS)


# Señal EXPLÍCITA de dirección (número, nomenclatura de calle, barrio, sector…).
# A diferencia de looks_like_place(), NO usa fuzzy/catálogo → sin falsos positivos.
_ADDRESS_SIGNAL_RE = re.compile(
    r'\d|#|\b(calle|carrera|cra|cr|cl|kr|kra|av|avenida|diagonal|diag|'
    r'transversal|tr|barrio|sector|vereda|conjunto|urbanizaci[oó]n|manzana|mz)\b',
    re.IGNORECASE,
)


def _has_address_signal(text: str) -> bool:
    """True si el texto contiene una señal explícita de dirección/lugar."""
    return bool(_ADDRESS_SIGNAL_RE.search(text or ""))


def clean_map_location(loc_name: str) -> str:
    """Removes city, country, and zip codes from a map location name for a more natural response."""
    if not loc_name:
        return ""
    loc = loc_name.strip()
    loc = re.sub(r',\s*Popayán.*', '', loc, flags=re.IGNORECASE)
    loc = re.sub(r',\s*Cauca.*', '', loc, flags=re.IGNORECASE)
    loc = re.sub(r',\s*Colombia.*', '', loc, flags=re.IGNORECASE)
    loc = re.sub(r',\s*CO$', '', loc, flags=re.IGNORECASE)
    return loc.strip()

class WpSession:
    def __init__(self, phone: str, company_id: int = 1):
        self.phone = phone
        self.company_id = company_id
        self.state = STATE_NEW
        self.tipo_servicio: Optional[str] = None
        self.fecha_hora_prog: Optional[str] = None
        self.fecha_programada: Optional[str] = None
        self.hora_programada: Optional[str] = None
        self.origen_text: Optional[str] = None
        self.origen_barrio: Optional[str] = None
        self.destino_text: Optional[str] = None
        self.observacion: Optional[str] = None
        self.updated_at: float = time.time()

_WP_SESSIONS: Dict[str, WpSession] = {}

def get_wp_session(phone: str, company_id: int = 1) -> WpSession:
    if phone not in _WP_SESSIONS:
        _WP_SESSIONS[phone] = WpSession(phone, company_id)
    s = _WP_SESSIONS[phone]
    if company_id:
        s.company_id = company_id
    s.updated_at = time.time()
    return s

def reset_wp_session(phone: str):
    _WP_SESSIONS.pop(phone, None)


# (Using extract_datetime_with_llm from core.address_utils)


# ✅ RESOLUCIÓN GEOGRÁFICA UNIFICADA (mismo pipeline que la llamada telefónica)
async def _resolve_wp_location(raw: str) -> tuple[float, float, str]:
    """Resuelve una ubicación de WhatsApp con el MISMO motor que las llamadas.

    Único pipeline compartido: core.geocoder_service (Cache → Google → Nominatim,
    con sus reglas de normalización, ranking y validación). No existe un motor
    geográfico distinto para WhatsApp.

    Dos entradas posibles:
      1. Ubicación compartida ("Ubicación en mapa: lat,lng | nombre"): se ejecuta
         Reverse Geocoding vía Google Maps sobre las coordenadas exactas para
         obtener la DIRECCIÓN POSTAL COMPLETA (no un POI cercano), y esa dirección
         se resuelve con el pipeline compartido. Las coordenadas ORIGINALES se
         conservan como fuente de verdad exacta durante todo el flujo.
      2. Texto libre (dirección escrita): se resuelve con el pipeline compartido,
         idéntico al de la llamada.

    Retorna (lat, lng, texto_dirección). lat/lng = 0.0 solo si no se resolvió.
    """
    from core.geocoder_service import run_pipeline, reverse_geocode_address

    map_match = re.search(
        r"Ubicación en mapa:\s*(-?\d+\.\d+),(-?\d+\.\d+)(?:\s*\|\s*(.*))?", raw or ""
    )
    if map_match:
        olat, olng, _shared_name = map_match.groups()
        olat, olng = float(olat), float(olng)

        # Coordenadas exactas → dirección postal completa (nunca un POI).
        full_address = await reverse_geocode_address(olat, olng)

        origen_text = None
        if full_address:
            # Misma resolución que la llamada: normaliza y etiqueta el barrio.
            res = await run_pipeline(full_address)
            if res.resolved and res.selected:
                origen_text = res.selected.display_name
            else:
                origen_text = clean_map_location(full_address)

        if not origen_text:
            # Sin reverse geocoding: enlace GPS con las coordenadas exactas.
            origen_text = (
                f"Ubicación compartida GPS "
                f"(Enlace: https://maps.google.com/?q={olat},{olng})"
            )

        # Las coordenadas ORIGINALES son la fuente de verdad exacta.
        return olat, olng, origen_text

    # Texto libre → mismo pipeline que la llamada telefónica.
    text = (raw or "").strip()
    res = await run_pipeline(text)
    if res.resolved and res.selected:
        c = res.selected
        return c.lat, c.lng, c.display_name

    # No resolvió con coordenadas: conservar el texto normalizado.
    return 0.0, 0.0, (normalize_address(text) or text)


# ✅ CREAR SERVICIO (ADAPTADO WHATSAPP)
async def _create_wp_service(
    celular: Optional[str],
    origen: str,
    destino: Optional[str],
    tipo_servicio: str,
    fecha_programada: Optional[str],
    hora_programada: Optional[str],
    observacion: Optional[str] = None
) -> tuple[bool, str]:
    import re

    # Resolución geográfica UNIFICADA: WhatsApp usa exactamente el mismo pipeline
    # que la llamada telefónica (core.geocoder_service). Una ubicación compartida
    # se convierte primero en dirección completa vía Reverse Geocoding, y las
    # coordenadas exactas se conservan como fuente de verdad.
    olat, olng, origen = await _resolve_wp_location(origen)

    # Solo el domicilio lleva destino (dirección de entrega). Taxi ahora/programado → destino=None.
    dlat, dlng = 0.0, 0.0
    if destino:
        dlat, dlng, destino = await _resolve_wp_location(destino)

    clase_v = "TAXI"
    service_type = "TAXI AHORA"
    
    if tipo_servicio == "domicilio":
        clase_v = "DOMICILIO"
        service_type = "DOMICILIO"
    elif tipo_servicio == "taxi programado":
        service_type = "PROGRAMADO"

    payload_origen = origen
    if fecha_programada and hora_programada:
        payload_origen = f"{origen} [Programado: {fecha_programada} {hora_programada}]"
    if tipo_servicio == "domicilio":
        payload_origen = f"[DOMICILIO] {payload_origen}"

    payload = {
        "pasajero_id": 1,
        "celular": celular,
        "pasajero_nombre": f"WhatsApp: {tipo_servicio.title()}",
        "canal_origen": "WHATSAPP_AI_CHAT",
        "origen": payload_origen,
        "origen_lat": float(olat),
        "origen_lng": float(olng),
        "clase_vehiculo": clase_v,
        "service_type": service_type,
        "precio_estimado": 0.0,
    }
    
    if fecha_programada and hora_programada:
        payload["fecha_programada"] = fecha_programada
        payload["hora_programada"] = hora_programada

    if destino and destino.strip():
        payload["destino"] = destino.strip()
        payload["destino_lat"] = float(dlat)
        payload["destino_lng"] = float(dlng)
    else:
        payload["destino"] = ""
        payload["destino_lat"] = 0.0
        payload["destino_lng"] = 0.0

    if observacion:
        payload["observaciones"] = observacion

    sess = get_wp_session(celular or "", 1)
    pendiente, msg_pendiente = await _tiene_reactivacion_pendiente(celular or sess.phone, sess.company_id)
    if pendiente:
        await send_whatsapp_message(celular or sess.phone, msg_pendiente or REACTIVACION_RECORDATORIO_DEFAULT)
        return False, msg_pendiente or REACTIVACION_RECORDATORIO_DEFAULT

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.INTELLITAXI_API_BASE}/taxi/solicitud-telefonica",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
        if resp.status_code >= 400:
            return False, "Uy, tuvimos un problema registrando tu servicio. Dale, inténtalo de nuevo en unos segunditos."
        
        if fecha_programada and hora_programada:
            return True, (
                "✅ ¡Servicio programado!\n\n"
                f"📅 Fecha: {fecha_programada}\n"
                f"🕒 Hora: {hora_programada}\n\n"
                "📍 Recogida:\n"
                f"{origen}\n\n"
                "🚕 Un conductor te recogerá a esa hora.\n"
                "Gracias por preferirnos."
            )
        if tipo_servicio == "domicilio":
            return True, (
                "✅ ¡Domicilio registrado!\n\n"
                "📍 Recogida:\n"
                f"{origen}\n\n"
                "📦 Entrega:\n"
                f"{destino or 'Por confirmar'}\n\n"
                "🚕 Un mensajero se comunicará contigo en un momento.\n"
                "Gracias por preferirnos."
            )
        return True, (
            "✅ Ubicación recibida\n\n"
            "📍 Recogida:\n"
            f"{origen}\n\n"
            "🚕 Estamos buscando un conductor para ti.\n"
            "Gracias por preferirnos."
        )
    except Exception as e:
        logger.error(f"Backend POST WP error: {e}")
        return False, "⚠️ Tuvimos un problemita técnico.\n\nPor favor, intenta de nuevo en unos segundos."


async def _finalizar_taxi(sender_phone: str, sess: "WpSession"):
    """Crea el servicio (taxi ahora / programado) solo con el origen y envía la confirmación.

    El flujo WhatsApp ya no solicita destino, por lo que siempre se invoca con destino=None.
    """
    ok, closing = await _create_wp_service(
        sender_phone,
        sess.origen_text or "",
        None,
        sess.tipo_servicio or "taxi ahora",
        sess.fecha_programada,
        sess.hora_programada,
    )
    if ok:
        sess.state = STATE_FINISHED
    await send_whatsapp_message(sender_phone, closing)


# ✅ PROCESAR MENSAJE (SIN BASE DE DATOS)
async def process_whatsapp_message(sender_phone: str, message: str, company_id: int = 1):
    import re

    def is_just_greeting(text: str) -> bool:
        t = text.lower().strip()
        t = re.sub(r'[^\w\s]', '', t)
        words = t.split()
        greetings = {
            "hola", "holas", "buen", "buenos", "buenas", "dia", "dias", "tarde", "tardes", "noche", "noches", 
            "qhubo", "que", "mas", "saludos", "ola", "holi", "holis", "tal", "mija", "amiga", "mijo", "amigo",
            "tío", "tio", "tía", "tia", "parce", "pana", "ve", "oiga", "mira", "ey"
        }
        if not words: return False
        return all(w in greetings for w in words)

    def is_thanks(text: str) -> bool:
        """Detecta mensajes de agradecimiento."""
        t = re.sub(r'[^\w\s]', '', text.lower().strip())
        thanks_phrases = {
            "gracias", "muchas gracias", "mil gracias", "gracias a ti", "gracias listo",
            "ok gracias", "okey gracias", "ok muchas gracias", "muchas gracias a ti",
            "gracia", "grcias", "grasias", "graciass", "gracias totales", "te agradezco",
            "muy amable", "que amable", "dios te bendiga", "bendecido", "bendecida"
        }
        words = t.split()
        return t in thanks_phrases or (len(words) <= 4 and any(
            t.startswith(p) for p in ["gracias", "gracia", "muchas", "mil gracias", "te agradezco", "muy amable", "que amable"]
        ))

    THANKS_RESPONSES = [
        "¡Con mucho gusto! 😊 Si necesitas otro servicio, aquí estaré.",
        "¡Para servirte! 🙌 Cuando necesites, cuéntame.",
        "¡De nada! Fue un placer atenderte. Si necesitas algo más, escríbeme.",
        "¡Claro que sí! Para eso estoy. Que tengas buen viaje 🚕",
        "¡A la orden! Cuando necesites otro taxi o domicilio, me avisas 😊",
    ]

    import hashlib
    _thanks_idx = int(hashlib.md5(sender_phone.encode()).hexdigest(), 16) % len(THANKS_RESPONSES)

    texto_usuario = message.strip()
    sess = get_wp_session(sender_phone, company_id)

    pendiente, msg_pendiente = await _tiene_reactivacion_pendiente(sender_phone, company_id)
    if pendiente:
        await send_whatsapp_message(sender_phone, msg_pendiente or REACTIVACION_RECORDATORIO_DEFAULT)
        return

    # Clean text to detect cancellations
    t_clean = texto_usuario.lower()
    if t_clean in ["cancelar", "salir", "reiniciar", "adios", "adiós", "no más"]:
        reset_wp_session(sender_phone)
        await send_whatsapp_message(sender_phone, "Has cancelado la solicitud. Escríbeme cuando necesites un taxi.")
        return

    # ── DETECCIÓN GLOBAL DE AGRADECIMIENTO ──
    # Si el usuario dice gracias en cualquier estado, respondemos y NO rompemos el flujo activo.
    if is_thanks(texto_usuario):
        response = THANKS_RESPONSES[_thanks_idx]
        await send_whatsapp_message(sender_phone, response)
        # Si el servicio ya finalizó, dejamos el estado en finished para que un próximo mensaje
        # pueda reiniciar. Si estaba a mitad del flujo, no alteramos el estado.
        return

    if sess.state == STATE_FINISHED:
        # ✅ RESETEAR CONTEXTO AL INICIAR NUEVA INTERACCIÓN
        reset_wp_session(sender_phone)
        sess = get_wp_session(sender_phone, company_id)

        t_clean_new = re.sub(r'[^\w\s]', '', texto_usuario.lower()).strip()
        SERVICE_KEYWORDS_MENU = {
            "taxi", "un taxi", "necesito taxi", "quiero taxi",
            "pedir taxi", "pide taxi", "solicitar taxi",
            "servicio", "un servicio", "necesito servicio",
        }
        SERVICE_KEYWORDS_AHORA = {"taxi ahora", "taxiahora", "taxi ya"}
        SERVICE_KEYWORDS_DOM = {"domicilio", "un domicilio", "necesito domicilio", "pedir domicilio"}

        if t_clean_new in SERVICE_KEYWORDS_MENU or is_just_greeting(texto_usuario):
            sess.state = STATE_WAITING_TIPO_SERVICIO
            await send_whatsapp_interactive_buttons(
                sender_phone,
                "¡Hola de nuevo! 👋 ¿Qué tipo de servicio necesitas?",
                [
                    ("taxi_ahora", "Taxi Ahora"),
                    ("taxi_prog", "Taxi Programado"),
                    ("domicilio", "Domicilio")
                ]
            )
            return
        elif t_clean_new in SERVICE_KEYWORDS_AHORA:
            sess.tipo_servicio = "taxi ahora"
            sess.state = STATE_WAITING_ORIGIN
            await send_whatsapp_location_request(sender_phone, "¡Hola! ¿En qué parte te recogemos? Toca el botón de abajo para enviar tu ubicación, o escribe una calle, barrio o lugar.")
            return
        elif t_clean_new in SERVICE_KEYWORDS_DOM:
            sess.tipo_servicio = "domicilio"
            sess.state = STATE_WAITING_DOM_ORIGIN
            await send_whatsapp_location_request(sender_phone, "¡Hola! ¿En qué dirección debemos recoger el paquete o pedido? (Usa el botón para tu ubicación o escíbela)")
            return

    # NOTA: NO incluir STATE_WAITING_ORIGIN aquí. Si el usuario ya eligió tipo de
    # servicio y está en captura de origen, un saludo NO debe reenviar el menú —
    # se maneja dentro del estado waiting_origin re-pidiendo el origen.
    if is_just_greeting(texto_usuario) and sess.state in (STATE_NEW, STATE_WAITING_TIPO_SERVICIO):
        sess.state = STATE_WAITING_TIPO_SERVICIO
        await send_whatsapp_interactive_buttons(
            sender_phone,
            "¡Hola! Soy tu asistente de Tax Belalcázar 🚕. ¿Qué tipo de servicio necesitas hoy?",
            [
                ("taxi_ahora", "Taxi Ahora"),
                ("taxi_prog", "Taxi Programado"),
                ("domicilio", "Domicilio")
            ]
        )
        return

    # ── Keywords de servicio para sesiones nuevas (STATE_NEW) ──
    # Mismo mecanismo que el reinicio post-STATE_FINISHED, para primera interacción.
    if sess.state == STATE_NEW:
        _t_new = re.sub(r'[^\w\s]', '', texto_usuario.lower()).strip()
        _MENU_KW = {"taxi", "un taxi", "necesito taxi", "quiero taxi", "pedir taxi", "solicitar taxi", "servicio", "un servicio"}
        _AHORA_KW = {"taxi ahora", "taxiahora", "taxi ya"}
        _DOM_KW   = {"domicilio", "un domicilio", "necesito domicilio", "pedir domicilio"}

        if _t_new in _MENU_KW or is_just_greeting(texto_usuario):
            sess.state = STATE_WAITING_TIPO_SERVICIO
            await send_whatsapp_interactive_buttons(
                sender_phone,
                "¡Hola! Soy tu asistente de Tax Belalcázar 🚕. ¿Qué tipo de servicio necesitas hoy?",
                [("taxi_ahora", "Taxi Ahora"), ("taxi_prog", "Taxi Programado"), ("domicilio", "Domicilio")]
            )
            return
        elif _t_new in _AHORA_KW:
            sess.tipo_servicio = "taxi ahora"
            sess.state = STATE_WAITING_ORIGIN
            await send_whatsapp_location_request(sender_phone, "¡Hola! ¿En qué parte te recogemos? Toca el botón de abajo para enviar tu ubicación, o escribe una calle, barrio o lugar.")
            return
        elif _t_new in _DOM_KW:
            sess.tipo_servicio = "domicilio"
            sess.state = STATE_WAITING_DOM_ORIGIN
            await send_whatsapp_location_request(sender_phone, "¡Hola! ¿En qué dirección debemos recoger el paquete o pedido? (Usa el botón para tu ubicación o escíbela)")
            return
        else:
            sess.state = STATE_WAITING_ORIGIN

    # ── STATE: waiting_tipo_servicio ──
    if sess.state == STATE_WAITING_TIPO_SERVICIO:
        t_clean_srv = texto_usuario.lower()
        if t_clean_srv == "taxi programado":
            sess.tipo_servicio = "taxi programado"
            sess.state = STATE_WAITING_HORA_PROG
            sess.origen_text = None
            await send_whatsapp_message(sender_phone, "📅 *Taxi Programado*\n\n¿Para qué fecha y hora lo necesitas?\n\nEjemplo: mañana a las 7:00 AM")
            return
        elif t_clean_srv == "taxi ahora":
            sess.tipo_servicio = "taxi ahora"
            sess.state = STATE_WAITING_ORIGIN
            await send_whatsapp_location_request(sender_phone, "🚕 *Taxi Ahora*\n\n📍 ¿En qué parte te recogemos?\n\nComparte tu ubicación con el botón, o escribe la calle, barrio o lugar.")
            return
        elif t_clean_srv == "domicilio":
            sess.tipo_servicio = "domicilio"
            sess.state = STATE_WAITING_DOM_ORIGIN
            await send_whatsapp_location_request(sender_phone, "📦 *Domicilio*\n\n📍 ¿En qué dirección recogemos el paquete o pedido?\n\nComparte tu ubicación con el botón, o escríbela.")
            return
        else:
            # No seleccionó botón. Si escribió saludo/pregunta (no dirección), aún
            # NO eligió servicio → re-mostrar el menú. Solo si escribió una dirección
            # real continuamos a la captura de origen (taxi ahora implícito).
            if (is_just_greeting(texto_usuario) or is_conversational_query(texto_usuario)) and not _has_address_signal(texto_usuario):
                await send_whatsapp_interactive_buttons(
                    sender_phone,
                    "Para ayudarte necesito que elijas una opción. ¿Qué tipo de servicio necesitas?",
                    [("taxi_ahora", "Taxi Ahora"), ("taxi_prog", "Taxi Programado"), ("domicilio", "Domicilio")],
                )
                return
            sess.state = STATE_WAITING_ORIGIN

    # ── STATE: waiting_hora_prog ──
    if sess.state == STATE_WAITING_HORA_PROG:
        dt_info = await extract_datetime_with_llm(texto_usuario)
        
        if "error" in dt_info:
            await send_whatsapp_message(sender_phone, "⚠️ No entendí la fecha y la hora.\n\n¿Me las repites? Ejemplo: mañana a las 7:00 AM")
            return

        f_prog = dt_info.get("fecha_programada")
        h_prog = dt_info.get("hora_programada")
        if not dt_info or not f_prog or not h_prog:
            await send_whatsapp_message(sender_phone, "⚠️ No entendí muy bien la fecha y la hora.\n\n¿Me las podrías decir de nuevo? Ejemplo: mañana a las 7:00 AM")
            return

        sess.fecha_programada = f_prog
        sess.hora_programada = h_prog
        sess.fecha_hora_prog = f"{f_prog} {h_prog}"
        sess.state = STATE_WAITING_ORIGIN
        await send_whatsapp_location_request(sender_phone, f"📅 Anotado para el {f_prog} a las {h_prog}.\n\n📍 ¿En qué lugar de Popayán te recogemos?\n\nComparte tu ubicación con el botón o escribe tu dirección.")
        return

    # ── STATE: waiting_origin ──
    if sess.state == STATE_WAITING_ORIGIN:
        if t_clean in ["no", "no se", "ninguno"]:
            await send_whatsapp_location_request(sender_phone, "📍 Necesito tu punto de recogida.\n\nEscribe la dirección o el barrio, o comparte tu ubicación con el botón.")
            return

        if texto_usuario.startswith("Ubicación en mapa:"):
            sess.origen_text = texto_usuario
            await _finalizar_taxi(sender_phone, sess)
            return

        # Saludo / pregunta / charla → re-pedir origen, SIN reenviar el menú.
        # Guard con señal EXPLÍCITA de dirección (número/calle/barrio), no fuzzy:
        # evita que un falso positivo de looks_like_place deje pasar una pregunta.
        if (is_just_greeting(texto_usuario) or is_conversational_query(texto_usuario)) and not _has_address_signal(texto_usuario):
            await send_whatsapp_location_request(sender_phone, "📍 Para continuar necesito tu punto de recogida.\n\n¿En qué dirección, barrio o lugar te recogemos? (Usa el botón o escribe)")
            return

        origen_llm, hint = extract_pickup_address(texto_usuario)
        origen = (origen_llm or "").strip()

        if origen:
            normalized = normalize_address(origen)
            if normalized and len(normalized) > len(origen) * 0.5:
                origen = normalized

        # No se pudo extraer un origen válido → re-pedir el origen SIN reenviar el menú.
        if not origen or len(origen) < 2 or not looks_like_place(origen):
            await send_whatsapp_location_request(sender_phone, "⚠️ No logré identificar el lugar de recogida.\n\n📍 Escribe la dirección o el barrio, o comparte tu ubicación.")
            return

        sess.origen_text = origen

        is_street = bool(re.search(r'(?:calle|carrera|cl|cra|cr|kra|kr)\s*\d+', origen.lower()))
        if is_street:
            sess.state = STATE_CONFIRMING_ORIGIN
            await send_whatsapp_message(sender_phone, f"📍 Tu dirección de recogida es:\n{origen}\n\n¿Es correcta? Responde *SÍ* o *NO*.")
            return

        # Origen válido → crear el servicio de inmediato (sin pedir destino).
        await _finalizar_taxi(sender_phone, sess)
        return

    # ── STATE: confirming_origin ──
    if sess.state == STATE_CONFIRMING_ORIGIN:
        is_yes = _parse_si_no(texto_usuario)

        if is_yes is True:
            # Confirmado → crear el servicio de inmediato (sin pedir destino).
            await _finalizar_taxi(sender_phone, sess)
            return

        if is_yes is False:
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            await send_whatsapp_location_request(sender_phone, "📍 ¿En qué dirección te recogemos entonces?\n\n(Escríbela o comparte tu ubicación)")
            return

        # Saludo/pregunta/charla → re-preguntar la confirmación, sin tomarlo como dirección.
        if (is_just_greeting(texto_usuario) or is_conversational_query(texto_usuario)) and not _has_address_signal(texto_usuario):
            await send_whatsapp_message(sender_phone, f"📍 ¿Confirmas que te recogemos en:\n{sess.origen_text}?\n\nResponde *SÍ* o *NO*.")
            return

        # Si responde otra cosa, lo tomamos como corrección directa → crear el servicio.
        sess.origen_text = texto_usuario
        await _finalizar_taxi(sender_phone, sess)
        return

    # ── STATE: waiting_dom_origin ──
    if sess.state == STATE_WAITING_DOM_ORIGIN:
        if texto_usuario.startswith("Ubicación en mapa:"):
            sess.origen_text = texto_usuario
            sess.state = STATE_WAITING_DOM_DEST
            await send_whatsapp_location_request(sender_phone, _DOM_DEST_PROMPT)
            return

        # Saludo/pregunta/charla (no dirección) → re-pedir origen sin extraer.
        if (is_just_greeting(texto_usuario) or is_conversational_query(texto_usuario)) and not _has_address_signal(texto_usuario):
            await send_whatsapp_location_request(sender_phone, "📍 Para continuar necesito el punto de recogida del domicilio.\n\n¿En qué dirección lo recogemos? (Usa el botón o escribe)")
            return

        origen_llm, hint = extract_pickup_address(texto_usuario)
        origen = (origen_llm or "").strip()

        if origen:
            normalized = normalize_address(origen)
            if normalized and len(normalized) > len(origen) * 0.5:
                origen = normalized

        if not origen or len(origen) < 2 or not looks_like_place(origen):
            await send_whatsapp_location_request(sender_phone, "⚠️ No logré identificar la dirección de recogida.\n\n📍 Escríbela o comparte tu ubicación.")
            return

        sess.origen_text = origen
        sess.state = STATE_WAITING_DOM_DEST
        await send_whatsapp_location_request(sender_phone, _DOM_DEST_PROMPT)
        return

    # ── STATE: waiting_dom_dest (solo el domicilio lleva destino de entrega) ──
    if sess.state == STATE_WAITING_DOM_DEST:
        # Saludo/pregunta/charla (no dirección) → re-pedir destino sin extraer.
        if not texto_usuario.startswith("Ubicación en mapa:") and (is_just_greeting(texto_usuario) or is_conversational_query(texto_usuario)) and not _has_address_signal(texto_usuario):
            await send_whatsapp_location_request(sender_phone, "📦 ¿A qué dirección llevamos el domicilio?\n\nComparte la ubicación de entrega con el botón o escríbela.")
            return

        if texto_usuario.startswith("Ubicación en mapa:"):
            dest = texto_usuario
        else:
            dest_llm, hint = extract_destination_address(texto_usuario)
            dest = (dest_llm or "").strip()
            if dest:
                normalized = normalize_address(dest)
                if normalized and len(normalized) > len(dest) * 0.5:
                    dest = normalized

        if not dest or len(dest) < 2 or (not dest.startswith("Ubicación en mapa:") and not looks_like_place(dest)):
            await send_whatsapp_location_request(sender_phone, "⚠️ No logré identificar la dirección de entrega.\n\n📦 Escríbela o comparte la ubicación.")
            return

        sess.destino_text = dest
        sess.state = STATE_WAITING_DOM_OBS
        await send_whatsapp_message(sender_phone, _DOM_OBS_PROMPT)
        return

    # ── STATE: waiting_dom_obs ──
    if sess.state == STATE_WAITING_DOM_OBS:
        obs_clean = texto_usuario.strip().lower()
        if obs_clean in ("no", "ninguna", "ninguno", "nada", "no gracias", "omitir"):
            sess.observacion = None
        else:
            sess.observacion = texto_usuario.strip()

        ok, closing = await _create_wp_service(
            sender_phone,
            sess.origen_text or "",
            sess.destino_text or "",
            sess.tipo_servicio or "domicilio",
            sess.fecha_programada,
            sess.hora_programada,
            sess.observacion,
        )
        if ok:
            sess.state = STATE_FINISHED
        await send_whatsapp_message(sender_phone, closing)
        return