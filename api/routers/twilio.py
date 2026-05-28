"""
gateway/twilio_voice.py — Gateway de voz Twilio para Lyra/IntelliTaxi.
VERSIÓN MEJORADA: streaming, tolerancia STT, adaptación dinámica, reparación conversacional.

Mejoras integradas:
1. Streaming incremental + partial transcripts
2. Corrección fonética y fuzzy matching de barrios de Popayán
3. Adaptación dinámica de VAD/endpointing por perfil de usuario
4. Reparación conversacional inteligente (sin "No entendí")
5. Resolución de referencias humanas ("por el éxito", "frente a la galería")
6. Memoria contextual de ubicaciones mencionadas
7. Manejo de barge-in / interrupciones
8. Robustez para audio telefónico degradado (PSTN, ruido vehicular, manos libres)
9. Detección de intención con frases incompletas
10. Latencia optimizada: local match primero, LLM solo como fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

# ── Módulos de mejora ─────────────────────────────────────────────────────────
from core.stt_enhancer import (
    AudioQualityProfile,
    correct_stt_errors,
    expand_number_words_in_streets,
    fuzzy_match_location,
    resolve_human_reference,
    strip_accents,
    POPAYAN_STT_CORRECTIONS,
)
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
    StreamingSTTBuffer,
    TurnProcessor,
    _get_contextual_hints,
    generate_contextual_response,
)

# ── Módulos originales ────────────────────────────────────────────────────────
from core.address_utils import (
    _strip_preamble,
    _parse_si_no,
    _is_correction_request,
    _is_repeat_request,
    _try_local_match,
    normalize_address,
    _nominatim_geocode_async,
)
from core.llm_utils import (
    get_async_openai_client as _get_async_openai,
    get_model as _get_model,
)

logger = logging.getLogger("lyra.twilio_voice")
voice_router = APIRouter()


# ── Configuración ─────────────────────────────────────────────────────────────


def _cfg(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _cfg_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _twilio_voice() -> str:
    """Fallback Polly voice (solo se usa si edge_tts falla)."""
    return _cfg("TWILIO_VOICE", "Polly.Lupe-Neural")


def _lyra_tts_voice() -> str:
    """Voz principal de Lyra via edge_tts (Azure Neural)."""
    return _cfg("LYRA_TTS_VOICE", "es-BO-SofiaNeural")


# ── Cache de audio TTS para Twilio ────────────────────────────────────────────
_TTS_AUDIO_CACHE: Dict[str, bytes] = {}
_TTS_CACHE_LOCK = threading.Lock()
_TTS_CACHE_MAX = 200  # Máximo de audios en cache


def _cache_audio(audio_bytes: bytes) -> str:
    """Almacena audio en cache y retorna un ID único."""
    audio_id = uuid.uuid4().hex[:12]
    with _TTS_CACHE_LOCK:
        # Limpiar cache si excede el máximo
        if len(_TTS_AUDIO_CACHE) >= _TTS_CACHE_MAX:
            keys = list(_TTS_AUDIO_CACHE.keys())
            for k in keys[: len(keys) // 2]:
                _TTS_AUDIO_CACHE.pop(k, None)
        _TTS_AUDIO_CACHE[audio_id] = audio_bytes
    return audio_id


def _get_cached_audio(audio_id: str) -> Optional[bytes]:
    """Recupera audio del cache."""
    with _TTS_CACHE_LOCK:
        return _TTS_AUDIO_CACHE.get(audio_id, None)


def _twilio_speech_timeout() -> str:
    return _cfg("TWILIO_SPEECH_TIMEOUT", "1.5")  # Mejorado: 1.0 → 1.5


def _twilio_gather_timeout() -> int:
    return _cfg_int("TWILIO_GATHER_TIMEOUT", 25)


def _max_silence() -> int:
    return _cfg_int("MAX_SILENCE_BEFORE_HANGUP", 3)


def _gather_action_url() -> str:
    return _cfg("TWILIO_GATHER_ACTION_URL", "")


# ── Estados de la máquina de estados ─────────────────────────────────────────

STATE_WAITING_ORIGIN = "waiting_origin"
STATE_CONFIRMING_ORIGIN = "confirming_origin"
STATE_WAITING_DEST_OR_SKIP = "waiting_dest_or_skip"
STATE_SERVICE_CREATED = "service_created"
STATE_CREATING_SERVICE = "creating_service"
STATE_FINISHED = "finished"

SESSION_TTL_SEC = int(os.getenv("CALL_SESSION_TTL_SEC", "7200"))

_SESSION_LOCK = threading.Lock()
_SESSIONS: Dict[str, "CallSession"] = {}


# ── Sesión enriquecida ────────────────────────────────────────────────────────


@dataclass
class CallSession:
    call_sid: str
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
            _SESSIONS[call_sid] = CallSession(call_sid=call_sid)
        s = _SESSIONS[call_sid]
        s.touch()
        return s


def reset_session(call_sid: str) -> None:
    with _SESSION_LOCK:
        _SESSIONS.pop(call_sid, None)


# ── TwiML helpers mejorados ───────────────────────────────────────────────────


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _generate_say_twiml(msg: str) -> str:
    """Fallback: genera <Say> con voz Polly (si edge_tts no está disponible)."""
    voice = _twilio_voice()
    return f'<Say voice="{voice}" language="es-MX">{_xml_escape(msg)}</Say>'


async def _generate_tts_audio(msg: str) -> Optional[bytes]:
    """Genera audio MP3 con la voz de Lyra usando edge_tts."""
    try:
        from core.voice_engine import get_voice_engine

        engine = get_voice_engine()
        audio_bytes = await engine.synthesize_to_bytes(msg, voice=_lyra_tts_voice())
        if audio_bytes and len(audio_bytes) > 100:
            return audio_bytes
    except Exception as e:
        logger.warning(f"[TTS] edge_tts falló, se usará Polly fallback: {e}")
    return None


def _generate_play_twiml(audio_id: str, base_url: str) -> str:
    """Genera <Play> apuntando al audio cacheado."""
    audio_url = f"{base_url}/voice/audio/{audio_id}"
    return f"<Play>{audio_url}</Play>"


async def _twiml_gather_message(
    msg: str,
    action_url: str,
    speech_timeout: str = "1.5",
    gather_timeout: int = 25,
    hints: Optional[str] = None,
    enable_partial: bool = True,
) -> str:
    """
    Construye TwiML <Gather> con:
    - Audio de Lyra generado via edge_tts (voz es-BO-SofiaNeural)
    - speechTimeout adaptativo por perfil de usuario
    - partialResultCallback para detección temprana de intención
    - hints contextuales para mejorar reconocimiento STT
    - Fallback a Polly <Say> si edge_tts falla
    """
    if hints is None:
        hints = _get_contextual_hints("waiting_origin")

    # Intentar generar audio con la voz real de Lyra
    audio_bytes = await _generate_tts_audio(msg)
    base_url = (
        action_url.rsplit("/process_speech", 1)[0]
        if "/process_speech" in action_url
        else action_url.rsplit("/", 1)[0]
    )

    if audio_bytes:
        audio_id = _cache_audio(audio_bytes)
        play_or_say = _generate_play_twiml(audio_id, base_url)
    else:
        play_or_say = _generate_say_twiml(msg)

    partial_attr = (
        f' partialResultCallback="{action_url.replace("process_speech", "partial_speech")}"'
        f' partialResultCallbackMethod="POST"'
        if enable_partial
        else ""
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech" language="es-CO"'
        f' speechTimeout="{speech_timeout}"'
        f' timeout="{gather_timeout}"'
        f' action="{action_url}" method="POST"'
        f"{partial_attr}"
        f' profanityFilter="false"'
        f' hints="{hints}">'
        f"{play_or_say}"
        "</Gather>"
        f'<Redirect method="POST">{action_url}</Redirect>'
        "</Response>"
    )


async def _twiml_gather_adaptive(
    msg: str,
    action_url: str,
    sess: CallSession,
    short_answer: bool = False,
) -> str:
    """
    Wrapper que usa los parámetros adaptativos de la sesión.
    """
    params = sess.get_endpoint_params(short_answer)
    hints = _get_contextual_hints(sess.state)
    return await _twiml_gather_message(
        msg,
        action_url,
        speech_timeout=params["speech_timeout"],
        gather_timeout=params["gather_timeout"],
        hints=hints,
    )


async def _twiml_say_hangup(msg: str, action_url: str = "") -> str:
    """TwiML que reproduce mensaje y cuelga, usando voz de Lyra."""
    audio_bytes = await _generate_tts_audio(msg)
    base_url = (
        action_url.rsplit("/process_speech", 1)[0]
        if "/process_speech" in action_url
        else action_url.rsplit("/", 1)[0] if action_url else ""
    )

    if audio_bytes and base_url:
        audio_id = _cache_audio(audio_bytes)
        play_or_say = _generate_play_twiml(audio_id, base_url)
    else:
        play_or_say = _generate_say_twiml(msg)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{play_or_say}"
        "<Hangup/>"
        "</Response>"
    )


async def _twiml_redirect(action_url: str, say_msg: Optional[str] = None) -> str:
    """Redirect con audio opcional (para transiciones de estado)."""
    if say_msg:
        audio_bytes = await _generate_tts_audio(say_msg)
        base_url = (
            action_url.rsplit("/process_speech", 1)[0]
            if "/process_speech" in action_url
            else action_url.rsplit("/", 1)[0]
        )
        if audio_bytes:
            audio_id = _cache_audio(audio_bytes)
            play_or_say = _generate_play_twiml(audio_id, base_url)
        else:
            voice = _twilio_voice()
            play_or_say = (
                f'<Say voice="{voice}" language="es-MX">{_xml_escape(say_msg)}</Say>'
            )
    else:
        play_or_say = ""

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response>\n"
        f"    {play_or_say}\n"
        f'    <Redirect method="POST">{action_url}</Redirect>\n'
        f"</Response>"
    )


def _twiml_response(xml: str) -> Response:
    return Response(content=xml, media_type="text/xml; charset=utf-8")


def _get_process_speech_url(request: Request) -> str:
    action = _gather_action_url()
    if action:
        return action

    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host", ""
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")

    if forwarded_host and "trycloudflare.com" in forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}/process_speech"

    if forwarded_host and forwarded_host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        host_no_port = (
            forwarded_host.split(":")[0] if ":" in forwarded_host else forwarded_host
        )
        if not host_no_port.replace(".", "").isdigit():
            return f"{forwarded_proto}://{forwarded_host}/process_speech"

    base = str(request.base_url).rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://") :]
    return base + "/process_speech"


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


def preprocess_stt(text: str, confidence: float = 1.0) -> str:
    """
    Pipeline completo de pre-procesamiento STT.

    Pasos (en orden):
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

    # 5. Correcciones STT específicas de Popayán
    t = correct_stt_errors(t)

    # 6. Normalizar espacios
    t = re.sub(r"\s+", " ", t).strip()

    return t


