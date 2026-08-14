"""
core/semantic/llm_resolver.py — Etapa C: traducir una necesidad al catálogo real.

El anclaje léxico (etapa B) resuelve todo lo que comparte palabras con la base de
datos. Lo que no resuelve es el salto de sentido: "dónde llevar a mi perro" no
comparte ninguna palabra con "Mascotas", y "revisar los ojos" no comparte ninguna
con "Salud y Bienestar". Ese salto necesita conocimiento del mundo, y para eso
está el LLM que el proyecto ya usa.

Dos restricciones lo mantienen honesto:

  * Sólo puede elegir entre etiquetas que EXISTEN en NexiService. La lista se le
    entrega en el prompt; no puede inventar un dominio.
  * NINGUNO es una respuesta válida y esperada. Sin esa salida, el sistema
    volvería a inventar intenciones, que es justo el defecto que se corrige.

Se invoca sólo cuando la etapa A dijo "esto es una necesidad" y la etapa B no
logró anclarla, y el resultado se cachea por contenido.
"""

import json
import logging
import re
import threading
from typing import Callable, List, Optional, Sequence

from core.semantic.morphology import normalize
from core.semantic.types import ConceptKind, GroundedConcept

logger = logging.getLogger("lyra.semantic.llm")


_PROMPT = """Eres el componente de comprensión de NexiService, un directorio de negocios en Colombia.

Tu única tarea: decidir a qué categoría REAL del catálogo corresponde lo que necesita una persona.

CATEGORÍAS DISPONIBLES (son las únicas que existen):
{domains}

MENSAJE DE LA PERSONA:
"{message}"

Reglas:
- Responde SOLO con JSON, sin texto alrededor.
- Elige como máximo 2 categorías, la más probable primero.
- Copia la etiqueta EXACTA de la lista. No inventes categorías ni las traduzcas.
- Si el mensaje no expresa una necesidad de producto o servicio (es un saludo, una
  queja, un insulto, una pregunta sobre ti, o algo sin sentido), responde con la
  lista vacía. La lista vacía es una respuesta correcta y frecuente.
- Si expresa una necesidad pero ninguna categoría de la lista la cubre, responde
  también con la lista vacía. No fuerces la coincidencia más cercana.

Formato exacto:
{{"categorias": ["<etiqueta exacta>"], "confianza": 0.0}}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_CACHE_MAX = 512
_cache_lock = threading.Lock()
_cache: dict = {}

#: Inyectable para pruebas: recibe (prompt) y devuelve el texto crudo del modelo.
_completion_fn: Optional[Callable[[str], str]] = None


def set_completion_fn(fn: Optional[Callable[[str], str]]) -> None:
    """Sustituye la llamada al modelo (pruebas deterministas)."""
    global _completion_fn
    _completion_fn = fn
    clear_cache()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _default_completion(prompt: str) -> str:
    """Llama al mismo motor LLM que ya usa el orquestador."""
    from core.llm_engine import LLMEngine

    engine = LLMEngine(model_path="")
    return engine.generate(
        [{"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0.0,
    )


def _parse(raw: str, valid_labels: Sequence[str]) -> List[str]:
    """Extrae etiquetas válidas de la respuesta del modelo, descartando invenciones."""
    if not raw:
        return []
    match = _JSON_RE.search(raw)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []

    proposed = data.get("categorias") or data.get("categories") or []
    if isinstance(proposed, str):
        proposed = [proposed]
    if not isinstance(proposed, list):
        return []

    # Sólo sobreviven las etiquetas que realmente existen en el catálogo.
    by_norm = {normalize(label): label for label in valid_labels}
    out: List[str] = []
    for item in proposed:
        canonical = by_norm.get(normalize(str(item)))
        if canonical and canonical not in out:
            out.append(canonical)
    return out[:2]


def resolve(
    message: str,
    content_terms: Sequence[str],
    domain_labels: Sequence[str],
) -> List[GroundedConcept]:
    """
    Traduce una necesidad expresada en lenguaje natural a categorías reales.

    Devuelve lista vacía cuando no hay correspondencia: es la señal de que el
    sistema entendió la forma del mensaje pero no encontró nada que le
    corresponda dentro de NexiService.
    """
    if not domain_labels:
        return []

    # Sin modelo externo habilitado no se sale a la red. El anclaje léxico ya
    # cubrió lo que comparte palabras con el catálogo —incluidos los nombres
    # populares de cada rubro—, y lo que no, se pregunta. Es la diferencia entre
    # una ayuda opcional y una dependencia.
    if _completion_fn is None:
        from core.config import settings
        if not settings.LLM_EXTERNAL_ENABLED:
            logger.debug("Resolutor semántico desactivado (modo local).")
            return []

    key = normalize(" ".join(content_terms)) or normalize(message)
    if not key:
        return []

    with _cache_lock:
        if key in _cache:
            return [_clone(c) for c in _cache[key]]

    prompt = _PROMPT.format(
        domains="\n".join(f"- {label}" for label in domain_labels),
        message=message.strip()[:300],
    )

    fn = _completion_fn or _default_completion
    try:
        raw = fn(prompt)
    except Exception as exc:
        # Sin modelo se sigue funcionando: el anclaje léxico ya resolvió lo que
        # comparte palabras con el catálogo, y lo que no, se pregunta.
        logger.warning("Resolutor semántico no disponible: %s", exc)
        return []

    labels = _parse(raw, domain_labels)
    concepts = [
        GroundedConcept(
            kind=ConceptKind.BUSINESS_CATEGORY,
            label=label,
            score=0.80 - (0.08 * idx),
            search_terms=[label],
            source="llm",
        )
        for idx, label in enumerate(labels)
    ]

    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = [_clone(c) for c in concepts]

    logger.info("Resolución semántica: %r → %s", key, labels or "NINGUNO")
    return concepts


def _clone(c: GroundedConcept) -> GroundedConcept:
    return GroundedConcept(
        kind=c.kind, label=c.label, score=c.score, entity_id=c.entity_id,
        search_terms=list(c.search_terms), source=c.source,
    )
