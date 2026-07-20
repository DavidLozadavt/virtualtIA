"""Turn Orchestrator (spec §3.4) — estados de negocio del FSM preservados.

Los estados y transiciones de negocio son EXACTAMENTE los de V1:
waiting_origin → confirming_origin / waiting_geo_context → creating_service
→ finished, con las mismas reglas (overrides de dirección, guard de troncal,
handoff de barrio cuando el geocoder agota reintentos, confirmación implícita
acotada, idempotencia, WhatsApp). Lo que cambia es CÓMO se llega a cada
transición: el turno lo dirige el resultado del NLU (spans extraídos) en vez
de heurísticas de primera pasada, y el geocoding puede llegar pre-calentado
por la ejecución especulativa de solo-lectura (nunca crea servicios).

La resolución de ubicaciones sigue siendo de core/location_match y
core/geocoder_service (bucket B, sin cambios).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

from core.address_utils import (
    _is_repeat_request,
    _parse_si_no,
    _try_local_match,
    looks_like_place,
    normalize_address,
    normalize_colombian_address,
    reattach_address_details,
)
from core.co_address_parser import AddressState, parse_co_address
from core.conversation_repair import (
    ConversationMemory,
    get_progressive_retry_message,
    get_repair_message,
)
from core.geo_types import ResolutionStatus
from core.geocoder_service import run_pipeline
from core.location_match import Decision, decide, is_filler, resolve_location_entity
from core.stt_enhancer import (
    fuzzy_match_location,
    strip_accents,
    strip_conversational_prefix,
)
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
from services.voice.nlu import NLUResult

logger = logging.getLogger("lyra.voice.orchestrator")

ASK_DESTINATION = False
MAX_SILENCE = 3

GREETING = (
    "Soy Lyra, tu asistente de Tax Belalcázar. "
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


async def _send_whatsapp_message_async(celular: str, message: str, call_uuid: str) -> None:
    """Envía una plantilla de WhatsApp a través del Telecom Manager de Laravel."""
    from core.config import settings
    url = f"{settings.INTELLITAXI_API_BASE}/admin/telecom/send"
    payload = {
        "company_id": 1,
        "to": celular,
        "message": message,
        "type": "template",
        "template_name": "servicio_creado",
        "template_language": "es"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            logger.info(
                "[orchestrator] WhatsApp template sent call_uuid=%s phone=%s status=%s resp=%s",
                call_uuid,
                celular,
                resp.status_code,
                resp.text[:200]
            )
    except Exception as e:
        logger.error(
            "[orchestrator] Error sending WhatsApp template call_uuid=%s phone=%s err=%s",
            call_uuid,
            celular,
            e
        )


class SpeculativeGeocoder:
    """Ejecución especulativa de solo-lectura del pipeline de geocoding.

    `prewarm()` lanza `run_pipeline` en paralelo apenas el NLU entrega un span
    con confianza razonable sobre un parcial estable; `resolve()` reutiliza el
    resultado si el turno final pidió la misma consulta. Es seguro especular:
    run_pipeline no crea servicios ni toca el backend de despacho. Un
    resultado especulativo JAMÁS crea un servicio por sí mismo — solo acelera
    la rama de confirmación (spec §3.4).
    """

    _TTL_SEC = 120.0
    _MAX_TASKS = 6

    def __init__(self):
        self._tasks: dict[tuple[str, int], tuple[float, asyncio.Task]] = {}

    @staticmethod
    def _key(query: str, attempt: int) -> tuple[str, int]:
        return (strip_accents((query or "").lower().strip()), attempt)

    def _prune(self) -> None:
        now = time.monotonic()
        for key in list(self._tasks):
            created, task = self._tasks[key]
            if now - created > self._TTL_SEC or (task.done() and task.exception()):
                self._tasks.pop(key, None)
        while len(self._tasks) > self._MAX_TASKS:
            _, (_, task) = self._tasks.popitem()
            task.cancel()

    def prewarm(self, query: str, attempt: int = 1) -> None:
        if not (query or "").strip():
            return
        key = self._key(query, attempt)
        if key in self._tasks:
            return
        self._prune()
        task = asyncio.create_task(run_pipeline(query, attempt=attempt))
        self._tasks[key] = (time.monotonic(), task)
        logger.info("[orchestrator] speculative geocode prewarm query=%r", query[:80])

    async def resolve(self, query: str, attempt: int = 1):
        key = self._key(query, attempt)
        entry = self._tasks.pop(key, None)
        if entry is not None:
            _, task = entry
            try:
                result = await task
                logger.info(
                    "[orchestrator] speculative geocode hit query=%r", query[:80]
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # la especulación falló: reintentar en frío
        return await run_pipeline(query, attempt=attempt)


class TurnOrchestrator:
    """Procesa turnos de conversación telefónica (control-loop V2)."""

    def __init__(
        self,
        backend: Optional[TelephonyBackendClient] = None,
        geocoder: Optional[SpeculativeGeocoder] = None,
    ):
        self.backend = backend or TelephonyBackendClient()
        self.geocoder = geocoder or SpeculativeGeocoder()
        self._memories: dict[str, ConversationMemory] = {}

    # ── memoria conversacional por llamada (para reparación con variación) ──

    def _memory(self, session: CallSession) -> ConversationMemory:
        mem = self._memories.get(session.call_uuid)
        if mem is None:
            mem = ConversationMemory(call_sid=session.call_uuid)
            self._memories[session.call_uuid] = mem
            if len(self._memories) > 200:
                self._memories.pop(next(iter(self._memories)))
        return mem

    def forget(self, call_uuid: str) -> None:
        self._memories.pop(call_uuid, None)

    # ── entrada de llamada ──

    def handle_inbound(self, session: CallSession) -> VoiceTurnResult:
        session.state = STATE_WAITING_ORIGIN
        session.last_message = GREETING
        return VoiceTurnResult(speak_text=GREETING, action=VoiceAction.LISTEN, session=session)

    # ── truncado de historial en barge-in (spec §3.6) ──

    def note_partial_delivery(self, session: CallSession, heard_text: str) -> None:
        """El usuario interrumpió: el contexto queda en lo realmente escuchado."""
        heard = (heard_text or "").strip()
        if heard and session.last_message and heard != session.last_message:
            logger.info(
                "[orchestrator] history truncated to heard prefix call_uuid=%s heard=%r",
                session.call_uuid,
                heard[:80],
            )
            session.last_message = heard

    # ── silencio (el runtime detecta la ausencia de turno y llama aquí) ──

    def handle_silence(self, session: CallSession) -> VoiceTurnResult:
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

    # ── generación anticipada: geocoding especulativo sobre parciales ──

    def prewarm_origin(self, session: CallSession, raw_text: str, nlu: NLUResult) -> None:
        """Pre-calienta el pipeline de geocoding con el span de un parcial.

        Espejo de solo-lectura de la derivación de origen de
        `_handle_waiting_origin` (canonical del catálogo → override → reattach
        → normalización), sin mutar la sesión. Si el turno final llega a la
        misma consulta, `SpeculativeGeocoder.resolve` reutiliza el resultado.
        """
        if session.state != STATE_WAITING_ORIGIN or session.pending_disambiguation:
            return
        span = nlu.best_pickup
        if not span or nlu.pickup_confidence < 0.6:
            return
        m = resolve_location_entity(span)
        d = decide(m)
        origen = (
            m.canonical
            if d in (Decision.ACCEPT, Decision.CONFIRM) and m.canonical
            else span
        )
        forced = _ORIGIN_ADDRESS_OVERRIDES.get(strip_accents(origen.lower().strip()))
        if forced:
            origen = forced
        elif not looks_like_place(origen) and not looks_like_place(raw_text):
            return
        origen = reattach_address_details(raw_text, origen) or origen
        parsed = parse_co_address(origen)
        # Estructura de vía inválida → no especular geocoding (spec §5).
        if parsed.state == AddressState.INVALID_ADDRESS_STRUCTURE:
            return
        if parsed.canonical:
            origen = parsed.canonical
        self.geocoder.prewarm(origen, attempt=1)

    # ── turno principal ──

    async def process_turn(
        self,
        session: CallSession,
        *,
        text: str = "",
        nlu: Optional[NLUResult] = None,
        confidence: float = 0.0,
        digits: str = "",
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> VoiceTurnResult:
        call_uuid = session.call_uuid
        text = (text or "").strip()
        if nlu is None:
            from services.voice.nlu import fallback_classify

            nlu = fallback_classify(text)

        logger.info(
            "[orchestrator] call_uuid=%s state=%s intent=%s text=%r digits=%r conf=%.2f",
            call_uuid,
            session.state,
            nlu.intent,
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
        if text and (nlu.intent == "repeat_request" or _is_repeat_request(text)):
            replay = session.last_message or "¿En qué parte de Popayán te recogemos?"
            return VoiceTurnResult(speak_text=replay, action=VoiceAction.LISTEN, session=session)

        # ── Saludo / social puro, sin datos útiles ──
        # Solo se intercepta fuera de la confirmación: en confirming_origin un
        # ack social corto ("listo pues", "muchas gracias") ES una señal de
        # confirmación implícita y la decide el handler del estado (regla V1).
        if (
            text
            and nlu.intent in ("greeting", "chitchat_only")
            and not nlu.best_pickup
            and session.state != STATE_CONFIRMING_ORIGIN
        ):
            if session.state == STATE_WAITING_ORIGIN:
                msg = "¡Hola! Con mucho gusto te ayudo. Cuéntame, ¿en dónde te recogemos?"
            else:
                # A mitad de flujo no se reinicia nada: se retoma la pregunta.
                msg = session.last_message or "¿En dónde te recogemos?"
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        # ── Silencio ──
        if not text:
            return self.handle_silence(session)

        session.silence_count = 0

        # ── waiting_origin ──
        if session.state == STATE_WAITING_ORIGIN:
            return await self._handle_waiting_origin(session, text, nlu, confidence)

        # ── waiting_geo_context ──
        if session.state == STATE_WAITING_GEO_CONTEXT:
            return await self._handle_geo_context(session, text, nlu)

        # ── confirming_origin ──
        if session.state == STATE_CONFIRMING_ORIGIN:
            return await self._handle_confirming_origin(session, text, nlu, confidence)

        msg = "¿En qué puedo ayudarte?"
        session.last_message = msg
        return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

    # ── creación de servicio (bucket A: contrato intacto) ──

    async def _create_service_turn(
        self,
        session: CallSession,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> VoiceTurnResult:
        celular = session.caller_phone
        if celular and es_numero_troncal_o_empresa(celular):
            logger.warning(
                "[orchestrator] blocked trunk as customer call_uuid=%s phone=%s",
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

        if celular:
            msg_whatsapp = (
                "Hola 👋\n\n"
                "Soy tu asistente de Tax Belalcázar.\n\n"
                "Hemos recibido correctamente tu solicitud de servicio.\n\n"
                "Hola soy tu asistente de taxi, de Tax Belalcázar, "
                "nos tomaremos un momento para buscar un movil para atender su servicio, gracias por esperar"
            )
            asyncio.create_task(
                _send_whatsapp_message_async(celular, msg_whatsapp, session.call_uuid)
            )

        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.HANGUP,
            session=session,
            backend_ok=True,
        )

    # ── captura de origen ──

    async def _handle_waiting_origin(
        self,
        session: CallSession,
        text: str,
        nlu: NLUResult,
        confidence: float,
    ) -> VoiceTurnResult:
        origen = None
        trusted = False

        raw_text = text
        clean_text = strip_conversational_prefix(text) or text
        span = nlu.best_pickup

        # Respuesta a una desambiguación pendiente (ej. "¿La Paz o La Paz Sur?"):
        # resolver acotado a las sedes ofrecidas.
        if session.pending_disambiguation:
            pd = session.pending_disambiguation
            m_dis = resolve_location_entity(span or clean_text, scope=pd.get("candidates"))
            if decide(m_dis) == Decision.ACCEPT and m_dis.canonical:
                session.pending_disambiguation = None
                origen = m_dis.canonical
                trusted = True
                logger.info("[orchestrator] disambiguated -> %r", origen)
            else:
                msg = pd.get("question") or "¿Cuál de las opciones?"
                session.last_message = msg
                return VoiceTurnResult(
                    speak_text=msg, action=VoiceAction.LISTEN, short_answer=True, session=session
                )
        else:
            m = resolve_location_entity(span or clean_text)
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
            elif span:
                # El NLU ya extrajo el fragmento útil del turno completo — esto
                # reemplaza al fallback LLM de V1 y corre en cada turno.
                origen = span
            elif nlu.intent in ("confirm_yes", "confirm_no", "unclear") or is_filler(clean_text):
                session.retry_count += 1
                msg = get_progressive_retry_message(session.retry_count) or (
                    get_repair_message(
                        clean_text, confidence, session.state, self._memory(session)
                    )
                )
                session.last_message = msg
                return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)
            else:
                origen = clean_text

        # Barrios con dirección fija (nombre geocodifica mal o es ambiguo).
        if origen:
            forced = _ORIGIN_ADDRESS_OVERRIDES.get(strip_accents(origen.lower().strip()))
            if forced:
                logger.info("[orchestrator] origin address override %r -> %r", origen, forced)
                origen = forced
                trusted = True

        if not trusted and not looks_like_place(origen) and not looks_like_place(text):
            session.retry_count += 1
            msg = get_progressive_retry_message(session.retry_count) or (
                get_repair_message(
                    clean_text, confidence, session.state, self._memory(session)
                )
            )
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        session.retry_count = 0
        # Red de seguridad: si la extracción recortó el número de casa o el
        # landmark que el usuario sí dijo, se recuperan del texto original antes
        # de geocodificar.
        if origen:
            origen = reattach_address_details(raw_text, origen)
        if origen:
            parsed = parse_co_address(origen)
            # Nomenclatura de vía pero estructura inválida → NUNCA se geocodifica;
            # se vuelve a pedir la dirección (spec §5, estado
            # INVALID_ADDRESS_STRUCTURE). Barrios/landmarks/lugares no entran aquí.
            if parsed.state == AddressState.INVALID_ADDRESS_STRUCTURE:
                session.retry_count += 1
                logger.info(
                    "[orchestrator] invalid address structure call_uuid=%s "
                    "reason=%s query=%r → re-ask",
                    session.call_uuid, parsed.invalid_reason, origen,
                )
                msg = get_progressive_retry_message(session.retry_count) or (
                    get_repair_message(
                        clean_text, confidence, session.state, self._memory(session)
                    )
                )
                session.last_message = msg
                return VoiceTurnResult(
                    speak_text=msg, action=VoiceAction.LISTEN, session=session
                )
            if parsed.canonical:
                origen = parsed.canonical

        session.origen_text = origen
        self._memory(session).add_location_mention(origen or "")
        is_street = bool(
            re.search(r"(?:calle|carrera|cl|cra|kr|kra)\s*\.?\s*\d+", (origen or "").lower())
        )

        if is_street:
            try:
                geo_result = await self.geocoder.resolve(origen, attempt=1)
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
                logger.warning("[orchestrator] geocode pipeline error: %s", exc)

        # Nombre propio / landmark sin patrón de calle: también se geocodifica
        # para ejercer el guard _NEVER_AUTOACCEPT en la captura inicial.
        if not is_street and origen and looks_like_place(origen):
            try:
                geo_result = await self.geocoder.resolve(origen, attempt=1)
                if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
                    barrio = geo_result.selected.neighborhood
                    if barrio:
                        session.origen_barrio = barrio
                    session.state = STATE_CONFIRMING_ORIGIN
                    barrio_str = f", barrio {barrio}" if barrio else ""
                    msg = f"¿{origen}{barrio_str}, es correcto?"
                    session.last_message = msg
                    return VoiceTurnResult(
                        speak_text=msg,
                        action=VoiceAction.LISTEN,
                        short_answer=True,
                        session=session,
                    )
                elif (
                    geo_result.status in (
                        ResolutionStatus.CONTEXT_GATHERING,
                        ResolutionStatus.NEEDS_DISAMBIGUATION,
                    )
                    and not trusted
                ):
                    # No degradar shortcuts confiables (catálogo local / override):
                    # solo los NO confiables pasan a pedir barrio/referencia.
                    session.geo_original_query = origen
                    session.geo_attempt = 1
                    session.state = STATE_WAITING_GEO_CONTEXT
                    msg = geo_result.disambiguation_question or "¿En qué barrio o referencia cercana queda?"
                    session.last_message = msg
                    return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)
                # trusted en no-RESOLVED, o FAILED → caer a confirm plano.
            except Exception as exc:
                logger.warning("[orchestrator] landmark geocode pipeline error: %s", exc)

        session.state = STATE_CONFIRMING_ORIGIN
        # Confianza media → confirmación implícita natural en vez de eco seco
        # (spec §3.4: "¿Vale, X es donde te recogemos?" en vez de loop sí/no).
        if span and 0.4 <= nlu.pickup_confidence < 0.75:
            msg = f"Vale, ¿{origen} es donde te recogemos?"
        else:
            msg = f"¿{origen} es correcto?"
        session.last_message = msg
        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.LISTEN,
            short_answer=True,
            session=session,
        )

    # ── contexto geográfico adicional ──

    async def _handle_geo_context(
        self, session: CallSession, text: str, nlu: NLUResult
    ) -> VoiceTurnResult:
        orig_q = session.geo_original_query or session.origen_text or ""
        context_text = nlu.best_pickup or text
        enriched = f"{orig_q}, {context_text}".strip(", ")
        geo_result = await self.geocoder.resolve(enriched, attempt=session.geo_attempt + 1)
        session.geo_attempt = geo_result.attempt
        if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
            barrio = geo_result.selected.neighborhood
            if barrio:
                session.origen_barrio = barrio
            session.origen_text = orig_q
            session.state = STATE_CONFIRMING_ORIGIN
            barrio_str = f", barrio {barrio}" if barrio else ""
            msg = f"¿{orig_q}{barrio_str}, es correcto?"
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
            # Reintentos agotados: NO se reinicia la captura (genera bucle). Se
            # toma el barrio/referencia que el usuario acaba de dar y se crea el
            # servicio con ese barrio: el conductor llama para afinar el punto.
            barrio = (
                session.origen_barrio
                or _try_local_match(context_text)
                or context_text.strip()
                or orig_q
            )
            logger.warning(
                "[orchestrator] geo context exhausted → barrio-only handoff "
                "call_uuid=%s barrio=%r origen=%r",
                session.call_uuid,
                barrio,
                orig_q,
            )
            session.origen_text = orig_q or barrio
            session.origen_barrio = barrio
            session.state = STATE_CREATING_SERVICE
            msg = (
                f"Listo, te ubico en el barrio {barrio}. El conductor te "
                "llamará para afinar el punto exacto. Un momento por favor."
            )
            session.last_message = msg
            return VoiceTurnResult(
                speak_text=msg,
                action=VoiceAction.CREATE_SERVICE,
                short_answer=True,
                session=session,
            )

        # NEEDS_DISAMBIGUATION u otro estado no terminal → confirmar texto.
        session.state = STATE_CONFIRMING_ORIGIN
        msg = f"¿{orig_q} es correcto?"
        session.last_message = msg
        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.LISTEN,
            short_answer=True,
            session=session,
        )

    # ── confirmación de origen ──

    async def _handle_confirming_origin(
        self,
        session: CallSession,
        text: str,
        nlu: NLUResult,
        confidence: float,
    ) -> VoiceTurnResult:
        if nlu.intent == "confirm_yes":
            is_yes: Optional[bool] = True
        elif nlu.intent == "confirm_no":
            is_yes = False
        else:
            is_yes = _parse_si_no(text)

        span = nlu.best_pickup

        if is_yes is True and not span:
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

        if is_yes is False and not span:
            session.state = STATE_WAITING_ORIGIN
            session.origen_text = None
            session.origen_barrio = None
            msg = "Entendido. ¿Dónde queda exactamente? Puedes darme el barrio o la dirección."
            session.last_message = msg
            return VoiceTurnResult(speak_text=msg, action=VoiceAction.LISTEN, session=session)

        # Corrección / restatement — override explícito del slot ya lleno
        # (Dialogue State Tracking estándar, spec §3.4): el usuario cambió de
        # idea o repitió el lugar; no se reinicia la conversación.
        local = _try_local_match(span or text)
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

        if span and nlu.intent in ("correction", "provide_pickup"):
            session.origen_text = span
            session.origen_barrio = None
            session.state = STATE_CONFIRMING_ORIGIN
            msg = f"Ah, {span}. ¿Te recogemos ahí? Di sí para confirmar."
            session.last_message = msg
            return VoiceTurnResult(
                speak_text=msg,
                action=VoiceAction.LISTEN,
                short_answer=True,
                session=session,
            )

        # Respuesta ambigua con origen+barrio ya resueltos → confirmación
        # implícita, SOLO para respuestas cortas (≤3 palabras) que no sean un
        # lugar ni una negación/corrección: un ack coloquial ("de una", "listo
        # pues") sí confirma; una frase larga o una dirección nueva NO.
        ambiguous_is_place = looks_like_place(text)
        token_count = len((text or "").split())
        if (
            session.origen_text
            and session.origen_barrio
            and not ambiguous_is_place
            and 1 <= token_count <= 3
            and nlu.intent not in ("confirm_no", "correction", "provide_pickup", "provide_destination")
        ):
            logger.info(
                "[orchestrator] implicit confirm call_uuid=%s text=%r",
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

        # No parseable: reparación contextual con variación (no un "no te
        # entendí" fijo — rescata ConversationRepair, spec §3.4).
        msg = get_repair_message(
            text, confidence, session.state, self._memory(session)
        )
        session.last_message = msg
        return VoiceTurnResult(
            speak_text=msg,
            action=VoiceAction.LISTEN,
            short_answer=True,
            session=session,
        )


def _disambiguation_question(candidates: list) -> str:
    if not candidates:
        return "¿Cuál de las opciones?"
    if len(candidates) == 2:
        return f"¿Te refieres a {candidates[0]} o a {candidates[1]}?"
    opts = ", ".join(candidates[:-1]) + f" o {candidates[-1]}"
    return f"¿Cuál de estas opciones: {opts}?"
