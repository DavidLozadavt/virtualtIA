import asyncio
import time
import httpx
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from core.logger import setup_logger
from services.twilio.constants import _SPEECH_CORRECTIONS, POPAYAN_PLACES, _TWILIO_HINTS
from services.twilio.twiml_utils import build_gather_response, build_say_hangup
from services.twilio.speech_processor import SpeechProcessor

logger = setup_logger("lyra.twilio.service")

# ── Constantes de estado ──────────────────────────────────────────────────────

class CallState:
    AWAITING_PICKUP    = "awaiting_pickup"
    CONFIRMING_ORIGIN  = "confirming_origin"
    AWAITING_DEST      = "awaiting_dest"
    COMPLETED          = "completed"
    FAILED             = "failed"


# ── Sesión ────────────────────────────────────────────────────────────────────

@dataclass
class CallSession:
    """
    Estado de una llamada activa.
    El lock se inicializa en __post_init__ para que el dataclass
    sea seguro con asyncio (los Lock no son copiables/serializables).
    """
    call_sid:      str
    state:         str            = CallState.AWAITING_PICKUP
    pickup:        Optional[str]  = None
    pickup_barrio: Optional[str]  = None
    destination:   Optional[str]  = None
    attempts:      int            = 0
    silence_count: int            = 0
    last_message:  str            = ""
    updated_at:    float          = field(default_factory=time.time)

    # No en el constructor — se crea post-init
    lock: asyncio.Lock = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.lock = asyncio.Lock()

    def touch(self) -> None:
        self.updated_at = time.time()


# ── Servicio ──────────────────────────────────────────────────────────────────

