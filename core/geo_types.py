"""
core/geo_types.py — Tipos del pipeline de geocodificación.

Separación de responsabilidades:
  location_cache     → verdad técnica (query → coordenadas)
  geo_human_aliases  → verdad humana (query_base → cómo la describe la gente) [Fase 2]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Bounding boxes de Popayán ────────────────────────────────────────────────

POPAYAN_URBAN_BBOX = {
    "min_lat": 2.38, "max_lat": 2.52,
    "min_lng": -76.72, "max_lng": -76.54,
}

POPAYAN_BBOX_WIDE = {
    "min_lat": 2.32, "max_lat": 2.58,
    "min_lng": -76.82, "max_lng": -76.42,
}

POPAYAN_CENTER = (2.4419, -76.6063)  # Parque Caldas


def in_urban_bbox(lat: float, lng: float) -> bool:
    bb = POPAYAN_URBAN_BBOX
    return bb["min_lat"] <= lat <= bb["max_lat"] and bb["min_lng"] <= lng <= bb["max_lng"]


def in_wide_bbox(lat: float, lng: float) -> bool:
    bb = POPAYAN_BBOX_WIDE
    return bb["min_lat"] <= lat <= bb["max_lat"] and bb["min_lng"] <= lng <= bb["max_lng"]


# ── Enums ────────────────────────────────────────────────────────────────────

class LocationType(str, Enum):
    ROOFTOP            = "ROOFTOP"
    RANGE_INTERPOLATED = "RANGE_INTERPOLATED"
    GEOMETRIC_CENTER   = "GEOMETRIC_CENTER"
    APPROXIMATE        = "APPROXIMATE"
    NOMINATIM_HIGH     = "NOMINATIM_HIGH"   # importance >= 0.75
    NOMINATIM_LOW      = "NOMINATIM_LOW"    # importance < 0.75
    MANUAL             = "MANUAL"
    CACHE              = "CACHE"


class ResolutionStatus(str, Enum):
    RESOLVED             = "resolved"
    NEEDS_DISAMBIGUATION = "needs_disambiguation"
    CONTEXT_GATHERING    = "context_gathering"
    FAILED               = "failed"


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class GeoCandidate:
    lat: float
    lng: float
    display_name: str
    source: str                          # "google" | "nominatim" | "cache"
    location_type: LocationType
    confidence: float                    # 0.0–1.0
    neighborhood: Optional[str] = None  # de address_components o addressdetails
    raw: dict = field(default_factory=dict, repr=False)

    def in_urban_bbox(self) -> bool:
        return in_urban_bbox(self.lat, self.lng)

    def in_wide_bbox(self) -> bool:
        return in_wide_bbox(self.lat, self.lng)

    def auto_acceptable(self) -> bool:
        """Solo ROOFTOP y RANGE_INTERPOLATED dentro del área urbana se auto-aceptan."""
        return (
            self.location_type in (LocationType.ROOFTOP, LocationType.RANGE_INTERPOLATED)
            and self.in_urban_bbox()
        )


@dataclass
class GeoResolution:
    status: ResolutionStatus
    query: str
    attempt: int = 1
    candidates: list[GeoCandidate] = field(default_factory=list)
    selected: Optional[GeoCandidate] = None
    disambiguation_question: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.status == ResolutionStatus.RESOLVED

    @property
    def needs_input(self) -> bool:
        return self.status in (
            ResolutionStatus.NEEDS_DISAMBIGUATION,
            ResolutionStatus.CONTEXT_GATHERING,
        )


# ── Estado de desambiguación persistido entre turnos ────────────────────────

@dataclass
class GeoSessionState:
    """
    Persiste entre turnos de conversación (WhatsApp / voz).
    Se guarda en la sesión del usuario mientras hay una resolución en curso.
    """
    pending: Optional[GeoResolution] = None
    original_query: str = ""
    attempt: int = 1
    field: str = "pickup"           # "pickup" | "destination"

    def reset(self) -> None:
        self.pending = None
        self.original_query = ""
        self.attempt = 1


# ── Constantes de pipeline ───────────────────────────────────────────────────

MAX_PIPELINE_ATTEMPTS = 3
MAX_CANDIDATES_SHOWN  = 4     # máx opciones presentadas al usuario
