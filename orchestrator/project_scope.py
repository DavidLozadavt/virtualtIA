"""
orchestrator/project_scope.py — Comprobación de que una respuesta pertenece al proyecto pedido.

Cada respuesta lleva en `final_data["_source_project"]` el proyecto que la
produjo (interceptor del proyecto o bucle del agente). ChatService la contrasta
con el de la petición antes de entregarla: si no coincide, la respuesta se
descarta, se reemplaza por una aclaración del propio proyecto y queda registrada.

`CONTAMINATION_EVENTS` cuenta esos casos desde el arranque; con el despacho por
registro debería quedar siempre en cero, y las pruebas lo verifican.
"""

import logging

from orchestrator.interceptors.manager import SOURCE_KEY

logger = logging.getLogger("lyra.project_scope")

CONTAMINATION_EVENTS = 0

_SAFE_REPLY = {
    "schoolsena": "No logré resolver eso. Puedo ayudarte con tus clases, actividades y notas.",
    "nexiservice": "No logré resolver eso. Puedo buscar negocios y servicios, mostrártelos en el mapa o ayudarte a reservar.",
}


def enforce(project_id: str, final_data: dict, reply: str) -> str:
    """Devuelve la respuesta si es del proyecto pedido; si no, una del proyecto correcto."""
    global CONTAMINATION_EVENTS
    source = (final_data or {}).get(SOURCE_KEY)
    if source in (None, project_id):
        return reply
    CONTAMINATION_EVENTS += 1
    logger.error("CONTAMINACIÓN: respuesta de '%s' en una petición de '%s'; se descarta.", source, project_id)
    for k in ("properties", "voice_action", "voice_action_payload"):
        final_data.pop(k, None)
    final_data[SOURCE_KEY] = project_id
    return _SAFE_REPLY.get(project_id, "No logré resolver eso. ¿Me lo dices de otra forma?")
