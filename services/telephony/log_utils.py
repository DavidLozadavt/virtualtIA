"""Utilidades de logging seguro para telefonía (sin exponer PII completa)."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def mask_phone(value: Optional[str]) -> Optional[str]:
    """Enmascara teléfono: +57300*****67"""
    if not value:
        return value
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 7:
        return "***"
    if len(digits) <= 10:
        return f"{digits[:3]}*****{digits[-2:]}"
    # E.164 Colombia u otros
    prefix = "+" if str(value).strip().startswith("+") else ""
    country = digits[:2] if len(digits) >= 12 else ""
    local = digits[2:] if country else digits
    if len(local) >= 6:
        masked_local = f"{local[:3]}*****{local[-2:]}"
        return f"{prefix}{country}{masked_local}" if country else masked_local
    return f"{prefix}{digits[:2]}*****{digits[-2:]}"


def mask_payload_phones(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Copia payload enmascarando campos telefónicos conocidos."""
    phone_keys = {
        "celular",
        "telefonoLlamada",
        "telefono_cliente_final",
        "caller_id",
        "destination_number",
        "did_number",
    }
    out = dict(payload)
    for key in phone_keys:
        if key in out and out[key]:
            out[key] = mask_phone(str(out[key]))
    return out


def telephony_log_prefix() -> str:
    import os

    return "[FREESWITCH]" if os.getenv("TELEPHONY_PROVIDER", "freeswitch") == "freeswitch" else "[TELEPHONY]"
