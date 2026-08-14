"""
tools/nexiservice.py — Tool functions for NexiService Colombia.

search_businesses: busca por TÍTULO de empresa + CATEGORÍA asignada.
La búsqueda es tolerante a sinónimos, errores ortográficos y lenguaje informal.
"""

import logging
import unicodedata
import math
import re
from typing import Optional
from core.config import settings
from tools.shared.utils import normalize_text as _normalize_shared

logger = logging.getLogger("lyra.tools.nexiservice")

# Registro local de ciudades principales para evitar rate-limits de Nominatim
COLOMBIA_CITIES_COORDS = {
    "popayan": (2.4411, -76.6063, "Popayán"),
    "cali": (3.4516, -76.5320, "Cali"),
    "bogota": (4.7110, -74.0721, "Bogotá"),
    "medellin": (6.2442, -75.5812, "Medellín"),
}

GEO_CACHE = {}


def _normalize(text: str) -> str:
    """Thin wrapper — delegates to tools.shared.utils.normalize_text with punctuation stripping."""
    return _normalize_shared(text, strip_punctuation=True)


def _clean_search_query(text: str) -> str:
    """
    Limpia de forma agresiva una consulta de búsqueda para extraer el núcleo (nombre o categoría).
    Elimina verbos, artículos, prefijos de intención y muletillas.
    """
    import re

    t = _normalize(text)
    if not t:
        return ""

    # 1. Eliminar prefijos de intención/acción comunes
    prefixes = [
        r"^(ver|buscar|abrir|abre|visitar|visita|ir a|llevame a|necesito|quiero|muestrame|ensename|donde esta|donde queda)\s+",
        r"^(sitio web de|pagina web de|redes sociales de|perfil de|biografia de|historia de|mision de|vision de|servicios de|productos de|catalogo de)\s+",
        r"^(que es|quien es|como es|que tal es|donde hay|donde queda el|donde queda la)\s+",
        r"^(la empresa de|el negocio de|empresa de|negocio de|la empresa|el negocio|la|el|los|las|un|una|al|a)\s+",
        r"^(web de|facebook de|instagram de|tiktok de|whatsapp de)\s+",
    ]
    for p in prefixes:
        t = re.sub(p, "", t, flags=re.IGNORECASE).strip()

    # 2. Eliminar ciudades al inicio o final para que no interfieran con el LIKE del nombre
    t = re.sub(
        r"\b(popayan|cali|bogota|medellin|santander|quilichao)\b",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()

    # 3. Eliminar stop words sueltas
    stop_words = {
        "de",
        "del",
        "en",
        "con",
        "por",
        "para",
        "un",
        "una",
        "el",
        "la",
        "los",
        "las",
    }
    parts = [p for p in t.split() if p not in stop_words]

    return " ".join(parts).strip()


def _format_logo(path: str) -> str:
    """Format business logo to absolute URL."""
    if not path:
        return "https://nexiservice.com/assets/default-logo.png"
    if path.startswith("http"):
        return path

    # El backend de inventario está en el puerto 8002 según metadata
    base = "http://localhost:8002"

    # Limpiar path (quitar public/ y slashes iniciales)
    clean_path = path.replace("public/", "").lstrip("/")

    # En Laravel, si no está en public directo, suele estar en storage/ (vía symlink)
    if not clean_path.startswith("storage/"):
        clean_path = "storage/" + clean_path

    return f"{base}/{clean_path}"


async def _get_active_cities_data() -> list[dict]:
    """Retorna lista de ciudades con presencia REAL de negocios en NexiService (dinámico desde DB)."""
    from core.database import get_connection

    POPAYAN_BBOX = {
        "min_lat": 2.32,
        "max_lat": 2.58,
        "min_lng": -76.82,
        "max_lng": -76.42,
    }
    CALI_BBOX = {"min_lat": 3.33, "max_lat": 3.51, "min_lng": -76.58, "max_lng": -76.45}

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # Popayán: negocios en BBOX + negocios sin coords (pertenecen a Popayán por defecto)
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT id) as cnt FROM empresa
                    WHERE (latitud BETWEEN %s AND %s AND longitud BETWEEN %s AND %s)
                       OR latitud IS NULL OR longitud IS NULL
                """,
                    (
                        POPAYAN_BBOX["min_lat"],
                        POPAYAN_BBOX["max_lat"],
                        POPAYAN_BBOX["min_lng"],
                        POPAYAN_BBOX["max_lng"],
                    ),
                )
                popcnt = (cur.fetchone() or {}).get("cnt", 0)

                cur.execute(
                    """
                    SELECT COUNT(DISTINCT id) as cnt FROM empresa
                    WHERE latitud BETWEEN %s AND %s AND longitud BETWEEN %s AND %s
                """,
                    (
                        CALI_BBOX["min_lat"],
                        CALI_BBOX["max_lat"],
                        CALI_BBOX["min_lng"],
                        CALI_BBOX["max_lng"],
                    ),
                )
                calicnt = (cur.fetchone() or {}).get("cnt", 0)

                return [
                    {
                        "name": "Popayan",
                        "lat": 2.4411,
                        "lng": -76.6063,
                        "biz_count": popcnt,
                    },
                    {
                        "name": "Cali",
                        "lat": 3.4516,
                        "lng": -76.5320,
                        "biz_count": calicnt,
                    },
                ]
    except Exception as e:
        logger.error(f"Error fetching active cities: {e}")
        return [
            {"name": "Popayan", "lat": 2.4411, "lng": -76.6063, "biz_count": 5},
            {"name": "Cali", "lat": 3.4516, "lng": -76.5320, "biz_count": 5},
        ]


async def get_general_info(
    topic: str = "",
    role: str = "client",
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> dict:
    """
    Proporciona información de ayuda filtrada por el rol del usuario.
    """
    ADMIN_KNOWLEDGE = {
        "venta": "La gestión de ingresos y pedidos se centraliza en el módulo de 'Ventas' de nexiserviceAdminReact.",
        "personal": "El control de nómina y colaboradores se encuentra en 'Gestión de Personal' dentro del panel administrativo.",
        "compra": "El abastecimiento y relación con proveedores se maneja desde el módulo de 'Compras'.",
        "inventario": "La administración de existencias, catálogos y menús se realiza en 'backendInvent'.",
        "cliente": "La base de datos de usuarios y seguimientos se gestiona en 'backNexiservicClientes'.",
        "configuracion": "Los ajustes generales de la empresa y permisos se encuentran en el panel de nexiserviceAdminReact.",
        "dashboard": "El resumen ejecutivo de métricas y rendimiento está disponible en el inicio del panel administrativo.",
    }

    CLIENT_KNOWLEDGE = {
        "catalogo": "Explora el catálogo de productos y menús desde el perfil interactivo de cada negocio en la web.",
        "producto": "Contamos con una amplia variedad de productos locales disponibles para consulta inmediata.",
        "negocio": "Nuestra plataforma agrupa Barberías, Restaurantes, Clínicas, Hoteles y más en un solo directorio centralizado.",
        "cita": "Puedes gestionar y agendar tus citas directamente desde la pestaña 'Servicios' del negocio que elijas.",
        "agendar": "Puedes gestionar y agendar tus citas directamente desde la pestaña 'Servicios' del negocio que elijas.",
        "servicio": "Ofrecemos visibilidad completa de los servicios locales, con descripción, precios y disponibilidad en tiempo real.",
        "web": "Muchos negocios tienen enlaces a sus redes sociales (Instagram, Facebook) en sus perfiles públicos.",
        "redes": "Puedes ver las redes sociales de un negocio desde su perfil o pidiéndomelo directamente.",
        "nexiservice": "NexiService es el ecosistema digital líder en Colombia para conectar usuarios con servicios locales de alta calidad.",
        "ubicacion": "Puedes ver la dirección exacta y mapa de cada negocio desde su perfil público.",
        "contacto": "La plataforma permite contactar a los negocios vía WhatsApp o email directamente desde sus perfiles.",
        "registro": "Para registrarte, haz clic en 'Iniciar sesión' y luego en 'Crear una cuenta'. Solo necesitarás tu nombre, correo y una contraseña para empezar.",
    }

    knowledge = CLIENT_KNOWLEDGE.copy()
    if role == "admin":
        knowledge.update(ADMIN_KNOWLEDGE)

    topic_clean = _normalize(topic or "")

    # Respuesta por defecto según rol
    if role == "admin":
        default_message = "Tienes acceso completo a los módulos de administración (Ventas, Personal, Inventario) y al portal público."
    else:
        default_message = "Bienvenido a NexiService.\n\nSoy Lyra, y estoy aquí para facilitarte la búsqueda de negocios locales, la revisión de servicios disponibles y el agendamiento de citas de forma rápida y sencilla. ¿En qué área te gustaría explorar hoy?"

    message = default_message

    for key, val in knowledge.items():
        if _normalize(key) in topic_clean or topic_clean in _normalize(key):
            message = "Con gusto te brindo esa información:\n\n" + val
            break

    if not topic_clean:
        message = f"¿En qué puedo ayudarte hoy? {default_message}"

    return {"success": True, "topic": topic, "message": message}


def _format_logo(path):
    """Formatea la ruta del logo para que sea accesible desde el frontend."""
    if not path:
        return None
    if path.startswith("http"):
        return path
    # El backend de inventario está en el puerto 8002 según metadata
    base = "http://localhost:8002"
    # Limpiar path si empieza con public/
    clean_path = path.replace("public/", "")
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    return f"{base}{clean_path}"


#: Una consulta con más de esto ya no nombra una cosa: es una oración. Ningún
#: negocio ni servicio del catálogo se llama con tantas palabras de contenido.
_MAX_QUERY_WORDS = 4


def _looks_like_sentence(query: str) -> bool:
    """
    True si la consulta parece una frase conversacional y no el nombre de algo.

    Es la última barrera contra el defecto que originó este trabajo: que
    "que me puedes ofrecer" llegara a la base como `%que%me%puedes%ofrecer%`.
    La comprensión semántica ya debería impedirlo antes, pero la herramienta es
    pública y cualquier ruta (LLM incluido) puede invocarla.
    """
    words = [w for w in (query or "").split() if w]
    if len(words) > _MAX_QUERY_WORDS:
        return True
    # Un verbo conjugado en 1ª o 2ª persona indica que esto es habla, no un
    # nombre. Se comprueba con la morfología, no con una lista de frases.
    from core.semantic.lexicon import FUNCTION_STEMS
    from core.semantic.morphology import phonetic_stem

    return any(phonetic_stem(w) in FUNCTION_STEMS for w in words)


async def search_businesses(
    category: str = "",
    near_me: bool = False,
    user_lat: float = None,
    user_lng: float = None,
    city: str = None,
    active_city: str = None,
    grounded: bool = False,
    **kwargs,
) -> dict:
    """
    Busca negocios en la base de datos real por TÍTULO (razonSocial) y CATEGORÍA asignada.
    Soporta priorización geográfica: mención explícita > ubicación GPS > ciudad activa.

    `grounded=True` declara que `category` ya proviene del catálogo (lo garantiza
    la capa de comprensión). Sin esa garantía, una consulta que parece una frase
    hablada se rechaza en vez de convertirse en un LIKE literal.
    """
    from core.database import get_connection

    import re

    # --- LIMPIEZA AGRESIVA DE LA CONSULTA ---
    query_norm = _clean_search_query(category or "")

    # --- GUARDA: la consulta debe nombrar algo, no ser una frase ---
    if query_norm and not grounded and _looks_like_sentence(query_norm):
        logger.warning(
            "search_businesses rechazó una consulta conversacional: %r", category
        )
        return {
            "success": False,
            "unresolved_query": True,
            "message": (
                "No logré identificar qué negocio o servicio necesitas. "
                "¿Me lo puedes decir con otras palabras?"
            ),
        }

    # CASE: Búsqueda Global (si el usuario pregunta "¿qué hay?" o "lista de empresas")
    is_global_search = not query_norm or query_norm in {
        "todo",
        "todos",
        "empresas",
        "que hay",
        "opciones",
        "negocios",
    }

    # --- MAPAS DE BÚSQUEDA ---
    title_keywords_map = {
        "barberia": ["barber", "peluquer", "barbershop", "tijeras", "cortes"],
        "peluqueria": ["peluquer", "salon de belleza", "estilista"],
        "corte": ["corte de cabello", "peluquer", "barber"],
        "hotel": [
            "hotel",
            "hosped",
            "motel",
            "cabaña",
            "alojam",
            "hostal",
            "habitacion",
        ],
        "restaurante": [
            "restaur",
            "comida",
            "pizz",
            "hamburgues",
            "cocina",
            "asado",
            "parrilla",
            "gastronomia",
        ],
        "comida": ["restaur", "comida", "pizz", "hamburgues", "cocina"],
        "cancha": ["cancha", "futbol", "deport", "poliedro"],
        "medico": ["medic", "clinic", "salud", "odont", "doctor"],
        "doctor": ["medic", "clinic", "salud", "odont", "doctor"],
        "odontologo": ["odont", "dental", "dentist"],
        "salon": ["salon de eventos", "recepciones"],
        "taller": ["taller", "mecanico", "carro", "moto", "reparacion"],
        "gym": ["gym", "gimnasio", "entrenar", "pesas", "fitness"],
    }

    category_map = {
        "barberia": ["barber", "peluquer"],
        "peluqueria": ["peluquer", "estilista"],
        "corte": ["barber", "peluquer"],
        "hotel": ["habitacion", "hospedaje", "reserva de habitacion"],
        "restaurante": ["reserva de mesa", "restaur", "gastronomia"],
        "comida": ["reserva de mesa", "restaur"],
        "cancha": ["canchas", "reserva de cancha", "deport"],
        "medico": ["citas mediticas", "medic", "salud", "odont"],
        "doctor": ["citas mediticas", "medic"],
        "odontologo": ["citas mediticas", "odont", "dental"],
        "salon": ["salon", "event"],
        "taller": ["mecanica", "repuestos", "taller"],
        "gym": ["entrenamiento", "fitness", "gimnasio"],
    }

    # Elegimos el key que mejor coincida con el query
    best_key = None
    if not is_global_search:
        for k in title_keywords_map:
            if k in query_norm or query_norm in k:
                best_key = k
                break

    # Mapa de exclusión (si el nombre contiene esto, y estamos buscando X, excluye)
    # Útil para filtrar "Gimnasio" de la categoría "BARBERIAS"
    exclusion_map = {
        "barberia": [
            "gimnasio",
            "gym",
            "restaurante",
            "cafe",
            "optica",
            "supermercado",
            "drogueria",
            "ferreteria",
            "panaderia",
            "centro comercial",
            "mall",
        ],
        "peluqueria": [
            "gimnasio",
            "gym",
            "restaurante",
            "cafe",
            "optica",
            "supermercado",
            "drogueria",
            "ferreteria",
            "panaderia",
            "centro comercial",
            "mall",
        ],
        "corte": [
            "gimnasio",
            "gym",
            "restaurante",
            "cafe",
            "optica",
            "supermercado",
            "drogueria",
            "ferreteria",
            "panaderia",
            "centro comercial",
            "mall",
        ],
        "restaurante": ["barberia", "peluqueria", "gym", "gimnasio", "hotel"],
    }
    negative_kws = exclusion_map.get(best_key, []) if best_key else []

    title_kws = (
        title_keywords_map.get(best_key, [query_norm]) if best_key else [query_norm]
    )
    cat_kws = category_map.get(best_key, [query_norm]) if best_key else [query_norm]

    # IMPORTANTE: Si es una búsqueda específica (best_key exists), recordamos los keywords para refinar el resultado
    # ya que la base de datos tiene mucha data contaminada (negocios con categoría "BARBERIAS" que son "Gimnasios").
    refinement_kws = []
    if best_key:
        refinement_kws = list(set(title_kws + cat_kws))

    if query_norm and query_norm not in title_kws:
        title_kws.append(query_norm)

    # Preparamos los parámetros del LIKE: si hay varias palabras, las unimos con % para mayor flexibilidad
    # Ej: "delicias mar" -> "%delicias%mar%"
    db_like_params = []
    for kw in title_kws:
        db_like_params.append(f"%{kw.replace(' ', '%')}%")
    for kw in cat_kws:
        db_like_params.append(f"%{kw.replace(' ', '%')}%")

    # --- Bounding Boxes para detección y filtrado de ciudad ---
    POPAYAN_BBOX = {
        "min_lat": 2.32,
        "max_lat": 2.58,
        "min_lng": -76.82,
        "max_lng": -76.42,
    }
    CALI_BBOX = {"min_lat": 3.33, "max_lat": 3.51, "min_lng": -76.58, "max_lng": -76.45}
    CITIES_BBOX = {"Popayan": POPAYAN_BBOX, "Cali": CALI_BBOX}

    # --- LÓGICA DE INTELIGENCIA GEOGRÁFICA ---
    resolved_city = city or active_city or "Popayan"
    city_norm = _normalize(resolved_city)

    # Delegamos la resolución completa (local → cache → Nominatim → fallback) al servicio geo
    from services.geo import resolve_city_coords_async

    city_lat_fixed, city_lng_fixed, official_name = await resolve_city_coords_async(
        resolved_city
    )

    current_city = official_name

    results = []
    has_more = False
    suggestion_msg = None
    suggested_next_city = None
    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                dist_sql = ""

                center_lat, center_lng = None, None
                if near_me and user_lat and user_lng:
                    center_lat, center_lng = user_lat, user_lng
                elif city_lat_fixed and city_lng_fixed:
                    center_lat, center_lng = city_lat_fixed, city_lng_fixed
                elif user_lat and user_lng:
                    center_lat, center_lng = user_lat, user_lng

                if center_lat and center_lng:
                    dist_sql = f""",
                        (6371 * acos(
                            LEAST(1.0, GREATEST(-1.0, 
                                cos(radians({center_lat})) * cos(radians(e.latitud)) * 
                                cos(radians(e.longitud) - radians({center_lng})) + 
                                sin(radians({center_lat})) * sin(radians(e.latitud))
                            ))
                        )) AS distance_km"""

                where_clauses = []
                db_params = []
                city_filter = ""
                if near_me and user_lat and user_lng:
                    # Si el usuario pide cercanía, limitamos a 20km a la redonda y anulamos el filtro estricto de ciudad.
                    city_filter = f"AND (6371 * acos(LEAST(1.0, GREATEST(-1.0, cos(radians({user_lat})) * cos(radians(e.latitud)) * cos(radians(e.longitud) - radians({user_lng})) + sin(radians({user_lat})) * sin(radians(e.latitud)))))) <= 20"
                elif resolved_city:
                    # POPAYAN es la ciudad raíz del sistema: negocios sin coords (lat IS NULL) pertenecen a Popayán por defecto
                    IS_HOME_CITY = city_norm in ("popayan", "popayan")
                    # Lookup normalizado para ignorar acentos/mayúsculas en las claves del BBOX
                    CITIES_BBOX_NORM = {
                        _normalize(k): v for k, v in CITIES_BBOX.items()
                    }
                    if city_norm in CITIES_BBOX_NORM:
                        bbox = CITIES_BBOX_NORM[city_norm]
                        if IS_HOME_CITY:
                            city_filter = f"AND ((e.latitud BETWEEN {bbox['min_lat']} AND {bbox['max_lat']} AND e.longitud BETWEEN {bbox['min_lng']} AND {bbox['max_lng']}) OR e.latitud IS NULL OR e.longitud IS NULL)"
                        else:
                            city_filter = f"AND (e.latitud BETWEEN {bbox['min_lat']} AND {bbox['max_lat']} AND e.longitud BETWEEN {bbox['min_lng']} AND {bbox['max_lng']})"
                    elif city_lat_fixed and city_lng_fixed:
                        if (
                            official_name
                            and official_name.lower() != resolved_city.lower()
                        ):
                            logger.info(
                                f"Fuzzy match city: {resolved_city} -> {official_name}"
                            )
                        if IS_HOME_CITY:
                            city_filter = f"AND ((e.latitud BETWEEN {city_lat_fixed - 0.1} AND {city_lat_fixed + 0.1} AND e.longitud BETWEEN {city_lng_fixed - 0.1} AND {city_lng_fixed + 0.1}) OR e.latitud IS NULL OR e.longitud IS NULL)"
                        else:
                            city_filter = f"AND (e.latitud BETWEEN {city_lat_fixed - 0.1} AND {city_lat_fixed + 0.1} AND e.longitud BETWEEN {city_lng_fixed - 0.1} AND {city_lng_fixed + 0.1})"
                    else:
                        city_filter = ""
                if not is_global_search:
                    kw_clauses = []
                    # Usamos los parámetros procesados con %
                    for p_val in db_like_params[: len(title_kws)]:
                        kw_clauses.append("LOWER(e.razonSocial) LIKE %s")
                        db_params.append(p_val)
                    for p_val in db_like_params[len(title_kws) :]:
                        kw_clauses.append("LOWER(ce.nombre) LIKE %s")
                        db_params.append(p_val)
                        kw_clauses.append("LOWER(ce2.nombre) LIKE %s")
                        db_params.append(p_val)
                    where_clauses.append(f"({' OR '.join(kw_clauses)})")

                where_clauses.append("e.idEstado = 1")
                where_clauses.append("e.publicado = 1")
                full_where = (
                    "WHERE " + " AND ".join(where_clauses)
                    if where_clauses
                    else "WHERE 1=1"
                )

                sql = f"""
                    SELECT 
                        e.id, e.razonSocial, e.latitud, e.longitud, e.direccion, 
                        e.acercaDeNosotros, e.rutaLogo, e.facebookUrl, e.instagramUrl, e.tiktokUrl, e.whatsappNumber,
                        COALESCE(MAX(ce.nombre), MAX(ce2.nombre)) AS categoria
                        {dist_sql}
                    FROM empresa e
                    LEFT JOIN categoriaempresa ce ON e.idCategoriaEmpresa = ce.id
                    LEFT JOIN asignacionCompanyCategoria acc ON e.id = acc.idCompany
                    LEFT JOIN categoriaempresa ce2 ON acc.idCategoriaCompany = ce2.id
                    {full_where}
                    {city_filter}
                    GROUP BY e.id, e.razonSocial, e.latitud, e.longitud, e.direccion, e.acercaDeNosotros, e.rutaLogo, e.facebookUrl, e.instagramUrl, e.tiktokUrl, e.whatsappNumber
                    ORDER BY {"distance_km ASC" if dist_sql else "e.razonSocial ASC"}
                    LIMIT 40
                """
                logger.info(f"SQL Search: {sql}")
                logger.info(f"Params: {db_params}")
                cur.execute(sql, db_params)
                rows = cur.fetchall()

                # --- REFINAMIENTO POST-QUERY (PARA DATA CONTAMINADA) ---
                if (refinement_kws or negative_kws) and not is_global_search:
                    original_count = len(rows)
                    filtered_rows = []
                    for row in rows:
                        biz_name_norm = _normalize(row["razonSocial"])
                        biz_cat_norm = _normalize(row["categoria"] or "")
                        biz_desc_norm = _normalize(row["acercaDeNosotros"] or "")
                        text_to_check = (
                            f"{biz_name_norm} {biz_cat_norm} {biz_desc_norm}"
                        )

                        # 1. Exclusión negativa (ej: no mostrar gimnasios si buscamos barberia)
                        if any(nk in biz_name_norm for nk in negative_kws):
                            continue

                        # 2. Inclusión: al menos una de las palabras clave debe estar en el texto
                        # (Más relajado que buscar la frase completa)
                        query_words = query_norm.split()
                        if not query_words:
                            filtered_rows.append(row)
                            continue

                        match_found = False
                        # Limpiar el texto del registro para la comparación
                        record_text_clean = _clean_search_query(text_to_check)

                        if any(
                            all(
                                word in record_text_clean
                                for word in _clean_search_query(kw).split()
                            )
                            for kw in refinement_kws
                        ):
                            match_found = True

                        if match_found:
                            filtered_rows.append(row)

                    rows = filtered_rows
                    logger.info(
                        f"Refinamiento: {original_count} -> {len(rows)} resultados (KWS: {refinement_kws}, NegKWS: {negative_kws})"
                    )

                logger.info(f"Busqueda finalizada. Resultados finales: {len(rows)}")

                if rows:
                    if len(rows) > 20:
                        has_more = True
                        rows = rows[:20]
                    for row in rows:
                        name = (
                            row["razonSocial"]
                            if row["razonSocial"] and row["razonSocial"].strip()
                            else f"Negocio #{row['id']}"
                        )
                        results.append(
                            {
                                "id": row["id"],
                                "name": name,
                                "razonSocial": name,
                                "category": row["categoria"] or "Negocio",
                                "lat": (
                                    float(row["latitud"]) if row["latitud"] else None
                                ),
                                "lng": (
                                    float(row["longitud"]) if row["longitud"] else None
                                ),
                                "address": row["direccion"],
                                "logo": _format_logo(row["rutaLogo"]),
                                "facebook": row["facebookUrl"],
                                "instagram": row["instagramUrl"],
                                "tiktok": row["tiktokUrl"],
                                "whatsapp": row["whatsappNumber"],
                                "distance_km": (
                                    round(row["distance_km"], 2)
                                    if row.get("distance_km") is not None
                                    else None
                                ),
                            }
                        )
    except Exception as e:
        logger.error(f"Error en search_businesses: {e}")
        return {
            "success": False,
            "message": "Tuve un problema al consultar la base de datos.",
        }

    # --- RESULTADOS VACÍOS: Lógica de proximidad y Fallback ---
    if not results:
        city_lat, city_lng = city_lat_fixed, city_lng_fixed
        suggestion_msg = ""
        cat_display = category.lower() if category else "negocios"
        if not cat_display.endswith("s") and len(cat_display) > 3:
            cat_display += "s"

        suggested_city = None
        suggested_businesses = []

        # El fallback anterior era demasiado genérico (mostraba cualquier negocio).
        # Ahora confiamos en la búsqueda global por categoría en ciudades cercanas si no hay nada en la actual.
        pass

        # 1. Si no hay nada en la ciudad, buscar en otras ciudades (Búsqueda Global por categoría)
        if resolved_city and city_lat and city_lng and not is_global_search:
            try:
                with get_connection("vt_inventario") as conn:
                    with conn.cursor() as cur:
                        sql_global = f"""
                            SELECT 
                                e.id, e.razonSocial, e.latitud, e.longitud, e.direccion, e.rutaLogo, e.acercaDeNosotros,
                                COALESCE(MAX(ce.nombre), MAX(ce2.nombre)) AS categoria,
                                (6371 * acos(LEAST(1.0, GREATEST(-1.0, cos(radians({city_lat})) * cos(radians(e.latitud)) * cos(radians(e.longitud) - radians({city_lng})) + sin(radians({city_lat})) * sin(radians(e.latitud)))))) AS distance_km
                            FROM empresa e
                            LEFT JOIN categoriaempresa ce ON e.idCategoriaEmpresa = ce.id
                            LEFT JOIN asignacionCompanyCategoria acc ON e.id = acc.idCompany
                            LEFT JOIN categoriaempresa ce2 ON acc.idCategoriaCompany = ce2.id
                            {full_where}
                            AND e.idEstado = 1 AND e.publicado = 1
                            GROUP BY e.id, e.razonSocial, e.latitud, e.longitud, e.direccion, e.rutaLogo, e.acercaDeNosotros
                            ORDER BY distance_km ASC
                            LIMIT 10
                        """
                        cur.execute(sql_global, db_params)
                        global_rows = cur.fetchall()
                        suggested_next_city = None
                        if global_rows:
                            first_biz = global_rows[0]
                            dist = first_biz["distance_km"]

                            # Intentar obtener el nombre de la ciudad para el anclaje de contexto
                            from tools.nexiservice import reverse_geocode

                            city_data = await reverse_geocode(
                                float(first_biz["latitud"]),
                                float(first_biz["longitud"]),
                            )
                            suggested_city = (
                                city_data.get("city")
                                if city_data.get("success")
                                else None
                            )

                            if suggested_city:
                                suggestion_msg = f"No encontré **{cat_display}** en **{current_city}**, pero hay excelentes opciones en **{suggested_city}** (a {dist:.1f} km). ¿Deseas explorarlas? [CITY:{suggested_city}]"
                                suggested_next_city = suggested_city
                            else:
                                suggestion_msg = f"No encontré **{cat_display}** en **{current_city}**, pero hay opciones en ciudades cercanas. ¿Deseas explorarlas?"
            except Exception as e:
                logger.error(f"Error en búsqueda global: {e}")

        # Retornamos siempre success: True con las coordenadas para que el mapa se mueva a la ciudad pedida
        return {
            "success": True,
            "message": suggestion_msg
            or f"No encontré resultados para **{cat_display}** en **{current_city}**.",
            "count": 0,
            "businesses": [],
            "city": current_city,
            "suggested_next_city": suggested_next_city,
            "category": category or cat_display,
            "target_city_coords": (
                {"lat": city_lat_fixed, "lng": city_lng_fixed}
                if city_lat_fixed
                else None
            ),
        }

    return {
        "success": True,
        "query": query_norm,
        "category": category or "Negocios",
        "count": len(results),
        "businesses": results,
        "has_more": has_more,
        "city": current_city,
        "target_city_coords": (
            {"lat": city_lat_fixed, "lng": city_lng_fixed} if city_lat_fixed else None
        ),
    }


async def navigate_to_company(company_id: str, **kwargs) -> dict:
    """
    Indica al sistema que debe redirigir al usuario al perfil de una empresa.
    """
    return {
        "success": True,
        "action": "navigate",
        "url": f"/empresa/{company_id}",
        "message": f"Entendido. Te estoy llevando al perfil de la empresa {company_id}.",
    }


async def fly_to_business(
    business_name: str = None, business_id: str = None, **kwargs
) -> dict:
    """
    Busca un negocio específico por su nombre o ID para centrar el mapa en él.
    Utiliza _resolve_business_id para aprovechar la lógica de búsqueda tolerante y por ciudad.
    """
    city = kwargs.get("city")
    target_id, real_name = await _resolve_business_id(
        business_name, business_id, city=city
    )

    if not target_id:
        return {
            "success": False,
            "message": f"No pude encontrar el negocio '{business_name}' en la base de datos.",
        }

    try:
        from core.database import get_connection

        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT id, razonSocial, latitud, longitud, direccion, acercaDeNosotros, whatsappNumber
                    FROM empresa
                    WHERE id = %s
                    LIMIT 1
                """
                cur.execute(sql, (target_id,))
                row = cur.fetchone()

                if row:
                    return {
                        "success": True,
                        "business": {
                            "id": row["id"],
                            "name": row["razonSocial"],
                            "lat": float(row["latitud"]) if row["latitud"] else None,
                            "lng": float(row["longitud"]) if row["longitud"] else None,
                            "address": row["direccion"],
                            "description": row["acercaDeNosotros"],
                            "whatsapp": row["whatsappNumber"],
                        },
                    }
                else:
                    return {
                        "success": False,
                        "message": f"No pude encontrar los detalles del negocio ID {target_id}.",
                    }
    except Exception as e:
        logger.error(f"Error en fly_to_business: {e}")
        return {
            "success": False,
            "message": "Ocurrió un error al buscar el negocio en el mapa.",
        }


