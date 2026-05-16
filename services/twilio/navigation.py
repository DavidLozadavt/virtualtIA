"""
services/twilio/navigation.py — Parser de navegación y matching geográfico para Popayán.
VERSIÓN MEJORADA con matching contextual, referencias humanas y corrección fonética profunda.

Resuelve:
- "subiendo al morro" → Morro de Tulcán (2.4453, -76.6064)
- "abajo de campanario" → sur de Campanario
- "después del puente" → área del Humilladero
- "por donde era el cine" → zona histórica del centro
- "la 15 con 9" → calle 15 con carrera 9
- Nombres con errores fonéticos de STT
"""

from __future__ import annotations

import re
import math
from typing import Optional, Dict, List, Tuple

from core.stt_enhancer import (
    strip_accents,
    phonetic_key,
    combined_score,
    fuzzy_match_location,
    resolve_human_reference,
    HUMAN_REFERENCES,
)


# ── Constantes geográficas ────────────────────────────────────────────────────

# Centro de Popayán (referencia para distancias)
POPAYAN_CENTER = (2.4418, -76.6066)
POPAYAN_BBOX   = {
    "lat_min": 2.38, "lat_max": 2.52,
    "lon_min": -76.65, "lon_max": -76.55,
}


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km entre dos coordenadas."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _is_in_popayan(lat: float, lon: float) -> bool:
    return (
        POPAYAN_BBOX["lat_min"] <= lat <= POPAYAN_BBOX["lat_max"]
        and POPAYAN_BBOX["lon_min"] <= lon <= POPAYAN_BBOX["lon_max"]
    )


# ── Modificadores relativos ───────────────────────────────────────────────────

# Patrón → (offset_lat, offset_lon, description)
# Offsets en grados (0.001 ≈ 111m)
RELATIVE_OFFSETS: Dict[str, Tuple[float, float, str]] = {
    # Dirección cardinal
    "norte":    (+0.0015, 0.0,     "al norte de"),
    "sur":      (-0.0015, 0.0,     "al sur de"),
    "oriente":  (0.0,     +0.0015, "al oriente de"),
    "occidente": (0.0,    -0.0015, "al occidente de"),
    "este":     (0.0,     +0.0015, "al este de"),
    "oeste":    (0.0,     -0.0015, "al oeste de"),

    # Relativo a lugar
    "arriba de":       (+0.0010, 0.0,    "arriba de"),
    "mas arriba de":   (+0.0015, 0.0,    "más arriba de"),
    "abajo de":        (-0.0010, 0.0,    "abajo de"),
    "mas abajo de":    (-0.0015, 0.0,    "más abajo de"),
    "subiendo al":     (+0.0008, 0.0,    "subiendo hacia"),
    "bajando del":     (-0.0008, 0.0,    "bajando de"),
    "bajando de":      (-0.0008, 0.0,    "bajando de"),
    "subiendo a":      (+0.0008, 0.0,    "subiendo a"),

    # Posición relativa
    "frente a":        (0.0,     0.0,    "frente a"),
    "al frente de":    (0.0,     0.0,    "frente a"),
    "enfrente de":     (0.0,     0.0,    "frente a"),
    "antes de":        (+0.0005, 0.0,    "antes de"),
    "pasando":         (-0.0005, 0.0,    "pasando"),
    "despues de":      (-0.0008, 0.0,    "después de"),
    "después de":      (-0.0008, 0.0,    "después de"),
    "detras de":       (-0.0010, 0.0,    "detrás de"),
    "detrás de":       (-0.0010, 0.0,    "detrás de"),
    "a espaldas de":   (-0.0010, 0.0,    "a espaldas de"),

    # Adyacencia
    "al lado de":      (0.0,     0.0,    "al lado de"),
    "enseguida de":    (0.0,     +0.0005,"enseguida de"),
    "junto a":         (0.0,     0.0,    "junto a"),
    "pegado a":        (0.0,     0.0,    "pegado a"),
    "cerca de":        (0.0,     0.0,    "cerca de"),
    "por":             (0.0,     0.0,    "por"),

    # Distancia
    "a una cuadra de": (0.0,     +0.0010,"a una cuadra de"),
    "a media cuadra":  (0.0,     +0.0005,"a media cuadra de"),
    "dos cuadras":     (0.0,     +0.0020,"a dos cuadras de"),
}

# Compilar regex de modificadores (más largos primero para prioridad)
_MODIFIER_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(m) for m in sorted(RELATIVE_OFFSETS.keys(), key=len, reverse=True)
    ) + r")\s+(?:el\s+|la\s+|los\s+|las\s+|del\s+|de\s+la\s+|al\s+|un\s+|una\s+)?",
    re.IGNORECASE,
)


