"""NLU de turno — extracción de spans con Structured Outputs (spec §3.3).

El LLM corre en CADA turno (no como fallback) y extrae únicamente:

    {intent, pickup_span, destination_span, landmark_reference, confidence}

La conformidad con el schema está garantizada por constrained decoding
(`response_format: json_schema` con `strict: true`): el modelo no puede
emitir tokens fuera del schema, y el saludo/muletillas/cortesías se
descartan POR DISEÑO — no existe campo donde ese texto pueda ir.

Frontera estricta LLM↔resolver: el LLM nunca resuelve la dirección. El span
crudo ("Valle del Hortigal") va después a core/location_match y
core/geocoder_service (bucket B, sin cambios).

Degradación: si el LLM no responde dentro de VOICE_NLU_TIMEOUT_SEC (o no hay
credencial), se usa el clasificador determinista local (sí/no, saludo,
repetición, filler) con el texto limpio como span — la llamada nunca muere
por el NLU.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

from core.config import settings

logger = logging.getLogger("lyra.voice.nlu")

INTENTS = (
    "greeting",
    "provide_pickup",
    "provide_destination",
    "confirm_yes",
    "confirm_no",
    "correction",
    "repeat_request",
    "chitchat_only",
    "unclear",
)

NLU_JSON_SCHEMA = {
    "name": "turn_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "intent",
            "pickup_span",
            "destination_span",
            "landmark_reference",
            "confidence",
        ],
        "properties": {
            "intent": {"type": "string", "enum": list(INTENTS)},
            "pickup_span": {"type": ["string", "null"]},
            "destination_span": {"type": ["string", "null"]},
            "landmark_reference": {"type": ["string", "null"]},
            "confidence": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pickup_span", "destination_span"],
                "properties": {
                    "pickup_span": {"type": "number"},
                    "destination_span": {"type": "number"},
                },
            },
        },
    },
}

_SYSTEM_PROMPT = """Eres el módulo de comprensión de Lyra, operadora telefónica de taxis en Popayán, Colombia. Recibes la transcripción de UN turno del usuario y extraes solo la información útil.

Reglas estrictas:
- pickup_span: el fragmento de texto EXACTO donde el usuario dice dónde recogerlo (barrio, dirección, lugar), recortando saludos, muletillas y cortesías. Null si no lo dice.
- destination_span: igual pero para el destino ("voy para..."). Null si no lo dice.
- landmark_reference: referencias indirectas de ubicación ("frente al hospital", "diagonal al Éxito", "por la bomba de gasolina", "donde siempre"). Null si no hay.
- NUNCA inventes, completes ni corrijas direcciones. NUNCA resuelvas la ubicación: solo copia el fragmento dicho.
- Saludos, conectores, cortesías y relleno se ignoran: no hay campo para ellos.
- intent:
  - greeting: solo saluda ("Buenas.", "Hola, ¿cómo estás?").
  - provide_pickup: da el lugar de recogida (aunque venga envuelto en cortesía).
  - provide_destination: da solo el destino.
  - confirm_yes / confirm_no: responde a la pregunta de confirmación del asistente (mira la última pregunta en el contexto). "Hágale", "de una", "listo pues" son confirm_yes; "no, espere" es confirm_no.
  - correction: se corrige o cambia un lugar ya dado ("no, espere, mejor en la carrera quinta").
  - repeat_request: pide que le repitan.
  - chitchat_only: habla social sin datos ("muchas gracias", "si Dios quiere").
  - unclear: nada de lo anterior aplica.
- confidence: 0.0-1.0 por span; 0.0 cuando el span es null.

