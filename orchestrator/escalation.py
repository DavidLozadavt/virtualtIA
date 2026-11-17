"""
orchestrator/escalation.py — Cuándo un turno merece ir al modelo externo.

Llegar al final de los interceptores sin respuesta no basta para gastar tokens.
Antes de escalar se comprueba que el modelo pueda aportar algo que lo local no
tiene; si no, se responde aquí (aclaración breve) y el motivo queda registrado.

Cada decisión lleva un motivo que se guarda en `lyra_llm_usage.gate_reason`
(migración 006). Agrupar por ese motivo dice qué capacidad local falta y cuánto
cuesta no tenerla.

Sólo gobierna NexiService y SchoolSena; los demás proyectos conservan su
comportamiento (escalan como antes, con motivo "legacy").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from core.semantic.morphology import normalize

GOVERNED_PROJECTS = {"nexiservice", "schoolsena"}

_VOWELS = re.compile(r"[aeiouáéíóú]")


@dataclass
class Decision:
    escalate: bool
    reason: str


def _looks_like_noise(text: str) -> bool:
    """Teclazos ("asdsasaddas"), símbolos o una sola letra: nada que interpretar."""
    words = [w for w in normalize(text).split() if w.isalpha()]
    if not words:
        return True
    if len(words) == 1 and len(words[0]) <= 2:
        return True
    letters = "".join(words)
    if len(set(letters)) <= 3 and len(letters) >= 4:
        return True
    vowel_ratio = len(_VOWELS.findall(letters)) / max(1, len(letters))
    return vowel_ratio < 0.2


def decide(project_id: str, user_text: str, intent: Optional[dict], final_data: dict) -> Decision:
    """¿Este turno, que nada local resolvió, debe ir al modelo?"""
    if project_id not in GOVERNED_PROJECTS:
        return Decision(True, "legacy")

    if _looks_like_noise(user_text):
        return Decision(False, "noise")

    reason = final_data.get("_escalation_reason")
    if reason:
        return Decision(True, str(reason))

    # Lo único que el modelo aporta sobre la capa local es entender una frase
    # que no se ancló. Un mensaje de una o dos palabras no tiene contexto
    # suficiente para que el modelo acierte más que una pregunta de vuelta.
    content = [w for w in normalize(user_text).split() if len(w) > 2]
    if len(content) < 2:
        return Decision(False, "too_short")

    name = (intent or {}).get("intent") or "none"
    return Decision(True, f"unresolved:{name}")
