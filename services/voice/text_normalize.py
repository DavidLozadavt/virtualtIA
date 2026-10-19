"""Normalización de texto a habla (es-CO) antes de sintetizar.

Convierte números y nomenclatura vial colombiana a palabras para que el TTS
no deletree ni lea símbolos: "Cra. 4 #70AN-09" → "carrera cuatro número
setenta A N cero nueve". Gap identificado en la auditoría V1 (§3.5 del spec).
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
    (r"\bn°\s*", "número "),
    (r"\bur[bh]\b\.?", "urbanización"),
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

    # Números pegados a letras de nomenclatura ("8C", "70AN") y números puros.
    out = re.sub(r"\b(\d{1,6})([A-Za-z]{1,3})?\b", _expand_address_token, out)

    return re.sub(r"\s{2,}", " ", out).strip()


_SENTENCE_SPLIT = re.compile(r"(?<=[\.\?\!…])\s+")
_MIN_CHUNK_CHARS = 12


def split_sentences(text: str) -> list[str]:
    """Divide en oraciones para síntesis incremental.

    Fragmentos muy cortos se fusionan con el siguiente para no pagar el
    overhead de una conexión TTS por dos palabras.
    """
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "") if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1]) < _MIN_CHUNK_CHARS:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged
