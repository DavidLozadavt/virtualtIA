"""
orchestrator/response_engine.py — Motor de respuestas determinista sin LLM.

Selecciona plantillas variadas desde response_templates.yaml según la
personalidad activa y el intent detectado. Evita repetir las últimas 5
respuestas de la sesión usando un historial en memoria.
"""

import logging
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("lyra.response_engine")

_templates: dict = {}
_last_loaded_mtime: float = 0.0
_TEMPLATES_PATH = Path(__file__).parent.parent / "projects" / "response_templates.yaml"

# Historial anti-repetición en memoria: conversation_id → deque de últimos N índices usados
# Clave compuesta: (conversation_id, personality, intent, scenario) → deque[int]
_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))


def _load_templates() -> dict:
    """Carga (o recarga si cambió) el archivo de plantillas YAML."""
    global _templates, _last_loaded_mtime

    if not _TEMPLATES_PATH.exists():
        logger.error(f"Archivo de plantillas no encontrado: {_TEMPLATES_PATH}")
        return _templates

    current_mtime = _TEMPLATES_PATH.stat().st_mtime
    if current_mtime != _last_loaded_mtime:
        try:
            with open(_TEMPLATES_PATH, "r", encoding="utf-8") as f:
                _templates = yaml.safe_load(f) or {}
            _last_loaded_mtime = current_mtime
            logger.info(f"[ResponseEngine] Plantillas cargadas/recargadas ({len(_templates)} personalidades)")
        except Exception as e:
            logger.error(f"[ResponseEngine] Error cargando plantillas: {e}")

    return _templates


def _get_variations(personality: str, intent: str, scenario: str = "default") -> list[str]:
    """Obtiene la lista de variaciones para una personalidad/intent/escenario."""
    templates = _load_templates()
    persona_data = templates.get(personality, {})
    intent_data = persona_data.get(intent, {})

    if isinstance(intent_data, list):
        return intent_data

    if isinstance(intent_data, dict):
        variations = intent_data.get(scenario, [])
        if variations:
            return variations
        return intent_data.get("default", [])

    return []


def _select_template(
    conversation_id: str,
    personality: str,
    intent: str,
    scenario: str = "default",
) -> Optional[str]:
    """
    Selecciona una plantilla evitando repetir las últimas 5 usadas en la sesión.
    Retorna None si no hay plantillas disponibles.
    """
    variations = _get_variations(personality, intent, scenario)
    if not variations:
        return None

    history_key = f"{conversation_id}:{personality}:{intent}:{scenario}"
    recent_indices = _history[history_key]

    # Índices disponibles (no usados recientemente)
    available = [i for i in range(len(variations)) if i not in recent_indices]

    if not available:
        # Si todas se usaron recientemente, reseteamos y elegimos cualquiera
        recent_indices.clear()
        available = list(range(len(variations)))

    chosen_idx = random.choice(available)
    recent_indices.append(chosen_idx)

    return variations[chosen_idx]


def _format_distance(distance_km: Optional[float]) -> str:
    """Formatea distancia: metros si <1km, km si >=1km."""
    if distance_km is None:
        return "distancia desconocida"
    try:
        d = float(distance_km)
        if d < 1.0:
            return f"{int(d * 1000)} metros"
        return f"{d:.1f} km"
    except (ValueError, TypeError):
        return "distancia desconocida"


def _build_business_list(businesses: list[dict], max_items: int = 12) -> str:
    """Genera la lista con viñetas de negocios con tags BIZ para el frontend."""
    lines = []
    for b in businesses[:max_items]:
        biz_id = b.get("id", "")
        name = b.get("name", "Desconocido")
        address = b.get("address", "").strip()
        addr_str = f" ({address})" if address else ""
        
        # El backend puede devolver 'distance' o 'distance_km'
        dist_val = b.get("distance_km") if b.get("distance_km") is not None else b.get("distance")
        dist_str = f" a {_format_distance(dist_val)}" if dist_val is not None else ""
        lines.append(f"- [**{name}**](/empresa/{biz_id}){addr_str}{dist_str} [TAG:{biz_id}] [BIZ:{biz_id}]")
    return "\n".join(lines)


def generate_response(
    conversation_id: str,
    personality: str,
    intent: str,
    scenario: str = "default",
    variables: Optional[dict] = None,
) -> Optional[str]:
    """
    Genera una respuesta a partir de las plantillas YAML.

    Args:
        conversation_id: ID de la conversación (para anti-repetición).
        personality: Personalidad activa ('lyra' o 'nexo').
        intent: Nombre del intent detectado.
        scenario: Escenario dentro del intent ('default', 'found', 'not_found', etc.).
        variables: Diccionario de variables para rellenar las plantillas.

    Returns:
        String con la respuesta formateada, o None si no hay plantilla disponible.
    """
    template = _select_template(conversation_id, personality, intent, scenario)
    if template is None:
        logger.warning(
            f"[ResponseEngine] Sin plantilla: personality={personality}, "
            f"intent={intent}, scenario={scenario}"
        )
        return None

    if variables:
        try:
            return template.format(**variables)
        except KeyError as e:
            logger.warning(f"[ResponseEngine] Variable faltante en plantilla: {e}")
            # Fallback: reemplazar solo las variables disponibles
            result = template
            for key, value in variables.items():
                result = result.replace(f"{{{key}}}", str(value))
            return result

    return template


def clear_session_history(conversation_id: str) -> None:
    """Limpia el historial anti-repetición de una conversación."""
    keys_to_remove = [k for k in _history if k.startswith(f"{conversation_id}:")]
    for key in keys_to_remove:
        del _history[key]
