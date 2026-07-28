"""
tests/test_whatsapp_voice_note.py — Notas de voz de WhatsApp.

Verifica que la nota de voz solo cambia la FORMA DE ENTRADA: se descarga, se
transcribe con el motor STT existente y el texto entra por el mismo
process_whatsapp_message que un mensaje escrito. Sin pipeline nuevo.
"""

import asyncio

import pytest

from core.config import settings
from services import whatsapp_media
from services.whatsapp_media import (
    DEFAULT_AUDIO_MIME,
    normalize_media_mime,
    transcribe_voice_note,
    voice_note_to_text,
)


# ── MIME ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ("audio/ogg", "audio/ogg"),
    ("audio/ogg; codecs=opus", "audio/ogg"),
    ("audio/OGG; codecs=opus", "audio/ogg"),
    ("audio/mpeg", "audio/mpeg"),
    ("audio/aac", "audio/m4a"),
    ("application/octet-stream", DEFAULT_AUDIO_MIME),
    ("", DEFAULT_AUDIO_MIME),
    (None, DEFAULT_AUDIO_MIME),
])
def test_normalize_media_mime(entrada, esperado):
    assert normalize_media_mime(entrada) == esperado


# ── Transcripción: reutiliza el motor existente ─────────────────────────────

class _FakeEngine:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def transcribe(self, audio_bytes, language="es", content_type="audio/webm"):
        self.calls.append((audio_bytes, language, content_type))
        return self.result


def _patch_engine(monkeypatch, result):
    engine = _FakeEngine(result)
    import core.voice_engine as ve

    monkeypatch.setattr(ve, "get_voice_engine", lambda: engine)
    return engine


def test_transcribe_usa_el_motor_stt_existente(monkeypatch):
    engine = _patch_engine(monkeypatch, {"success": True, "text": "Recógeme en Campanario", "confidence": 1.0})

    texto = asyncio.run(transcribe_voice_note(b"x" * 500, "audio/ogg; codecs=opus"))

    assert "Campanario" in texto
    assert len(engine.calls) == 1
    audio, language, content_type = engine.calls[0]
    assert audio == b"x" * 500
    assert language == (settings.VOICE_STT_LANGUAGE or "es")
    assert content_type == "audio/ogg"


def test_transcripcion_fallida_devuelve_vacio(monkeypatch):
    _patch_engine(monkeypatch, {"success": False, "text": "", "error": "No se detectó voz en el audio."})
    assert asyncio.run(transcribe_voice_note(b"x" * 500)) == ""


def test_alucinacion_de_silencio_se_descarta(monkeypatch):
    from services.voice.filters import is_stt_hallucination

    frase = "Subtítulos realizados por la comunidad de Amara.org"
    assert is_stt_hallucination(frase), "precondición: el filtro compartido la detecta"

    _patch_engine(monkeypatch, {"success": True, "text": frase, "confidence": 1.0})
    assert asyncio.run(transcribe_voice_note(b"x" * 500)) == ""


# ── Descarga ────────────────────────────────────────────────────────────────