def classify_speech_quality(
    text: str, confidence: float, profile: AudioQualityProfile
) -> str:
    """Clasifica calidad teniendo en cuenta el perfil de audio de la llamada."""
    t = (text or "").strip()

    if not t or len(t) < 2:
        return "empty"

    word_count = len(t.split())
    t_clean = re.sub(r"[^\w\s]", "", t.lower()).strip()

    # Respuestas cortas explícitas: siempre high
    if t_clean in {
        "no",
        "si",
        "sí",
        "sip",
        "nop",
        "ok",
        "vale",
        "dale",
        "listo",
        "bueno",
        "ya",
        "claro",
        "exacto",
        "correcto",
        "afirmativo",
        "negativo",
    }:
        return "high"

    # Si la llamada es consistentemente ruidosa, ser más tolerante
    if profile.is_noisy_call:
        if confidence >= 0.30 or word_count >= 4:
            return "medium"
        return "low"

    # Normal
    if confidence >= 0.65 or word_count >= 6:
        return "high"
    if 0.35 <= confidence < 0.65 and word_count >= 3:
        return "medium"
    if confidence < 0.35 and word_count < 4:
        return "low"

    return "medium"


# ── Extracción de direcciones ─────────────────────────────────────────────────


async def extract_pickup_address(user_text: str) -> Tuple[Optional[str], str]:
    """
    Extrae dirección de recogida.
    Pipeline: preamble strip → local match → referencia humana → LLM
    """
    # 1. Strip preamble
    cleaned = _strip_preamble(user_text)

    # 2. Referencia humana ("por el éxito", "frente a la galería")
    human_ref = resolve_human_reference(cleaned) or resolve_human_reference(user_text)
    if human_ref and human_ref.get("canonical"):
        logger.info(f"[EXTRACT] Human ref: {user_text!r} → {human_ref['canonical']!r}")
        return human_ref["canonical"], ""

    # 3. Local match (barrios/calles conocidas)
    for candidate in (cleaned, user_text):
        local = _try_local_match(candidate)
        if local:
            logger.info(f"[EXTRACT] Local match origin: {local!r}")
            return local, ""

    # 4. LLM (solo si no hay match local)
    client = _get_async_openai()
    if not client:
        fb = user_text.strip()
        return (fb if len(fb) > 3 else None), "¿Dónde te recogemos en Popayán?"

    model = _get_model()
    prompt = (
        "Eres asistente de taxi en Popayán, Cauca, Colombia. El usuario habla por teléfono.\n"
        "Extrae SOLO el punto de RECOGIDA (origen). Si dice 'de X a Y', el origen es X.\n"
        "Prioriza: cruces (calle 5 con carrera 9), nomenclatura (carrera 6 # 12-34), "
        "una vía (calle 15), barrio o lugar conocido de Popayán.\n"
        'Responde SOLO JSON: {"origen": "texto normalizado o null", "nota": "breve"}\n'
        f"Texto: {user_text}"
    )

    try:
        result = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=4.0,
        )
        data = json.loads(result.choices[0].message.content or "{}")
        origen = data.get("origen")

        if not origen or str(origen).strip().lower() in ("null", "none", ""):
            fb = user_text.strip()
            return (
                fb if len(fb) >= 4 else None
            ), "¿Dónde te recogemos? Dime el barrio o la calle."

        return str(origen).strip(), ""

    except Exception as e:
        logger.error(f"extract_pickup_address error: {e}")
        fb = user_text.strip()
        return (
            fb if len(fb) >= 4 else None
        ), "Hubo un problema técnico. Repite tu punto de recogida."


async def extract_destination_address(user_text: str) -> Tuple[Optional[str], str]:
    """Extrae dirección de destino. Mismo pipeline que origen."""
    cleaned = _strip_preamble(user_text)

    human_ref = resolve_human_reference(cleaned) or resolve_human_reference(user_text)
    if human_ref and human_ref.get("canonical"):
        return human_ref["canonical"], ""

    for candidate in (cleaned, user_text):
        local = _try_local_match(candidate)
        if local:
            logger.info(f"[EXTRACT] Local match dest: {local!r}")
            return local, ""

    client = _get_async_openai()
    if not client:
        fb = user_text.strip()
        return (fb if len(fb) > 2 else None), "¿A dónde vas en Popayán?"

    model = _get_model()
    prompt = (
        "Popayán, Cauca, Colombia. Extrae SOLO el DESTINO del viaje.\n"
        "Prioriza: cruces, nomenclatura, una vía, barrio o lugar conocido.\n"
        'Responde SOLO JSON: {"destino": "texto o null", "nota": "breve"}\n'
        f"Texto: {user_text}"
    )

    try:
        result = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=4.0,
        )
        data = json.loads(result.choices[0].message.content or "{}")
        dest = data.get("destino")

        if not dest or str(dest).strip().lower() in ("null", "none", ""):
            fb = user_text.strip()
            return (
                fb if len(fb) >= 3 else None
            ), "¿Cuál es tu destino? Calle, barrio o lugar conocido."

        return str(dest).strip(), ""

    except Exception as e:
        logger.error(f"extract_destination_address error: {e}")
        fb = user_text.strip()
        return (fb if len(fb) >= 3 else None), "Repite el destino, por favor."


