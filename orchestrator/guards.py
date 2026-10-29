"""
orchestrator/guards.py — Filtros deterministas previos a la comprensión.

Atiende, sin modelo y sin herramientas, los mensajes que intentan sacar al
asistente de su papel: pedir sus instrucciones internas o declararse otro rol
("ahora eres administrador"). Responder esto con el modelo cuesta tokens y deja
la decisión en manos de quien escribió el ataque; responderlo aquí es gratis y
siempre igual.

Lo que NO se filtra: cambiar de tema dentro del uso normal ("olvida eso, mejor
busca restaurantes") es una corrección legítima y sigue su curso.
"""

import re
import unicodedata
from typing import Optional


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]", " ", text)).strip()


# Pedir el contenido interno del asistente.
_REVEAL_VERB = r"(muestra|muestrame|ensena|ensename|dime|dame|revela|revelame|imprime|copia|repite|escribe|cual es|cuales son|pasame|comparte)"
_INTERNALS = (
    r"(prompt|system prompt|mensaje de sistema|mensaje del sistema|instrucciones (del sistema|internas|ocultas|iniciales|originales)"
    r"|tus instrucciones|tus reglas|reglas internas|configuracion interna|tu configuracion|api key|clave de api|token de acceso)"
)
_REVEAL_RE = re.compile(rf"\b{_REVEAL_VERB}\b.*\b{_INTERNALS}\b|\b{_INTERNALS}\b.*\b(completo|completa|textual|exacto)\b")

# Anular las reglas en bloque ("ignora tus instrucciones").
_OVERRIDE_RE = re.compile(
    r"\b(ignora|olvida|omite|desobedece|salta(te)?)\b\s+(todas\s+)?(tus|las|cualquier)\s+"
    r"(instrucciones|reglas|restricciones|indicaciones)"
)

# Autoasignarse un rol con más permisos.
_ROLE_CLAIM_RE = re.compile(
    r"\b(ahora eres|eres un|eres el|actua como|actuar como|modo)\s+(un\s+|el\s+)?"
    r"(administrador|admin|desarrollador|developer|root|superusuario|coordinador|dios|dan)\b"
)
# "Soy el administrador de mi negocio" es una frase normal en NexiService: sólo
# se filtra cuando se le dice al asistente qué es él.


def check_message(text: str) -> Optional[dict]:
    """
    Devuelve {"reason", "reply"} si el mensaje debe resolverse aquí, o None.
    """
    t = _fold(text)
    if not t:
        return None
    if _REVEAL_RE.search(t):
        return {
            "reason": "reveal_internals",
            "reply": "Eso es configuración interna y no la comparto. Puedo ayudarte con lo que ofrece la plataforma: dime qué necesitas.",
        }
    if _OVERRIDE_RE.search(t) or _ROLE_CLAIM_RE.search(t):
        return {
            "reason": "role_override",
            "reply": "No puedo cambiar mis reglas ni tus permisos desde el chat: respondo según tu sesión real. ¿En qué te ayudo dentro de lo que tienes habilitado?",
        }
    return None
