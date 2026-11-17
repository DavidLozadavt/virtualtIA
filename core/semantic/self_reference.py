"""
core/semantic/self_reference.py — Cuando el usuario se refiere al asistente o a la plataforma.

"¿Qué puede hacer en NexiService?", "¿para qué funciona esta app?", "¿qué
negocios tiene activos esta web?", "hola Lyra". El nombre del asistente, el de
la plataforma o "esta app / esta web" no son contenido que buscar: son el
interlocutor. Leídos como contenido, "web" disparaba "abrir la web de un
negocio" y "nexiservice" se buscaba como rubro.

Los nombres se reconocen por parecido fonético, no por una lista de errores:
el STT del navegador escribe "messi service" o "nequi service" por NexiService.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from core.semantic.morphology import normalize, phonetic

_DEICTIC_PLATFORM = re.compile(
    r"\b(?:en|de|a|con|para)?\s*(?:esta|este|esa)\s+(?:app|aplicacion|aplicación|plataforma|pagina|página|web)\b",  # "este sitio" suele ser un negocio
    re.I)
#: "En LA plataforma", "en la app": con artículo definido también es la propia
#: plataforma. "La página" o "la web" no entran: "la página web de Fogón" es de un negocio.
_DEFINITE_PLATFORM = re.compile(r"\b(?:en|de|a|con|para)?\s*la\s+(?:app|aplicacion|aplicación|plataforma)\b", re.I)
_ABOUT_SELF = re.compile(r"^(?:que es|qué es|para que (?:sirve|sirves|funciona\w*)|para qué (?:sirve|sirves|funciona\w*)|"
                         r"como funciona|cómo funciona|que hace|qué hace)\s*\??$", re.I)


def _like(a: str, b: str) -> bool:
    pa, pb = phonetic(a), phonetic(b)
    return pa == pb or (len(pa) >= 5 and SequenceMatcher(None, pa, pb).ratio() >= 0.8)


def strip_self_reference(text: str, names: Iterable[str]) -> str:
    """El mensaje sin las menciones al asistente o a la plataforma (la petición queda igual)."""
    if not text:
        return text
    targets = [normalize(n).replace(" ", "") for n in names if n]
    words = text.split()
    keep = []
    i = 0
    removed = False
    while i < len(words):
        w = normalize(words[i])
        pair = w + (normalize(words[i + 1]) if i + 1 < len(words) else "")
        # Un nombre partido en dos ("messi service"): la unión tiene que medir
        # casi lo mismo que el nombre y ninguna mitad puede ser una palabra
        # corta suelta ("es", "ofrece" + "nexiservice" no es un nombre partido).
        if (i + 1 < len(words) and len(w) >= 3 and len(normalize(words[i + 1])) >= 4
                and any(len(t) >= 6 and abs(len(pair) - len(t)) <= 2 and _like(pair, t) for t in targets)):
            i += 2
            removed = True
            continue
        if w and any(_like(w, t) for t in targets):
            i += 1
            removed = True
            continue
        keep.append(words[i])
        i += 1
    out = " ".join(keep)
    out2 = _DEFINITE_PLATFORM.sub(" ", _DEICTIC_PLATFORM.sub(" ", out))
    removed = removed or out2 != out
    out = re.sub(r"\s+", " ", out2).strip(" ,")
    out = re.sub(r"\s+(?:en|de|a|con|para)\s*([?¿!.]*)$", r"\1", out).strip(" ,")
    if not removed:
        return text
    if not re.search(r"\w", out):
        return text
    # "¿Qué es NexiService?" / "¿para qué funciona esta app?": preguntan qué
    # hace el asistente.
    if _ABOUT_SELF.match(normalize(out, keep_punctuation=False)):
        return "que puedes hacer"
    return out