# ── Backend ───────────────────────────────────────────────────────────────────


async def _create_service(
    celular: Optional[str],
    origen: str,
    destino: Optional[str],
    http_client: Optional[httpx.AsyncClient] = None,
) -> Tuple[bool, str]:
    """Geocodifica y crea el servicio de taxi en el backend Laravel."""
    origen_norm = normalize_address(origen)

    if destino:
        dest_norm = normalize_address(destino)
        g_o, g_d = await asyncio.gather(
            _nominatim_geocode_async(origen_norm),
            _nominatim_geocode_async(dest_norm),
        )
        if not g_o:
            g_o = await _nominatim_geocode_async(origen)
        if not g_d:
            g_d = await _nominatim_geocode_async(destino)
    else:
        g_o = await _nominatim_geocode_async(
            origen_norm
        ) or await _nominatim_geocode_async(origen)
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

    try:
        from core.config import settings

        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=8.0, write=5.0, pool=5.0)
        )
        try:
            resp = await client.post(
                f"{settings.INTELLITAXI_API_BASE}/taxi/solicitud-telefonica",
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
            logger.error(f"Backend {resp.status_code}: {resp.text[:300]}")
            return (
                False,
                "Tuvimos un problema registrando tu servicio. Inténtalo de nuevo.",
            )

        return True, (
            "¡Listo! Ya te estamos buscando un móvil. "
            "En un momento se comunica el conductor contigo. "
            "Además, te vamos a enviar un mensaje de confirmación por WhatsApp con los datos de quien tomó tu servicio. "
            "¡Que tengas un excelente viaje! Fue un placer atenderte."
        )

    except httpx.TimeoutException:
        return False, "Se demoró el servidor. Inténtalo de nuevo, porfa."
    except Exception as e:
        logger.error(f"Backend POST error: {e}")
        return False, "Problemita técnico. Intenta de nuevo o pide el taxi por la app."


# ── Rutas ─────────────────────────────────────────────────────────────────────


@voice_router.get("/voice/audio/{audio_id}")
async def serve_voice_audio(audio_id: str):
    """Sirve los audios de TTS cacheados en memoria para Twilio."""
    audio_bytes = _get_cached_audio(audio_id)
    if not audio_bytes:
        logger.warning(f"[TTS] Audio ID {audio_id} no encontrado en cache.")
        return Response(status_code=404)
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@voice_router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    """
    Webhook inicial de Twilio. Saluda y comienza a escuchar.
    """
    form = await request.form()
    call_sid = str(
        form.get("CallSid") or request.query_params.get("CallSid") or "unknown"
    )
    sess = get_session(call_sid)
    sess.state = STATE_WAITING_ORIGIN

    # Log headers para debugging de tunnel/proxy
    logger.info(
        f"[VOICE] CallSid={call_sid} "
        f"host={request.headers.get('host','?')} "
        f"x-fwd-host={request.headers.get('x-forwarded-host','?')} "
        f"x-fwd-proto={request.headers.get('x-forwarded-proto','?')}"
    )

    saludo = (
        "¡Hola! Te recordamos que para mayor facilidad, puedes solicitar tus servicios mediante WhatsApp al número: "
        "3  11... 5  44... 48... 51. ... ... "
        "Soy Lyra, tu asistente de TaxBelalcazar. "
        "Estoy aquí para ayudarte con tu servicio. "
        "Cuéntame, ¿en dónde te recogemos hoy?"
    )
    sess.last_message = saludo

    action_url = _get_process_speech_url(request)
    logger.info(f"[VOICE] action_url={action_url}")

    xml = await _twiml_gather_adaptive(saludo, action_url, sess, short_answer=False)
    return _twiml_response(xml)


@voice_router.api_route("/process_speech", methods=["GET", "POST"])
async def process_speech(request: Request):
    """
    Webhook principal. Procesa cada turno de la conversación.
    Integra todos los módulos de mejora.
    """
    form = await request.form()
    call_sid = str(
        form.get("CallSid") or request.query_params.get("CallSid") or "unknown"
    )
    caller_id = str(form.get("From") or "").replace("whatsapp:", "")

    # ── Extraer resultado STT de Twilio ──
    texto_usuario = ""
    for key in ("SpeechResult", "StableSpeechResult", "UnstableSpeechResult"):
        v = str(form.get(key) or "").strip()
        if v:
            texto_usuario = v
            break

    try:
        confidence = float(form.get("Confidence") or 0.0)
    except (ValueError, TypeError):
        confidence = 0.0

    if texto_usuario:
        logger.info(f"[RAW_STT] conf={confidence:.2f} text={texto_usuario!r}")

    sess = get_session(call_sid)
    action_url = _get_process_speech_url(request)

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
        f"[SPEECH] CallSid={call_sid} state={sess.state} "
        f"quality={speech_quality} text={texto_usuario[:100]!r}"
    )

    # ── Estados terminales ──
    if sess.state == STATE_FINISHED or (
        sess.service_created and sess.state == STATE_SERVICE_CREATED
    ):
        return _twiml_response(
            await _twiml_say_hangup(
                "¡Muchas gracias por comunicarte con TaxBelalcazar! "
                "Fue un placer atenderte. ¡Que tengas un excelente día!",
                action_url,
            )
        )

    http_client = getattr(request.app.state, "http_client", None)

    # ── Creación de servicio (estado de transición) ──
    if sess.state == STATE_CREATING_SERVICE:
        ok, closing = await _create_service(
            caller_id,
            sess.origen_text or "",
            sess.destino_text,
            http_client=http_client,
        )
        if not ok:
            sess.state = STATE_WAITING_DEST_OR_SKIP
            sess.last_message = closing
            return _twiml_response(
                await _twiml_gather_adaptive(
                    closing, action_url, sess, short_answer=True
                )
            )

        sess.service_created = True
        sess.state = STATE_FINISHED
        reset_session(call_sid)
        return _twiml_response(await _twiml_say_hangup(closing, action_url))

    # ── Detección de "repite" ──
    if texto_usuario and _is_repeat_request(texto_usuario):
        logger.info("[SPEECH] Repeat request detected.")
        replay = sess.last_message or "¿En qué parte de Popayán te recogemos?"
        return _twiml_response(await _twiml_gather_adaptive(replay, action_url, sess))

    # ── Saludo sin dirección ──
    if texto_usuario == "__GREETING__":
        texto_usuario = ""
        msgs = {
            STATE_WAITING_ORIGIN: "¡Hola! Con mucho gusto te ayudo. Cuéntame, ¿en dónde te recogemos?",
            STATE_WAITING_DEST_OR_SKIP: "¡Hola! Dime, ¿a dónde te diriges? O si prefieres, dime no y le cuentas al conductor.",
        }
        msg = msgs.get(sess.state, "¡Hola! Soy Lyra, ¿en qué puedo ayudarte?")
        sess.last_message = msg
        return _twiml_response(await _twiml_gather_adaptive(msg, action_url, sess))

    # ── Silencio ──
    if not texto_usuario:
        sess.silence_count += 1
        sess.quality_profile.silence_count += 1
        logger.info(f"[SILENCE] #{sess.silence_count} state={sess.state}")

        if sess.silence_count >= _max_silence():
            reset_session(call_sid)
            return _twiml_response(
                await _twiml_say_hangup(
                    "Parece que no puedes hablar en este momento. "
                    "No te preocupes, llámanos cuando gustes, estaremos encantados de atenderte. "
                    "¡Que tengas un excelente día!",
                    action_url,
                )
            )

        silence_msgs = {
            (STATE_WAITING_ORIGIN, 1): "¿Sigues ahí? Dime dónde te recojo.",
            (STATE_WAITING_ORIGIN, 2): "¿Dónde estás en Popayán?",
            (STATE_WAITING_DEST_OR_SKIP, 1): "No te escuché. ¿A dónde vas? O dime no.",
            (STATE_WAITING_DEST_OR_SKIP, 2): "Dime el destino o dime no.",
            (
                STATE_CONFIRMING_ORIGIN,
                1,
            ): f"¿Confirmas {sess.origen_barrio or 'esa zona'}? Di sí o no.",
        }

        msg = silence_msgs.get(
            (sess.state, min(sess.silence_count, 2)), "¿Me escuchas? Háblame."
        )
        sess.last_message = msg
        return _twiml_response(await _twiml_gather_adaptive(msg, action_url, sess))

    # ── Reset contador de silencio ──
    sess.silence_count = 0

    # ── Gate de baja calidad: reparación inteligente ──
    if speech_quality == "low" and sess.state in (
        STATE_WAITING_ORIGIN,
        STATE_WAITING_DEST_OR_SKIP,
    ):
        sess.retry_count += 1
        sess.endpoint_ctrl.on_retry()

        msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)

        # En el 3er reintento, cambiar estrategia: pedir solo barrio
        if sess.retry_count >= 3:
            if sess.state == STATE_WAITING_ORIGIN:
                msg = "¿Solo dime el barrio donde estás?"
            else:
                msg = "¿Solo dime el barrio de destino?"

        logger.info(f"[LOW_QUALITY] Retry #{sess.retry_count}: {msg!r}")
        sess.last_message = msg
        return _twiml_response(await _twiml_gather_adaptive(msg, action_url, sess))

    # ── Calidad media: intentar match local agresivo antes de LLM ──
    if speech_quality == "medium" and sess.state in (
        STATE_WAITING_ORIGIN,
        STATE_WAITING_DEST_OR_SKIP,
    ):
        local_try = _try_local_match(texto_usuario)
        if not local_try:
            local_try = _try_local_match(preprocess_stt(texto_usuario, confidence))
        if local_try:
            logger.info(f"[MEDIUM_QUALITY] Resolved via local match: {local_try!r}")
            texto_usuario = local_try

    sess.retry_count = 0
    sess.endpoint_ctrl.on_successful_response()

    # ═══════════════════════════════════════════════════════════════
    #  MÁQUINA DE ESTADOS
    # ═══════════════════════════════════════════════════════════════

    # ── ESTADO: waiting_origin ────────────────────────────────────
    if sess.state == STATE_WAITING_ORIGIN:

        # 1. Referencia humana ("por el éxito", "frente a la galería")
        human_ref = resolve_human_reference(texto_usuario)
        if human_ref and human_ref.get("canonical"):
            origen = human_ref["canonical"]
            logger.info(f"[ORIGIN] Human ref: {origen!r}")
        else:
            # 2. Local match directo
            local = _try_local_match(texto_usuario)
            if local:
                origen = local
                logger.info(f"[ORIGIN] Local match: {origen!r}")
            else:
                # 3. LLM extraction
                origen_llm, hint = await extract_pickup_address(texto_usuario)
                origen = (origen_llm or texto_usuario or "").strip()

        # Normalizar
        if origen:
            norm = normalize_address(origen)
            if norm and len(norm) > len(origen) * 0.4:
                origen = norm

        sess.origen_text = origen
        sess.memory.add_location_mention(origen)
        logger.info(f"[ORIGIN] Extracted: {origen!r}")

        if not origen or len(origen) < 2:
            msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)
            sess.last_message = msg
            return _twiml_response(await _twiml_gather_adaptive(msg, action_url, sess))

        # Determinar si es dirección de calle (requiere confirmación de barrio)
        is_street = bool(
            re.search(r"(?:calle|carrera|cl|cra|kr|kra)\s*\d+", origen.lower())
        )

        if is_street:
            # Buscar barrio más cercano para confirmar
            try:
                from tools.popayan_geodata import (
                    geocode_local,
                    get_nearby_barrios,
                    ALL_BARRIOS,
                    _haversine,
                )

                geo = geocode_local(origen)
                if geo:
                    nearby = get_nearby_barrios(geo[0], geo[1], radius_km=5.0)
                    if not nearby:
                        closest = min(
                            ALL_BARRIOS.items(),
                            key=lambda x: _haversine(geo[0], geo[1], x[1][0], x[1][1]),
                        )
                        nearby = [{"name": closest[0], "distance_km": 0.0}]

                    if nearby:
                        barrio_name = nearby[0]["name"]
                        sess.origen_barrio = barrio_name
                        sess.state = STATE_CONFIRMING_ORIGIN
                        msg = f"Entendido, veo que es por la {origen}, en el barrio {barrio_name}. ¿Es correcto?"
                        sess.last_message = msg
                        return _twiml_response(
                            await _twiml_gather_adaptive(
                                msg, action_url, sess, short_answer=True
                            )
                        )

            except Exception as exc:
                logger.warning(f"[ORIGIN] Barrio lookup failed: {exc}")

            # Sin barrio encontrado: preguntar directamente
            sess.state = STATE_CONFIRMING_ORIGIN
            msg = f"Perfecto, {origen}. ¿Me podrías indicar en qué barrio queda?"
            sess.last_message = msg
            return _twiml_response(await _twiml_gather_adaptive(msg, action_url, sess))

        # Lugar nombrado: ir directo a destino
        sess.state = STATE_WAITING_DEST_OR_SKIP
        msg = f"¡Excelente! Te recogemos en {origen}. ¿Tienes algún destino en mente o prefieres indicárselo al conductor?"
        sess.last_message = msg
        return _twiml_response(
            await _twiml_gather_adaptive(msg, action_url, sess, short_answer=True)
        )

    # ── ESTADO: confirming_origin ─────────────────────────────────
    if sess.state == STATE_CONFIRMING_ORIGIN:
        is_yes = _parse_si_no(texto_usuario)

        if is_yes is True:
            sess.memory.last_confirmed_origin = sess.origen_text
            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = f"¡Perfecto! Te recogemos entonces en {sess.origen_text}. ¿Tienes algún destino para tu viaje o prefieres indicárselo directamente al conductor?"
            sess.last_message = msg
            return _twiml_response(
                await _twiml_gather_adaptive(msg, action_url, sess, short_answer=True)
            )

        if is_yes is False:
            # Extraer corrección inline ("no, en el ortigal")
            rest = re.sub(
                r"^(?:no|nones|negativo|nop)[,\s]*",
                "",
                texto_usuario,
                flags=re.IGNORECASE,
            ).strip()
            if len(rest) > 4:
                texto_usuario = rest
                # Caemos al match local/LLM abajo
            else:
                sess.state = STATE_WAITING_ORIGIN
                msg = "Entendido. ¿En qué barrio te encuentras exactamente para ubicarte mejor?"
                sess.last_message = msg
                return _twiml_response(
                    await _twiml_gather_adaptive(msg, action_url, sess)
                )

        # Intentar match local con la respuesta del usuario
        local = _try_local_match(texto_usuario)
        if local:
            sess.origen_text = local
            sess.memory.add_location_mention(local)
            sess.memory.last_confirmed_origin = local
            sess.state = STATE_WAITING_DEST_OR_SKIP
            msg = f"Listo, te recogemos en {local}. ¿Tienes un destino o prefieres indicárselo al conductor?"
            sess.last_message = msg
            return _twiml_response(
                await _twiml_gather_adaptive(msg, action_url, sess, short_answer=True)
            )

        # No se pudo parsear: reparación contextual
        msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)
        if sess.origen_barrio:
            msg = f"Disculpa, no logré escucharte bien. ¿Confirmas que estás por {sess.origen_barrio}? Puedes responderme sí, o indicarme tu barrio."
        sess.last_message = msg
        return _twiml_response(
            await _twiml_gather_adaptive(msg, action_url, sess, short_answer=True)
        )

    # ── ESTADO: waiting_dest_or_skip ─────────────────────────────
    if sess.state == STATE_WAITING_DEST_OR_SKIP:

        # Corrección de origen
        if _is_correction_request(texto_usuario):
            sess.state = STATE_WAITING_ORIGIN
            sess.origen_text = None
            sess.origen_barrio = None
            msg = (
                "¡Claro que sí, corregimos de inmediato! Cuéntame, ¿dónde te recogemos?"
            )
            sess.last_message = msg
            return _twiml_response(await _twiml_gather_adaptive(msg, action_url, sess))

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
            return _twiml_response(
                await _twiml_redirect(action_url, "Procesando tu solicitud...")
            )

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

        # Referencia humana en destino
        human_ref = resolve_human_reference(dest_text)
        if human_ref and human_ref.get("canonical"):
            dest = human_ref["canonical"]
            logger.info(f"[DEST] Human ref: {dest!r}")
        else:
            # Local match
            local_dest = _try_local_match(dest_text)
            if local_dest:
                dest = local_dest
                logger.info(f"[DEST] Local match: {dest!r}")
            else:
                dest_llm, _ = await extract_destination_address(dest_text)
                dest = (dest_llm or dest_text or "").strip()

        if dest:
            norm = normalize_address(dest)
            if norm and len(norm) > len(dest) * 0.4:
                dest = norm

        sess.destino_text = dest
        sess.memory.add_location_mention(dest)
        logger.info(f"[DEST] Extracted: {dest!r}")

        if not dest or len(dest) < 2:
            msg = get_repair_message(texto_usuario, confidence, sess.state, sess.memory)
            sess.last_message = msg
            return _twiml_response(
                await _twiml_gather_adaptive(msg, action_url, sess, short_answer=True)
            )

        sess.state = STATE_CREATING_SERVICE
        return _twiml_response(
            await _twiml_redirect(action_url, "Procesando tu solicitud...")
        )

    # ── Fallback ──────────────────────────────────────────────────
    return _twiml_response(
        await _twiml_say_hangup(
            "¡Muchas gracias por comunicarte con TaxBelalcazar! "
            "Fue un placer atenderte. ¡Que tengas un excelente día!",
            action_url,
        )
    )


