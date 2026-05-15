import re
import unicodedata
import logging
from typing import Optional
import difflib

from tools.shared.utils import (
    normalize_text as _normalize_shared,
    CATEGORY_KEYWORDS as _CATEGORY_KEYWORDS,
    HELP_KEYWORDS as _HELP_KEYWORDS,
    GREETING_KEYWORDS as _GREETING_KEYWORDS,
    FAREWELL_KEYWORDS as _FAREWELL_KEYWORDS,
    IDENTITY_KEYWORDS as _IDENTITY_KEYWORDS,
    CAPABILITIES_KEYWORDS as _CAPABILITIES_KEYWORDS,
    MAP_SHOW_KEYWORDS as _MAP_SHOW_KEYWORDS,
    MAP_FIT_KEYWORDS as _MAP_FIT_KEYWORDS,
    MAP_LOCATE_KEYWORDS as _MAP_LOCATE_KEYWORDS,
    GPS_GRANTED_KEYWORDS as _GPS_GRANTED_KEYWORDS,
    GPS_DENIED_KEYWORDS as _GPS_DENIED_KEYWORDS,
    GPS_NO_SIGNAL_KEYWORDS as _GPS_NO_SIGNAL_KEYWORDS,
    ZOOM_IN_KEYWORDS as _ZOOM_IN_KEYWORDS,
    ZOOM_OUT_KEYWORDS as _ZOOM_OUT_KEYWORDS,
    FLY_TO_BIZ_KEYWORDS as _FLY_TO_BIZ_KEYWORDS,
    REVIEWS_KEYWORDS as _REVIEWS_KEYWORDS,
    AVAILABILITY_KEYWORDS as _AVAILABILITY_KEYWORDS,
    MISSION_KEYWORDS as _MISSION_KEYWORDS,
    SERVICES_KEYWORDS as _SERVICES_KEYWORDS,
    WEB_KEYWORDS as _WEB_KEYWORDS,
    COMPARE_KEYWORDS as _COMPARE_KEYWORDS,
    RECOMMEND_KEYWORDS as _RECOMMEND_KEYWORDS,
    INFO_KEYWORDS as _INFO_KEYWORDS,
    ADMIN_NAV_TARGETS as _ADMIN_NAV_TARGETS,
)

from core.logger import setup_logger

logger = setup_logger("lyra.intent_router")

def _extract_service_name(text: str) -> Optional[str]:
    """
    Extrae el nombre del servicio dividiendo el texto por conectores y filtrando segmentos temporales.
    """
    # Ciudades y palabras genéricas que NO son servicios
    _not_service = {
        "cali", "medellin", "bogota", "popayan", "cartagena", "barranquilla", "bucaramanga",
        "cali ciudad", "opciones", "opcion", "alternatives", "alternativas", "negocios", "negocio",
        "barberias", "barberia", "peluquerias", "restaurantes", "hoteles", "gimnasios",
        "ese", "esa", "esos", "esas", "el", "la", "los", "las", "uno", "una",
    }
    
    # Dividir el texto por los conectores comunes
    parts = re.split(r'\b(para|en|de|el servicio de)\b', text, flags=re.IGNORECASE)
    
    _temporal_words = ["manana", "hoy", "ayer", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo", 
                       "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    _conf_words = ["si", "dale", "vale", "ok", "okay", "listo", "claro", "proceder", "vaya", "acepto", "confirmo", "perfecto", "bueno",
                   "necesito", "quiero", "gustaria", "reservar", "agendar", "cita", "turno", "reserva", "pide", "pedir", "solicitar"]

    # Buscamos de atrás hacia adelante (los servicios suelen ir al final en confirmaciones)
    for i in range(len(parts)-1, 0, -1):
        if parts[i].lower() in ["para", "en", "de", "el servicio de"]:
            candidate = parts[i+1].strip() if (i+1) < len(parts) else ""
            if not candidate: continue
            
            # Limpieza básica
            clean_cand = re.sub(r'^(?:el\s+servicio\s+de|la|el|un|una|mi|mis|este|esta)\s+', '', candidate, flags=re.IGNORECASE).strip()
            
            # Cortar indicadores temporales del candidato en lugar de descartarlo todo
            for kw in [" a las ", " pm", " am", " hoy", " manana", " horas", " de abril", " de mayo", " de junio"]:
                if kw in clean_cand.lower():
                    clean_cand = clean_cand.lower().split(kw)[0].strip()

            words = clean_cand.split()
            if not words: continue
            first_word = words[0].lower()
            
            if first_word in _temporal_words:
                continue
            
            # Excluir ciudades y palabras genéricas que no son servicios
            if clean_cand.lower() in _not_service or first_word in _not_service:
                continue
                
            # Si el conector era "de", puede ser parte de un nombre compuesto (ej: "Reparacion de PC")
            if parts[i].lower() == "de" and i >= 2:
                prev_segment = parts[i-1].strip()
                if prev_segment.lower() not in _temporal_words:
                    clean_cand = prev_segment + " de " + clean_cand
            
            # Limpiar palabras de confirmación
            for cw in _conf_words:
                clean_cand = re.sub(r'\b' + cw + r'\b', '', clean_cand, flags=re.IGNORECASE).strip()

            # Limpiar símbolos al inicio/final
            clean_cand = re.sub(r'[^\w\s]', '', clean_cand).strip()

            if len(clean_cand) >= 3:
                return clean_cand
                
    return None

def _normalize(text: str) -> str:
    """Thin wrapper — delegates to tools.shared.utils.normalize_text with punctuation stripping."""
    return _normalize_shared(text, strip_punctuation=True)


def _extract_city(text: str) -> Optional[str]:
    """
    Extrae la ciudad del texto. Soporta ciudades principales y un heurístico para 'en [Ciudad]'.
    """
    if not text: return None
    
    # 1. Ciudades con nombres conocidos (mapeo a normalizado)
    known_cities = {
        "cali": "Cali", "caly": "Cali",
        "popayan": "Popayan", "popayan": "Popayan", "popayna": "Popayan", "popyan": "Popayan",
        "medellin": "Medellin", "medellin": "Medellin", "medallo": "Medellin",
        "bogota": "Bogota", "bogota": "Bogota", "rolo": "Bogota",
        "pereira": "Pereira", "manizales": "Manizales", "armenia": "Armenia",
        "barranquilla": "Barranquilla", "cartagena": "Cartagena", "santa marta": "Santa Marta",
        "bucaramanga": "Bucaramanga", "cucuta": "Cucuta", "villavicencio": "Villavicencio",
        "pasto": "Pasto", "neiva": "Neiva", "tunja": "Tunja", "ibague": "Ibague"
    }

    # Búsqueda directa por palabra clave
    for kw, city_name in known_cities.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            return city_name

    # 2. Heurístico: buscar lo que sigue a "en " si parece un nombre propio o es el final de la cadena
    # Ejemplo: "ver negocios en Medellin" -> extrae "Medellin"
    match = re.search(r"\ben\s+([a-záéíóúñ]{3,}(?:\s+[a-záéíóúñ]{3,})?)$", text)
    if match:
        candidate = match.group(1).strip()
        # Evitar capturar palabras comunes como "el mapa", "la lista"
        if candidate.lower() not in ["el mapa", "mapa", "la lista", "lista", "este momento", "mi zona"]:
            return candidate.capitalize()

    return None


# ── Detector de spam / texto basura ─────────────────────────────────────────
# Protege el presupuesto de tokens: detecta mash de teclado, texto aleatorio
# y cualquier entrada que no tenga palabras en español reconocibles.

# Palabras mínimas que indican que el mensaje tiene algún significado en español
_SPANISH_ANCHOR_WORDS = {
    "hola", "buenos", "dias", "tardes", "noches", "gracias", "por", "favor", "si", "no",
    "que", "como", "donde", "cuando", "quien", "cual", "hay", "tengo", "quiero", "busco",
    "necesito", "puedo", "puede", "ayuda", "informacion", "ver", "ir", "lleva", "vale",
    "ok", "dale", "bien", "listo", "adios", "chao", "bye", "cita", "reserva", "servicio",
    "hotel", "restaurante", "barberia", "medico", "gym", "taller", "negocio", "empresa",
    "precio", "costo", "horario", "abierto", "cerrado", "telefono", "direccion",
    "perfil", "mas", "menos", "todo", "nada", "algo", "alguien", "nadie",
    # Respuestas cortas válidas
    "dale", "vamos", "anda", "esta", "ese", "esa", "eso",
}


def _is_spam(text: str) -> bool:
    """
    Detecta de forma AGRESIVA si el texto es basura / spam / mash de teclado.
    """
    if not text:
        return False

    t = text.strip().lower()
    
    # Muy corto pero válido ("si", "no", "ok", "ir", "hey")
    if len(t) <= 3:
        # Pero si son caracteres repetidos o no-alfa, es basura
        if len(t) > 1 and not any(c.isalpha() for c in t): return True
        return False

    # --- Criterio 1: Bloques de consonantes consecutivos (ajustado a 3 para palabras cortas) ---
    # En español es raro tener 4 consonantes seguidas (ej: "trans...", "const...")
    # Pero 3 seguidas es común. Si hay 4+ seguidas es muy sospechoso.
    if re.search(r'[bcdfghjklmnñpqrstvwxyz]{4,}', t):
        return True

    # --- Criterio 2: Densidad Global de Consonantes ---
    vowels = set('aeiouáéíóúü')
    alfa_chars = [c for c in t if c.isalpha()]
    if alfa_chars:
        v_count = sum(1 for c in alfa_chars if c in vowels)
        v_ratio = v_count / len(alfa_chars)
        # El español tiene aprox 45% de vocales. Si tiene menos del 25% en una palabra larga, es basura.
        if len(alfa_chars) >= 6 and v_ratio < 0.28:
            return True
        # Si no tiene ninguna vocal y mide más de 2 caracteres
        if len(alfa_chars) >= 3 and v_count == 0:
            return True

    # --- Criterio 3: Mash de teclado sin espacios y repetitivo ---
    if ' ' not in t:
        # Repetición de caracteres
        for char in set(t):
            if t.count(char) / len(t) > 0.45: # Si una letra ocupa casi la mitad del texto
                return True
        # Patrones repetitivos (ej: asdasd, qweqwe)
        if len(t) >= 6:
            half = len(t) // 2
            if t[:half] == t[half:] or t[:3] == t[3:6]:
                return True

    # --- Criterio 4: Detección de Mash por longitud ---
    # Si es una palabra muy larga (>12) y no tiene espacios, es sospechosa a menos que sea muy "vocal"
    if ' ' not in t and len(t) > 12:
        return True

    return False