async def reverse_geocode(lat: float, lng: float) -> dict:
    """Thin wrapper — delegates to services.geo.reverse_geocode."""
    from services.geo import reverse_geocode as _reverse_geocode

    return await _reverse_geocode(lat, lng)


async def get_businesses_comparison(business_ids: list) -> dict:
    """
    Obtiene datos comparativos para una lista de negocios.
    """
    from core.database import get_connection

    if not business_ids:
        return {"success": False, "message": "No se proporcionaron IDs para comparar."}

    # Asegurar que los IDs sean enteros y únicos
    try:
        clean_ids = list(set(int(bid) for bid in business_ids))
    except (ValueError, TypeError):
        return {"success": False, "message": "IDs de negocio inválidos."}

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # 1. Obtener información básica de las empresas y sus categorías
                placeholders = ", ".join(["%s"] * len(clean_ids))
                sql_basic = f"""
                    SELECT e.id, e.razonSocial, e.direccion, e.rutaLogo, e.latitud, e.longitud, ce.nombre as categoria
                    FROM empresa e
                    LEFT JOIN categoriaempresa ce ON e.idCategoriaEmpresa = ce.id
                    WHERE e.id IN ({placeholders})
                """
                cur.execute(sql_basic, tuple(clean_ids))
                companies = {row["id"]: row for row in cur.fetchall()}

                # 2. Obtener estadísticas de calificación
                sql_stats = f"""
                    SELECT idCompany, AVG(calificacion) as avg_rating, COUNT(id) as count
                    FROM calificacionCompany
                    WHERE idCompany IN ({placeholders})
                    GROUP BY idCompany
                """
                cur.execute(sql_stats, tuple(clean_ids))
                stats = {row["idCompany"]: row for row in cur.fetchall()}

                # 3. Obtener servicios destacados
                sql_services = f"""
                    SELECT idCompany, nombre, valor
                    FROM servicios
                    WHERE idCompany IN ({placeholders})
                    ORDER BY destacado DESC, id ASC
                """
                cur.execute(sql_services, tuple(clean_ids))
                all_services = cur.fetchall()
                services_by_biz = {}
                for s in all_services:
                    bid = s["idCompany"]
                    if bid not in services_by_biz:
                        services_by_biz[bid] = []
                    if len(services_by_biz[bid]) < 3:  # Máximo 3 por negocio
                        services_by_biz[bid].append(
                            f"{s['nombre']} (${s['valor']})"
                            if s["valor"]
                            else s["nombre"]
                        )

                # 4. Construir comparativa
                comparison = []
                # Mantenemos el orden original de los IDs si es posible
                for bid in clean_ids:
                    comp = companies.get(bid, {})
                    stat = stats.get(bid, {"avg_rating": 0, "count": 0})
                    servs = services_by_biz.get(bid, [])

                    comparison.append(
                        {
                            "id": bid,
                            "name": comp.get("razonSocial", "Desconocido"),
                            "category": comp.get("categoria", "Varios"),
                            "logo": _format_logo(comp.get("rutaLogo")),
                            "lat": (
                                float(comp.get("latitud"))
                                if comp.get("latitud")
                                else None
                            ),
                            "lng": (
                                float(comp.get("longitud"))
                                if comp.get("longitud")
                                else None
                            ),
                            "rating": (
                                round(float(stat["avg_rating"]), 1)
                                if stat["avg_rating"]
                                else 0
                            ),
                            "reviews_count": stat["count"],
                            "services": servs,
                        }
                    )

                return {"success": True, "comparison": comparison}
    except Exception as e:
        logger.error(f"Error en get_businesses_comparison: {e}")
        return {"success": False, "message": "Error al obtener datos comparativos."}