# ── Mantenimiento ─────────────────────────────────────────────────────────────

MAINTENANCE_MESSAGE = (
    "En este momento nuestro sistema de llamadas se encuentra en mantenimiento. "
    "Por favor, solicita tu servicio a través de WhatsApp al número: "
    "3  11... 5  44... 48... 51. "
    "Si deseas que te repita el número, por favor di repetir. "
    "Si no deseas escucharlo de nuevo, simplemente puedes colgar la llamada."
)

REPEAT_MESSAGE = (
    "Claro que sí, el número es: "
    "3  11... 5  44... 48... 51. "
    "Si deseas escucharlo otra vez, di repetir, o simplemente cuelga la llamada."
)

SIMPLE_MAINTENANCE_MESSAGE = (
    "En este momento nuestro sistema se encuentra en mantenimiento. "
    "Por favor, intenta comunicarte más tarde. Muchas gracias."
)


def _get_base_url_for_twilio(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host", ""
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")

    if forwarded_host and "trycloudflare.com" in forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}"

    if forwarded_host and forwarded_host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        host_no_port = (
            forwarded_host.split(":")[0] if ":" in forwarded_host else forwarded_host
        )
        if not host_no_port.replace(".", "").isdigit():
            return f"{forwarded_proto}://{forwarded_host}"

    base = str(request.base_url).rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://") :]
    return base


