"""
core/semantic/display_text.py — Cómo se MUESTRA en el chat lo que el usuario dictó.

El reconocimiento de voz del navegador devuelve texto sin mayúsculas, sin
signos y sin tildes en los interrogativos: "como me fue en el examen de ayer".
Mostrarlo tal cual se ve descuidado; reescribirlo cambiaría lo que la persona
dijo. Aquí sólo se corrige la FORMA:

  * mayúscula inicial;
  * signos de interrogación cuando la frase es una pregunta (empieza por un
    interrogativo o lo trae tras un saludo), con su tilde ("como" → "¿Cómo");
  * punto final en lo demás. Un imperativo ("recomiéndame hospitales") lleva
    punto, nunca "¿…?": "¿Recomiéndame…?" es agramatical;
  * espacios y signos sueltos.

No se cambian palabras, no se reordena nada y no se corrige ortografía más
allá de la tilde del interrogativo: el texto literal se guarda aparte (es el que
se persiste), y la comprensión usa su propia versión normalizada.
"""

from __future__ import annotations

import re

_INTERROGATIVES = {
    "que": "qué", "qué": "qué", "como": "cómo", "cómo": "cómo", "cuanto": "cuánto", "cuánto": "cuánto",
    "cuanta": "cuánta", "cuantos": "cuántos", "cuantas": "cuántas", "donde": "dónde", "dónde": "dónde",
    "adonde": "adónde", "cual": "cuál", "cuál": "cuál", "cuales": "cuáles", "quien": "quién",
    "quién": "quién", "quienes": "quiénes", "cuando": "cuándo", "cuándo": "cuándo",
    "por que": "por qué", "por qué": "por qué", "para que": "para qué",
}
# Preguntas de sí/no que empiezan por un verbo conjugado de consulta.
_YESNO_START = re.compile(
    r"^(tienes|tienen|tiene|hay|puedes|puedo|podrias|podrías|sabes|me puedes|me podrias|me podrías|"
    r"es posible|existe|existen|tengo|hay algun|hay algún|me ayudas|me dices|me recomiendas)\b",
    re.I)
# Sólo saludos: "mira que…", "oye que…" llevan un "que" conjunción, no una pregunta.
_GREETING = re.compile(r"^(hola|buenas|buenos días|buenos dias|buenas tardes|buenas noches)\b[,\s]*",
                       re.I)


def _interrogative_at(text: str) -> tuple[str, str] | None:
    low = text.lower()
    for raw in sorted(_INTERROGATIVES, key=len, reverse=True):
        if low.startswith(raw + " ") or low == raw:
            return raw, _INTERROGATIVES[raw]
    return None


def format_transcript(text: str) -> str:
    """Versión para mostrar de una transcripción de voz (no cambia las palabras)."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return t
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    if t[-1] in ".!?…":
        return t[0].upper() + t[1:]

    greeting = ""
    m = _GREETING.match(t)
    body = t
    if m and len(t) > m.end():
        greeting = m.group(0).strip().rstrip(",")
        body = t[m.end():]

    hit = _interrogative_at(body)
    if hit:
        raw, accented = hit
        body = "¿" + accented + body[len(raw):] + "?"
    elif _YESNO_START.match(body):
        body = "¿" + body + "?"
    else:
        body = body + "."

    if greeting:
        greeting = greeting[0].upper() + greeting[1:]
        # "¿" no lleva mayúscula detrás de sí la palabra siguiente en minúscula si
        # sigue a un saludo: "Hola, ¿cómo estás?".
        return f"{greeting}, {body}"
    if body.startswith("¿"):
        return "¿" + body[1:2].upper() + body[2:]
    return body[0].upper() + body[1:]