async def get_business_reviews(
    business_name: str = None, business_id: int = None, city: str = None, **kwargs
) -> dict:
    """
    Obtiene las reseñas y calificaciones de un negocio.
    """
    target_id, real_name = await _resolve_business_id(
        business_name, business_id, city=city
    )
    from core.database import get_connection

    if not target_id:
        return {
            "success": False,
            "message": f"No pude encontrar el negocio '{business_name}' para ver sus reseñas.",
        }

    # Si tenemos ID pero no nombre real (recuperado de contexto), lo buscamos
    if target_id and not real_name:
        try:
            with get_connection("vt_inventario") as conn:
                with conn.cursor() as cur:
                    sql = "SELECT razonSocial FROM empresa WHERE id = %s"
                    cur.execute(sql, (target_id,))
                    row = cur.fetchone()
                    if row:
                        real_name = row["razonSocial"]
        except:
            real_name = f"Negocio #{target_id}"

    # 2. Consultar reseñas
    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # Obtenemos promedio y conteo
                sql_stats = """
                    SELECT AVG(calificacion) as avg_rating, COUNT(*) as count
                    FROM calificacionCompany
                    WHERE idCompany = %s
                """
                cur.execute(sql_stats, (target_id,))
                stats = cur.fetchone()

                # Obtenemos las últimas 5 reseñas con el nombre del cliente (tercero)
                sql_reviews = """
                    SELECT c.calificacion, c.comentario, t.nombre as cliente, c.created_at
                    FROM calificacionCompany c
                    LEFT JOIN tercero t ON c.idTercero = t.id
                    WHERE c.idCompany = %s
                    ORDER BY c.created_at DESC
                    LIMIT 5
                """
                cur.execute(sql_reviews, (target_id,))
                rows = cur.fetchall()

                reviews = []
                for r in rows:
                    reviews.append(
                        {
                            "rating": (
                                float(r["calificacion"]) if r["calificacion"] else 0
                            ),
                            "comment": r["comentario"],
                            "client": r["cliente"] or "Cliente anónimo",
                            "date": (
                                r["created_at"].strftime("%Y-%m-%d")
                                if r["created_at"]
                                else None
                            ),
                        }
                    )

                # Obtenemos info básica extra
                sql_info = (
                    "SELECT direccion, acercaDeNosotros FROM empresa WHERE id = %s"
                )
                cur.execute(sql_info, (target_id,))
                b_info = cur.fetchone()

                return {
                    "success": True,
                    "business_id": target_id,
                    "business_name": real_name,
                    "business_info": b_info,
                    "average_rating": (
                        round(float(stats["avg_rating"]), 1)
                        if stats and stats["avg_rating"]
                        else 0
                    ),
                    "total_reviews": stats["count"] if stats else 0,
                    "reviews": reviews,
                }
    except Exception as e:
        logger.error(f"Error en get_business_reviews: {e}")
        # Fallback por si la tabla no existe o tiene otros nombres
        return {
            "success": False,
            "message": "No pude cargar las reseñas en este momento.",
        }


