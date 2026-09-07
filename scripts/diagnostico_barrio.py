#!/usr/bin/env python3
"""
scripts/diagnostico_barrio.py — Por qué una ubicación sale con barrio o sin él.

Muestra, para unas coordenadas o para una dirección escrita:
  1. Qué texto arma Lyra para el conductor.
  2. Qué barrio devuelve el mapa local (`core.barrio_popayan`).
  3. Qué dice la capa de la Alcaldía EN VIVO para ese punto: predio, dirección
     oficial, barrio y manzana.
  4. Los predios etiquetados más cercanos, con su distancia.

Uso:
    python scripts/diagnostico_barrio.py 2.4638 -76.6412
    python scripts/diagnostico_barrio.py "cra 52 # 3-3"
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from core.barrio_popayan import barrio_de_coordenadas
from core.address_utils import geocode_direccion_escrita

SERVIDOR = "https://services6.arcgis.com/Gcf3PUdNKJVyKM8K/arcgis/rest/services"
ESTRATOS = f"{SERVIDOR}/Estratos/FeatureServer/0/query"
MANZANAS = f"{SERVIDOR}/Manzanas/FeatureServer/0/query"


def _punto(lat: float, lng: float) -> str:
    return json.dumps({"x": lng, "y": lat, "spatialReference": {"wkid": 4326}})


def capa_en_el_punto(lat: float, lng: float) -> None:
    """Qué tiene la Alcaldía exactamente bajo esas coordenadas."""
    comun = {
        "geometry": _punto(lat, lng), "geometryType": "esriGeometryPoint",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "false", "f": "json",
    }

    predios = httpx.get(ESTRATOS, params={
        **comun, "outFields": "BARRIO_O_V,DIRECCIÓN,manzana_co,ESTRATO"},
        timeout=60).json().get("features", [])
    if predios:
        for p in predios[:3]:
            a = p["attributes"]
            rotulo = (a.get("BARRIO_O_V") or "").strip() or "(sin barrio en la capa)"
            print(f"   predio  : {a.get('DIRECCIÓN')} | barrio: {rotulo}")
    else:
        print("   predio  : el punto no cae dentro de ningún predio de la capa")

    manzanas = httpx.get(MANZANAS, params={**comun, "outFields": "codigo"},
                         timeout=60).json().get("features", [])
    print(f"   manzana : {[m['attributes']['codigo'] for m in manzanas] or 'ninguna'}")


def predios_cercanos(lat: float, lng: float, metros: int = 150) -> None:
    """Predios etiquetados alrededor: sirve para ver si el punto quedó en un
    hueco de la capa y qué barrios lo rodean."""
    grados = metros / 111_000
    envelope = json.dumps({
        "xmin": lng - grados, "ymin": lat - grados,
        "xmax": lng + grados, "ymax": lat + grados,
        "spatialReference": {"wkid": 4326},
    })
    d = httpx.get(ESTRATOS, params={
        "geometry": envelope, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "where": "BARRIO_O_V IS NOT NULL AND BARRIO_O_V <> ' '",
        "outFields": "BARRIO_O_V,DIRECCIÓN", "returnGeometry": "true",
        "outSR": 4326, "geometryPrecision": 6, "resultRecordCount": 500, "f": "json",
    }, timeout=90).json()

    cercanos = []
    for f in d.get("features", []):
        anillos = f.get("geometry", {}).get("rings") or []
        if not anillos:
            continue
        pts = anillos[0]
        clat = sum(p[1] for p in pts) / len(pts)
        clng = sum(p[0] for p in pts) / len(pts)
        dist = math.hypot((clat - lat) * 111_000, (clng - lng) * 111_000 * math.cos(math.radians(lat)))
        cercanos.append((dist, f["attributes"]["BARRIO_O_V"], f["attributes"]["DIRECCIÓN"]))

    cercanos.sort()
    print(f"   predios etiquetados en {metros} m: {len(cercanos)}")
    for dist, barrio, direccion in cercanos[:6]:
        print(f"      {dist:6.0f} m  {barrio:26} {direccion}")


async def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    if len(args) >= 2:
        try:
            lat, lng = float(args[0]), float(args[1])
        except ValueError:
            print("Coordenadas inválidas.")
            return 1
        origen = "coordenadas dadas"
    else:
        direccion = args[0]
        punto = await geocode_direccion_escrita(direccion)
        if not punto:
            print(f"'{direccion}': Google no la ubica con precisión de predio; "
                  "el servicio se crea sin coordenadas y sin barrio.")
            return 0
        lat, lng = punto
        origen = f"geocodificación de {direccion!r}"

    print(f"punto: {lat}, {lng}  ({origen})")
    print(f"   mapa local  : {barrio_de_coordenadas(lat, lng) or 'sin barrio'}")
    print("   capa oficial (en vivo):")
    capa_en_el_punto(lat, lng)
    predios_cercanos(lat, lng)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