Ejemplos:
- "Buenas, si mira, estoy aquí en Valle del Hortigal, por favor" → {"intent":"provide_pickup","pickup_span":"Valle del Hortigal","destination_span":null,"landmark_reference":null,"confidence":{"pickup_span":0.9,"destination_span":0.0}}
- "Me recoges por el hospital" → {"intent":"provide_pickup","pickup_span":null,"destination_span":null,"landmark_reference":"por el hospital","confidence":{"pickup_span":0.0,"destination_span":0.0}}
- "No, espere, mejor en la carrera quinta" → {"intent":"correction","pickup_span":"la carrera quinta","destination_span":null,"landmark_reference":null,"confidence":{"pickup_span":0.85,"destination_span":0.0}}
- "Cómo estás" → {"intent":"greeting","pickup_span":null,"destination_span":null,"landmark_reference":null,"confidence":{"pickup_span":0.0,"destination_span":0.0}}"""


@dataclass
class NLUResult:
    intent: str
    pickup_span: Optional[str]
    destination_span: Optional[str]
    landmark_reference: Optional[str]
    pickup_confidence: float
    destination_confidence: float
    source: str  # "llm" | "fallback"

    @property
    def best_pickup(self) -> Optional[str]:
        """Span de recogida preferido: directo, o la referencia indirecta.

        La referencia indirecta también la resuelve el geocoder (bucket B):
        el NLU solo entrega el texto.
        """
        return self.pickup_span or self.landmark_reference


def _clamp(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def parse_nlu_payload(payload: dict) -> NLUResult:
    """Valida/normaliza el JSON del modelo (defensa extra sobre el strict mode)."""
    intent = payload.get("intent")
    if intent not in INTENTS:
        intent = "unclear"

    def _span(key: str) -> Optional[str]:
        val = payload.get(key)
        if isinstance(val, str):
            val = val.strip()
            return val or None
        return None

    conf = payload.get("confidence") or {}
    if not isinstance(conf, dict):
        conf = {}
    return NLUResult(
        intent=intent,
        pickup_span=_span("pickup_span"),
        destination_span=_span("destination_span"),
        landmark_reference=_span("landmark_reference"),
        pickup_confidence=_clamp(conf.get("pickup_span")),
        destination_confidence=_clamp(conf.get("destination_span")),
        source="llm",
    )


def _nlu_api_key() -> str:
    dedicated = (settings.VOICE_NLU_API_KEY or "").strip()
    if dedicated:
        return dedicated
    fallback = (settings.OPENAI_API_KEY or "").strip()
    if fallback and not fallback.startswith("sk-or"):
        return fallback
    return ""


def fallback_classify(text: str) -> NLUResult:
    """Clasificador determinista local cuando el LLM no está disponible."""
    from core.address_utils import _is_repeat_request, _parse_si_no
    from core.location_match import is_filler
    from core.stt_enhancer import strip_conversational_prefix

    t = (text or "").strip()
    if not t:
        return NLUResult("unclear", None, None, None, 0.0, 0.0, "fallback")

    yes_no = _parse_si_no(t)
    if yes_no is True:
        return NLUResult("confirm_yes", None, None, None, 0.0, 0.0, "fallback")
    if yes_no is False:
        return NLUResult("confirm_no", None, None, None, 0.0, 0.0, "fallback")
    if _is_repeat_request(t):
        return NLUResult("repeat_request", None, None, None, 0.0, 0.0, "fallback")
    if _is_greeting_only(t):
        return NLUResult("greeting", None, None, None, 0.0, 0.0, "fallback")
    if is_filler(t):
        return NLUResult("unclear", None, None, None, 0.0, 0.0, "fallback")

    cleaned = strip_conversational_prefix(t) or t
    return NLUResult(
        "provide_pickup", cleaned, None, None, 0.3, 0.0, "fallback"
    )


def _is_greeting_only(text: str) -> bool:
    """Saludo puro sin datos (preservado de V1)."""
    if not text:
        return False
    words = {
        "hola", "buenas", "buenos", "qhubo", "alo", "aló", "bueno", "diga",
        "dígame",
    }
    t_words = set(text.lower().strip().rstrip(".,!?").split())
    return bool(t_words & words) and len(t_words) <= 3


class TurnNLU:
    """Extractor por llamada con generación anticipada sobre parciales.

    `preempt()` arranca la extracción sobre un parcial estable; si el turno
    termina con el mismo texto, `extract()` reutiliza la tarea en curso
    (patrón preemptive generation, spec §3.4). Si el texto final difiere, la
    especulación se descarta y se re-ejecuta.
    """

    def __init__(self):
        self._client = None
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        api_key = _nlu_api_key()
        if api_key:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key)
        else:
            logger.warning(
                "[nlu] sin credencial OpenAI — operando con clasificador local"
            )

    def _key(self, text: str, state: str) -> tuple[str, str]:
        return (state, text.strip())

    def preempt(
        self, text: str, state: str, last_bot_message: str
    ) -> Optional[asyncio.Task]:
        """Arranca la extracción sobre un parcial estable; devuelve la tarea
        (el runtime le cuelga el prewarm de geocoding especulativo)."""
        if not text.strip() or self._client is None:
            return None
        key = self._key(text, state)
        existing = self._tasks.get(key)
        if existing is not None:
            return existing
        self._prune()
        task = asyncio.create_task(self._call_llm(text, state, last_bot_message))
        self._tasks[key] = task
        return task

    def _prune(self, cap: int = 8) -> None:
        while len(self._tasks) >= cap:
            _, task = self._tasks.popitem()
            task.cancel()

    async def extract(
        self, text: str, state: str, last_bot_message: str
    ) -> NLUResult:
        if not text.strip():
            return fallback_classify(text)
        if self._client is None:
            return fallback_classify(text)

        key = self._key(text, state)
        task = self._tasks.pop(key, None)
        for stale_key in list(self._tasks):
            self._tasks.pop(stale_key).cancel()
        if task is None:
            task = asyncio.create_task(self._call_llm(text, state, last_bot_message))
        try:
            return await asyncio.wait_for(
                task, timeout=float(settings.VOICE_NLU_TIMEOUT_SEC)
            )
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            logger.warning("[nlu] extract degraded (%s) — fallback local", e)
            return fallback_classify(text)

    async def _call_llm(
        self, text: str, state: str, last_bot_message: str
    ) -> NLUResult:
        user_content = (
            f"Estado de la conversación: {state}\n"
            f"Última frase del asistente: {last_bot_message or '(ninguna)'}\n"
            f"Turno del usuario: {text}"
        )
        response = await self._client.chat.completions.create(
            model=settings.VOICE_NLU_MODEL or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_schema", "json_schema": NLU_JSON_SCHEMA},
            temperature=0.0,
            max_tokens=200,
            timeout=float(settings.VOICE_NLU_TIMEOUT_SEC),
        )
        raw = response.choices[0].message.content or "{}"
        result = parse_nlu_payload(json.loads(raw))
        logger.info(
            "[nlu] intent=%s pickup=%r landmark=%r conf=%.2f text=%r",
            result.intent,
            result.pickup_span,
            result.landmark_reference,
            result.pickup_confidence,
            text[:80],
        )
        return result
