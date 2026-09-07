"""
core/barrio_popayan.py — Barrio EXACTO de unas coordenadas en Popayán.

Por qué existe este módulo
--------------------------
Ni Google ni OpenStreetMap sirven para el barrio en Popayán (medido el
2026-09-07): Google devuelve la comuna como `sublocality` ("Comuna 1") o una
urbanización vecina, y OSM devuelve el barrio colindante — en Valle del Ortigal
responde "Villa Colombia". Tomar "el barrio más cercano" de un catálogo de
centroides es peor todavía: es la aproximación que ponía puntos de referencia
en vez de la dirección.

La fuente aquí es la capa `Estratos` del servidor ArcGIS de la Alcaldía de
Popayán: el polígono de cada predio con su barrio oficial. `scripts/
build_barrios_popayan.py` la convierte en una grilla de celdas de ~11 m
(`tools/barrios_popayan.json.gz`) y este módulo la consulta sin red ni
dependencias geoespaciales.

Si el punto no cae dentro de ningún barrio de la capa, la respuesta es None.
Nunca se devuelve el barrio más cercano.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("lyra.barrio")

RUTA_DATOS = Path(__file__).resolve().parent.parent / "tools" / "barrios_popayan.json.gz"

_LOCK = threading.Lock()
_CELDAS: Optional[Dict[int, int]] = None
_BARRIOS: List[str] = []
_COMUNAS: List[str] = []
_CELDA_GRADOS: float = 0.00013

# Empaquetado de la celda (ix, iy) en una sola llave entera.
_DESPLAZAMIENTO = 1 << 20


def _clave(ix: int, iy: int) -> int:
    return ix * _DESPLAZAMIENTO + iy


def _cargar() -> None:
    """Carga la grilla una sola vez. Sin datos, el módulo queda inerte."""
    global _CELDAS, _BARRIOS, _COMUNAS, _CELDA_GRADOS

    with _LOCK:
        if _CELDAS is not None:
            return

        if not RUTA_DATOS.exists():
            logger.warning(f"Mapa de barrios ausente: {RUTA_DATOS}")
            _CELDAS = {}
            return

        try:
            with gzip.open(RUTA_DATOS, "rt", encoding="utf-8") as fh:
                datos = json.load(fh)

            _BARRIOS = datos.get("barrios", [])
            _COMUNAS = datos.get("comunas", [])
            _CELDA_GRADOS = float(datos.get("celda_grados", 0.00013))

            celdas: Dict[int, int] = {}
            for indice, planas in (datos.get("celdas") or {}).items():
                i = int(indice)
                for pos in range(0, len(planas), 2):
                    celdas[_clave(planas[pos], planas[pos + 1])] = i

            _CELDAS = celdas
            logger.info(
                f"Mapa de barrios cargado: {len(_BARRIOS)} barrios, {len(celdas)} celdas"
            )
        except Exception as exc:
            logger.error(f"No se pudo cargar el mapa de barrios: {exc}")
            _CELDAS = {}


def barrio_de_coordenadas(lat: float, lng: float) -> Optional[str]:
    """Barrio que CONTIENE al punto, o None si la capa oficial no lo cubre.

    'Valle del Ortigal' para 2.4638, -76.6412. Nunca devuelve un barrio vecino
    ni el más cercano: sin cobertura, no hay barrio.
    """
    if lat is None or lng is None:
        return None

    _cargar()
    if not _CELDAS:
        return None

    ix = int(float(lng) / _CELDA_GRADOS)
    iy = int(float(lat) / _CELDA_GRADOS)
    indice = _CELDAS.get(_clave(ix, iy))
    if indice is None:
        return None
    return _BARRIOS[indice]


def comuna_de_barrio(barrio: str) -> Optional[str]:
    """Comuna del barrio según la misma capa oficial."""
    _cargar()
    if not barrio or barrio not in _BARRIOS:
        return None
    comuna = _COMUNAS[_BARRIOS.index(barrio)] if _COMUNAS else ""
    return comuna or None


def barrios_disponibles() -> Tuple[str, ...]:
    """Nombres de barrio que cubre la capa (para diagnóstico y tests)."""
    _cargar()
    return tuple(_BARRIOS)