async def get_business_availability(
    business_name: str = None,
    business_id: int = None,
    date: str = None,
    city: str = None,
    **kwargs,
) -> dict:
    """
    Consulta la disponibilidad (agenda) de un negocio para una fecha específica.
    """
    from core.database import get_connection
    from datetime import datetime, timedelta

    # 1. Resolver ID y Nombre (hallucination-proof)
    target_id, real_name = await _resolve_business_id(
        business_name, business_id, city=city
    )

    if not target_id:
        return {
            "success": False,
            "message": f"No encontré el negocio '{business_name}' para verificar su agenda.",
        }

    # 2. Configurar fechas (Hoy y Mañana por defecto si no hay fecha)
    is_default_date = date is None
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    professional_ids = kwargs.get("professional_ids")  # Lista de IDs
    professional_id = kwargs.get("professional_id")
    prof_name = kwargs.get("professional_name")

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # Resolver ID de profesional si se pasó el nombre
                if not professional_id and not professional_ids and prof_name:
                    from tools.nexiservice import _normalize

                    sql_p = """
                        SELECT rs.id 
                        FROM responsableservicio rs
                        JOIN tercero t ON rs.idPersona = t.id
                        WHERE t.idCompany = %s AND (LOWER(t.nombre) LIKE %s OR LOWER(t.nombre) = %s)
                        LIMIT 1
                    """
                    cur.execute(
                        sql_p,
                        (
                            target_id,
                            f"%{_normalize(prof_name)}%",
                            _normalize(prof_name),
                        ),
                    )
                    p_row = cur.fetchone()
                    if p_row:
                        professional_id = p_row["id"]

                # Base SQL (Recuperar descripción o nombre del servicio)
                sql_base = """
                    SELECT a.fechaInicial, a.horaInicial, a.horaFinal, a.descripcion, a.estado, s.nombre as servicio_nombre
                    FROM agenda a
                    LEFT JOIN asignacionresponsableservicio ars ON a.id = ars.idAgenda
                    LEFT JOIN servicios s ON ars.idServicio = s.id
                """
                where_clause = "WHERE a.idCompany = %s AND a.estado NOT IN ('CANCELADO', 'ARCHIVADO')"
                params = [target_id]

                if professional_id:
                    where_clause += " AND ars.idResponsable = %s"
                    params.append(professional_id)
                elif professional_ids:
                    placeholders = ", ".join(["%s"] * len(professional_ids))
                    where_clause += f" AND ars.idResponsable IN ({placeholders})"
                    params.extend(professional_ids)

                if is_default_date:
                    where_clause += " AND a.fechaInicial IN (%s, %s)"
                    params.append(target_date)
                    params.append(tomorrow_date)
                else:
                    where_clause += " AND a.fechaInicial = %s"
                    params.append(target_date)

                sql = f"{sql_base} {where_clause} ORDER BY a.fechaInicial ASC, a.horaInicial ASC"
                cur.execute(sql, tuple(params))

                rows = cur.fetchall()

                busy_slots = []
                for r in rows:
                    h_init = str(r["horaInicial"])[:5]
                    # La hora de fin puede venir vacía; sin esta comprobación se
                    # imprimía literalmente "07:00 - None" en la respuesta.
                    h_end = str(r["horaFinal"])[:5] if r["horaFinal"] else None
                    f_init = str(r["fechaInicial"])

                    label = (
                        "Hoy"
                        if f_init == target_date
                        else ("Mañana" if f_init == tomorrow_date else f_init)
                    )

                    desc = r["descripcion"] or r["servicio_nombre"] or "Cita ocupada"

                    busy_slots.append(
                        {
                            "date": f_init,
                            "date_label": label,
                            "start": h_init,
                            "end": h_end,
                            "description": desc,
                            "status": r["estado"],
                        }
                    )

                return {
                    "success": True,
                    "business_id": target_id,
                    "business_name": real_name,
                    "date": target_date,
                    "busy_slots": busy_slots,
                    "count": len(busy_slots),
                    "is_default_view": is_default_date,
                }
    except Exception as e:
        logger.error(f"Error en get_business_availability: {e}")
        return {
            "success": False,
            "message": "Ocurrió un error al consultar la disponibilidad.",
        }


async def _resolve_business_id(
    business_name: str = None, business_id: int = None, city: str = None
):
    """Resuelve el ID y nombre real de un negocio a partir de nombre o ID."""
    from core.database import get_connection

    target_id = business_id
    real_name = business_name

    # Si tenemos ID pero el nombre parece una alucinación (del historial), lo corregimos
    if target_id:
        try:
            with get_connection("vt_inventario") as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT razonSocial FROM empresa WHERE id = %s", (target_id,)
                    )
                    row = cur.fetchone()
                    if row:
                        real_name = row["razonSocial"]
        except Exception:
            pass

    # Si no hay ID, buscamos por nombre
    if not target_id and business_name:
        import re

        # Limpiar stop words y ciudades comunes que suelen venir en el input
        name_clean = _clean_search_query(business_name)

        if not name_clean:
            name_clean = _normalize(
                business_name
            )  # Fallback al original si quedó vacío

        try:
            with get_connection("vt_inventario") as conn:
                with conn.cursor() as cur:
                    city_filter = ""
                    params = []
                    if city:
                        city_filter = " AND (LOWER(direccion) LIKE %s OR LOWER(barrio) LIKE %s OR LOWER(zona) LIKE %s OR LOWER(razonSocial) LIKE %s)"
                        params = [f"%{city}%", f"%{city}%", f"%{city}%", f"%{city}%"]

                    # PRIORIDAD 1: Búsqueda exacta (ej: "FS")
                    cur.execute(
                        f"SELECT id, razonSocial FROM empresa WHERE LOWER(razonSocial) = %s{city_filter} LIMIT 1",
                        [name_clean] + params,
                    )
                    row = cur.fetchone()
                    if row:
                        target_id = row["id"]
                        real_name = row["razonSocial"]
                    else:
                        # PRIORIDAD 2: Búsqueda parcial (LIKE) con el nombre limpio
                        if len(name_clean) <= 3:
                            sql = f"SELECT id, razonSocial FROM empresa WHERE razonSocial REGEXP %s{city_filter} LIMIT 1"
                            cur.execute(sql, [f"[[:<:]]{name_clean}[[:>:]]"] + params)
                        else:
                            sql = f"SELECT id, razonSocial FROM empresa WHERE LOWER(razonSocial) LIKE %s{city_filter} LIMIT 1"
                            cur.execute(sql, [f"%{name_clean}%"] + params)

                        row = cur.fetchone()
                        if row:
                            target_id = row["id"]
                            real_name = row["razonSocial"]
                        else:
                            # PRIORIDAD 3: Búsqueda ultra-tolerante (vocales -> _) para bypass de encodings rotos (ó -> )
                            # Ej: "fogon" -> "f_g_n" que matchea "fogn"
                            loose_clean = re.sub(r"[aeiou]", "_", name_clean)
                            cur.execute(
                                f"SELECT id, razonSocial FROM empresa WHERE LOWER(razonSocial) LIKE %s{city_filter} LIMIT 1",
                                [f"%{loose_clean}%"] + params,
                            )
                            row = cur.fetchone()
                            if row:
                                target_id = row["id"]
                                real_name = row["razonSocial"]
                            else:
                                # PRIORIDAD 4: Búsqueda parcial con el nombre original (por si acaso)
                                orig_clean = _normalize(business_name)
                                cur.execute(sql, [f"%{orig_clean}%"] + params)
                                row = cur.fetchone()
                                if row:
                                    target_id = row["id"]
                                    real_name = row["razonSocial"]
        except Exception as e:
            logger.error(f"Error resolviendo nombre en _resolve_business_id: {e}")

    return target_id, real_name


