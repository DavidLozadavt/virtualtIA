"""
Motor conversacional IntelliTaxi / Lyra — telefonía FreeSWITCH (sin Twilio).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import httpx

from services.telephony.types import TurnResult
from core.stt_enhancer import (
    AudioQualityProfile,
    correct_stt_errors,
    expand_number_words_in_streets,
    fuzzy_match_location,
    repair_location_transcription,
    repair_mangled_street_address,
    resolve_human_reference,
    strip_accents,
    POPAYAN_STT_CORRECTIONS,
)
from core.location_match import resolve_location_entity, decide, Decision, is_filler

_CITY_LEVEL_NAMES = frozenset({"popayan", "cauca", "colombia"})
from core.conversation_repair import (
    ConversationMemory,
    BargeInHandler,
    get_repair_message,
    infer_intent,
    _extract_partial_location,
)
from core.streaming_pipeline import (
    AdaptiveEndpointController,
    PartialIntentDetector,
    TurnProcessor,
)
from core.address_utils import (
    _strip_preamble,
    _parse_si_no,
    _is_correction_request,
    _is_repeat_request,
    _try_local_match,
    looks_like_place,
    normalize_address,
    normalize_colombian_address,
)
from core.geocoder_service import geocode, run_pipeline, handle_user_context
from core.geo_types import GeoSessionState
from core.llm_utils import (
    get_async_openai_client as _get_async_openai,
    get_model as _get_model,
)

logger = logging.getLogger("lyra.telephony.engine")

# ── Configuración ─────────────────────────────────────────────────────────────


def _cfg(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _cfg_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))




# ── Fallback DTMF (teclado) ───────────────────────────────────────────────────
# Tras 2 fallos STT consecutivos en waiting_origin, en vez de seguir pidiendo que
# repita (inútil con audio malo), ofrecemos un menú numérico con los barrios más
# solicitados. El usuario marca un dígito → mapeo directo al nombre canónico, sin
# pasar por STT. "8" = otro barrio → vuelve a captura por voz.
DTMF_BARRIO_MAP: Dict[str, str] = {
    "1": "Pubenza",
    "2": "Centro",
    "3": "Campanario",
    "4": "Los Sauces",
    "5": "Yanaconas",
    "6": "Valle del Ortigal",
    "7": "María Oriente",
    # "8" → "otro barrio": no mapea a canónico, vuelve a pedir por voz.
}

DTMF_MENU_MESSAGE = (
    "Si no me escuchas bien, marca en el teclado: "
    "1 para Pubenza. 2 para el Centro. 3 para Campanario. "
    "4 para Los Sauces. 5 para Yanaconas. 6 para Valle del Ortigal. "
    "7 para María Oriente. 8 para otro barrio."
)




def _max_silence() -> int:
    return _cfg_int("MAX_SILENCE_BEFORE_HANGUP", 3)




# ── Estados de la máquina de estados ─────────────────────────────────────────

STATE_WAITING_ORIGIN      = "waiting_origin"
STATE_WAITING_GEO_CONTEXT = "waiting_geo_context"   # pipeline en CONTEXT_GATHERING
STATE_CONFIRMING_ORIGIN   = "confirming_origin"
STATE_WAITING_DEST_OR_SKIP = "waiting_dest_or_skip"
STATE_CONFIRMING_DEST     = "confirming_dest"
STATE_SERVICE_CREATED     = "service_created"
STATE_CREATING_SERVICE    = "creating_service"
STATE_FINISHED            = "finished"

# ── Feature flag: preguntar destino ──────────────────────────────────────────
# Cuando es False, el sistema crea el servicio inmediatamente después de
# confirmar el origen, sin preguntar a dónde va el pasajero.
# Para re-habilitar: cambiar a True. El estado STATE_WAITING_DEST_OR_SKIP
# y STATE_CONFIRMING_DEST siguen implementados y funcionan normalmente.
ASK_DESTINATION = False

SESSION_TTL_SEC = int(os.getenv("CALL_SESSION_TTL_SEC", "7200"))

_SESSION_LOCK = threading.Lock()
_SESSIONS: Dict[str, "CallSession"] = {}


# ── Sesión enriquecida ────────────────────────────────────────────────────────


@dataclass
class CallSession:
    call_sid: str
    caller_phone: Optional[str] = None
    state: str = STATE_WAITING_ORIGIN
    origen_text: Optional[str] = None
    origen_barrio: Optional[str] = None
    destino_text: Optional[str] = None
    service_created: bool = False
    silence_count: int = 0
    last_message: str = ""
    updated_at: float = field(default_factory=time.time)

    # ── Nuevos: mejoras de calidad ──
    quality_profile: AudioQualityProfile = field(default_factory=AudioQualityProfile)
    memory: ConversationMemory = field(default_factory=lambda: ConversationMemory(""))
    endpoint_ctrl: Optional[AdaptiveEndpointController] = None
    intent_detector: Optional[PartialIntentDetector] = None
    turn_processor: Optional[TurnProcessor] = None
    retry_count: int = 0  # Reintentos consecutivos en el mismo estado
    geo_origin: GeoSessionState = field(default_factory=GeoSessionState)
    geo_dest:   GeoSessionState = field(default_factory=GeoSessionState)
    # Desambiguación pendiente de un grupo multi-sede (data-driven). Dict con
    # {"candidates": [canónicos], "question": str}. None cuando no hay nada pendiente.
    pending_disambiguation: Optional[dict] = None

    def __post_init__(self):
        self.memory = ConversationMemory(self.call_sid)
        self.endpoint_ctrl = AdaptiveEndpointController(self.quality_profile)
        self.intent_detector = PartialIntentDetector()
        self.turn_processor = TurnProcessor(
            self.quality_profile,
            self.memory,
            self.intent_detector,
            self.endpoint_ctrl,
        )

    def touch(self) -> None:
        self.updated_at = time.time()

    def get_endpoint_params(self, short_answer: bool = False) -> dict:
        """Parámetros de endpointing adaptativos para el próximo Gather."""
        return self.endpoint_ctrl.get_parameters(self.state, short_answer)

    def speech_timeout(self, short_answer: bool = False) -> str:
        return self.get_endpoint_params(short_answer)["speech_timeout"]

    def gather_timeout(self) -> int:
        return self.get_endpoint_params()["gather_timeout"]


def _prune_sessions() -> None:
    now = time.time()
    dead = [k for k, s in _SESSIONS.items() if now - s.updated_at > SESSION_TTL_SEC]
    for k in dead:
        _SESSIONS.pop(k, None)


def get_session(call_sid: str) -> CallSession:
    with _SESSION_LOCK:
        _prune_sessions()
        if call_sid not in _SESSIONS:
            logger.warning("[SESSION] Warning: Using in-memory sessions. In a multi-worker production environment (e.g., Gunicorn), caller_phone may be lost. Migration to Redis/Shared Storage is required.")
            _SESSIONS[call_sid] = CallSession(call_sid=call_sid)
        s = _SESSIONS[call_sid]
        s.touch()
        return s


def reset_session(call_sid: str) -> None:
    with _SESSION_LOCK:
        _SESSIONS.pop(call_sid, None)


# ── Procesamiento de texto STT ────────────────────────────────────────────────

# Contracciones payanesas
_PAYANES_CONTRACTIONS = {
    "tá": "está",
    "toy": "estoy",
    "tamos": "estamos",
    "taba": "estaba",
    "pa": "para",
    "pal": "para el",
    "pa'l": "para el",
    "p'alla": "para allá",
    "pa'lla": "para allá",
    "palla": "para allá",
    "pa'ca": "para acá",
    "paca": "para acá",
    "onde": "donde",
    "ónde": "donde",
    "d'onde": "de donde",
    "nonces": "entonces",
    "tonces": "entonces",
    "'tonces": "entonces",
    "tons": "entonces",
    "l centro": "el centro",
    "'l centro": "el centro",
    "vea": "mire",
    "hágale": "sí",
    "de una": "sí",
    "cójame": "recoja",
    "recojame": "recoja",
    "mandame": "envíe",
}

# Fragmentos de barge-in de Lyra que Twilio puede capturar
_BARGEIN_FRAGMENTS = [
    r"^hola\s+soy\s+lyra[,.]?\s*",
    r"^soy\s+lyra[,.]?\s*",
    r"^tu\s+asistente\s+de\s+taxbelalcazar[,.]?\s*",
    r"^cu[eé]ntame[,.]?\s*",
    r"^listo[,.]?\s+te\s+recojo\s+en\s*",
    r"^procesando\s+tu\s+solicitud[,.]?\s*",
]

_FUSED_STREET_RE = re.compile(
    r"\b(calle|carrera|barrio)(\d+|[a-záéíóúñ]{3,})",
    re.IGNORECASE,
)

_NUM_WORDS_STREET = {
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciséis": 16,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintidós": 22,
    "veintidos": 22,
    "veintitrés": 23,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
}

import re

def limpiar_numero(valor: str | None) -> str | None:
    if not valor:
        return None

    # Handle sip:, whatsapp: prefixes by extracting only the digit part
    match = re.search(r'\+?\d{7,15}', str(valor))
    if not match:
        return None

    numero = match.group(0)

    if numero.startswith("+"):
        if numero.startswith("+57") and len(numero) == 13:
            return numero
        return numero

    if len(numero) == 10 and numero.startswith("3"):
        return "+57" + numero

    if len(numero) == 12 and numero.startswith("57"):
        return "+" + numero

    return numero


def es_numero_troncal_o_empresa(numero: str | None) -> bool:
    if not numero:
        return False
        
    limpio = re.sub(r'\D', '', numero)
    prohibidos = ['576028231111', '6028231111', '57602823111', '602823111']
    
    for prohibido in prohibidos:
        if prohibido in limpio:
            return True
            
    return False


def obtener_telefono_cliente(form_data) -> tuple[str | None, str]:
    campos_prioritarios = [
        "SipHeader_X-Original-Caller",
        "SipHeader_X-Original-ANI",
        "SipHeader_P-Asserted-Identity",
        "SipHeader_Remote-Party-ID",
        "SipHeader_Diversion",
        "From",
        "Caller",
    ]

    for campo in campos_prioritarios:
        telefono = limpiar_numero(form_data.get(campo))
        if telefono and not es_numero_troncal_o_empresa(telefono):
            return telefono, campo

    return None, "not_found"


# ── Normalización agresiva para audio MUY degradado ──────────────────────────
# Tokens de 1 carácter que SÍ son palabras/marcadores válidos en español o en
# nomenclatura de direcciones — no se eliminan. El resto de tokens de 1 char
# (ruido suelto que el STT escupe sobre línea PSTN sucia) se descarta.
_VALID_1CHAR_TOKENS = frozenset({"a", "y", "o", "u", "e", "#"})

# Frases-basura observadas: "quiero un móvil" mal transcrito como "quisiera/quiero
# morir". Se eliminan enteras para no contaminar la extracción del destino.
_STT_JUNK_PHRASES = [
    r"\bquisiera\s+morir\b",
    r"\bquiero\s+morir\b",
]

_REPEAT_CHAR_RE = re.compile(r"(.)\1{2,}")          # 3+ repeticiones → 1
_WEIRD_CHARS_RE = re.compile(r"[^\w\s#\-]", re.UNICODE)


def _aggressive_normalize(text: str) -> str:
    """
    Normalización agresiva previa, para audio telefónico muy degradado.

    - Elimina caracteres extraños que Twilio a veces inyecta en transcripciones
      de audio ruidoso (deja letras —incl. acentuadas—, dígitos, espacio, # y -).
    - Colapsa caracteres repetidos: "fuuuerza" → "fuerza".
    - Elimina frases-basura conocidas ("quisiera morir" = "quiero un móvil").
    - Descarta tokens sueltos de 1 carácter que no sean número ni palabra válida.
    """
    t = _WEIRD_CHARS_RE.sub(" ", text)
    t = _REPEAT_CHAR_RE.sub(r"\1", t)

    for pat in _STT_JUNK_PHRASES:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE)

    tokens = []
    for tok in t.split():
        if len(tok) == 1 and not tok.isdigit() and tok.lower() not in _VALID_1CHAR_TOKENS:
            continue
        tokens.append(tok)

    return re.sub(r"\s+", " ", " ".join(tokens)).strip()


def preprocess_stt(text: str, confidence: float = 1.0) -> str:
    """
    Pipeline completo de pre-procesamiento STT.

    Pasos (en orden):
    0. Normalización agresiva (audio muy degradado): chars extraños, repeticiones,
       frases-basura, tokens sueltos de 1 char
    1. Eliminar fragmentos de barge-in de Lyra
    2. Expandir contracciones payanesas
    3. Separar palabras fusionadas (habla rápida)
    4. Convertir números-palabra en contexto de calles
    5. Corregir errores fonéticos específicos de Popayán
    6. Normalizar espacios
    """
    if not text or len(text) < 2:
        return text

    t = text.strip()

    # 0. Normalización agresiva previa
    t = _aggressive_normalize(t)
    if not t:
        return text.strip()

    # 1. Barge-in cleanup
    for pat in _BARGEIN_FRAGMENTS:
        t = re.sub(pat, "", t, flags=re.IGNORECASE).strip()

    if not t:
        return text.strip()

    # 2. Contracciones payanesas
    for contraction, expansion in _PAYANES_CONTRACTIONS.items():
        pat = r"\b" + re.escape(contraction) + r"\b"
        t = re.sub(pat, expansion, t, flags=re.IGNORECASE)

    # 3. Palabras fusionadas: "callequince" → "calle quince"
    t = _FUSED_STREET_RE.sub(r"\1 \2", t)

    # 4. Números-palabra en contexto de calle: "calle quince" → "calle 15"
    t = expand_number_words_in_streets(t)

    # 4b. Reparar direcciones mangled: "carrera 4 a eb 1728" → "carrera 4a # 17b 28"
    t = repair_mangled_street_address(t)

    # 5. Correcciones STT específicas de Popayán (dict curado)
    t = correct_stt_errors(t)

    # 5b. Reparación fonética de transcripción contra el catálogo completo
    #     (generaliza el dict a los ~600 lugares; guardas estrictas, no expande
    #     ni colisiona — ej. "villa del karmen" → "villa del carmen").
    t = repair_location_transcription(t)

    # 6. Normalizar espacios
    t = re.sub(r"\s+", " ", t).strip()

    return t


# Señales de que el texto YA contiene una dirección/lugar útil. Si Twilio
# transcribió esto, oyó algo procesable — no hay que pedir que repita.
_ADDRESS_SIGNAL_RE = re.compile(
    r"\d|#|\b(calle|carrera|cra|cr|cl|kr|kra|diagonal|diag|transversal|tr|"
    r"avenida|av|barrio|sector|conjunto|urbanizaci[oó]n|manzana|"
    r"norte|sur|oriente|occidente)\b",
    re.IGNORECASE,
)


def classify_speech_quality(
    text: str, confidence: float, profile: AudioQualityProfile
) -> str:
    """
    Clasifica la calidad del turno de voz.

    Filosofía (corregida): Twilio YA hizo VAD + STT. Si devolvió texto, oyó algo.
    Confiamos en el TEXTO, no en el `Confidence` (que Twilio reporta de forma
    poco fiable — frecuentemente 0.00 en transcripciones correctas). La confianza
    es solo un desempate, NUNCA una barrera que obligue al usuario a repetir o a
    subir la voz.

    "low" se reserva para ruido real: 1 token corto sin contenido. Todo lo que
    parezca dirección/lugar o frase con varias palabras se procesa.
    """
    t = (text or "").strip()

    if not t or len(t) < 2:
        return "empty"

    word_count = len(t.split())
    t_clean = re.sub(r"[^\w\s]", "", t.lower()).strip()

    # Respuestas cortas explícitas (sí/no/ok...) → siempre procesables
    if t_clean in {
        "no", "si", "sí", "sip", "nop", "ok", "vale", "dale", "listo",
        "bueno", "ya", "claro", "exacto", "correcto", "afirmativo", "negativo",
    }:
        return "high"

    # Texto con señal de dirección/lugar (número, calle, barrio, sector...) →
    # Twilio transcribió contenido útil sin importar la confianza reportada.
    if _ADDRESS_SIGNAL_RE.search(t_clean):
        return "high"

    # Frase con varias palabras reales → procesable.
    if word_count >= 3:
        return "high"

    # 2 palabras → procesar (puede ser "los sauces", "santa teresa", etc.)
    if word_count == 2:
        return "medium"

    # 1 sola palabra: si parece nombre de lugar (>=4 letras) la intentamos;
    # si es un token corto/ruido ("eh", "mmm", "ah") → low.
    if len(t_clean) >= 4:
        return "medium"

    return "low"


def _aggressive_place_recovery(text: str) -> Optional[str]:
    """
    Último recurso para rescatar un lugar de una transcripción mala.

    Cuando la confianza es razonable pero el texto vino mal transcrito (ej:
    "noches quisiera morir para la fuerza" con conf=0.73), en vez de rechazar el
    turno en silencio, deslizamos ventanas de 3→2→1 palabras y probamos cada
    fragmento contra el resolver tipado.

    Contrato de PRECISIÓN: solo devuelve un canónico para fragmentos que el
    resolver decide ACCEPT (coincidencia textual/fonética fuerte y NO ambigua).
    Fragmentos de cortesía/relleno ("buenas", "hola", "gracias") o coincidencias
    débiles → None (el llamador pide repetir). Nunca acepta AMBIGUOUS ni inventa
    una sede a partir de un fuzzy débil.
    """
    if not text:
        return None

    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    if not words:
        return None

    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            frag = " ".join(words[i : i + size]).strip()
            if len(frag) < 4:
                continue
            m = resolve_location_entity(frag)
            if decide(m) == Decision.ACCEPT and m.canonical:
                logger.info(f"[RECOVERY] Fragment {frag!r} → {m.canonical!r} [{m.match_type.name}]")
                return m.canonical

    return None


def _short_place_name(canonical: str) -> str:
    """Nombre corto para leer en voz alta (parte antes de la primera coma)."""
    return (canonical or "").split(",")[0].strip()


def _disambiguation_question(candidates: list[str]) -> str:
    """Construye la pregunta de desambiguación a partir de las sedes reales.
    Generalizable: no contiene nombres hardcodeados."""
    names = [_short_place_name(c) for c in candidates if c]
    if len(names) >= 2:
        return f"¿Cuál: {', '.join(names[:-1])} o {names[-1]}?"
    if names:
        return f"¿Te refieres a {names[0]}?"
    return "¿Cuál de las opciones?"


# ── Extracción de direcciones ─────────────────────────────────────────────────


_STREET_KW_RE = re.compile(
    r"\b(calle|carrera|cra|cr|cl|kr|kra|transversal|tr|diagonal|diag|avenida|av)\b",
    re.IGNORECASE,
)


def _extract_address_span(text: str) -> str:
    """
    Recorta el ruido conversacional dejando solo la dirección.

    El geocoder NO debe recibir "muy buenas tardes quisiera un móvil para aquí
    para la calle 16 # 366" — solo "calle 16 # 366". Devuelve el texto desde la
    primera palabra-clave vial en adelante; si no hay, devuelve el texto igual.
    """
    if not text:
        return text
    m = _STREET_KW_RE.search(text)
    if m and m.start() > 0:
        return text[m.start():].strip()
    return text


async def extract_address(user_text: str, role: str = "origen") -> Tuple[Optional[str], str]:
    """
    Pipeline unificado de extracción. role = "origen" | "destino"
    Retorna (canonical_name, hint_message)
    """
    # 1. Strip preamble
    cleaned = _strip_preamble(user_text)

    # 2. Detectar si hay nomenclatura de calle con número
    has_street = bool(re.search(
        r'(?:calle|carrera|cl|cra|cr|transversal|tr|diagonal|diag|avenida|av|kr|kra)\s*[\da-záéíóú]',
        cleaned.lower()
    ))

    # 3. Referencia humana ("por el éxito", "frente a la galería")
    human_ref = resolve_human_reference(cleaned) or resolve_human_reference(user_text)
    if human_ref and human_ref.get("canonical"):
        # Si el texto ALSO contiene dirección de calle, mantener la dirección
        # (recortando el ruido conversacional previo a la palabra-clave vial).
        if has_street:
            span = _extract_address_span(cleaned)
            logger.info(f"[EXTRACT] Human ref + street → address span: {span!r}")
            return span, ""
        logger.info(f"[EXTRACT] Human ref: {user_text!r} → {human_ref['canonical']!r}")
        return human_ref["canonical"], ""

    # 4. Local match (stub → siempre None; mantenido por compatibilidad de flujo)
    for candidate in (cleaned, user_text):
        local = _try_local_match(candidate)
        if local:
            logger.info(f"[EXTRACT] Local match {role}: {local!r}")
            return local, ""

    # 5. Si hay dirección de calle, recortar ruido conversacional y retornar.
    if has_street:
        span = _extract_address_span(cleaned)
        logger.info(f"[EXTRACT] Street address span: {span!r}")
        return span, ""

    # 6. LLM con prompt simplificado (solo si no hay match local)
    client = _get_async_openai()
    if not client:
        fb = user_text.strip()
        if role == "origen":
            return (fb if len(fb) > 3 else None), "¿Dónde te recogemos en Popayán?"
        else:
            return (fb if len(fb) > 2 else None), "¿A dónde vas en Popayán?"

    model = _get_model()
    prompt = (
        "Eres un asistente de taxi en Popayán, Colombia.\n"
        f"El usuario dijo: '{user_text}'\n"
        f"Extrae SOLO el nombre del lugar de {role} como texto limpio.\n"
        "Sin JSON, sin explicaciones, solo el nombre.\n"
        "Si el usuario dio una dirección con número (ej: carrera 5 # 12-34), retorna la dirección COMPLETA.\n"
        "Si el usuario dio solo un barrio o punto de referencia, retorna solo ese nombre.\n"
        "Ejemplos:\n"
        "- 'recógeme en la torre del reloj' → Torre del Reloj\n"
        "- 'estoy en carrera 5 # 12-34 barrio Modelo' → Carrera 5 # 12-34\n"
        "- 'campanario' → Centro Comercial Campanario\n"
        "Lugar:"
    )

    try:
        result = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            timeout=4.0,
        )
        raw = (result.choices[0].message.content or "").strip()

        # Guard anti-default-city: el LLM, ante un saludo o texto sin lugar,
        # tiende a devolver la ciudad/región del prompt ("Popayán"). Eso NO es
        # un punto de recogida → tratarlo como sin-resultado. Evita el fallback
        # geográfico implícito "Hola" → "Popayán".
        if not raw or strip_accents(raw.lower()) in _CITY_LEVEL_NAMES or raw.lower() in ("null", "none", ""):
            if raw:
                logger.info(f"[EXTRACT] LLM devolvió nivel-ciudad/none {raw!r} → sin lugar")
            fb = user_text.strip()
            if role == "origen":
                return (fb if len(fb) >= 4 else None), "¿Dónde te recogemos? Dime el barrio o la calle."
            else:
                return (fb if len(fb) >= 3 else None), "¿Cuál es tu destino?"

        return raw, ""

    except Exception as e:
        logger.error(f"extract_address error: {e}")
        fb = user_text.strip()
        if role == "origen":
            return (fb if len(fb) >= 4 else None), "¿Dónde te recogemos? Dime el barrio o la calle."
        else:
            return (fb if len(fb) >= 3 else None), "Repite el destino, por favor."


# Mantener compatibilidad con código existente que llame a las funciones anteriores
async def extract_pickup_address(user_text: str) -> Tuple[Optional[str], str]:
    return await extract_address(user_text, role="origen")


async def extract_destination_address(user_text: str) -> Tuple[Optional[str], str]:
    return await extract_address(user_text, role="destino")


# ── Backend ───────────────────────────────────────────────────────────────────


async def _create_service(
    celular: Optional[str],
    origen: str,
    destino: Optional[str],
    http_client: Optional[httpx.AsyncClient] = None,
    origen_barrio: Optional[str] = None,
    call_id: str = "",
) -> Tuple[bool, str]:
    """Geocodifica y crea el servicio de taxi en el backend Laravel."""
    from core.config import settings
    from core.geocoder_service import geocode
    from services.telephony.log_utils import mask_payload_phones, mask_phone, telephony_log_prefix

    prefix = telephony_log_prefix()
    api_base = (settings.INTELLITAXI_API_BASE or "").strip().rstrip("/")
    if not api_base:
        logger.error(f"{prefix} backend_error call_id={call_id} reason=INTELLITAXI_API_BASE_not_configured")
        return False, "Problema de configuración del servidor. Intenta más tarde."

    # geocode() ya aplica normalize_colombian_address internamente.
    # Pasar barrio mejora precisión de Google Maps para nomenclaturas colombianas
    # (ej: "Cl. 16 # 3CE-41, Santa Teresa" vs solo "Cl. 16 # 3CE-41")
    g_o = await geocode(origen, barrio=origen_barrio)

    if destino:
        g_d = await geocode(destino)
    else:
        g_d = None

    if not g_o:
        return False, (
            "No me aparece esa ubicación en Popayán. "
            "¿Me la dices de otra forma? Prueba con un barrio o una calle."
        )

    olat, olng, geo_o = g_o
    logger.info(f"Geocode origin OK: {geo_o[:80]}")

    dlat = dlng = 0.0
    if destino and g_d:
        dlat, dlng, geo_d = g_d
        logger.info(f"Geocode dest OK: {geo_d[:80]}")
    elif destino and not g_d:
        return False, (
            "El destino no me aparece en Popayán. " "¿Me lo dices de otra forma?"
        )

    payload = {
        "pasajero_id": 1,
        "celular": celular,
        "telefonoLlamada": celular,
        "telefono_cliente_final": celular,
        "pasajero_nombre": "Usuario Telefónico",
        "canal_origen": "PHONE_AI_CALL",
        "origen": origen,
        "origen_lat": float(olat),
        "origen_lng": float(olng),
        "clase_vehiculo": "TAXI",
        "precio_estimado": 0.0,
        "destino": (destino or "").strip(),
        "destino_lat": float(dlat),
        "destino_lng": float(dlng),
    }

    url = f"{api_base}/taxi/solicitud-telefonica"
    logger.info(
        f"{prefix} backend_request_started call_id={call_id} "
        f"url={url} celular={mask_phone(celular)} origen={origen[:80]!r}"
    )
    logger.info(
        f"{prefix} backend_request_payload call_id={call_id} "
        f"payload={mask_payload_phones(payload)}"
    )

    try:
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=8.0, write=5.0, pool=5.0)
        )
        try:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        finally:
            if not http_client:
                await client.aclose()

        if resp.status_code >= 400:
            logger.error(
                f"{prefix} backend_error call_id={call_id} "
                f"status={resp.status_code} body={resp.text[:200]!r}"
            )
            return (
                False,
                "Tuvimos un problema registrando tu servicio. Inténtalo de nuevo.",
            )

        logger.info(
            f"{prefix} backend_response call_id={call_id} "
            f"status={resp.status_code} ok=true"
        )
        logger.info(f"{prefix} service_created call_id={call_id}")
        return True, (
            "Te enviaremos los datos del conductor por WhatsApp "
            "y en un momento él se comunica contigo. "
            "¡Que tengas un excelente viaje!"
        )

    except httpx.TimeoutException:
        logger.error(f"{prefix} backend_error call_id={call_id} reason=timeout")
        return False, "Se demoró el servidor. Inténtalo de nuevo, porfa."
    except Exception as e:
        logger.error(f"{prefix} backend_error call_id={call_id} reason={e!r}")
        return False, "Problemita técnico. Intenta de nuevo o pide el taxi por la app."




def _r(speak: str, listen: bool = True, hangup: bool = False, short: bool = False, dtmf: bool = False, proc: str = "") -> TurnResult:
    return TurnResult(
        speak=speak,
        listen=listen,
        hangup=hangup,
        short_answer=short,
        dtmf_mode=dtmf,
        processing_message=proc,
    )


GREETING_MESSAGE = (
    "Soy Lyra, tu asistente de TaxBelalcazar. "
    "Cuéntame, ¿en dónde te recogemos hoy?"
)


async def handle_call_start(call_id: str, caller_phone: Optional[str] = None) -> TurnResult:
    sess = get_session(call_id)
    sess.caller_phone = caller_phone
    sess.state = STATE_WAITING_ORIGIN
    sess.last_message = GREETING_MESSAGE
    logger.info({"event": "call_started", "call_id": call_id, "caller_phone": caller_phone})
    return _r(GREETING_MESSAGE)


async def process_turn(
    call_id: str,
    texto_usuario: str = "",
    confidence: float = 1.0,
    digits: str = "",
    caller_id: Optional[str] = None,
    caller_source: str = "session",
    http_client=None,
) -> TurnResult:
    return await _process_turn_impl(
        call_sid=call_id,
        texto_usuario=texto_usuario,
        confidence=confidence,
        digits=digits,
        caller_id=caller_id,
        source=caller_source,
        http_client=http_client,
    )


async def _process_turn_impl(
    call_sid: str,
    texto_usuario: str,
    confidence: float,
    digits: str,
    caller_id: Optional[str],
    source: str,
    http_client=None,
) -> TurnResult:
    sess = get_session(call_sid)
    telefono_cliente = caller_id
    if telefono_cliente and not es_numero_troncal_o_empresa(telefono_cliente):
        sess.caller_phone = telefono_cliente

    # ── Pipeline de pre-procesamiento STT ──
    texto_original = texto_usuario
    if texto_usuario:
        texto_usuario = preprocess_stt(texto_usuario, confidence)
        if texto_usuario != texto_original:
            logger.info(f"[STT] Preprocessed: {texto_original!r} → {texto_usuario!r}")

    # ── Actualizar perfil de calidad de audio ──
    sess.quality_profile.update(confidence, texto_usuario)

    # ── Clasificar calidad con perfil adaptativo ──
    speech_quality = classify_speech_quality(
        texto_usuario, confidence, sess.quality_profile
    )
    if texto_usuario:
        logger.info(
            f"[QUALITY] {speech_quality} conf={confidence:.2f} "
            f"noisy={sess.quality_profile.is_noisy_call} "
            f"fast={sess.quality_profile.is_fast_speaker}"
        )

    # ── Detección de saludo sin dirección ──
    _GREETING_WORDS = {
        "hola",
        "buenas",
        "buenos",
        "qhubo",
        "alo",
        "aló",
        "bueno",
        "diga",
        "dígame",
    }
    texto_para_greeting_check = texto_original if texto_original else ""
    if not texto_usuario and texto_para_greeting_check:
        orig_words = set(
            texto_para_greeting_check.lower().strip().rstrip(".,!?").split()
        )
        if orig_words & _GREETING_WORDS:
            texto_usuario = "__GREETING__"

    logger.info(
        f"[SPEECH] call_id={call_sid} state={sess.state} "
        f"quality={speech_quality} text={texto_usuario[:100]!r}"
    )

    # ── Estados terminales ──
    if sess.state == STATE_FINISHED or (
        sess.service_created and sess.state == STATE_SERVICE_CREATED
    ):
        return _r("¡Gracias por llamar! ¡Que te vaya bien!", listen=False, hangup=True)

    # http_client from parameter

    # ── Creación de servicio (estado de transición) ──
    if sess.state == STATE_CREATING_SERVICE:
        telefono_final = caller_id
        source_final = source
        
        if es_numero_troncal_o_empresa(telefono_final):
            from services.telephony.log_utils import mask_phone, telephony_log_prefix

            logger.error({
                "event": "blocked_trunk_number_as_customer",
                "call_sid": call_sid,
                "telefono_detectado": mask_phone(telefono_final),
                "from_original": None,
                "original_caller": None,
                "prefix": telephony_log_prefix(),
            })
            telefono_final = None
            source_final = "not_found"
            
        log_data = {
            "event": "sending_service_to_laravel",
            "call_sid": call_sid,
            "from_original": None,
            "payload_field": "celular"
        }
        
        if telefono_final:
            log_data["telefono_cliente_final"] = telefono_final
            log_data["source"] = source_final
        else:
            log_data["telefono_cliente_final"] = None
            log_data["source"] = source_final
            log_data["warning"] = "No original caller found; trunk number was blocked"
            
        # Reemplazamos payload_field a como va a quedar
        log_data["payload_field"] = "telefonoLlamada/celular/telefono_cliente_final"
        
        logger.info(log_data)
        
        ok, closing = await _create_service(
            telefono_final,
            sess.origen_text or "",
            sess.destino_text,
            http_client=http_client,
            origen_barrio=sess.origen_barrio,
            call_id=call_sid,
        )
        if not ok:
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            sess.origen_barrio = None
            sess.last_message = closing
            return _r(closing, short=True)

        sess.service_created = True
        sess.state = STATE_FINISHED
        reset_session(call_sid)
        return _r(closing, listen=False, hangup=True)

    # ── DTMF: el usuario marcó un dígito en el menú fallback de barrios ──
    # Se procesa ANTES del silencio (un DTMF llega con SpeechResult vacío y, sin
    # esto, caería en la rama de silencio). Mapea el dígito al barrio canónico y
    # salta directo a confirmación, sin pasar por STT.
    if digits:
        logger.info(f"[DTMF] Digit pressed: {digits!r} state={sess.state}")
        sess.silence_count = 0
        sess.retry_count = 0
        sess.endpoint_ctrl.on_successful_response()
        canonical = DTMF_BARRIO_MAP.get(digits)
        if canonical:
            # canonical ES el nombre del barrio → no se pasa además como barrio=
            # al geocoder (duplicaría "Pubenza, Pubenza"). Igual que un lugar
            # nombrado dicho por voz: origen_barrio se deja sin fijar.
            sess.origen_text = canonical
            sess.origen_barrio = None
            sess.memory.add_location_mention(canonical)
            sess.geo_origin.reset()
            sess.state = STATE_CONFIRMING_ORIGIN
            msg = f"Perfecto, {canonical}. ¿Te recogemos ahí? Di sí para confirmar."
            sess.last_message = msg
            return _r(msg, short=True)
        # "8" (otro barrio) o dígito no mapeado → volver a captura por voz
        sess.state = STATE_WAITING_ORIGIN
        msg = "Listo. Dime el nombre del barrio o la dirección donde te recogemos."
        sess.last_message = msg
        return _r(msg)

    # ── Detección de "repite" ──
    if texto_usuario and _is_repeat_request(texto_usuario):
        logger.info("[SPEECH] Repeat request detected.")
        replay = sess.last_message or "¿En qué parte de Popayán te recogemos?"
        return _r(replay)

    # ── Saludo sin dirección ──
    if texto_usuario == "__GREETING__":
        texto_usuario = ""
        msgs = {
            STATE_WAITING_ORIGIN: "¡Hola! Con mucho gusto te ayudo. Cuéntame, ¿en dónde te recogemos?",
            STATE_WAITING_DEST_OR_SKIP: "¡Hola! Dime, ¿a dónde te diriges? O si prefieres, dime no y le cuentas al conductor.",
        }
        msg = msgs.get(sess.state, "¡Hola! Soy Lyra, ¿en qué puedo ayudarte?")
        sess.last_message = msg
        return _r(msg)

    # ── Silencio ──
    if not texto_usuario:
        sess.silence_count += 1
        sess.quality_profile.silence_count += 1
        logger.info(f"[SILENCE] #{sess.silence_count} state={sess.state}")

        if sess.silence_count >= _max_silence():
            reset_session(call_sid)
            return _r("No te escucho. Llámanos cuando puedas. ¡Hasta luego!", listen=False, hangup=True)

        silence_msgs = {
            (STATE_WAITING_ORIGIN, 1): "¿Sigues ahí? Dime dónde te recojo.",
            (STATE_WAITING_ORIGIN, 2): "¿Dónde estás en Popayán?",
            (STATE_WAITING_DEST_OR_SKIP, 1): "No te escuché. ¿A dónde vas? O dime no.",
            (STATE_WAITING_DEST_OR_SKIP, 2): "¿A dónde vas? O dime no si le cuentas al conductor.",
            (
                STATE_CONFIRMING_ORIGIN,
                1,
            ): f"¿Confirmas {sess.origen_barrio or 'esa zona'}? Di sí o no.",
            (
                STATE_CONFIRMING_DEST,
                1,
            ): f"¿Confirmas que vas a {sess.destino_text or 'ese destino'}? Di sí o no.",
            (
                STATE_CONFIRMING_DEST,
                1,
            ): f"¿Confirmas que vas a {sess.destino_text or 'ese destino'}? Di sí o no.",
        }

        msg = silence_msgs.get(
            (sess.state, min(sess.silence_count, 2)), "¿Me escuchas? Háblame."
        )
        sess.last_message = msg
        return _r(msg)

    # ── Reset contador de silencio ──
    sess.silence_count = 0

    # ── Gate de baja calidad: reparación inteligente ──
    if speech_quality == "low" and sess.state in (
        STATE_WAITING_ORIGIN,
        STATE_WAITING_DEST_OR_SKIP,
    ):
        sess.retry_count += 1
        sess.endpoint_ctrl.on_retry()

        # Fallback DTMF: tras 2 fallos STT consecutivos en waiting_origin, dejar
        # de pedir que repita (inútil con audio malo) y ofrecer menú numérico.
        if sess.state == STATE_WAITING_ORIGIN and sess.retry_count >= 2:
            logger.info(f"[LOW_QUALITY] Retry #{sess.retry_count} → DTMF fallback menu")
            sess.last_message = DTMF_MENU_MESSAGE
            return _r(DTMF_MENU_MESSAGE, dtmf=True)

        msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)

        # En el 3er reintento de destino, cambiar estrategia: pedir solo barrio
        if sess.retry_count >= 3:
            if sess.state == STATE_WAITING_ORIGIN:
                msg = "¿Solo dime el barrio donde estás?"
            else:
                msg = "¿Solo dime el barrio de destino?"

        logger.info(f"[LOW_QUALITY] Retry #{sess.retry_count}: {msg!r}")
        sess.last_message = msg
        return _r(msg)

    # ── Gate de baja calidad en estados de confirmación ──
    if speech_quality == "low" and sess.state in (STATE_CONFIRMING_ORIGIN, STATE_CONFIRMING_DEST):
        msg = "No te escuché bien. ¿Confirmas con sí o no?"
        sess.last_message = msg
        return _r(msg, short=True)

    # ── Calidad media: intentar match local agresivo antes de LLM ──
    _medium_local_resolved = False
    if speech_quality == "medium" and sess.state in (
        STATE_WAITING_ORIGIN,
        STATE_WAITING_DEST_OR_SKIP,
    ):
        local_try = _try_local_match(texto_usuario)
        if not local_try:
            local_try = _try_local_match(preprocess_stt(texto_usuario, confidence))
        if local_try:
            # Si el match infirió una sede SENA específica pero el usuario solo
            # dijo "sena" (sin "norte"/"centro"), no aceptar — la lógica de
            # desambiguación lo resolverá correctamente.
            _tl_orig = strip_accents(texto_usuario.lower())
            _sena_specific = local_try.upper().startswith("SENA") and local_try not in ("SENA Popayán",)
            _user_said_which = any(k in _tl_orig for k in ["norte", "centro", "senacentro"])
            if _sena_specific and not _user_said_which:
                logger.info(f"[MEDIUM_QUALITY] SENA ambiguous — skipping local match {local_try!r}")
            else:
                logger.info(f"[MEDIUM_QUALITY] Resolved via local match: {local_try!r}")
                texto_usuario = local_try
                _medium_local_resolved = True

    # NOTA: retry_count NO se resetea aquí. Un turno de calidad alta/media puede
    # aún ser rechazado por el gate anti-basura de waiting_origin (extracción que
    # no parece lugar). El reset ocurre en los puntos de ÉXITO real (origen/dest
    # aceptado) para que 2 fallos —de cualquier tipo— activen el fallback DTMF.
    sess.endpoint_ctrl.on_successful_response()

    # ═══════════════════════════════════════════════════════════════
    #  MÁQUINA DE ESTADOS
    # ═══════════════════════════════════════════════════════════════

    # ── ESTADO: waiting_origin ────────────────────────────────────
    if sess.state == STATE_WAITING_ORIGIN:

        # Fuente de la extracción: human_ref/local match = confiable (catálogo);
        # LLM/raw = NO confiable → se valida con looks_like_place antes de aceptar.
        trusted_origin = False

        # 0. Resolución de ambigüedad pendiente (grupo multi-sede, data-driven).
        #    La respuesta se resuelve contra las sedes del grupo (scope), usando
        #    sus tokens distintivos — sin reglas hardcodeadas tipo "centro"/"norte".
        if sess.pending_disambiguation:
            _pd = sess.pending_disambiguation
            m_dis = resolve_location_entity(texto_usuario, scope=_pd.get("candidates"))
            if decide(m_dis) == Decision.ACCEPT and m_dis.canonical:
                origen = m_dis.canonical
                trusted_origin = True
                sess.pending_disambiguation = None
                logger.info(f"[ORIGIN] disambiguated → {origen!r}")
            else:
                msg = _pd.get("question") or "¿Cuál de las opciones?"
                sess.last_message = msg
                return _r(msg, short=True)

        # 1. MEDIUM_QUALITY ya resolvió local match — usar directamente.
        if not trusted_origin and _medium_local_resolved:
            origen = texto_usuario
            trusted_origin = True
            logger.info(f"[ORIGIN] Local match (medium pre-resolved): {origen!r}")

        # 2. Resolución tipada central (referencias humanas + barrios + landmarks).
        #    Una sola política decide; precisión sobre recall.
        elif not trusted_origin:
            m = resolve_location_entity(texto_usuario)
            d = decide(m)
            if d == Decision.AMBIGUOUS and m.canonical:
                sess.pending_disambiguation = {
                    "candidates": list(m.disambiguation_candidates),
                    "question":   _disambiguation_question(m.disambiguation_candidates),
                }
                msg = sess.pending_disambiguation["question"]
                sess.last_message = msg
                logger.info(f"[ORIGIN] ambiguous ({m.canonical!r}) — asking options")
                return _r(msg, short=True)
            elif d in (Decision.ACCEPT, Decision.CONFIRM) and m.canonical:
                # ACCEPT/CONFIRM: ambos son lugares reales del catálogo; el estado
                # CONFIRMING_ORIGIN verifica con el usuario antes de crear el viaje.
                origen = m.canonical
                trusted_origin = True
                logger.info(
                    f"[ORIGIN] matcher=resolver type={m.match_type.name} "
                    f"score={m.confidence:.3f} decision={d.value} "
                    f"reason=catalog_match → {origen!r}"
                )
            elif is_filler(texto_usuario):
                # Saludo / cortesía / relleno: NO es una ubicación. No llamar al
                # LLM (alucina la ciudad). Pedir repetición directamente.
                logger.info(
                    f"[ORIGIN] matcher=resolver type=NONE score=0.000 "
                    f"decision=reject reason=filler/greeting ({texto_usuario!r}) → ask repeat"
                )
                sess.retry_count += 1
                sess.endpoint_ctrl.on_retry()
                sess.origen_text = None
                if sess.retry_count >= 2:
                    sess.last_message = DTMF_MENU_MESSAGE
                    return _r(DTMF_MENU_MESSAGE, dtmf=True)
                msg = "No logré identificar la ubicación. ¿Podrías repetirla?"
                sess.last_message = msg
                return _r(msg)
            else:
                # REJECT (no relleno): puede ser un lugar novel que el catálogo no
                # tiene. Extracción LLM (con guard anti-ciudad) y, si no parece
                # lugar, el gate anti-basura lo rechaza (cae a geocoder).
                origen_llm, hint = await extract_pickup_address(texto_usuario)
                origen = (origen_llm or texto_usuario or "").strip()
                logger.info(
                    f"[ORIGIN] matcher=llm score=n/a decision={d.value} "
                    f"reason=resolver_reject_nonfiller ({texto_usuario!r}) → {origen!r}"
                )

        # ── Gate anti-basura: si la extracción NO viene del catálogo/referencia,
        #    exigir que parezca un lugar real (calle/número/barrio/landmark).
        #    Bloquea alucinaciones del LLM tipo "tu cuenta", "fuerza", "dos".
        #    También bloquea el nombre de la ciudad/región como "punto de recogida"
        #    (defensa-en-profundidad contra el fallback geográfico implícito).
        _origen_is_city = strip_accents((origen or "").lower().strip()) in _CITY_LEVEL_NAMES
        if not trusted_origin and (_origen_is_city or not (
            looks_like_place(origen) or looks_like_place(texto_usuario)
        )):
            if _origen_is_city:
                logger.info(f"[ORIGIN] rejected city-level extraction {origen!r} (no default city)")
                origen = ""
            # Antes de rechazar: rescate agresivo. El texto puede venir muy mal
            # transcrito (conf alta, palabras erradas). Deslizamos ventanas sobre
            # el texto y el original crudo buscando CUALQUIER fragmento que matchee
            # el catálogo. Si algo aparece, lo tomamos como hipótesis confiable.
            recovered = _aggressive_place_recovery(texto_usuario) or _aggressive_place_recovery(texto_original)
            if recovered:
                origen = recovered
                trusted_origin = True
                logger.info(f"[ORIGIN] Recovered via aggressive matching: {origen!r}")
            else:
                # Nada matcheó: contar como fallo y, tras 2, ofrecer menú DTMF en
                # vez de seguir pidiendo que repita sobre el mismo audio malo.
                sess.retry_count += 1
                sess.endpoint_ctrl.on_retry()
                sess.origen_text = None
                logger.info(
                    f"[ORIGIN] Rejected non-place extraction (retry #{sess.retry_count}): "
                    f"{origen!r} (raw={texto_usuario!r})"
                )
                if sess.retry_count >= 2:
                    sess.last_message = DTMF_MENU_MESSAGE
                    return _r(DTMF_MENU_MESSAGE, dtmf=True)
                msg = "Perdona, no te capté el lugar. Dime solo el barrio o la dirección donde te recogemos."
                sess.last_message = msg
                return _r(msg)

        # Origen aceptado (confiable o recuperado/validado) → éxito: resetear el
        # contador de fallos para que el fallback DTMF parta de cero la próxima vez.
        sess.retry_count = 0

        # Normalizar — aplicar normalize_colombian_address para limpiar artefactos STT
        # (ej: "4a ae" → "4ae", "carrera" → "Cra.", etc.) antes de confirmar
        if origen:
            col_norm = normalize_colombian_address(origen)
            if col_norm and len(col_norm) >= 3:
                origen = col_norm
            else:
                norm = normalize_address(origen)
                if norm and len(norm) > len(origen) * 0.4:
                    origen = norm

        sess.origen_text = origen
        sess.memory.add_location_mention(origen)
        logger.info(f"[ORIGIN] Extracted: {origen!r}")

        if not origen or len(origen) < 2:
            msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)
            sess.last_message = msg
            return _r(msg)

        # Determinar si es dirección de calle
        is_street = bool(
            re.search(r"(?:calle|carrera|cl|cra|kr|kra|Cra|Cl)\s*\.?\s*\d+", origen)
        )

        if is_street:

            # Intentar geocodificar con el nuevo pipeline para descubrir barrio
            try:
                geo_result = await run_pipeline(origen, attempt=1)
                from core.geo_types import ResolutionStatus

                if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
                    barrio_name = geo_result.selected.neighborhood
                    if barrio_name:
                        sess.origen_barrio = barrio_name
                        sess.geo_origin.reset()
                        sess.state = STATE_CONFIRMING_ORIGIN
                        msg = f"Te repito: tu dirección es {origen}, barrio {barrio_name}. ¿Me confirmas?"
                        sess.last_message = msg
                        return _r(msg, short=True)
                    # Resuelto pero sin barrio en metadata → confirmar sin barrio
                    sess.geo_origin.reset()

                elif geo_result.status == ResolutionStatus.CONTEXT_GATHERING:
                    sess.geo_origin.pending = geo_result
                    sess.geo_origin.original_query = origen
                    sess.geo_origin.attempt = 1
                    sess.state = STATE_WAITING_GEO_CONTEXT
                    geo_question = geo_result.disambiguation_question or "¿En qué barrio o sector queda?"
                    msg = geo_question
                    sess.last_message = msg
                    return _r(msg)

                elif geo_result.status == ResolutionStatus.NEEDS_DISAMBIGUATION:
                    # Google devolvió 2+ opciones reales — preguntar al usuario
                    sess.geo_origin.pending = geo_result
                    sess.geo_origin.original_query = origen
                    sess.geo_origin.attempt = 1
                    sess.state = STATE_WAITING_GEO_CONTEXT
                    geo_question = (
                        geo_result.disambiguation_question
                        or "¿En cuál barrio queda esa dirección?"
                    )
                    msg = geo_question
                    sess.last_message = msg
                    return _r(msg, short=True)

            except Exception as exc:
                logger.warning(f"[ORIGIN] Pipeline geocode failed: {exc}")

            # Sin barrio encontrado: confirmar de todas formas
            sess.state = STATE_CONFIRMING_ORIGIN
            msg = f"te repito: tu dirección es {origen}. ¿Me confirmas?"
            sess.last_message = msg
            return _r(msg, short=True)

        # Lugar nombrado: ir a confirmación
        sess.state = STATE_CONFIRMING_ORIGIN
        msg = f"te repito: el punto de recogida es {origen}. ¿Me confirmas?"
        sess.last_message = msg
        return _r(msg, short=True)

    # ── ESTADO: confirming_origin ─────────────────────────────────
    if sess.state == STATE_CONFIRMING_ORIGIN:
        is_yes = _parse_si_no(texto_usuario)

        if is_yes is True:
            sess.memory.last_confirmed_origin = sess.origen_text
            
            # Check if user already provided destination in this turn (e.g., "sí, y voy para el Valle del Ortigal")
            dest_candidate = None
            cleaned_dest_input = re.sub(r'^(?:sí|si|bien|correcto|exacto|ok|dale|claro)[,\s]*(?:y\s+)?', '', texto_usuario, flags=re.IGNORECASE).strip()
            
            if len(cleaned_dest_input) > 2:
                m_dest = resolve_location_entity(cleaned_dest_input)
                d_dest = decide(m_dest)
                if d_dest in (Decision.ACCEPT, Decision.CONFIRM) and m_dest.canonical:
                    dest_candidate = m_dest.canonical
                elif d_dest == Decision.AMBIGUOUS:
                    dest_candidate = None  # se pregunta el destino por el flujo normal
                else:
                    dest_llm, _ = await extract_destination_address(cleaned_dest_input)
                    if dest_llm:
                        dest_candidate = dest_llm

            if dest_candidate and ASK_DESTINATION:
                norm = normalize_address(dest_candidate)
                if norm and len(norm) > len(dest_candidate) * 0.4:
                    dest_candidate = norm
                sess.destino_text = dest_candidate
                sess.memory.add_location_mention(dest_candidate)
                sess.state = STATE_CONFIRMING_DEST
                msg = f"¿Te llevo a {dest_candidate}? Responde sí o no."
                sess.last_message = msg
                logger.info(f"[CONFIRM_ORIGIN] Destination pre-extracted, confirming: {dest_candidate!r}")
                return _r(msg, short=True)

            if not ASK_DESTINATION:
                sess.state = STATE_CREATING_SERVICE
                return _r("", listen=False, proc="Un momento por favor...")

            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = f"Listo {sess.origen_text}. ¿Me dices a dónde vas, o se lo cuentas al conductor?"
            sess.last_message = msg
            return _r(msg, short=True)

        if is_yes is False:
            # Extraer corrección inline ("no, calle 16 # 3CE-41")
            rest = re.sub(
                r"^(?:no|nones|negativo|nop)[,\s]*",
                "",
                texto_usuario,
                flags=re.IGNORECASE,
            ).strip()
            if len(rest) > 4:
                # Si la corrección es una dirección de calle → re-geocodificar inline
                rest_is_street = bool(re.search(
                    r'(?:calle|carrera|cl|cra|kr|kra)\s*[\d]', rest.lower()
                ))
                if rest_is_street:
                    new_origen = normalize_colombian_address(rest)
                    if not new_origen or len(new_origen) < 3:
                        new_origen = rest
                    sess.origen_text   = new_origen
                    sess.origen_barrio = None
                    sess.geo_origin.reset()
                    logger.info(f"[CONFIRM_ORIGIN] Correction detected → re-geocoding: {new_origen!r}")
                    try:
                        from core.geo_types import ResolutionStatus
                        geo_result = await run_pipeline(new_origen, attempt=1)
                        if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
                            barrio_name = geo_result.selected.neighborhood
                            sess.origen_barrio = barrio_name
                            barrio_str = f", barrio {barrio_name}" if barrio_name else ""
                            msg = f"Entendido. ¿La dirección es {new_origen}{barrio_str}? ¿Correcto?"
                        elif geo_result.status in (
                            ResolutionStatus.NEEDS_DISAMBIGUATION,
                            ResolutionStatus.CONTEXT_GATHERING,
                        ):
                            sess.geo_origin.pending = geo_result
                            sess.geo_origin.original_query = new_origen
                            sess.geo_origin.attempt = 1
                            sess.state = STATE_WAITING_GEO_CONTEXT
                            msg = (
                                geo_result.disambiguation_question
                                or "¿En qué barrio o sector queda esa dirección?"
                            )
                            sess.last_message = msg
                            return _r(msg, short=False)
                        else:
                            msg = f"Entendido. ¿La dirección es {new_origen}? ¿Correcto?"
                    except Exception as exc:
                        logger.warning(f"[CONFIRM_ORIGIN] re-geocode error: {exc}")
                        msg = f"Entendido. ¿La dirección es {new_origen}? ¿Correcto?"
                    sess.state    = STATE_CONFIRMING_ORIGIN
                    sess.last_message = msg
                    return _r(msg, short=True)
                texto_usuario = rest
                _explicit_correction = True  # el usuario dijo "No" → nunca restatement
                # Caemos al match local/LLM abajo
            else:
                sess.state = STATE_WAITING_ORIGIN
                sess.origen_barrio = None
                msg = "Entendido. ¿Dónde queda exactamente? Puedes darme el barrio o la dirección completa."
                sess.last_message = msg
                return _r(msg)
        else:
            _explicit_correction = False  # respuesta ambigua: puede ser restatement

        # ── Respuesta ni sí ni no (o corrección inline sin dirección de calle):
        #    el usuario probablemente re-dicta o corrige el origen.
        #    Si dijo "No, X" (_explicit_correction=True) → SIEMPRE corrección,
        #    nunca restatement (aunque X fuzzy-matchee con el origen anterior).
        _hr_corr = resolve_human_reference(texto_usuario)
        local = (_hr_corr["canonical"] if (_hr_corr and _hr_corr.get("canonical")) else None) or _try_local_match(texto_usuario)
        if local:
            _cur = strip_accents((sess.origen_text or "").lower().strip())
            _new = strip_accents(local.lower().strip())
            _same = (not _explicit_correction) and bool(_cur) and (
                _cur == _new
                or bool(fuzzy_match_location(_new, [_cur], threshold=0.80))
            )

            if _same:
                # Re-statement del mismo origen → confirmación implícita
                logger.info(f"[CONFIRM_ORIGIN] Restatement of same origin → confirm: {local!r}")
                sess.memory.last_confirmed_origin = sess.origen_text or local
                if not ASK_DESTINATION:
                    sess.state = STATE_CREATING_SERVICE
                    return _r("", listen=False, proc="Un momento por favor...")
                sess.state = STATE_WAITING_DEST_OR_SKIP
                msg = f"Listo, te recogemos en {sess.origen_text or local}. ¿A dónde vas, o se lo cuentas al conductor?"
                sess.last_message = msg
                return _r(msg, short=True)

            # Origen distinto → el bot había oído mal; re-confirmar el corregido
            logger.info(f"[CONFIRM_ORIGIN] Corrected origin via local match: {sess.origen_text!r} → {local!r}")
            sess.origen_text   = local
            sess.origen_barrio = None
            sess.geo_origin.reset()
            sess.memory.add_location_mention(local)
            sess.state = STATE_CONFIRMING_ORIGIN
            msg = f"Ah, {local}. ¿Te recogemos ahí? Di sí para confirmar."
            sess.last_message = msg
            return _r(msg, short=True)

        # No se pudo parsear la respuesta: si ya tenemos origen con barrio conocido,
        # tratar la respuesta ambigua como confirmación implícita para no hacer
        # repetir al usuario innecesariamente.
        # PERO: si el texto parece un lugar/dirección, es una CORRECCIÓN que no
        # logramos matchear contra el catálogo — nunca una confirmación. Confirmar
        # aquí producía falsos "sí" (el usuario dijo un lugar, no aceptó el origen).
        _ambiguous_is_place = looks_like_place(texto_usuario)
        if sess.origen_text and sess.origen_barrio and not _ambiguous_is_place:
            logger.info(f"[CONFIRM_ORIGIN] Ambiguous response — accepting implicit confirm: {texto_usuario!r}")
            sess.memory.last_confirmed_origin = sess.origen_text
            if not ASK_DESTINATION:
                sess.state = STATE_CREATING_SERVICE
                return _r("", listen=False, proc="Un momento por favor...")
            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = f"Listo, te recogemos en {sess.origen_text}. ¿Me dices a dónde vas, o se lo cuentas al conductor?"
            sess.last_message = msg
            return _r(msg, short=True)

        msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)
        if sess.origen_barrio:
            msg = f"¿Confirmas que estás por {sess.origen_barrio}? Di sí, o dime tu barrio."
        sess.last_message = msg
        return _r(msg, short=True)

    # ── ESTADO: waiting_geo_context ──────────────────────────────
    # Pipeline devolvió CONTEXT_GATHERING — esperando barrio/referencia del usuario
    if sess.state == STATE_WAITING_GEO_CONTEXT:
        from core.geo_types import ResolutionStatus

        pending   = sess.geo_origin.pending
        orig_q    = sess.geo_origin.original_query
        attempt   = sess.geo_origin.attempt

        # ── Re-statement de dirección ──
        # Si el usuario vuelve a decir una dirección completa (calle/carrera +
        # número) en vez de responder el barrio, es una corrección NUEVA — no
        # hay que enriquecer la query vieja (eso generaba basura tipo
        # "Cl. 16 # 3C-6, es la calle 16 36"). Re-geocodificamos desde cero el
        # span limpio de la nueva dirección.
        restated_is_address = bool(
            _STREET_KW_RE.search(texto_usuario) and re.search(r"\d", texto_usuario)
        )
        if restated_is_address:
            new_span   = _extract_address_span(_strip_preamble(texto_usuario))
            new_origen = normalize_colombian_address(new_span) or new_span
            logger.info(f"[GEO_CONTEXT] Address re-stated → fresh geocode: {new_origen!r}")
            sess.origen_text   = new_origen
            sess.origen_barrio = None
            sess.geo_origin.reset()
            geo_result = await run_pipeline(new_origen, attempt=1)

            if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
                barrio_name = geo_result.selected.neighborhood
                if barrio_name:
                    sess.origen_barrio = barrio_name
                sess.state = STATE_CONFIRMING_ORIGIN
                barrio_str = f", barrio {barrio_name}" if barrio_name else ""
                msg = f"te repito: tu dirección es {new_origen}{barrio_str}. ¿Me confirmas?"
                sess.last_message = msg
                return _r(msg, short=True)

            if geo_result.status == ResolutionStatus.NEEDS_DISAMBIGUATION:
                sess.geo_origin.pending = geo_result
                sess.geo_origin.original_query = new_origen
                sess.geo_origin.attempt = 1
                msg = geo_result.disambiguation_question or "¿En cuál barrio queda esa dirección?"
                sess.last_message = msg
                return _r(msg, short=True)

            # CONTEXT_GATHERING u otro → seguir pidiendo barrio para la nueva dir.
            sess.geo_origin.pending = geo_result
            sess.geo_origin.original_query = new_origen
            sess.geo_origin.attempt = geo_result.attempt
            msg = geo_result.disambiguation_question or "¿En qué barrio o sector queda?"
            sess.last_message = msg
            return _r(msg)

        geo_result = await handle_user_context(
            user_text=texto_usuario,
            pending=pending,
            original_query=orig_q,
            attempt=attempt,
        )
        sess.geo_origin.attempt = geo_result.attempt

        if geo_result.status == ResolutionStatus.RESOLVED and geo_result.selected:
            # Barrio: preferir metadata de Google; si falta, usar la aclaración
            # del usuario (la cola de la query enriquecida tras el orig_q).
            barrio_name = geo_result.selected.neighborhood
            if not barrio_name and geo_result.query and "," in geo_result.query:
                barrio_name = geo_result.query.rsplit(",", 1)[-1].strip() or None
            if barrio_name:
                sess.origen_barrio = barrio_name
            # origen_text se mantiene como la dirección de calle original limpia
            # (orig_q). El barrio se muestra/pasa por separado para evitar
            # duplicarlo en la confirmación.
            sess.origen_text = orig_q
            sess.geo_origin.reset()
            sess.state = STATE_CONFIRMING_ORIGIN
            barrio_str = f", barrio {barrio_name}" if barrio_name else ""
            msg = f"te repito: tu dirección es {orig_q}{barrio_str}. ¿Me confirmas?"
            sess.last_message = msg
            return _r(msg, short=True)

        if geo_result.status == ResolutionStatus.CONTEXT_GATHERING:
            sess.geo_origin.pending = geo_result
            geo_question = geo_result.disambiguation_question or "¿En qué barrio o referencia cercana queda?"
            sess.last_message = geo_question
            return _r(geo_question)

        # FAILED o máximo de intentos
        sess.geo_origin.reset()
        sess.state = STATE_CONFIRMING_ORIGIN  # confirmar sin coordenadas
        display = sess.origen_text or orig_q
        msg = f"te repito: el punto de recogida es {display}. ¿Me confirmas?"
        sess.last_message = msg
        return _r(msg, short=True)

    # ── ESTADO: waiting_dest_or_skip ─────────────────────────────
    if sess.state == STATE_WAITING_DEST_OR_SKIP:

        # 0. Desambiguación de destino pendiente (grupo multi-sede, data-driven).
        if sess.pending_disambiguation:
            _pd = sess.pending_disambiguation
            m_dis = resolve_location_entity(texto_usuario, scope=_pd.get("candidates"))
            if decide(m_dis) == Decision.ACCEPT and m_dis.canonical:
                sess.pending_disambiguation = None
                dest = m_dis.canonical
                sess.destino_text = dest
                sess.memory.add_location_mention(dest)
                sess.retry_count = 0
                sess.state = STATE_CONFIRMING_DEST
                msg = f"¿Te llevamos a {dest}? Responde sí o corriges el destino."
                sess.last_message = msg
                logger.info(f"[DEST] disambiguated → {dest!r}")
                return _r(msg, short=True)
            msg = _pd.get("question") or "¿Cuál de las opciones?"
            sess.last_message = msg
            return _r(msg, short=True)

        # Corrección de origen
        if _is_correction_request(texto_usuario):
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            sess.origen_barrio = None
            msg = (
                "¡Claro, corregimos! Cuéntame, ¿dónde te recogemos?"
            )
            sess.last_message = msg
            return _r(msg)

        # Declinar destino
        is_no = _parse_si_no(texto_usuario)
        t_lower = texto_usuario.lower()
        indirect_decline = any(
            p in t_lower
            for p in [
                "no tengo",
                "no sé",
                "no se",
                "sin destino",
                "al conductor",
                "el conductor",
                "le digo al",
                "le digo el",
                "ya le digo",
                "donde sea",
                "no importa",
                "allá le digo",
                "alla le digo",
                "después le digo",
            ]
        )

        if is_no is False or indirect_decline:
            sess.state = STATE_CREATING_SERVICE
            return _r("", listen=False, proc="Un momento por favor...")

        # Tratar como destino
        dest_preambles = [
            r"^(?:me\s+dirijo\s+(?:hacia|a|al|para))\s+",
            r"^(?:voy\s+(?:para|hacia|a|al|pa))\s+",
            r"^(?:llévame\s+(?:a|al|hacia|para|pa))\s+",
            r"^(?:hacia|para|al|a)\s+",
            r"^(?:mi\s+destino\s+es)\s+",
            r"^(?:deja(me)?\s+(?:en|por))\s+",
        ]
        dest_text = texto_usuario
        for pat in dest_preambles:
            dest_text = re.sub(pat, "", dest_text, flags=re.IGNORECASE).strip()

        # Resolución de destino vía política tipada central.
        if _medium_local_resolved:
            dest = texto_usuario
            logger.info(f"[DEST] Local match (medium pre-resolved): {dest!r}")
        else:
            m_dest = resolve_location_entity(dest_text)
            d_dest = decide(m_dest)
            if d_dest == Decision.AMBIGUOUS and m_dest.canonical:
                # Sede multi-opción → preguntar; la respuesta se resuelve arriba (0.)
                sess.pending_disambiguation = {
                    "candidates": list(m_dest.disambiguation_candidates),
                    "question":   _disambiguation_question(m_dest.disambiguation_candidates),
                }
                msg = sess.pending_disambiguation["question"]
                sess.last_message = msg
                logger.info(f"[DEST] ambiguous ({m_dest.canonical!r}) — asking options")
                return _r(msg, short=True)
            elif d_dest in (Decision.ACCEPT, Decision.CONFIRM) and m_dest.canonical:
                dest = m_dest.canonical
                logger.info(
                    f"[DEST] matcher=resolver type={m_dest.match_type.name} "
                    f"score={m_dest.confidence:.3f} decision={d_dest.value} → {dest!r}"
                )
            elif is_filler(dest_text):
                # Saludo / cortesía como "destino": no es ubicación. Pedir repetir.
                logger.info(f"[DEST] filler/greeting ({dest_text!r}) → ask repeat")
                msg = "No logré identificar el destino. ¿Podrías repetirlo?"
                sess.last_message = msg
                return _r(msg, short=True)
            else:
                dest_llm, _ = await extract_destination_address(dest_text)
                dest = (dest_llm or dest_text or "").strip()
                if strip_accents((dest or "").lower().strip()) in _CITY_LEVEL_NAMES:
                    logger.info(f"[DEST] rejected city-level {dest!r} (no default city)")
                    dest = ""

        if dest:
            norm = normalize_address(dest)
            if norm and len(norm) > len(dest) * 0.4:
                dest = norm

        sess.destino_text = dest
        sess.memory.add_location_mention(dest)
        sess.retry_count = 0  # destino aceptado → reset de fallos
        logger.info(f"[DEST] Extracted: {dest!r}")

        if not dest or len(dest) < 2:
            msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)
            sess.last_message = msg
            return _r(msg, short=True)

        # Confirm destination before creating service
        sess.state = STATE_CONFIRMING_DEST
        msg = f"¿Te llevamos a {dest}? Responde sí o corriges el destino."
        sess.last_message = msg
        return _r(msg, short=True)

    # ── ESTADO: confirming_dest ─────────────────────────────────────
    if sess.state == STATE_CONFIRMING_DEST:
        is_yes = _parse_si_no(texto_usuario)

        if is_yes is True:
            # User confirmed destination → create service
            sess.state = STATE_CREATING_SERVICE
            return _r("", listen=False, proc="Un momento por favor...")

        if is_yes is False:
            # User rejected → ask again for destination
            sess.destino_text = None
            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = "Sin problema. ¿Cuál es tu destino? Dímelo o di no si le cuentas al conductor."
            sess.last_message = msg
            return _r(msg, short=False)

        # Try to match a correction inline (e.g. "no, al ortigal")
        rest = re.sub(
            r"^(?:no|nones|negativo|nop)[,\s]*",
            "",
            texto_usuario,
            flags=re.IGNORECASE,
        ).strip()
        if len(rest) > 3:
            # Treat the remainder as the corrected destination
            local_dest = _try_local_match(rest)
            if local_dest:
                dest_corr = local_dest
            else:
                dest_corr_llm, _ = await extract_destination_address(rest)
                dest_corr = (dest_corr_llm or rest).strip()
            if dest_corr and len(dest_corr) >= 2:
                norm = normalize_address(dest_corr)
                if norm and len(norm) > len(dest_corr) * 0.4:
                    dest_corr = norm
                sess.destino_text = dest_corr
                sess.memory.add_location_mention(dest_corr)
                msg = f"¿Te llevamos a {dest_corr}? Di sí para confirmar o corriges de nuevo."
                sess.last_message = msg
                return _r(msg, short=True)

        # Could not parse: ask again
        dest_name = sess.destino_text or "ese destino"
        msg = f"Disculpa, no logré entenderte. ¿Confirmas que vas a {dest_name}? Di sí o no."
        sess.last_message = msg
        return _r(msg, short=True)

    # ── Fallback ──────────────────────────────────────────────────
    return _r("¡Gracias por llamar! ¡Que te vaya bien!", listen=False, hangup=True)



def get_active_session_count() -> int:
    return len(_SESSIONS)
