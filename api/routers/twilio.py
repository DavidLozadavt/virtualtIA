"""
gateway/twilio_voice.py — Twilio voice gateway for IntelliTaxi.

Replaces the Flask IA-CALL server. Endpoints:
  /voice          → Initial greeting, starts Gather for origin
  /process_speech → Processes each speech turn via state machine
  /health         → Health check for the Laravel proxy

State machine:
  waiting_origin → waiting_dest_decision → waiting_destination → finished
                                         └→ finished (sin destino)

Laravel proxies Twilio webhooks:
  POST /api/twilio/ia/voice        → POST http://lyra:8099/voice
  POST /api/twilio/ia/process-speech → POST http://lyra:8099/process_speech
"""

import asyncio
import json
import logging
import os
import re
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request, Form
from fastapi.responses import Response

from core.address_utils import (
    _clean_stt_text,
    _normalize_text,
    _spanish_phonetic_key,
    _correct_speech,
    _strip_preamble,
    _parse_si_no,
    _is_correction_request,
    _is_repeat_request,
    _try_local_match,
    normalize_address,
    _nominatim_geocode,
    _nominatim_geocode_async,
)
from core.llm_utils import (
    get_openai_client as _get_openai,
    get_async_openai_client as _get_async_openai,
    get_model as _get_model,
    extract_json_object as _extract_json_object,
    call_llm,
)

logger = logging.getLogger("lyra.twilio_voice")
voice_router = APIRouter()

# ── Configuration (lazy-loaded to ensure dotenv runs first) ───────────────────


def _cfg(key: str, default: str = "") -> str:
    """Read env var lazily (after dotenv has loaded)."""
    return os.getenv(key, default).strip()


def _cfg_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _twilio_voice() -> str:
    return _cfg("TWILIO_VOICE", "Polly.Andres-Neural")


def _twilio_speech_timeout() -> str:
    return _cfg("TWILIO_SPEECH_TIMEOUT", "1.0")


def _twilio_gather_timeout() -> int:
    return _cfg_int("TWILIO_GATHER_TIMEOUT", 25)


def _max_silence() -> int:
    return _cfg_int("MAX_SILENCE_BEFORE_HANGUP", 3)


def _gather_action_url() -> str:
    return _cfg("TWILIO_GATHER_ACTION_URL", "")


# ── Session management ────────────────────────────────────────────────────────

STATE_WAITING_ORIGIN = "waiting_origin"
STATE_CONFIRMING_ORIGIN = "confirming_origin"  # confirm street address with barrio
STATE_WAITING_DEST_OR_SKIP = (
    "waiting_dest_or_skip"  # user gives destination or says "no"
)
STATE_SERVICE_CREATED = "service_created"
STATE_CREATING_SERVICE = "creating_service"
STATE_FINISHED = "finished"

SESSION_TTL_SEC = int(os.getenv("CALL_SESSION_TTL_SEC", "7200"))

_SESSION_LOCK = threading.Lock()
_SESSIONS: Dict[str, "CallSession"] = {}


@dataclass
class CallSession:
    call_sid: str
    state: str = STATE_WAITING_ORIGIN
    origen_text: Optional[str] = None
    origen_barrio: Optional[str] = None  # nearest barrio for confirmation
    destino_text: Optional[str] = None
    service_created: bool = False
    silence_count: int = 0
    last_message: str = ""  # track last bot message for "repeat" requests
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


def _prune_sessions() -> None:
    now = time.time()
    dead = [k for k, s in _SESSIONS.items() if now - s.updated_at > SESSION_TTL_SEC]
    for k in dead:
        _SESSIONS.pop(k, None)


def get_session(call_sid: str) -> CallSession:
    with _SESSION_LOCK:
        _prune_sessions()
        if call_sid not in _SESSIONS:
            _SESSIONS[call_sid] = CallSession(call_sid=call_sid)
        s = _SESSIONS[call_sid]
        s.touch()
        return s


def reset_session(call_sid: str) -> None:
    with _SESSION_LOCK:
        _SESSIONS.pop(call_sid, None)


# ── TwiML helpers ─────────────────────────────────────────────────────────────

# ── Twilio speech hints ───────────────────────────────────────────────────────
# These help Twilio's speech recognizer prefer barrio names over common words.
# Limit: ~500 words. We include the most critical/confusable names.

_TWILIO_HINTS = (
    "calle,carrera,barrio,con,esquina,norte,sur,número,"
    # Barrios that are commonly misrecognized
    "los sauces,sauces,maría oriente,maria oriente,alfonso lópez,alfonso lopez,"
    "pandiguando,yanaconas,campanario,la esmeralda,esmeralda,belalcázar,"
    "los comuneros,comuneros,pueblillo,yambitará,camilo torres,"
    "valle del ortigal,ortigal,polideportivo,valle vertical,"
    # Major barrios all comunas
    "modelo,loma linda,prados del norte,santa clara,pubenza,el recuerdo,"
    "bello horizonte,el tablazo,la primavera,villa del norte,san ignacio,"
    "los ángeles,pinares,san fernando,bolívar,ciudad jardín,periodistas,"
    "los hoyos,la estancia,villa mercedes,el prado,los álamos,la pamba,"
    "berlín,suizo,las ferias,la campiña,santa mónica,la floresta,los andes,"
    "valparaíso,primero de mayo,loma de la virgen,sindical,calicanto,limonar,"
    "las palmas,nazaret,chapinero,nuevo popayán,la libertad,santa librada,"
    "el libertador,el triunfo,popular,llano largo,kennedy,la sombrilla,"
    "lomas de granada,la capitana,cinco de abril,maría occidente,"
    "pomona,el uvo,las américas,santa rosa,los tejares,el cadillal,"
    "retiro alto,la colina,versalles,la paz sur,jorge eliécer gaitán,"
    "villa del viento,torres del río,provitec,el jardín,zaguan,"
    "rincón de la estancia,la campiña,el plateado,la alameda,"
    # Landmarks
    "centro,parque caldas,torre del reloj,puente del humilladero,"
    "catedral,universidad del cauca,unicauca,sena,"
    "hospital san josé,clínica la estancia,terminal,aeropuerto,"
    "galería,estadio,coliseo,morro de tulcán,polideportivo,"
    "centro comercial campanario,terra plaza,anarkos,éxito,"
    # Corregimientos
    "julumito,la yunga,calibío,poblazón,las guacas,pisojé,"
    # City name
    "popayán"
)