async def get_business_mission_vision(
    business_name: str = None, business_id: int = None, city: str = None
) -> dict:
    """Obtiene la historia, misión y visión de un negocio."""
    target_id, real_name = await _resolve_business_id(
        business_name, business_id, city=city
    )
    if not target_id:
        return {
            "success": False,
            "message": f"No pude encontrar el negocio '{business_name}' para ver su historia.",
        }

    try:
        from core.database import get_connection

        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                sql = "SELECT mision, vision FROM empresa WHERE id = %s"
                cur.execute(sql, (target_id,))
                row = cur.fetchone()
                if not row:
                    return {
                        "success": False,
                        "message": "No encontré los datos de la empresa.",
                    }

                return {
                    "success": True,
                    "business_id": target_id,
                    "business_name": real_name,
                    "mision": row.get("mision"),
                    "vision": row.get("vision"),
                }
    except Exception as e:
        logger.error(f"Error en get_business_mission_vision: {e}")
        return {
            "success": False,
            "message": "Ocurrió un error al consultar la historia del negocio.",
        }


async def get_business_services(
    business_name: str = None, business_id: int = None, city: str = None, **kwargs
) -> dict:
    """Obtiene el catálogo de servicios de un negocio."""
    target_id, real_name = await _resolve_business_id(
        business_name, business_id, city=city
    )
    if not target_id:
        return {
            "success": False,
            "message": f"No pude encontrar el negocio '{business_name}' para ver sus servicios.",
        }

    try:
        from core.database import get_connection

        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # Filtrar solo servicios activos si aplica (ej. idEstado = 1) o simplemente los que tengan valor
                sql = """
                    SELECT id, nombre, descripcion, valor, tiempoServicio 
                    FROM servicios 
                    WHERE idCompany = %s AND estado = 1
                """  # Asumiendo columna estado o idEstado, usaremos un where sin estado temporalmente si falla
                try:
                    cur.execute(sql, (target_id,))
                except Exception as e:
                    sql_fallback = "SELECT id, nombre, descripcion, valor, tiempoServicio FROM servicios WHERE idCompany = %s"
                    cur.execute(sql_fallback, (target_id,))

                rows = cur.fetchall()

                return {
                    "success": True,
                    "business_id": target_id,
                    "business_name": real_name,
                    "services": rows,
                    "action": "navigate",
                    "url": f"/empresa/{target_id}#servicios",
                }
    except Exception as e:
        logger.error(f"Error en get_business_services: {e}")
        return {
            "success": False,
            "message": "Ocurrió un error al consultar los servicios del negocio.",
        }


async def get_service_professionals(service_id: int) -> list:
    """Obtiene la lista de profesionales/responsables que pueden prestar un servicio específico."""
    from core.database import get_connection

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT t.id, t.nombre, rs.id as responsable_id, p.perfil
                    FROM prestador_servicios ps
                    JOIN responsableservicio rs ON ps.responsable_servicio_id = rs.id
                    JOIN tercero t ON rs.idPersona = t.id
                    LEFT JOIN persona p ON t.id = p.id
                    WHERE ps.servicio_id = %s AND ps.estado = 'activo'
                """
                cur.execute(sql, (service_id,))
                return cur.fetchall()
    except Exception as e:
        logger.error(f"Error en get_service_professionals: {e}")
        return []


async def find_businesses_offering(service_term: str, city: str = None, **kwargs) -> dict:
    """
    Negocios que prestan un servicio concreto.

    `search_businesses` mira el nombre y la categoría de la empresa, así que una
    necesidad que se expresa por el servicio ("alguna medicina", "un masaje")
    no encontraba nada aunque hubiera negocios prestándolo. Esta consulta cierra
    ese hueco entrando por la tabla de servicios.

    El término debe venir del catálogo (lo garantiza la capa de comprensión);
    aquí nunca llega una frase cruda del usuario.
    """
    from core.database import get_connection

    term = _normalize(service_term or "").strip()
    if not term:
        return {"success": False, "message": "Necesito saber qué servicio buscas."}

    like = f"%{term.replace(' ', '%')}%"

    # Mismo criterio geográfico que search_businesses: una caja alrededor de la
    # ciudad. En Popayán, que es la ciudad raíz, los negocios sin coordenadas se
    # consideran locales en lugar de desaparecer.
    city_filter = ""
    resolved_city = city or "Popayan"
    try:
        from services.geo import resolve_city_coords_async

        c_lat, c_lng, official = await resolve_city_coords_async(resolved_city)
    except Exception:
        c_lat, c_lng, official = None, None, resolved_city

    if c_lat and c_lng:
        is_home = _normalize(resolved_city) == "popayan"
        box = (
            f"e.latitud BETWEEN {c_lat - 0.1} AND {c_lat + 0.1} "
            f"AND e.longitud BETWEEN {c_lng - 0.1} AND {c_lng + 0.1}"
        )
        city_filter = (
            f"AND (({box}) OR e.latitud IS NULL OR e.longitud IS NULL)"
            if is_home else f"AND ({box})"
        )

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT e.id, e.razonSocial, e.latitud, e.longitud, e.direccion,
                           e.rutaLogo, e.facebookUrl, e.instagramUrl, e.tiktokUrl,
                           e.whatsappNumber,
                           COALESCE(MAX(ce.nombre), 'Negocio') AS categoria,
                           GROUP_CONCAT(DISTINCT s.nombre SEPARATOR ', ') AS servicios
                    FROM servicios s
                    JOIN empresa e ON s.idCompany = e.id
                    LEFT JOIN categoriaempresa ce ON e.idCategoriaEmpresa = ce.id
                    LEFT JOIN categoriaservicios cs ON s.idCategoriaServicio = cs.id
                    WHERE s.idEstado = 1
                      AND e.idEstado = 1 AND e.publicado = 1
                      AND (LOWER(s.nombre) LIKE %s OR LOWER(cs.nombre) LIKE %s)
                      {city_filter}
                    GROUP BY e.id, e.razonSocial, e.latitud, e.longitud, e.direccion,
                             e.rutaLogo, e.facebookUrl, e.instagramUrl, e.tiktokUrl,
                             e.whatsappNumber
                    LIMIT 20
                    """,
                    (like, like),
                )
                rows = cur.fetchall() or []

        businesses = [
            {
                "id": r["id"],
                "name": r["razonSocial"] or f"Negocio #{r['id']}",
                "razonSocial": r["razonSocial"],
                "category": r["categoria"],
                "lat": float(r["latitud"]) if r["latitud"] else None,
                "lng": float(r["longitud"]) if r["longitud"] else None,
                "address": r["direccion"],
                "logo": _format_logo(r["rutaLogo"]),
                "facebook": r["facebookUrl"],
                "instagram": r["instagramUrl"],
                "tiktok": r["tiktokUrl"],
                "whatsapp": r["whatsappNumber"],
                "matched_services": r["servicios"],
            }
            for r in rows
        ]

        return {
            "success": True,
            "count": len(businesses),
            "businesses": businesses,
            "category": service_term,
            "city": official or resolved_city,
            "matched_by": "service",
        }
    except Exception as e:
        logger.error(f"Error en find_businesses_offering: {e}")
        return {
            "success": False,
            "message": "Tuve un problema al consultar los servicios.",
        }


def _format_slot(slot: dict) -> str:
    """
    Un tramo ocupado, dicho como lo diría una persona.

    Si no consta la hora de fin —que pasa— se menciona sólo la de inicio en vez
    de escribir "07:00 - None", que es lo que salía antes.
    """
    start, end = slot.get("start"), slot.get("end")
    if start and end and start != end:
        return f"de {start} a {end}"
    valor = str(start or end or "").strip()
    return f"a las {valor}" if valor else ""


def _natural_list(items: list) -> str:
    """
    Enumera como se enumera al hablar: "a, b y c".

    Con comas hasta el final quedaba "hoy de 07:00, 07:00 a 07:30, 10:00", que
    no se entiende ni leído en voz alta.
    """
    limpios = [str(i).strip() for i in items if str(i).strip()]
    if not limpios:
        return ""
    if len(limpios) == 1:
        return limpios[0]
    return ", ".join(limpios[:-1]) + " y " + limpios[-1]


async def get_business_professionals(business_id: int) -> dict:
    """
    Lista el equipo que presta servicios en un negocio.

    Existía la consulta por servicio (`get_service_professionals`), pero no una
    por negocio, y el usuario suele preguntar "¿quiénes trabajan ahí?" antes de
    haber elegido servicio.
    """
    from core.database import get_connection

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT razonSocial FROM empresa WHERE id = %s", (business_id,)
                )
                biz = cur.fetchone()
                if not biz:
                    return {
                        "success": False,
                        "message": "No encontré ese negocio.",
                    }

                cur.execute(
                    """
                    SELECT DISTINCT t.id, t.nombre, p.perfil
                    FROM responsableservicio rs
                    JOIN tercero t ON rs.idPersona = t.id
                    LEFT JOIN persona p ON t.id = p.id
                    JOIN prestador_servicios ps ON ps.responsable_servicio_id = rs.id
                    JOIN servicios s ON ps.servicio_id = s.id
                    WHERE s.idCompany = %s AND ps.estado = 'activo'
                    ORDER BY t.nombre ASC
                    """,
                    (business_id,),
                )
                rows = cur.fetchall() or []

                return {
                    "success": True,
                    "business_id": business_id,
                    "business_name": biz["razonSocial"],
                    "professionals": [
                        {
                            "id": r["id"],
                            "name": r["nombre"],
                            "perfil": r.get("perfil"),
                        }
                        for r in rows
                    ],
                }
    except Exception as e:
        logger.error(f"Error en get_business_professionals: {e}")
        return {
            "success": False,
            "message": "Ocurrió un error al consultar el equipo del negocio.",
        }


