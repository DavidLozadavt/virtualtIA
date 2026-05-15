"""
tools/rentus.py — Tool functions for the Rentus project.
"""
import logging
import httpx
from core.config import settings

logger = logging.getLogger("lyra.tools.rentus")
RENTUS_API = settings.RENTUS_API_BASE
TIMEOUT = 10.0

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _truncate_property(p: dict) -> dict:
    """Normaliza y extrae solo los campos necesarios de una propiedad."""
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "price": p.get("monthly_price"),   # La BD usa monthly_price
        "city": p.get("city"),
        "neighborhood": p.get("neighborhood") or p.get("zone") or "",
        "bedrooms": p.get("bedrooms"),
        "lat": p.get("lat"),
        "lng": p.get("lng"),
    }


def _parse_properties_from_response(data) -> list[dict]:
    """Extrae la lista de propiedades de cualquier estructura de respuesta de la API."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Intenta data.data.data, data.data, data
        inner = data.get("data", data)
        if isinstance(inner, dict):
            inner = inner.get("data", inner)
        if isinstance(inner, list):
            return inner
    return []


# ─── Tools ────────────────────────────────────────────────────────────────────

async def search_properties(
    location: str = None,
    property_type: str = None,
    max_price: float = 0,
    min_rooms: int = 0,
) -> dict:
    """
    Busca propiedades disponibles en Rentus.
    Si location es None, busca en todas las ciudades (modo recomendación general).
    Devuelve máximo 5 resultados para no saturar el contexto del LLM.
    """
    params: dict = {}
    if location and location.strip():
        params["city"] = location.strip()
    if property_type and property_type.strip():
        params["type"] = property_type.strip()
    if max_price > 0:
        params["max_price"] = str(int(max_price))
    if min_rooms > 0:
        params["bedrooms"] = str(min_rooms)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{RENTUS_API}/properties", params=params)

        if response.status_code != 200:
            return {
                "success": False,
                "data": [],
                "message": f"Error de API ({response.status_code}). Intenta de nuevo.",
            }

        raw = response.json()
        all_properties = _parse_properties_from_response(raw)
        total = len(all_properties)

        # Limitar a 5 resultados para eficiencia de tokens
        # IMPORTANTE: count refleja los resultados reales que se pasan al LLM,
        # no el total de la BD, para que el LLM no alucine sobre propiedades que no ve.
        truncated = [_truncate_property(p) for p in all_properties[:5]]

        suffix = f" (mostrando las {len(truncated)} mejores)" if total > 5 else ""
        return {
            "success": True,
            "count": len(truncated),  # Deliberadamente el count de lo que ve el LLM
            "data": truncated,
            "message": f"Encontré {total} propiedades disponibles{suffix}.",
        }

    except httpx.TimeoutException:
        logger.warning("search_properties: timeout")
        return {"success": False, "data": [], "message": "La búsqueda tardó demasiado. ¿Intentamos de nuevo?"}
    except Exception as e:
        logger.error(f"search_properties error: {e}")
        return {"success": False, "data": [], "message": "Error de conexión con el servidor."}


class SearchPropertiesTool:
    TOOL_NAME = "search_properties"
    TOOL_SCHEMA = {
        "name": "search_properties",
        "description": "Busca propiedades disponibles en Rentus según los filtros del usuario. Úsala cuando el usuario pida buscar, recomendar o filtrar inmuebles. Devuelve una lista de propiedades con id, title, price, city, lat, lng.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Ciudad o barrio donde buscar (ej: Medellín, El Poblado, Laureles)",
                },
                "property_type": {
                    "type": "string",
                    "description": "Tipo de inmueble: apartamento, casa, local, finca, estudio, oficina, bodega, lote",
                },
                "max_price": {
                    "type": "number",
                    "description": "Precio máximo mensual en pesos colombianos (COP)",
                },
                "min_rooms": {
                    "type": "integer",
                    "description": "Número mínimo de habitaciones requeridas",
                },
            },
        },
    }

    async def execute(self, params: dict, context: dict) -> dict:
        return await search_properties(**params)


async def get_property_detail(property_id: str) -> dict:
    """Obtiene el detalle completo de una propiedad por ID."""
    if not property_id or str(property_id).strip() in ("", "0"):
        return {"success": False, "data": None, "message": "Se requiere un ID de propiedad válido."}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{RENTUS_API}/properties/{property_id}")

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "data": data.get("data", data),
                "message": "Detalles obtenidos correctamente.",
            }
        if response.status_code == 404:
            return {"success": False, "data": None, "message": f"No se encontró la propiedad con ID {property_id}."}

        return {"success": False, "data": None, "message": f"Error de API ({response.status_code})."}

    except Exception as e:
        logger.error(f"get_property_detail error: {e}")
        return {"success": False, "data": None, "message": "Error obteniendo los detalles."}


class GetPropertyDetailTool:
    TOOL_NAME = "get_property_detail"
    TOOL_SCHEMA = {
        "name": "get_property_detail",
        "description": "Obtiene el detalle completo de una propiedad por ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "ID único de la propiedad",
                }
            },
            "required": ["property_id"],
        },
    }

    async def execute(self, params: dict, context: dict) -> dict:
        return await get_property_detail(**params)


async def view_property(property_id: str) -> dict:
    """
    Interceptada por el orquestador para disparar la acción de vista en el frontend.
    Aquí devuelve los datos para que el LLM pueda narrar los detalles al usuario.
    """
    return await get_property_detail(property_id)


class ViewPropertyTool:
    TOOL_NAME = "view_property"
    TOOL_SCHEMA = {
        "name": "view_property",
        "description": "Abre una propiedad específica en la pantalla del usuario para ver fotos, detalles y recorrido 3D. Úsala SOLO cuando el usuario pida ver más detalles, fotos, o diga 'sí' en respuesta a una propiedad que acaban de discutir.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "ID numérico de la propiedad — debe ser un ID real mencionado en la conversación.",
                }
            },
            "required": ["property_id"],
        },
    }

    async def execute(self, params: dict, context: dict) -> dict:
        return await view_property(**params)


async def schedule_visit(
    property_id: str = "",
    user_id: str = "",
    preferred_date: str = "",
    preferred_time: str = "",
    _auth_header: str = "",
) -> dict:
    """
    Agenda una visita a una propiedad.
    El user_id es sobreescrito por el orquestador con el ID de sesión real.
    Si el usuario no está autenticado (prefijo va_), retorna auth_required.
    """
    # Bloqueo preventivo: usuarios invitados del Voice Assistant
    if not user_id or user_id.startswith("va_"):
        return {
            "success": False,
            "error_type": "auth_required",
            "message": "Debes iniciar sesión para agendar una visita. ¿Te gustaría hacerlo ahora?",
        }

    if not property_id:
        return {"success": False, "data": None, "message": "Falta el ID de la propiedad."}

    if not preferred_date:
        return {"success": False, "data": None, "message": "Falta la fecha preferida para la visita."}
    if not preferred_time:
        return {"success": False, "data": None, "message": "Falta la hora preferida para la visita."}

    try:
        payload = {
            "property_id": property_id,
            "requested_date": preferred_date,
            "requested_time": preferred_time,
        }
        headers = {"Authorization": _auth_header} if _auth_header else {}

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{RENTUS_API}/rental-requests",
                json=payload,
                headers=headers,
            )

        success = response.status_code in (200, 201)
        data = None
        try:
            data = response.json()
        except Exception:
            pass

        if response.status_code in (401, 403):
            return {
                "success": False,
                "error_type": "auth_required",
                "message": "Debes iniciar sesión para agendar una visita. ¿Te gustaría hacerlo ahora?",
            }

        if success:
            return {
                "success": True,
                "data": data,
                "message": f"Visita agendada para el {preferred_date} a las {preferred_time}.",
            }

        # Error conocido de la API
        api_message = (data or {}).get("message", "") if isinstance(data, dict) else ""
        return {
            "success": False,
            "data": None,
            "message": api_message or f"No se pudo agendar la visita (error {response.status_code}).",
        }

    except httpx.TimeoutException:
        return {"success": False, "data": None, "message": "El servidor tardó demasiado. Intenta de nuevo."}
    except Exception as e:
        logger.error(f"schedule_visit error: {e}")
        return {"success": False, "data": None, "message": "Error al agendar la visita."}


class ScheduleVisitTool:
    TOOL_NAME = "schedule_visit"
    TOOL_SCHEMA = {
        "name": "schedule_visit",
        "description": "Agenda una visita presencial a una propiedad. El user_id ya está disponible en la sesión — NO lo pidas al usuario. Si el usuario no dio fecha u hora, pídelas antes de llamar a esta herramienta.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "ID numérico de la propiedad a visitar",
                },
                "preferred_date": {
                    "type": "string",
                    "description": "Fecha preferida en formato YYYY-MM-DD",
                },
                "preferred_time": {
                    "type": "string",
                    "description": "Hora preferida en formato HH:MM (24h)",
                },
            },
            "required": ["property_id", "preferred_date", "preferred_time"],
        },
    }

    async def execute(self, params: dict, context: dict) -> dict:
        # Extraer auth y user_id del contexto si están disponibles
        auth = context.get("auth_header", "")
        user_id = context.get("user_data", {}).get("id") or context.get("user_id")

        return await schedule_visit(
            **params, user_id=user_id, _auth_header=auth
        )


# Instanciar herramientas para el auto-descubrimiento del ToolRegistry
search_properties_tool = SearchPropertiesTool()
get_property_detail_tool = GetPropertyDetailTool()
view_property_tool = ViewPropertyTool()
schedule_visit_tool = ScheduleVisitTool()
