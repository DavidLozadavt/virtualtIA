"""STT streaming — OpenAI Realtime transcription (gpt-4o-mini-transcribe) por WebSocket.

Reemplaza a Deepgram manteniendo EXACTAMENTE el mismo flujo aguas abajo: este
módulo emite los mismos eventos tipados (`TranscriptEvent`, `UtteranceEndEvent`,
`SpeechStartedEvent`) y expone la misma interfaz pública (`connect`, `send_audio`,
`events`, `close`) que consumía `DeepgramLiveSTT`, así que
`runtime.py`/`endpointing.py`/`orchestrator.py` no cambian.

Mapeo protocolo OpenAI Realtime → eventos internos:
  - `input_audio_buffer.speech_started`                        → SpeechStartedEvent
  - `conversation.item.input_audio_transcription.delta`        → TranscriptEvent(is_final=False)
      (los deltas son incrementales: se acumulan en un buffer de interim)
  - `conversation.item.input_audio_transcription.completed`    → TranscriptEvent(is_final=True,
      speech_final=True)  — el cierre de enunciado lo decide el server_vad de OpenAI

Audio: FreeSWITCH entrega PCM16 lineal @ 8000 Hz (telefonía). Se envía como
`g711_ulaw` (8 kHz nativo) — sin resample ni pérdida de etiqueta de sample rate,
codificado con numpy (sin depender de `audioop`, que no es dependencia del
proyecto). El sesgo de vocabulario de Popayán (los barrios que Deepgram boosteaba
con `keywords`) pasa al `prompt` de la sesión de transcripción (sesgo suave; OpenAI
no tiene boosting de decodificación por keyword).

La resolución de direcciones (core/geocoder_service, core/location_match) y la
lógica de negocio del FSM no se tocan.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from core.config import settings

logger = logging.getLogger("lyra.voice.stt")

_OPENAI_REALTIME_WSS = "wss://api.openai.com/v1/realtime?intent=transcription"
_KEEPALIVE_INTERVAL_SEC = 5.0
_PROMPT_CAP_CHARS = 480  # sesgo suave acotado (el prompt no es un vocabulario duro)

# ── Vocabulario de sesgo (nombres distintivos de Popayán para el prompt) ──
# Antes alimentaba el `keywords` boosting de Deepgram; OpenAI no tiene boosting
# de decodificación, así que estos nombres se listan en el prompt de la sesión.

_HINT_STOPWORDS = frozenset({
    "el", "la", "los", "las", "de", "del", "san", "santa", "villa", "ciudad",
    "centro", "norte", "sur", "oriente", "occidente", "este", "oeste",
    "alto", "alta", "bajo", "baja", "nuevo", "nueva", "barrio", "sector",
    "conjunto", "urbanizacion", "parque", "plaza", "plazuela", "calle",
    "carrera", "avenida", "comercio", "servicios", "popayan", "cauca",
    "colombia", "real", "grande", "loma", "prados", "campo", "torres",
    "portal", "jardin", "jardines", "vista", "altos", "colina", "colinas",
    "hospital", "clinica", "edificio",
})

_PRIORITY_HINTS = (
    "Pubenza", "Campanario", "Yanaconas", "Valle del Ortigal", "Pandiguando",
    "Belalcázar", "Yambitará", "Los Sauces", "María Oriente", "Comfacauca",
    "Éxito Popayán", "SENA", "Universidad del Cauca", "Terminal de Transportes",
    "Villa del Carmen", "Villa del Viento",
)

_PROMPT_CACHE: Optional[str] = None


def _catalog_names() -> list[str]:
    from core.stt_enhancer import strip_accents

    names: list[str] = []
    seen: set[str] = set()

    def _add(nm: str) -> None:
        if not nm:
            return
        k = strip_accents(nm.lower())
        if k not in seen:
            seen.add(k)
            names.append(nm)

    for nm in _PRIORITY_HINTS:
        _add(nm)
    try:
        from tools.popayan_geodata import BARRIO_ALIASES, LANDMARKS

        for nm in BARRIO_ALIASES:
            _add(nm)
        for nm in LANDMARKS:
            _add(nm)
    except ImportError:
        pass
    try:
        from core.stt_enhancer import HUMAN_REFERENCES

        for data in HUMAN_REFERENCES.values():
            _add(data.get("canonical", ""))
    except ImportError:
        pass
    return names


def build_prompt() -> str:
    """Prompt de sesgo para la sesión de transcripción.

    Lista nombres propios distintivos de Popayán (barrios, hitos) para orientar
    al modelo hacia la ortografía correcta — es sesgo suave, no boosting duro.
    """
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE

    names = _catalog_names()
    lead = (
        "Llamada de taxi en Popayán, Colombia. El usuario dicta una dirección o "
        "barrio. Nombres propios frecuentes: "
    )
    picked: list[str] = []
    total = len(lead)
    for nm in names:
        add = len(nm) + 2
        if total + add > _PROMPT_CAP_CHARS:
            break
        picked.append(nm)
        total += add
    _PROMPT_CACHE = lead + ", ".join(picked) + "."
    return _PROMPT_CACHE


# ── G.711 μ-law encode (PCM16 8k → ulaw 8k), vectorizado con numpy ──

_ULAW_SEG_END = None  # se inicializa perezosamente con numpy


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    """Codifica PCM16 little-endian a G.711 μ-law. Determinista, sin audioop."""
    if not pcm:
        return b""
    import numpy as np

    global _ULAW_SEG_END
    if _ULAW_SEG_END is None:
        _ULAW_SEG_END = np.array(
            [0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF], dtype=np.int32
        )

    BIAS = 0x84
    CLIP = 32635

    x = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
    sign = np.where(x < 0, 0x80, 0x00).astype(np.int32)
    mag = np.minimum(np.abs(x), CLIP) + BIAS
    # exponente = primer segmento cuyo límite superior alcanza a `mag`
    exponent = np.searchsorted(_ULAW_SEG_END, mag, side="left").astype(np.int32)
    np.clip(exponent, 0, 7, out=exponent)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    ulaw = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


# ── Eventos (idénticos a los que consumía el endpointer con Deepgram) ──

@dataclass
class TranscriptEvent:
    text: str
    confidence: float
    is_final: bool
    speech_final: bool
    start: float = 0.0
    duration: float = 0.0


@dataclass
class UtteranceEndEvent:
    last_word_end: float = 0.0


@dataclass
class SpeechStartedEvent:
    timestamp: float = 0.0


STTEvent = TranscriptEvent | UtteranceEndEvent | SpeechStartedEvent


def _confidence_from_logprobs(logprobs: object) -> float:
    """exp(media de logprobs) ∈ (0,1]; 1.0 si no hay logprobs disponibles."""
    if not isinstance(logprobs, list) or not logprobs:
        return 1.0
    vals = [
        lp.get("logprob")
        for lp in logprobs
        if isinstance(lp, dict) and isinstance(lp.get("logprob"), (int, float))
    ]
    if not vals:
        return 1.0
    try:
        return round(min(1.0, math.exp(sum(vals) / len(vals))), 3)
    except OverflowError:
        return 1.0


class STTStreamError(RuntimeError):
    """El stream STT no puede establecerse u operó con error fatal."""


@dataclass
class OpenAIRealtimeSTT:
    """Sesión de transcripción en streaming de OpenAI Realtime para una llamada."""

    call_uuid: str
    sample_rate: int = 8000
    _ws: object = field(default=None, init=False)
    _keepalive_task: Optional[asyncio.Task] = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)
    _interim: str = field(default="", init=False)  # acumulador de deltas del enunciado
    _interim_logprobs: list = field(default_factory=list, init=False)

    def _session_config(self) -> dict:
        return {
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "g711_ulaw",
                "input_audio_transcription": {
                    "model": settings.VOICE_STT_MODEL or "gpt-4o-mini-transcribe",
                    "language": settings.VOICE_STT_LANGUAGE or "es",
                    "prompt": build_prompt(),
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": float(settings.VOICE_STT_VAD_THRESHOLD),
                    "prefix_padding_ms": int(settings.VOICE_STT_PREFIX_PADDING_MS),
                    "silence_duration_ms": int(settings.VOICE_STT_SILENCE_MS),
                },
                "input_audio_noise_reduction": {"type": "near_field"},
                "include": ["item.input_audio_transcription.logprobs"],
            },
        }

    async def connect(self) -> None:
        api_key = settings.openai_stt_key()
        if not api_key:
            raise STTStreamError(
                "Sin OPENAI_API_KEY real para STT (OpenRouter no soporta audio)"
            )

        import websockets

        try:
            self._ws = await websockets.connect(
                _OPENAI_REALTIME_WSS,
                additional_headers={
                    "Authorization": f"Bearer {api_key}",
                    "OpenAI-Beta": "realtime=v1",
                },
                max_size=2**22,
                open_timeout=10,
                close_timeout=5,
            )
        except Exception as e:
            raise STTStreamError(f"OpenAI Realtime connect failed: {e}") from e

        try:
            await self._ws.send(json.dumps(self._session_config()))
        except Exception as e:
            raise STTStreamError(f"OpenAI Realtime session config failed: {e}") from e

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info(
            "[stt] openai realtime connected call_uuid=%s model=%s lang=%s",
            self.call_uuid,
            settings.VOICE_STT_MODEL,
            settings.VOICE_STT_LANGUAGE,
        )

    async def _keepalive_loop(self) -> None:
        # Ping WS a nivel de protocolo: mantiene viva la conexión en silencios
        # largos sin inyectar audio falso al buffer.
        try:
            while not self._closed and self._ws is not None:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_SEC)
                try:
                    await self._ws.ping()
                except Exception:
                    return
        except asyncio.CancelledError:
            pass

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed or self._ws is None or not pcm:
            return
        try:
            ulaw = pcm16_to_ulaw(pcm)
            audio_b64 = base64.b64encode(ulaw).decode("ascii")
            await self._ws.send(
                json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
            )
        except Exception as e:
            if not self._closed:
                logger.warning(
                    "[stt] send failed call_uuid=%s err=%s", self.call_uuid, e
                )

    def _parse(self, data: dict) -> Optional[STTEvent]:
        """Evento del protocolo OpenAI Realtime → evento interno tipado."""
        msg_type = data.get("type")

        if msg_type == "input_audio_buffer.speech_started":
            self._interim = ""
            self._interim_logprobs = []
            return SpeechStartedEvent(
                timestamp=float(data.get("audio_start_ms") or 0.0) / 1000.0
            )

        if msg_type == "conversation.item.input_audio_transcription.delta":
            delta = data.get("delta") or ""
            if not delta:
                return None
            self._interim += delta
            lp = data.get("logprobs")
            if isinstance(lp, list):
                self._interim_logprobs.extend(lp)
            return TranscriptEvent(
                text=self._interim.strip(),
                confidence=_confidence_from_logprobs(self._interim_logprobs),
                is_final=False,
                speech_final=False,
            )

        if msg_type == "conversation.item.input_audio_transcription.completed":
            text = (data.get("transcript") or self._interim or "").strip()
            confidence = _confidence_from_logprobs(
                data.get("logprobs") or self._interim_logprobs
            )
            self._interim = ""
            self._interim_logprobs = []
            return TranscriptEvent(
                text=text,
                confidence=confidence,
                is_final=True,
                speech_final=True,
            )

        if msg_type == "conversation.item.input_audio_transcription.failed":
            err = (data.get("error") or {}).get("message", "")
            logger.warning(
                "[stt] transcription failed call_uuid=%s err=%s", self.call_uuid, err
            )
            self._interim = ""
            self._interim_logprobs = []
            return None

        if msg_type == "error":
            logger.warning(
                "[stt] openai realtime error call_uuid=%s payload=%s",
                self.call_uuid,
                data.get("error"),
            )
            return None

        return None

    async def events(self) -> AsyncIterator[STTEvent]:
        if self._ws is None:
            raise STTStreamError("events() antes de connect()")
        try:
            async for raw in self._ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event = self._parse(data)
                if event is not None:
                    yield event
        except Exception as e:
            if not self._closed:
                logger.warning(
                    "[stt] stream ended call_uuid=%s err=%s", self.call_uuid, e
                )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        logger.info("[stt] closed call_uuid=%s", self.call_uuid)