@voice_router.api_route("/voice/maintenance/call", methods=["GET", "POST"])
async def voice_maintenance(request: Request):
    """
    Webhook inicial de Twilio para modo mantenimiento.
    """
    base_url = _get_base_url_for_twilio(request)
    action_url = f"{base_url}/voice/maintenance/process"

    audio_bytes = await _generate_tts_audio(MAINTENANCE_MESSAGE)
    if audio_bytes:
        audio_id = _cache_audio(audio_bytes)
        play_or_say = _generate_play_twiml(audio_id, base_url)
    else:
        play_or_say = _generate_say_twiml(MAINTENANCE_MESSAGE)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech" language="es-CO"'
        f' timeout="10" speechTimeout="auto"'
        f' action="{action_url}" method="POST">'
        f"{play_or_say}"
        "</Gather>"
        f'<Redirect method="POST">{action_url}</Redirect>'
        "</Response>"
    )
    return _twiml_response(xml)


@voice_router.api_route("/voice/maintenance/process", methods=["GET", "POST"])
async def voice_maintenance_process(request: Request):
    """
    Webhook para procesar la respuesta en modo mantenimiento.
    """
    form = await request.form()
    texto_usuario = str(form.get("SpeechResult", "")).strip().lower()

    base_url = _get_base_url_for_twilio(request)
    action_url = f"{base_url}/voice/maintenance/process"

    repeat_words = [
        "repite",
        "repetir",
        "repita",
        "repítelo",
        "repítemelo",
        "otra vez",
        "repiteme",
    ]
    wants_repeat = any(w in texto_usuario for w in repeat_words)

    if wants_repeat:
        audio_bytes = await _generate_tts_audio(REPEAT_MESSAGE)
        if audio_bytes:
            audio_id = _cache_audio(audio_bytes)
            play_or_say = _generate_play_twiml(audio_id, base_url)
        else:
            play_or_say = _generate_say_twiml(REPEAT_MESSAGE)
    else:
        play_or_say = ""

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech" language="es-CO"'
        f' timeout="10" speechTimeout="auto"'
        f' action="{action_url}" method="POST">'
        f"{play_or_say}"
        "</Gather>"
        f'<Redirect method="POST">{action_url}</Redirect>'
        "</Response>"
    )
    return _twiml_response(xml)