class NavigationParser:
    """
    Parser de descripciones de navegación relativa comunes en Colombia/Popayán.
    
    Convierte frases como:
    - "frente a la galería"     → Galería Centenario + offset (0,0)
    - "subiendo al morro"       → Morro de Tulcán + offset norte
    - "después del puente"      → Humilladero + offset sur
    - "abajo de campanario"     → Campanario + offset sur
    - "dos cuadras del parque"  → Parque Caldas + offset este
    """

    @classmethod
    def extract_relative_context(cls, text: str) -> Dict:
        """
        Extrae modificador relativo y landmark base.
        
        Retorna:
        {
          "modifier": "subiendo al",
          "modifier_description": "subiendo hacia",
          "lat_offset": +0.0008,
          "lon_offset": 0.0,
          "landmark": "morro de tulcán",
          "full_match": "subiendo al morro",
        }
        """
        text_lower = strip_accents(text.lower().strip())

        match = _MODIFIER_RE.search(text_lower)
        if not match:
            return {
                "modifier":             None,
                "modifier_description": None,
                "lat_offset":           0.0,
                "lon_offset":           0.0,
                "landmark":             text_lower,
                "full_match":           text_lower,
            }

        modifier_raw = strip_accents(match.group(1).lower().strip())
        offset_data  = RELATIVE_OFFSETS.get(modifier_raw, (0.0, 0.0, modifier_raw))
        lat_off, lon_off, description = offset_data

        # El landmark es todo lo que viene después del modificador+artículo
        landmark_start = match.end()
        landmark       = text_lower[landmark_start:].strip()

        # Limpiar trailing fillers
        landmark = re.sub(
            r"\s+(?:por favor|gracias|pues|dale|ya|sí|entonces)$",
            "", landmark
        ).strip()

        return {
            "modifier":             modifier_raw,
            "modifier_description": description,
            "lat_offset":           lat_off,
            "lon_offset":           lon_off,
            "landmark":             landmark,
            "full_match":           text_lower,
        }

    @classmethod
    def apply_relative_offset(
        cls,
        lat: float,
        lon: float,
        nav_context: Dict,
    ) -> Tuple[float, float]:
        """
        Aplica el offset posicional al punto base.
        El offset es una heurística (~100m) para direcciones relativas.
        """
        new_lat = lat + nav_context.get("lat_offset", 0.0)
        new_lon = lon + nav_context.get("lon_offset", 0.0)

        # Verificar que el resultado sigue dentro de Popayán
        if not _is_in_popayan(new_lat, new_lon):
            return lat, lon  # No aplicar offset si saca de la ciudad

        return new_lat, new_lon

    @classmethod
    def resolve_full_reference(cls, text: str) -> Optional[Dict]:
        """
        Resuelve una referencia de navegación completa a coordenadas.
        
        Proceso:
        1. Extraer modificador + landmark
        2. Resolver landmark → coordenadas
        3. Aplicar offset del modificador
        4. Retornar resultado con contexto
        
        Ejemplo:
        "subiendo al morro de tulcán" →
        {
          "canonical": "Morro de Tulcán",
          "lat": 2.4461, "lon": -76.6064,  (desplazado hacia el norte)
          "description": "subiendo hacia el Morro de Tulcán",
          "confidence": 0.9,
        }
        """
        nav = cls.extract_relative_context(text)
        landmark_text = nav.get("landmark") or text

        # Intentar resolver el landmark
        resolved = (
            resolve_human_reference(landmark_text)
            or resolve_human_reference(text)  # Intentar con texto completo
        )

        if not resolved:
            return None

        base_lat = resolved.get("lat")
        base_lon = resolved.get("lon")

        if base_lat is None or base_lon is None:
            # No hay coordenadas pero sí canonical name
            return {
                "canonical":   resolved["canonical"],
                "lat":         None,
                "lon":         None,
                "description": f"{nav.get('modifier_description', '')} {resolved['canonical']}".strip(),
                "confidence":  0.7,
                "modifier":    nav.get("modifier"),
            }

        # Aplicar offset
        final_lat, final_lon = cls.apply_relative_offset(base_lat, base_lon, nav)

        modifier_desc = nav.get("modifier_description") or ""
        description   = f"{modifier_desc} {resolved['canonical']}".strip() if modifier_desc else resolved["canonical"]

        return {
            "canonical":   resolved["canonical"],
            "lat":         final_lat,
            "lon":         final_lon,
            "description": description,
            "confidence":  0.85,
            "modifier":    nav.get("modifier"),
            "base_coords": (base_lat, base_lon),
            "offset":      (nav.get("lat_offset", 0.0), nav.get("lon_offset", 0.0)),
        }


# ── Matching geográfico contextual ────────────────────────────────────────────