# Mapeo de categorías para search_businesses
# Keywords are now imported from tools.shared.utils

def _extract_date(text: str) -> Optional[str]:
    """
    Extrae la fecha del texto. Soporta relativos y fechas específicas.
    """
    if "manana" in text: return "tomorrow"
    if "hoy" in text: return "today"
    
    # Soporte para "el 30 de abril", "el 5 de mayo", etc.
    months = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05", "junio": "06",
        "julio": "07", "agosto": "08", "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }
    # Ordinales textuales en español
    _ordinal_to_day = {
        "primero": 1, "primera": 1, "uno": 1,
        "segundo": 2, "segunda": 2, "dos": 2,
        "tercero": 3, "tercera": 3, "tres": 3,
        "cuarto": 4, "cuarta": 4, "cuatro": 4,
        "quinto": 5, "quinta": 5, "cinco": 5,
        "sexto": 6, "sexta": 6, "seis": 6,
        "septimo": 7, "septima": 7, "siete": 7,
        "octavo": 8, "octava": 8, "ocho": 8,
        "noveno": 9, "novena": 9, "nueve": 9,
        "decimo": 10, "decima": 10, "diez": 10,
    }
    
    # "el primero de mayo", "el tres de junio"
    ord_match = re.search(r"(?:el\s+)?([a-z]+)\s+de\s+([a-z]+)", text, re.IGNORECASE)
    if ord_match:
        day_word = ord_match.group(1).lower()
        month_name = ord_match.group(2).lower()
        if day_word in _ordinal_to_day and month_name in months:
            from datetime import datetime
            year = datetime.now().year
            return f"{year}-{months[month_name]}-{_ordinal_to_day[day_word]:02d}"

    date_match = re.search(r"(\d{1,2})\s+de\s+([a-z]+)", text, re.IGNORECASE)
    if date_match:
        day = int(date_match.group(1))
        month_name = date_match.group(2).lower()
        if month_name in months:
            from datetime import datetime
            year = datetime.now().year
            return f"{year}-{months[month_name]}-{day:02d}"
            
    # Solo número: "el 30" (asumimos mes actual)
    day_match = re.search(r"\bel\s+(\d{1,2})\b", text, re.IGNORECASE)
    if day_match:
        from datetime import datetime
        day = int(day_match.group(1))
        now = datetime.now()
        return f"{now.year}-{now.month:02d}-{day:02d}"

    return None

