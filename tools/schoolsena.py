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
            # Cada fallo dice QUÉ pasó: no es lo mismo "no hay registros" que
            # "no tienes permiso", "esa consulta no existe en este servidor" o
            # "no pude hablar con SchoolSena". Ninguno se presenta como vacío.
            if resp.status_code == 401:
                return {"error": "tu sesión expiró o no es válida; vuelve a iniciar sesión", "error_kind": "auth"}
            if resp.status_code == 403:
                return {"error": "tu usuario no tiene permiso para consultar esta información", "error_kind": "forbidden"}
            if resp.status_code == 404:
                try:
                    cuerpo = resp.json()
                except ValueError:
                    cuerpo = None
                if isinstance(cuerpo, dict) and cuerpo.get("error"):
                    # 404 que responde el controlador: lo pedido no existe.
                    return {"error": cuerpo["error"], "error_kind": "not_found"}
                # 404 de Laravel sin cuerpo propio: la ruta no está en este servidor.
                return {"error": "esa consulta no está disponible en este servidor de SchoolSena",
                        "error_kind": "no_route"}
            return {"error": f"el servidor de SchoolSena respondió con un error ({resp.status_code})",
                    "error_kind": "server"}
    except httpx.ConnectError:
        logger.error(f"No se pudo conectar a SchoolSena API: {url}")
        return {"error": "No se pudo conectar al servidor de SchoolSena", "error_kind": "unreachable"}
    except httpx.TimeoutException:
        logger.error(f"Timeout llamando SchoolSena API: {url}")
        return {"error": "el servidor de SchoolSena tardó demasiado en responder", "error_kind": "timeout"}
    except Exception as e:
        logger.error(f"Error llamando SchoolSena API: {e}")
        return {"error": "no pude consultar SchoolSena en este momento", "error_kind": "server"}


def _safe_result(data) -> dict:
    """Normalize API response into {result, error, error_kind} regardless of shape (dict or list)."""
    if isinstance(data, list):
        return {"result": data, "error": None}
    if isinstance(data, dict):
        err = data.get("error")
        out = {"result": data.get("data", data), "error": err if err else None}
        if err:
            out["error_kind"] = data.get("error_kind") or "server"
        return out
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
    actividad_id = str(p.get("actividad_id", "")).strip()
    if not actividad_id.isdigit():
        return {"result": None, "error": "falta el número de la actividad"}
    data = await _api_get(f"lyra/actividades/{actividad_id}/respuestas", auth)
    return _safe_result(data)


async def get_estudiantes_ficha(params=None, context=None, **kwargs):
    """
    Aprendices de una ficha, por su CÓDIGO (el número que la gente conoce).

    Si el backend todavía no tiene la ruta por código (servidor sin actualizar),
    se intenta por el id interno, que es lo que había antes.
    """
    p = params or kwargs
    ctx = context or kwargs
    auth = ctx.get("auth_header", "")
    ficha = str(p.get("ficha_id", "")).strip()
    if not ficha:
        return {"result": None, "error": "falta el número de la ficha", "error_kind": "missing"}
    data = await _api_get(f"lyra/fichas/codigo/{ficha}/estudiantes", auth)
    if isinstance(data, dict) and data.get("error_kind") == "no_route" and ficha.isdigit():
        data = await _api_get(f"lyra/fichas/{ficha}/estudiantes", auth)
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
async def _consulta(ruta: str, ctx: dict) -> dict:
    return _safe_result(await _api_get(ruta, (ctx or {}).get("auth_header", "")))


async def get_capacidades(params=None, context=None, **kwargs):
    """Consultas y secciones habilitadas por los permisos efectivos del usuario."""
    return await _consulta("lyra/capacidades", context or kwargs)


async def get_centros_formacion(params=None, context=None, **kwargs):
    """Centros de formación de la empresa del usuario."""
    return await _consulta("lyra/centros-formacion", context or kwargs)


async def get_sedes(params=None, context=None, **kwargs):
    """Sedes de la empresa del usuario."""
    return await _consulta("lyra/sedes", context or kwargs)


async def get_ambientes(params=None, context=None, **kwargs):
    """Ambientes de formación (infraestructura) de las sedes de la empresa."""
    return await _consulta("lyra/ambientes", context or kwargs)


