import pusher
import logging
import os
from core.config import settings

logger = logging.getLogger("lyra.pusher")

# Las credenciales se obtienen preferiblemente de variables de entorno, 
# pero usamos los valores del main.py como fallback para consistencia.
PUSHER_APP_ID = os.getenv("PUSHER_APP_ID", "2140848")
PUSHER_KEY = os.getenv("PUSHER_KEY", "ae9cbea5e49a86a070bf")
PUSHER_SECRET = os.getenv("PUSHER_SECRET", "7e0c9101c93db9f39332")
PUSHER_CLUSTER = os.getenv("PUSHER_CLUSTER", "us2")

_pusher_client = None

def get_pusher_client():
    global _pusher_client
    if _pusher_client is None:
        try:
            _pusher_client = pusher.Pusher(
                app_id=PUSHER_APP_ID,
                key=PUSHER_KEY,
                secret=PUSHER_SECRET,
                cluster=PUSHER_CLUSTER,
                ssl=True
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
