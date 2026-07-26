"""Telephony services — contratos de negocio compartidos del canal de voz.

El motor conversacional vive en services/voice (Lyra Voice V2); aquí quedan
los componentes de negocio que V2 consume sin cambios: sesiones, cliente del
backend IntelliTaxi, utilidades de teléfono, idempotencia, ESL y ffmpeg.
"""

from services.telephony.backend_client import TelephonyBackendClient
from services.telephony.session_store import CallSession, SessionStore, get_session_store

__all__ = [
    "SessionStore",
    "CallSession",
    "get_session_store",
    "TelephonyBackendClient",
]