async def get_periodos(params=None, context=None, **kwargs):
    """Periodos académicos: los de la empresa, o los propios si no gestiona periodos."""
    return await _consulta("lyra/periodos", context or kwargs)


async def get_jornadas(params=None, context=None, **kwargs):
    """Jornadas de los centros de formación de la empresa."""
    return await _consulta("lyra/jornadas", context or kwargs)


async def get_regionales(params=None, context=None, **kwargs):
    """Regionales registradas."""
    return await _consulta("lyra/regionales", context or kwargs)


async def get_eventos(params=None, context=None, **kwargs):
    """Eventos de la empresa (opcional: estado)."""
    ctx = context or kwargs
    p = {k: v for k, v in (params or {}).items() if k == "estado" and v}
    return _safe_result(await _api_get("lyra/eventos", ctx.get("auth_header", ""), p))


async def get_formularios(params=None, context=None, **kwargs):
    """Formularios de la empresa (opcional: estado)."""
    ctx = context or kwargs
    p = {k: v for k, v in (params or {}).items() if k == "estado" and v}
    return _safe_result(await _api_get("lyra/formularios", ctx.get("auth_header", ""), p))


async def get_carpetas_viajeras(params=None, context=None, **kwargs):
    """Carpetas viajeras de las personas de la empresa."""
    return await _consulta("lyra/carpetas-viajeras", context or kwargs)


async def get_configuracion_pagos(params=None, context=None, **kwargs):
    """Conceptos de la configuración de pagos de la empresa."""
    return await _consulta("lyra/configuracion-pagos", context or kwargs)


async def get_pagos_pendientes(params=None, context=None, **kwargs):
    """Pagos de contratos pendientes de la empresa."""
    return await _consulta("lyra/pagos-pendientes", context or kwargs)


async def get_solicitudes_inscripcion(params=None, context=None, **kwargs):
    """Solicitudes de inscripción de la empresa."""
    return await _consulta("lyra/solicitudes-inscripcion", context or kwargs)


async def get_configuracion_inscripcion(params=None, context=None, **kwargs):
    """Configuración de inscripciones de la empresa (un solo objeto)."""
    res = await _consulta("lyra/configuracion-inscripcion", context or kwargs)
    if not res.get("error") and isinstance(res.get("result"), dict):
        res["result"] = [res["result"]]
    return res


async def get_multimedia(params=None, context=None, **kwargs):
    """Historias y reels de la empresa."""
    ctx = context or kwargs
    p = {k: v for k, v in (params or {}).items() if k == "tipo" and v}
    return _safe_result(await _api_get("lyra/multimedia", ctx.get("auth_header", ""), p))


async def get_informes_instructores(params=None, context=None, **kwargs):
    """Informes de actividades de instructores."""
    return await _consulta("lyra/informes-instructores", context or kwargs)


async def crear_formulario(params=None, context=None, **kwargs):
    """Crea un formulario en borrador (POST lyra/formularios)."""
    return await _escribir("lyra/formularios", params, context or kwargs, "crear formularios")


async def crear_evento(params=None, context=None, **kwargs):
    """
    Crea un evento (POST lyra/eventos). El backend exige el permiso efectivo, la
    empresa del token y valida los datos; aquí sólo se envía y se interpreta.
    """
    return await _escribir("lyra/eventos", params, context or kwargs, "crear eventos")


async def get_formulario(params=None, context=None, **kwargs):
    """Estructura real de un formulario (preguntas, opciones, versión, eventos vinculados)."""
    p, ctx = params or {}, context or kwargs
    return _safe_result(await _api_get(f"lyra/formularios/{int(p['id'])}", ctx.get("auth_header", "")))


async def guardar_formulario(params=None, context=None, **kwargs):
    """PUT lyra/formularios/{id}: título, descripción y preguntas, con la versión leída (409 si cambió)."""
    p = dict(params or {})
    fid = int(p.pop("id"))
    return await _escribir(f"lyra/formularios/{fid}", p, context or kwargs, "modificar formularios", "PUT")


async def estado_formulario(params=None, context=None, **kwargs):
    p = dict(params or {})
    fid = int(p.pop("id"))
    return await _escribir(f"lyra/formularios/{fid}/estado", p, context or kwargs, "publicar formularios", "PUT")


