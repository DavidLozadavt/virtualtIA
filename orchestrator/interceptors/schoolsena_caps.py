"""
orchestrator/interceptors/schoolsena_caps.py — Qué puede hacer Nexo por ESTE usuario.

Antes se decidía por el nombre del rol ("contiene ADMIN → nómina"). En School
los permisos se asignan por rol y por usuario: dos aprendices pueden tener
capacidades distintas, un administrador puede tenerlas restringidas y aparecen
permisos nuevos. La fuente de verdad es el backend (`GET lyra/capacidades`), que
calcula los permisos efectivos en vivo y además autoriza cada consulta.

Aquí se distingue:
- consulta habilitada: Nexo SABE hacerla y el usuario tiene permiso;
- sección navegable: el usuario tiene permiso a esa pantalla (con ruta), aunque
  Nexo no sepa consultar sus datos — un permiso nuevo cae aquí sin tocar código;
- sin permiso: ni una ni otra.

Ocultar algo en la conversación NO es la seguridad: el backend vuelve a
autorizar cada consulta con el token del usuario.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Corta: un permiso revocado deja de ofrecerse enseguida (el backend, además,
# vuelve a autorizar cada consulta; y un 403 vacía la caché al momento).
_TTL_S = 60
_KEY = "_school_caps"

#: Capacidad de Lyra → consulta del backend (la primera que esté habilitada).
CAP_QUERY = {
    "schedule": ("horario",), "activities": ("pendientes",), "grades": ("notas",),
    "fichas": ("fichas",), "students": ("estudiantes",), "deliveries": ("entregas",),
    "payroll": ("nomina",), "instructors": ("instructores",), "contracts": ("contratos",),
    "dashboard": ("resumen",), "centers": ("centros",), "sites": ("sedes",), "rooms": ("ambientes",),
    "periods": ("periodos", "periodos_propios"), "shifts": ("jornadas",), "planning": ("planeacion",),
}

#: Herramienta de datos de cada consulta (para ofrecérselas al modelo).
QUERY_TOOL = {
    "clases": "get_clases_hoy", "horario": "get_horario", "notas": "get_notas",
    "pendientes": "get_actividades_pendientes", "fichas": "get_fichas_activas",
    "estudiantes": "get_estudiantes_ficha", "entregas": "get_entregas", "resumen": "get_resumen_admin",
    "nomina": "get_nomina_resumen", "instructores": "get_lista_instructores",
    "contratos": "get_contratos_vencimiento", "centros": "get_centros_formacion", "sedes": "get_sedes",
    "ambientes": "get_ambientes", "periodos": "get_periodos", "periodos_propios": "get_periodos",
    "jornadas": "get_jornadas", "planeacion": "get_planeacion",
}

#: Cómo se nombra cada consulta al ofrecerla ("Puedo mostrarte ...").
_OFFER_NAME = {
    "clases": "tus clases", "notas": "tus notas", "pendientes": "tus actividades pendientes",
    "fichas": "las fichas", "estudiantes": "los aprendices de una ficha", "entregas": "las entregas de una actividad",
    "resumen": "el resumen del centro", "nomina": "la nómina", "instructores": "los instructores",
    "contratos": "los contratos por vencer", "centros": "los centros de formación", "sedes": "las sedes",
    "ambientes": "los ambientes", "periodos": "los periodos", "periodos_propios": "tus periodos",
    "jornadas": "las jornadas", "planeacion": "la planeación",
}
_QUICK = {
    "clases": ("Clases de hoy", "clases de hoy"), "pendientes": ("Actividades pendientes", "actividades pendientes"),
    "notas": ("Mis notas", "mis notas"), "fichas": ("Fichas activas", "fichas activas"),
    "contratos": ("Contratos por vencer", "contratos por vencer"), "resumen": ("Resumen del centro", "resumen del centro"),
    "centros": ("Centros de formación", "qué centros de formación hay"), "periodos": ("Periodos", "qué periodos hay"),
}
#: Orden de presentación: primero lo de gestión (lo que distingue a este usuario).
_ORDER = ["fichas", "instructores", "contratos", "nomina", "resumen", "centros", "sedes", "ambientes",
          "periodos", "jornadas", "planeacion", "entregas", "estudiantes", "clases", "pendientes", "notas"]


def _token_hash(auth_header: str) -> str:
    return hashlib.sha256((auth_header or "").encode("utf-8")).hexdigest()[:16]


async def load(context: dict, final_data: dict) -> Optional[dict]:
    """Capacidades del usuario (en caché 5 min por token). None si no se pudieron consultar."""
    auth = context.get("auth_header") or ""
    if not auth:
        return None
    th = _token_hash(auth)
    hit = final_data.get(_KEY)
    if isinstance(hit, dict) and hit.get("tok") == th and time.time() - float(hit.get("ts") or 0) < _TTL_S:
        return hit
    from tools.schoolsena import TOOLS_REGISTRY
    try:
        res = await TOOLS_REGISTRY["get_capacidades"]["handler"]({}, context)
    except Exception as exc:  # backend caído: el backend autorizará igual cada consulta
        logger.warning("No pude leer las capacidades de School: %s", exc)
        return None
    data = res.get("result") if not res.get("error") else None
    if not isinstance(data, dict) or "consultas" not in data:
        logger.info("Capacidades de School no disponibles (%s)", res.get("error_kind") or "sin datos")
        return None
    caps = {"tok": th, "ts": time.time(), "consultas": list(data.get("consultas") or []),
            "secciones": list(data.get("secciones") or []), "permisos": list(data.get("permisos") or []),
            # Sólo viene si el usuario puede ver el catálogo de permisos en School.
            "catalogo": data.get("catalogo")}
    final_data[_KEY] = caps
    return caps


def query_for(caps: Optional[dict], cap: str) -> Optional[str]:
    """La consulta habilitada para la capacidad, o None si ninguna lo está."""
    if not caps:
        return None
    for q in CAP_QUERY.get(cap, ()):
        if q in caps["consultas"]:
            return q
    return None


def allows(caps: Optional[dict], cap: str) -> Optional[bool]:
    """True/False según los permisos efectivos; None si no se conocen (decide el backend)."""
    if not caps or cap not in CAP_QUERY:
        return None
    return query_for(caps, cap) is not None


def section_for(caps: Optional[dict], words: list) -> Optional[dict]:
    """Una sección navegable (con permiso) cuyo nombre coincide con las palabras dadas."""
    if not caps:
        return None
    from core.semantic.morphology import normalize, strict_closeness
    ws = [w for w in words if len(w) > 3]
    for s in caps.get("secciones") or []:
        nombre = normalize(s.get("nombre") or "").split()
        if nombre and ws and all(any(strict_closeness(w, n) for n in nombre) for w in ws[:2]):
            return s
    return None


def offer(caps: Optional[dict]) -> Optional[tuple]:
    """(frase, respuestas rápidas) con lo que ESTE usuario puede pedir, o None si no se sabe."""
    if not caps:
        return None
    habilitadas = [q for q in _ORDER if q in caps["consultas"]]
    if "periodos" in habilitadas:
        habilitadas = [q for q in habilitadas if q != "periodos_propios"]
    nombres = [_OFFER_NAME[q] for q in habilitadas if q in _OFFER_NAME][:6]
    if not nombres:
        return None
    from tools.nexiservice import natural_list
    frase = natural_list(nombres, conjunction="o")
    quick = [{"title": _QUICK[q][0], "text": _QUICK[q][1]} for q in habilitadas if q in _QUICK][:3]
    return frase, quick


def allowed_tools(caps: Optional[dict]) -> Optional[set]:
    """Herramientas de datos que el modelo puede usar para este usuario; None si no se sabe."""
    if not caps:
        return None
    return {QUERY_TOOL[q] for q in caps["consultas"] if q in QUERY_TOOL}
