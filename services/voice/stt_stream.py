"""STT streaming — Deepgram nova-2 por WebSocket (spec §3.2).

Requisitos no negociables cumplidos aquí:
  - hipótesis parciales continuas (`interim_results=true`),
  - endpointing nativo acústico (`endpointing`, `speech_final`) + señal por
    tiempos de palabra (`utterance_end_ms` → mensaje UtteranceEnd),
  - eventos de inicio de voz (`vad_events=true` → SpeechStarted),
  - sesgo de vocabulario REAL de decodificación (`keywords`, soportado por
    nova-2 en todas las lenguas, máx 100 términos) — reemplaza el prompt de
    texto suave de V1; el vocabulario se deriva del catálogo local (lógica
    rescatada de core/streaming_pipeline._build_hint_vocab, bucket D).

nova-2 soporta español streaming (language=es-419) según la matriz oficial de
modelos de Deepgram. El audio se envía tal cual llega de FreeSWITCH
(linear16 @ 8000 Hz mono): Deepgram maneja audio telefónico nativo, sin el
resample local a 16 kHz que necesitaba Whisper en V1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from core.config import settings

logger = logging.getLogger("lyra.voice.stt")

_DEEPGRAM_WSS = "wss://api.deepgram.com/v1/listen"
_KEEPALIVE_INTERVAL_SEC = 5.0
_KEYWORD_CAP = 100  # límite documentado de Deepgram

# ── Vocabulario de boosting (rescatado de streaming_pipeline, bucket D) ──

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

_PHONETIC_VARIANT_KEYWORDS = (
    "Pubensa", "Pubencia", "Campanaryo", "Yanakonas", "Pandeguando",
    "Belalcasar", "Yambitara",
)

_KEYWORDS_CACHE: Optional[list[str]] = None


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


def build_keywords() -> list[str]:
    """Tokens propios distintivos del catálogo (palabras sueltas poco comunes).

    Deepgram boostea keywords sueltas: se filtran genéricos que diluyen el
    boost y se priorizan variantes fonéticas reales observadas del STT.
    """
    global _KEYWORDS_CACHE
    if _KEYWORDS_CACHE is not None:
        return _KEYWORDS_CACHE

    from core.stt_enhancer import strip_accents

    tokens: list[str] = []
    seen: set[str] = set()

    def _add_tok(tok: str) -> None:
        t = tok.strip(" ,.")
        tl = strip_accents(t.lower())
        if len(tl) < 4 or tl in _HINT_STOPWORDS or tl in seen:
            return
        seen.add(tl)
        tokens.append(t)

    for variant in _PHONETIC_VARIANT_KEYWORDS:
        _add_tok(variant)
    for name in _catalog_names():
        for tok in re.split(r"[\s,]+", name):
            _add_tok(tok)
            if len(tokens) >= _KEYWORD_CAP:
                break
        if len(tokens) >= _KEYWORD_CAP:
            break

    _KEYWORDS_CACHE = tokens[:_KEYWORD_CAP]
    return _KEYWORDS_CACHE


# ── Eventos ──

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


def parse_deepgram_message(data: dict) -> Optional[STTEvent]:
    """JSON de Deepgram → evento tipado (None para mensajes sin interés)."""
    msg_type = data.get("type")
    if msg_type == "Results":
        channel = data.get("channel") or {}
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            return None
        alt = alternatives[0] or {}
        return TranscriptEvent(
            text=(alt.get("transcript") or "").strip(),
            confidence=float(alt.get("confidence") or 0.0),
            is_final=bool(data.get("is_final")),
            speech_final=bool(data.get("speech_final")),
            start=float(data.get("start") or 0.0),
            duration=float(data.get("duration") or 0.0),
        )
    if msg_type == "UtteranceEnd":
        return UtteranceEndEvent(last_word_end=float(data.get("last_word_end") or 0.0))
    if msg_type == "SpeechStarted":
        return SpeechStartedEvent(timestamp=float(data.get("timestamp") or 0.0))
    return None


class STTStreamError(RuntimeError):
    """El stream STT no puede establecerse u operó con error fatal."""


@dataclass
class DeepgramLiveSTT:
    """Sesión streaming de Deepgram para una llamada."""

    call_uuid: str
    sample_rate: int = 8000
    _ws: object = field(default=None, init=False)
    _keepalive_task: Optional[asyncio.Task] = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def build_url(self) -> str:
        params: list[tuple[str, str]] = [
            ("model", settings.VOICE_STT_MODEL or "nova-2"),
            ("language", settings.VOICE_STT_LANGUAGE or "es-419"),
            ("encoding", "linear16"),
            ("sample_rate", str(self.sample_rate)),
            ("channels", "1"),
            ("interim_results", "true"),
            ("endpointing", str(int(settings.VOICE_STT_ENDPOINTING_MS))),
            ("utterance_end_ms", str(int(settings.VOICE_STT_UTTERANCE_END_MS))),
            ("vad_events", "true"),
            ("punctuate", "true"),
        ]
        boost = float(settings.VOICE_STT_KEYWORD_BOOST or 2.0)
        for kw in build_keywords():
            params.append(("keywords", f"{kw}:{boost:g}"))
        return f"{_DEEPGRAM_WSS}?{urlencode(params)}"

    async def connect(self) -> None:
        api_key = (settings.DEEPGRAM_API_KEY or "").strip()
        if not api_key:
            raise STTStreamError("DEEPGRAM_API_KEY no configurada")

        import websockets

        try:
            self._ws = await websockets.connect(
                self.build_url(),
                additional_headers={"Authorization": f"Token {api_key}"},
                max_size=2**22,
                open_timeout=10,
                close_timeout=5,
            )
        except Exception as e:
            raise STTStreamError(f"Deepgram connect failed: {e}") from e

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info(
            "[stt] deepgram connected call_uuid=%s model=%s lang=%s keywords=%d",
            self.call_uuid,
            settings.VOICE_STT_MODEL,
            settings.VOICE_STT_LANGUAGE,
            len(build_keywords()),
        )

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closed and self._ws is not None:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_SEC)
                try:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
                except Exception:
                    return
        except asyncio.CancelledError:
            pass

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed or self._ws is None or not pcm:
            return
        try:
            await self._ws.send(pcm)
        except Exception as e:
            if not self._closed:
                logger.warning(
                    "[stt] send failed call_uuid=%s err=%s", self.call_uuid, e
                )

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
                event = parse_deepgram_message(data)
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
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
        logger.info("[stt] closed call_uuid=%s", self.call_uuid)