async def get_evento(params=None, context=None, **kwargs):
    """Detalle de un evento con su formulario de inscripción y el número de inscritos."""
    p, ctx = params or {}, context or kwargs
    return _safe_result(await _api_get(f"lyra/eventos/{int(p['id'])}", ctx.get("auth_header", "")))


async def vincular_formulario(params=None, context=None, **kwargs):
    """PUT lyra/eventos/{id}/formulario {idFormulario|null}."""
    p = dict(params or {})
    eid = int(p.pop("id"))
    return await _escribir(f"lyra/eventos/{eid}/formulario", {"idFormulario": p.get("idFormulario")},
                           context or kwargs, "modificar eventos", "PUT")


async def _escribir(ruta: str, params, ctx, que: str, metodo: str = "POST"):
    auth = ctx.get("auth_header", "")
    url = f"{SCHOOLSENA_API_BASE}/{ruta}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request(metodo, url, json=dict(params or {}),
                                        headers={"Authorization": auth, "Accept": "application/json"})
    except httpx.TimeoutException:
        return {"result": None, "error": "SchoolSena no respondió a tiempo", "error_kind": "timeout"}
    except httpx.HTTPError:
        return {"result": None, "error": "No se pudo conectar al servidor de SchoolSena", "error_kind": "unreachable"}
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code in (200, 201):
        return {"result": data, "error": None}
    if resp.status_code == 422:
        errores = data.get("errors") or {}
        detalle = "; ".join(f"{k}: {v[0] if isinstance(v, list) else v}" for k, v in errores.items())
        return {"result": None, "error": detalle or "datos no válidos", "error_kind": "validation"}
    if resp.status_code == 403:
        return {"result": None, "error": f"tu usuario no tiene permiso para {que}", "error_kind": "forbidden"}
    if resp.status_code == 409:
        return {"result": data.get("actual"), "error": data.get("error") or "el registro cambió", "error_kind": "conflict"}
    if resp.status_code == 404:
        return {"result": None, "error": data.get("error") or "no existe", "error_kind": "not_found"}
    if resp.status_code == 401:
        return {"result": None, "error": "tu sesión expiró; vuelve a iniciar sesión", "error_kind": "auth"}
    return {"result": None, "error": f"el servidor respondió con un error ({resp.status_code})", "error_kind": "server"}


async def get_planeacion(params=None, context=None, **kwargs):
    """Planeación académica: redes de conocimiento y sus programas."""
    return await _consulta("lyra/planeacion", context or kwargs)


SCHEMAS = {
    "get_centros_formacion": {"name": "get_centros_formacion", "description": "Lista los centros de formación de la empresa", "parameters": {}},
    "get_sedes": {"name": "get_sedes", "description": "Lista las sedes de la empresa", "parameters": {}},
    "get_ambientes": {"name": "get_ambientes", "description": "Lista los ambientes de formación (aulas, talleres) de las sedes", "parameters": {}},
    "get_periodos": {"name": "get_periodos", "description": "Lista los periodos académicos (de la empresa o los propios)", "parameters": {}},
    "get_jornadas": {"name": "get_jornadas", "description": "Lista las jornadas de formación", "parameters": {}},
    "get_planeacion": {"name": "get_planeacion", "description": "Resume la planeación académica: redes y programas", "parameters": {}},
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
# Uso interno (no se ofrece al modelo): qué puede hacer Nexo por este usuario.
TOOLS_REGISTRY["get_capacidades"] = {"schema": None, "handler": get_capacidades}
# Consultas de módulos y la única escritura integrada. No se ofrecen al modelo:
# las ejecuta el registro de módulos tras comprobar permiso e intención.
for _n in ("get_regionales", "get_eventos", "get_formularios", "get_carpetas_viajeras",
           "get_configuracion_pagos", "crear_evento", "crear_formulario", "get_pagos_pendientes",
           "get_solicitudes_inscripcion", "get_configuracion_inscripcion", "get_multimedia",
           "get_informes_instructores", "get_formulario", "guardar_formulario", "estado_formulario", "get_evento",
           "vincular_formulario"):
    TOOLS_REGISTRY[_n] = {"schema": None, "handler": globals()[_n]}
