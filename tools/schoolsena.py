"""
tools/schoolsena.py — SchoolSena tool handlers for Lyra AI.

These tools query the SchoolSena Laravel API to retrieve academic and 
administrative data for the authenticated user.
"""

import logging
import httpx
import os

logger = logging.getLogger(__name__)

SCHOOLSENA_API_BASE = os.getenv("SCHOOLSENA_API_BASE", "http://127.0.0.1:8000/api")


async def _api_get(endpoint: str, auth_header: str, params: dict = None) -> dict | list:
    """Helper to call SchoolSena Laravel API with forwarded auth."""
    url = f"{SCHOOLSENA_API_BASE}/{endpoint.lstrip('/')}"
    headers = {}
    if auth_header:
        headers["Authorization"] = auth_header
    else:
        logger.warning(f"No auth header found for endpoint {endpoint}")
    headers["Accept"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"SchoolSena API {endpoint} returned {resp.status_code}: {resp.text[:200]}")
            return {"error": f"API respondió con código {resp.status_code}"}
    except httpx.ConnectError:
        logger.error(f"No se pudo conectar a SchoolSena API: {url}")
        return {"error": "No se pudo conectar al servidor de SchoolSena"}
    except Exception as e:
        logger.error(f"Error llamando SchoolSena API: {e}")
        return {"error": str(e)}


def _safe_result(data) -> dict:
    """Normalize API response into {result, error} regardless of shape (dict or list)."""
    if isinstance(data, list):
        return {"result": data, "error": None}
    if isinstance(data, dict):
        err = data.get("error")
        return {"result": data.get("data", data), "error": err if err else None}
    return {"result": data, "error": None}


# ── TOOL HANDLERS (Flexible signatures for LegacyToolAdapter) ────────────────

async def get_clases_hoy(params=None, context=None, **kwargs):
    """Gets today's classes for the authenticated user."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/horario-materia/hoy", auth)
    return _safe_result(data)


async def get_entregas(params=None, context=None, **kwargs):
    """Gets submission status for a specific activity."""
    p = params or kwargs
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    actividad_id = p.get("actividad_id", "")
    data = await _api_get(f"lyra/actividades/{actividad_id}/respuestas", auth)
    return _safe_result(data)


async def get_estudiantes_ficha(params=None, context=None, **kwargs):
    """Gets the number of students in a training group (ficha)."""
    p = params or kwargs
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    ficha_id = p.get("ficha_id", "")
    data = await _api_get(f"lyra/fichas/{ficha_id}/estudiantes", auth)
    return _safe_result(data)


async def get_actividades_pendientes(params=None, context=None, **kwargs):
    """Gets pending activities for the authenticated student."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/actividades/pendientes", auth)
    return _safe_result(data)


async def get_horario(params=None, context=None, **kwargs):
    """Gets the weekly schedule for the authenticated user."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/horario-materia/semana", auth)
    return _safe_result(data)


async def get_notas(params=None, context=None, **kwargs):
    """Gets grades for the authenticated student."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/notas/mis-notas", auth)
    return _safe_result(data)


async def get_resumen_admin(params=None, context=None, **kwargs):
    """Gets a system summary for administrators (active instructors, ficha counts)."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/dashboard/resumen", auth)
    return _safe_result(data)


async def get_fichas_activas(params=None, context=None, **kwargs):
    """Gets a list of all active training groups (fichas). Useful for coordinators."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/fichas/activas", auth)
    return _safe_result(data)


async def get_nomina_resumen(params=None, context=None, **kwargs):
    """Gets a payroll summary for the company/center."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/nomina/resumen", auth)
    return _safe_result(data)


async def get_lista_instructores(params=None, context=None, **kwargs):
    """Gets a list of active instructors for the coordinator."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/instructores/lista", auth)
    return _safe_result(data)


async def get_contratos_vencimiento(params=None, context=None, **kwargs):
    """Gets contracts expiring in the next 30 days."""
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    data = await _api_get("lyra/contratos/vencimiento", auth)
    return _safe_result(data)


# ── SCHEMAS dict for auto-discovery ──
SCHEMAS = {
    "get_clases_hoy": {
        "name": "get_clases_hoy",
        "description": "Obtiene las clases del día actual para el usuario autenticado",
        "parameters": {}
    },
    "get_entregas": {
        "name": "get_entregas",
        "description": "Obtiene estudiantes que han entregado o no una actividad",
        "parameters": {
            "actividad_id": {"type": "integer", "description": "ID de la actividad"}
        },
        "required": ["actividad_id"]
    },
    "get_estudiantes_ficha": {
        "name": "get_estudiantes_ficha",
        "description": "Obtiene cantidad de estudiantes en una ficha",
        "parameters": {
            "ficha_id": {"type": "integer", "description": "ID de la ficha"}
        },
        "required": ["ficha_id"]
    },
    "get_actividades_pendientes": {
        "name": "get_actividades_pendientes",
        "description": "Obtiene las actividades pendientes del estudiante",
        "parameters": {}
    },
    "get_horario": {
        "name": "get_horario",
        "description": "Obtiene el horario semanal completo",
        "parameters": {}
    },
    "get_notas": {
        "name": "get_notas",
        "description": "Obtiene las calificaciones del estudiante",
        "parameters": {}
    },
    "get_resumen_admin": {
        "name": "get_resumen_admin",
        "description": "Obtiene un resumen administrativo general",
        "parameters": {}
    },
    "get_fichas_activas": {
        "name": "get_fichas_activas",
        "description": "Lista todas las fichas (grupos) activas",
        "parameters": {}
    },
    "get_nomina_resumen": {
        "name": "get_nomina_resumen",
        "description": "Obtiene el costo total de la nómina mensual para la coordinación",
        "parameters": {}
    },
    "get_lista_instructores": {
        "name": "get_lista_instructores",
        "description": "Lista todos los instructores activos",
        "parameters": {}
    },
    "get_contratos_vencimiento": {
        "name": "get_contratos_vencimiento",
        "description": "Lista contratos próximos a vencer (30 días)",
        "parameters": {}
    },
}

TOOLS_REGISTRY = {
    name: {"schema": schema, "handler": globals()[name]}
    for name, schema in SCHEMAS.items()
}
