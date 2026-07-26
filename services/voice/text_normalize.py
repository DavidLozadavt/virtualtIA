"""Preparación de texto para la síntesis (es-CO) — pronunciación y prosodia.

Dos etapas, con responsabilidades separadas:

* `normalize_for_speech` — PRONUNCIACIÓN. Convierte números y nomenclatura vial
  colombiana a palabras para que el TTS no deletree ni lea símbolos:
  "Cra. 4 #70AN-09" → "carrera cuatro número setenta A N nueve".
* `polish_prosody` — PUNTUACIÓN. Ajusta comas y puntos para que la respiración
  y la entonación caigan donde caerían en una llamada real. Nunca cambia el
  significado: solo añade o normaliza signos.

`prepare_for_speech` aplica las dos, en ese orden, y es lo único que el TTS
envía al modelo.
"""

from __future__ import annotations

import re

_UNITS = (
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",
    "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",
    "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte",
    "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco",
    "veintiséis", "veintisiete", "veintiocho", "veintinueve",
)
_TENS = {
    30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta",
    70: "setenta", 80: "ochenta", 90: "noventa",
}
_HUNDREDS = {
    100: "cien", 200: "doscientos", 300: "trescientos", 400: "cuatrocientos",
    500: "quinientos", 600: "seiscientos", 700: "setecientos",
    800: "ochocientos", 900: "novecientos",
}

# Abreviaturas viales colombianas → palabra completa. `\b` tras la palabra y
# el punto FUERA del límite ("Cra." → "carrera", sin dejar el punto huérfano).
_STREET_ABBREVS = [
    (r"\bcra\b\.?", "carrera"),
    (r"\bkra\b\.?", "carrera"),
    (r"\bkr\b\.?", "carrera"),
    (r"\bcr\b\.?", "carrera"),
    (r"\bcll\b\.?", "calle"),
    (r"\bcl\b\.?", "calle"),
    (r"\bav\b\.?", "avenida"),
    (r"\bavda\b\.?", "avenida"),
    (r"\btv\b\.?", "transversal"),
    (r"\btransv\b\.?", "transversal"),
    (r"\bdg\b\.?", "diagonal"),
    (r"\bdiag\b\.?", "diagonal"),
    (r"\bmz\b\.?", "manzana"),
    (r"\bapto\b\.?", "apartamento"),
    (r"\bapt\b\.?", "apartamento"),
    (r"\bno\.\s*", "número "),
    (r"\bnro\b\.?", "número"),
    (r"\bn[°º]\s*", "número "),
    (r"\bn\.[°º]\s*", "número "),
    (r"\bur[bh]\b\.?", "urbanización"),
    (r"\bedif\b\.?", "edificio"),
    (r"\bblq?\b\.?", "bloque"),
    (r"\bcc\b\.?", "centro comercial"),
    (r"\bkm\b\.?", "kilómetro"),
]