class TwilioService:
    def __init__(self, db, config, llm_client):
        self.db     = db
        self.config = config
        self.speech = SpeechProcessor(
            llm_client=llm_client,
            model=config.OPENAI_MODEL,
            corrections=_SPEECH_CORRECTIONS,
            places=POPAYAN_PLACES,
        )
        # TODO: migrar a Redis en producción con múltiples workers.
        self._sessions:     Dict[str, CallSession] = {}
        self._session_lock: asyncio.Lock           = asyncio.Lock()

    # ── Propiedad pública (health check) ─────────────────────────────────────

    @property
    def session_count(self) -> int:
        """Número de sesiones activas."""
        return len(self._sessions)

    # ── Gestión de sesiones ───────────────────────────────────────────────────

    async def _get_session(self, call_sid: str) -> CallSession:
        async with self._session_lock:
            if call_sid not in self._sessions:
                self._sessions[call_sid] = CallSession(call_sid=call_sid)
            session = self._sessions[call_sid]
            session.touch()
            return session

    async def end_call(self, call_sid: str) -> None:
        """Limpia el estado de sesión al finalizar la llamada."""
        async with self._session_lock:
            self._sessions.pop(call_sid, None)

    # ── Webhook handlers ──────────────────────────────────────────────────────

    async def handle_incoming_call(self, call_sid: str, action_url: str) -> str:
        """TwiML de bienvenida para llamada entrante."""
        session = await self._get_session(call_sid)
        async with session.lock:
            session.state = CallState.AWAITING_PICKUP
            msg = "¿Desde dónde solicitas el servicio de taxi en Popayán?"
            session.last_message = msg
            return build_gather_response(
                message=msg,
                action_url=action_url,
                hints=_TWILIO_HINTS,
                voice=self.config.TWILIO_VOICE,
                speech_timeout=self.config.TWILIO_SPEECH_TIMEOUT,
                gather_timeout=self.config.TWILIO_GATHER_TIMEOUT,
            )

    async def process_speech(
        self,
        call_sid:      str,
        speech_result: str,
        action_url:    str,
        caller_id:     str,
    ) -> str:
        """Procesa el input de voz y retorna el siguiente paso TwiML."""
        session = await self._get_session(call_sid)
        async with session.lock:
            text = speech_result.strip()

            # ── Silencio ──────────────────────────────────────────────────────
            if not text:
                return await self._handle_silence(session, action_url, call_sid)

            session.silence_count = 0

            # ── Repetir último mensaje ────────────────────────────────────────
            if self.speech.is_repeat_request(text):
                msg = session.last_message or "Dime tu ubicación."
                return build_gather_response(msg, action_url, _TWILIO_HINTS, self.config.TWILIO_VOICE)

            # ── Máquina de estados ────────────────────────────────────────────
            match session.state:
                case CallState.AWAITING_PICKUP:
                    return await self._handle_awaiting_pickup(session, text, action_url)
                case CallState.AWAITING_DEST:
                    return await self._handle_awaiting_dest(session, text, action_url, caller_id, call_sid)
                case _:
                    logger.warning(f"Estado inesperado {session.state!r} en call {call_sid}")
                    return build_say_hangup("Gracias por llamar.", voice=self.config.TWILIO_VOICE)

    # ── Handlers de estado ────────────────────────────────────────────────────

    async def _handle_silence(
        self, session: CallSession, action_url: str, call_sid: str
    ) -> str:
        session.silence_count += 1
        if session.silence_count >= self.config.MAX_SILENCE_BEFORE_HANGUP:
            logger.info(f"Call {call_sid} finalizada por silencio.")
            await self.end_call(call_sid)
            return build_say_hangup(
                "Gracias por llamarnos. Hasta luego.",
                voice=self.config.TWILIO_VOICE,
            )
        msg = session.last_message or "No te escuché bien. ¿Dónde te recogemos?"
        return build_gather_response(msg, action_url, _TWILIO_HINTS, self.config.TWILIO_VOICE)

    async def _handle_awaiting_pickup(
        self, session: CallSession, text: str, action_url: str
    ) -> str:
        pickup, hint = await self.speech.extract_address(text, "pickup")
        if not pickup:
            session.last_message = hint
            return build_gather_response(hint, action_url, _TWILIO_HINTS, self.config.TWILIO_VOICE)

        session.pickup = pickup
        session.state  = CallState.AWAITING_DEST
        msg = (
            f"Listo, te recogemos en {pickup}. "
            "¿A dónde vas? O si prefieres dile al conductor, solo dime no."
        )
        session.last_message = msg
        return build_gather_response(msg, action_url, _TWILIO_HINTS, self.config.TWILIO_VOICE)

    async def _handle_awaiting_dest(
        self,
        session:    CallSession,
        text:       str,
        action_url: str,
        caller_id:  str,
        call_sid:   str,
    ) -> str:
        # Corrección de origen
        if self.speech.is_correction_request(text):
            session.state  = CallState.AWAITING_PICKUP
            session.pickup = None
            msg = "Corrijamos. ¿Dónde te recogemos?"
            session.last_message = msg
            return build_gather_response(msg, action_url, _TWILIO_HINTS, self.config.TWILIO_VOICE)

        # Sin destino
        if self.speech.parse_si_no(text) is False:
            return await self._close_call(session, call_sid, caller_id, destination=None)

        # Extraer destino
        dest, hint = await self.speech.extract_address(text, "destination")
        if not dest:
            session.last_message = hint
            return build_gather_response(hint, action_url, _TWILIO_HINTS, self.config.TWILIO_VOICE)

        return await self._close_call(session, call_sid, caller_id, destination=dest)

    async def _close_call(
        self,
        session:     CallSession,
        call_sid:    str,
        caller_id:   str,
        destination: Optional[str],
    ) -> str:
        """Crea el servicio, limpia la sesión y retorna TwiML de cierre."""
        ok, closing = await self._create_service(caller_id, session.pickup, destination)
        session.state       = CallState.COMPLETED if ok else CallState.FAILED
        session.destination = destination
        await self.end_call(call_sid)
        return build_say_hangup(closing, voice=self.config.TWILIO_VOICE)

    # ── Backend IntelliTaxi ───────────────────────────────────────────────────

    async def _create_service(
        self,
        caller_id:   str,
        origin:      Optional[str],
        destination: Optional[str],
    ) -> Tuple[bool, str]:
        """Registra el servicio en el backend de IntelliTaxi."""
        from tools.intellitaxi import INTELLITAXI_API, TIMEOUT   # constantes, no lógica

        payload = {
            "telefono": caller_id,
            "origen":   origin,
            "destino":  destination,
            "canal":    "voz",
        }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    f"{INTELLITAXI_API}/servicios",
                    json=payload,
                )
            data = resp.json()

            if resp.status_code in (200, 201):
                return True,  data.get("mensaje", "Tu servicio ha sido solicitado exitosamente.")
            
            logger.warning(f"IntelliTaxi [{resp.status_code}]: {data}")
            return False, data.get("mensaje", "No pudimos crear tu servicio en este momento.")

        except httpx.TimeoutException:
            logger.error("Timeout al contactar IntelliTaxi.")
            return False, "El sistema está tardando. Intenta en un momento."
        except Exception as exc:
            logger.error(f"Error IntelliTaxi: {exc}")
            return False, "Lo sentimos, hay un problema técnico. Intenta más tarde."