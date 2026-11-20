"""
core/pusher.py — Eventos en tiempo real (Pusher).

Las credenciales salen SOLO del entorno (PUSHER_APP_ID, PUSHER_KEY,
PUSHER_SECRET, PUSHER_CLUSTER). No hay valores por defecto en el código: antes
el secret de la app de producción venía escrito aquí, de modo que cualquier
Lyra local sin esas variables emitía sus eventos (saludo al arrancar, citas
creadas, cambios de personalidad) a la app de producción.

Sin credenciales, Pusher queda apagado: `get_pusher_client()` devuelve None y
`trigger_pusher_event()` no hace nada. Los llamadores ya contemplan ese caso.
Para un entorno local con tiempo real, usar una app de Pusher propia de
desarrollo, nunca la de producción.
"""

import logging
import os

import pusher

logger = logging.getLogger("lyra.pusher")

PUSHER_APP_ID = os.getenv("PUSHER_APP_ID", "")
PUSHER_KEY = os.getenv("PUSHER_KEY", "")
PUSHER_SECRET = os.getenv("PUSHER_SECRET", "")
PUSHER_CLUSTER = os.getenv("PUSHER_CLUSTER", "us2")

_pusher_client = None
_warned = False


def is_configured() -> bool:
    return bool(PUSHER_APP_ID and PUSHER_KEY and PUSHER_SECRET)


def get_pusher_client():
    global _pusher_client, _warned
    if not is_configured():
        if not _warned:
            logger.info("[Pusher] Sin credenciales en el entorno: los eventos en tiempo real quedan apagados.")
            _warned = True
        return None
    if _pusher_client is None:
        try:
            _pusher_client = pusher.Pusher(
                app_id=PUSHER_APP_ID,
                key=PUSHER_KEY,
                secret=PUSHER_SECRET,
                cluster=PUSHER_CLUSTER,
                ssl=True,
            )
            logger.info(f"[Pusher] Cliente inicializado (Cluster: {PUSHER_CLUSTER})")
        except Exception as e:
            logger.error(f"[Pusher] Error al inicializar cliente: {e}")
            return None
    return _pusher_client


def trigger_pusher_event(channel: str, event: str, data: dict):
    client = get_pusher_client()
    if client:
        try:
            client.trigger(channel, event, data)
            logger.info(f"[Pusher] Evento '{event}' emitido en canal '{channel}'")
            return True
        except Exception as e:
            logger.error(f"[Pusher] Error al emitir evento: {e}")
    return False