def _generate_say_twiml(msg: str) -> str:
    """Generate TwiML <Say> with Twilio's neural voice — ZERO extra latency.
    
    This is the fastest possible TTS path:
    - No external WebSocket (edge-tts saves ~200ms)
    - No extra HTTP round-trip (Twilio doesn't need to fetch /tts/ saves ~200ms)
    - Twilio processes <Say> server-side, audio starts in milliseconds
    """
    voice = _twilio_voice()
    return f'<Say voice="{voice}" language="es-MX">{_xml_escape(msg)}</Say>'


def _twiml_gather_message(msg: str, action_url: str, short_timeout: bool = False) -> str:
    """Build TwiML XML: <Gather> with <Say> (instant), then <Redirect> fallback on no input."""
    speech_timeout = _twilio_speech_timeout()
    gather_timeout = _twilio_gather_timeout()
    
    if short_timeout:
        speech_timeout = "0.8"  # Force 0.8 seconds for yes/no answers

    speech_timeout_attr = f' speechTimeout="{speech_timeout}"'

    say_twiml = _generate_say_twiml(msg)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech" language="es-CO"'
        f"{speech_timeout_attr}"
        f' timeout="{gather_timeout}"'
        f' action="{action_url}" method="POST"'
        f' profanityFilter="false"'
        f' hints="{_TWILIO_HINTS}">'
        f"{say_twiml}"
        "</Gather>"
        f'<Redirect method="POST">{action_url}</Redirect>'
        "</Response>"
    )


def _twiml_say_hangup(msg: str) -> str:
    """Build TwiML XML: <Say> then <Hangup>."""
    say_twiml = _generate_say_twiml(msg)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{say_twiml}"
        "<Hangup/>"
        "</Response>"
    )


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _twiml_response(xml: str) -> Response:
    return Response(content=xml, media_type="text/xml; charset=utf-8")


# ── Speech quality & normalization (Problem 2 + 3) ────────────────────────────

# Payanés contractions / initial-syllable drops
_PAYANES_CONTRACTIONS = {
    "tá": "está",
    "toy": "estoy",
    "tamos": "estamos",
    "taba": "estaba",
    "pa": "para",
    "pal": "para el",
    "pa'l": "para el",
    "onde": "donde",
    "ónde": "donde",
    "nonces": "entonces",
    "tonces": "entonces",
    "'tonces": "entonces",
    "l centro": "el centro",
    "'l centro": "el centro",
    "quiar": "aquiar",
}

# Barge-in: NexiService greeting fragments that Twilio may capture at start of user speech
_BARGEIN_FRAGMENTS = [
    r"^hola\s+soy\s+nexo[,.]?\s*",
    r"^soy\s+nexo[,.]?\s*",
    r"^tu\s+asistente\s+de\s+taxi[,.]?\s*",
    r"^cu[eé]ntame[,.]?\s*",
]

# Number words to digits mapping for mixed-number normalization
_NUM_WORDS = {
    "uno": "1",
    "una": "1",
    "dos": "2",
    "tres": "3",
    "cuatro": "4",
    "cinco": "5",
    "seis": "6",
    "siete": "7",
    "ocho": "8",
    "nueve": "9",
    "diez": "10",
    "once": "11",
    "doce": "12",
    "trece": "13",
    "catorce": "14",
    "quince": "15",
    "dieciséis": "16",
    "dieciseis": "16",
    "diecisiete": "17",
    "dieciocho": "18",
    "diecinueve": "19",
    "veinte": "20",
    "veintiuno": "21",
    "veintidós": "22",
    "veintidos": "22",
    "veintitrés": "23",
    "veintitres": "23",
    "veinticuatro": "24",
    "veinticinco": "25",
    "veintiséis": "26",
    "veintiseis": "26",
    "veintisiete": "27",
    "veintiocho": "28",
    "veintinueve": "29",
    "treinta": "30",
    "cuarenta": "40",
    "cincuenta": "50",
    "sesenta": "60",
    "setenta": "70",
    "ochenta": "80",
    "noventa": "90",
    "cien": "100",
}

# Pre-compiled fused-word pattern: "calle/carrera/barrio" glued to number/name
_FUSED_STREET_RE = re.compile(
    r"\b(calle|carrera|barrio)(\d+|[a-záéíóúñ]{3,})",
    re.IGNORECASE,
)