async def get_professional_info(
    professional_name: str, business_id: int = None
) -> dict:
    """Obtiene información detallada (biografía/perfil) de un profesional."""
    from core.database import get_connection

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # Normalizar búsqueda
                clean_name = _normalize(professional_name)

                # Búsqueda en tercero + persona
                sql = """
                    SELECT t.id, t.nombre, t.idCompany, p.perfil, ps.servicio_id, s.nombre as servicio_nombre
                    FROM tercero t
                    JOIN persona p ON t.id = p.id
                    LEFT JOIN prestador_servicios ps ON ps.responsable_servicio_id = (SELECT id FROM responsableservicio WHERE idPersona = t.id LIMIT 1)
                    LEFT JOIN servicios s ON ps.servicio_id = s.id
                    WHERE LOWER(t.nombre) LIKE %s
                """
                if business_id:
                    sql += " AND t.idCompany = %s"
                    cur.execute(sql, (f"%{clean_name}%", business_id))
                else:
                    cur.execute(sql, (f"%{clean_name}%",))

                rows = cur.fetchall()
                if not rows:
                    return {
                        "success": False,
                        "message": f"No encontré información sobre el profesional '**{professional_name}**'.",
                    }

                prof = rows[0]
                services = list(
                    set([r["servicio_nombre"] for r in rows if r["servicio_nombre"]])
                )

                return {
                    "success": True,
                    "name": prof["nombre"],
                    "profile": prof["perfil"] or "Sin descripción disponible.",
                    "services": services,
                    "message": f"**{prof['nombre']}** es un profesional destacado en nuestro equipo. \n\n**Perfil:** {prof['perfil'] or 'No cuenta con una biografía detallada en este momento.'}\n\n**Servicios que presta:** "
                    + (
                        ", ".join(services)
                        if services
                        else "No hay servicios asociados."
                    )
                    + f"\n\n[BIZ:{prof['idCompany']}]",
                }
    except Exception as e:
        logger.error(f"Error en get_professional_info: {e}")
        return {
            "success": False,
            "message": "Ocurrió un error al consultar la información del profesional.",
        }


async def get_service_info(service_name: str, business_id: int = None) -> dict:
    """Obtiene información detallada de un servicio."""
    from core.database import get_connection

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                srv_clean = _normalize(service_name)
                sql = "SELECT id, nombre, descripcion, valor, tiempoServicio, idCompany FROM servicios WHERE LOWER(nombre) LIKE %s"
                if business_id:
                    sql += " AND idCompany = %s"
                    cur.execute(sql, (f"%{srv_clean}%", business_id))
                else:
                    cur.execute(sql, (f"%{srv_clean}%",))

                srv = cur.fetchone()
                if not srv:
                    return {
                        "success": False,
                        "message": f"No encontré el servicio '**{service_name}**'.",
                    }

                price = (
                    f"${srv['valor']:,.0f}" if srv["valor"] else "Precio a consultar"
                )
                duration = (
                    f"{srv['tiempoServicio']} min"
                    if srv["tiempoServicio"]
                    else "Duración no especificada"
                )

                return {
                    "success": True,
                    "name": srv["nombre"],
                    "description": srv["descripcion"],
                    "price": price,
                    "duration": duration,
                    "message": f"**{srv['nombre']}**\n\n**Descripción:** {srv['descripcion'] or 'No hay una descripción detallada disponible.'}\n\n**Valor:** {price}\n**Duración estimada:** {duration}\n\n¿Te gustaría agendar este servicio?\n\n[BIZ:{srv['idCompany']}] [SERVICIO:{srv['nombre']}]",
                }
    except Exception as e:
        logger.error(f"Error en get_service_info: {e}")
        return {
            "success": False,
            "message": "Ocurrió un error al consultar la información del servicio.",
        }


async def open_business_web(business_name: str = None) -> dict:
    """
    Obtiene la URL de la página web o red social de un negocio para abrirla.
    """
    if not business_name:
        return {"success": False, "message": "¿De qué negocio deseas ver el sitio web?"}

    res = await search_businesses(category=business_name)
    if res.get("success") and res.get("businesses"):
        biz = res["businesses"][0]
        # Prioridad de URLs: Instagram > Facebook > TikTok > WhatsApp
        url = (
            biz.get("instagram")
            or biz.get("facebook")
            or biz.get("tiktok")
            or biz.get("whatsapp")
        )
        if url:
            if not isinstance(url, str) or not url.strip():
                return {
                    "success": False,
                    "business_id": biz["id"],
                    "message": f"**{biz['name']}** no tiene un enlace web válido todavía.",
                }

            final_url = url
            if not url.startswith("http"):
                if biz.get("instagram") == url:
                    final_url = f"https://instagram.com/{url.replace('@', '')}"
                elif biz.get("facebook") == url:
                    final_url = f"https://facebook.com/{url}"
                elif biz.get("whatsapp") == url:
                    clean_phone = re.sub(r"\D", "", str(url))
                    if not clean_phone.startswith("57"):
                        clean_phone = f"57{clean_phone}"
                    final_url = f"https://wa.me/{clean_phone}"
                else:
                    final_url = f"https://{url}"

            return {
                "success": True,
                "action": "open_web",
                "url": final_url,
                "business_name": biz["name"],
                "message": f"Abriendo el sitio web de **{biz['name']}**...",
            }
        else:
            return {
                "success": False,
                "business_id": biz["id"],
                "message": f"**{biz['name']}** no tiene un sitio web registrado todavía, pero puedes ver su perfil aquí.",
            }

    return {
        "success": False,
        "message": f"No encontré el negocio '{business_name}' para abrir su sitio web.",
    }


async def recommend_businesses(
    category: str = "", city: str = None, active_city: str = None, **kwargs
) -> dict:
    """
    Recomienda negocios basados en calificación promedio (satisfacción) y categoría (gusto).
    """
    from core.database import get_connection

    resolved_city = city or active_city or "Popayan"
    city_norm = _normalize(resolved_city)

    # 1. Definir filtros de ciudad
    POPAYAN_BBOX = {
        "min_lat": 2.32,
        "max_lat": 2.58,
        "min_lng": -76.82,
        "max_lng": -76.42,
    }
    CALI_BBOX = {"min_lat": 3.33, "max_lat": 3.51, "min_lng": -76.58, "max_lng": -76.45}
    CITIES_BBOX = {"Popayan": POPAYAN_BBOX, "Cali": CALI_BBOX}
    CITIES_BBOX_NORM = {_normalize(k): v for k, v in CITIES_BBOX.items()}

    city_filter = ""
    if city_norm in CITIES_BBOX_NORM:
        bbox = CITIES_BBOX_NORM[city_norm]
        if city_norm == "popayan":
            city_filter = f"AND ((e.latitud BETWEEN {bbox['min_lat']} AND {bbox['max_lat']} AND e.longitud BETWEEN {bbox['min_lng']} AND {bbox['max_lng']}) OR e.latitud IS NULL OR e.longitud IS NULL)"
        else:
            city_filter = f"AND (e.latitud BETWEEN {bbox['min_lat']} AND {bbox['max_lat']} AND e.longitud BETWEEN {bbox['min_lng']} AND {bbox['max_lng']})"

    # 2. Búsqueda con ordenamiento por calificación
    db_params = []
    where_clause = "WHERE 1=1"
    if category:
        # Limpiar stop words para la búsqueda de recomendación también
        import re

        cat_norm = _normalize(category)
        cat_norm = re.sub(
            r"^(la empresa de|el negocio de|empresa de|negocio de|la empresa|el negocio|la|el|los|las|de|del|un|una)\s+",
            "",
            cat_norm,
            flags=re.IGNORECASE,
        ).strip()

        # Heurístico simple para plurales: si termina en 's', probar también sin la 's'
        cat_variations = [cat_norm]
        if cat_norm.endswith("s") and len(cat_norm) > 4:
            cat_variations.append(cat_norm[:-1])

        # Construir OR para variaciones
        sub_clauses = []
        for var in cat_variations:
            sub_clauses.append(
                "(LOWER(e.razonSocial) LIKE %s OR LOWER(ce.nombre) LIKE %s OR LOWER(cs.nombre) LIKE %s)"
            )
            db_params.extend([f"%{var}%", f"%{var}%", f"%{var}%"])

        where_clause = "WHERE (" + " OR ".join(sub_clauses) + ")"

    sql = """
        SELECT 
            e.id, e.razonSocial, e.rutaLogo, e.direccion,
            COALESCE(MAX(ce.nombre), MAX(cs.nombre)) AS categoria,
            AVG(cc.calificacion) as avg_rating,
            COUNT(cc.id) as review_count
        FROM empresa e
        LEFT JOIN categoriaempresa ce ON e.idCategoriaEmpresa = ce.id
        LEFT JOIN asignacionCompanyCategoria acc ON e.id = acc.idCompany
        LEFT JOIN categoriaServicios cs ON acc.idCategoriaCompany = cs.id
        LEFT JOIN calificacionCompany cc ON e.id = cc.idCompany
        {where_clause}
        AND e.idEstado = 1 AND e.publicado = 1
        {city_filter}
        GROUP BY e.id, e.razonSocial, e.rutaLogo, e.direccion
        ORDER BY avg_rating DESC, review_count DESC
        LIMIT 10
    """

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # Intento 1: Con filtro de ciudad (coordenadas)
                full_sql = sql.replace("{where_clause}", where_clause).replace(
                    "{city_filter}", city_filter
                )
                logger.info(f"RECOMMEND SQL: {full_sql} | PARAMS: {db_params}")
                cur.execute(full_sql, db_params)
                rows = cur.fetchall()

                # Intento 2: Si no hay resultados en la ciudad, buscar sin filtro de coordenadas
                # (Especialmente útil en entornos de prueba con datos mal geolocalizados)
                if not rows and city_filter:
                    logger.info(
                        "RECOMMEND: No se hallaron resultados con filtro GPS. Reintentando sin GPS."
                    )
                    full_sql_no_gps = sql.replace(
                        "{where_clause}", where_clause
                    ).replace("{city_filter}", "")
                    cur.execute(full_sql_no_gps, db_params)
                    rows = cur.fetchall()

                results = []
                for row in rows:
                    results.append(
                        {
                            "id": row["id"],
                            "name": row["razonSocial"],
                            "category": row["categoria"] or "Negocio",
                            "rating": (
                                round(float(row["avg_rating"]), 1)
                                if row["avg_rating"]
                                else 0
                            ),
                            "reviews": row["review_count"],
                            "logo": _format_logo(row["rutaLogo"]),
                            "address": row["direccion"],
                        }
                    )

                return {
                    "success": True,
                    "city": resolved_city,
                    "category": category,
                    "businesses": results,
                    "message": f"Basado en tus gustos y en la satisfacción de otros usuarios, te recomiendo estos negocios en {resolved_city}: ⭐",
                }
    except Exception as e:
        logger.error(f"Error en recommend_businesses: {e}")
        return {
            "success": False,
            "message": "No pude generar recomendaciones en este momento.",
        }


