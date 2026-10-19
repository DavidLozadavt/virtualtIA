"""
services/whatsapp_media.py — Notas de voz de WhatsApp.

Responsabilidad ÚNICA: convertir una nota de voz en TEXTO. Nada más.

No hay pipeline nuevo. El audio se transcribe con el MISMO motor STT que ya usa
el resto del sistema (core.voice_engine.VoiceEngine.transcribe → OpenAI
gpt-4o-mini-transcribe, el mismo modelo del canal telefónico y del navegador),
y se limpia con los MISMOS filtros de las llamadas (services.voice.filters +
core.stt_enhancer.preprocess_stt).

El texto resultante se entrega al flujo conversacional de WhatsApp exactamente
igual que un mensaje escrito: mismo extractor, misma normalización, misma
resolución geográfica, mismo razonamiento. Solo cambia la forma de ENTRADA.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger("lyra.whatsapp.media")

# MIME de WhatsApp → content_type que entiende VoiceEngine._MIME_TO_EXT.
# Las notas de voz de WhatsApp llegan como audio/ogg (codec opus), que la API
# de transcripción acepta directamente: no hace falta transcodificar.
_MIME_ALIASES = {
    "audio/ogg": "audio/ogg",
    "audio/ogg; codecs=opus": "audio/ogg",
    "audio/opus": "audio/ogg",
    "audio/oga": "audio/ogg",
    "audio/amr": "audio/ogg",
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mp3",
    "audio/mp4": "audio/mp4",
    "audio/m4a": "audio/m4a",
    "audio/aac": "audio/m4a",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/webm": "audio/webm",
}

DEFAULT_AUDIO_MIME = "audio/ogg"


def normalize_media_mime(mime: Optional[str]) -> str:
    """Mapea el MIME reportado por WhatsApp al content_type del motor STT."""
    raw = (mime or "").strip().lower()
    if not raw:
        return DEFAULT_AUDIO_MIME
    if raw in _MIME_ALIASES:
        return _MIME_ALIASES[raw]
    base = raw.split(";", 1)[0].strip()
    return _MIME_ALIASES.get(base, DEFAULT_AUDIO_MIME)


class WhatsAppMediaError(RuntimeError):
    """Fallo al obtener el binario de una nota de voz."""


async def _get_bytes(url: str, headers: dict) -> tuple[bytes, str]:
    timeout = settings.WHATSAPP_MEDIA_TIMEOUT_SEC
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise WhatsAppMediaError(f"HTTP {resp.status_code} descargando media")
    data = resp.content or b""
    if not data:
        raise WhatsAppMediaError("media vacío")
    if len(data) > settings.WHATSAPP_MEDIA_MAX_BYTES:
        raise WhatsAppMediaError(f"media excede {settings.WHATSAPP_MEDIA_MAX_BYTES} bytes")
    return data, resp.headers.get("content-type", "")


async def download_media_via_backend(media_id: str, company_id: int = 1) -> tuple[bytes, str]:
    """
    Descarga una nota de voz a través del backend (IntelliTaxi / Laravel).

    Es la ruta PREFERIDA. El token de Meta vive por empresa en telecom_configs,
    no en el .env de Lyra; el backend lo resuelve igual que ya hace para enviar
    mensajes. Así este microservicio no necesita credenciales de Meta —ni en
    producción ni en local— y el token no se duplica en dos sitios.
    """
    url = f"{settings.INTELLITAXI_API_BASE}/admin/telecom/media/{company_id}/{media_id}"
    data, http_mime = await _get_bytes(url, {})
    return data, normalize_media_mime(http_mime)


async def download_media_by_id(media_id: str) -> tuple[bytes, str]:
    """
    Descarga una nota de voz hablándole directo a la Cloud API de Meta.

    Solo se usa como respaldo si Lyra tiene su propia WHATSAPP_API_TOKEN. Dos
    pasos, como exige la API: GET /{media_id} devuelve una URL firmada de corta
    vida, y esa URL exige el mismo Bearer token para descargarse.
    """
    token = (settings.WHATSAPP_API_TOKEN or "").strip()
    if not token:
        raise WhatsAppMediaError("WHATSAPP_API_TOKEN no configurado")

    headers = {"Authorization": f"Bearer {token}"}
    version = settings.WHATSAPP_GRAPH_VERSION or "v19.0"
    meta_url = f"https://graph.facebook.com/{version}/{media_id}"

    async with httpx.AsyncClient(timeout=settings.WHATSAPP_MEDIA_TIMEOUT_SEC) as client:
        resp = await client.get(meta_url, headers=headers)
    if resp.status_code >= 400:
        raise WhatsAppMediaError(f"HTTP {resp.status_code} consultando media {media_id}")

    info = resp.json() or {}
    file_url = info.get("url")
    if not file_url:
        raise WhatsAppMediaError(f"media {media_id} sin url")

    data, http_mime = await _get_bytes(file_url, headers)
    return data, normalize_media_mime(info.get("mime_type") or http_mime)


async def download_media_by_url(url: str, mime: Optional[str] = None) -> tuple[bytes, str]:
    """
    Descarga una nota de voz desde una URL directa.

    Es el caso del Telecom Manager (Laravel), que ya resolvió el media_id y
    reenvía la URL. Si la URL es de graph.facebook.com se le adjunta el Bearer.
    """
    headers = {}
    token = (settings.WHATSAPP_API_TOKEN or "").strip()
    if token and "graph.facebook.com" in url:
        headers["Authorization"] = f"Bearer {token}"

    data, http_mime = await _get_bytes(url, headers)
    return data, normalize_media_mime(mime or http_mime)


async def transcribe_voice_note(audio_bytes: bytes, mime: str = DEFAULT_AUDIO_MIME) -> str:
    """
    Transcribe una nota de voz con el motor STT existente y aplica los mismos
    filtros de las llamadas. Devuelve "" si no hay habla utilizable.
    """
    from core.voice_engine import get_voice_engine

    engine = get_voice_engine()
    result = await engine.transcribe(
        audio_bytes=audio_bytes,
        language=settings.VOICE_STT_LANGUAGE or "es",
        content_type=normalize_media_mime(mime),
    )

    if not result.get("success"):
        logger.info("Nota de voz sin transcripción: %s", result.get("error"))
        return ""

    text = (result.get("text") or "").strip()
    if not text:
        return ""

    # Mismos filtros que el canal telefónico: alucinaciones de silencio primero,
    # luego la normalización léxica compartida.
    from services.voice.filters import is_stt_hallucination

    if is_stt_hallucination(text):
        logger.info("Nota de voz descartada por filtro de alucinación: %r", text[:60])
        return ""

    from core.stt_enhancer import preprocess_stt

    cleaned = (preprocess_stt(text, result.get("confidence", 1.0)) or "").strip()
    return cleaned or text


async def _download(media_id: Optional[str], media_url: Optional[str],
                    mime: Optional[str], company_id: int) -> Optional[tuple[bytes, str]]:
    """Obtiene el binario de la nota de voz. None si ninguna vía funcionó.

    Orden de preferencia:
      1. media_url directa, si el Telecom Manager ya la resolvió.
      2. Proxy del backend (/admin/telecom/media) — el token vive allí.
      3. Graph API directa, solo si Lyra tiene su propia WHATSAPP_API_TOKEN.
    """
    if media_url:
        try:
            return await download_media_by_url(media_url, mime)
        except Exception as e:
            logger.warning("Descarga por URL fallida (%s); se intenta el backend.", e)

    if not media_id:
        return None

    try:
        return await download_media_via_backend(media_id, company_id)
    except Exception as e:
        logger.warning("Descarga vía backend fallida: %s", e)

    # Respaldo: solo si este microservicio tiene credenciales propias de Meta.
    if (settings.WHATSAPP_API_TOKEN or "").strip():
        try:
            return await download_media_by_id(media_id)
        except Exception as e:
            logger.warning("Descarga directa a Meta fallida: %s", e)

    return None


async def voice_note_to_text(
    *,
    media_id: Optional[str] = None,
    media_url: Optional[str] = None,
    mime: Optional[str] = None,
    company_id: int = 1,
) -> str:
    """
    Nota de voz → texto. Acepta media_id o una media_url ya resuelta.

    Devuelve "" ante cualquier fallo o audio ininteligible; el llamador decide
    qué responderle al usuario.
    """
    if not settings.WHATSAPP_AUDIO_ENABLED:
        logger.info("WHATSAPP_AUDIO_ENABLED=False — nota de voz ignorada.")
        return ""

    if not media_id and not media_url:
        return ""

    downloaded = await _download(media_id, media_url, mime, company_id)
    if downloaded is None:
        logger.warning("Nota de voz no descargable (media_id=%s).", media_id)
        return ""

    audio, resolved_mime = downloaded
    return await transcribe_voice_note(audio, mime or resolved_mime)
