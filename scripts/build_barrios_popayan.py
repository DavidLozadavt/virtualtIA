#!/usr/bin/env python3
"""
scripts/build_barrios_popayan.py — Construye el mapa de barrios de Popayán.

Fuente: capa `Estratos` del servidor ArcGIS de la Alcaldía de Popayán
(https://services6.arcgis.com/Gcf3PUdNKJVyKM8K/arcgis/rest/services/Estratos),
que trae el polígono de cada predio con su barrio oficial (`BARRIO_O_V`), la
comuna y la dirección. Es la única fuente exacta que encontramos: Google
devuelve la comuna como "barrio" y OSM responde el barrio colindante.

Salida: `tools/barrios_popayan.json.gz` — una grilla de celdas de ~11 m donde
cada celda cae dentro de un solo barrio. El runtime (`core/barrio_popayan.py`)
resuelve un punto con un lookup de diccionario, sin dependencias geoespaciales
ni llamadas de red.

Uso:
    python scripts/build_barrios_popayan.py [--salida RUTA]

Solo hay que volver a correrlo cuando la Alcaldía actualice la capa.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import httpx

SERVIDOR = "https://services6.arcgis.com/Gcf3PUdNKJVyKM8K/arcgis/rest/services"
LAYER_URL = f"{SERVIDOR}/Estratos/FeatureServer/0"
MANZANAS_URL = f"{SERVIDOR}/Manzanas/FeatureServer/0"

# Una manzana solo se usa para rellenar si TODOS sus predios etiquetados dicen
# el mismo barrio. Con 0,9 el relleno metía errores reales: la manzana de
# "Ciudad Dos Mil" se tragaba predios de Villa Colombia y Lomas de Comfacauca.
PUREZA_MINIMA = 1.0

# Fracción de votos que un barrio necesita para quedarse con una celda que
# tocan varios predios. Por debajo de esto la celda queda sin barrio.
DOMINANCIA_MINIMA = 0.8
CAMPO_BARRIO = "BARRIO_O_V"
CAMPO_COMUNA = "COMUNA"
PAGINA = 1000

# Tamaño de celda en grados. 0.0001° ≈ 11 m en latitud y en longitud a la
# latitud de Popayán (2.44°), así que la celda es casi cuadrada. Con celdas más
# grandes, un predio pequeño no alcanza a contener ningún centro de celda y el
# barrio se queda sin cubrir.
CELDA = 0.0001

# Anillos de celdas que se añaden alrededor de cada barrio para cubrir la vía y
# los lotes sin etiqueta pegados al barrio (4 × 11 m ≈ 44 m). Solo crece hacia
# donde no hay otro barrio compitiendo, así que ensanchar no genera errores.
ANILLOS_FRONTERA = 4

# Palabras que van en minúscula al pasar el nombre de MAYÚSCULAS a Título.
MINUSCULAS = {"de", "del", "la", "las", "los", "el", "y", "e", "en", "a"}


def titulo_barrio(nombre: str) -> str:
    """'VALLE DEL ORTIGAL' → 'Valle del Ortigal'."""
    palabras = nombre.strip().split()
    salida: List[str] = []
    for i, p in enumerate(palabras):
        bajo = p.lower()
        if i > 0 and bajo in MINUSCULAS:
            salida.append(bajo)
        elif len(p) <= 2 and p.isupper() and not p.isalpha():
            salida.append(p)
        else:
            salida.append(bajo.capitalize())
    return " ".join(salida)


def descargar_manzanas() -> List[dict]:
    """Polígonos de manzana (la cuadra completa) con su código catastral."""
    total = httpx.get(
        f"{MANZANAS_URL}/query",
        params={"where": "1=1", "returnCountOnly": "true", "f": "json"},
        timeout=90,
    ).json().get("count", 0)
    print(f"[2/4] manzanas en la capa oficial: {total}")

    manzanas: List[dict] = []
    offset = 0
    while offset < total:
        data = httpx.get(
            f"{MANZANAS_URL}/query",
            params={
                "where": "1=1", "outFields": "codigo", "returnGeometry": "true",
                "outSR": 4326, "geometryPrecision": 6,
                "resultOffset": offset, "resultRecordCount": PAGINA,
                "orderByFields": "FID", "f": "json",
            },
            timeout=180,
        ).json()
        lote = data.get("features", [])
        if not lote:
            break
        manzanas.extend(lote)
        offset += len(lote)
        print(f"      {offset}/{total}", end="\r", flush=True)

    print(f"\n      descargadas: {len(manzanas)}")
    return manzanas


def barrio_por_manzana(predios: List[dict]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Código de manzana → barrio, por mayoría de los predios etiquetados.

    Una manzana pertenece a un solo barrio; etiquetar la cuadra completa cubre
    también los predios sin barrio en la capa (el 56% del total).
    """
    votos: Dict[str, Counter] = defaultdict(Counter)
    comunas: Dict[str, Counter] = defaultdict(Counter)

    for feat in predios:
        a = feat.get("attributes", {})
        mz = (a.get("manzana_co") or "").strip()
        bruto = (a.get(CAMPO_BARRIO) or "").strip()
        if not mz or not bruto:
            continue
        barrio = titulo_barrio(bruto)
        votos[mz][barrio] += 1
        comuna = (a.get(CAMPO_COMUNA) or "").strip()
        if comuna:
            comunas[barrio][comuna] += 1

    mapa: Dict[str, str] = {}
    descartadas = 0
    for mz, cuenta in votos.items():
        barrio, top = cuenta.most_common(1)[0]
        if top / sum(cuenta.values()) >= PUREZA_MINIMA:
            mapa[mz] = barrio
        else:
            descartadas += 1

    comuna_de = {b: c.most_common(1)[0][0] for b, c in comunas.items()}
    print(f"      manzanas resueltas: {len(mapa)} | descartadas por mezcla: {descartadas}")
    return mapa, comuna_de