@voice_router.api_route("/voice/maintenance/systems", methods=["GET", "POST"])
async def voice_maintenance_simple(request: Request):
    """
    Webhook inicial de Twilio para modo mantenimiento simple (dice el mensaje y cuelga).
    """
    base_url = _get_base_url_for_twilio(request)

    audio_bytes = await _generate_tts_audio(SIMPLE_MAINTENANCE_MESSAGE)
    if audio_bytes:
        audio_id = _cache_audio(audio_bytes)
        play_or_say = _generate_play_twiml(audio_id, base_url)
    else:
        play_or_say = _generate_say_twiml(SIMPLE_MAINTENANCE_MESSAGE)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{play_or_say}"
        "<Hangup/>"
        "</Response>"
    )
    return _twiml_response(xml)


# ── Partial speech endpoint ───────────────────────────────────────────────────


@voice_router.post("/partial_speech")
async def partial_speech(
    request: Request,
    CallSid: str = Form(""),
    UnstableSpeechResult: str = Form(""),
    SequenceNumber: str = Form(""),
):
    """
    Procesa resultados parciales de STT de Twilio (partialResultCallback).

    Permite:
    - Detección temprana de cancelaciones ("no gracias")
    - Detección temprana de correcciones
    - Slot filling incremental de ubicaciones
    - Preparar hipótesis antes de que el usuario termine de hablar

    Nota: Este endpoint debe retornar 200 vacío rápidamente.
    Las acciones reales (ej: cancelar TTS vía Twilio REST API) se harían
    de forma asíncrona en background.
    """
    if not UnstableSpeechResult or not CallSid:
        return Response(content="", media_type="text/xml")

    t_lower = strip_accents(UnstableSpeechResult.lower().strip())
    sess = get_session(CallSid) if CallSid else None

    try:
        seq = int(SequenceNumber or 0)
    except ValueError:
        seq = 0

    logger.debug(f"[PARTIAL] seq={seq} text={t_lower!r}")

    # Procesar con el turn processor de la sesión
    if sess and sess.turn_processor:
        result = sess.turn_processor.process_partial_speech(
            UnstableSpeechResult,
            confidence=0.5,  # Parciales no tienen confidence score
            current_state=sess.state if sess else "unknown",
            seq_num=seq,
        )

        action = result.get("action", "wait")

        if action == "interrupt_tts":
            logger.info(f"[PARTIAL] INTERRUPT signal detected: {t_lower!r}")
            # En producción: llamar Twilio REST API para cancelar el TTS actual
            # asyncio.create_task(_cancel_current_tts(CallSid))
            # Por ahora: loguear para monitoreo

        elif action == "prepare_response":
            partial_loc = result.get("partial_location")
            if partial_loc and sess:
                sess.memory.add_location_mention(partial_loc)
                logger.info(f"[PARTIAL] Early location slot filled: {partial_loc!r}")

        # Barge-in explícito
        elif BargeInHandler.is_interruption(t_lower):
            logger.info(f"[PARTIAL] Barge-in detected: {t_lower!r}")

    return Response(content="", media_type="text/xml")


