"""Utilidades de normalización de teléfono — agnósticas al canal."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


def limpiar_numero(valor: str | None) -> str | None:
    if not valor:
        return None
    match = re.search(r"\+?\d{7,15}", str(valor))
    if not match:
        return None
    numero = match.group(0)
    if numero.startswith("+"):
        return numero
    if len(numero) == 10 and numero.startswith("3"):
        return "+57" + numero
    if len(numero) == 12 and numero.startswith("57"):
        return "+" + numero
    return numero


def es_numero_troncal_o_empresa(numero: str | None) -> bool:
    if not numero:
        return False
    limpio = re.sub(r"\D", "", numero)
    prohibidos = ["576028231111", "6028231111", "57602823111", "602823111"]
    return any(p in limpio for p in prohibidos)


def resolve_caller_phone(
    caller_number: Optional[str] = None,
    sip_headers: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], str]:
    """
    Resuelve el teléfono real del cliente desde número directo o headers SIP.
    """
    if caller_number:
        cleaned = limpiar_numero(caller_number)
        if cleaned and not es_numero_troncal_o_empresa(cleaned):
            return cleaned, "caller_number"

    headers = sip_headers or {}
    priority_keys = [
        "X-Original-Caller",
        "X-Original-ANI",
        "P-Asserted-Identity",
        "Remote-Party-ID",
        "Diversion",
        "From",
        "Caller",
    ]
    for key in priority_keys:
        val = headers.get(key) or headers.get(f"SipHeader_{key}")
        cleaned = limpiar_numero(str(val) if val else None)
        if cleaned and not es_numero_troncal_o_empresa(cleaned):
            return cleaned, key

    return None, "not_found"
