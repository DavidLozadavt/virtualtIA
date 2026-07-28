"""
core/poi_catalog.py — Catálogo configurable de Puntos de Interés (POI).

Responsabilidad ÚNICA: dado el texto que el usuario dijo o escribió, decidir si
nombra un POI conocido y, en ese caso, devolver el nombre de VISUALIZACIÓN.

Lo que este módulo NO hace (por diseño):
  - No geocodifica.
  - No normaliza direcciones.
  - No participa en la resolución ni en la desambiguación.
  - No reemplaza jamás la dirección oficial.

La dirección resuelta por core.geocoder_service sigue siendo la fuente de verdad
para despacho, búsqueda de conductor, rutas, navegación y coordenadas. El
display name solo cambia CÓMO se le presenta el lugar al usuario.

El catálogo vive en un JSON externo (config/poi_catalog.json por defecto,
override con POI_CATALOG_PATH). Agregar un POI no requiere tocar código.

Formato del JSON:
    {
      "pois": [
        {
          "name": "Centro Comercial Campanario",
          "display_name": "Centro Comercial Campanario",
          "aliases": ["campanario", "cc campanario"],
          "exact_aliases": ["centro comercial"]
        }
      ]
    }

  aliases       → coinciden como texto completo O como subsecuencia de palabras
                  dentro de una frase ("recógeme en el campanario").
  exact_aliases → coinciden SOLO si son el texto completo. Para alias genéricos
                  que serían ambiguos dentro de una frase.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lyra.poi_catalog")

# Longitud mínima de un alias para poder coincidir DENTRO de una frase.
# Evita que fragmentos cortos ("cc", "u") secuestren texto arbitrario.
_MIN_CONTAINMENT_LEN = 4

# Señales de nomenclatura vial. Si el texto las tiene, es una dirección normal
# (Calle 5 #12-20, Carrera 9 #73N-200) y NUNCA recibe display name.
_ADDRESS_SIGNAL = re.compile(
    r"(?:#|\bn[°º]\b|\bnro\b|"
    r"\b(?:calle|carrera|cra|cr|kra|kr|cll|cl|avenida|av|diagonal|dg|dig|"
    r"transversal|tv|trans|circunvalar|autopista|via|manzana|mz)\b\s*\d|"
    r"\d+\s*-\s*\d+)",
    re.IGNORECASE,
)

_PUNCT = re.compile(r"[^0-9a-z]+")
# Los puntos de una sigla se eliminan (no separan): "C.C." → "cc", no "c c".
_DOTS = re.compile(r"(?<=[a-z])\.(?=[a-z]?)")


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def normalize_key(text: str) -> str:
    """Clave de comparación: minúsculas, sin tildes, sin puntuación, espacios simples."""
    if not text:
        return ""
    low = _strip_accents(str(text).lower())
    return _PUNCT.sub(" ", _DOTS.sub("", low)).strip()


@dataclass(frozen=True)
class PoiEntry:
    name: str
    display_name: str
    aliases: tuple[str, ...] = field(default=())        # claves normalizadas
    exact_aliases: tuple[str, ...] = field(default=())  # claves normalizadas


# ── Carga del catálogo (cacheada, con recarga por mtime) ────────────────────

_lock = threading.Lock()
_cache: Optional[list[PoiEntry]] = None
_cache_path: Optional[Path] = None
_cache_mtime: float = -1.0


def _default_path() -> Path:
    from core.config import settings

    configured = (getattr(settings, "POI_CATALOG_PATH", "") or "").strip()
    if configured:
        p = Path(configured)
    else:
        p = Path(__file__).resolve().parent.parent / "config" / "poi_catalog.json"
    return p


def _parse(raw: dict) -> list[PoiEntry]:
    entries: list[PoiEntry] = []
    for item in raw.get("pois") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        display = str(item.get("display_name") or "").strip() or name
        if not display:
            continue

        alias_keys: set[str] = set()
        for a in list(item.get("aliases") or []) + [name, display]:
            k = normalize_key(a)
            if k:
                alias_keys.add(k)

        exact_keys: set[str] = set()
        for a in item.get("exact_aliases") or []:
            k = normalize_key(a)
            if k and k not in alias_keys:
                exact_keys.add(k)

        entries.append(
            PoiEntry(
                name=name or display,
                display_name=display,
                aliases=tuple(sorted(alias_keys, key=len, reverse=True)),
                exact_aliases=tuple(sorted(exact_keys, key=len, reverse=True)),
            )
        )
    return entries


def load_catalog(force: bool = False) -> list[PoiEntry]:
    """Devuelve el catálogo. Recarga solo si el archivo cambió (o force=True)."""
    global _cache, _cache_path, _cache_mtime

    path = _default_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0

    with _lock:
        if (
            not force
            and _cache is not None
            and _cache_path == path
            and _cache_mtime == mtime
        ):
            return _cache

        entries: list[PoiEntry] = []
        if mtime >= 0:
            try:
                entries = _parse(json.loads(path.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning("POI catalog no cargado (%s): %s", path, e)
                entries = []
        else:
            logger.info("POI catalog ausente en %s — display names deshabilitados.", path)

        _cache = entries
        _cache_path = path
        _cache_mtime = mtime
        return entries


def reset_cache() -> None:
    """Invalida la caché en memoria (tests / recarga en caliente)."""
    global _cache, _cache_path, _cache_mtime
    with _lock:
        _cache = None
        _cache_path = None
        _cache_mtime = -1.0


# ── Matching ────────────────────────────────────────────────────────────────

def _contains_tokens(haystack: list[str], needle: list[str]) -> bool:
    """True si `needle` aparece como secuencia consecutiva de palabras completas."""
    n, m = len(haystack), len(needle)
    if m == 0 or m > n:
        return False
    for i in range(n - m + 1):
        if haystack[i:i + m] == needle:
            return True
    return False


def poi_display_name(text: Optional[str]) -> Optional[str]:
    """
    Devuelve el display name del POI que nombra `text`, o None.

    None significa "no es un POI del catálogo" → se debe mostrar el texto o la
    dirección tal cual, exactamente como hoy.

    Precision-first:
      - Texto con nomenclatura vial (Calle 5 #12-20) → None, siempre.
      - Solo coincidencias de palabras completas.
      - Gana el alias más largo (así "centro comercial anarkos" no se confunde
        con un alias genérico "centro comercial").
    """
    key = normalize_key(text)
    if not key:
        return None

    # Una dirección normal jamás recibe display name.
    if _ADDRESS_SIGNAL.search(str(text)):
        return None

    tokens = key.split()

    best_display: Optional[str] = None
    best_len = 0

    for entry in load_catalog():
        for alias in entry.exact_aliases:
            if alias == key and len(alias) > best_len:
                best_display, best_len = entry.display_name, len(alias)

        for alias in entry.aliases:
            if alias == key:
                if len(alias) > best_len:
                    best_display, best_len = entry.display_name, len(alias)
                continue
            if len(alias) < _MIN_CONTAINMENT_LEN:
                continue
            if len(alias) > best_len and _contains_tokens(tokens, alias.split()):
                best_display, best_len = entry.display_name, len(alias)

    return best_display


def display_for(user_text: Optional[str], official_address: Optional[str]) -> str:
    """
    Texto a MOSTRAR al usuario.

    `user_text`        → lo que el usuario dijo/escribió (base del match de POI).
    `official_address` → la dirección oficial resuelta (fuente de verdad interna).

    Si `user_text` nombra un POI del catálogo devuelve su display name; en
    cualquier otro caso devuelve la dirección oficial sin tocar.
    """
    return poi_display_name(user_text) or (official_address or user_text or "")


def is_known_poi(text: Optional[str]) -> bool:
    return poi_display_name(text) is not None
