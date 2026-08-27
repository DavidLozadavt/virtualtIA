"""
tools/shared/utils.py — Funciones utilitarias centralizadas para todo Lyra AI.

Reemplaza las implementaciones duplicadas de:
  - _normalize / _normalize_text   (5 archivos)
  - _haversine                     (popayan_geodata.py)
  - _looks_like_iso_date, _looks_like_time_24h, _format_time_24h,
    _parse_date_candidate, _parse_time_candidate   (tool_runner.py)

Uso:
    from tools.shared.utils import normalize_text, haversine, parse_date_candidate
"""

import math
import re
import json
import logging
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

from core.logger import setup_logger

logger = setup_logger("lyra.utils")

# ═══════════════════════════════════════════════════════════════════════════════
# 1. NORMALIZACIÓN DE TEXTO
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_text(
    text: str,
    strip_punctuation: bool = False,
    condense_spaces: bool = True,
) -> str:
    """
    Normaliza texto eliminando tildes/diacríticos, convirtiendo a minúsculas,
    y opcionalmente quitando puntuación y condensando espacios múltiples.

    Consolidación de:
      - orchestrator/tool_runner.py  :: _normalize
      - orchestrator/intent_router.py :: _normalize
      - orchestrator/context_builder.py :: _normalize_text
      - tools/nexiservice.py :: _normalize
      - tools/popayan_geodata.py :: _normalize_text
      - gateway/twilio_voice.py :: _normalize_text
    """
    if not text:
        return ""

    nfkd = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

    if strip_punctuation:
        text = re.sub(r"[^\w\s]", "", text)

    if condense_spaces:
        text = re.sub(r"\s+", " ", text)

    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CÁLCULOS GEOESPACIALES