def normalize_raw_stt(text: str, confidence: float = 1.0) -> str:
    """Pre-process raw STT text BEFORE regex or LLM matching.

    Handles:
      a) Fused words from fast speech  ("callequince" → "calle quince")
      b) Payanés contractions          ("tá en el centro" → "está en el centro")
      c) Mixed number-word combos      ("calle 1cinco" → "calle 15")
      d) Barge-in artifacts            (strips Nexo's greeting if echoed)
    """
    if not text or len(text) < 2:
        return text

    t = text.strip()

    # d) Barge-in cleanup — remove Nexo's greeting echoed into user speech
    for pat in _BARGEIN_FRAGMENTS:
        t = re.sub(pat, "", t, flags=re.IGNORECASE).strip()

    # b) Payanés contractions — expand before splitting
    t_lower = t.lower()
    for contraction, expansion in _PAYANES_CONTRACTIONS.items():
        pat = r"\b" + re.escape(contraction) + r"\b"
        t = re.sub(pat, expansion, t, flags=re.IGNORECASE)

    # a) Fused-word splitting: "callequince" → "calle quince"
    t = _FUSED_STREET_RE.sub(r"\1 \2", t)

    # c) Mixed number normalization: "calle 1cinco" → "calle 15"
    #    Handle digit+word: "1cinco" → "15"
    def _expand_mixed_num(m):
        digit_part = m.group(1)
        word_part = m.group(2).lower()
        if word_part in _NUM_WORDS:
            return (
                str(int(digit_part) * 10 + int(_NUM_WORDS[word_part]))
                if int(digit_part) < 10
                else digit_part + _NUM_WORDS[word_part]
            )
        return m.group(0)

    t = re.sub(
        r"(\d)(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)",
        _expand_mixed_num,
        t,
        flags=re.IGNORECASE,
    )

    # Handle "6 e 20" -> "6E-20" (common STT error for "6 este 20")
    t = re.sub(r"\b(\d+)\s+e\s+(\d+)\b", r"\1E-\2", t)
    
    # Handle "6620" -> "6E-20" if it follows a street word
    t = re.sub(r"(calle|carrera|#|número)\s+66(\d+)\b", r"\1 6E-\2", t, flags=re.IGNORECASE)
    # Handle "67" -> "6E" if it follows a street word (siete sounds like este)
    t = re.sub(r"(calle|carrera|#|número)\s+67\b", r"\1 6E", t, flags=re.IGNORECASE)

    # Normalize whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def classify_speech_quality(text: str, confidence: float) -> str:
    """Classify speech input quality using Twilio confidence + text length.

    Returns: "high", "medium", "low", or "empty".
    """
    t = (text or "").strip()
    if not t or len(t) < 2:
        return "empty"

    word_count = len(t.split())

    # Short explicit answers are always high quality even with 0 confidence
    t_clean = re.sub(r'[^\w\s]', '', t.lower()).strip()
    if t_clean in ("no", "si", "sí", "sip", "nop", "ok", "vale", "dale", "listo", "bueno", "ya", "claro", "exacto"):
        return "high"

    # High: good confidence OR long enough text (user spoke clearly with many words)
    if confidence >= 0.65 or word_count >= 6:
        return "high"

    # Medium: moderate confidence with some words
    if 0.35 <= confidence < 0.65 and 3 <= word_count <= 5:
        return "medium"

    # Low: poor confidence AND short text
    if confidence < 0.35 and word_count < 4:
        return "low"

    # Edge cases: default to medium (e.g., confidence 0.35-0.64 with word_count > 5)
    return "medium"


def _adaptive_retry_message(text: str, confidence: float, state: str) -> str:
    """Generate a context-aware retry message based on detected failure pattern.

    Guides the user differently depending on WHY the recognition failed.
    """
    t = (text or "").strip()
    words = t.split()
    word_count = len(words)
    t_lower = t.lower()

    # Pattern: has numbers but no street type keyword
    has_number = bool(re.search(r"\d+", t))
    has_street_kw = bool(
        re.search(r"\b(calle|carrera|cl|cra|kr|kra|barrio)\b", t_lower)
    )
    if has_number and not has_street_kw:
        return (
            "¿Es calle o carrera? "
            "Por ejemplo, calle quince."
        )

    # Pattern: very short + low confidence → acoustic issue
    if word_count < 3 and confidence < 0.4:
        return "Repítemelo más despacio."

    # Pattern: long text but no address extracted → too much info
    if word_count > 5 and not has_street_kw:
        return "Solo dime el barrio o la calle."

    # Pattern: text ends with preposition/article → cut-off speech
    cut_markers = {"en", "de", "del", "la", "el", "las", "los", "con", "por", "al", "a"}
    if words and words[-1].lower().rstrip(".,;") in cut_markers:
        return "Se cortó. ¿Me repites?"

    # Default retry per state
    if state == STATE_WAITING_ORIGIN:
        return "No te pillé. ¿Dónde te recojo?"
    elif state == STATE_WAITING_DEST_OR_SKIP:
        return "No te pillé. ¿A dónde vas? O dime no."
    return "No te escuché. ¿Me lo repites?"


def _get_process_speech_url(request: Request) -> str:
    """Determine the action URL for <Gather>.

    Priority:
    1. TWILIO_GATHER_ACTION_URL env var (manual override)
    2. Auto-detect from request headers (Host + X-Forwarded-Proto)
       Cloudflare tunnel sets these automatically.
    3. Fallback: build from request base URL with HTTPS
    """
    # 1. Manual override
    action = _gather_action_url()
    if action:
        return action

    # 2. Auto-detect from Cloudflare/proxy headers
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host", ""
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")

    if forwarded_host and "trycloudflare.com" in forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}/process_speech"

    # Also check for any forwarded host (ngrok, other tunnels)
    if forwarded_host and forwarded_host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        # Strip port if present for public URLs
        host_no_port = (
            forwarded_host.split(":")[0] if ":" in forwarded_host else forwarded_host
        )
        if not host_no_port.replace(".", "").isdigit():  # Not a raw IP
            return f"{forwarded_proto}://{forwarded_host}/process_speech"

    base = str(request.base_url).rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://") :]
    return base + "/process_speech"


# (Using utilities from core.address_utils)


def _extract_json_object(raw: str) -> dict:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


