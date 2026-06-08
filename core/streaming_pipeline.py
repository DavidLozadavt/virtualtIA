"""
core/streaming_pipeline.py — Pipeline de streaming incremental para Lyra.

Implementa:
- Procesamiento de audio incremental (partial transcripts)
- Detección de intención parcial (intent detection antes de que el usuario termine)
- Slot filling progresivo
- Hipótesis mientras el usuario habla
- Adaptación dinámica de VAD/endpointing
- Token streaming del LLM
- Control de barge-in vía Twilio REST API

Arquitectura:
  Twilio partialResultCallback → StreamingSTTBuffer → PartialIntentDetector
                                                     ↓
                                             HypothesisGenerator
                                                     ↓
                                          AdaptiveEndpointController
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncGenerator, Callable, Deque, Optional

from core.stt_enhancer import (
    AudioQualityProfile,
    correct_stt_errors,
    expand_number_words_in_streets,
    fuzzy_match_location,
    resolve_human_reference,
    strip_accents,
)
from core.conversation_repair import (
    ConversationMemory,
    BargeInHandler,
    _extract_partial_location,
    infer_intent,
)

logger = logging.getLogger("lyra.streaming_pipeline")


# ── Buffer de STT incremental ─────────────────────────────────────────────────

@dataclass
class PartialTranscript:
    text:          str
    confidence:    float
    is_final:      bool
    timestamp:     float = field(default_factory=time.time)
    sequence_num:  int   = 0


class StreamingSTTBuffer:
    """
    Acumula transcripciones parciales de Twilio y construye hipótesis incrementales.
    
    Twilio envía partialResultCallback con texto inestable → estable → final.
    Este buffer aplica correcciones a cada fragmento y mantiene el contexto.
    """

    def __init__(self, max_history: int = 10):
        self._partials:    Deque[PartialTranscript] = deque(maxlen=max_history)
        self._final:       Optional[str]             = None
        self._started_at:  float                     = time.time()
        self._last_update: float                     = time.time()

    def add_partial(self, text: str, confidence: float, seq_num: int = 0) -> str:
        """
        Agrega un fragmento parcial y retorna el texto acumulado mejorado.
        Aplica correcciones STT incrementalmente.
        """
        if not text:
            return ""

        # Aplicar correcciones al fragmento parcial
        corrected = correct_stt_errors(text)
        corrected = expand_number_words_in_streets(corrected)

        partial = PartialTranscript(
            text=corrected,
            confidence=confidence,
            is_final=False,
            sequence_num=seq_num,
        )
        self._partials.append(partial)
        self._last_update = time.time()

        return corrected

    def finalize(self, final_text: str, confidence: float) -> str:
        """
        Marca la transcripción como final y aplica todas las correcciones.
        Retorna el texto final mejorado.
        """
        corrected = correct_stt_errors(final_text)
        corrected = expand_number_words_in_streets(corrected)

        self._final = corrected
        final_partial = PartialTranscript(
            text=corrected,
            confidence=confidence,
            is_final=True,
        )
        self._partials.append(final_partial)

        logger.info(
            f"[STT_BUFFER] Final: {final_text!r} → {corrected!r} "
            f"(conf={confidence:.2f}, duration={time.time()-self._started_at:.1f}s)"
        )

        return corrected

    @property
    def best_hypothesis(self) -> Optional[str]:
        """Mejor texto disponible (final si existe, si no el último parcial)."""
        if self._final:
            return self._final
        if self._partials:
            return self._partials[-1].text
        return None

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._started_at

    @property
    def silence_duration(self) -> float:
        """Segundos desde la última actividad de voz."""
        return time.time() - self._last_update

    def speech_rate_estimate(self) -> float:
        """
        Estima palabras por segundo basado en el texto acumulado y el tiempo.
        Útil para adaptar endpointing.
        """
        text = self.best_hypothesis or ""
        words = len(text.split())
        elapsed = max(self.elapsed_seconds, 0.1)
        return words / elapsed


# ── Detector de intención parcial ────────────────────────────────────────────

class PartialIntentDetector:
    """
    Detecta la intención del usuario en tiempo real, antes de que termine de hablar.
    
    Permite:
    - Preparar respuesta mientras el usuario habla
    - Detectar cancelaciones inmediatamente
    - Hacer slot filling incremental de ubicaciones
    """

    # Intenciones que se pueden detectar con texto muy parcial
    EARLY_INTENTS = {
        "cancel":      [r"\b(no gracias|cancela|ya no|mejor no|olvídalo|olvidalo)\b"],
        "repeat":      [r"\b(repite|repíteme|no entendí|no escuché|cómo)\b"],
        "correction":  [r"\b(no es|me equivoqu|cambiar|corregir|está mal)\b"],
        "affirmative": [r"^(s[ií]|claro|dale|listo|ok|bueno|correcto)$"],
        "negative":    [r"^no$", r"^nop$", r"^negativo$"],
    }

    def __init__(self):
        self._current_intent: Optional[str]  = None
        self._intent_confidence: float        = 0.0
        self._location_slots: list[str]       = []

    def process_partial(
        self,
        partial_text:  str,
        current_state: str,
        memory:        ConversationMemory,
    ) -> dict:
        """
        Procesa texto parcial y retorna estado de intención actualizado.
        
        Retorna:
        {
          "intent": "give_origin" | "cancel" | None,
          "confidence": 0.85,
          "action": "wait" | "interrupt_tts" | "prepare_response",
          "partial_location": "campanario",
        }
        """
        t = strip_accents((partial_text or "").lower().strip())

        if not t:
            return {"intent": None, "confidence": 0.0, "action": "wait"}

        # Detectar intenciones tempranas (cancelación, corrección, etc.)
        for intent_name, patterns in self.EARLY_INTENTS.items():
            for pattern in patterns:
                if re.search(pattern, t):
                    self._current_intent     = intent_name
                    self._intent_confidence  = 0.8
                    action = "interrupt_tts" if intent_name in ("cancel", "correction") else "wait"
                    return {
                        "intent":     intent_name,
                        "confidence": 0.8,
                        "action":     action,
                    }

        # Extracción incremental de ubicación
        partial_location = _extract_partial_location(partial_text)
        if partial_location:
            if partial_location not in self._location_slots:
                self._location_slots.append(partial_location)
                logger.debug(f"[PARTIAL_INTENT] Location slot: {partial_location!r}")

        # Inferencia de intención basada en contexto
        full_intent = infer_intent(partial_text, current_state, memory)
        if full_intent["confidence"] >= 0.6:
            self._current_intent    = full_intent["primary_intent"]
            self._intent_confidence = full_intent["confidence"]

        # Determinar acción
        action = "wait"
        if self._intent_confidence >= 0.85 and partial_location:
            action = "prepare_response"  # Suficiente info para preparar respuesta

        return {
            "intent":           self._current_intent,
            "confidence":       self._intent_confidence,
            "action":           action,
            "partial_location": partial_location or (self._location_slots[-1] if self._location_slots else None),
        }

    def reset(self) -> None:
        self._current_intent     = None
        self._intent_confidence  = 0.0
        self._location_slots     = []

    @property
    def accumulated_locations(self) -> list[str]:
        return list(self._location_slots)


# ── Controlador de endpointing adaptativo ────────────────────────────────────

class AdaptiveEndpointController:
    """
    Adapta los parámetros de VAD/endpointing de Twilio basado en:
    - Perfil de audio de la llamada (calidad, ruido)
    - Velocidad de habla detectada
    - Estado conversacional (sí/no vs dirección larga)
    - Historial de silencios falsos
    
    Controla:
    - speechTimeout: cuánto silencio esperar antes de terminar
    - timeout: tiempo máximo total de <Gather>
    - hints: vocabulario sugerido al STT
    """

    def __init__(self, quality_profile: AudioQualityProfile):
        self._profile             = quality_profile
        self._consecutive_empties = 0
        self._consecutive_retries = 0
        self._speech_rate_samples: list[float] = []

    def update_speech_rate(self, rate: float) -> None:
        self._speech_rate_samples.append(rate)
        if len(self._speech_rate_samples) > 5:
            self._speech_rate_samples.pop(0)

    @property
    def avg_speech_rate(self) -> float:
        if not self._speech_rate_samples:
            return 2.0  # default: 2 palabras/segundo
        return sum(self._speech_rate_samples) / len(self._speech_rate_samples)

    def on_empty_response(self) -> None:
        self._consecutive_empties += 1

    def on_successful_response(self) -> None:
        self._consecutive_empties = 0
        self._consecutive_retries = 0

    def on_retry(self) -> None:
        self._consecutive_retries += 1

    def get_parameters(self, state: str, short_answer_expected: bool = False) -> dict:
        """
        Retorna los parámetros óptimos para el próximo <Gather>.
        
        state: estado actual de la conversación
        short_answer_expected: True si esperamos sí/no (confirming_origin, etc.)
        """
        profile = self._profile

        # ── speechTimeout ──
        # Política (corregida): para captura de direcciones usamos "auto" =
        # detección adaptativa de fin-de-habla de Twilio. Es lo mejor para habla
        # natural a ritmo variable con pausas ("calle 16... número 3CE-41...
        # Santa Teresa"); un timeout fijo corto cortaba al usuario a mitad de
        # frase. Para sí/no mantenemos un valor numérico para que responda ágil.
        # Ambos configurables por env sin tocar código.
        short_to = os.getenv("TWILIO_SPEECH_TIMEOUT_SHORT", "1.5")
        long_to = os.getenv("TWILIO_SPEECH_TIMEOUT_LONG", "auto")

        if short_answer_expected:
            speech_timeout = short_to
        elif long_to.lower() == "auto":
            # Default: Twilio decide el fin de habla (no corta pausas naturales).
            speech_timeout = "auto"
        elif profile.is_noisy_call:
            speech_timeout = "3.0"
        elif profile.is_fast_speaker or profile.is_slow_speaker:
            speech_timeout = "2.5"
        elif self._consecutive_retries >= 2:
            speech_timeout = "2.5"
        else:
            speech_timeout = long_to  # numérico si el env lo fija explícitamente

        # ── timeout total ──
        if profile.is_slow_speaker:
            gather_timeout = 35
        elif self._consecutive_empties >= 2:
            gather_timeout = 25  # No lo dejamos esperando eternamente
        else:
            gather_timeout = 30

        # ── hints de vocabulario ──
        # Se pueden adaptar por estado (ej: en confirming_origin, añadir el barrio)
        hints = _get_contextual_hints(state)

        return {
            "speech_timeout":  speech_timeout,
            "gather_timeout":  gather_timeout,
            "hints":           hints,
        }


# ── Generación de hints (phrase/keyword boosting) desde catálogos ──────────────
#
# El vocabulario de boost se DERIVA de los catálogos (BARRIO_ALIASES, LANDMARKS,
# HUMAN_REFERENCES), no de una lista hand-picked. Es model-aware:
#
#   - Deepgram (default, deepgram_nova-2): `hints` → keywords de Deepgram, que
#     boostea PALABRAS SUELTAS poco comunes. Emitimos tokens propios distintivos
#     (Pubenza, Yanaconas, Ortigal, Campanario…), sin palabras genéricas
#     (centro, norte, villa, del…) que diluyen el boost. Cap ~100.
#   - googlev2 / otros: `hints` → frases de Google Speech Adaptation. Emitimos
#     los nombres canónicos completos. Cap ~200.
#
# Twilio admite hasta 500 entradas; el cap es por relevancia (Deepgram degrada
# con demasiadas keywords), no por límite de Twilio.

# Palabras genéricas que NO sirven como keyword distintiva (se filtran en modo
# Deepgram para no diluir el boost de los nombres propios).
_HINT_STOPWORDS = frozenset({
    "el", "la", "los", "las", "de", "del", "san", "santa", "villa", "ciudad",
    "centro", "norte", "sur", "oriente", "occidente", "este", "oeste",
    "alto", "alta", "bajo", "baja", "nuevo", "nueva", "barrio", "sector",
    "conjunto", "urbanizacion", "parque", "plaza", "plazuela", "calle",
    "carrera", "avenida", "comercio", "servicios", "popayan", "cauca",
    "colombia", "real", "grande", "loma", "prados", "campo", "torres",
    "portal", "jardin", "jardines", "vista", "altos", "colina", "colinas",
    "hospital", "clinica", "edificio", "conjunto",
})

# Lugares más solicitados / que el STT más falla → al frente de la prioridad.
_PRIORITY_HINTS = (
    "Pubenza", "Campanario", "Yanaconas", "Valle del Ortigal", "Pandiguando",
    "Belalcázar", "Yambitará", "Los Sauces", "María Oriente", "Comfacauca",
    "Éxito Popayán", "SENA", "Universidad del Cauca", "Terminal de Transportes",
    "Villa del Carmen", "Villa del Viento",
)

# Variantes fonéticas reales del STT que conviene boostear como keywords sueltas.
_PHONETIC_VARIANT_KEYWORDS = (
    "Pubensa", "Pubencia", "Campanaryo", "Yanakonas", "Pandeguando",
    "Belalcasar", "Yambitara",
)

_HINT_CAP_DEEPGRAM = 100
_HINT_CAP_PHRASE = 200

# Cache por régimen de modelo ("dg" | "phrase").
_HINT_VOCAB_CACHE: dict[str, str] = {}


def _prioritized_canonical_names() -> list[str]:
    """Nombres canónicos del catálogo, con los más relevantes al frente."""
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


def _build_hint_vocab(model: str) -> str:
    """Construye la cadena de hints model-aware desde los catálogos (cacheada)."""
    regime = "dg" if (model or "").startswith("deepgram") else "phrase"
    cached = _HINT_VOCAB_CACHE.get(regime)
    if cached is not None:
        return cached

    names = _prioritized_canonical_names()

    if regime == "dg":
        # Tokens propios distintivos (palabras sueltas poco comunes).
        tokens: list[str] = []
        tseen: set[str] = set()

        def _add_tok(tok: str) -> bool:
            t = tok.strip(" ,.")
            tl = strip_accents(t.lower())
            if len(tl) < 4 or tl in _HINT_STOPWORDS or tl in tseen:
                return False
            tseen.add(tl)
            tokens.append(t)
            return True

        for v in _PHONETIC_VARIANT_KEYWORDS:
            _add_tok(v)
        for nm in names:
            for tok in re.split(r"[\s,]+", nm):
                _add_tok(tok)
                if len(tokens) >= _HINT_CAP_DEEPGRAM:
                    break
            if len(tokens) >= _HINT_CAP_DEEPGRAM:
                break
        result = ",".join(tokens[:_HINT_CAP_DEEPGRAM])
    else:
        # Frases canónicas completas.
        result = ",".join(names[:_HINT_CAP_PHRASE])

    _HINT_VOCAB_CACHE[regime] = result
    return result


def _get_contextual_hints(
    state: str,
    detected_barrio: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """
    Genera hints de vocabulario según el estado y el modelo STT.

    - Captura (waiting_origin / waiting_dest_or_skip): vocabulario del catálogo
      (model-aware) — keywords sueltas para Deepgram, frases para googlev2.
    - Confirmación: sí/no/correcto/exacto + el nombre ya detectado (refuerzo).
    """
    if state in ("confirming_origin", "confirming_destination", "confirming_dest"):
        base = "sí,no,correcto,exacto"
        if detected_barrio:
            return f"{base},{detected_barrio.strip()}"
        return base

    vocab = _build_hint_vocab(model or "")
    if state == "waiting_dest_or_skip":
        return vocab + ",no"
    return vocab


# ── Pipeline de procesamiento de turno ───────────────────────────────────────

class TurnProcessor:
    """
    Procesa un turno completo de la conversación.
    Integra: STT buffer → correcciones → intent → slot filling → respuesta.
    
    Es la pieza central que conecta todos los módulos de mejora.
    """

    def __init__(
        self,
        quality_profile:   AudioQualityProfile,
        memory:            ConversationMemory,
        intent_detector:   Optional[PartialIntentDetector] = None,
        endpoint_ctrl:     Optional[AdaptiveEndpointController] = None,
    ):
        self._quality   = quality_profile
        self._memory    = memory
        self._intent_d  = intent_detector or PartialIntentDetector()
        self._endpoint  = endpoint_ctrl or AdaptiveEndpointController(quality_profile)
        self._stt_buf   = StreamingSTTBuffer()

    def process_final_speech(
        self,
        raw_text:   str,
        confidence: float,
    ) -> dict:
        """
        Procesa el texto final de STT de un turno.
        
        Retorna:
        {
          "processed_text": str,
          "intent": dict,
          "quality": str,          # "high" | "medium" | "low" | "empty"
          "partial_location": str | None,
          "human_reference": dict | None,
          "endpoint_params": dict,
          "should_retry": bool,
        }
        """
        # Actualizar perfil de calidad
        self._quality.update(confidence, raw_text)

        # Finalizar buffer STT con texto corregido
        processed = self._stt_buf.finalize(raw_text, confidence) if raw_text else ""

        # Resolver referencias humanas ("por el éxito", "frente a la galería")
        human_ref = None
        if processed:
            human_ref = resolve_human_reference(processed)
            if human_ref:
                logger.info(
                    f"[TURN] Human ref resolved: {processed!r} → {human_ref['canonical']!r}"
                )
                # Agregar a memoria si se resolvió
                self._memory.add_location_mention(human_ref["canonical"])

        # Inferencia de intención
        intent = infer_intent(
            processed,
            current_state="unknown",  # Se pasa desde el llamador en proceso real
            memory=self._memory,
        ) if processed else {"primary_intent": "unknown", "confidence": 0.0}

        # Clasificar calidad
        quality = self._classify_quality(processed, confidence)

        # Registrar ubicaciones en memoria
        partial_loc = _extract_partial_location(processed) if processed else None
        if partial_loc:
            self._memory.add_location_mention(partial_loc)

        # Actualizar speech rate
        if self._stt_buf.elapsed_seconds > 0:
            rate = self._stt_buf.speech_rate_estimate()
            self._endpoint.update_speech_rate(rate)

        # Determinar si hay que reintentar
        should_retry = quality == "low" or not processed

        if not should_retry:
            self._endpoint.on_successful_response()
        else:
            self._endpoint.on_retry()

        # Parámetros de endpointing para el próximo turno
        endpoint_params = self._endpoint.get_parameters("unknown")

        # Reiniciar buffer para el próximo turno
        self._stt_buf = StreamingSTTBuffer()
        self._intent_d.reset()

        return {
            "processed_text":  processed,
            "intent":          intent,
            "quality":         quality,
            "partial_location": partial_loc,
            "human_reference": human_ref,
            "endpoint_params": endpoint_params,
            "should_retry":    should_retry,
        }

    def _classify_quality(self, text: str, confidence: float) -> str:
        t = (text or "").strip()

        if not t or len(t) < 2:
            self._endpoint.on_empty_response()
            return "empty"

        word_count = len(t.split())
        t_clean = re.sub(r"[^\w\s]", "", t.lower()).strip()

        # Respuestas cortas explícitas siempre son high quality
        if t_clean in {
            "no", "si", "sí", "sip", "nop", "ok", "vale", "dale", "listo",
            "bueno", "ya", "claro", "exacto", "correcto", "afirmativo",
        }:
            return "high"

        # Perfil de llamada ruidosa: bajar el umbral de exigencia
        if self._quality.is_noisy_call:
            if confidence >= 0.40 or word_count >= 4:
                return "medium"
            return "low"

        # Normal
        if confidence >= 0.65 or word_count >= 6:
            return "high"
        if 0.35 <= confidence < 0.65 and 3 <= word_count:
            return "medium"
        if confidence < 0.35 and word_count < 4:
            return "low"

        return "medium"

    def process_partial_speech(
        self,
        partial_text:  str,
        confidence:    float,
        current_state: str,
        seq_num:       int = 0,
    ) -> dict:
        """
        Procesa un fragmento de speech parcial (de partialResultCallback).
        Retorna acción inmediata: wait, interrupt_tts, prepare_response.
        """
        # Agregar al buffer
        processed = self._stt_buf.add_partial(partial_text, confidence, seq_num)

        # Detectar intención parcial
        intent_state = self._intent_d.process_partial(
            processed, current_state, self._memory
        )

        # Detectar barge-in
        if BargeInHandler.is_interruption(processed):
            intent_state["action"] = "interrupt_tts"

        return intent_state


# ── LLM Streaming ─────────────────────────────────────────────────────────────

async def stream_llm_response(
    client,
    model:    str,
    messages: list[dict],
    on_chunk: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Hace streaming del LLM y retorna el texto completo.
    Llama on_chunk(texto_parcial) por cada fragmento recibido.
    
    Permite iniciar TTS antes de que el LLM termine (para respuestas largas).
    """
    full_text = []

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            max_tokens=200,  # Respuestas de Lyra son cortas
            temperature=0.3,  # Baja temperatura para respuestas consistentes
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                fragment = delta.content
                full_text.append(fragment)
                if on_chunk:
                    on_chunk(fragment)

    except Exception as e:
        logger.error(f"[LLM_STREAM] Error: {e}")
        raise

    return "".join(full_text)