def number_to_words_es(n: int) -> str:
    """Entero 0..999999 a palabras en español."""
    if n < 0:
        return "menos " + number_to_words_es(-n)
    if n < 30:
        return _UNITS[n]
    if n < 100:
        tens = (n // 10) * 10
        rest = n % 10
        if rest == 0:
            return _TENS[tens]
        return f"{_TENS[tens]} y {_UNITS[rest]}"
    if n < 1000:
        hundreds = (n // 100) * 100
        rest = n % 100
        head = "ciento" if hundreds == 100 and rest else _HUNDREDS[hundreds]
        if rest == 0:
            return _HUNDREDS[hundreds]
        return f"{head} {number_to_words_es(rest)}"
    if n < 1_000_000:
        thousands = n // 1000
        rest = n % 1000
        head = "mil" if thousands == 1 else f"{number_to_words_es(thousands)} mil"
        if rest == 0:
            return head
        return f"{head} {number_to_words_es(rest)}"
    return str(n)  # fuera de rango razonable para una dirección: literal


def _spell_letters(letters: str) -> str:
    """Sufijos de nomenclatura ("8C" → "ocho C") se deletrean letra a letra."""
    return " ".join(letters.upper())


def _expand_address_token(match: re.Match) -> str:
    """"70AN" → "setenta A N"; "8" → "ocho"."""
    digits, letters = match.group(1), match.group(2)
    words = number_to_words_es(int(digits))
    if letters:
        return f"{words} {_spell_letters(letters)}"
    return words


def normalize_for_speech(text: str) -> str:
    """Texto listo para TTS: sin dígitos, sin '#', sin abreviaturas viales."""
    if not text:
        return text
    out = text

    for pattern, replacement in _STREET_ABBREVS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)

    # "#" y "-" dentro de nomenclatura → palabras ("70AN-09" → "70AN 09").
    out = re.sub(r"\s*#\s*", " número ", out)
    out = re.sub(r"(?<=\w)-(?=\d)", " ", out)

    # Separador de miles: "12.000" es UN número, no "doce" y "cero". Se quita
    # antes de expandir para que suene "doce mil" y no "doce punto cero".
    out = re.sub(r"\b\d{1,3}(?:[.,]\d{3})+\b", lambda m: re.sub(r"[.,]", "", m.group(0)), out)

    # Símbolos que jamás se leen literalmente.
    out = re.sub(r"\$\s*(\d+)", r"\1 pesos", out)
    out = re.sub(r"(\d)\s*%", r"\1 por ciento", out)

    # Números pegados a letras de nomenclatura ("8C", "70AN") y números puros.
    out = re.sub(r"\b(\d{1,6})([A-Za-z]{1,3})?\b", _expand_address_token, out)

    return re.sub(r"\s{2,}", " ", out).strip()


# ── Prosodia: puntuación al servicio de la respiración y la entonación ──────

# Marcadores con los que arranca una operadora ("Listo, te ubico..."). Sin la
# coma el modelo los pega a lo que sigue y la frase sale de un tirón.
_OPENERS = (
    "listo", "perfecto", "claro", "bueno", "entonces", "mira", "vale", "dale",
    "muy bien", "excelente", "de una", "por supuesto", "a ver", "ok",
    "con gusto", "tranquilo", "tranquila", "señor", "señora",
)
_OPENER_RE = re.compile(
    r"^(" + "|".join(_OPENERS) + r")(?=\s+[¿¡\w])", re.IGNORECASE
)

# Coletillas que en el habla real van precedidas de coma.
_TAIL_RE = re.compile(
    r"(?<=[\wáéíóúñ])\s+(por favor|si quieres|si gustas|entonces|ya mismo)\b",
    re.IGNORECASE,
)

_TERMINALS = ".?!…:"

# Frontera de la última oración/cláusula: es ahí donde empieza lo que se
# pregunta o se exclama, no al principio del texto.
_CLAUSE_BOUNDARY = re.compile(r"[.!?…;:,]\s*(?=\S)")


def _open_final_clause(text: str, closing: str, opening: str) -> str:
    """Abre la última cláusula si termina en `closing` y le falta `opening`.

    "Listo, te ubico en Pubenza. Es correcto?" → "... Pubenza. ¿Es correcto?"
    y no "Listo, ¿te ubico...", que cambiaría la entonación de toda la frase.
    """
    if not text.endswith(closing) or opening in text:
        return text
    starts = [m.end() for m in _CLAUSE_BOUNDARY.finditer(text[:-1])]
    at = starts[-1] if starts else 0
    return f"{text[:at]}{opening}{text[at:]}"


def polish_prosody(text: str) -> str:
    """Puntuación que mejora la prosodia sin tocar el significado.

    Solo añade o normaliza signos: coma tras el marcador inicial y antes de las
    coletillas (respiración), apertura "¿" en las preguntas (la entonación
    ascendente del español la dispara el signo de apertura, no el de cierre) y
    punto final (para que la frase caiga en vez de quedar suspendida).
    """
    if not text:
        return text
    out = re.sub(r"\s+", " ", text).strip()
    if not out:
        return out

    out = _OPENER_RE.sub(lambda m: f"{m.group(1)},", out)
    out = _TAIL_RE.sub(lambda m: f", {m.group(1)}", out)

    # Signos duplicados por los pasos anteriores o por el texto de origen.
    out = re.sub(r",\s*,+", ",", out)
    out = re.sub(r",\s*(?=[.?!…:;])", "", out)
    out = re.sub(r"\s+([,.?!;:])", r"\1", out)

    # En español la entonación la dispara el signo de APERTURA, no el de cierre.
    out = _open_final_clause(out, "?", "¿")
    out = _open_final_clause(out, "!", "¡")

    if out[-1] not in _TERMINALS:
        out = f"{out}."

    return out


def prepare_for_speech(text: str) -> str:
    """Texto tal como se envía al modelo: pronunciación + prosodia."""
    return polish_prosody(normalize_for_speech(text))


_SENTENCE_SPLIT = re.compile(r"([.?!…])\s+")
_MIN_CHUNK_CHARS = 12

# Abreviaturas cuyo punto NO cierra una oración. Sin esta lista "Cra. 4" se
# partía en dos: la operadora decía "te ubico en la carrera." y empezaba una
# frase nueva con el número — una dirección leída como dos cosas distintas.
_ABBREV_TOKENS = frozenset(
    {
        "cra", "kra", "kr", "cr", "cll", "cl", "av", "avda", "tv", "transv",
        "dg", "diag", "mz", "apto", "apt", "no", "nro", "urb", "urh", "edif",
        "bl", "blq", "cc", "km", "sr", "sra", "srta", "dr", "dra", "etc",
        "aprox", "ext", "piso",
    }
)


def _closes_sentence(text: str, sign: str) -> bool:
    """¿El signo que cierra `text` termina de verdad la oración?

    `text` incluye el signo, así que se mira la palabra que lo precede.
    """
    if sign != ".":
        return True
    match = re.search(r"([\wáéíóúñÁÉÍÓÚÑ]+)$", text[:-1])
    if match is None:
        return True
    word = match.group(1).lower()
    # Abreviatura o inicial ("J. Pérez"): el punto es parte del término.
    return word not in _ABBREV_TOKENS and len(word) > 1


def split_sentences(text: str) -> list[str]:
    """Divide en oraciones para síntesis incremental.

    Fragmentos muy cortos se fusionan con el siguiente para no pagar el
    overhead de una conexión TTS por dos palabras.
    """
    raw = text or ""
    parts: list[str] = []
    buffer: list[str] = []
    last = 0
    for match in _SENTENCE_SPLIT.finditer(raw):
        piece = raw[last : match.end(1)]
        buffer.append(piece)
        last = match.end()
        if _closes_sentence(piece, match.group(1)):
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(raw[match.end(1) : match.end()])
    tail = raw[last:]
    if tail:
        buffer.append(tail)
    if buffer:
        parts.append("".join(buffer))

    parts = [p.strip() for p in parts if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1]) < _MIN_CHUNK_CHARS:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged
