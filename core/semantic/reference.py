"""
core/semantic/reference.py — Resolución de referencias entre mensajes.

"el segundo", "ese", "con ella", "ahí", "el primero que me mostraste": son
expresiones que no significan nada por sí mismas. Sólo significan si el sistema
recuerda qué le enseñó al usuario y en qué orden.

Antes, esa memoria se reconstruía raspando con expresiones regulares el markdown
que el propio asistente había escrito (negritas, etiquetas [BIZ:id]). Funciona
mientras el asistente escriba siempre igual. Aquí la memoria es explícita: lo que
se mostró se guarda como datos, con su posición, y la referencia se resuelve
contra esos datos.
"""

from typing import List, Optional

from core.semantic import lexicon as lx
from core.semantic.morphology import normalize, phrase_overlap
from core.semantic.speech_act import Analysis
from core.semantic.types import ConceptKind, ConversationState, PresentedItem


#: Pronombres que indican persona antes que lugar ("con ella", "con él").
_PERSON_PRONOUNS = frozenset({"ella", "ellas", "el", "ellos"})

#: Adverbios que apuntan a un lugar ya mencionado ("ahí", "allá").
_PLACE_ADVERBS = frozenset({"ahi", "alli", "alla", "aqui", "aca"})


def resolve(analysis: Analysis, state: ConversationState, message: str = "") -> Optional[PresentedItem]:
    """
    Devuelve el elemento al que apunta el mensaje, o None si no hay referencia
    resoluble con seguridad.

    Nunca adivina: si hay varias listas posibles o la posición no existe,
    prefiere devolver None y que el sistema pregunte.
    """
    presented = state.presented or []

    # ── 1. Posición explícita: "el segundo", "el último" ────────────────────
    if analysis.ordinal is not None and presented:
        item = _by_position(presented, analysis.ordinal, analysis)
        if item:
            return item

    # ── 2. Referencia por nombre parcial: "quiero el de Fogón Criollo" ──────
    if message and presented:
        item = _by_partial_name(presented, message)
        if item:
            return item

    tokens = set(normalize(message).split()) if message else set()

    # ── 3. Pronombre de persona: "con ella", "con él" ───────────────────────
    if tokens & _PERSON_PRONOUNS:
        person = _first_of_kind(presented, "professional")
        if person:
            return person

    # ── 4. Adverbio de lugar: "reservar ahí", "quiero ir allá" ──────────────
    if tokens & _PLACE_ADVERBS:
        focused = _focused_item(state)
        if focused:
            return focused
        business = _first_of_kind(presented, ConceptKind.BUSINESS)
        if business and _is_unambiguous(presented, ConceptKind.BUSINESS):
            return business

    # ── 5. Demostrativo genérico: "ese", "eso", "esa opción" ────────────────
    if analysis.anaphoric:
        focused = _focused_item(state)
        if focused:
            return focused
        # Sin foco, sólo se resuelve si la lista deja una única lectura posible.
        if len(presented) == 1:
            return presented[0]

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Auxiliares
# ═══════════════════════════════════════════════════════════════════════════════

def _by_position(presented: List[PresentedItem], ordinal: int, analysis: Analysis) -> Optional[PresentedItem]:
    """Selecciona por posición, respetando el tipo de cosa que se pregunta."""
    pool = presented
    # "quiero reservar con el segundo" tras una lista de profesionales debe
    # elegir dentro de esa lista, no dentro de una lista de negocios anterior.
    if "human_agent" in analysis.frames:
        people = [p for p in presented if p.kind == "professional"]
        if people:
            pool = people

    if not pool:
        return None
    if ordinal == -1:
        return pool[-1]
    if 1 <= ordinal <= len(pool):
        return pool[ordinal - 1]
    return None


def _by_partial_name(presented: List[PresentedItem], message: str) -> Optional[PresentedItem]:
    """Coincidencia por nombre parcial contra lo ya mostrado."""
    best, best_score = None, 0.62
    for item in presented:
        if not item.label:
            continue
        score = phrase_overlap(item.label, message)
        if score > best_score:
            best, best_score = item, score
    return best


def _first_of_kind(presented: List[PresentedItem], kind: str) -> Optional[PresentedItem]:
    for item in presented:
        if item.kind == kind:
            return item
    return None


def _is_unambiguous(presented: List[PresentedItem], kind: str) -> bool:
    return sum(1 for p in presented if p.kind == kind) == 1


def _focused_item(state: ConversationState) -> Optional[PresentedItem]:
    if not state.focus_id and not state.focus_label:
        return None
    return PresentedItem(
        kind=state.focus_kind or ConceptKind.BUSINESS,
        entity_id=state.focus_id,
        label=state.focus_label or "",
        position=0,
    )


def has_resolvable_context(state: ConversationState) -> bool:
    """True si hay algo a lo que una referencia podría apuntar."""
    return bool(state.presented or state.focus_id)