# ── WebSocket Media Streams ───────────────────────────────────────────────────


@voice_router.websocket("/voice/stream")
async def voice_stream(websocket: WebSocket):
    """
    WebSocket para Twilio Media Streams.

    Permite streaming de audio bidireccional para:
    - STT incremental con Deepgram/AssemblyAI (menor latencia que polling)
    - TTS en streaming directo al caller
    - Control de barge-in en tiempo real

    Estado actual: captura eventos y loguea. Para STT real vía WebSocket,
    integrar con Deepgram Nova-2 o similar.
    """
    await websocket.accept()
    stream_sid = None
    call_sid = None
    audio_buffer: list[bytes] = []

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")

            if event == "connected":
                logger.info("[WS_STREAM] Connected")

            elif event == "start":
                stream_sid = data["start"]["streamSid"]
                call_sid = data["start"]["callSid"]
                logger.info(
                    f"[WS_STREAM] Started: CallSid={call_sid} StreamSid={stream_sid}"
                )

            elif event == "media":
                # Audio µ-law 8kHz mono en base64
                # Para STT en tiempo real: acumular y enviar a Deepgram vía WebSocket
                payload = data["media"].get("payload", "")
                # audio_buffer.append(base64.b64decode(payload))  # Activar con Deepgram
                pass

            elif event == "stop":
                logger.info(f"[WS_STREAM] Stopped: StreamSid={stream_sid}")
                break

    except WebSocketDisconnect:
        logger.info(f"[WS_STREAM] Disconnected: StreamSid={stream_sid}")
    except Exception as e:
        logger.error(f"[WS_STREAM] Error: {e}")


# ── Health check ──────────────────────────────────────────────────────────────


@voice_router.get("/health")
async def voice_health():
    from core.config import settings

    return {
        "ok": True,
        "service": "lyra-intellitaxi-voice-v2",
        "backend_api": settings.INTELLITAXI_API_BASE,
        "twilio_voice": _twilio_voice(),
        "speech_timeout": _twilio_speech_timeout(),
        "gather_timeout": _twilio_gather_timeout(),
        "gather_action_url": _gather_action_url() or "inferred",
        "llm_provider": settings.LLM_PROVIDER,
        "active_sessions": len(_SESSIONS),
        "improvements": [
            "adaptive_vad",
            "stt_phonetic_correction",
            "human_reference_resolution",
            "conversational_repair",
            "partial_intent_detection",
            "contextual_memory",
            "barge_in_detection",
            "quality_adaptive_endpointing",
        ],
    }