def descargar_predios(url: str) -> List[dict]:
    """Predios etiquetados, con geometría en WGS84."""
    where = f"{CAMPO_BARRIO} IS NOT NULL AND {CAMPO_BARRIO} <> ' '"
    total = httpx.get(
        f"{url}/query",
        params={"where": where, "returnCountOnly": "true", "f": "json"},
        timeout=90,
    ).json().get("count", 0)
    print(f"[1/4] predios con barrio en la capa oficial: {total}")

    predios: List[dict] = []
    offset = 0
    while offset < total:
        params = {
            "where": where,
            "outFields": f"manzana_co,{CAMPO_BARRIO},{CAMPO_COMUNA}",
            "returnGeometry": "true",
            "outSR": 4326,
            "geometryPrecision": 6,
            "resultOffset": offset,
            "resultRecordCount": PAGINA,
            "orderByFields": "OBJECTID_1",
            "f": "json",
        }
        for intento in range(3):
            try:
                data = httpx.get(f"{url}/query", params=params, timeout=180).json()
                break
            except Exception as exc:  # red intermitente: reintento con espera
                if intento == 2:
                    raise
                print(f"      reintento {intento + 1}: {type(exc).__name__}")
                time.sleep(3)

        lote = data.get("features", [])
        if not lote:
            break
        predios.extend(lote)
        offset += len(lote)
        print(f"      {offset}/{total}", end="\r", flush=True)

    print(f"\n      descargados: {len(predios)}")
    return predios


def celdas_de_anillo(anillo: List[List[float]]) -> Iterable[Tuple[int, int]]:
    """Celdas cuyo centro cae dentro del anillo (ray casting)."""
    xs = [p[0] for p in anillo]
    ys = [p[1] for p in anillo]
    if not xs:
        return

    ix0, ix1 = int(min(xs) / CELDA), int(max(xs) / CELDA)
    iy0, iy1 = int(min(ys) / CELDA), int(max(ys) / CELDA)

    # Un predio enorme (error de digitalización) no debe inundar la grilla.
    if (ix1 - ix0) > 600 or (iy1 - iy0) > 600:
        return

    # El centroide garantiza al menos una celda: un predio de 6 m de frente
    # puede no contener ningún centro de celda.
    yield (int((sum(xs) / len(xs)) / CELDA), int((sum(ys) / len(ys)) / CELDA))

    for ix in range(ix0, ix1 + 1):
        cx = (ix + 0.5) * CELDA
        for iy in range(iy0, iy1 + 1):
            cy = (iy + 0.5) * CELDA
            if punto_en_anillo(cx, cy, anillo):
                yield (ix, iy)


def punto_en_anillo(x: float, y: float, anillo: List[List[float]]) -> bool:
    """Ray casting clásico; el anillo viene cerrado desde ArcGIS."""
    dentro = False
    n = len(anillo)
    j = n - 1
    for i in range(n):
        xi, yi = anillo[i][0], anillo[i][1]
        xj, yj = anillo[j][0], anillo[j][1]
        if (yi > y) != (yj > y):
            corte = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < corte:
                dentro = not dentro
        j = i
    return dentro


