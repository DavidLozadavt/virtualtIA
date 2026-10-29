"""
core/auth_claims.py — Lectura de claims del JWT que reenvía el front.

Lyra no conoce el secreto con que se firma el token, así que no lo valida: eso
lo hace el backend en cada consulta de datos. Leer los claims sirve para decidir
qué ofrecer en el chat (por ejemplo, no proponer la nómina a un aprendiz) sin
creer en el campo `role` que manda el navegador, que cualquiera puede cambiar.
Un token con claims falsos no obtiene datos: el backend lo rechaza.
"""

import base64
import json
from typing import Optional


def decode_unverified(auth_header: Optional[str]) -> dict:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return {}
    token = auth_header.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def roles_from_token(auth_header: Optional[str]) -> list[str]:
    roles = decode_unverified(auth_header).get("roles") or []
    if isinstance(roles, dict):
        roles = list(roles.values())
    return [str(r) for r in roles if r][:20] if isinstance(roles, list) else []