async def extract_pickup_address(user_text: str) -> Tuple[Optional[str], str]:
    """Extract pickup address from spoken text.
    Strips conversational preamble, then tries local match, then async LLM."""
    # 0. Strip conversational preamble ("hola amiga, me encuentro en...")
    cleaned = _strip_preamble(user_text)
    if cleaned != user_text:
        logger.info(f"[EXTRACT] Stripped preamble: {user_text!r} → {cleaned!r}")

    # 1. Fast local match on cleaned text (no API call)
    local = _try_local_match(cleaned)
    if local:
        logger.info(f"[EXTRACT] Local match for origin: {local!r}")
        return local, ""

    # 1b. Try with original text if cleaned didn't match
    if cleaned != user_text:
        local = _try_local_match(user_text)
        if local:
            logger.info(f"[EXTRACT] Local match (original) for origin: {local!r}")
            return local, ""

    # 2. Async LLM extraction (non-blocking)
    client = _get_async_openai()
    if not client:
        t = (user_text or "").strip()
        return (
            t if len(t) > 3 else None,
            "Por favor dime con más detalle dónde te recogemos en Popayán.",
        )

    model = _get_model()
    prompt = f"""Eres un asistente para taxi en Popayán, Cauca, Colombia.
El usuario habla por teléfono. Extrae SOLO el punto de RECOGIDA (origen).
Si dice "de X a Y", "desde X hasta Y" o "de X hacia Y", el origen es X (no Y).
Prioriza direcciones por calle y/o carrera: cruces (calle 5 con carrera 9),
nomenclatura con placa (carrera 6 # 12-34), una sola vía (calle 15).
Abreviaturas: cl, cra, kr, k. Barrios y lugares conocidos solo si no hay calle/carrera clara.
Responde SOLO JSON: {{"origen": "texto normalizado o null", "nota": "breve"}}
Si no hay ninguna ubicación clara, origen=null.
Texto del usuario:
{user_text}
"""
    try:
        result = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=4.0,
        )
        data = _extract_json_object(result.choices[0].message.content or "")
        o = data.get("origen")
        if o is None or str(o).strip().lower() in ("null", "none", ""):
            fb = (user_text or "").strip()
            if len(fb) >= 4:
                return fb, ""
            return None, (
                "No alcanzamos a entender bien el punto de recogida. ¿Nos lo repites, por favor? "
                "Intenta decir una calle, carrera, barrio o lugar conocido en Popayán."
            )
        return str(o).strip(), ""
    except Exception as exc:
        logger.error(f"extract_pickup_address error: {exc}")
        fb = (user_text or "").strip()
        if len(fb) >= 4:
            return fb, ""
        return (
            None,
            "Hubo un problema técnico. Intenta decir de nuevo tu punto de recogida.",
        )


async def extract_destination_address(user_text: str) -> Tuple[Optional[str], str]:
    """Extract destination address from spoken text. Tries local match first, then async LLM."""
    # 0. Strip conversational preamble
    cleaned = _strip_preamble(user_text)

    # 1. Fast local match
    local = _try_local_match(cleaned)
    if local:
        logger.info(f"[EXTRACT] Local match for destination: {local!r}")
        return local, ""

    # 1b. Try original text
    if cleaned != user_text:
        local = _try_local_match(user_text)
        if local:
            logger.info(f"[EXTRACT] Local match (original) for destination: {local!r}")
            return local, ""

    # 2. Async LLM extraction (non-blocking)
    client = _get_async_openai()
    if not client:
        t = (user_text or "").strip()
        return (t if len(t) > 2 else None, "Indica tu destino en Popayán, por favor.")

    model = _get_model()
    prompt = f"""Popayán, Cauca, Colombia. Extrae SOLO la dirección o lugar de DESTINO del viaje.
Prioriza calle y carrera: cruces (calle 5 con carrera 9),
placas (carrera 6 # 12-34), una vía (calle 15 norte). Abreviaturas: cl, cra, kr, k.
Si no hay nomenclatura de vías, acepta lugar conocido o barrio.
Responde SOLO JSON: {{"destino": "texto o null", "nota": "breve"}}
Texto:
{user_text}
"""
    try:
        result = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=4.0,
        )
        data = _extract_json_object(result.choices[0].message.content or "")
        d = data.get("destino")
        if d is None or str(d).strip().lower() in ("null", "none", ""):
            fb = (user_text or "").strip()
            if len(fb) >= 3:
                return fb, ""
            return (
                None,
                "¿Cuál es tu destino? Puedes decir calle, carrera, barrio o un lugar conocido en Popayán.",
            )
        return str(d).strip(), ""
    except Exception as exc:
        logger.error(f"extract_destination_address error: {exc}")
        fb = (user_text or "").strip()
        if len(fb) >= 3:
            return fb, ""
        return None, "Repite el destino, por favor."


def _parse_si_no(texto: str) -> Optional[bool]:
    """Parse affirmative/negative response. True=sí, False=no, None=unclear.
    Uses only regex — no LLM call (speed critical)."""
    t = (texto or "").lower().strip()
    if not t:
        return None

    t_clean = re.sub(r"[^\w\s]", "", t)

    no_patterns = (
        r"^no$",
        r"\bno gracias\b",
        r"\bnop\b",
        r"\bmejor no\b",
        r"\bno quiero\b",
        r"\b(?:le digo |al |el |ya |all[áa] )*conductor\b",
        r"\ble digo [ae]l\b",
        r"\ball[áa] le digo\b",
        r"\ball[áa] le indico\b",
        r"\bno deseo\b",
        r"\bnegativo\b",
        r"\bprefiero no\b",
        r"\bno por ahora\b",
    )
    si_patterns = (
        r"^s[ií]$",
        r"^si$",
        r"\bclaro\b",
        r"\bpor supuesto\b",
        r"\bdale\b",
        r"\bok\b",
        r"\bvale\b",
        r"\blisto\b",
        r"\bafirmativo\b",
        r"\bquiero indicar\b",
        r"\bs[ií] quiero\b",
        r"\bsi quiero\b",
        r"\bdesde luego\b",
        r"\bclaro que s[ií]\b",
        r"\bs[ií] s[ií]\b",
        r"\bsi si\b",
        r"\bbueno\b",
        r"\bcorrecto\b",
    )
    for p in no_patterns:
        if re.search(p, t_clean):
            return False
    for p in si_patterns:
        if re.search(p, t_clean):
            return True

    # Fuzzy: sí/si anywhere = yes, no anywhere = no
    if re.search(r"\bs[ií]\b", t_clean):
        return True
    if re.search(r"\bno\b", t_clean):
        return False

    return None