def construir_grilla(
    manzanas: List[dict], mapa: Dict[str, str], predios: List[dict]
) -> Tuple[Dict[Tuple[int, int], str], set]:
    """Celda → barrio: primero la manzana completa, luego predio por predio.

    La manzana cubre la cuadra entera (incluidos los predios sin etiqueta); los
    predios rellenan las urbanizaciones nuevas cuyo código de manzana todavía no
    está en la capa `Manzanas`.
    """
    # 1. Predio por predio: es la etiqueta más fina y manda sobre la manzana.
    votos: Dict[Tuple[int, int], Counter] = defaultdict(Counter)
    for n, feat in enumerate(predios):
        bruto = (feat.get("attributes", {}).get(CAMPO_BARRIO) or "").strip()
        if not bruto:
            continue
        barrio = titulo_barrio(bruto)
        for anillo in feat.get("geometry", {}).get("rings", []):
            for celda in celdas_de_anillo(anillo):
                votos[celda][barrio] += 1

        if n % 5000 == 0:
            print(f"      predios {n}/{len(predios)}", end="\r", flush=True)

    # Una celda que se disputan dos barrios (linderos que se tocan dentro de los
    # 11 m de la celda) se descarta: antes que un barrio equivocado, ninguno.
    grilla: Dict[Tuple[int, int], str] = {}
    vetadas: set = set()
    for celda, cuenta in votos.items():
        barrio, top = cuenta.most_common(1)[0]
        if top / sum(cuenta.values()) >= DOMINANCIA_MINIMA:
            grilla[celda] = barrio
        else:
            vetadas.add(celda)
    disputadas = len(vetadas)
    por_predio = len(grilla)
    print(f"      celdas descartadas por lindero disputado: {disputadas}")

    # 2. La manzana rellena lo que el predio no cubre (predios sin etiqueta,
    #    patios, zonas comunes), solo si la cuadra entera es de un mismo barrio.
    extra: Dict[Tuple[int, int], Counter] = defaultdict(Counter)
    sin_barrio = 0
    for n, feat in enumerate(manzanas):
        codigo = (feat.get("attributes", {}).get("codigo") or "").strip()
        barrio = mapa.get(codigo)
        if not barrio:
            sin_barrio += 1
            continue

        for anillo in feat.get("geometry", {}).get("rings", []):
            for celda in celdas_de_anillo(anillo):
                if celda not in grilla:
                    extra[celda][barrio] += 1

        if n % 500 == 0:
            print(f"      manzanas {n}/{len(manzanas)}", end="\r", flush=True)

    for celda, cuenta in extra.items():
        if celda in vetadas:    # lindero disputado: la celda se queda vacía
            continue
        grilla[celda] = cuenta.most_common(1)[0][0]

    print(f"\n[3/4] celdas: {len(grilla)} ({por_predio} por predio, "
          f"{len(extra)} por manzana) | barrios: {len(set(grilla.values()))} "
          f"| manzanas sin barrio uniforme: {sin_barrio}")
    return grilla, vetadas


def expandir_frontera(
    grilla: Dict[Tuple[int, int], str], vetadas: set
) -> Dict[Tuple[int, int], str]:
    """Ensancha cada barrio hasta la calle donde cae el pin (~17 m).

    Las manzanas no incluyen la vía, y un pin de WhatsApp casi siempre cae
    sobre la calzada. Solo se añade una celda si TODAS las celdas vecinas
    ocupadas pertenecen al mismo barrio: en el límite entre dos barrios no se
    inventa pertenencia. Las celdas vetadas (linderos disputados) nunca se
    rellenan.
    """
    total = 0
    for _ in range(ANILLOS_FRONTERA):
        candidatos: Dict[Tuple[int, int], set] = defaultdict(set)
        for (ix, iy), barrio in grilla.items():
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    vecina = (ix + dx, iy + dy)
                    if vecina not in grilla:
                        candidatos[vecina].add(barrio)

        nuevas = {
            c: next(iter(b))
            for c, b in candidatos.items()
            if len(b) == 1 and c not in vetadas
        }
        grilla.update(nuevas)
        total += len(nuevas)

    print(f"      celdas de frontera añadidas: {total}")
    return grilla


def empacar(grilla: Dict[Tuple[int, int], str], comuna_de: Dict[str, str]) -> dict:
    """Formato compacto: lista de barrios + celdas agrupadas por barrio."""
    barrios = sorted(set(grilla.values()))
    indice = {b: i for i, b in enumerate(barrios)}

    por_barrio: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    for celda, barrio in grilla.items():
        por_barrio[indice[barrio]].append(celda)

    celdas_planas: Dict[str, List[int]] = {}
    for i, celdas in por_barrio.items():
        planas: List[int] = []
        for ix, iy in sorted(celdas):
            planas.extend((ix, iy))
        celdas_planas[str(i)] = planas

    return {
        "fuente": LAYER_URL,
        "generado": time.strftime("%Y-%m-%d"),
        "celda_grados": CELDA,
        "barrios": barrios,
        "comunas": [comuna_de.get(b, "") for b in barrios],
        "celdas": celdas_planas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--salida",
        default=str(Path(__file__).resolve().parents[1] / "tools" / "barrios_popayan.json.gz"),
    )
    args = parser.parse_args()

    predios = descargar_predios(LAYER_URL)
    if not predios:
        print("Sin predios: la capa no respondió.", file=sys.stderr)
        return 1

    mapa, comuna_de = barrio_por_manzana(predios)
    manzanas = descargar_manzanas()
    if not manzanas:
        print("Sin manzanas: la capa no respondió.", file=sys.stderr)
        return 1

    grilla, vetadas = construir_grilla(manzanas, mapa, predios)
    grilla = expandir_frontera(grilla, vetadas)
    paquete = empacar(grilla, comuna_de)

    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(salida, "wt", encoding="utf-8") as fh:
        json.dump(paquete, fh, ensure_ascii=False, separators=(",", ":"))

    print(f"[4/4] {salida} — {salida.stat().st_size / 1024:.0f} KB, "
          f"{len(paquete['barrios'])} barrios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
