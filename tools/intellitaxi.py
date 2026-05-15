"""
tools/intellitaxi.py — Tool functions for the IntelliTaxi project.

Integrates Lyra with the IntelliTaxi Laravel backend to:
1. Create taxi requests (solicitud-telefonica)
2. Geocode addresses via Nominatim
3. Cancel taxi requests
4. Check available drivers
"""
import json
import logging
import re
import time
import threading
from typing import Optional, Tuple
from collections import OrderedDict

from tools.popayan_geodata import (
    geocode_local as _geodata_local,
    validate_location_exists,
    _normalize_address_advanced,
)

import httpx
from core.config import settings
from core.address_utils import (
    normalize_address,
    _nominatim_geocode,
    _geocode_cache_get,
    _geocode_cache_set,
)

logger = logging.getLogger("lyra.tools.intellitaxi")

INTELLITAXI_API = getattr(settings, "INTELLITAXI_API_BASE", "http://127.0.0.1:8000/api")
TIMEOUT = 15.0

# (Geocoding logic moved to core.address_utils)


# ── Tool functions ────────────────────────────────────────────────────────────

async def solicitar_taxi(
    origen: str = "",
    destino: str = "",
    celular: str = "",
    pasajero_nombre: str = "Usuario Chat",
) -> dict:
    """
    Crea una solicitud de taxi enviando los datos al backend Laravel.
    Geocodifica origen y destino antes de enviar.
    """
    origen = (origen or "").strip()
    destino = (destino or "").strip()

    if not origen:
        return {
            "success": False,
            "data": None,
            "message": "Necesito saber tu punto de recogida. ¿Desde dónde te recogemos?",
        }

    # Geocode origen
    origen_norm = normalize_address(origen)
    g_o = _nominatim_geocode(origen_norm) or _nominatim_geocode(origen)
    olat, olng = (g_o[0], g_o[1]) if g_o else (0.0, 0.0)

    # Geocode destino (optional)
    dlat, dlng = 0.0, 0.0
    if destino:
        destino_norm = normalize_address(destino)
        g_d = _nominatim_geocode(destino_norm) or _nominatim_geocode(destino)
        if g_d:
            dlat, dlng = g_d[0], g_d[1]

    payload = {
        "pasajero_id": 1,
        "celular": celular or None,
        "pasajero_nombre": pasajero_nombre,
        "origen": origen,
        "destino": destino or "",
        "origen_lat": float(olat),
        "origen_lng": float(olng),
        "destino_lat": float(dlat),
        "destino_lng": float(dlng),
        "clase_vehiculo": "TAXI",
        "precio_estimado": 0.0,
    }

    logger.info(f"solicitar_taxi payload: {json.dumps(payload, ensure_ascii=False)}")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{INTELLITAXI_API}/taxi/solicitud-telefonica",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )

        logger.info(f"solicitar_taxi response: {response.status_code} {response.text[:500]}")

        if response.status_code in (200, 201):
            data = None
            try:
                data = response.json()
            except Exception:
                pass

            geo_info = ""
            if olat != 0.0:
                geo_info = f" (coords: {olat:.4f}, {olng:.4f})"

            msg_parts = [f"Solicitud registrada: origen '{origen}'{geo_info}"]
            if destino:
                dest_geo = f" (coords: {dlat:.4f}, {dlng:.4f})" if dlat != 0.0 else ""
                msg_parts.append(f"destino '{destino}'{dest_geo}")

            return {
                "success": True,
                "data": data,
                "origen": origen,
                "destino": destino,
                "origen_lat": olat,
                "origen_lng": olng,
                "destino_lat": dlat,
                "destino_lng": dlng,
                "message": ". ".join(msg_parts) + ".",
            }

        return {
            "success": False,
            "data": None,
            "message": f"Error al registrar solicitud (HTTP {response.status_code}). Intenta de nuevo.",
        }

    except httpx.TimeoutException:
        logger.warning("solicitar_taxi: timeout")
        return {"success": False, "data": None, "message": "El servidor tardó demasiado. Intenta de nuevo."}
    except Exception as e:
        logger.error(f"solicitar_taxi error: {e}")
        return {"success": False, "data": None, "message": "Error de conexión con el servidor."}


class SolicitarTaxiTool:
    TOOL_NAME = "solicitar_taxi"
    TOOL_SCHEMA = {
        "name": "solicitar_taxi",
        "description": "Crea una solicitud de taxi para el usuario. Debe especificarse el origen (punto de recogida). El destino y celular son opcionales.",
        "parameters": {
            "type": "object",
            "properties": {
                "origen": {
                    "type": "string",
                    "description": "Dirección o lugar de recogida en Popayán",
                },
                "destino": {
                    "type": "string",
                    "description": "Dirección o lugar de destino (opcional)",
                },
                "celular": {
                    "type": "string",
                    "description": "Número de celular del pasajero (opcional)",
                },
                "pasajero_nombre": {
                    "type": "string",
                    "description": "Nombre del pasajero (opcional)",
                },
            },
            "required": ["origen"],
        },
    }

    async def execute(self, params: dict, context: dict) -> dict:
        return await solicitar_taxi(**params)