def _is_repeat_request(texto: str) -> bool:
    """Detect if the user is asking Lyra to repeat her last message."""
    t = _normalize_text(texto)
    if len(t) < 3:
        return False

    repeat_patterns = [
        r"\brepite\b",
        r"\brepetir\b",
        r"\brepiteme\b",
        r"\brepíteme\b",
        r"\bme puede repetir\b",
        r"\bme puedes repetir\b",
        r"\bme repite\b",
        r"\bme repites\b",
        r"\bque dijiste\b",
        r"\bqué dijiste\b",
        r"\bque dijo\b",
        r"\bqué dijo\b",
        r"\bno te escuche\b",
        r"\bno te escuché\b",
        r"\bno escuche\b",
        r"\bno entendi\b",
        r"\bno entendí\b",
        r"\bno le entendi\b",
        r"\bcomo asi\b",
        r"\bcómo así\b",
        r"\bperdon\b.*\bescuche\b",
        r"\bperdón\b.*\bescuché\b",
        r"\bme puede recordar\b",
        r"\bme puedes recordar\b",
        r"\bque me dijo\b",
        r"\bqué me dijo\b",
        r"\bque me dijiste\b",
        r"\bqué me dijiste\b",
        r"\botra vez\b",
        r"\bde nuevo\b",
        r"\bno oi\b",
        r"\bno oí\b",
    ]
    for p in repeat_patterns:
        if re.search(p, t):
            return True
    return False


# ── Backend communication ─────────────────────────────────────────────────────

import httpx