def test_flag_desactivada_ignora_la_nota(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_AUDIO_ENABLED", False)
    assert asyncio.run(voice_note_to_text(media_id="123")) == ""


def test_sin_media_id_ni_url_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_AUDIO_ENABLED", True)
    assert asyncio.run(voice_note_to_text()) == ""


def test_fallo_de_descarga_no_propaga_excepcion(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_AUDIO_ENABLED", True)
    monkeypatch.setattr(settings, "WHATSAPP_API_TOKEN", "")

    async def _boom(media_id, company_id=1):
        raise whatsapp_media.WhatsAppMediaError("HTTP 404 descargando media")

    monkeypatch.setattr(whatsapp_media, "download_media_via_backend", _boom)
    assert asyncio.run(voice_note_to_text(media_id="123")) == ""


def test_descarga_va_por_el_backend_no_por_meta(monkeypatch):
    """El token de Meta vive en telecom_configs del backend, no en Lyra."""
    monkeypatch.setattr(settings, "WHATSAPP_AUDIO_ENABLED", True)
    monkeypatch.setattr(settings, "WHATSAPP_API_TOKEN", "")

    visto = {}

    async def _fake_backend(media_id, company_id=1):
        visto["args"] = (media_id, company_id)
        return b"a" * 800, "audio/ogg"

    async def _meta_no_debe_llamarse(media_id):
        raise AssertionError("no debe hablarle directo a Meta")

    monkeypatch.setattr(whatsapp_media, "download_media_via_backend", _fake_backend)
    monkeypatch.setattr(whatsapp_media, "download_media_by_id", _meta_no_debe_llamarse)
    _patch_engine(monkeypatch, {"success": True, "text": "necesito un taxi", "confidence": 1.0})

    assert asyncio.run(voice_note_to_text(media_id="MEDIA-1", company_id=7)) == "necesito un taxi"
    assert visto["args"] == ("MEDIA-1", 7)


def test_respaldo_a_meta_solo_si_lyra_tiene_token(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_AUDIO_ENABLED", True)
    monkeypatch.setattr(settings, "WHATSAPP_API_TOKEN", "EAAG-token-propio")

    async def _backend_caido(media_id, company_id=1):
        raise whatsapp_media.WhatsAppMediaError("HTTP 502")

    async def _fake_meta(media_id):
        return b"a" * 800, "audio/ogg"

    monkeypatch.setattr(whatsapp_media, "download_media_via_backend", _backend_caido)
    monkeypatch.setattr(whatsapp_media, "download_media_by_id", _fake_meta)
    _patch_engine(monkeypatch, {"success": True, "text": "un taxi por favor", "confidence": 1.0})

    assert asyncio.run(voice_note_to_text(media_id="MEDIA-1")) == "un taxi por favor"


def test_media_url_tiene_prioridad(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_AUDIO_ENABLED", True)

    async def _fake_url(url, mime=None):
        assert url == "https://cdn.local/nota.ogg"
        return b"a" * 800, "audio/ogg"

    async def _backend_no_debe_llamarse(media_id, company_id=1):
        raise AssertionError("con media_url no se consulta el backend")

    monkeypatch.setattr(whatsapp_media, "download_media_by_url", _fake_url)
    monkeypatch.setattr(whatsapp_media, "download_media_via_backend", _backend_no_debe_llamarse)
    _patch_engine(monkeypatch, {"success": True, "text": "hola", "confidence": 1.0})

    assert asyncio.run(
        voice_note_to_text(media_url="https://cdn.local/nota.ogg")
    ) == "hola"


# ── Integración con el flujo conversacional ─────────────────────────────────

def test_nota_de_voz_entra_por_el_mismo_process_whatsapp_message(monkeypatch):
    from api.routers import whatsapp as wa

    async def _fake_voice_to_text(**kwargs):
        assert kwargs["media_id"] == "MEDIA-9"
        assert kwargs["company_id"] == 7
        return "recógeme en el Campanario"

    recibido = {}

    async def _fake_process(phone, message, company_id=1):
        recibido["args"] = (phone, message, company_id)

    monkeypatch.setattr(whatsapp_media, "voice_note_to_text", _fake_voice_to_text)
    monkeypatch.setattr(wa, "process_whatsapp_message", _fake_process)

    asyncio.run(wa.process_whatsapp_voice_note("573001112233", "MEDIA-9", None, "audio/ogg", 7))

    assert recibido["args"] == ("573001112233", "recógeme en el Campanario", 7)


def test_nota_ininteligible_avisa_al_usuario_y_no_procesa(monkeypatch):
    from api.routers import whatsapp as wa

    async def _fake_voice_to_text(**kwargs):
        return ""

    enviados = []
    llamado = []

    async def _fake_send(to, text):
        enviados.append((to, text))

    async def _fake_process(phone, message, company_id=1):
        llamado.append(message)

    monkeypatch.setattr(whatsapp_media, "voice_note_to_text", _fake_voice_to_text)
    monkeypatch.setattr(wa, "send_whatsapp_message", _fake_send)
    monkeypatch.setattr(wa, "process_whatsapp_message", _fake_process)

    asyncio.run(wa.process_whatsapp_voice_note("573001112233", "MEDIA-X"))

    assert llamado == []
    assert len(enviados) == 1
    assert "nota de voz" in enviados[0][1].lower()