async def request_appointment(
    business_name: str = None,
    business_id: int = None,
    time: str = None,
    service_name: str = None,
    date: str = None,
    professional_name: str = None,
    reservation_name: str = None,
    **kwargs,
) -> dict:
    """
    Inicia el proceso de reserva. Si se proporciona servicio, intenta confirmarlo.
    """
    target_id, real_name = await _resolve_business_id(business_name, business_id)

    # Limpiar servicio si es un término genérico de reserva
    if service_name:
        _GENERIC_SRV = {"reservar", "reserva", "cita", "turno", "agendar", "servicio", "servicios"}
        if _normalize(service_name) in _GENERIC_SRV:
            service_name = None

    if not target_id:
        return {
            "success": False,
            "asking": "business",
            "message": "¿En qué negocio te gustaría agendar tu cita?",
        }

    # Si hay hora pero NO hay fecha, pedir fecha explícitamente
    if time and not date:
        return {
            "success": False,
            "needs_input": True,
            "asking": "date",
            # La hora viaja en su propio marcador `[HORA:…]`. Suelta al final
            # ("Hora: 08:30") el usuario la veía impresa, y metida dentro del
            # texto del marcador de confirmación dejaba de ser reconocible para
            # el limpiador, que sólo retira etiquetas en mayúsculas.
            "message": (
                f"Perfecto, a las **{time}** hay espacio en **{real_name}**. "
                "¿Para qué día lo dejamos? Puedes decirme *hoy*, *mañana* o una fecha.\n\n"
                f"[BIZ:{target_id}] [CONFIRMACIÓN NECESARIA] [HORA:{time}]"
            ),
            "reservation_name": reservation_name,  # ← parámetro que ya llega a la función
        }

    # Si hay hora Y servicio, verificamos profesionales antes de confirmar
    if time and service_name:
        # Resolver ID del servicio para buscar profesionales
        srv_data = await _resolve_service_id(target_id, service_name)
        if srv_data:
            professionals = await get_service_professionals(srv_data["id"])

            # Si hay exactamente 1 profesional, asignarlo automáticamente sin preguntar
            if len(professionals) == 1 and not professional_name:
                professional_name = professionals[0]["nombre"]
                logger.info(
                    f"AUTO-ASSIGN: Único profesional '{professional_name}' asignado automáticamente."
                )

            # Si hay múltiples y el usuario no eligió, preguntar — con anchors para contexto
            elif professionals and not professional_name:
                prof_items = []
                for p in professionals:
                    perfil_text = f" ({p['perfil'][:80]}...)" if p.get("perfil") else ""
                    prof_items.append(f"• **{p['nombre']}**{perfil_text}")

                # Calcular fecha legible
                from datetime import datetime, timedelta

                date_label = "mañana"
                if date == "today":
                    date_label = "hoy"
                elif date and date not in ("today", "tomorrow"):
                    date_label = f"el {date}"

                return {
                    "success": True,
                    "business_id": target_id,
                    "business_name": real_name,
                    "service_name": srv_data["nombre"],
                    "time": time,
                    "date": date,
                    "asking": "professional_name",
                    "professionals": [p["nombre"] for p in professionals],
                    "message": (
                        f"Para el servicio de **{srv_data['nombre']}** en **{real_name}** "
                        f"({date_label} a las **{time}**), ¿con quién te gustaría agendar?\n\n"
                        + "\n".join(prof_items)
                        + "\n\n¿Con quién prefieres, o te asigno a cualquiera disponible?"
                        f"\n\n[BIZ:{target_id}] [CONFIRMACIÓN NECESARIA] "
                        f"[SERVICIO:{srv_data['nombre']}] [HORA:{time}]"
                    ),
                }

            return await confirm_appointment(
                business_id=target_id,
                time=time,
                service_name=service_name,
                date=date,
                professional_name=professional_name,
                reservation_name=reservation_name,
                **kwargs,
            )
        else:
            # Sugerir servicios similares si no se encuentra el solicitado
            all_srvs = await get_business_services(business_id=target_id)
            similar_msg = ""
            if all_srvs.get("success") and all_srvs.get("services"):
                names = [s["nombre"] for s in all_srvs["services"][:5]]
                similar_msg = "\n\n**Tal vez te interese uno de estos:**\n" + "\n".join(
                    [f"• {n}" for n in names]
                )

            # Obtener contacto para alternativa
            biz_info = await fly_to_business(business_id=target_id)
            whatsapp = ""
            if biz_info.get("success"):
                whatsapp = biz_info["business"].get("whatsapp")

            contact_msg = (
                f"\n\nTambién puedes contactarlos directamente vía WhatsApp aquí: https://wa.me/{whatsapp}"
                if whatsapp
                else ""
            )

            msg = f"Lo siento, no encontré el servicio '**{service_name}**' en **{real_name}**."
            if not service_name or _normalize(service_name) in {"reservar", "reserva", "cita", "turno", "agendar", "servicio", "servicios"}:
                msg = f"¿Qué servicio deseas agendar en **{real_name}**?"

            return {
                "success": False,
                "asking": "service_name",
                "message": f"{msg}{similar_msg}{contact_msg}\n\n¿Deseas que busque en otro negocio o prefieres ver su catálogo completo?",
            }

    if not time:
        # Si hay servicio, filtramos disponibilidad por los profesionales que lo prestan
        p_ids = None
        srv_real_name = service_name
        if service_name:
            srv_data = await _resolve_service_id(target_id, service_name)
            if srv_data:
                srv_real_name = srv_data["nombre"]
                profs = await get_service_professionals(srv_data["id"])
                if profs:
                    p_ids = [p["id"] for p in profs]

        # Consultar disponibilidad real
        avail = await get_business_availability(
            business_id=target_id,
            date=date,
            professional_name=professional_name,
            professional_ids=p_ids,
        )
        # Se arma UNA sola respuesta, con una única pregunta al final. Antes se
        # encadenaban dos ("¿En qué otro horario…?" y "¿A qué hora…?") y sonaba
        # a formulario, no a alguien atendiendo.
        que_agenda = f"tu **{srv_real_name}**" if service_name else f"tu cita en **{real_name}**"
        partes = [f"Con gusto te ayudo a agendar {que_agenda}."]

        if avail.get("success") and avail.get("busy_slots"):
            by_day: dict = {}
            for s in avail["busy_slots"]:
                tramo = _format_slot(s)
                if tramo and tramo not in by_day.setdefault(s["date_label"], []):
                    by_day[s["date_label"]].append(tramo)

            dias = _natural_list([
                f"{day.lower()} {_natural_list(times)}"
                for day, times in by_day.items() if times
            ])
            if dias:
                partes.append(f"Ya tienen ocupado {dias}.")
            partes.append("¿A qué hora te viene bien?")
        else:
            partes.append("Tienen la agenda libre, así que puedes elegir la hora que prefieras.")
            partes.append("¿A qué hora te gustaría?")

        _srv_anchor = f" [SERVICIO:{service_name}]" if service_name else ""

        return {
            "success": True,
            "business_id": target_id,
            "business_name": real_name,
            "action": "navigate_to_booking",
            "url": f"/empresa/{target_id}#servicios",
            # Declarar QUÉ se está preguntando es lo que permite que el turno
            # siguiente se lea como su respuesta. Deducirlo buscando subcadenas
            # dentro del texto ya redactado fallaba en cuanto cambiaba una palabra.
            "asking": "time",
            "message": " ".join(partes) + f"\n\n[BIZ:{target_id}]{_srv_anchor}",
        }

    srv_data = await get_business_services(business_id=target_id)
    srv_list = ""
    if srv_data.get("success") and srv_data.get("services"):
        srvs = srv_data["services"][:10]
        srv_items = []
        for s in srvs:
            desc_text = f" ({s['descripcion'][:60]}...)" if s.get("descripcion") else ""
            srv_items.append(f"• **{s['nombre']}**{desc_text}")
        srv_list = "\n\n**¿Qué servicio deseas agendar?**\n" + "\n".join(srv_items)

    # Determinar texto de fecha
    date_text = "mañana"
    if date == "today":
        date_text = "hoy"
    elif date and date != "tomorrow":
        date_text = f"el {date}"

    return {
        "success": True,
        "asking": "service_name",
        "business_id": target_id,
        "business_name": real_name,
        "time": time,
        "action": "navigate_to_booking",
        "url": f"/empresa/{target_id}#servicios",
        "message": (
            f"¡Perfecto! Tengo disponibilidad en **{real_name}** para {date_text} a las **{time}**."
            f"{srv_list}"
            f"\n\nEscribe el nombre del servicio y confirmo tu cita. [BIZ:{target_id}]"
        ),
    }