class GeographicMatcher:
    """
    Matching geográfico con:
    - Nombres completos e incompletos
    - Aliases y referencias humanas
    - Corrección fonética de topónimos
    - Ranking por distancia + relevancia semántica
    - Frecuencia de uso (lugares más comunes tienen prioridad)
    """

    # Frecuencia relativa de solicitudes por zona (estimada)
    # Usada para desempatar cuando el score semántico es similar
    ZONE_FREQUENCY: Dict[str, float] = {
        "centro":          1.0,
        "campanario":      0.9,
        "parque caldas":   0.9,
        "universidad del cauca": 0.8,
        "sena":            0.8,
        "los sauces":      0.7,
        "la esmeralda":    0.7,
        "belalcázar":      0.7,
        "valle del ortigal": 0.65,
        "yanaconas":       0.65,
        "pandiguando":     0.60,
        "pubenza":         0.60,
        "morro de tulcán": 0.55,
        "terminal":        0.7,
        "aeropuerto":      0.5,
    }

    def __init__(self, all_locations: Optional[Dict[str, Tuple[float, float]]] = None):
        """
        all_locations: dict de {nombre_canonico: (lat, lon)}
        Si no se provee, usa HUMAN_REFERENCES.
        """
        self.locations = all_locations or self._build_from_human_refs()

    def _build_from_human_refs(self) -> Dict[str, Tuple[float, float]]:
        result = {}
        for key, data in HUMAN_REFERENCES.items():
            if data.get("lat") and data.get("lon"):
                result[data["canonical"]] = (data["lat"], data["lon"])
        return result

    def rank_candidates(
        self,
        user_input:    str,
        candidates:    Optional[List[str]] = None,
        reference_lat: Optional[float]     = None,
        reference_lon: Optional[float]     = None,
        top_n:         int                 = 3,
    ) -> List[Dict]:
        """
        Rankea candidatos por score combinado: semántico + fonético + distancia + frecuencia.
        
        Parámetros:
        - user_input: texto del usuario
        - candidates: lista de nombres a rankear (si None, usa self.locations)
        - reference_lat/lon: si se proveen, favorece candidatos cercanos
        - top_n: máximo de resultados
        
        Retorna lista de {name, score, lat, lon, distance_km}
        """
        candidate_pool = candidates or list(self.locations.keys())
        input_norm     = strip_accents(user_input.lower().strip())

        results = []

        for candidate in candidate_pool:
            cand_norm = strip_accents(candidate.lower().strip())

            # Score semántico-fonético
            semantic_score = combined_score(input_norm, cand_norm)

            # Boost si el input está contenido en el candidato o viceversa
            if input_norm in cand_norm or cand_norm in input_norm:
                semantic_score = min(1.0, semantic_score + 0.20)

            # Boost por frecuencia de uso
            freq_boost = self.ZONE_FREQUENCY.get(cand_norm, 0.0) * 0.10

            # Score de distancia (si tenemos coordenadas de referencia)
            distance_score = 0.0
            lat, lon       = self.locations.get(candidate, (None, None))

            if reference_lat and reference_lon and lat and lon:
                dist_km = _haversine(reference_lat, reference_lon, lat, lon)
                # Más cerca → mejor score (decae con distancia)
                distance_score = max(0.0, 1.0 - dist_km / 10.0) * 0.15
            elif lat and lon:
                # Sin referencia: bonus por estar en Popayán
                distance_score = 0.05 if _is_in_popayan(lat, lon) else 0.0

            total_score = semantic_score * 0.75 + freq_boost + distance_score

            if total_score >= 0.40:  # Umbral mínimo
                results.append({
                    "name":        candidate,
                    "score":       round(total_score, 3),
                    "lat":         lat,
                    "lon":         lon,
                    "distance_km": (
                        round(_haversine(reference_lat, reference_lon, lat, lon), 2)
                        if reference_lat and lat else None
                    ),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]

    def match(
        self,
        user_input:    str,
        reference_lat: Optional[float] = None,
        reference_lon: Optional[float] = None,
        threshold:     float           = 0.50,
    ) -> Optional[Dict]:
        """
        Retorna el mejor match o None si no supera el umbral.
        """
        ranked = self.rank_candidates(user_input, reference_lat=reference_lat, reference_lon=reference_lon)
        if ranked and ranked[0]["score"] >= threshold:
            return ranked[0]
        return None


# ── API pública simplificada ──────────────────────────────────────────────────

_geo_matcher = GeographicMatcher()


def resolve_location(text: str, context_lat: float = None, context_lon: float = None) -> Optional[Dict]:
    """
    Resuelve un texto de ubicación a datos estructurados.
    
    Intenta en orden:
    1. Referencia con modificador relativo ("subiendo al morro")
    2. Referencia humana directa ("por el éxito")
    3. Match geográfico fuzzy
    
    Retorna dict con: canonical, lat, lon, confidence, description
    """
    # 1. Con modificador relativo
    full_ref = NavigationParser.resolve_full_reference(text)
    if full_ref:
        return full_ref

    # 2. Referencia humana directa
    human = resolve_human_reference(text)
    if human:
        return {
            "canonical":   human["canonical"],
            "lat":         human.get("lat"),
            "lon":         human.get("lon"),
            "confidence":  0.85,
            "description": human["canonical"],
        }

    # 3. Match geográfico fuzzy
    geo_match = _geo_matcher.match(text, reference_lat=context_lat, reference_lon=context_lon)
    if geo_match:
        return {
            "canonical":   geo_match["name"],
            "lat":         geo_match.get("lat"),
            "lon":         geo_match.get("lon"),
            "confidence":  geo_match["score"],
            "description": geo_match["name"],
        }

    return None