async def generate_contextual_response(
    client,
    model:         str,
    state:         str,
    memory:        ConversationMemory,
    user_text:     str,
    extracted_loc: Optional[str] = None,
) -> str:
    """
    Genera una respuesta contextual del LLM para casos ambiguos.
    Usa el historial de la conversación para ser más preciso.
    
    Solo se llama cuando la lógica determinista no puede resolver el turno.
    """
    context_parts = []

    if memory.last_confirmed_origin:
        context_parts.append(f"Origen confirmado: {memory.last_confirmed_origin}")
    if memory.mentioned_locations:
        recent = memory.mentioned_locations[-3:]
        context_parts.append(f"Lugares mencionados recientemente: {', '.join(recent)}")
    if memory.partial_hypotheses:
        hyp = memory.best_hypothesis()
        if hyp:
            context_parts.append(f"Hipótesis previa: {hyp['extracted']} (confianza: {hyp['confidence']:.2f})")

    context = "\n".join(context_parts) if context_parts else "Sin contexto previo."

    messages = [
        {
            "role": "system",
            "content": (
                "Eres Lyra, operador de taxi en Popayán, Colombia. "
                "Hablas en español colombiano informal pero profesional. "
                "Tus respuestas son CORTAS (máx 2 oraciones). "
                "Nunca dices 'No entendí' — siempre propones una hipótesis o haces una pregunta específica. "
                "Usas nombres reales de barrios y calles de Popayán."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Estado de la conversación: {state}\n"
                f"Contexto: {context}\n"
                f"El usuario dijo: {user_text!r}\n"
                f"Ubicación extraída (puede ser incorrecta): {extracted_loc or 'ninguna'}\n\n"
                "Genera una respuesta breve que:\n"
                "1. Confirme la ubicación si parece razonable, O\n"
                "2. Pida aclaración específica (ej: '¿Es calle o carrera?'), O\n"
                "3. Proponga una hipótesis con el barrio más probable dado el contexto.\n"
                "Solo la respuesta, sin explicaciones."
            ),
        },
    ]

    try:
        result = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=80,
            temperature=0.4,
        )
        return (result.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[CONTEXTUAL_LLM] Error: {e}")
        return "¿Me repites dónde estás?"