async def confirm_appointment(
    business_id: int,
    time: str,
    service_name: str,
    date: str = None,
    professional_name: str = None,
    reservation_name: str = None,
    **kwargs,
) -> dict:
    """
    Realiza la inserción REAL en la tabla `agenda` y vincula profesional si aplica.

    Flujo:
    1. Resolver usuario (por external_user_id o reserva anónima con nombre).
    2. Validar/pedir nombre si el usuario es anónimo.
    3. Resolver servicio (ID) con búsqueda ultra-flexible.
    4. Resolver profesional (si se proporcionó).
    5. Parsear y normalizar fecha y hora.
    6. Insertar en `agenda` + `asignacionresponsableservicio`.
    7. Emitir evento Pusher para UI en tiempo real.

    Args:
        business_id:       ID del negocio donde se agenda.
        time:              Hora deseada (HH:MM o "2 pm").
        service_name:      Nombre del servicio a agendar.
        date:              Fecha en formato ISO, "today" o "tomorrow".
        professional_name: Nombre del profesional preferido (opcional).
        reservation_name:  Nombre del beneficiario de la reserva.
        **kwargs:          user_data con external_user_id.

    Returns:
        Dict con success, message y opcionalmente url y datos de la cita.
    """
    from core.database import get_connection
    from datetime import datetime, timedelta

    user_data = kwargs.get("user_data", {})
    external_user_id = user_data.get("external_user_id") or user_data.get("id")

    logger.info(
        "[CONFIRM_APPOINTMENT] biz=%s | srv=%s | time=%s | res_name=%s | ext_id=%s",
        business_id,
        service_name,
        time,
        reservation_name,
        external_user_id,
    )

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:

                # ── FIX 1: Obtener nombre real del negocio desde la DB ────────
                cur.execute(
                    "SELECT razonSocial FROM empresa WHERE id = %s LIMIT 1",
                    (business_id,),
                )
                biz_row = cur.fetchone()
                real_name = (
                    biz_row["razonSocial"] if biz_row else f"Negocio #{business_id}"
                )

                # ── Paso 1: Resolver usuario ──────────────────────────────────
                user_id = None
                user_name = None

                # Identificar si es un usuario real o una sesión demo/anónima
                is_real_user = external_user_id and str(external_user_id) not in ("user_client_demo", "unknown", "guest") and not str(external_user_id).startswith("anon_")

                if is_real_user:
                    # ✅ Seteamos user_id para indicar que hay sesión activa
                    user_id = external_user_id

                    # 1. Intentar obtener nombre desde la sesión (user_data) para evitar nombres de semilla
                    user_name = (
                        user_data.get("name")
                        or user_data.get("full_name")
                        or user_data.get("nombre")
                        or user_data.get("display_name")
                    )

                    # 2. Resolver Tercero ID vía idpersona (Relación Directa en vt_inventario)
                    cur.execute(
                        """
                        SELECT t.id, t.nombre 
                        FROM usuario u
                        JOIN tercero t ON u.idpersona = t.id
                        WHERE u.id = %s
                        LIMIT 1
                        """,
                        (external_user_id,),
                    )
                    tercero_row = cur.fetchone()
                    
                    if tercero_row:
                        # idtercero_final es el ID del perfil físico (tercero)
                        idtercero_final = tercero_row["id"]
                        # Si no vino nombre en user_data, usar el de la DB
                        if not user_name:
                            user_name = tercero_row["nombre"]
                    else:
                        # Si no hay tercero vinculado, usar el ID de usuario como fallback
                        idtercero_final = external_user_id
                        if not user_name:
                            user_name = "Usuario"

                    # El user_id para la tabla 'agenda' debe ser el ID de la cuenta (usuario)
                    # El idtercero_final es para tablas relacionales de personas
                    user_id_for_agenda = external_user_id
                    user_id_for_client = idtercero_final
                else:
                    # Caso anónimo: se resolverá más adelante con el nombre proporcionado
                    user_id = None
                    user_id_for_agenda = None
                    user_id_for_client = None

                # ── Paso 2: Validar nombre de reserva ─────────────────────────
                _GENERIC_NAMES = frozenset(
                    {
                        "usuario",
                        "cliente",
                        "invitado",
                        "anonimo",
                        "anónimo",
                        "alguien",
                        "persona",
                    }
                )
                _AFFIRMATIVE = frozenset(
                    {
                        "si",
                        "sí",
                        "yes",
                        "ok",
                        "claro",
                        "correcto",
                        "así es",
                        "exacto",
                        "ese",
                        "esa",
                    }
                )

                if not user_id:
                    # Sin sesión no se crea la reserva.
                    #
                    # Un nombre escrito en el chat no identifica a nadie: no hay
                    # forma de avisar al cliente, de que consulte su cita ni de
                    # que la cancele, y el negocio recibe una reserva que no
                    # puede verificar. Se conserva todo lo acordado y se pide
                    # entrar; en cuanto haya sesión, la cita se confirma sola.
                    logger.info(
                        "Reserva pendiente de autenticación | biz=%s srv=%s time=%s date=%s",
                        business_id, service_name, time, date,
                    )
                    return {
                        "success": False,
                        "needs_auth": True,
                        "pending_reservation": {
                            "business_id": business_id,
                            "business_name": real_name,
                            "service_name": service_name,
                            "professional_name": professional_name,
                            "time": time,
                            "date": date,
                        },
                        "message": (
                            "Tengo todo listo para dejarla agendada. Sólo falta que "
                            "entres a tu cuenta —o que crees una si aún no la tienes— "
                            "para poder confirmarla a tu nombre. En cuanto lo hagas, "
                            "termino de reservarla sin que tengas que repetirme nada."
                        ),
                    }

                else:
                    # ✅ Usuario logueado: user_name ya fue resuelto desde DB en el Paso 1.
                    # Si reservation_name es una afirmación ("si", "sí") o el propio user_name,
                    # simplemente usamos user_name. NUNCA pedimos confirmación al logueado.
                    if (
                        reservation_name
                        and reservation_name.strip().lower() not in _AFFIRMATIVE
                        and reservation_name.strip().lower() not in _GENERIC_NAMES
                        and len(reservation_name.strip()) >= 2
                    ):
                        # El usuario quiere reservar a nombre de otra persona
                        user_name = reservation_name.strip()
                        logger.info(
                            "Logged-in user %s reserving for: %s", user_id, user_name
                        )
                    else:
                        # Usar el nombre del propio usuario logueado, sin preguntar
                        logger.info(
                            "Logged-in user %s reserving as self: %s",
                            user_id,
                            user_name,
                        )
                        # user_name ya está seteado desde el Paso 1 — no hacer nada

                # ── Paso 3: Resolver servicio ──────────────────────────────────
                srv_data = await _resolve_service_id(business_id, service_name)
                if not srv_data:
                    return {
                        "success": False,
                        "message": f"Lo siento, no encontré el servicio '**{service_name}**' en **{real_name}**.",
                    }

                srv_id = srv_data["id"]
                srv_real_name = srv_data["nombre"]
                duration_min = srv_data["tiempoServicio"] or 30

                # ── Paso 4: Resolver profesional ───────────────────────────────
                professional_id = None
                if professional_name:
                    prof_clean = _normalize(professional_name)
                    profs = await get_service_professionals(srv_id)
                    for p in profs:
                        if prof_clean in _normalize(p["nombre"]):
                            professional_id = p["id"]
                            professional_name = p["nombre"]
                            break

                # ── Paso 5: ── FIX 2: Resolver fecha ──────────────────────────
                if not date:
                    return {
                        "success": False,
                        "needs_input": True,
                        "message": (
                            f"Para dejar tu **{srv_real_name}** en **{real_name}**, "
                            "¿qué día te viene bien? Dime *hoy*, *mañana* o una fecha."
                            f"\n\n[BIZ:{business_id}] [CONFIRMACIÓN NECESARIA] "
                            f"[SERVICIO:{srv_real_name}] [HORA:{time}]"
                        ),
                    }

                if date == "today":
                    target_date = datetime.now().strftime("%Y-%m-%d")
                elif date == "tomorrow":
                    target_date = (datetime.now() + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                else:
                    target_date = date

                # ── Parsear hora ───────────────────────────────────────────────
                if not time:
                    return {
                        "success": False,
                        "needs_input": True,
                        "message": (
                            f"¿A qué hora quieres tu **{srv_real_name}** en **{real_name}**?"
                            f"\n\n[BIZ:{business_id}] [CONFIRMACIÓN NECESARIA] "
                            f"[SERVICIO:{srv_real_name}]"
                        ),
                    }

                try:
                    if ":" not in str(time):
                        h_match = re.search(r"(\\d{1,2})", str(time))
                        if not h_match:
                            raise ValueError(f"Hora no reconocible: {time}")
                        h = int(h_match.group(1))
                        if "pm" in str(time).lower() and h < 12:
                            h += 12
                        start_time = f"{h:02d}:00:00"
                    else:
                        t_parts = str(time).split(":")
                        start_time = str(time) if len(t_parts) == 3 else f"{time}:00"

                    start_dt = datetime.strptime(start_time, "%H:%M:%S")
                    end_dt = start_dt + timedelta(minutes=duration_min)
                    end_time = end_dt.strftime("%H:%M:%S")

                except Exception as exc:
                    logger.error("Error parseando hora '%s': %s", time, exc)
                    return {
                        "success": False,
                        "needs_input": True,
                        "message": (
                            f"No pude entender la hora '**{time}**'. "
                            "¿Puedes indicarla en formato '2:30 pm' o '14:00'?"
                        ),
                    }

                # ── Paso 6: Insertar en agenda ─────────────────────────────────
                descripcion = (
                    f"Reserva vía Lyra: {srv_real_name} a nombre de {user_name}"
                )

                cur.execute(
                    """
                    INSERT INTO agenda
                        (idCompany, fechaInicial, horaInicial, horaFinal,
                         descripcion, estado, tipo, idUser, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'agendado', 'servicio', %s, NOW(), NOW())
                    """,
                    (
                        business_id,
                        target_date,
                        start_time,
                        end_time,
                        descripcion,
                        user_id_for_agenda,
                    ),
                )
                agenda_id = cur.lastrowid

                cur.execute(
                    """
                    INSERT INTO asignacionresponsableservicio
                        (idAgenda, idCliente, idServicio, idResponsable, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                    """,
                    (agenda_id, user_id_for_client, srv_id, professional_id),
                )
                conn.commit()

                # ── Paso 7: Emitir evento Pusher ───────────────────────────────
                from core.pusher import trigger_pusher_event

                trigger_pusher_event(
                    "lyra-channel",
                    "appointment_created",
                    {
                        "business_id": business_id,
                        "service_name": srv_real_name,
                        "professional_name": professional_name,
                        "user_id": user_id_for_agenda,
                        "external_user_id": external_user_id,
                    },
                )

                # ── Formatear fecha para el mensaje de confirmación ────────────
                today_str = datetime.now().strftime("%Y-%m-%d")
                tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

                if target_date == today_str:
                    date_msg = "hoy"
                elif target_date == tomorrow_str:
                    date_msg = "mañana"
                else:
                    date_msg = f"el {target_date}"

                prof_msg = f" con **{professional_name}**" if professional_name else ""

                return {
                    "success": True,
                    "business_id": business_id,
                    "service_name": srv_real_name,
                    "professional_name": professional_name,
                    "date": target_date,
                    "time": start_time[:5],
                    "action": "navigate",
                    "url": "/perfil/mis-reservas",
                    "message": (
                        f"¡Listo, **{user_name}**! Tu **{srv_real_name}**"
                        f"{prof_msg} queda para {date_msg} a las **{start_time[:5]}**.\n\n"
                        "Te dejo en **Mis Reservas** por si quieres revisarla. ¡Nos vemos! ✨"
                    ),
                }

    except Exception as exc:
        logger.error("Error insertando reserva: %s", exc, exc_info=True)
        return {
            "success": False,
            "message": (
                "Ocurrió un error técnico al crear tu reserva. "
                "Por favor, inténtalo nuevamente."
            ),
        }


async def _resolve_service_id(business_id: int, service_name: str) -> dict:
    """Helper para resolver el ID de un servicio en un negocio con búsqueda ultra-flexible."""
    from core.database import get_connection
    import re

    if not service_name:
        return None

    # Términos genéricos que no son servicios reales
    _GENERIC_SRV = {"reservar", "reserva", "cita", "turno", "agendar", "servicio", "servicios"}
    if _normalize(service_name) in _GENERIC_SRV:
        return None

    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                # Normalización relajada: convertimos cualquier secuencia no-alfanumérica en %
                # para que 'Semi-Sintético' matchee con 'semi sintetico' o 'semi-sintetico'
                raw_clean = _normalize(service_name)
                # Reemplazamos espacios por % si _normalize no los quitó,
                # pero _normalize quita casi todo. Mejor trabajar sobre el original para el pattern.
                import unicodedata

                nfkd = unicodedata.normalize("NFKD", str(service_name))
                pattern = "".join(
                    c for c in nfkd if not unicodedata.combining(c)
                ).lower()
                pattern = re.sub(r"[^a-z0-9]+", "%", pattern).strip("%")

                if not pattern:
                    return None

                sql = "SELECT id, nombre, tiempoServicio, descripcion FROM servicios WHERE idCompany = %s AND LOWER(nombre) LIKE %s LIMIT 1"
                cur.execute(sql, (business_id, f"%{pattern}%"))
                return cur.fetchone()
    except Exception:
        return None