# ═══════════════════════════════════════════════════════════════════════════════

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distancia en km entre dos coordenadas GPS (fórmula de Haversine).

    Consolidación de: tools/popayan_geodata.py :: _haversine
    """
    R = 6371.0  # Radio de la Tierra en km
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PARSING DE FECHAS Y HORAS (español)
# ═══════════════════════════════════════════════════════════════════════════════

WEEKDAY_MAP: dict[str, int] = {
    "lunes": 0, "martes": 1, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6,
}

MONTH_MAP: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# ═══════════════════════════════════════════════════════════════════════════════
# 4. RECONOCIMIENTO DE INTENCIONES Y CATEGORÍAS
# ═══════════════════════════════════════════════════════════════════════════════

CATEGORY_KEYWORDS = {
    "barberia": ["barber", "peluquer", "corte", "estetic", "barba", "peluqueria", "barberias", "peluquerias", "salon de belleza"],
    "hotel": ["hotel", "hosped", "alojam", "cabaña", "motel", "dormir", "habitacion", "hoteles", "hospedajes"],
    "restaurante": ["restaur", "comida", "pizz", "hamburgues", "gastronomia", "rico", "restaurantes"],
    "cancha": ["cancha", "futbol", "deporte", "poliedro", "sintetica", "canchas"],
    "medico": ["medic", "clinic", "salud", "doctor", "odont", "dental", "dentista", "hospital", "medicos", "clinicas"],
    "taller": ["taller", "mecanico", "carro", "moto", "reparacion", "llanta", "talleres"],
    "gym": ["gym", "gimnasio", "entrenar", "pesas", "spinning", "gimnasios"],
}

HELP_KEYWORDS = [
    "ayuda", "como funciona", "que es", "modulos", "plataforma", "vender", 
    "informacion", "acerca de", "detalles", "info", "nomina", "ventas", 
    "pago", "personal", "inventario", "compras", "productos", "catalogo", 
    "clientes", "citas", "servicios", "hacer", "sirve", "puedes hacer", "puede hacer", 
    "capacidades", "funcionar", "funciona", "app", "aplicacion", "ofrece", "proposito", "objetivo",
    "web", "pagina web", "pagina", "sitio", "sitio web", "agendar", "cita", "reserva", "turno",
    "crear cuenta", "registrarse", "registro", "iniciar sesion", "perfil", "mi cuenta", "mis citas",
    "donde queda", "como llego", "que hacen", "quienes son", "dueño", "contacto", "telefono", "celular",
    "precio", "costo", "valor", "gratis", "pago online", "metodos de pago", "efectivo",
    "horario", "abierto", "cerrado", "domingo", "festivo", "mañana", "tarde", "noche"
]
GREETING_KEYWORDS = ["hola", "holas", "buen", "dia", "tarde", "noche", "como estas", "que tal", "saludos", "buenos dias", "buenas tardes", "buenas noches", "holi", "buenas", "que mas"]
FAREWELL_KEYWORDS = ["chao", "adios", "hasta luego", "bye", "nos vemos", "quedamos asi"]
IDENTITY_KEYWORDS = ["quien eres", "quien sos", "quien eres tu", "como te llamas", "quien habla", "tu nombre", "con quien hablo"]
CAPABILITIES_KEYWORDS = [
    "que puedes hacer", "que sabes hacer", "que funciones tienes", "que haces", "cual es tu funcion", 
    "ayudame", "ayuda", "puedes ayudarme", "como me ayudas", "en que me puedes ayudar", 
    "que servicios ofreces", "que servicios tienes", "que habilidades tienes", "para que sirves"
]

# Intenciones de control del mapa
MAP_SHOW_KEYWORDS = ["mapa", "ver mapa", "ver el mapa", "muestra mapa", "mostrar mapa", "abrir mapa", "expandir mapa", "enseñame el mapa", "muestrame el mapa", "volver al mapa", "regresar al mapa", "volver", "regresar", "regresame"]
MAP_FIT_KEYWORDS = ["todos en el mapa", "mostrar todos", "ver todos", "todos los negocios", "todas las empresas"]
MAP_LOCATE_KEYWORDS = ["donde estoy", "mi ubicacion", "mi posicion", "donde me encuentro", "ubicacion actual", "ubicarme", "localizarme"]

# Intenciones GPS
GPS_GRANTED_KEYWORDS = [
    "acepto el permiso", "acepto gps", "autorizo gps", "autorizo mi ubicacion",
    "si usar gps", "si usar mi ubicacion", "permitir ubicacion", "permitir gps",
    "gps activado", "ya tengo gps", "ya active gps", "activa gps", "activa mi gps",
    "compartir mi ubicacion", "usar mi ubicacion", "ok gps"
]
GPS_DENIED_KEYWORDS = [
    "niego el permiso", "no autorizo gps", "no dar gps", "sin gps acceso",
    "no compartir ubicacion", "no usar gps", "deniego gps", "no permitir gps",
    "prefiero elegir ciudad", "quiero elegir ciudad", "ciudad manual", "ingresar ciudad",
    "elijo ciudad", "elegir ciudad", "ciudad a mano"
]
GPS_NO_SIGNAL_KEYWORDS = [
    "sin senal gps", "gps sin senal", "no hay senal", "gps no funciona",
    "fallo el gps", "error de gps", "gps fallo", "no se encuentra mi ubicacion",
    "no detecta gps", "no pudo obtener ubicacion", "perdio la senal"
]
ZOOM_IN_KEYWORDS = ["acercar", "acercame", "mas zoom", "zoom in", "mas cerca", "ampliar", "aumentar zoom", "hace zoom", "haz zoom", "acerca", "hacer zoom", "zoom"]
ZOOM_OUT_KEYWORDS = ["alejar", "alejame", "menos zoom", "zoom out", "mas lejos", "reducir", "reducir zoom", "aleja", "alejar mapa", "quitar zoom", "mermar", "merma"]
FLY_TO_BIZ_KEYWORDS = ["ver en el mapa", "muestrame en el mapa", "muestra en el mapa", "ubicar en el mapa", "donde esta", "donde queda", "ubicacion de", "lleva al mapa", "en el mapa", "ver en mapa", "como llegar", "como llego"]
REVIEWS_KEYWORDS = ["reseña", "reseñas", "resena", "resenas", "criticas", "opiniones", "comentarios", "calificacion", "calificaciones", "puntaje", "que dicen", "que cree", "reputacion", "valoracion", "valoraciones", "estrellas", "que tal", "como es", "que tal es", "como esta", "opina", "opinan", "opinas", "parece", "piensan", "piensa", "crees", "creen", "creer"]
AVAILABILITY_KEYWORDS = ["disponibilidad", "espacio", "hueco", "libre", "ocupado", "cuando puedo ir", "tiene cupo", "esta lleno", "horario"]
MISSION_KEYWORDS = ["mision", "vision", "historia", "quienes son", "sobre ellos", "acerca de", "historia de"]
SERVICES_KEYWORDS = ["servicio", "servicios", "servico", "servicos", "serviocio", "serviocios", "precios", "precio", "catalogo", "cuanto cobran", "que ofrecen", "que venden", "ofrece", "ofrecen", "tiene", "tienen", "tienes", "que ofrece", "que tiene"]
WEB_KEYWORDS = ["web", "sitio web", "pagina web", "pagina", "website", "facebook", "instagram", "tiktok", "redes", "sociales", "url", "link", "enlace"]
COMPARE_KEYWORDS = ["compara", "comparar", "diferencia", "diferencias", "cual es mejor", "cual conviene", "versus", "vs", "comparativa", "ayudame a decidir", "cual elijo", "comparar opciones"]
RECOMMEND_KEYWORDS = ["recomienda", "recomendacion", "recomendaciones", "que hay de bueno", "mejores", "mejor", "mas destacados", "top", "ranking", "populares", "satisfaccion", "gustos", "preferencias", "sugiereme", "sugerencia"]
INFO_KEYWORDS = ["quien es", "quienes son", "quien atiende", "que hace", "cuentame de", "sobre", "quien es el profesional", "perfil de", "biografia de", "que ofrece", "que es"]

# --- ADMIN NAVIGATION TARGETS ---
ADMIN_NAV_TARGETS = {
    "/punto-venta": ["punto de venta", "pos", "caja", "vender", "facturar", "venta", "ventas"],
    "/configuracion/gestion-productos": ["producto", "productos", "inventario", "stock", "catalogo", "servicios"],
    "/gestion-personal": ["personal", "empleado", "empleados", "trabajador", "plantilla", "nomina", "equipo"],
    "/configuracion/config-pagos/medios-pago": ["medios de pago", "metodo de pago", "pasarela", "wompi", "pagos"],
    "/gestion-agendamientos/agenda": ["agenda", "calendario", "cita", "citas", "reserva", "reservas", "agendamientos"],
    "/empresa/configuracion-empresa": [
        "configuracion de empresa", "configuracion empresa", "perfil de empresa",
        "perfil de mi empresa", "mi empresa", "datos de la empresa", "mi negocio",
        "banners", "logo", "redes sociales de mi empresa",
    ],
}

#: Cómo se llama cada destino cuando Lyra lo anuncia. Antes se decía la primera
#: palabra clave de la lista —"Te llevo a Producto"—, que es el término con el
#: que se BUSCA el destino, no su nombre.
ADMIN_NAV_LABELS = {
    "/punto-venta": "Punto de venta",
    "/configuracion/gestion-productos": "Productos e inventario",
    "/gestion-personal": "Gestión de personal",
    "/configuracion/config-pagos/medios-pago": "Medios de pago",
    "/gestion-agendamientos/agenda": "tu agenda",
    "/empresa/configuracion-empresa": "la configuración de tu empresa",
}

def is_generic_query(text: str | None) -> bool:
    """Determina si una consulta es genérica o si menciona un nombre específico."""
    if not text:
        return True
    
    text_norm = normalize_text(text)
    generic_keywords = [
        "este", "ese", "aqui", "alli", "negocio", "local", "tienda", 
        "ella", "ello", "esta", "compañia", "empresa", "lugar", "sitio", "dia", "sol", "mar", "paz"
    ]
    
    words = text_norm.split()
    if len(words) <= 3:
        for kw in generic_keywords:
            if re.search(rf"\b{kw}\b", text_norm):
                return True
                
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 5. EXTRACCIÓN DE CONTEXTO DESDE HISTORIAL
# ═══════════════════════════════════════════════════════════════════════════════

def recover_suggested_city_from_history(messages: list[dict]) -> str | None:
    """Recupera la ciudad sugerida en el último mensaje de la herramienta o del asistente."""
    for m in reversed(messages):
        if m.get("role") == "tool":
            try:
                content = m.get("content", "{}")
                if isinstance(content, str):
                    content = json.loads(content)
                city = content.get("_suggested_city") or content.get("suggested_next_city")
                if city:
                    return city
            except:
                continue
    
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content") or ""
            if isinstance(content, str):
                tag_match = re.search(r"\[CITY:([^\]]+)\]", content)
                if tag_match:
                    return tag_match.group(1).strip()
                match = re.search(r"(?:opciones|negocios|resultados)\s+en\s+\*?\*?([a-zA-Z\u00e1\u00e9\u00ed\u00f3\u00fa\u00c1\u00c9\u00cd\u00d3\u00da\u00f1\u00d1 ]+?)\*?\*?\s*(?:\(|\?|\[)", content, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
            break
    return None


def recover_last_search_args_from_history(messages: list[dict]) -> dict:
    """Recupera tanto la ciudad como la categoría de la última búsqueda."""
    for m in reversed(messages):
        if m.get("role") == "tool":
            try:
                content = m.get("content", "{}")
                if isinstance(content, str):
                    content = json.loads(content)
                if "category" in content:
                    suggested_biz_id = None
                    suggested_biz_name = None
                    biz_list = content.get("suggested_businesses", [])
                    if biz_list and len(biz_list) > 0:
                        suggested_biz_id = biz_list[0].get("id")
                        suggested_biz_name = biz_list[0].get("name")

                    return {
                        "city": content.get("suggested_next_city") or content.get("city"),
                        "category": content.get("category"),
                        "suggested_biz_id": suggested_biz_id,
                        "suggested_biz_name": suggested_biz_name,
                        "biz_count": len(biz_list) if biz_list else 0
                    }
            except:
                continue
    
    suggested_city = recover_suggested_city_from_history(messages)
    last_category = ""
    if suggested_city:
        for m in reversed(messages):
            if m.get("role") == "assistant":
                content = m.get("content") or ""
                cat_match = re.search(r"No encontré?\s+\*\*([^*]+)\*\*", content, re.IGNORECASE)
                if cat_match:
                    raw_cat = cat_match.group(1).strip().lower()
                    resolved = None
                    for cat, kws in CATEGORY_KEYWORDS.items():
                        if any(kw in raw_cat for kw in kws):
                            resolved = cat
                            break
                    last_category = resolved or raw_cat
                break
    if suggested_city:
        return {"city": suggested_city, "category": last_category, "suggested_biz_id": None, "biz_count": 0}
        
    return {}


def recover_last_businesses_from_history(messages: list[dict]) -> list[dict]:
    """Recupera la lista de negocios mencionados en el historial."""
    for m in reversed(messages):
        if m.get("role") == "tool":
            try:
                content = json.loads(m.get("content") or "{}")
                biz_list = content.get("businesses") or content.get("results") or content.get("suggested_businesses")
                if biz_list and isinstance(biz_list, list):
                    return [{"id": str(b.get("id")), "name": b.get("name") or b.get("razonSocial")} for b in biz_list]
            except:
                continue
    
    biz_list = []
    messages_scanned = 0
    user_messages_seen = 0
    max_messages = 12
    
    for m in reversed(messages):
        messages_scanned += 1
        if messages_scanned > max_messages: break
        
        role = m.get("role")
        if role == "user":
            user_messages_seen += 1
            if len(biz_list) >= 2 and user_messages_seen >= 3: break
            continue
            
        if role == "assistant":
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join([block.get("text", "") for block in content if block.get("type") == "text"])
            
            if not isinstance(content, str): continue
            
            for _match in re.finditer(r'\*\*(.+?)\*\*', content):
                _biz_name = _match.group(1).strip()
                _start_idx = content.find(f"**{_biz_name}**")
                if _start_idx == -1: continue
                _lookahead = content[_start_idx:_start_idx+1000]
                _next_bold = content.find("**", _start_idx + len(_biz_name) + 4)
                if _next_bold != -1 and _next_bold < (_start_idx + 1000):
                    _lookahead = content[_start_idx:_next_bold]

                _biz_id_match = re.search(r'\[(?:BIZ|ID|TAG):(\d+)\]', _lookahead)
                if _biz_id_match:
                    biz_id = str(_biz_id_match.group(1))
                    if not any(b["id"] == biz_id for b in biz_list):
                        biz_list.append({"id": biz_id, "name": _biz_name})
    return biz_list


def find_anchored_id_in_messages(messages: list[dict]) -> int | None:
    """Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible."""
    for msg in reversed(messages):
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join([block.get("text", "") for block in content if block.get("type") == "text"])
        if not content or not isinstance(content, str):
            continue

        matches = re.findall(r"\[(?:ID|BIZ|TAG)[:\s]+(\d+)\]", content, re.IGNORECASE)
        link_matches = re.findall(r"/empresa/(\d+)", content)
        all_matches = matches + link_matches
        if all_matches:
            try:
                return int(all_matches[-1])
            except ValueError:
                continue
    return None


def recover_last_appointment_entities(messages: list[dict]) -> dict:
    """Busca menciones de servicios, profesionales o negocios en contextos de agendamiento."""
    entities = {"service_name": None, "professional_name": None, "business_name": None}
    for msg in reversed(messages):
        content = (msg.get("content") or "").lower()
        if not isinstance(content, str): continue
        
        if msg.get("role") == "assistant":
            srv_tag = re.search(r"\[SERVICIO:([^\]]+)\]", content, re.IGNORECASE)
            if srv_tag and not entities["service_name"]:
                entities["service_name"] = srv_tag.group(1).strip()
            
            if any(kw in content for kw in ["agendar", "disponibles", "cita"]):
                biz_match = re.search(r"(?:en|al|el)\s+(?!servicio\s+de)([a-zA-Z0-9áéíóúÁÉÍÓÚñÑ\s]{2,})", content, re.IGNORECASE)
                if biz_match and not entities["business_name"]:
                    entities["business_name"] = biz_match.group(1).strip()

                srv_match = re.search(r"para \*\*([^*]+)\*\*", content)
                if srv_match and not entities["service_name"]:
                    entities["service_name"] = srv_match.group(1).strip()
                
                prof_match = re.search(r"con \*\*([^*]+)\*\*", content)
                if prof_match and not entities["professional_name"]:
                    entities["professional_name"] = prof_match.group(1).strip()
                
        if msg.get("role") == "user":
            if " en " in content and not entities["business_name"]:
                biz_part = content.split(" en ")[-1].split(" para ")[0].split(" con ")[0].strip()
                if len(biz_part) >= 2:
                    entities["business_name"] = biz_part
            if " con " in content and not entities["professional_name"]:
                prof_part = content.split(" con ")[-1].split(" para ")[0].split(" en ")[0].strip()
                if len(prof_part.split()) >= 2:
                    entities["professional_name"] = prof_part
            if " para " in content and not entities["service_name"]:
                entities["service_name"] = content.split(" para ")[-1].split(" con ")[0].split(" en ")[0].strip()
                
        if entities["service_name"] and entities["professional_name"] and entities["business_name"]:
            break
    return entities


def recover_appointment_details_from_history(messages: list[dict], target_biz_id: int = None) -> dict | None:
    """Recupera los detalles de una reserva pendiente de confirmación."""
    for msg in reversed(messages):
        role = msg.get("role")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join([block.get("text", "") for block in content if block.get("type") == "text"])
        if not content or not isinstance(content, str):
            continue

        if role == "assistant":
            if "CONFIRMACI" in content and "NECESARIA" in content:
                service_match = re.search(r"Reserva:\s*\*?\*?([^*\n\]]+)\*?\*?", content)
                time_match = (
                    re.search(r"solicitada[:\s*]+\*?\*?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", content, re.IGNORECASE) or
                    re.search(r"a\s+las\s+\*?\*?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\*?\*?", content, re.IGNORECASE) or
                    re.search(r"a\s+las\s+\*?\*?(\d{1,2})\s+de\s+la\s+(mañana|tarde|noche)", content, re.IGNORECASE) or
                    re.search(r"\bHora:\s*\*?\*?(\d{1,2}(?::\d{2})?)\b", content, re.IGNORECASE)
                )
                biz_id_match = re.search(r'\[(?:BIZ|ID|TAG):(\d+)\]', content)
                biz_id = int(biz_id_match.group(1)) if biz_id_match else None
                if target_biz_id and biz_id and biz_id != target_biz_id:
                    continue

                _time_val = None
                if time_match:
                    _raw = time_match.group(1).strip()
                    if ":" in _raw:
                        h_part, m_part = _raw.split(":", 1)
                        m_part = re.sub(r'[^0-9]', '', m_part) or "00"
                        _time_val = f"{int(h_part):02d}:{m_part[:2]}"
                    elif re.match(r'^\d{1,2}$', _raw):
                        _time_val = f"{int(_raw):02d}:00"
                    else:
                        _time_val = _raw

                if service_match and _time_val:
                    _srv_clean = service_match.group(1).strip().rstrip('*').strip()
                    _srv_clean = re.sub(r'\s+a\s+las.*$', '', _srv_clean, flags=re.IGNORECASE).strip()
                    return {
                        "service_name": _srv_clean,
                        "time": _time_val,
                        "date": "today" if "hoy" in content.lower() else ("tomorrow" if "mañana" in content.lower() else None),
                        "business_id": biz_id
                    }
            
            elif "agendar" in content.lower() and "[SERVICIO:" in content:
                srv_match = re.search(r"\[SERVICIO[:\s]+([^\]]+)\]", content, re.IGNORECASE)
                biz_match = re.search(r"\[BIZ[:\s]+(\d+)\]", content, re.IGNORECASE)
                if srv_match and biz_match:
                    return {
                        "service_name": srv_match.group(1).strip(),
                        "time": None,
                        "date": None,
                        "business_id": int(biz_match.group(1))
                    }

            elif any(p in content.lower() for p in ["a qué hora", "qué hora te", "disponibilidad"]):
                biz_id_match = re.search(r'\[(?:BIZ|ID|TAG):(\d+)\]', content)
                biz_id = int(biz_id_match.group(1)) if biz_id_match else None
                if target_biz_id and biz_id and biz_id != target_biz_id:
                    continue

                _srv_anchor_match = re.search(r'\[SERVICIO:([^\]]+)\]', content, re.IGNORECASE)
                if biz_id and _srv_anchor_match:
                    return {
                        "service_name": _srv_anchor_match.group(1).strip(),
                        "time": None, "date": None, "business_id": biz_id
                    }
                if biz_id:
                    _srv_from_user = None
                    _srv_re = re.compile(r"(?:agendar|para|de|servicio)\s+(?:el|la|un|una)?\s*([a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\s]{3,35}?)", re.IGNORECASE)
                    for _um in reversed(messages):
                        if _um.get("role") == "user":
                            _srv_m = _srv_re.search((_um.get("content") or "").lower())
                            if _srv_m:
                                _cand = _srv_m.group(1).strip()
                                if len(_cand) >= 3 and not any(b in _cand for b in ["hoy", "manana", "tarde"]):
                                    _srv_from_user = _cand
                                    break
                    return {"service_name": _srv_from_user, "time": None, "date": None, "business_id": biz_id}
    return None


def extract_session_user_id(messages: list[dict]) -> str | None:
    """Lee el ID de usuario que el context builder incrusta en el system prompt."""
    try:
        content = messages[0].get("content") or ""
        match = re.search(r"ID del usuario autenticado:\s*(.+)", content)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return None


def extract_session_today(messages: list[dict]) -> date:
    """Extrae la fecha base de la sesión."""
    try:
        content = messages[0].get("content") or ""
        match = re.search(r"Fecha de hoy:\s*(\d{4}-\d{2}-\d{2})", content)
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except Exception:
        pass
    return datetime.now().date()


def get_recent_user_messages(messages: list[dict], limit: int = 3) -> list[str]:
    """Toma los ultimos mensajes del usuario."""
    recent: list[str] = []
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = (msg.get("content") or "").strip()
            if content: recent.append(content)
            if len(recent) >= limit: break
    return recent


def extract_tastes_from_history(messages: list[dict]) -> str:
    """Analiza el historial para extraer categorías de interés recurrentes."""
    history_text = " ".join([m.get("content", "") for m in messages if m.get("role") == "user"]).lower()
    tastes = []
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in history_text for kw in kws):
            tastes.append(cat)
    return tastes[0] if tastes else ""


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LÓGICA DE AGENDAMIENTO Y PERSONALIDAD
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_schedule_datetime(messages: list[dict], tool_args: dict) -> tuple[str, str]:
    """Normaliza fecha/hora preferidas desde args del LLM o el historial reciente."""
    base_date = extract_session_today(messages)
    recent_user_messages = get_recent_user_messages(messages)
    candidates: list[str] = []

    for key in ("preferred_date", "preferred_time", "date", "time", "fecha", "hora"):
        value = tool_args.get(key)
        if value: candidates.append(str(value))

    candidates.extend(recent_user_messages)
    if len(recent_user_messages) > 1:
        candidates.append(" ".join(reversed(recent_user_messages)))

    preferred_date = ""
    preferred_time = ""

    for candidate in candidates:
        if not preferred_date:
            preferred_date = parse_date_candidate(candidate, base_date) or ""
        if not preferred_time:
            preferred_time = parse_time_candidate(candidate) or ""
        if preferred_date and preferred_time:
            break

    raw_date = str(tool_args.get("preferred_date") or "").strip()
    raw_time = str(tool_args.get("preferred_time") or "").strip()

    if not preferred_date and looks_like_iso_date(raw_date):
        preferred_date = raw_date
    if not preferred_time and looks_like_time_24h(raw_time):
        preferred_time = raw_time

    return preferred_date, preferred_time


def build_schedule_clarification(property_id: str, preferred_date: str, preferred_time: str) -> str:
    if not str(property_id or "").strip():
        return "Necesito saber cuál propiedad quieres visitar antes de agendarla."
    if not preferred_date and not preferred_time:
        return "Para agendar la visita necesito la fecha y la hora que prefieres."
    if not preferred_date:
        return "Para agendar la visita necesito la fecha que prefieres."
    return "Para agendar la visita necesito la hora que prefieres."


def match_property_id_in_reply(reply: str, properties: list[dict]) -> int | None:
    """Intenta identificar qué propiedad mencionó la IA en su respuesta."""
    reply_norm = normalize_text(reply)
    match = re.search(r"\[ID:\s*(\d+)\]", reply, re.IGNORECASE)
    if match: return int(match.group(1))

    for prop in properties:
        title_norm = normalize_text(prop.get("title") or "")
        if title_norm and title_norm in reply_norm:
            return prop.get("id")

    reply_digits = reply_norm.replace(".", "").replace(",", "")
    for prop in properties:
        price_str = str(int(float(prop.get("price") or 0))) if prop.get("price") else ""
        if price_str and price_str in reply_digits:
            return prop.get("id")

    if len(properties) == 1:
        return properties[0].get("id")

    return None


def inject_ids_into_titles(properties: list[dict]) -> list[dict]:
    """Inyecta el ancla [ID: X] en el título para el siguiente turno."""
    for prop in properties:
        prop_id = prop.get("id")
        title = prop.get("title") or ""
        if prop_id and f"[ID: {prop_id}]" not in title:
            prop["title"] = f"{title} [ID: {prop_id}]"
    return properties


def resolve_personality(project_config: dict) -> str:
    """Resuelve el nombre de la personalidad activa desde project_config."""
    name = (project_config.get("assistant_name") or "Lyra").lower()
    return name if name in ("lyra", "nexo") else "lyra"


def looks_like_iso_date(value: str) -> bool:
    """Verifica si un string tiene formato ISO (YYYY-MM-DD)."""
    try:
        from datetime import datetime as _dt
        _dt.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def looks_like_time_24h(value: str) -> bool:
    """Verifica si un string tiene formato de hora 24h (HH:MM)."""
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value))


def format_time_24h(
    hour_raw: str,
    minute_raw: str | None = None,
    meridiem: str | None = None,
) -> str | None:
    """Convierte hora/minuto/meridiem a formato HH:MM (24h)."""
    minute = int(minute_raw or "0")
    hour = int(hour_raw)

    if minute < 0 or minute > 59:
        return None

    meridiem_norm = normalize_text(meridiem or "")
    if meridiem_norm in {"am", "manana"}:
        if hour == 12:
            hour = 0
    elif meridiem_norm in {"pm", "tarde", "noche"}:
        if hour < 12:
            hour += 12

    if hour < 0 or hour > 23:
        return None

    return f"{hour:02d}:{minute:02d}"


def parse_date_candidate(raw: str | None, base_date: date) -> str | None:
    """
    Intenta parsear una fecha en español desde texto libre.

    Soporta: ISO (2025-05-06), DD/MM/YYYY, DD/MM, "3 de mayo",
    "hoy", "mañana", "pasado mañana", nombres de días de la semana.

    Consolidación de:
      - orchestrator/tool_runner.py :: _parse_date_candidate
      - orchestrator/intent_router.py :: _extract_date (parcial)
    """
    if not raw:
        return None

    text = normalize_text(str(raw))

    # ── ISO: 2025-05-06 ──────────────────────────────────────────────────────
    match = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return None

    # ── DD/MM/YYYY ────────────────────────────────────────────────────────────
    match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2,4})(?!\d)", text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    # ── DD/MM (sin año) ───────────────────────────────────────────────────────
    match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!/\d)", text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), base_date.year
        try:
            candidate = date(year, month, day)
            if candidate < base_date:
                candidate = date(year + 1, month, day)
            return candidate.isoformat()
        except ValueError:
            return None

    # ── "3 de mayo", "primero de junio" ───────────────────────────────────────
    match = re.search(
        r"(?<!\d)(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s+de\s+(\d{4}))?",
        text,
    )
    if match:
        day = int(match.group(1))
        month = MONTH_MAP.get(normalize_text(match.group(2)))
        year = int(match.group(3)) if match.group(3) else base_date.year
        if month:
            try:
                candidate = date(year, month, day)
                if not match.group(3) and candidate < base_date:
                    candidate = date(year + 1, month, day)
                return candidate.isoformat()
            except ValueError:
                return None

    # ── Relativos ─────────────────────────────────────────────────────────────
    if "pasado manana" in text:
        return (base_date + timedelta(days=2)).isoformat()
    if "manana" in text:
        return (base_date + timedelta(days=1)).isoformat()
    if "hoy" in text:
        return base_date.isoformat()

    # ── Día de la semana ("este lunes", "el viernes") ─────────────────────────
    for weekday_name, weekday_number in WEEKDAY_MAP.items():
        if re.search(rf"\b(?:este\s+|el\s+)?{weekday_name}\b", text):
            delta = (weekday_number - base_date.weekday()) % 7
            if delta == 0:
                delta = 7
            return (base_date + timedelta(days=delta)).isoformat()

    return None


def parse_time_candidate(raw: str | None) -> str | None:
    """
    Intenta parsear una hora en español desde texto libre.

    Soporta: "14:30", "2 pm", "a las 3 de la tarde", "mediodía", "medianoche".

    Consolidación de: orchestrator/tool_runner.py :: _parse_time_candidate
    """
    if not raw:
        return None

    text = normalize_text(str(raw))
    text = re.sub(r"\ba\s*\.?\s*m\s*\.?\b", "am", text)
    text = re.sub(r"\bp\s*\.?\s*m\s*\.?\b", "pm", text)
    text = text.replace(".", ":")

    if "mediodia" in text:
        return "12:00"
    if "medianoche" in text:
        return "00:00"

    # ── HH:MM [am/pm] ────────────────────────────────────────────────────────
    match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})\s*(am|pm)?(?!\d)", text)
    if match:
        meridiem = match.group(3)
        if not meridiem:
            if "de la manana" in text:
                meridiem = "manana"
            elif "de la tarde" in text:
                meridiem = "tarde"
            elif "de la noche" in text:
                meridiem = "noche"
        return format_time_24h(match.group(1), match.group(2), meridiem)

    # ── H am/pm ───────────────────────────────────────────────────────────────
    match = re.search(r"(?<!\d)(\d{1,2})\s*(am|pm)(?!\d)", text)
    if match:
        return format_time_24h(match.group(1), "00", match.group(2))

    # ── H [de la] mañana/tarde/noche ──────────────────────────────────────────
    match = re.search(
        r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(?:de la )?(manana|tarde|noche)(?!\w)",
        text,
    )
    if match:
        return format_time_24h(match.group(1), match.group(2) or "00", match.group(3))

    # ── "a las H[:MM]" ────────────────────────────────────────────────────────
    match = re.search(r"(?:a\s+las|hora\s*:?)\s*(\d{1,2})(?::(\d{2}))?", text)
    if match:
        return format_time_24h(match.group(1), match.group(2) or "00")

    return None
