"""
gateway/whatsapp.py — Webhook integration for Meta WhatsApp Cloud API.
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
    _is_correction_request,
    normalize_address,
    normalize_colombian_address,
    _nominatim_reverse_geocode_async,
    extract_datetime_with_llm,
)
from core.geocoder_service import geocode, run_pipeline, handle_user_context
from core.geo_types import GeoSessionState, ResolutionStatus

logger = logging.getLogger("lyra.whatsapp")
whatsapp_router = APIRouter(prefix="/wh/whatsapp", tags=["whatsapp"])


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

STATE_NEW                 = "new"
STATE_WAITING_TIPO_SERVICIO = "waiting_tipo_servicio"
STATE_WAITING_HORA_PROG   = "waiting_hora_prog"
STATE_WAITING_ORIGIN      = "waiting_origin"
STATE_WAITING_GEO_CONTEXT = "waiting_geo_context"   # pipeline en CONTEXT_GATHERING
STATE_CONFIRMING_ORIGIN   = "confirming_origin"
STATE_WAITING_DEST_OR_SKIP = "waiting_dest_or_skip"
STATE_WAITING_DOM_ORIGIN  = "waiting_dom_origin"
STATE_WAITING_DOM_DEST    = "waiting_dom_dest"
STATE_WAITING_DOM_OBS     = "waiting_dom_obs"
STATE_FINISHED            = "finished"

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
        self.geo_origin: GeoSessionState = GeoSessionState()
        self.geo_dest:   GeoSessionState = GeoSessionState()

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

    async def _resolve_gps_text(lat: float, lng: float) -> str:
        """Convierte coordenadas GPS a nombre legible via Nominatim reverse."""
        name = await _nominatim_reverse_geocode_async(lat, lng)
        if name:
            return clean_map_location(name)
        return f"GPS ({lat:.5f},{lng:.5f})"

    map_match_o = re.search(r"Ubicación en mapa:\s*(-?\d+\.\d+),(-?\d+\.\d+)(?:\s*\|\s*(.*))?", origen)
    if map_match_o:
        olat_s, olng_s, explicit_name = map_match_o.groups()
        olat, olng = float(olat_s), float(olng_s)
        if explicit_name:
            origen = clean_map_location(explicit_name)
        else:
            origen = await _resolve_gps_text(olat, olng)
        g_o = (olat, olng, origen)
    else:
        origen = normalize_address(origen) or origen
        olat, olng = 0.0, 0.0

    dlat, dlng = 0.0, 0.0
    if destino:
        map_match_d = re.search(r"Ubicación en mapa:\s*(-?\d+\.\d+),(-?\d+\.\d+)(?:\s*\|\s*(.*))?", destino)
        if map_match_d:
            dlat_s, dlng_s, explicit_name = map_match_d.groups()
            dlat, dlng = float(dlat_s), float(dlng_s)
            if explicit_name:
                destino = clean_map_location(explicit_name)
            else:
                destino = await _resolve_gps_text(dlat, dlng)
        else:
            destino = normalize_address(destino) or destino
            dlat, dlng = 0.0, 0.0

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
            return True, f"¡Listo! Tu {tipo_servicio} ha quedado programado para: *{fecha_programada} a las {hora_programada}*. El conductor llegará en ese horario. Muchas gracias por preferirnos."
        else:
            return True, f"¡Listo! Ya te estamos buscando un {tipo_servicio}. En un momentico se comunica contigo el conductor. Muchas gracias por preferirnos."
    except Exception as e:
        logger.error(f"Backend POST WP error: {e}")
        return False, "Tuvimos un problemita técnico. Intenta de nuevo."


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

    if is_just_greeting(texto_usuario) and sess.state in (STATE_NEW, STATE_WAITING_ORIGIN, STATE_WAITING_TIPO_SERVICIO):
        sess.state = STATE_WAITING_TIPO_SERVICIO
        await send_whatsapp_interactive_buttons(
            sender_phone,
            "¡Hola! Soy Lyra, tu asistente de IntelliTaxi. ¿Qué tipo de servicio necesitas hoy?",
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
                "¡Hola! Soy Lyra, tu asistente de IntelliTaxi. ¿Qué tipo de servicio necesitas hoy?",
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
            await send_whatsapp_message(sender_phone, "¡Perfecto! Has elegido Taxi Programado. Dime para qué fecha y a qué hora lo necesitas (ej. mañana a las 7:00 AM).")
            return
        elif t_clean_srv == "taxi ahora":
            sess.tipo_servicio = "taxi ahora"
            sess.state = STATE_WAITING_ORIGIN
            await send_whatsapp_location_request(sender_phone, "¡Perfecto! Has elegido Taxi Ahora. ¿En qué parte te recogemos? Toca el botón de abajo para enviar tu ubicación, o escribe una calle, barrio o un lugar.")
            return
        elif t_clean_srv == "domicilio":
            sess.tipo_servicio = "domicilio"
            sess.state = STATE_WAITING_DOM_ORIGIN
            await send_whatsapp_location_request(sender_phone, "¡Perfecto! Has elegido Domicilio. ¿En qué dirección debemos recoger el paquete o pedido? (Usa el botón para enviar tu ubicación, o escríbela)")
            return
        else:
            # If they didn't push the button but wrote an address right away, we fall through.
            sess.state = STATE_WAITING_ORIGIN

    # ── STATE: waiting_hora_prog ──
    if sess.state == STATE_WAITING_HORA_PROG:
        dt_info = await extract_datetime_with_llm(texto_usuario)
        
        if "error" in dt_info:
            await send_whatsapp_message(sender_phone, f"DEBUG ERROR LLM: {dt_info['error']}")
            return
            
        f_prog = dt_info.get("fecha_programada")
        h_prog = dt_info.get("hora_programada")
        if not dt_info or not f_prog or not h_prog:
            await send_whatsapp_message(sender_phone, f"DEBUG FAILED DT_INFO: '{dt_info}'\nNo entendí muy bien la fecha y hora. ¿Me la podrías decir de nuevo?")
            return

        sess.fecha_programada = f_prog
        sess.hora_programada = h_prog
        sess.fecha_hora_prog = f"{f_prog} {h_prog}"
        sess.state = STATE_WAITING_ORIGIN
        await send_whatsapp_location_request(sender_phone, f"Anotado para el {f_prog} a las {h_prog}. Ahora, ¿en qué lugar de Popayán te recogemos? Envía la ubicación con el botón o escribe tu dirección.")
        return

    # ── STATE: waiting_origin ──
    if sess.state == STATE_WAITING_ORIGIN:
        if t_clean in ["no", "no se", "ninguno"]:
            await send_whatsapp_message(sender_phone, "Necesito saber de dónde te recogemos. Por favor escribe tu dirección, barrio o envía tu ubicación.")
            return

        if texto_usuario.startswith("Ubicación en mapa:"):
            origen = texto_usuario
            sess.origen_text = origen
            sess.geo_origin.reset()
            sess.state = STATE_WAITING_DEST_OR_SKIP

            map_match_o = re.search(r"Ubicación en mapa:\s*-?\d+\.\d+,-?\d+\.\d+(?:\s*\|\s*(.*))?", origen)
            loc_name = "la ubicación compartida"
            if map_match_o and map_match_o.group(1):
                loc_name = clean_map_location(map_match_o.group(1))

            msg = f"Listo, te recogemos en {loc_name}. ¿A dónde te diriges? Envía la ubicación con el botón, escríbela, o dinos NO si prefieres avisarle al conductor."
            await send_whatsapp_location_request(sender_phone, msg)
            return

        origen_llm, hint = extract_pickup_address(texto_usuario)
        origen = normalize_colombian_address((origen_llm or "").strip())

        sess.origen_text = origen

        if not origen or len(origen) < 2:
            msg = hint or "¿Me dices de nuevo dónde te recogemos? Puedes pulsar el botón abajo para enviar tu ubicación GPS o escribir el nombre."
            await send_whatsapp_location_request(sender_phone, msg)
            return

        # Intentar geocodificar para obtener barrio y verificar existencia
        geo_result = await run_pipeline(origen, attempt=1)

        if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
            barrio = geo_result.selected.neighborhood
            sess.origen_barrio = barrio
            sess.geo_origin.reset()
            sess.state = STATE_CONFIRMING_ORIGIN
            barrio_str = f", barrio *{barrio}*" if barrio else ""
            msg = f"Entendemos que tu dirección es: *{origen}*{barrio_str}. ¿Es correcta? Responde SÍ o NO."
            await send_whatsapp_message(sender_phone, msg)
            return

        if geo_result.status == ResolutionStatus.CONTEXT_GATHERING:
            sess.geo_origin.pending = geo_result
            sess.geo_origin.original_query = origen
            sess.geo_origin.attempt = 1
            sess.state = STATE_WAITING_GEO_CONTEXT
            geo_q = geo_result.disambiguation_question or "¿En qué barrio o referencia cercana queda?"
            await send_whatsapp_message(sender_phone, geo_q)
            return

        # Pipeline falló pero tenemos texto → confirmar igual y geocodificar al crear servicio
        is_street = bool(re.search(r'(?:calle|carrera|cl|cra|cr|kra|kr)\s*\.?\s*\d+', origen.lower()))
        if is_street:
            sess.state = STATE_CONFIRMING_ORIGIN
            msg = f"Entendemos que tu dirección es: *{origen}*. ¿Es correcta? Responde SÍ o NO."
            await send_whatsapp_message(sender_phone, msg)
            return

        sess.geo_origin.reset()
        sess.state = STATE_WAITING_DEST_OR_SKIP
        msg = f"Listo, te recogemos en {origen}. ¿A dónde te diriges? Envía tu ubicación abajo, escríbela o di NO si prefieres contarle al conductor."
        await send_whatsapp_location_request(sender_phone, msg)
        return

    # ── STATE: waiting_geo_context (pipeline pidió barrio/referencia) ──
    if sess.state == STATE_WAITING_GEO_CONTEXT:
        pending = sess.geo_origin.pending
        orig_q  = sess.geo_origin.original_query
        attempt = sess.geo_origin.attempt

        geo_result = await handle_user_context(
            user_text=texto_usuario,
            pending=pending,
            original_query=orig_q,
            attempt=attempt,
        )
        sess.geo_origin.attempt = geo_result.attempt

        if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
            barrio = geo_result.selected.neighborhood
            if barrio:
                sess.origen_barrio = barrio
            if geo_result.query and len(geo_result.query) > len(orig_q):
                sess.origen_text = geo_result.query
            sess.geo_origin.reset()
            sess.state = STATE_CONFIRMING_ORIGIN
            display = sess.origen_text or orig_q
            barrio_str = f", barrio *{barrio}*" if barrio else ""
            msg = f"Entendemos que tu dirección es: *{display}*{barrio_str}. ¿Es correcta? Responde SÍ o NO."
            await send_whatsapp_message(sender_phone, msg)
            return

        if geo_result.status == ResolutionStatus.CONTEXT_GATHERING:
            sess.geo_origin.pending = geo_result
            geo_q = geo_result.disambiguation_question or "¿Puedes darme más detalles de la ubicación?"
            await send_whatsapp_message(sender_phone, geo_q)
            return

        # FAILED — confirmar igual, geocodificar al crear servicio
        sess.geo_origin.reset()
        sess.state = STATE_CONFIRMING_ORIGIN
        display = sess.origen_text or orig_q
        msg = f"Entendemos que tu dirección es: *{display}*. ¿Es correcta? Responde SÍ o NO."
        await send_whatsapp_message(sender_phone, msg)
        return

    # ── STATE: confirming_origin ──
    if sess.state == STATE_CONFIRMING_ORIGIN:
        is_yes = _parse_si_no(texto_usuario)
        
        if is_yes is True:
            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = f"Perfecto. Te recogemos en {sess.origen_text}. ¿A dónde te diriges? Toca el botón para compartir ubicación, escríbela o di NO si prefieres contarle al conductor."
            await send_whatsapp_location_request(sender_phone, msg)
            return
            
        if is_yes is False:
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            msg = "¿En qué dirección te recogemos entonces? (Puedes enviarla escrita o compartir ubicación)"
            await send_whatsapp_message(sender_phone, msg)
            return

        # Si responde otra cosa, lo tomamos como corrección directa de la dirección
        sess.origen_text = texto_usuario
        sess.state = STATE_WAITING_DEST_OR_SKIP
        msg = f"Listo, te recogemos en {texto_usuario}. ¿A dónde te diriges? Toca el botón para compartir la ubicación, escríbela o di NO."
        await send_whatsapp_location_request(sender_phone, msg)
        return

    # ── STATE: waiting_dest_or_skip ──
    if sess.state == STATE_WAITING_DEST_OR_SKIP:
        if _is_correction_request(texto_usuario):
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            msg = "Sin problema, vamos a corregir la ubicación. ¿Dónde te recogemos? (Usa el botón abajo o escribe)"
            await send_whatsapp_location_request(sender_phone, msg)
            return

        is_no = _parse_si_no(texto_usuario)
        if is_no is False:
            ok, closing = await _create_wp_service(sender_phone, sess.origen_text or "", None, sess.tipo_servicio or "taxi ahora", sess.fecha_programada, sess.hora_programada)
            if ok:
                sess.state = STATE_FINISHED
            await send_whatsapp_message(sender_phone, closing)
            return

        if texto_usuario.startswith("Ubicación en mapa:"):
            dest = texto_usuario
        else:
            dest_llm, hint = extract_destination_address(texto_usuario)
            dest = (dest_llm or texto_usuario or "").strip()

            if dest:
                normalized = normalize_address(dest)
                if normalized and len(normalized) > len(dest) * 0.5:
                    dest = normalized

        sess.destino_text = dest

        if not dest or len(dest) < 2:
            msg = hint or "¿Me dices a dónde vas? Dime un barrio, calle o sitio, envía tu ubicación con el botón, o escribe NO."
            await send_whatsapp_location_request(sender_phone, msg)
            return

        ok, closing = await _create_wp_service(sender_phone, sess.origen_text or "", dest, sess.tipo_servicio or "taxi ahora", sess.fecha_programada, sess.hora_programada)
        if ok:
            sess.state = STATE_FINISHED
        await send_whatsapp_message(sender_phone, closing)
        return

    # ── STATE: waiting_dom_origin ──
    if sess.state == STATE_WAITING_DOM_ORIGIN:
        if texto_usuario.startswith("Ubicación en mapa:"):
            origen = texto_usuario
            sess.origen_text = origen
            sess.state = STATE_WAITING_DOM_DEST
            
            map_match_o = re.search(r"Ubicación en mapa:\s*-?\d+\.\d+,-?\d+\.\d+(?:\s*\|\s*(.*))?", origen)
            loc_name = "la ubicación compartida"
            if map_match_o and map_match_o.group(1):
                loc_name = clean_map_location(map_match_o.group(1))

            await send_whatsapp_location_request(sender_phone, f"Anotado, recogemos en {loc_name}. ¿A qué dirección debemos llevarlo? (Usa el botón abajo o escribe)")
            return

        origen_llm, hint = extract_pickup_address(texto_usuario)
        origen = (origen_llm or texto_usuario or "").strip()
        
        if origen:
            normalized = normalize_address(origen)
            if normalized and len(normalized) > len(origen) * 0.5:
                origen = normalized

        sess.origen_text = origen
        
        if not origen or len(origen) < 2:
            msg = hint or "¿Me dices nuevamente en dónde recogemos el domicilio? Usa el botón para enviar tu ubicación o escribe un lugar."
            await send_whatsapp_location_request(sender_phone, msg)
            return
            
        sess.state = STATE_WAITING_DOM_DEST
        await send_whatsapp_location_request(sender_phone, f"Anotado, recogemos en {origen}. ¿A qué dirección debemos llevarlo? (Usa el botón abajo o escribe)")
        return

    # ── STATE: waiting_dom_dest ──
    if sess.state == STATE_WAITING_DOM_DEST:
        if texto_usuario.startswith("Ubicación en mapa:"):
            dest = texto_usuario
        else:
            dest_llm, hint = extract_destination_address(texto_usuario)
            dest = (dest_llm or texto_usuario or "").strip()
            
            if dest:
                normalized = normalize_address(dest)
                if normalized and len(normalized) > len(dest) * 0.5:
                    dest = normalized

        sess.destino_text = dest
        
        if not dest or len(dest) < 2:
            msg = hint or "¿A qué dirección debemos llevar el domicilio? Envía tu ubicación con el botón o descríbela."
            await send_whatsapp_location_request(sender_phone, msg)
            return
            
        msg_dest = "la ubicación compartida"
        if dest.startswith("Ubicación en mapa:"):
            map_match_d = re.search(r"Ubicación en mapa:\s*-?\d+\.\d+,-?\d+\.\d+(?:\s*\|\s*(.*))?", dest)
            if map_match_d and map_match_d.group(1):
                msg_dest = clean_map_location(map_match_d.group(1))
        else:
            msg_dest = dest

        sess.state = STATE_WAITING_DOM_OBS
        await send_whatsapp_message(sender_phone, f"Listo, lo llevamos a {msg_dest}. ¿Tienes alguna observación? Por ejemplo, a quién debemos entregarlo, si hay que pagar algo al recibir, o alguna otra instrucción.")
        return

    # ── STATE: waiting_dom_obs ──
    if sess.state == STATE_WAITING_DOM_OBS:
        sess.observacion = texto_usuario.strip()
        
        ok, closing = await _create_wp_service(
            sender_phone, 
            sess.origen_text or "", 
            sess.destino_text or "", 
            sess.tipo_servicio or "domicilio", 
            sess.fecha_programada, 
            sess.hora_programada, 
            sess.observacion
        )
        if ok:
            sess.state = STATE_FINISHED
        await send_whatsapp_message(sender_phone, closing)
        return