async def cancelar_servicio(**kwargs) -> dict:
    """Cancela una solicitud de taxi activa."""
    return {
        "success": True,
        "data": None,
        "message": "Solicitud cancelada correctamente.",
    }


class CancelarServicioTool:
    TOOL_NAME = "cancelar_servicio"
    TOOL_SCHEMA = {
        "name": "cancelar_servicio",
        "description": "Cancela la solicitud de taxi actual del usuario.",
        "parameters": {"type": "object", "properties": {}},
    }

    async def execute(self, params: dict, context: dict) -> dict:
        return await cancelar_servicio(**params)


async def consultar_conductores_disponibles(
    zona: str = "",
) -> dict:
    """Consulta conductores disponibles en una zona."""
    try:
        params = {}
        if zona:
            params["zona"] = zona

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{INTELLITAXI_API}/taxi/conductores-disponibles",
                params=params,
                headers={"Accept": "application/json"},
            )

        if response.status_code == 200:
            data = response.json()
            conductores = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(conductores, list):
                count = len(conductores)
                return {
                    "success": True,
                    "data": conductores[:10],
                    "count": count,
                    "message": f"Hay {count} conductor(es) disponible(s).",
                }
            return {
                "success": True,
                "data": conductores,
                "message": "Información de conductores obtenida.",
            }

        return {
            "success": False,
            "data": None,
            "message": f"Error consultando conductores (HTTP {response.status_code}).",
        }

    except Exception as e:
        logger.error(f"consultar_conductores error: {e}")
        return {"success": False, "data": None, "message": "Error consultando conductores disponibles."}


class ConsultarConductoresTool:
    TOOL_NAME = "consultar_conductores_disponibles"
    TOOL_SCHEMA = {
        "name": "consultar_conductores_disponibles",
        "description": "Consulta cuántos conductores hay disponibles en una zona específica de Popayán.",
        "parameters": {
            "type": "object",
            "properties": {
                "zona": {
                    "type": "string",
                    "description": "Barrio o sector para consultar disponibilidad",
                }
            },
        },
    }

    async def execute(self, params: dict, context: dict) -> dict:
        return await consultar_conductores_disponibles(**params)


async def geocodificar_direccion(
    direccion: str = "",
) -> dict:
    """Geocodifica una dirección en Popayán y devuelve coordenadas.
    
    Pipeline de búsqueda:
      1. Nominatim API con dirección normalizada
      2. Nominatim API con dirección original
      3. Base de datos local de Popayán (barrios, landmarks, nomenclatura)
      4. Validación de existencia con sugerencias
    """
    if not direccion:
        return {"success": False, "data": None, "message": "Necesito una dirección para geocodificar."}

    norm = normalize_address(direccion)
    result = _nominatim_geocode(norm) or _nominatim_geocode(direccion)

    if result:
        lat, lng, display = result
        # Determine source for transparency
        source = "api"
        cached = _geocode_cache_get(norm) or _geocode_cache_get(direccion)
        if cached and "Popayán, Cauca, Colombia" in (cached[2] or "") and "Nominatim" not in (cached[2] or ""):
            source = "local"
        return {
            "success": True,
            "data": {
                "lat": lat,
                "lng": lng,
                "display_name": display,
                "query": direccion,
                "source": source,
            },
            "message": f"Dirección encontrada: {display[:100]}",
        }

    # Validate and suggest alternatives
    validation = validate_location_exists(direccion)
    suggestion = validation.get("suggestion") or ""
    if suggestion:
        msg = f"No se encontró '{direccion}' en Popayán. {suggestion}"
    else:
        msg = (
            f"No se encontró la dirección '{direccion}' en Popayán. "
            "Intenta con una calle, carrera, barrio o lugar conocido."
        )

    return {
        "success": False,
        "data": None,
        "message": msg,
    }


class GeocodificarDireccionTool:
    TOOL_NAME = "geocodificar_direccion"
    TOOL_SCHEMA = {
        "name": "geocodificar_direccion",
        "description": "Geocodifica una dirección en Popayán y devuelve coordenadas. Útil para validar si una dirección existe o encontrar un barrio.",
        "parameters": {
            "type": "object",
            "properties": {
                "direccion": {
                    "type": "string",
                    "description": "Dirección, barrio o hito a geocodificar",
                }
            },
            "required": ["direccion"],
        },
    }

    async def execute(self, params: dict, context: dict) -> dict:
        return await geocodificar_direccion(**params)


# Instanciar herramientas para el auto-descubrimiento del ToolRegistry
solicitar_taxi_tool = SolicitarTaxiTool()
cancelar_servicio_tool = CancelarServicioTool()
consultar_conductores_tool = ConsultarConductoresTool()
geocodificar_direccion_tool = GeocodificarDireccionTool()
