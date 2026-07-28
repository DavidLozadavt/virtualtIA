"""
routers/whatsapp.py — Webhook integration for Meta WhatsApp Cloud API.
"""

import logging
import unicodedata

import httpx
from fastapi import APIRouter, Request, HTTPException, Response, BackgroundTasks

from core.config import settings
from orchestrator.context_builder import load_project_config
from orchestrator.tool_runner import run_agent_loop
from core.address_correction import correct_address
from core.address_utils import (
    extract_pickup_address,
    extract_destination_address,
    extract_address_llm,
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

    if not sender_phone:
        return {"status": "ignored"}

    # Nota de voz: el Telecom Manager puede reenviar el media_id o una URL
    # directa. Se transcribe y se procesa como si el usuario la hubiera escrito.
    if not message_content:
        media_id = body.get("media_id") or body.get("audio_id")
        media_url = body.get("media_url") or body.get("audio_url")
        if media_id or media_url:
            print(f"🎤 NOTA DE VOZ UNIVERSAL [{company_id}] de {sender_phone}")
            background_tasks.add_task(
                process_whatsapp_voice_note,
                sender_phone,
                media_id,
                media_url,
                body.get("mime_type"),
                company_id,
            )
            return {"status": "success"}
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

                elif msg_type in ("audio", "voice"):
                    # Nota de voz: se descarga y transcribe en background (la
                    # descarga a Meta no puede bloquear el ACK del webhook).
                    media = msg.get(msg_type) or {}
                    media_id = media.get("id")
                    msg_id = msg.get("id")
                    if msg_id and PROCESSED_MESSAGES.is_processed(msg_id):
                        print(f"♻️ NOTA DE VOZ DUPLICADA IGNORADA: {msg_id}")
                        continue
                    if media_id and sender_phone:
                        print(f"🎤 NOTA DE VOZ de {sender_phone} (media {media_id})")
                        background_tasks.add_task(
                            process_whatsapp_voice_note,
                            sender_phone,
                            media_id,
                            None,
                            media.get("mime_type"),
                            1,
                        )
                    continue

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


# Intención de cancelar/salir/reiniciar el flujo, aunque venga en una frase
# ("quiero cancelar el servicio", "mejor cancela", "cancelar servicio").
# Se evalúa sobre texto plegado (_fold): sin tildes ni puntuación, así "anúlalo"
# y "anulalo" son el mismo caso. El sufijo de pronombre enclítico es obligatorio
# contemplarlo: sin él, "cancélalo" y "anúlalo" —la forma más común— no casaban,
# porque el \b caía en medio de "cancela|lo".
_ENCLITIC = r'(?:me|te|lo|la|los|las|le|nos)?'
_CANCEL_RE = re.compile(
    r'\b(?:cancel(?:ar|a|o|e|en|emos|amos)?' + _ENCLITIC + r'|'
    r'anul(?:ar|a|o|e|en|emos|amos)?' + _ENCLITIC + r'|'
    r'reinici(?:ar|a|o|e|emos)?|empezar\s+de\s+nuevo|'
    r'olvid(?:ar|a|e|emos)?' + _ENCLITIC + r'|'
    r'dej(?:ar|a|e|emos)?' + _ENCLITIC + r'\s+as[ií]?)\b',
    re.IGNORECASE,
)
# Palabras sueltas de cierre (solo como mensaje corto, evita falsos positivos
# dentro de una dirección: "avenida los adioses" no debe cancelar).
_CANCEL_SINGLE = {"cancelar", "salir", "reiniciar", "adios", "adiós", "no más", "no mas"}

# Desistir SIN decir "cancelar": "ya no necesito el servicio", "ya no lo quiero",
# "ya conseguí otro carro", "ya me voy en otro". Es como habla la mayoría.
#
# Precision-first: "ya no" a secas NO alcanza —podría ser "ya no, mejor en la
# calle 5"—. Se exige un verbo de necesidad/uso, o un hecho que cierra el
# servicio (ya conseguí / ya me fui). Se compara sobre texto plegado (_fold),
# así funciona igual con las tildes que escribe el STT de las notas de voz.
_GIVE_UP_RE = re.compile(
    r'\bya\s+no\s+(?:\w+\s+){0,3}?'
    r'(?:necesito|necesita|necesitamos|nesecito|ocupo|preciso|requiero|'
    r'quiero|queremos|va|van|voy|vamos|sirve|hace\s+falta)\b'
    r'|\bno\s+(?:lo|la|los|las)\s+(?:necesito|nesecito|quiero|ocupo|voy\s+a\s+necesitar)\b'
    r'|\bno\s+(?:necesito|nesecito|quiero|ocupo)\s+(?:\w+\s+){0,2}?'
    r'(?:servicio|taxi|carro|domicilio|mensajero|conductor|vehiculo)\b'
    r'|\bya\s+(?:consegui|conseguimos|cogi|tome|tomamos|pedi|pedimos)\s+'
    r'(?:\w+\s+){0,2}?(?:otro|otra|uno|una)\b'
    r'|\bya\s+me\s+(?:voy|fui|vine|iba)\b'
    r'|\bya\s+no\s+(?:sera|va\s+a\s+ser|hay\s+necesidad)\b',
    re.IGNORECASE,
)


def is_cancel_request(text: str) -> bool:
    """True si el usuario pide cancelar el servicio, lo diga como lo diga.

    Cubre dos formas:
      - explícita: "cancelar", "anúlalo", "olvídalo"
      - desistir:  "ya no necesito el servicio", "ya conseguí otro"
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.lower() in _CANCEL_SINGLE:
        return True
    folded = _fold(raw)
    return bool(_CANCEL_RE.search(folded) or _GIVE_UP_RE.search(folded))


def _fold(text: str) -> str:
    """Texto plegado para comparar: minúsculas, sin tildes, sin puntuación.

    Imprescindible desde que entran notas de voz: el STT devuelve siempre la
    ortografía correcta ("buenos días", "cuánto"), mientras que los vocabularios
    de saludos/muletillas están escritos sin tilde. Sin plegar, un "hola buenos
    días" no se reconocía como saludo y terminaba en el extractor de direcciones
    respondiendo "no encontré esa dirección".
    """
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = re.sub(r'[^\w\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def is_conversational_query(text: str) -> bool:
    """
    True si el texto es una pregunta/charla conversacional y NO una dirección.
    Ej: 'buenas, el servicio cuándo llega', '¿cuánto vale?', 'ya viene?'.
    """
    if "?" in text or "¿" in text:
        return True
    t = _fold(text)
    if not t:
        return False
    return any(_fold(m) in t for m in _QUESTION_MARKERS)


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


# ✅ PRESENTACIÓN: display name de POI conocidos
def _display(text: Optional[str]) -> str:
    """Cómo se le muestra un lugar al usuario.

    Si el texto nombra un POI del catálogo (config/poi_catalog.json) se muestra
    su display name; en cualquier otro caso se muestra el texto tal cual. No
    toca la resolución ni la dirección que va al backend.
    """
    from core.poi_catalog import poi_display_name

    return poi_display_name(text) or (text or "")


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

    from core.poi_catalog import poi_display_name

    # Display name de POI: se calcula sobre lo que ESCRIBIÓ el usuario, antes de
    # resolver. Solo afecta el texto que se le muestra a él; la dirección oficial
    # (y por tanto el payload, las coordenadas y el despacho) no cambia.
    origen_poi = poi_display_name(origen)
    destino_poi = poi_display_name(destino) if destino else None

    # Resolución geográfica UNIFICADA: WhatsApp usa exactamente el mismo pipeline
    # que la llamada telefónica (core.geocoder_service). Una ubicación compartida
    # se convierte primero en dirección completa vía Reverse Geocoding, y las
    # coordenadas exactas se conservan como fuente de verdad.
    olat, olng, origen = await _resolve_wp_location(origen)

    # Solo el domicilio lleva destino (dirección de entrega). Taxi ahora/programado → destino=None.
    dlat, dlng = 0.0, 0.0
    if destino:
        dlat, dlng, destino = await _resolve_wp_location(destino)

    # Texto visible al usuario. Sin POI en el catálogo → la dirección tal cual.
    origen_display = origen_poi or origen
    destino_display = destino_poi or destino

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
                f"{origen_display}\n\n"
                "🚕 Un conductor te recogerá a esa hora.\n"
                "Gracias por preferirnos."
            )
        if tipo_servicio == "domicilio":
            return True, (
                "✅ ¡Domicilio registrado!\n\n"
                "📍 Recogida:\n"
                f"{origen_display}\n\n"
                "📦 Entrega:\n"
                f"{destino_display or 'Por confirmar'}\n\n"
                "🚕 Un mensajero se comunicará contigo en un momento.\n"
                "Gracias por preferirnos."
            )
        return True, (
            "✅ Ubicación recibida\n\n"
            "📍 Recogida:\n"
            f"{origen_display}\n\n"
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


_CANCEL_OK = "Listo, cancelé tu solicitud. ✅\n\nEscríbeme cuando necesites un taxi."
_CANCEL_NADA_QUE_CANCELAR = "No tienes ninguna solicitud activa. Escríbeme cuando necesites un taxi."
_CANCEL_CON_CONDUCTOR = (
    "Tu servicio ya fue tomado por un conductor, así que no puedo cancelarlo desde aquí. 🚕\n\n"
    "Por favor coordínalo directamente con él o con la central."
)
_CANCEL_FALLO = (
    "⚠️ No pude confirmar la cancelación con la central.\n\n"
    "Por favor comunícate con nosotros para asegurarnos de que quede cancelada."
)


async def _cancelar_servicio_backend(sender_phone: str, company_id: int) -> dict:
    """Cancela en el backend los servicios publicados de este teléfono.

    El backend es el dueño del servicio: cancelar solo en Lyra deja la solicitud
    publicada para conductores y operadores. Devuelve el resultado tal cual para
    que el mensaje al usuario refleje lo que realmente pasó.

    Retorna {"ok": bool, "cancelados": int, "con_conductor": int}.
    """
    url = f"{settings.INTELLITAXI_API_BASE}/taxi/solicitud-telefonica/cancelar"
    payload = {
        "telefono": sender_phone,
        "company_id": company_id,
        "motivo": "Servicio cancelado por el cliente desde WhatsApp",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code >= 400:
            logger.error("Cancelación backend falló: %s %s", resp.status_code, resp.text)
            return {"ok": False, "cancelados": 0, "con_conductor": 0}

        data = resp.json() or {}
        return {
            "ok": True,
            "cancelados": int(data.get("cancelados") or 0),
            "con_conductor": int(data.get("con_conductor") or 0),
        }
    except Exception as e:
        logger.error("Error cancelando servicio en backend: %s", e)
        return {"ok": False, "cancelados": 0, "con_conductor": 0}


def _mensaje_cancelacion(resultado: dict) -> str:
    """Mensaje honesto: nunca decir 'cancelado' si el backend no lo canceló."""
    if not resultado.get("ok"):
        return _CANCEL_FALLO
    if resultado.get("cancelados"):
        return _CANCEL_OK
    if resultado.get("con_conductor"):
        return _CANCEL_CON_CONDUCTOR
    return _CANCEL_NADA_QUE_CANCELAR


_VOICE_NOTE_UNCLEAR = (
    "🎤 No logré entender la nota de voz.\n\n"
    "¿Me la repites hablando un poquito más despacio, o me lo escribes?"
)


# ✅ PROCESAR NOTA DE VOZ (misma conversación, distinta forma de entrada)
async def process_whatsapp_voice_note(
    sender_phone: str,
    media_id: Optional[str] = None,
    media_url: Optional[str] = None,
    mime: Optional[str] = None,
    company_id: int = 1,
):
    """Descarga la nota de voz, la transcribe y la entrega al flujo de siempre.

    No hay pipeline nuevo: la transcripción usa el mismo motor STT que el resto
    del sistema (core.voice_engine) con los mismos filtros de las llamadas, y el
    texto resultante entra por process_whatsapp_message exactamente igual que un
    mensaje escrito. Solo cambia la forma de entrada.
    """
    from services.whatsapp_media import voice_note_to_text

    texto = await voice_note_to_text(
        media_id=media_id, media_url=media_url, mime=mime, company_id=company_id
    )

    if not texto:
        await send_whatsapp_message(sender_phone, _VOICE_NOTE_UNCLEAR)
        return

    print("🎤 NOTA DE VOZ TRANSCRITA:", texto)
    await process_whatsapp_message(sender_phone, texto, company_id)


# ✅ PROCESAR MENSAJE (SIN BASE DE DATOS)
async def process_whatsapp_message(sender_phone: str, message: str, company_id: int = 1):
    import re

    def is_just_greeting(text: str) -> bool:
        # _fold pliega tildes: el STT de las notas de voz escribe "días",
        # "buenísimo", y el vocabulario de abajo va sin tilde.
        words = _fold(text).split()
        greetings = {
            "hola", "holas", "buen", "buenos", "buenas", "dia", "dias", "tarde", "tardes", "noche", "noches", 
            "qhubo", "que", "mas", "saludos", "ola", "holi", "holis", "tal", "mija", "amiga", "mijo", "amigo",
            "tío", "tio", "tía", "tia", "parce", "pana", "ve", "oiga", "mira", "ey"
        }
        if not words: return False
        return all(w in greetings for w in words)

    def is_thanks(text: str) -> bool:
        """Detecta mensajes de agradecimiento."""
        t = _fold(text)
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

    def is_short_ack(text: str) -> bool:
        """Confirmaciones/muletillas cortas de cierre ('vale', 'ok', 'listo')."""
        t = _fold(text)
        acks = {
            "vale", "ok", "oka", "okey", "okay", "listo", "dale", "bueno",
            "buenisimo", "perfecto", "excelente", "genial", "de una", "va",
            "chevere", "chere", "de acuerdo", "entendido",
        }
        return t in acks

    # Cierre amable tras finalizar el servicio.
    CLOSING_RESPONSES = [
        "¡Con mucho gusto! 🙌 Fue un placer atenderte. Esperamos prestarte nuestro servicio muy pronto de nuevo. 🚕",
        "¡A la orden siempre! 😊 Gracias por preferirnos. Aquí estaremos cuando nos necesites otra vez. 🚕",
        "¡Con todo gusto! Esperamos volver a atenderte muy pronto. Que tengas un excelente día. 🚕",
    ]

    import hashlib
    _thanks_idx = int(hashlib.md5(sender_phone.encode()).hexdigest(), 16) % len(THANKS_RESPONSES)
    _closing_idx = int(hashlib.md5(sender_phone.encode()).hexdigest(), 16) % len(CLOSING_RESPONSES)

    texto_usuario = message.strip()
    sess = get_wp_session(sender_phone, company_id)

    pendiente, msg_pendiente = await _tiene_reactivacion_pendiente(sender_phone, company_id)
    if pendiente:
        await send_whatsapp_message(sender_phone, msg_pendiente or REACTIVACION_RECORDATORIO_DEFAULT)
        return

    # Clean text to detect cancellations
    t_clean = texto_usuario.lower()
    if is_cancel_request(texto_usuario):
        # Cancelar de verdad: limpiar la sesión de Lyra NO basta, el servicio ya
        # está creado en el backend y sigue publicado para conductores y
        # operadores hasta que este llamado lo cancele allá.
        resultado = await _cancelar_servicio_backend(sender_phone, company_id)
        reset_wp_session(sender_phone)
        await send_whatsapp_message(sender_phone, _mensaje_cancelacion(resultado))
        return

    # ── CIERRE AMABLE TRAS FINALIZAR EL SERVICIO ──
    # Solo en STATE_FINISHED: si agradece o confirma corto ('gracias', 'vale',
    # 'ok', 'listo'), enviamos una despedida cálida. Fuera de FINISHED un 'ok'
    # NO se toma como cierre (lo maneja el flujo del estado activo).
    if sess.state == STATE_FINISHED and (is_thanks(texto_usuario) or is_short_ack(texto_usuario)):
        await send_whatsapp_message(sender_phone, CLOSING_RESPONSES[_closing_idx])
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

        # Extracción unificada: mismo extractor + normalizador que la llamada.
        origen = (await extract_address_llm(texto_usuario, kind="pickup") or "").strip()

        # No se pudo extraer un origen válido → re-pedir el origen SIN reenviar el menú.
        if not origen or len(origen) < 2 or not looks_like_place(origen):
            await send_whatsapp_location_request(sender_phone, "⚠️ No logré identificar el lugar de recogida.\n\n📍 Escribe la dirección o el barrio, o comparte tu ubicación.")
            return

        sess.origen_text = origen

        is_street = bool(re.search(r'(?:calle|carrera|cl|cra|cr|kra|kr)\s*\d+', origen.lower()))
        if is_street:
            sess.state = STATE_CONFIRMING_ORIGIN
            await send_whatsapp_message(sender_phone, f"📍 Tu dirección de recogida es:\n{_display(origen)}\n\n¿Es correcta? Responde *SÍ* o *NO*.")
            return

        # Origen válido → crear el servicio de inmediato (sin pedir destino).
        await _finalizar_taxi(sender_phone, sess)
        return

    # ── STATE: confirming_origin ──
    if sess.state == STATE_CONFIRMING_ORIGIN:
        # ── Corrección PARCIAL ("no, es 3C-6") ──
        # El usuario no repite la dirección completa, solo el pedazo malo. Se
        # conserva la vía y se reemplaza la placa. Va ANTES de _parse_si_no
        # porque el turno empieza por "no" y si no se perdería la corrección.
        corregida = correct_address(sess.origen_text, texto_usuario)
        if corregida:
            sess.origen_text = corregida
            await send_whatsapp_message(
                sender_phone,
                f"📍 Corrijo, entonces es:\n{_display(corregida)}\n\n¿Así está bien? Responde *SÍ* o *NO*.",
            )
            return

        is_yes = _parse_si_no(texto_usuario)

        if is_yes is True:
            # Confirmado → crear el servicio de inmediato (sin pedir destino).
            await _finalizar_taxi(sender_phone, sess)
            return

        if is_yes is False:
            # ── Corrección COMPLETA en el mismo mensaje ──
            # "no, es en la calle 5 #12-20": el usuario ya dio la dirección
            # buena. Volver a preguntarla sería hacérsela repetir.
            if _has_address_signal(texto_usuario):
                nueva = (await extract_address_llm(texto_usuario, kind="pickup") or "").strip()
                if nueva and len(nueva) >= 2 and looks_like_place(nueva):
                    sess.origen_text = nueva
                    await send_whatsapp_message(
                        sender_phone,
                        f"📍 Corrijo, entonces es:\n{_display(nueva)}\n\n¿Así está bien? Responde *SÍ* o *NO*.",
                    )
                    return

            # "No" a secas → sí hay que pedir la dirección de nuevo.
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            await send_whatsapp_location_request(sender_phone, "📍 ¿En qué dirección te recogemos entonces?\n\n(Escríbela o comparte tu ubicación)")
            return

        # Saludo/pregunta/charla → re-preguntar la confirmación, sin tomarlo como dirección.
        if (is_just_greeting(texto_usuario) or is_conversational_query(texto_usuario)) and not _has_address_signal(texto_usuario):
            await send_whatsapp_message(sender_phone, f"📍 ¿Confirmas que te recogemos en:\n{_display(sess.origen_text)}?\n\nResponde *SÍ* o *NO*.")
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

        # Extracción unificada: mismo extractor + normalizador que la llamada.
        origen = (await extract_address_llm(texto_usuario, kind="pickup") or "").strip()

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
            # Extracción unificada: mismo extractor + normalizador que la llamada.
            dest = (await extract_address_llm(texto_usuario, kind="destination") or "").strip()

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