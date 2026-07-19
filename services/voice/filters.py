"""Filtros de texto post-STT — capa barata ya probada en producción.

Se conservan de V1 (spec §3.2): filtro de alucinaciones de STT sobre
silencio/ruido, filtro de eco textual del bot y normalización payanesa
(core.stt_enhancer.preprocess_stt). Operan sobre el transcript final del
turno, antes de entrar al NLU/orquestador.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("lyra.voice.filters")

# Alucinaciones típicas de modelos ASR sobre silencio/ruido (sin tildes).
STT_HALLUCINATIONS = (
    "amara.org",
    "subtitulos realizados por la comunidad",
    "subtitulado por la comunidad",
    "gracias por ver el video",
    "gracias por ver el vídeo",
    "suscribete al canal",
    "www.mooji.org",
)


def is_stt_hallucination(text: str) -> bool:
    from core.stt_enhancer import strip_accents

    t = strip_accents((text or "").lower())
    return any(h in t for h in STT_HALLUCINATIONS)


def _echo_tokens(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).split()


def _transcript_is_known_entity(transcript: str) -> bool:
    """True si el transcript resuelve a una entidad real del catálogo local.

    Una dirección que el usuario REPITE compartiendo palabras con el prompt
    ("Sí, La Paz" tras "¿Confirmas barrio La Paz?") no es eco: nunca debe
    descartarse.
    """
    try:
        from core.location_match import Decision, decide, resolve_location_entity

        m = resolve_location_entity(transcript)
        return bool(m.canonical) and decide(m) in (
            Decision.ACCEPT,
            Decision.CONFIRM,
            Decision.AMBIGUOUS,
        )
    except Exception as e:  # nunca bloquear el turno por el catálogo
        logger.debug("[filters] entity check skipped: %s", e)
        return False


def looks_like_bot_echo(transcript: str, last_message: str) -> bool:
    """True si el transcript es un fragmento literal del último mensaje del bot.

    Con AEC el eco acústico se atenúa pero no desaparece al 100%; esta capa de
    texto descarta secuencias contiguas de >=3 palabras del prompt (nunca un
    "sí"/"no" corto ni una confirmación de 2 palabras que comparte el nombre
    del barrio con la pregunta del bot).
    """
    t = _echo_tokens(transcript)
    if len(t) < 3:
        return False
    if _transcript_is_known_entity(transcript):
        return False
    p = _echo_tokens(last_message)
    if len(t) > len(p):
        return False
    for i in range(len(p) - len(t) + 1):
        if p[i : i + len(t)] == t:
            return True
    return False


def normalize_transcript(text: str, confidence: float) -> str:
    """Limpieza STT (core.stt_enhancer.preprocess_stt).

    Elimina fragmentos de eco de Lyra, expande contracciones payanesas
    ("hágale"/"de una" -> "sí"), repara direcciones y corrige fonética de
    Popayán. Sin esto una confirmación coloquial no matchea el parser sí/no.
    """
    if not text:
        return text
    try:
        from core.stt_enhancer import preprocess_stt

        cleaned = preprocess_stt(text, confidence)
        return cleaned or text
    except Exception as e:  # nunca romper el turno por un fallo de normalización
        logger.debug("[filters] preprocess_stt skipped: %s", e)
        return text
