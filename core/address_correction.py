"""
core/address_correction.py — Correcciones PARCIALES de una dirección ya capturada.

Autoridad única y agnóstica del canal. La lógica vivía dentro del orquestador de
voz (services/voice/orchestrator.py) acoplada a CallSession; aquí queda como
funciones puras para que la llamada telefónica y WhatsApp compartan exactamente
el mismo comportamiento en vez de duplicarlo.

Caso que resuelve: el usuario no repite la dirección completa, solo corrige un
pedazo.

    Bot:     ¿Carrera 52 #3B-6 es correcto?
    Usuario: no, es 3C-6
    →        Cra. 52 #3C-6      (la vía se conserva, la placa se reemplaza)

Reglas (precision-first):
  - Solo es corrección si el turno es EXCLUSIVAMENTE una placa más preámbulos
    ("no", "es", "perdón"). Si trae nomenclatura de vía es una dirección nueva.
  - La dirección previa debe ser una vía con número; si no, no hay nada que
    corregir de forma segura.
  - El resultado pasa siempre por core.co_address_parser (única autoridad de
    direcciones): si no queda una dirección válida, no se corrige nada.
"""

from __future__ import annotations

import re
from typing import Optional

from core.co_address_parser import AddressState, parse_co_address
from core.stt_enhancer import strip_accents

# Placa de corrección: "17-25", "17A-25", "3C-6"… (cruce[letra]-distancia).
_PLACA_TOKEN_RE = re.compile(r"\b(\d{1,3}[A-Za-z]?)\s*-\s*(\d{1,4})\b")

# Preámbulos de corrección / relleno que suelen preceder a la placa corregida.
_PLACA_PREAMBLE_RE = re.compile(
    r"\b(?:no|nop|nel|perdon|disculpa|disculpe|es|era|seria|mejor|digo|osea|"
    r"o\s*sea|mas\s*bien|quise\s*decir|queria\s*decir|el|la|numero|nro)\b",
    re.IGNORECASE,
)

# Vía con número: distingue una dirección de calle de una placa suelta.
_STREET_RE = re.compile(r"(?:calle|carrera|cl|cra|kr|kra)\s*\.?\s*\d+", re.IGNORECASE)


def looks_like_street(text: str) -> bool:
    return bool(_STREET_RE.search((text or "").lower()))


def extract_placa_correction(text: str) -> Optional[str]:
    """Placa de una corrección PARCIAL ("No, 17-25" → "17-25"), o None.

    Solo devuelve algo cuando el turno es exclusivamente una placa (más
    preámbulos de corrección/relleno): sin nomenclatura de vía ni otro contenido
    significativo. Así "17-25 en el centro" o "cra 5 #17-25" NO se tratan como
    corrección de placa.
    """
    if not text:
        return None
    t = strip_accents(text.strip().lower()).replace("#", " ")
    if looks_like_street(t):
        return None                       # trae vía → dirección nueva, no corrección
    m = _PLACA_TOKEN_RE.search(t)
    if not m:
        return None
    remainder = _PLACA_TOKEN_RE.sub(" ", t)
    remainder = _PLACA_PREAMBLE_RE.sub(" ", remainder)
    remainder = re.sub(r"[^\w]+", " ", remainder)
    remainder = " ".join(remainder.split()).strip()
    if len(remainder) > 2:
        return None                       # queda contenido → no es corrección pura
    return f"{m.group(1).upper()}-{m.group(2)}"


# Cruce suelto de una corrección aún más parcial: "no es #3C", "no, 17A".
_CRUCE_TOKEN_RE = re.compile(r"\b(\d{1,3}[A-Za-z]?)\b")


def extract_cruce_correction(text: str) -> Optional[str]:
    """Cruce de una corrección parcial sin distancia ("no es #3C" → "3C"), o None.

    Solo el cruce cambia; la distancia de la dirección previa se conserva.
    Precision-first: exige señal inequívoca de que es nomenclatura —una letra
    de sufijo ("3C", "17A") o un "#" explícito—. Así un "no, 5" suelto, que
    podría ser cualquier cosa, no se toma como corrección.
    """
    if not text:
        return None
    raw = text.strip()
    t = strip_accents(raw.lower())
    if looks_like_street(t):
        return None                       # trae vía → dirección nueva
    if _PLACA_TOKEN_RE.search(t.replace("#", " ")):
        return None                       # placa completa → la maneja el otro extractor

    tiene_almohadilla = "#" in raw
    t = t.replace("#", " ")

    m = _CRUCE_TOKEN_RE.search(t)
    if not m:
        return None

    token = m.group(1)
    tiene_letra = bool(re.search(r"[A-Za-z]", token))
    if not (tiene_letra or tiene_almohadilla):
        return None                       # número pelado sin "#": demasiado ambiguo

    remainder = _CRUCE_TOKEN_RE.sub(" ", t)
    remainder = _PLACA_PREAMBLE_RE.sub(" ", remainder)
    remainder = re.sub(r"[^\w]+", " ", remainder)
    remainder = " ".join(remainder.split()).strip()
    if len(remainder) > 2:
        return None                       # queda contenido → no es corrección pura

    return token.upper()


def apply_cruce_correction(stored: Optional[str], cruce: str) -> Optional[str]:
    """Reemplaza solo el cruce de `stored`, conservando vía y distancia.

    None si la dirección previa no tiene una placa completa que corregir o si el
    resultado no valida como dirección de vía.
    """
    if not stored or not cruce:
        return None

    ast = parse_co_address(stored).ast
    if ast is None or ast.via is None or ast.via.numero is None:
        return None

    m = _PLACA_TOKEN_RE.search(stored.replace("#", " "))
    if not m:
        return None                       # sin distancia previa no hay qué conservar

    return apply_placa_correction(stored, f"{cruce}-{m.group(2)}")


def apply_placa_correction(stored: Optional[str], placa: str) -> Optional[str]:
    """Reemplaza la placa de `stored` conservando la vía. None si no aplica.

    Devuelve None cuando la dirección previa no es una vía con número (no hay
    nada que corregir) o cuando el resultado no valida como dirección de vía.
    """
    if not stored or not placa:
        return None

    stored_parsed = parse_co_address(stored)
    ast = stored_parsed.ast
    if ast is None or ast.via is None or ast.via.numero is None:
        return None

    base = re.sub(r"#.*$", "", stored).strip()
    if not base:
        return None

    new_parsed = parse_co_address(f"{base} #{placa}")
    if new_parsed.state != AddressState.STREET_ADDRESS or not new_parsed.canonical:
        return None

    return new_parsed.canonical


def correct_address(stored: Optional[str], text: str) -> Optional[str]:
    """Dirección corregida a partir de un turno de corrección parcial, o None.

    Cubre las dos formas en que la gente corrige sin repetir todo:
      - placa completa  → "no, es 3C-6"  (cambian cruce y distancia)
      - solo el cruce   → "no es #3C"    (la distancia previa se conserva)

    None significa "este turno no es una corrección parcial aplicable"; el
    llamador decide qué hacer entonces.
    """
    placa = extract_placa_correction(text)
    if placa:
        return apply_placa_correction(stored, placa)

    cruce = extract_cruce_correction(text)
    if cruce:
        return apply_cruce_correction(stored, cruce)

    return None