async def _create_service(
    celular: Optional[str],
    origen: str,
    destino: Optional[str],
    http_client: Optional[httpx.AsyncClient] = None,
) -> Tuple[bool, str]:
    """Geocode and post taxi request to Laravel backend.

    Uses async geocoding and parallel origin+destination resolution when both are present.
    Accepts an optional shared httpx.AsyncClient to avoid per-call TCP setup.
    """
    origen_norm = normalize_address(origen)

    if destino:
        # Parallel geocoding: origin and destination at the same time
        dest_norm = normalize_address(destino)
        g_o_task = _nominatim_geocode_async(origen_norm)
        g_d_task = _nominatim_geocode_async(dest_norm)
        g_o, g_d = await asyncio.gather(g_o_task, g_d_task)

        # Fallback attempts (still async, but sequential since primary failed)
        if not g_o:
            g_o = await _nominatim_geocode_async(origen)
        if not g_d:
            g_d = await _nominatim_geocode_async(destino)
    else:
        # Origin only
        g_o = await _nominatim_geocode_async(origen_norm)
        if not g_o:
            g_o = await _nominatim_geocode_async(origen)
        g_d = None

    if not g_o:
        return False, (
            "Ay, no me aparece esa ubicación en Popayán. "
            "¿Me la dices de otra forma? Prueba con un barrio cercano o una calle."
        )

    olat, olng, geo_o = g_o
    logger.info(f"Geocode origin OK: {geo_o[:100]}")

    dlat, dlng = 0.0, 0.0
    if destino:
        if g_d:
            dlat, dlng, geo_d = g_d
            logger.info(f"Geocode dest OK: {geo_d[:100]}")
        else:
            return False, (
                "Hmm, ese destino no me aparece en Popayán. "
                "¿Me lo dices de otra forma? Una calle, barrio o sitio conocido."
            )

    payload = {
        "pasajero_id": 1,
        "celular": celular,
        "pasajero_nombre": "Usuario Telefónico",
        "origen": origen,
        "origen_lat": float(olat),
        "origen_lng": float(olng),
        "clase_vehiculo": "TAXI",
        "precio_estimado": 0.0,
    }
    if destino and destino.strip():
        payload["destino"] = destino.strip()
        payload["destino_lat"] = float(dlat)
        payload["destino_lng"] = float(dlng)
    else:
        payload["destino"] = ""
        payload["destino_lat"] = 0.0
        payload["destino_lng"] = 0.0

    logger.info(f"Backend POST payload: {json.dumps(payload, ensure_ascii=False)}")

    try:
        from core.config import settings

        backend_url = settings.INTELLITAXI_API_BASE

        # Use shared client if provided, otherwise create a one-off
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=8.0, write=5.0, pool=5.0)
        )
        try:
            resp = await client.post(
                f"{backend_url}/taxi/solicitud-telefonica",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        finally:
            if not http_client:
                await client.aclose()

        logger.info(f"Backend response: {resp.status_code} {resp.text[:500]}")

        if resp.status_code >= 400:
            return False, (
                "Uy, tuvimos un problema registrando tu servicio. "
                "Dale, inténtalo de nuevo en unos segunditos o pídelo por la app."
            )
        return True, (
            "¡Listo! Ya te estamos buscando un carro. "
            "En un momentico se comunica contigo el conductor. "
            "¡Que tengas un excelente viaje! Chao."
        )
    except httpx.TimeoutException:
        return False, "Se nos demoró un poquito el servidor. Inténtalo de nuevo, porfa."
    except Exception as e:
        logger.error(f"Backend POST error: {e}")
        return False, (
            "Tuvimos un problemita técnico. "
            "Intenta de nuevo o pide el taxi por la aplicación."
        )


# ── Routes ────────────────────────────────────────────────────────────────────


@voice_router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    """
    Initial Twilio webhook when a call comes in.
    Greets the user and starts listening for the pickup address.
    """
    form = await request.form()
    call_sid = form.get("CallSid") or request.query_params.get("CallSid") or "unknown"
    sess = get_session(str(call_sid))
    sess.state = STATE_WAITING_ORIGIN

    logger.info(f"[VOICE] New call CallSid={call_sid}")

    # Debug: log headers from Cloudflare tunnel for URL auto-detection
    host_h = request.headers.get("host", "?")
    xfh = request.headers.get("x-forwarded-host", "?")
    xfp = request.headers.get("x-forwarded-proto", "?")
    cf_ray = request.headers.get("cf-ray", "?")
    logger.info(
        f"[VOICE] Headers: host={host_h} x-fwd-host={xfh} x-fwd-proto={xfp} cf-ray={cf_ray}"
    )

    saludo = (
        "Hola, soy Nexo, tu asistente de taxi. "
        "¿Dónde te recogemos?"
    )
    sess.last_message = saludo

    action_url = _get_process_speech_url(request)
    logger.info(f"[VOICE] action_url={action_url}")
    xml = _twiml_gather_message(saludo, action_url)
    return _twiml_response(xml)


@voice_router.api_route("/process_speech", methods=["GET", "POST"])
async def process_speech(request: Request):
    """
    Twilio webhook for each speech turn after <Gather>.
    Processes audio transcription through the state machine.
    """
    form = await request.form()
    call_sid = str(
        form.get("CallSid") or request.query_params.get("CallSid") or "unknown"
    )
    caller_id = str(form.get("From") or "").replace("whatsapp:", "")

    # Twilio sends speech result under different keys
    texto_usuario = ""
    confidence = 0.0
    for key in ("SpeechResult", "StableSpeechResult", "UnstableSpeechResult"):
        v = str(form.get(key) or "").strip()
        if v:
            texto_usuario = v
            break

    # Get Twilio confidence score (0.0 - 1.0)
    try:
        confidence = float(form.get("Confidence") or 0.0)
    except (ValueError, TypeError):
        confidence = 0.0

    # Log raw speech + confidence for debugging
    if texto_usuario:
        logger.info(f"[SPEECH_RAW] confidence={confidence:.2f} text={texto_usuario!r}")

    # ── Normalize raw STT (fused words, contractions, barge-in) ──
    texto_original = texto_usuario  # Save original before corrections
    if texto_usuario:
        texto_normalizado = normalize_raw_stt(texto_usuario, confidence)
        if texto_normalizado != texto_usuario:
            logger.info(
                f"[SPEECH] Normalized: {texto_usuario!r} → {texto_normalizado!r}"
            )
            texto_usuario = texto_normalizado

    # Apply speech corrections for common Twilio misrecognitions
    if texto_usuario:
        texto_corregido = _correct_speech(texto_usuario)
        if texto_corregido != texto_usuario:
            logger.info(f"[SPEECH] Corrected: {texto_usuario!r} → {texto_corregido!r}")
            texto_usuario = texto_corregido

    # Strip conversational preamble EARLY (before state machine)
    # "Hola amiga, me encuentro aquí en el Valle del Ortigal" → "el Valle del Ortigal"
    if texto_usuario:
        texto_limpio = _strip_preamble(texto_usuario)
        if texto_limpio != texto_usuario:
            logger.info(
                f"[SPEECH] Preamble stripped: {texto_usuario!r} → {texto_limpio!r}"
            )
            texto_usuario = texto_limpio

    # ── Greeting-only detection ──
    # If original had text but corrections/preamble stripped it to empty,
    # the user just said a greeting ("Hola", "Buenas") without an address.
    _GREETING_WORDS = {"hola", "buenas", "buenos", "qhubo", "alo", "aló"}
    if not texto_usuario and texto_original:
        orig_words = set(texto_original.lower().strip().rstrip(".,!?").split())
        if orig_words & _GREETING_WORDS:
            logger.info(f"[SPEECH] Greeting-only detected: {texto_original!r}")
            texto_usuario = "__GREETING__"  # sentinel for state machine

    # ── Classify speech quality (Problem 2 + 3: confidence integration) ──
    speech_quality = classify_speech_quality(texto_usuario, confidence)
    if texto_usuario:
        logger.info(f"[SPEECH] quality={speech_quality} confidence={confidence:.2f}")

    sess = get_session(call_sid)
    action_url = _get_process_speech_url(request)

    logger.info(
        f"[SPEECH] CallSid={call_sid} state={sess.state} "
        f"text_len={len(texto_usuario)} preview={texto_usuario[:120]!r}"
    )

    # ── Already finished ──
    if sess.state == STATE_FINISHED or (
        sess.service_created and sess.state == STATE_SERVICE_CREATED
    ):
        return _twiml_response(
            _twiml_say_hangup(
                "¡Gracias por llamarnos! Que te vaya bien, chao."
            )
        )

    # Get shared httpx client for backend calls
    http_client = getattr(request.app.state, "http_client", None)

    # ── Service Background Creation ──
    if sess.state == STATE_CREATING_SERVICE:
        logger.info(f"[STATE] Finalizing service creation in background...")
        ok, closing = await _create_service(
            caller_id, sess.origen_text or "", sess.destino_text, http_client=http_client
        )
        if not ok:
            sess.state = STATE_WAITING_DEST_OR_SKIP
            sess.last_message = closing
            return _twiml_response(
                _twiml_gather_message(closing, action_url, short_timeout=True)
            )

        sess.service_created = True
        sess.state = STATE_FINISHED
        reset_session(call_sid)
        return _twiml_response(_twiml_say_hangup(closing))

    # ── Repeat request detection ──
    if texto_usuario and _is_repeat_request(texto_usuario):
        logger.info(f"[SPEECH] Repeat requested. Replaying last message.")
        replay = sess.last_message or "Cuéntame, ¿en qué parte de Popayán te recogemos?"
        return _twiml_response(_twiml_gather_message(replay, action_url))

    # ── Greeting-only handling ──
    # User said "Hola" without an address → respond with prompt
    if texto_usuario == "__GREETING__":
        texto_usuario = ""  # clear sentinel
        if sess.state == STATE_WAITING_ORIGIN:
            msg = "¡Hola! ¿Dónde te recogemos?"
        elif sess.state == STATE_WAITING_DEST_OR_SKIP:
            msg = "¡Hola! ¿A dónde vas? O dime no."
        else:
            msg = "¡Hola! ¿En qué te ayudo?"
        sess.last_message = msg
        return _twiml_response(_twiml_gather_message(msg, action_url))

    # ── Silence handling ──
    if not texto_usuario:
        sess.silence_count += 1
        logger.info(f"[SPEECH] Silence #{sess.silence_count} state={sess.state}")

        if sess.silence_count >= _max_silence():
            reset_session(call_sid)
            return _twiml_response(
                _twiml_say_hangup(
                    "Parece que no puedes hablar ahora. "
                    "Llámanos cuando quieras. ¡Chao!",
                )
            )

        if sess.state == STATE_WAITING_ORIGIN:
            if sess.silence_count == 1:
                msg = (
                    "¿Sigues ahí? "
                    "Dime dónde te recojo."
                )
            else:
                msg = (
                    "Seguimos aquí. ¿Dónde estás?"
                )
        elif sess.state == STATE_WAITING_DEST_OR_SKIP:
            if sess.silence_count == 1:
                msg = (
                    "No te escuché. ¿A dónde vas? "
                    "O dime no."
                )
            else:
                msg = (
                    "Dime el destino o dime no."
                )
        elif sess.state == STATE_CONFIRMING_ORIGIN:
            msg = (
                f"¿Confirmas que estás por {sess.origen_barrio or 'esa zona'}? "
                "Solo dime sí, o dime tu barrio."
            )
        else:
            msg = "¿Me escuchas? Háblame."

        sess.last_message = msg
        return _twiml_response(_twiml_gather_message(msg, action_url))

    # ── Reset silence counter ──
    sess.silence_count = 0

    # ── Low quality gate: skip LLM, ask user to reformulate ──
    if speech_quality == "low" and sess.state in (
        STATE_WAITING_ORIGIN,
        STATE_WAITING_DEST_OR_SKIP,
    ):
        msg = _adaptive_retry_message(texto_usuario, confidence, sess.state)
        logger.info(f"[SPEECH] Low quality gate — skipping LLM. Retry msg sent.")
        sess.last_message = msg
        return _twiml_response(_twiml_gather_message(msg, action_url))

    # ── Medium quality: aggressive normalize + retry local match before LLM ──
    if speech_quality == "medium" and sess.state in (
        STATE_WAITING_ORIGIN,
        STATE_WAITING_DEST_OR_SKIP,
    ):
        local_match = _try_local_match(texto_usuario)
        if not local_match:
            # Try aggressive re-normalization
            re_normalized = normalize_raw_stt(texto_usuario, confidence)
            local_match = _try_local_match(re_normalized)
        if local_match:
            logger.info(
                f"[SPEECH] Medium quality resolved via local match: {local_match!r}"
            )
            texto_usuario = local_match  # Use the matched canonical name

    # ── STATE: waiting_origin ──
    if sess.state == STATE_WAITING_ORIGIN:
        # 1. TRY LOCAL MATCH FIRST — instant, no LLM call needed
        local = _try_local_match(texto_usuario)
        if local:
            origen = local
            logger.info(f"[STATE] Local match for origin: {origen!r} (LLM skipped)")
        else:
            # 2. Only call LLM if local match fails
            origen_llm, hint = await extract_pickup_address(texto_usuario)
            origen = (origen_llm or texto_usuario or "").strip()

        # Normalize through our address normalizer
        if origen:
            normalized = normalize_address(origen)
            if normalized and len(normalized) > len(origen) * 0.5:
                origen = normalized

        sess.origen_text = origen
        logger.info(f"[STATE] waiting_origin extracted origen={origen!r}")

        if not origen or len(origen) < 2:
            msg = (
                "No te pillé bien. "
                "¿Dónde te recojo? Un barrio o calle."
            )
            sess.last_message = msg
            return _twiml_response(
                _twiml_gather_message(msg, action_url)
            )

        # Check if this is a street address (not a named barrio/landmark)
        # If it contains street patterns (calle/carrera + numbers), ask for barrio confirmation
        is_street_address = bool(
            re.search(r"(?:calle|carrera|cl|cra|cr|kra|kr)\s*\d+", origen.lower())
        )

        if is_street_address:
            # Find nearest barrio for confirmation
            try:
                from tools.popayan_geodata import (
                    geocode_local,
                    get_nearby_barrios,
                    ALL_BARRIOS,
                    _haversine,
                )

                geo = geocode_local(origen)
                if geo:
                    # Use 5km radius (street estimations can be imprecise)
                    nearby = get_nearby_barrios(geo[0], geo[1], radius_km=5.0)
                    if not nearby:
                        # Fallback: find absolute closest barrio
                        closest = min(
                            ALL_BARRIOS.items(),
                            key=lambda x: _haversine(geo[0], geo[1], x[1][0], x[1][1]),
                        )
                        nearby = [
                            {
                                "name": closest[0],
                                "distance_km": _haversine(
                                    geo[0], geo[1], closest[1][0], closest[1][1]
                                ),
                            }
                        ]
                    if nearby:
                        barrio_name = nearby[0]["name"]
                        dist_km = nearby[0]["distance_km"]
                        sess.origen_barrio = barrio_name
                        sess.state = STATE_CONFIRMING_ORIGIN
                        msg = (
                            f"Ok, {origen}, "
                            f"barrio {barrio_name}. "
                            "¿Correcto?"
                        )
                        sess.last_message = msg
                        logger.info(
                            f"[STATE] Confirming origin: {origen!r} near barrio {barrio_name!r} ({dist_km:.1f}km)"
                        )
                        return _twiml_response(
                            _twiml_gather_message(msg, action_url, short_timeout=True)
                        )
            except Exception as exc:
                logger.warning(f"[STATE] Barrio confirmation lookup failed: {exc}")

            # Even if geocoding/lookup failed, STILL ask for barrio confirmation
            sess.state = STATE_CONFIRMING_ORIGIN
            msg = (
                f"Ok, {origen}. "
                "¿En qué barrio queda?"
            )
            sess.last_message = msg
            logger.info(f"[STATE] Asking barrio for street address: {origen!r}")
            return _twiml_response(
                _twiml_gather_message(msg, action_url)
            )

        # Named place or confirmation lookup failed → proceed directly
        sess.state = STATE_WAITING_DEST_OR_SKIP
        msg = (
            f"Listo, te recojo en {origen}. "
            "¿Tienes destino o le dices al conductor?"
        )
        sess.last_message = msg
        return _twiml_response(_twiml_gather_message(msg, action_url, short_timeout=True))

    # ── STATE: confirming_origin ──
    #    User confirms barrio or provides a different barrio
    if sess.state == STATE_CONFIRMING_ORIGIN:
        is_yes = _parse_si_no(texto_usuario)

        if is_yes is True:
            # User confirmed → proceed with the original address
            logger.info(f"[STATE] Origin confirmed: {sess.origen_text!r}")
            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = (
                f"Perfecto, en {sess.origen_text}. "
                "¿Tienes destino o le dices al conductor?"
            )
            sess.last_message = msg
            return _twiml_response(
                _twiml_gather_message(msg, action_url, short_timeout=True)
            )

        if is_yes is False:
            # User said "no" → check if they provided a different address in the same turn
            # e.g., "no, urbanización santa lucía"
            rest = re.sub(r"^(?:no|nones|negativo|nop)[,\s]*", "", texto_usuario, flags=re.IGNORECASE).strip()
            if len(rest) > 4:
                # User provided a correction immediately. We'll use this as the new text to process.
                texto_usuario = rest
                logger.info(f"[STATE] Negative confirmation with correction: {rest!r}")
                # We continue to the local match/extraction below
            else:
                # Just a "no"
                msg = (
                    "Ok. ¿En qué barrio estás?"
                )
                sess.state = STATE_WAITING_ORIGIN
                sess.last_message = msg
                return _twiml_response(
                    _twiml_gather_message(msg, action_url)
                )

        # User gave a different barrio or location → use that as origin
        local = _try_local_match(texto_usuario)
        if local:
            sess.origen_text = local
            logger.info(f"[STATE] Origin updated from confirmation: {local!r}")
            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = (
                f"Listo, en {local}. "
                "¿Tienes destino o le dices al conductor?"
            )
            sess.last_message = msg
            return _twiml_response(
                _twiml_gather_message(msg, action_url, short_timeout=True)
            )

        # Couldn't parse response → ask again
        msg = (
            f"No te pillé. ¿Confirmas que estás por {sess.origen_barrio or 'esa zona'}? "
            "Di sí, o dime tu barrio."
        )
        sess.last_message = msg
        return _twiml_response(_twiml_gather_message(msg, action_url, short_timeout=True))

    # ── STATE: waiting_dest_or_skip ──
    #    User says "no" → service without destination
    #    User says correction phrase → go back to waiting_origin
    #    User says anything else → treat as destination
    if sess.state == STATE_WAITING_DEST_OR_SKIP:
        # Check if user wants to correct the origin
        if _is_correction_request(texto_usuario):
            logger.info(f"[STATE] User wants to correct origin: {texto_usuario!r}")
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            sess.origen_barrio = None
            msg = (
                "Ok, vamos a corregir. "
                "¿Dónde te recogemos?"
            )
            sess.last_message = msg
            return _twiml_response(
                _twiml_gather_message(msg, action_url)
            )

        # Check if user is declining destination
        is_no = _parse_si_no(texto_usuario)
        # Also detect indirect declines
        t_lower = texto_usuario.lower()
        indirect_decline = any(p in t_lower for p in [
            "no tengo", "no sé", "no se", "sin destino", "al conductor", "el conductor",
            "le digo al", "le digo el", "ya le digo", "donde sea", "no importa",
            "allá le digo", "alla le digo", "después le digo", "despue le digo",
        ])

        if is_no is False or indirect_decline:
            # Create service WITHOUT destination
            logger.info(
                f"[STATE] User declined destination. Redirecting to create service without dest."
            )
            sess.state = STATE_CREATING_SERVICE
            xml = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<Response>\n'
                f'    <Say voice="{_twilio_voice()}" language="es-MX">Procesando tu solicitud...</Say>\n'
                f'    <Redirect method="POST">{action_url}</Redirect>\n'
                f'</Response>'
            )
            return _twiml_response(xml)

        # Everything else is treated as a destination
        # Strip destination preambles: "me dirijo hacia X" → "X"
        dest_text = texto_usuario
        dest_preambles = [
            r'^(?:me\s+dirijo\s+(?:hacia|a|al|para))\s+',
            r'^(?:voy\s+(?:para|hacia|a|al|pa))\s+',
            r'^(?:llévame\s+(?:a|al|hacia|para|pa))\s+',
            r'^(?:hacia|para|al|a)\s+',
            r'^(?:mi\s+destino\s+es)\s+',
        ]
        for pat in dest_preambles:
            dest_text = re.sub(pat, '', dest_text, flags=re.IGNORECASE).strip()

        # 1. TRY LOCAL MATCH FIRST — instant
        local_dest = _try_local_match(dest_text)
        if local_dest:
            dest = local_dest
            logger.info(f"[STATE] Local match for destination: {dest!r} (LLM skipped)")
        else:
            # 2. Only call LLM if local match fails
            dest_llm, hint = await extract_destination_address(dest_text)
            dest = (dest_llm or dest_text or "").strip()

        if dest:
            normalized = normalize_address(dest)
            if normalized and len(normalized) > len(dest) * 0.5:
                dest = normalized

        sess.destino_text = dest
        logger.info(f"[STATE] waiting_dest_or_skip extracted dest={dest!r}")

        if not dest or len(dest) < 2:
            msg = (
                "No te pillé. ¿A dónde vas? "
                "O dime no para que le cuentes al conductor."
            )
            sess.last_message = msg
            return _twiml_response(
                _twiml_gather_message(msg, action_url, short_timeout=True)
            )

        # Create service WITH destination
        logger.info(f"[STATE] Destination acquired. Redirecting to create service.")
        sess.state = STATE_CREATING_SERVICE
        xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<Response>\n'
            f'    <Say voice="{_twilio_voice()}" language="es-MX">Procesando tu solicitud...</Say>\n'
            f'    <Redirect method="POST">{action_url}</Redirect>\n'
            f'</Response>'
        )
        return _twiml_response(xml)

    # ── Fallback ──
    return _twiml_response(
        _twiml_say_hangup(
            "¡Gracias por llamarnos! Que te vaya súper bien, chao."
        )
    )


@voice_router.get("/health")
async def voice_health():
    """Health check endpoint for the Laravel proxy."""
    from core.config import settings

    return {
        "ok": True,
        "service": "lyra-intellitaxi-voice",
        "backend_api": settings.INTELLITAXI_API_BASE,
        "twilio_voice": _twilio_voice(),
        "twilio_gather_timeout": _twilio_gather_timeout(),
        "twilio_speech_timeout": _twilio_speech_timeout(),
        "gather_action_url": _gather_action_url() or "inferred from request",
        "llm_provider": settings.LLM_PROVIDER,
    }
