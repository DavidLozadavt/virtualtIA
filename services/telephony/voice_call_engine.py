"""
Motor conversacional agnóstico del canal telefónico.

No importa Twilio ni FreeSWITCH. Recibe texto reconocido + sesión;
devuelve qué decir y qué acción ejecutar.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import httpx

from core.address_utils import (
    _is_correction_request,
    _is_repeat_request,
    _parse_si_no,
    _try_local_match,
    looks_like_place,
    normalize_address,
    normalize_colombian_address,
    reattach_address_details,
)
from core.conversation_repair import get_progressive_retry_message
from core.geocoder_service import run_pipeline
from core.geo_types import ResolutionStatus
from core.location_match import decide, is_filler, resolve_location_entity, Decision
from core.stt_enhancer import fuzzy_match_location, resolve_human_reference, strip_accents
from services.telephony.backend_client import TelephonyBackendClient
from services.telephony.phone_utils import es_numero_troncal_o_empresa
from services.telephony.session_store import (
    CallSession,
    STATE_CONFIRMING_ORIGIN,
    STATE_CREATING_SERVICE,
    STATE_FINISHED,
    STATE_WAITING_GEO_CONTEXT,
    STATE_WAITING_ORIGIN,
)

logger = logging.getLogger("lyra.telephony.engine")

ASK_DESTINATION = False
MAX_SILENCE = 3

GREETING = (
    "Soy Lyra, tu asistente de TaxBelalcazar. "
    "Cuéntame, ¿en dónde te recogemos hoy?"
)

# Barrios cuyo NOMBRE geocodifica mal o choca con otro homónimo: se fuerza la
# dirección correcta (clave = canónico normalizado, sin tildes). La creación del
# servicio geocodifica esta dirección, no el nombre del barrio.
_ORIGIN_ADDRESS_OVERRIDES = {
    "la paz": "Cra. 4 #70AN-09, Popayán, Cauca",
}

DTMF_BARRIO_MAP = {
    "1": "Pubenza",
    "2": "Centro",
    "3": "Campanario",
    "4": "Los Sauces",
    "5": "Yanaconas",
    "6": "Valle del Ortigal",
    "7": "María Oriente",
}


class VoiceAction(str, Enum):
    LISTEN = "listen"
    HANGUP = "hangup"
    CREATE_SERVICE = "create_service"


@dataclass
class VoiceTurnResult:
    speak_text: str
    action: VoiceAction = VoiceAction.LISTEN
    short_answer: bool = False
    session: Optional[CallSession] = None
    backend_ok: Optional[bool] = None


class VoiceCallEngine:
    """Procesa turnos de conversación telefónica."""

    def __init__(self, backend: Optional[TelephonyBackendClient] = None):
        self.backend = backend or TelephonyBackendClient()

    def handle_inbound(self, session: CallSession) -> VoiceTurnResult:
        session.state = STATE_WAITING_ORIGIN
        session.last_message = GREETING
        return VoiceTurnResult(speak_text=GREETING, action=VoiceAction.LISTEN, session=session)

    async def process_turn(
        self,
        session: CallSession,
        *,
        user_text: str = "",
        confidence: float = 0.0,
        digits: str = "",
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> VoiceTurnResult:
        call_uuid = session.call_uuid
        text = (user_text or "").strip()

        logger.info(
            "[engine] call_uuid=%s state=%s text=%r digits=%r conf=%.2f",
            call_uuid,
            session.state,
            text[:100],
            digits,
            confidence,
        )

        # ── Terminales (servicio ya creado o llamada finalizada) ──
        if session.service_created or session.state == STATE_FINISHED:
            closing = (
                session.last_message
                if session.service_created and session.last_message
                else "¡Gracias por llamar! ¡Que te vaya bien!"
            )
            session.state = STATE_FINISHED
            return VoiceTurnResult(
                speak_text=closing,
                action=VoiceAction.HANGUP,
                session=session,
            )

        # ── Crear servicio en backend ──
        if session.state == STATE_CREATING_SERVICE:
            return await self._create_service_turn(session, http_client=http_client)

        # ── DTMF ──
        if digits:
            session.silence_count = 0
            session.retry_count = 0
            canonical = DTMF_BARRIO_MAP.get(digits)
            if canonical:
                session.origen_text = canonical
                session.origen_barrio = None
                session.state = STATE_CONFIRMING_ORIGIN
                msg = f"Perfecto, {canonical}. ¿Te recogemos ahí? Di sí para confirmar."
                session.last_message = msg
                return VoiceTurnResult(
                    speak_text=msg, action=VoiceAction.LISTEN, short_answer=True, session=session
                )
            session.state = STATE_WAITING_ORIGIN
            msg = "Listo. Dime el nombre del barrio o la dirección donde te recogemos."
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        # ── Repetir ──
        if text and _is_repeat_request(text):
            replay = session.last_message or "¿En qué parte de Popayán te recogemos?"
            return VoiceTurnResult(speak_text=replay, action=VoiceAction.LISTEN, session=session)

        # ── Saludo sin dirección ──
        if self._is_greeting_only(text):
            msg = "¡Hola! Con mucho gusto te ayudo. Cuéntame, ¿en dónde te recogemos?"
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        # ── Silencio ──
        if not text:
            session.silence_count += 1
            if session.silence_count >= MAX_SILENCE:
                return VoiceTurnResult(
                    speak_text="No te escucho. Llámanos cuando puedas. ¡Hasta luego!",
                    action=VoiceAction.HANGUP,
                    session=session,
                )
            msgs = {
                (STATE_WAITING_ORIGIN, 1): "¿Sigues ahí? Dime dónde te recojo.",
                (STATE_WAITING_ORIGIN, 2): "¿Dónde estás en Popayán?",
                (STATE_CONFIRMING_ORIGIN, 1): (
                    f"¿Confirmas {session.origen_barrio or session.origen_text or 'esa zona'}? Di sí o no."
                ),
            }
            msg = msgs.get(
                (session.state, min(session.silence_count, 2)),
                "¿Me escuchas? Háblame.",
            )
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        session.silence_count = 0

        # ── waiting_origin ──
        if session.state == STATE_WAITING_ORIGIN:
            return await self._handle_waiting_origin(session, text, confidence)

        # ── waiting_geo_context ──
        if session.state == STATE_WAITING_GEO_CONTEXT:
            return await self._handle_geo_context(session, text)

        # ── confirming_origin ──
        if session.state == STATE_CONFIRMING_ORIGIN:
            return await self._handle_confirming_origin(session, text, confidence)

        msg = "¿En qué puedo ayudarte?"
        session.last_message = msg
        return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

    async def _create_service_turn(
        self,
        session: CallSession,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> VoiceTurnResult:
        celular = session.caller_phone
        if celular and es_numero_troncal_o_empresa(celular):
            logger.warning(
                "[engine] blocked trunk as customer call_uuid=%s phone=%s",
                session.call_uuid,
                celular,
            )
            celular = None

        ok, msg = await self.backend.create_service_from_geocoded(
            celular=celular,
            origen=session.origen_text or "",
            destino=session.destino_text,
            call_uuid=session.call_uuid,
            use_freeswitch_channel=True,
            http_client=http_client,
            origen_barrio=session.origen_barrio,
        )

        if not ok:
            session.state = STATE_WAITING_ORIGIN
            session.origen_text = None
            session.origen_barrio = None
            session.last_message = msg
            return VoiceTurnResult(
                speak_text=msg,
                action=VoiceAction.LISTEN,
                short_answer=True,
                session=session,
                backend_ok=False,
            )

        session.service_created = True
        session.state = STATE_FINISHED
        session.last_message = msg
        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.HANGUP,
            session=session,
            backend_ok=True,
        )

    async def _handle_waiting_origin(
        self,
        session: CallSession,
        text: str,
        confidence: float,
    ) -> VoiceTurnResult:
        origen = None
        trusted = False

        # Respuesta a una desambiguación pendiente (ej. "¿La Paz o La Paz Sur?"):
        # resolver acotado a las sedes ofrecidas. Sin esto el motor solo PREGUNTA
        # pero nunca consume la respuesta (mismo patrón que twilio.py:1643).
        if session.pending_disambiguation:
            pd = session.pending_disambiguation
            m_dis = resolve_location_entity(text, scope=pd.get("candidates"))
            if decide(m_dis) == Decision.ACCEPT and m_dis.canonical:
                session.pending_disambiguation = None
                origen = m_dis.canonical
                trusted = True
                logger.info("[engine] disambiguated -> %r", origen)
            else:
                msg = pd.get("question") or "¿Cuál de las opciones?"
                session.last_message = msg
                return VoiceTurnResult(
                    speak_text=msg, action=VoiceAction.LISTEN, short_answer=True, session=session
                )
        else:
            m = resolve_location_entity(text)
            d = decide(m)
            if d == Decision.AMBIGUOUS and m.canonical:
                session.pending_disambiguation = {
                    "candidates": list(m.disambiguation_candidates),
                    "question": _disambiguation_question(m.disambiguation_candidates),
                }
                msg = session.pending_disambiguation["question"]
                session.last_message = msg
                return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

            if d in (Decision.ACCEPT, Decision.CONFIRM) and m.canonical:
                origen = m.canonical
                trusted = True
            elif is_filler(text):
                session.retry_count += 1
                # Reintentos consecutivos (>=2): simplificar y pedir por partes
                # (barrio primero) en vez de repetir "no entendí".
                msg = get_progressive_retry_message(session.retry_count) or (
                    "No logré identificar la ubicación. ¿Podrías repetirla?"
                )
                session.last_message = msg
                return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)
            else:
                origen = await self._extract_origin_llm(text)
                if not origen:
                    origen = text

        # Barrios con dirección fija (nombre geocodifica mal o es ambiguo).
        if origen:
            forced = _ORIGIN_ADDRESS_OVERRIDES.get(strip_accents(origen.lower().strip()))
            if forced:
                logger.info("[engine] origin address override %r -> %r", origen, forced)
                origen = forced
                trusted = True

        if not trusted and not looks_like_place(origen) and not looks_like_place(text):
            session.retry_count += 1
            # Reintentos consecutivos (>=2): pedir la dirección por partes.
            msg = get_progressive_retry_message(session.retry_count) or (
                "Disculpa, no te entendí. ¿Me puedes repetir la dirección?"
            )
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        session.retry_count = 0
        # Red de seguridad: si la extracción (LLM / catálogo / referencia humana)
        # recortó el número de casa o el landmark que el usuario sí dijo, los
        # recuperamos del texto original antes de geocodificar (bug item 7).
        if origen:
            origen = reattach_address_details(text, origen)
        if origen:
            col_norm = normalize_colombian_address(origen)
            if col_norm and len(col_norm) >= 3:
                origen = col_norm
            else:
                norm = normalize_address(origen)
                if norm and len(norm) > len(origen) * 0.4:
                    origen = norm

        session.origen_text = origen
        is_street = bool(
            re.search(r"(?:calle|carrera|cl|cra|kr|kra)\s*\.?\s*\d+", (origen or "").lower())
        )

        if is_street:
            try:
                geo_result = await run_pipeline(origen, attempt=1)
                if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
                    barrio = geo_result.selected.neighborhood
                    if barrio:
                        session.origen_barrio = barrio
                        session.state = STATE_CONFIRMING_ORIGIN
                        msg = f"El punto de recogida es {origen}, barrio {barrio}."
                        session.last_message = msg
                        return VoiceTurnResult(
                            speak_text=msg,
                            action=VoiceAction.LISTEN,
                            short_answer=True,
                            session=session,
                        )
                elif geo_result.status in (
                    ResolutionStatus.CONTEXT_GATHERING,
                    ResolutionStatus.NEEDS_DISAMBIGUATION,
                ):
                    session.geo_original_query = origen
                    session.geo_attempt = 1
                    session.state = STATE_WAITING_GEO_CONTEXT
                    msg = geo_result.disambiguation_question or "¿En qué barrio o sector queda?"
                    session.last_message = msg
                    return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)
            except Exception as exc:
                logger.warning("[engine] geocode pipeline error: %s", exc)

        session.state = STATE_CONFIRMING_ORIGIN
        msg = f"El punto de recogida es {origen}."
        session.last_message = msg
        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.LISTEN,
            short_answer=True,
            session=session,
        )

    async def _handle_geo_context(self, session: CallSession, text: str) -> VoiceTurnResult:
        orig_q = session.geo_original_query or session.origen_text or ""
        # Sin pending persistido: reintentar pipeline con contexto del usuario
        enriched = f"{orig_q}, {text}".strip(", ")
        geo_result = await run_pipeline(enriched, attempt=session.geo_attempt + 1)
        session.geo_attempt = geo_result.attempt
        if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
            barrio = geo_result.selected.neighborhood
            if barrio:
                session.origen_barrio = barrio
            session.origen_text = orig_q
            session.state = STATE_CONFIRMING_ORIGIN
            barrio_str = f", barrio {barrio}" if barrio else ""
            msg = f"El punto de recogida es {orig_q}{barrio_str}."
            session.last_message = msg
            return VoiceTurnResult(
                speak_text=msg,
                action=VoiceAction.LISTEN,
                short_answer=True,
                session=session,
            )

        if geo_result.status == ResolutionStatus.CONTEXT_GATHERING:
            msg = geo_result.disambiguation_question or "¿En qué barrio o referencia cercana queda?"
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        if geo_result.status == ResolutionStatus.FAILED:
            # Reintentos agotados y el geocoder sigue sin precisión de número de
            # casa. Fallback seguro y EXPLÍCITO: no aceptar el resultado de baja
            # precisión, no confirmar una dirección sin coordenadas reales (la
            # creación del servicio igualmente la rechazaría). Este canal no tiene
            # transferencia a humano, así que reiniciamos la captura del origen
            # pidiendo explícitamente número de casa + referencia.
            logger.warning(
                "[engine] geo context exhausted (still low precision) → safe "
                "fallback, restarting origin capture call_uuid=%s",
                session.call_uuid,
            )
            session.state = STATE_WAITING_ORIGIN
            session.origen_text = None
            session.origen_barrio = None
            session.geo_attempt = 0
            session.geo_original_query = None
            msg = (
                "No logré ubicar la dirección exacta. Intentémoslo de nuevo: "
                "dime la calle con el número de la casa y un punto de "
                "referencia cercano."
            )
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        # NEEDS_DISAMBIGUATION u otro estado no terminal → confirmar texto.
        session.state = STATE_CONFIRMING_ORIGIN
        msg = f"El punto de recogida es {orig_q}."
        session.last_message = msg
        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.LISTEN,
            short_answer=True,
            session=session,
        )

    async def _handle_confirming_origin(
        self,
        session: CallSession,
        text: str,
        confidence: float,
    ) -> VoiceTurnResult:
        is_yes = _parse_si_no(text)

        if is_yes is True:
            if not ASK_DESTINATION:
                session.state = STATE_CREATING_SERVICE
                return VoiceTurnResult(
                    speak_text="Un momento por favor...",
                    action=VoiceAction.CREATE_SERVICE,
                    session=session,
                )
            session.state = STATE_WAITING_ORIGIN  # placeholder if dest enabled later
            msg = f"Listo {session.origen_text}. ¿A dónde vas?"
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        if is_yes is False:
            session.state = STATE_WAITING_ORIGIN
            session.origen_text = None
            session.origen_barrio = None
            msg = "Entendido. ¿Dónde queda exactamente? Puedes darme el barrio o la dirección."
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        # Corrección / restatement
        local = _try_local_match(text)
        if local:
            cur = strip_accents((session.origen_text or "").lower())
            new = strip_accents(local.lower())
            if cur and (cur == new or fuzzy_match_location(new, [cur], threshold=0.80)):
                if not ASK_DESTINATION:
                    session.state = STATE_CREATING_SERVICE
                    return VoiceTurnResult(
                        speak_text="Un momento por favor...",
                        action=VoiceAction.CREATE_SERVICE,
                        session=session,
                    )
            session.origen_text = local
            session.origen_barrio = None
            session.state = STATE_CONFIRMING_ORIGIN
            msg = f"Ah, {local}. ¿Te recogemos ahí? Di sí para confirmar."
            session.last_message = msg
            return VoiceTurnResult(
                speak_text=msg,
                action=VoiceAction.LISTEN,
                short_answer=True,
                session=session,
            )

        # Respuesta ambigua con origen+barrio ya resueltos → confirmación implícita
        # (misma lógica que twilio.py; evita bucle de "¿Me confirmas el barrio?")
        ambiguous_is_place = looks_like_place(text)
        if session.origen_text and session.origen_barrio and not ambiguous_is_place:
            logger.info(
                "[engine] implicit confirm call_uuid=%s text=%r",
                session.call_uuid,
                text[:80],
            )
            if not ASK_DESTINATION:
                session.state = STATE_CREATING_SERVICE
                return VoiceTurnResult(
                    speak_text="Un momento por favor...",
                    action=VoiceAction.CREATE_SERVICE,
                    session=session,
                )

        # Respuesta no parseable en confirmación: pedir repetir la dirección de
        # forma natural (no "¿me confirmas el barrio o la dirección?").
        msg = "Disculpa, no te entendí. ¿Me puedes repetir la dirección?"
        session.last_message = msg
        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.LISTEN,
            short_answer=True,
            session=session,
        )

    async def _extract_origin_llm(self, text: str) -> Optional[str]:
        from core.llm_utils import get_async_openai_client, get_model

        client = get_async_openai_client()
        if not client:
            return text.strip() if len(text.strip()) >= 4 else None

        prompt = (
            "Eres un asistente de taxi en Popayán, Colombia.\n"
            f"El usuario dijo: '{text}'\n"
            "Extrae SOLO el nombre del lugar de origen como texto limpio.\n"
            "Sin JSON, sin explicaciones, solo el nombre.\n"
            "Lugar:"
        )
        try:
            result = await client.chat.completions.create(
                model=get_model(),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60,
                timeout=4.0,
            )
            raw = (result.choices[0].message.content or "").strip()
            city_names = {"popayan", "cauca", "colombia"}
            if not raw or strip_accents(raw.lower()) in city_names:
                return None
            return raw
        except Exception as e:
            logger.error("[engine] LLM extract error: %s", e)
            return None

    @staticmethod
    def _is_greeting_only(text: str) -> bool:
        if not text:
            return False
        words = {
            "hola", "buenas", "buenos", "qhubo", "alo", "aló", "bueno", "diga", "dígame",
        }
        t_words = set(text.lower().strip().rstrip(".,!?").split())
        return bool(t_words & words) and len(t_words) <= 3


def _disambiguation_question(candidates: list) -> str:
    if not candidates:
        return "¿Cuál de las opciones?"
    if len(candidates) == 2:
        return f"¿Te refieres a {candidates[0]} o a {candidates[1]}?"
    opts = ", ".join(candidates[:-1]) + f" o {candidates[-1]}"
    return f"¿Cuál de estas opciones: {opts}?"