def detect_intent(message: str, project_id: str, mentioned_city: Optional[str] = None, current_context: dict = None) -> dict:
    """
    Analiza el mensaje del usuario localmente para determinar si Lyra
    debe ejecutar alguna herramienta antes de ir al LLM.
    """
    text = _normalize(message)
    # Limpieza preliminar de muletillas de cortesía
    text = re.sub(r'\b(por\s*favor|por\s*fa|gracias|muchas\s*gracias)\b', '', text).strip()
    
    # DIAGNÓSTICO VER-2.6: Prioridad de Búsqueda + Localización
    logger.info(f"--- [DETECTOR V2.6] Texto: '{text}' | Project: '{project_id}' ---")

    if not text:
        return {"intent": "conversation"}
        
    current_context = current_context or {}
    last_assistant_msg = current_context.get("last_assistant_msg", "").lower()
    
    # ─── BOOKING FLOW CONTEXT ───────────────────────────────────────────────
    # Detect if we are in the middle of a booking process to avoid misidentifying intents
    booking_kws = ["agendar", "reserva", "cita", "disponibilidad", "horario", "servicio", "quien", "profesional", "agendamos", "nombre"]
    is_in_booking_flow = any(kw in last_assistant_msg for kw in booking_kws) or "[CONFIRMACIÓN NECESARIA]" in last_assistant_msg
    
    mentioned_city_from_text = _extract_city(text)
    mentioned_city = mentioned_city_from_text or mentioned_city
    mentioned_date = _extract_date(text)
    
    # --- [TIME/DATE DETECTION] ---
    is_time_or_date = mentioned_date is not None or re.search(r"\b\d{1,2}(?::\d{2})?\s*(am|pm|tarde|noche)\b", text, re.IGNORECASE)

    # (Context-aware block moved down for priority or integrated)

    # --- [FAST PATH] GREETINGS / FAREWELLS (0 TOKENS) ---
    if any(text == kw for kw in _FAREWELL_KEYWORDS):
        logger.info("DETECCION: intent='farewell' (Fast Path)")
        return {"intent": "farewell", "args": {}}

    _is_pure_greeting = any(text == kw for kw in _GREETING_KEYWORDS)
    _is_identity = any(text == kw for kw in _IDENTITY_KEYWORDS)

    if (len(text) <= 3 and text not in ["si", "ok", "no", "dale", "vale", "zoom", "ver", "ir", "voy"]) or _is_pure_greeting:
        logger.info(f"DETECCION: intent='greeting' (Fast Path) | is_pure={_is_pure_greeting}")
        return {"intent": "greeting", "args": {}}

    if _is_identity:
        logger.info("DETECCION: intent='identity' (Fast Path)")
        return {"intent": "identity", "args": {}}

    _is_capabilities = any(text == kw for kw in _CAPABILITIES_KEYWORDS)
    if _is_capabilities:
        logger.info("DETECCION: intent='capabilities' (Fast Path)")
        return {"intent": "capabilities", "args": {}}

    # Greeting con texto adicional (ej: "hola busco un restaurante")
    # Si detectamos un saludo al inicio, lo removemos para que no ensucie la búsqueda de categorías
    for kw in _GREETING_KEYWORDS:
        if text.startswith(kw + " "):
            logger.info(f"SALUDO DETECTADO: '{kw}' | Recortando texto...")
            text = text[len(kw):].strip()
            # Si después de quitar el saludo queda una identidad o despedida, lo re-detectamos
            if any(text == k for k in _IDENTITY_KEYWORDS):
                logger.info("DETECCION: intent='identity' (Fast Path post-greeting)")
                return {"intent": "identity", "args": {}}
            if any(text == k for k in _CAPABILITIES_KEYWORDS):
                logger.info("DETECCION: intent='capabilities' (Fast Path post-greeting)")
                return {"intent": "capabilities", "args": {}}
            if any(text == k for k in _FAREWELL_KEYWORDS):
                logger.info("DETECCION: intent='farewell' (Fast Path post-greeting)")
                return {"intent": "farewell", "args": {}}
            break


    # Ej: "el primero de mayo a las 2 pm" / "a las 3 de la tarde mañana"
    _early_is_pm = any(kw in text for kw in ["tarde", "noche", "pm", "pasado meridiano"])
    _early_is_am = any(kw in text for kw in ["madrugada", "am"]) and not _early_is_pm
    _early_time_match = re.search(r"(?:a las|las)?\s*(\d{1,2})(?::(\d{2}))?(?:\s+de\s+la)?\s*(am|pm|tarde|noche|manana|madrugada)?", message, re.IGNORECASE)
    _early_date = mentioned_date
    if _early_time_match and _early_date and project_id == "nexiservice":
        _has_appointment_kw = any(kw in text for kw in ["agendar", "reservar", "cita", "turno", "agendame"])
        if not _has_appointment_kw:
            h = int(_early_time_match.group(1))
            m = _early_time_match.group(2) or "00"
            p = (_early_time_match.group(3) or "").lower()
            if p in ("pm", "tarde", "noche") and h < 12: h += 12
            elif p in ("am", "madrugada") and h == 12: h = 0
            elif _early_is_pm and h < 12: h += 12
            elif _early_is_am and h == 12: h = 0
            time_str = f"{h:02d}:{m}"
            logger.info(f"DETECCIÓN TEMPRANA: fecha+hora → request_appointment | time={time_str} | date={_early_date}")
            return {"intent": "request_appointment", "args": {"business_name": None, "time": time_str, "service_name": None, "date": _early_date, "professional_name": None}}

    # --- 0. ACCIONES TÉCNICAS (Prioridad Crítica Real) ---
    # Detectar zoom out ANTES de zoom in para evitar que "menos zoom" coincida con "zoom"
    if any(kw in text for kw in _ZOOM_OUT_KEYWORDS):
        logger.info("DETECCION: intent='zoom_out' (Prioridad 0)")
        return {"intent": "zoom_out", "args": {}}
    if any(kw in text for kw in _ZOOM_IN_KEYWORDS):
        # SI el texto dice algo como "negocio mas cercano" o "la mas cercana", priorizamos busqueda de proximidad
        if any(kw in text for kw in ["cerca", "cercan", "cercano", "mas cerca"]):
            cat_match = next((cat for cat, kws in _CATEGORY_KEYWORDS.items() if any(k in text for k in kws)), "negocios")
            logger.info(f"DETECCION: intent='search_businesses' (singular mas cercano) | cat={cat_match} | city={mentioned_city}")
            return {"intent": "search_businesses", "args": {"category": cat_match, "near_me": True, "city": mentioned_city}}
            
        logger.info("DETECCION: intent='zoom_in' (Prioridad 0)")
        return {"intent": "zoom_in", "args": {}}

    # ── GPS: Permiso aceptado ────────────────────────────────────────────────
    if any(kw in text for kw in _GPS_GRANTED_KEYWORDS):
        logger.info("DETECCION: intent='gps_granted'")
        return {"intent": "gps_granted", "args": {}}

    # ── GPS: Permiso denegado → ciudad manual ───────────────────────────────
    if any(kw in text for kw in _GPS_DENIED_KEYWORDS):
        logger.info("DETECCION: intent='gps_denied'")
        return {"intent": "gps_denied", "args": {}}

    # ── GPS: Sin señal → fallback manual ───────────────────────────────────
    if any(kw in text for kw in _GPS_NO_SIGNAL_KEYWORDS):
        logger.info("DETECCION: intent='gps_no_signal'")
        return {"intent": "gps_no_signal", "args": {}}

    # ── Ciudad manual: "mi ciudad es X" / "estoy en X" ──────────────────────
    city_set_match = re.search(
        r"(?:mi ciudad es|vivo en|estoy en|me encuentro en|ciudad\s*:?\s*)([A-Za-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00c1\u00c9\u00cd\u00d3\u00da\u00d1]+(?:\s+[A-Za-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00c1\u00c9\u00cd\u00d3\u00da\u00d1]+)?)",
        text, re.IGNORECASE
    )
    if city_set_match:
        city_name = city_set_match.group(1).strip().title()
        _stop = {"El", "La", "Los", "Las", "Un", "Una", "Mi", "Tu", "Su", "Mapa", "Lista", "El Mapa"}
        if city_name not in _stop and len(city_name) >= 3:
            logger.info(f"DETECCION: intent='set_city_manual' | city='{city_name}'")
            return {"intent": "set_city_manual", "args": {"city": city_name}}

    # Localizarme
    if any(kw in text for kw in _MAP_LOCATE_KEYWORDS):
        # SI el texto contiene también palabras de búsqueda (negocios, empresas, hay, busca),
        # priorizamos search_businesses para que Lyra recomiende algo.
        if any(kw in text for kw in ["negocio", "empresa", "hay", "lista", "busca", "estan"]):
            logger.info(f"DETECCION: intent='search_businesses' (cerca de mí) | city={mentioned_city}")
            return {"intent": "search_businesses", "args": {"category": "negocios", "near_me": True, "city": mentioned_city}}
            
        logger.info("DETECCION: intent='locate_me'")
        return {"intent": "locate_me", "args": {}}

    # Detectar comparación de negocios (Prioridad alta para evitar sombras con 'reseñas' o 'precios')
    if any(kw in text for kw in _COMPARE_KEYWORDS):
        logger.info("DETECCION: intent='compare_businesses'")
        return {"intent": "compare_businesses", "args": {}}


    # --- [CONTEXT-AWARE] FAST PATHS FOR CONVERSATIONAL BOOKING FLOW (PRIORITY) ---
    # We enter these if we are in a booking flow and the input is short.
    if project_id == "nexiservice" and len(text.split()) <= 5 and not is_time_or_date:
        asking_time = any(kw in last_assistant_msg for kw in ["hora", "cuándo", "cuando", "momento", "tiempo"])
        asking_prof = any(kw in last_assistant_msg for kw in ["profesional", "quién", "quien", "atender"])
        
        # Priority 1: Name (Strict to avoid collision with "nombre del servicio")
        # If the assistant says "agendaremos el servicio X... ¿a nombre de quién?", we must prioritize the name.
        asking_name = ("tu nombre" in last_assistant_msg or "a nombre de" in last_assistant_msg or "indícame tu nombre" in last_assistant_msg) or \
                     ("nombre" in last_assistant_msg and ("quién" in last_assistant_msg or "quien" in last_assistant_msg) and ("reserva" in last_assistant_msg or "agendar" in last_assistant_msg))
        
        if asking_name:
            logger.info(f"DETECCION: intent='request_appointment' (vía context: nombre de reserva) -> '{text}'")
            return {"intent": "request_appointment", "args": {"reservation_name": text}}

        # Priority 2: Service (Assistant says "Escribe el nombre del servicio" or "¿Cuál servicio?")
        asking_service = ("servicio" in last_assistant_msg or "cuál" in last_assistant_msg or "que servicio" in last_assistant_msg) and \
                         ("agendar" in last_assistant_msg or "deseas" in last_assistant_msg or "escribe" in last_assistant_msg or "elegir" in last_assistant_msg)
        
        if asking_service and not (asking_time or asking_prof):
             logger.info(f"DETECCION: intent='request_appointment' (vía context: servicio) -> '{text}'")
             return {"intent": "request_appointment", "args": {"service_name": text}}

    # --- [NORMAL] BUSINESS NAVIGATION ---
    # Detectar "ver perfil de X" o "ir al negocio X"
    # Skip if we are likely in a booking flow (to avoid hijacking service names)
    profile_match = None
    if not is_in_booking_flow:
        profile_match = re.search(r"(?:ver perfil|ir a|ir al negocio|abre el perfil|ver negocio|perfil de|llevame a|llévame a|quiero ir a|visitar)\s+(?:la empresa de|el negocio de|la empresa|el negocio|la|el|lo|los|las|de|al|a)?\s*(.+)", text, re.IGNORECASE)
    if profile_match:
        name = profile_match.group(1).strip()
        # Segunda capa de limpieza para el nombre capturado
        name = re.sub(r'^(la empresa de|el negocio de|empresa de|negocio de|la empresa|el negocio|la|el|los|las|de|del|un|una)\s+', '', name, flags=re.IGNORECASE).strip()
        
        # Evitar capturar palabras que deberían ir a otros intentos
        _stop_words = ["mapa", "lista", "perfil", "web", "pagina", "sitio", "mis citas", "agenda", "reseñas", "resenas", "opiniones", "servicios", "precios", "redes", "sociales", "facebook", "instagram"]
        if not any(sw in name.lower() for sw in _stop_words):
            logger.info(f"DETECCION: intent='navigate_to_company' -> '{name}' | city={mentioned_city}")
            return {"intent": "navigate_to_company", "args": {"business_name": name, "city": mentioned_city}}

    # --- Fallback: Nombre de negocio directo (ej: 'Parrilla y Carbon' o 'Parrilla y Carbon Popayan') ---
    if project_id == "nexiservice":
        # Si hay ciudad, la removemos para el nombre del negocio
        clean_text = text
        if mentioned_city:
            clean_text = re.sub(rf"\b{re.escape(mentioned_city)}\b", "", text, flags=re.IGNORECASE).strip()
            
        # Si queda algo sustancial que no parece spam ni categoría pura
        # GUARD: Si estamos en flujo de reserva, NO detectamos navegación por nombre directo
        # para evitar que nombres de servicios (ej: 'pechuga a la plancha') sean tomados como negocios.
        if len(clean_text) >= 4 and not _is_spam(clean_text) and not is_in_booking_flow:
            is_cat = any(any(kw in clean_text.lower() for kw in kws) for kws in _CATEGORY_KEYWORDS.values())
            if not is_cat:
                logger.info(f"DETECCION: intent='navigate_to_company' (direct match) -> '{clean_text}' | city={mentioned_city}")
                return {"intent": "navigate_to_company", "args": {"business_name": clean_text, "city": mentioned_city}}

    # --- NUEVO: REVIEWS ---
    if any(kw in text for kw in _REVIEWS_KEYWORDS):
        # Intentar extraer el nombre del negocio si lo menciona
        # Caso A: "reseñas de X"
        biz_match = re.search(r"(?:resenas|reseñas|opiniones|comentarios|que dicen|valoracion|calificacion|estrellas|reputacion|testimonio|que tal es|como es|que tal esta|como esta|opina|opinan|opinas|parece|piensan|piensa|crees|creen|creer)\s+(?:las\s+|los\s+|la\s+|el\s+)?(?:reseñas\s+|resenas\s+|opiniones\s+|comentarios\s+|valoracion\s+|calificacion\s+)?(?:\bde\b|sobre|del negocio|de la empresa|del local|\bdel\b|tiene|para|sobre el|sobre la)?\s*(.+)", text, re.IGNORECASE)
        
        # Caso B: "X ver reseñas"
        if not biz_match:
            biz_match = re.search(r"^(.+?)\s+(?:ver|mirar|mostrar|dame|enseñame|muestrame|quiero|necesito)\s+(?:las\s+|los\s+|la\s+|el\s+)?(?:reseñas|resenas|opiniones|comentarios|calificaciones|valoraciones)", text, re.IGNORECASE)

        biz_name = biz_match.group(1).strip() if biz_match else None
        
        # Limpiar conectores y artículos finales
        if biz_name:
            # Eliminación recursiva de prefijos genéricos
            for _ in range(3):
                biz_name = re.sub(r'^(?:las|los|la|el|mi|mis|sus|este|esta|ese|esa|negocio|empresa|local|tienda|del|de la|de los|de las|un|una|ver|el perfil de|la informacion de|info de|perfil de|biografia de)\s+', '', biz_name, flags=re.IGNORECASE).strip()
            
            # Limpiar conectores y ciudades al final (ej: "es de Cali", "en Popayan")
            biz_name = re.sub(r'\s+(?:es de|esta en|queda en|en|de)\s+([a-z\s]+)$', '', biz_name, flags=re.IGNORECASE).strip()

            _generic_terms = _REVIEWS_KEYWORDS + ["resenas", "opinion", "comentario", "valoracion", "ellas", "ellos", "este", "ese", "ella", "el", "su", "sus", "negocio", "empresa", "local", "tienda", "este negocio", "esta empresa", "este local", "esta tienda", "este establecimiento"]
            if mentioned_city:
                biz_name = re.sub(rf"\b{re.escape(mentioned_city)}\b", "", biz_name, flags=re.IGNORECASE).strip()
                biz_name = re.sub(rf"\b{re.escape(mentioned_city.lower())}\b", "", biz_name, flags=re.IGNORECASE).strip()

            if biz_name.lower() in _generic_terms or len(biz_name) < 2:
                biz_name = None

        logger.info(f"DETECCION: intent='get_business_reviews' -> '{biz_name}'")
        return {"intent": "get_business_reviews", "args": {"business_name": biz_name}}

    # --- NUEVO: INFORMACIÓN DE PROFESIONALES / SERVICIOS ---
    # (Context check moved to top)
    
    if any(kw in text for kw in ["quien es", "quien atiende", "cuentame de", "perfil de", "biografia de", "quien es el"]) and project_id == "nexiservice" and not is_in_booking_flow:
        # Regex V3.6: Case-insensitive y flexible con nombres
        prof_match = re.search(r"(?:quien es|quien atiende|cuentame de|perfil de|biografia de|quien es el)\s+(?:el\s+|la\s+|el profesional\s+|la profesional\s+)?([a-záéíóúñ\s]+)", text, re.IGNORECASE)
        if prof_match:
            prof_name = prof_match.group(1).strip()
            # Limpiar posibles restos
            prof_name = re.sub(r"\?|\!|\.", "", prof_name).strip()
            logger.info(f"DETECCION: intent='get_professional_info' -> '{prof_name}'")
            return {"intent": "get_professional_info", "args": {"professional_name": prof_name}}

    if any(kw in text for kw in ["que es", "en que consiste", "de que trata", "que ofrece el servicio"]) and project_id == "nexiservice" and not is_in_booking_flow:
        srv_match = re.search(r"(?:que es|en que consiste|de que trata|que ofrece el servicio|precio de|valor de)\s+(?:el\s+|la\s+|el servicio\s+|el servicio de\s+|la\s+)?([a-záéíóúñ\s]+)", text, re.IGNORECASE)
        if srv_match:
            srv_name = srv_match.group(1).strip()
            # Limpiar restos
            srv_name = re.sub(r"\?|\!|\.", "", srv_name).strip()
            logger.info(f"DETECCION: intent='get_service_info' -> '{srv_name}'")
            return {"intent": "get_service_info", "args": {"service_name": srv_name}}

    # --- DETECCIÓN TEMPRANA: Selección de profesional ("con [Nombre]") ---
    # Solo aplica si el mensaje es CORTO (≤6 palabras) y empieza con "con "
    # Esto captura: "con Juan", "con Lina Marcela", "con ella", "con cualquiera"
    _prof_sel_match = re.match(r'^con\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{2,40})$', message.strip(), re.IGNORECASE)
    if _prof_sel_match and len(message.strip().split()) <= 5:
        _prof_candidate = _prof_sel_match.group(1).strip()
        # Excluir si parece ser nombre de negocio (múltiples palabras con números o muy largo)
        if not re.search(r'\d', _prof_candidate):
            logger.info(f"DETECCION: intent='request_appointment' (selección profesional) -> '{_prof_candidate}'")
            return {
                "intent": "request_appointment",
                "args": {
                    "professional_name": _prof_candidate,
                    "business_name": None,
                    "service_name": None,
                    "time": None,
                    "date": None
                }
            }

    # --- NUEVO: SOLICITAR CITA (RESERVA) ---
    # Prioridad alta para acciones directas: "agendame", "reservame"
    if any(kw in text for kw in ["agendar", "reservar", "hacer cita", "solicitar cita", "pide la cita", "agendame", "reservame", "pide un turno", "quiero cita"]):
        professional_name = None
        service_name = None
        time_str = None
        srv_srv_match = re.search(r"servicio\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]{3,})", text, re.IGNORECASE)
        prof_match = re.search(r"(?:con|atendido por|con el barbero|con la esteticista|con el doctor|con la doctora|con el profesional|con la profesional)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)", message)
        if prof_match:
            professional_name = prof_match.group(1).strip()
            # Limpiar el texto para que no interfiera con servicio/negocio
            prof_phrase_norm = _normalize(prof_match.group(0))
            text = text.replace(prof_phrase_norm, "").strip()
        elif "con cualquiera" in text or "con quien sea" in text:
            professional_name = "cualquiera"

        # 1.5. Extraer hora ANTES de procesar servicio/negocio (evita que la hora se confunda con servicio)
        _is_pm = any(kw in text for kw in ["tarde", "noche", "pm", "pasado meridiano"])
        _is_am = any(kw in text for kw in ["madrugada", "am"]) and not _is_pm

        # Orden de prioridad: "a las X:XX pm", "a las X pm", "X:XX", "X pm"
        _tm_full = re.search(r"a\s+las\s+(\d{1,2}):(\d{2})\s*(am|pm|tarde|noche)?", text, re.IGNORECASE)
        _tm_hour_only = re.search(r"a\s+las\s+(\d{1,2})\s*(am|pm|tarde|noche)?", text, re.IGNORECASE)
        _tm_colon = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text, re.IGNORECASE)
        _tm_simple = re.search(r"\b(\d{1,2})\s*(am|pm)\b", text, re.IGNORECASE)

        _tm = _tm_full or _tm_hour_only or _tm_colon or _tm_simple

        if _tm:
            try:
                _h = int(_tm.group(1))
                _m = "00"
                _p_raw = ""
                
                # Determinar qué capturó cada grupo según el regex que hizo match
                if _tm == _tm_full:
                    _m = _tm.group(2)
                    _p_raw = _tm.group(3) or ""
                elif _tm == _tm_hour_only:
                    _p_raw = _tm.group(2) or ""
                elif _tm == _tm_colon:
                    _m = _tm.group(2)
                    _p_raw = _tm.group(3) or ""
                elif _tm == _tm_simple:
                    _p_raw = _tm.group(2) or ""

                _p_raw = _p_raw.lower().strip()
                if (_p_raw in ("pm", "tarde", "noche") or _is_pm) and _h < 12: 
                    _h += 12
                elif (_p_raw in ("am", "madrugada") or _is_am) and _h == 12: 
                    _h = 0
                
                time_str = f"{_h:02d}:{_m}"
                logger.info(f"TIME EXTRAÍDO en bloque appointment: {time_str}")
            except Exception as _te:
                logger.warning(f"Error extrayendo hora: {_te}")

        # 2. Intento de extracción de negocio (V3.9: excluir contexto de servicio)
        # Primero remover el fragmento "servicio de X" para que no contamine el biz_name
        text_for_biz = re.sub(r'(?:un |una )?servicio\s+de\s+[a-záéíóúñ\s]+', '', text, flags=re.IGNORECASE).strip()
        biz_match = re.search(r"(?:\bde\b|\ben\b|\bdel\b|\bal\b)\s+(?!servicio\s+de|dia\b|hoy\b|mañana\b|lunes\b|martes\b|miercoles\b|jueves\b|viernes\b|sabado\b|domingo\b)([a-zA-Z0-9áéíóúÁÉÍÓÚñÑ\s]{2,})", text_for_biz, re.IGNORECASE)
        biz_name = biz_match.group(1).strip() if biz_match else None
        
        if srv_srv_match:
            service_name = srv_srv_match.group(1).strip()
        elif is_in_booking_flow and len(text.split()) <= 4:
            # Si estamos en flujo y no se detectó nada más, asumimos que el texto es el servicio
            service_name = text
        # Caso B: Acción directa al inicio "[agendar] [servicio]"
        if not service_name:
            srv_match = re.search(r"(?:agendar|reservar|agendame|reservame|cita para|turno para)\s+(?:un|una|el|la)?\s*([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]{3,})", text, re.IGNORECASE)
            if srv_match:
                service_name = srv_match.group(1).strip()
                # Excluir si capturó "servicio de" como service_name
                if re.match(r'^servicio\s+de$', service_name, re.IGNORECASE):
                    service_name = None
        
        # Limpieza cruzada de conectores
        _connectors = r"(?:\b)(?:en|para|con|el|la|a las|el dia)(?:\b)"
        if biz_name:
            biz_name = re.split(_connectors, biz_name, flags=re.IGNORECASE)[0].strip()
        if service_name:
            # Si el servicio capturado contiene el negocio, lo limpiamos SOLO si es una palabra independiente
            if biz_name and re.search(rf'\b{re.escape(biz_name)}\b', service_name, flags=re.IGNORECASE):
                # Cortar usando límites de palabra
                parts = re.split(rf'\b{re.escape(biz_name)}\b', service_name, flags=re.IGNORECASE)
                if len(parts) > 1:
                    # Re-ensamblar partes no vacías
                    cleaned = " ".join([p.strip() for p in parts if p.strip()])
                    if len(cleaned) > 3:
                        service_name = cleaned
                    else:
                        service_name = parts[0].strip()
            
            # Limpiar conectores al inicio o final
            service_name = re.sub(r"^(?:en|con|de|para|el|la|a las|el dia|una|un)\s+", "", service_name, flags=re.IGNORECASE).strip()
            # Limpiar verbos de acción al final (ej: "postre del dia agendar")
            service_name = re.sub(r"\s+(?:agendar|reservar|agendame|reservame|cita|turno|turno para|cita para)$", "", service_name, flags=re.IGNORECASE).strip()
            
            # Limpiar si contiene indicadores de fecha/hora
            _date_indicators = [
                "hoy", "manana", "ayer", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
                " a las ", " pm", " am", " de la tarde", " de la noche", " de la manana", " tarde", " noche",
                " de enero", " de febrero", " de marzo", " de abril", " de mayo", " de junio",
                " de julio", " de agosto", " de septiembre", " de octubre", " de noviembre", " de diciembre",
                "30 de", "primero de", "segundo de"
            ]
            # No limpiar indicadores de fecha si parecen ser parte de un nombre (ej: "Menu del dia")
            # Solo limpiar si están aislados o seguidos de números
            for kw in _date_indicators:
                if re.search(rf"\b{kw}\b\s+\d+", service_name, re.IGNORECASE):
                    service_name = re.sub(rf"\b{kw}\b\s+\d+", "", service_name, flags=re.IGNORECASE).strip()
                    break
            
            if len(service_name) < 2: service_name = None
            else:
                service_name = re.split(_connectors, service_name, flags=re.IGNORECASE)[0].strip()

        # Fallback de servicio si no se detectó o quedó muy corto
        if not service_name or len(service_name) < 2:
            # Buscar después de conectores comunes al final del texto
            # pero excluir si el candidato parece ser una fecha
            _date_starters = ["hoy", "manana", "ayer", "el", "para", "lunes", "martes", "miercoles",
                              "jueves", "viernes", "sabado", "domingo", "enero", "febrero", "marzo",
                              "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                              "noviembre", "diciembre", "primero", "segundo", "tercero", "dia", "sol", "mar", "paz"]
            final_match = re.search(r"(?:para|de|del|al|un|una|el|la)\s+([a-zA-Z0-9áéíóúÁÉÍÓÚñÑ\s]{2,})$", text, re.IGNORECASE)
            if final_match:
                cand = final_match.group(1).strip()
                # Rechazar si el candidato empieza con indicador de fecha
                if not any(cand.lower().startswith(ds) for ds in _date_starters):
                    service_name = cand
        
        # Caso especial: Si el servicio está DESPUES del negocio (ej: "en FS una reparacion")
        if biz_name and not service_name:
            after_biz = re.search(re.escape(biz_name) + r"\s+(?:una|un|la|el)?\s*([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]{3,})", text, re.IGNORECASE)
            if after_biz:
                service_name = after_biz.group(1).strip()
                service_name = re.split(_connectors, service_name, flags=re.IGNORECASE)[0].strip()
        # Nueva Opción: Patrón "SERVICIO agendar/reservar"
        if not service_name:
            srv_rev_match = re.search(r"^([a-zA-Z0-9áéíóúÁÉÍÓÚñÑ\s]{3,})\s+(?:agendar|reservar|agendame|reservame|cita|turno)$", text, re.IGNORECASE)
            if srv_rev_match:
                service_name = srv_rev_match.group(1).strip()
        
        # Nueva Opción: Patrón "PROFESIONAL agendar/reservar"
        if not professional_name:
            prof_rev_match = re.search(r"^([a-zA-Z0-9áéíóúÁÉÍÓÚñÑ\s]{3,})\s+(?:agendar|reservar|agendame|reservame|cita|turno)$", text, re.IGNORECASE)
            if prof_rev_match:
                professional_name = prof_rev_match.group(1).strip()
                # Si se detectó como servicio por el regex anterior, pero es el mismo texto,
                # dejar que el sistema de búsqueda de Nexi lo resuelva después

        # Limpieza final de negocio (Evitar que sea igual al servicio o una fecha)
        if biz_name:
            _date_indicators = ["hoy", "manana", "ayer", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo", 
                                "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
                                " a las ", " pm", " am", " horas", " hora", "dia"]
            if any(k in biz_name.lower() for k in _date_indicators) or (service_name and service_name.lower() in biz_name.lower()) or biz_name.lower().strip() == "dia":
                biz_name = None
            else:
                for _ in range(2):
                    biz_name = re.sub(r'^(?:las|los|la|el|mi|mis|sus|este|esta|ese|esa|para|en|negocio|empresa|local|tienda)\s+', '', biz_name, flags=re.IGNORECASE).strip()

        if biz_name or service_name or professional_name or time_str or any(kw in text.lower() for kw in ["agendar", "reservar", "agendame", "reservame"]):
            logger.info(f"DETECCION: intent='request_appointment' | biz={biz_name} | srv={service_name} | prof={professional_name} | time={time_str}")
            return {
                "intent": "request_appointment",
                "args": {
                    "business_name": biz_name,
                    "service_name": service_name,
                    "professional_name": professional_name,
                    "time": time_str,
                    "date": mentioned_date
                }
            }

    # --- BÚSQUEDA DE HORA (NIVEL SUPERIOR) ---
    # Helper: detectar si hay indicador de tarde/noche en el texto
    _is_pm_context = any(kw in text for kw in ["tarde", "noche", "pm", "pasado meridiano"])
    _is_am_context = any(kw in text for kw in ["manana", "madrugada", "am"]) and not _is_pm_context

    time_match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not time_match:
        time_match = re.search(r"(\d{1,2})\s*(am|pm)", text, re.IGNORECASE)
    if not time_match:
        time_match = re.search(r"a\s+las\s+(\d{1,2})(?::(\d{2}))?(?:\s+de\s+la)?\s*(am|pm|tarde|noche|manana|madrugada)?", text, re.IGNORECASE)
    
    if time_match:
        try:
            h = int(time_match.group(1))
            m = time_match.group(2) or "00" if ":" in time_match.group(0) else "00"
            if "a las" in time_match.group(0).lower() and ":" in time_match.group(0):
                m = time_match.group(2) or "00"
            
            p = None
            m_full = time_match.group(0).lower()
            if "pm" in m_full or "tarde" in m_full or "noche" in m_full: p = "pm"
            elif "am" in m_full or "madrugada" in m_full: p = "am"
            elif _is_pm_context: p = "pm"
            elif _is_am_context: p = "am"
            
            if p == "pm" and h < 12: h += 12
            if p == "am" and h == 12: h = 0
            time_str = f"{h:02d}:{m}"
            
            if project_id == "nexiservice":
                return {
                    "intent": "request_appointment",
                    "args": {
                        "time": time_str,
                        "date": mentioned_date,
                        "service_name": None,
                        "business_name": None
                    }
                }
        except:
            pass

        if biz_name:
            # Limpiar si el nombre parece ser solo una fecha/hora o el servicio capturado
            _date_indicators = ["hoy", "manana", "ayer", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo", 
                                "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
                                " a las ", " pm", " am", " horas", " hora"]
            if any(k in biz_name.lower() for k in _date_indicators) or (service_name and service_name.lower() in biz_name.lower()):
                biz_name = None
            else:
                for _ in range(2):
                    biz_name = re.sub(r'^(?:las|los|la|el|mi|mis|sus|este|esta|ese|esa|para|en|negocio|empresa|local|tienda)\s+', '', biz_name, flags=re.IGNORECASE).strip()

        # Extracción de profesional (ej: "con Valentina", "con el barbero Carlos")
        professional_name = None
        prof_match = re.search(r"(?:con|atendido por|con el barbero|con la esteticista|con el doctor|con la doctora|con el profesional|con la profesional)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)", message)
        if prof_match:
            professional_name = prof_match.group(1).strip()
        elif "con cualquiera" in text or "con quien sea" in text:
            professional_name = "cualquiera"

        logger.info(f"DETECCION: intent='request_appointment' -> '{biz_name}' | time='{time_str}' | srv='{service_name}' | date='{mentioned_date}' | prof='{professional_name}'")
        return {"intent": "request_appointment", "args": {"business_name": biz_name, "time": time_str, "service_name": service_name, "date": mentioned_date, "professional_name": professional_name}}

    # --- NUEVO: DISPONIBILIDAD (AGENDA) ---
    # Mejorado: Typo-tolerant (disponinilidad) y busca el keyword en cualquier posición
    _avail_kw = ["disponibilidad", "disponinilidad", "agenda", "libre", "citas", "turnos", "agendamiento", "horario"]
    if any(kw in text.lower() for kw in _avail_kw) and str(project_id or "").lower() != "schoolsena":
        # GUARDA: Si hay ciudad + categoría, es una búsqueda de negocios, no de agenda de un negocio específico
        has_category_in_text = any(any(kw in text for kw in kws) for kws in _CATEGORY_KEYWORDS.values())
        if has_category_in_text and mentioned_city:
            for cat, kws in _CATEGORY_KEYWORDS.items():
                if any(kw in text for kw in kws):
                    logger.info(f"REDIRECCIÓN: 'disponibilidad + ciudad + categoría' → search_businesses | cat={cat} | city={mentioned_city}")
                    return {"intent": "search_businesses", "args": {"category": cat, "city": mentioned_city}}
        
        # Extraer nombre del negocio
        # Caso 1: [disponibilidad] de [Negocio]
        biz_match = re.search(r"(?:disponibilidad|disponinilidad|agenda|citas?|turnos?|agendamientos?|espacio|hueco|cupo|horario)\s+(?:tienen\s+)?(?:registradas?\s+)?(?:para\s+hoy\s+|para\s+manana\s+)?(?:de|en|sobre|del negocio|de la empresa|del local|de la tienda|del|tiene)?\s*(.+)", text, re.IGNORECASE)
        biz_name = biz_match.group(1).strip() if biz_match else None
        
        # Caso 2: [Negocio] [disponibilidad] (Ej: "Delicias del Mar ver disponibilidad")
        if not biz_name or biz_name.lower() in ["hoy", "manana", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]:
            rev_match = re.search(r"(.+?)\s+(?:ver|consultar|mirar|chequear|saber|mostrar)?\s*(?:la\s+)?(?:disponibilidad|disponinilidad|agenda|citas?|turnos?|agendamientos?|horario)", text, re.IGNORECASE)
            if rev_match:
                biz_name = rev_match.group(1).strip()

        if biz_name:
            if biz_name.lower() in ["hoy", "manana", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]:
                biz_name = None
            else:
                for _ in range(2):
                    biz_name = re.sub(r'^(?:las|los|la|el|mi|mis|sus|este|esta|ese|esa|para|en|negocio|empresa|local|tienda|tienen|registradas?|hay|disponible|quiero|ver|del|de la)\s+', '', biz_name, flags=re.IGNORECASE).strip()
                # Quitar ciudad si está al final
                if mentioned_city:
                    biz_name = re.sub(rf'\b{mentioned_city}\b', '', biz_name, flags=re.IGNORECASE).strip()

        logger.info(f"DETECCION: intent='get_business_availability' -> '{biz_name}' | date='{mentioned_date}'")
        return {"intent": "get_business_availability", "args": {"business_name": biz_name, "date": mentioned_date}}

    # --- NUEVO: MISION/VISION ---
    if any(kw in text for kw in _MISSION_KEYWORDS):
        biz_match = re.search(r"(?:mision|vision|historia|quienes son|sobre ellos|acerca de)\s+(?:de|sobre|del negocio|de la empresa|del local|la empresa|el|del)?\s*(.+)", text, re.IGNORECASE)
        biz_name = biz_match.group(1).strip() if biz_match else None
        
        if biz_name:
            for _ in range(2):
                biz_name = re.sub(r'^(?:las|los|la|el|mi|mis|sus|este|esta|ese|esa|negocio|empresa|local|tienda|tienen|hay|disponible|quiero|ver|muestrame|muestra|conoce|sobre|acerca de)\s+', '', biz_name, flags=re.IGNORECASE).strip()
            _generic_terms = _MISSION_KEYWORDS + ["ellas", "ellos", "este", "ese", "ella", "el", "su", "sus", "negocio", "empresa", "local", "tienda", "este negocio", "esta empresa", "este local"]
            if biz_name.lower() in _generic_terms:
                biz_name = None

        logger.info(f"DETECCION: intent='get_business_mission_vision' -> '{biz_name}'")
        return {"intent": "get_business_mission_vision", "args": {"business_name": biz_name}}

    # --- NUEVO: BÚSQUEDA POR CATEGORÍA (Prioridad Crítica para evitar consumo de tokens) ---
    # Si detectamos una categoría canónica, es prioritario sobre servicios o regex.
    # Ej: "que barberias tienes" -> search_businesses(cat=barberia)
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in kws):
            logger.info(f"DETECCION: intent='search_businesses' (vía categoría canónica) | cat={cat} | city={mentioned_city}")
            return {
                "intent": "search_businesses",
                "args": {"category": cat, "city": mentioned_city}
            }
            
    # Búsqueda difusa para atrapar typos (ej. "barbebrias" -> "barberias")
    words = text.split()
    all_kws = []
    for cat, kws in _CATEGORY_KEYWORDS.items():
        all_kws.append(cat)
        all_kws.extend(kws)
    
    all_kws = [kw for kw in all_kws if len(kw) > 4]
    
    for word in words:
        if len(word) > 4:
            matches = difflib.get_close_matches(word, all_kws, n=1, cutoff=0.85)
            if matches:
                matched_kw = matches[0]
                matched_cat = matched_kw
                for cat, kws in _CATEGORY_KEYWORDS.items():
                    if matched_kw == cat or matched_kw in kws:
                        matched_cat = cat
                        break
                        
                logger.info(f"DETECCION: intent='search_businesses' (vía categoría FUZZY '{word}'->'{matched_kw}') | cat={matched_cat} | city={mentioned_city}")
                return {
                    "intent": "search_businesses",
                    "args": {"category": matched_cat, "city": mentioned_city}
                }

    # --- NUEVO: SERVICIOS Y PRECIOS ---
    # Usamos límites de palabra para evitar que "tienes" en "que barberias tienes" coincida 
    # si ya fue procesado como categoría.
    if any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in _SERVICES_KEYWORDS):
        biz_match = re.search(r"(?:servicio|servicios|servico|servicos|serviocio|serviocios|precio|precios|catalogo|cuanto cobran|que ofrecen|que venden|que ofrece|que tiene|que tienen|ofrece|ofrecen|tiene|tienen)\s+(?:\bde\b|\ben\b|sobre|del negocio|de la empresa|del local|\bdel\b)?\s*(.+)", text, re.IGNORECASE)
        biz_name = biz_match.group(1).strip() if biz_match else None
        
        if biz_name:
            # Limpiar verbos de acción que quedan pegados al nombre
            for _ in range(2):
                biz_name = re.sub(r'^(?:las|los|la|el|mi|mis|sus|este|esta|ese|esa|negocio|empresa|local|tienda|tienen|hay|disponible|quiero|ver|muestrame|muestra|catalogo|precios|servicios|ofrece|ofrecen|tiene|vende|venden|maneja|manejan)\s+', '', biz_name, flags=re.IGNORECASE).strip()
            _generic_terms = _SERVICES_KEYWORDS + ["ellas", "ellos", "este", "ese", "ella", "el", "su", "sus", "negocio", "empresa", "local", "tienda", "este negocio", "esta empresa", "este local", "catalogo", "servicios", "precios", "ofrece", "ofrecen", "tiene", "tienen"]
            if biz_name.lower() in _generic_terms:
                biz_name = None

        # Detectar subcategoría de servicio (postres, bebidas, comida típica, etc.)
        _SUBCATEGORY_MAP = {
            "postres": ["postre", "postres", "dulce", "dulces", "torta", "tortas", "pastel", "pasteles"],
            "bebidas": ["bebida", "bebidas", "trago", "tragos", "jugo", "jugos", "cerveza", "cervezas", "vino", "vinos", "limonada", "cafe", "coctel", "cocteles"],
            "comida_tipica": ["comida tipica", "tipica", "tipico", "regional", "comida criolla", "plato criollo", "plato tipico", "platos tipicos", "tradicional", "autoctono"],
            "entradas": ["entrada", "entradas", "aperitivo", "aperitivos"],
            "platos_fuertes": ["plato fuerte", "platos fuertes", "almuerzo", "almuerzos", "cena", "cenas", "plato principal"],
            "menu": ["menu", "carta", "la carta", "ver menu", "ver carta"],
        }
        subcategory = None
        for subcat, subcat_kws in _SUBCATEGORY_MAP.items():
            if any(kw in text for kw in subcat_kws):
                subcategory = subcat
                break

        logger.info(f"DETECCION: intent='get_business_services' -> '{biz_name}' | subcategory={subcategory}")
        return {"intent": "get_business_services", "args": {"business_name": biz_name, "subcategory": subcategory}}

    # --- NUEVO: SITIO WEB / REDES ---
    if any(kw in text for kw in _WEB_KEYWORDS):
        biz_match = re.search(r"(?:web|sitio web|pagina web|pagina|website|facebook|instagram|tiktok|redes|sociales|url|link|enlace)\s+(?:de|en|sobre|del negocio|de la empresa|del local|del)?\s*(.+)", text, re.IGNORECASE)
        biz_name = biz_match.group(1).strip() if biz_match else None
        
        if biz_name:
            for _ in range(2):
                biz_name = re.sub(r'^(?:las|los|la|el|mi|mis|sus|este|esta|ese|esa|negocio|empresa|local|tienda|tienen|hay|disponible|quiero|ver|muestrame|muestra|sitio|pagina|redes|sociales|perfil)\s+', '', biz_name, flags=re.IGNORECASE).strip()
            _generic_terms = _WEB_KEYWORDS + ["ellas", "ellos", "este", "ese", "ella", "el", "su", "sus", "negocio", "empresa", "local", "tienda", "este negocio", "esta empresa", "este local"]
            if biz_name.lower() in _generic_terms:
                biz_name = None

        logger.info(f"DETECCION: intent='open_business_web' -> '{biz_name}'")
        return {"intent": "open_business_web", "args": {"business_name": biz_name}}

    # --- NUEVO: RECOMENDACIONES ---
    if any(kw in text for kw in _RECOMMEND_KEYWORDS):
        # Intentar extraer la categoría si se menciona
        # Mejorado para capturar "recomiendame", "sugierenos", "mejor", "satisfaccion", etc.
        biz_match = re.search(r"(?:recomiend\w*|recomendacion\w*|sugier\w*|sugerencia\w*|mejores|mejor|top|populares|satisfaccion)\s+(?:una\s+|un\s+|la\s+|el\s+)?(?:mejor\s+)?(?:de\s+)?\s*(.+)", text, re.IGNORECASE)
        cat_name = biz_match.group(1).strip() if biz_match else ""
        
        # Fallback: Si el regex no capturó nada útil, buscamos categorías conocidas directamente en el texto
        if not cat_name:
            for cat, kws in _CATEGORY_KEYWORDS.items():
                if any(kw in text for kw in kws):
                    cat_name = cat
                    break

        if cat_name:
            cat_name = re.sub(r'^(una |un |la |el |mi |mis |sus |este |esta |ese |esa |en |de |del |sobre )\s*', '', cat_name, flags=re.IGNORECASE).strip()
            # Eliminar calificadores comunes que ensucian la búsqueda
            cat_name = re.sub(r'\s+(con mejor satisfaccion|con buena satisfaccion|mejor calificadas?|mejores|mejor|destacados?|populares?|segun mis gustos|segun mis preferencias|de la ciudad|de esta zona|en esa zona|de cali|de popayan|en cali|en popayan).*$', '', cat_name, flags=re.IGNORECASE).strip()
            
            # Limpiar si el nombre capturado es simplemente un artículo o palabra de relleno
            if cat_name.lower() in ["una", "un", "el", "la", "negocio", "empresa", "local", "tienda", "opcion", "opciones", "segun mis gustos", "mis gustos", "preferencias", "satisfaccion"]:
                cat_name = ""

        logger.info(f"DETECCION: intent='recommend_businesses' -> '{cat_name}'")
        return {"intent": "recommend_businesses", "args": {"category": cat_name, "city": mentioned_city}}

    # Detectar "ver [negocio] en el mapa" — Regex V2.4 (Prioridad sobre 'show_map')
    # Caso 1: "ver hotel en el mapa"
    fly_to_v1 = re.search(r"(?:ver|ubica|muestrame|donde esta|donde queda|enseñame|lleva|pon|busca|como llegar a|como llego a)\s+(.+?)\s+(?:en\s+el\s+mapa|en\s+mapa|mapa)", text)
    # Caso 2: "ver en el mapa hotel"
    fly_to_v2 = re.search(r"(?:ver|ubica|muestrame|donde esta|donde queda|enseñame|lleva|pon|busca|como llegar a|como llego a)\s+en\s+el\s+mapa\s+(.+)", text) or \
                re.search(r"(?:ver|ubica|muestrame|donde esta|donde queda|enseñame|lleva|pon|busca|como llegar a|como llego a)\s+en\s+mapa\s+(.+)", text) or \
                re.search(r"(?:donde queda|donde esta|como llegar a|como llego a)\s+(.+)", text)
    
    fly_match = fly_to_v1 or fly_to_v2
    if fly_match:
        biz_name = fly_match.group(1).strip()
        # Limpieza de conectores
        biz_name = re.sub(r'^(a |al |la |el |lo |de |del |mi |mis )\s*', '', biz_name, flags=re.IGNORECASE).strip()
        if len(biz_name) >= 2 and biz_name not in ["el", "la", "mi", "en"]:
            # SI el nombre es genérico como "negocios", es una búsqueda global (para traer cards al chat)
            if biz_name in ["negocios", "los negocios", "las empresas", "empresas"]:
                logger.info(f"DETECCION: intent='search_businesses' (genérico plural via fly-to) | city={mentioned_city}")
                return {"intent": "search_businesses", "args": {"category": "", "city": mentioned_city}}
                
            logger.info(f"DETECCION: intent='fly_to_business' -> '{biz_name}' | city={mentioned_city}")
            return {"intent": "fly_to_business", "args": {"business_name": biz_name, "city": mentioned_city}}

    # --- 2. INTENCIONES GENÉRICAS (UI/Navegación) ---
    # Volver al mapa / Mostrar mapa (Se checkea AL FINAL para evitar solapamiento)
    if any(kw in text for kw in _MAP_SHOW_KEYWORDS):
        logger.info("DETECCION: intent='show_map'")
        return {"intent": "show_map", "args": {}}

    # Fallback para "en el mapa" al final o nombres directos
    if "en el mapa" in text or "en mapa" in text:
        biz_name = text.replace("en el mapa", "").replace("en mapa", "").strip()
        for kw in ["ver", "ubica", "muestrame", "donde esta", "donde queda", "enseñame", "lleva", "pon", "busca"]:
            biz_name = biz_name.replace(kw, "").strip()
        biz_name = re.sub(r'^(a |al |la |el |lo |de |del |mi |mis )\s*', '', biz_name).strip()
        if len(biz_name) >= 2:
            return {"intent": "fly_to_business", "args": {"business_name": biz_name, "city": mentioned_city}}

    # --- 1. FILTRO ANTI-SPAM (PROTECCION DE TOKENS) ---
    if _is_spam(message):
        logger.warning(f"SPAM DETECTADO: '{message[:60]}' — bloqueado sin gastar tokens.")
        return {"intent": "spam"}

    # --- 2. GREETINGS & FAREWELLS (FAST) ---
    # Moved to FAST PATH at the beginning of the file

    # --- 3. OTROS COMANDOS DE MAPA ---
    # Detectar "muestra el mapa", "ver mapa", "abrir mapa"
    if any(kw in text for kw in _MAP_SHOW_KEYWORDS):
        has_category = any(any(kw in text for kw in kws) for kws in _CATEGORY_KEYWORDS.values())
        if not has_category:
            return {"intent": "show_map", "args": {}}

    # Detectar "ver todos en el mapa", "mostrar todos los negocios"
    if any(kw in text for kw in _MAP_FIT_KEYWORDS):
        return {"intent": "fit_all_businesses", "args": {}}

    # --- DETECCIÓN: ver/buscar catálogo por subcategoría sin intent de servicio explícito ---
    # Triggers: "qué comida típica ofrece", "ver postres", "qué bebidas tienen"
    _SUBCAT_DIRECT_MAP = {
        "postres": ["ver postres", "todos los postres", "postre", "postres", "ver los postres", "que postres"],
        "bebidas": ["ver bebidas", "que bebidas", "todas las bebidas", "ver las bebidas", "bebidas"],
        "comida_tipica": ["comida tipica", "que comida tipica", "platos tipicos", "ver comida tipica", "plato criollo", "comida criolla"],
        "menu": ["ver menu", "ver carta", "ver la carta", "que menu"],
    }
    for _subcat, _subcat_kws in _SUBCAT_DIRECT_MAP.items():
        if any(kw in text for kw in _subcat_kws):
            # Extraer nombre de negocio si hay ("qué postres tiene fogón criollo")
            _biz_after = re.search(r"(?:" + "|".join(re.escape(k) for k in _subcat_kws) + r")\s+(?:ofrece|tiene|tienen|de|del|en)\s+(.+)", text, re.IGNORECASE)
            _biz_name_subcat = _biz_after.group(1).strip() if _biz_after else None
            if _biz_name_subcat:
                _biz_name_subcat = re.sub(r'^(?:la|el|los|las|mi|un|una)\s+', '', _biz_name_subcat, flags=re.IGNORECASE).strip()
            logger.info(f"DETECCION: intent='get_business_services' (subcategoría directa) -> '{_biz_name_subcat}' | subcategory={_subcat}")
            return {"intent": "get_business_services", "args": {"business_name": _biz_name_subcat, "subcategory": _subcat}}

    # --- 3. NAVEGACIÓN DIRECTA ---
    # Detectar navegación a admin (ej: llevame a inventario)
    nav_admin_pattern = r"\b(lleva|vamos|llevame|llévame|les?go|les'?t?\s*go|let'?s\s*go|navega|ir a|ir al|ir a la|abre|abrir|ver|dirigeme|redirige|redirigueme|entrar)\b\s+(.*)"
    admin_nav_match = re.search(nav_admin_pattern, text)
    if admin_nav_match:
        target_text = admin_nav_match.group(2).strip()
        for url, kws in _ADMIN_NAV_TARGETS.items():
            if any(kw in target_text or kw == target_text for kw in kws):
                return {
                    "intent": "admin_navigate",
                    "args": {"url": url, "name": kws[0].capitalize()}
                }

    # Navegación general de clientes (Ir a negocio X) - YA CUBIERTO ARRIBA en profile_match
    # Pero mantenemos una versión simplificada por si acaso
    if re.search(r"\b(ir a|ver perfil de|ver perfil|llevar?me a|visitar)\b\s+(.+)", text, re.IGNORECASE):
        # Evitar recursividad si ya se detectó arriba
        pass

    nav_pattern = r"\b(lleva|vamos|llevame|llévame|les?go|les'?t?\s*go|let'?s\s*go|navega|ir a|ver perfil|perfiles|seccion|sección|dirigeme|redirige|redirigueme|redirigieras|verla|verlo|mirarla|mirarlo|mirar|entrar|ir)\b"
    if re.search(nav_pattern, text):
        return {"intent": "confirm_navigation", "args": {}}
    
    # Confirmaciones (sí, dale, ok) — también variantes repetidas como "sí sí", "dale dale"
    _CONF_WORDS = {"si", "dale", "vale", "ok", "okay", "listo", "claro",
                   "proceder", "vaya", "acepto", "confirmo", "anda", "perfecto", "bueno"}
    words_in_text = set(text.split())
    if words_in_text and words_in_text.issubset(_CONF_WORDS):
        return {"intent": "confirm_general", "args": {}}
    
    first_word = text.split()[0] if text.split() else ""
    if first_word in _CONF_WORDS:
        # PRIORIDAD: Si el texto contiene frases de navegación/exploración, es confirm_navigation
        # Ej: "sí quiero ver las opciones en Cali" → confirm_navigation, NO request_appointment
        _confirm_show_phrases = [
            "dejame ver", "déjame ver", "muéstrame", "muestrame", "enséñame", "enseñame",
            "quiero ver", "quiero verlos", "quiero verlas", "si hay", "a ver", "veamos",
            "cuáles hay", "cuales hay", "los que hay", "las que hay",
            "ver las opciones", "ver los negocios", "ver las barberias", "opciones en", "las opciones"
        ]
        if any(phrase in text for phrase in _confirm_show_phrases):
            return {"intent": "confirm_navigation", "args": {}}

        # SI el texto contiene también una hora o servicio, es una confirmación de cita (ConfirmAppointment)
        time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        service_name = _extract_service_name(text)
        
        if time_match or service_name:
            time_str = None
            if time_match:
                h = int(time_match.group(1))
                m = time_match.group(2) or "00"
                p = time_match.group(3)
                if p == "pm" and h < 12: h += 12
                time_str = f"{h:02d}:{m}"
            
            logger.info(f"DETECCION: intent='request_appointment' (vía confirmación) | time='{time_str}' | srv='{service_name}'")
            return {"intent": "request_appointment", "args": {"business_name": None, "time": time_str, "service_name": service_name}}

        # SI el texto después del sí tiene una categoría de negocio → es una nueva búsqueda
        rest_of_text = " ".join(text.split()[1:])
        for cat, kws in _CATEGORY_KEYWORDS.items():
            if any(kw in rest_of_text for kw in kws):
                logger.info(f"DETECCION: intent='search_businesses' (confirmación con categoría) | cat={cat} | city={mentioned_city}")
                return {"intent": "search_businesses", "args": {"category": cat, "city": mentioned_city}}

        if len(text.split()) <= 4:
            return {"intent": "confirm_navigation", "args": {}}

    # --- INTERCEPTAR CONVERSACIÓN CONTINUADA ---
    # Si el usuario hace referencia a algo de lo que ya se hablaba ("esa", "eso", "esa opcion")
    _conversation_continuations = [
        "esa opcion", "esa opción", "ese negocio", "esa empresa", "ese", "esa", "eso",
        "la recomendacion", "lo que me dices", "lo que dijiste",
        "que hay disponibles", "que tienes disponibles", "que opciones tienes",
        "las disponibles", "los disponibles", "que hay", "cuales son"
    ]
    if any(re.search(r"\b" + kw + r"\b", text) for kw in _conversation_continuations):
        # Si también incluye palabras de navegación, confirmar
        if any(v in text for v in ["ver", "ir", "lleva", "muestra", "mostrar", "quiero", "vamos", "visitar", "abrir"]):
            logger.info("DETECCION: intent='confirm_navigation' (continuación de contexto navegable)")
            return {"intent": "confirm_navigation", "args": {}}
        
        logger.info("DETECCION: intent='conversation' (continuación de contexto)")
        return {"intent": "conversation"}

    # --- 2. AYUDA SOBRE NEXISERVICE (Local / 0 Tokens) ---
    # Solo si NO hay también una categoría de negocio en el texto (ej: "cuántas barberias hay en nexiservice")
    has_category_in_text = any(any(kw in text for kw in kws) for kws in _CATEGORY_KEYWORDS.values())
    if not has_category_in_text and ("nexiservice" in text or "que es esta app" in text or "para que sirve" in text):
        return {"intent": "get_general_info", "args": {"topic": "nexiservice"}}

    # --- 3. SELECCIÓN POR NOMBRE / ENTIDAD ---
    # Captura nombres directos como "Barbería VIP" o "Hotel Paraíso"
    known_businesses = ["barberia vip", "hotel paraiso", "tienda tech"]
    for biz in known_businesses:
        if biz in text:
            return {
                "intent": "search_businesses",
                "args": {"category": biz, "city": mentioned_city}
            }
            
    # Caso directo para inventario si dice solo "inventario", "pos", etc.
    if len(text.split()) <= 3:
        for url, kws in _ADMIN_NAV_TARGETS.items():
            if any(kw == text or kw in text for kw in kws):
                return {
                    "intent": "admin_navigate",
                    "args": {"url": url, "name": kws[0].capitalize()}
                }

    # FIX 2A: Composites de intención directa (Ej: "quiero hacer una reserva")
    _APPOINTMENT_COMPOSITE = [
        "quiero hacer una reserva", "hacer una reserva", "necesito una cita", 
        "sacar una cita", "quiero agendar", "necesito agendar", "hacer reserva",
        "hacer cita", "pedir cita", "pedir turno", "agendar una cita"
    ]
    if any(comp in text for comp in _APPOINTMENT_COMPOSITE):
        logger.info("DETECCION: intent='request_appointment' (vía composite natural)")
        return {"intent": "request_appointment", "args": {"business_name": None, "time": None, "service_name": None, "date": mentioned_date, "professional_name": None}}

    # --- 4. AYUDA / INFORMACIÓN ---
    precise_help_keywords = _HELP_KEYWORDS + ["donde queda", "como puedo", "como funciona", "como agendar", "agendar", "cita", "reserva"]
    help_pattern = r"\b(" + "|".join(precise_help_keywords) + r")\b"
    if re.search(r"^(como|donde|que|para que)\b", text) or any(kw in text for kw in ["ayuda", "informacion", "agendar"]):
        if re.search(help_pattern, text):
            return {
                "intent": "get_general_info",
                "args": {"topic": text}
            }

    # --- 6. EXTRACCIÓN POR PATRÓN (SELECT PATTERN) ---
    select_pattern = r"\b(e[lnñ]\s+de|ver\s+a|ver\s+el|ver\s+la|quien\s+es|que\s+es|busca\s+a|muestrame\s+a|donde\s+esta|donde\s+queda|info\s+de|info\s+sobre|informacion\s+de|necesito|quiero|busco)\b\s+(.+)"
    match = re.search(select_pattern, text)
    if match:
        full_payload = match.group(2).strip()
        entity_name = re.split(r'\s+(me\s+parece|es\s+|esta\s+|que\s+|en\s+|para\s+|con\s+|y\s+)', full_payload)[0].strip()
        
        # Intentar resolver entity_name a una categoría canónica (redundante pero seguro)
        resolved_cat = None
        for cat, kws in _CATEGORY_KEYWORDS.items():
            if any(kw in entity_name for kw in kws):
                resolved_cat = cat
                break
        
        # FIX 2B: Guarda de seguridad para entidades ambiguas
        if any(kw in entity_name.lower() for kw in ["reserva", "turno", "cita"]):
            logger.info(f"DETECCION: intent='request_appointment' (vía guarda de select_pattern) -> {entity_name}")
            return {"intent": "request_appointment", "args": {"business_name": None, "time": None, "service_name": None, "date": mentioned_date, "professional_name": None}}

        if resolved_cat:
            logger.info(f"DETECCION: intent='search_businesses' (select-pattern cat resuelto) | cat={resolved_cat} | city={mentioned_city}")
            return {"intent": "search_businesses", "args": {"category": resolved_cat, "city": mentioned_city}}
        
        # Ignorar entidades genéricas que deberían ir al LLM (ej. "quiero ver esa opcion")
        _generic_entity_words = ["ver", "negocios", "negocio", "empresas", "empresa", "locales", "local",
                                  "esa", "ese", "esos", "esas", "opcion", "opciones", "alternativa", "alternativas",
                                  "recomendacion", "recomendaciones"]
        is_generic = all(word in _generic_entity_words for word in entity_name.split())

        if len(entity_name) >= 2 and not is_generic:
            logger.info(f"DETECCION: intent='search_businesses' (select-pattern entity) | cat={entity_name} | city={mentioned_city}")
            return {
                "intent": "search_businesses",
                "args": {"category": entity_name, "city": mentioned_city}
            }

    
    # --- 7. DIRECTORIO / BÚSQUEDA GENERAL ---
    dir_pattern = r"\b(negocios?|empresas?|lista|que hay|que ofrece|donde hay|directorio|mostrar|ver todo|opciones|reproduce|visitar|visita|abre|abrir|ir|vamos|ver)\b"

    # Catch-all para "ver [algo] en [ciudad]" o "[algo] en [ciudad]"
    search_re = re.search(r"\b(ver|buscar|encuentra|donde hay|mostrar|abre|abrir|visitar|visita|ir a|donde queda)\s+(?:las?\s+|los?\s+)?(.+?)(?:\s+en\s+([a-zA-ZáéíóúÁÉÍÓÚ\s]+))?$", text)
    if search_re:
        cat_candidate = search_re.group(2).strip()
        cat_candidate = re.sub(r'^(la empresa de|el negocio de|empresa de|negocio de|la empresa|el negocio|la|el|los|las|de|del|un|una)\s+', '', cat_candidate, flags=re.IGNORECASE).strip()
        
        city_candidate = search_re.group(3).strip() if search_re.group(3) else mentioned_city
        
        # Si no es una de las keywords de ayuda, lo tomamos como búsqueda de negocio
        _generic_entity_words = [
            "esa", "ese", "esos", "esas", "opcion", "opciones", "alternativa", "alternativas",
            "recomendacion", "recomendaciones", "negocio", "empresa", "local", "cuales", "cuál",
            "cuales", "disponibles", "disponible", "hay", "tiene", "tienes", "existen", "ver",
            "mostrar", "los", "las", "que", "los que", "las que", "esos que", "esas que"
        ]
        is_generic_cat = (
            all(word in _generic_entity_words for word in cat_candidate.split()) or
            # Detectar frases interrogativas/conversacionales como "cuales hay disponibles"
            any(w in cat_candidate.lower() for w in ["cuales", "cuáles", "hay disponibles", "que hay", "que tienes", "los que hay"])
        )
        
        if cat_candidate not in _HELP_KEYWORDS and len(cat_candidate.split()) < 4 and not is_generic_cat:
            # Intentar resolver el candidato a una categoría canónica
            resolved_cat = None
            for cat, kws in _CATEGORY_KEYWORDS.items():
                if any(kw in cat_candidate for kw in kws):
                    resolved_cat = cat
                    break
            final_cat = resolved_cat or cat_candidate
            logger.info(f"DETECCION: intent='search_businesses' (regex-match) | cat={final_cat} | city={city_candidate}")
            return {"intent": "search_businesses", "args": {"category": final_cat, "city": city_candidate}}

    # --- NAVEGACIÓN Y BÚSQUEDA (ESPECÍFICO DE NEXISERVICE) ---
    if project_id == "nexiservice":
        # Catch-all para "en [Negocio]" (ej: "en fs", "en barberia vip")
        if re.search(r"^en\s+(.+)$", text) and len(text.split()) <= 4:
            biz_name = re.search(r"^en\s+(.+)$", text).group(1).strip()
            # Si no es una ciudad, asumimos negocio
            if not _extract_city(biz_name):
                 logger.info(f"DETECCION: intent='navigate_to_company' (vía 'en X') -> '{biz_name}' | city={mentioned_city}")
                 return {"intent": "navigate_to_company", "args": {"business_name": biz_name, "city": mentioned_city}}

        # Catch-all para búsquedas directas por nombre de negocio (ej: "fogon criollo norte")
        _exclude_words = _HELP_KEYWORDS + _GREETING_KEYWORDS + _SERVICES_KEYWORDS + ["ver", "buscar", "mostrar", "ir", "lleva"]
        has_exclude = any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in _exclude_words)
        if len(text.split()) <= 6 and not has_exclude:
            logger.info(f"DETECCION: intent='navigate_to_company' (vía nombre directo) -> '{text}'")
            return {"intent": "navigate_to_company", "args": {"business_name": text, "city": mentioned_city}}

        if re.search(dir_pattern, text):
            logger.info(f"DETECCION: intent='search_businesses' (genérica/directorio) | city={mentioned_city}")
            return {"intent": "search_businesses", "args": {"category": "", "city": mentioned_city}}

        # --- 8. ÚLTIMO RECURSO: NOMBRE DIRECTO (NEXISERVICE) ---
        # Si el texto es muy corto (1-2 palabras) y no es una confirmación ni saludo,
        # probablemente sea un nombre de negocio que el usuario escribió solo.
        # Excluimos palabras reservadas.
        if len(text.split()) <= 2:
            _stop_words = {"si", "no", "ok", "vale", "dale", "ver", "ir", "mas", "menos", "mapa", "hola", "chao", "bye", "gracias", "por", "fa", "esta", "ese", "eso", "esa", "este", "uno", "dos", "tres"}
            # Excluir si parece una hora
            is_time = re.search(r'\d{1,2}(?::\d{2})?\s*(?:am|pm)?', text, re.IGNORECASE)
            # Excluir si es una ciudad conocida
            is_city = _extract_city(text)
            
            if text not in _stop_words and any(c.isalpha() for c in text) and not is_time and not is_city:
                logger.info(f"DETECCION: intent='navigate_to_company' (nombre directo) -> '{text}' | city={mentioned_city}")
                return {"intent": "navigate_to_company", "args": {"business_name": text, "city": mentioned_city}}

    return {"intent": None}
