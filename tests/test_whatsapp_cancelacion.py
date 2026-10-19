"""
tests/test_whatsapp_cancelacion.py — Cancelar de verdad, no solo en Lyra.

El servicio lo crea y lo publica el backend. Limpiar la sesión de Lyra no lo
cancela: seguía visible para conductores y operadores. Estos tests fijan que
cancelar SIEMPRE llama al backend y que el mensaje al usuario refleja lo que
realmente pasó allá.
"""

import asyncio

import pytest

from api.routers import whatsapp as wa


# ── Mensaje honesto según el resultado del backend ──────────────────────────

def test_mensaje_confirma_solo_si_el_backend_cancelo():
    msg = wa._mensaje_cancelacion({"ok": True, "cancelados": 1, "con_conductor": 0})
    assert msg == wa._CANCEL_OK


def test_mensaje_no_miente_si_el_backend_falla():
    msg = wa._mensaje_cancelacion({"ok": False, "cancelados": 0, "con_conductor": 0})
    assert msg == wa._CANCEL_FALLO
    assert "cancel" in msg.lower()  # explica, pero no afirma que quedó cancelado


def test_mensaje_avisa_si_ya_lo_tomo_un_conductor():
    msg = wa._mensaje_cancelacion({"ok": True, "cancelados": 0, "con_conductor": 1})
    assert msg == wa._CANCEL_CON_CONDUCTOR


def test_mensaje_cuando_no_habia_nada_que_cancelar():
    msg = wa._mensaje_cancelacion({"ok": True, "cancelados": 0, "con_conductor": 0})
    assert msg == wa._CANCEL_NADA_QUE_CANCELAR


# ── Llamada al backend ──────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}
        self.text = str(self._data)

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, response, registro):
        self._response = response
        self._registro = registro

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self._registro.append((url, json))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_httpx(monkeypatch, response):
    registro = []
    monkeypatch.setattr(
        wa.httpx, "AsyncClient", lambda *a, **k: _FakeClient(response, registro)
    )
    return registro


def test_cancelar_llama_al_endpoint_del_backend(monkeypatch):
    registro = _patch_httpx(
        monkeypatch,
        _FakeResponse(200, {"ok": True, "cancelados": 1, "servicio_ids": [42], "con_conductor": 0}),
    )

    resultado = asyncio.run(wa._cancelar_servicio_backend("573001112233", 3))

    assert resultado == {"ok": True, "cancelados": 1, "con_conductor": 0}
    assert len(registro) == 1
    url, payload = registro[0]
    assert url.endswith("/taxi/solicitud-telefonica/cancelar")
    assert payload["telefono"] == "573001112233"
    assert payload["company_id"] == 3
    assert payload["motivo"]


def test_error_http_del_backend_no_propaga(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResponse(500, {"message": "boom"}))
    assert asyncio.run(wa._cancelar_servicio_backend("573001112233", 1)) == {
        "ok": False, "cancelados": 0, "con_conductor": 0
    }


def test_backend_caido_no_propaga(monkeypatch):
    _patch_httpx(monkeypatch, RuntimeError("connection refused"))
    assert asyncio.run(wa._cancelar_servicio_backend("573001112233", 1)) == {
        "ok": False, "cancelados": 0, "con_conductor": 0
    }


# ── Integración: texto y nota de voz llegan al mismo punto ──────────────────

@pytest.mark.parametrize("texto", [
    "cancelar",
    "quiero cancelar el servicio",
    "mejor cancela",
    "cancelar servicio",
])
def test_cancelar_por_texto_pide_cancelacion_al_backend(monkeypatch, texto):
    llamadas = []
    enviados = []

    async def _fake_cancel(phone, company_id):
        llamadas.append((phone, company_id))
        return {"ok": True, "cancelados": 1, "con_conductor": 0}

    async def _fake_send(to, text):
        enviados.append(text)

    async def _sin_reactivacion(phone, company_id):
        return False, None

    monkeypatch.setattr(wa, "_cancelar_servicio_backend", _fake_cancel)
    monkeypatch.setattr(wa, "send_whatsapp_message", _fake_send)
    monkeypatch.setattr(wa, "_tiene_reactivacion_pendiente", _sin_reactivacion)

    asyncio.run(wa.process_whatsapp_message("573001112233", texto, 5))

    assert llamadas == [("573001112233", 5)], "cancelar debe llegar al backend"
    assert enviados == [wa._CANCEL_OK]


def test_nota_de_voz_cancelando_usa_el_mismo_camino(monkeypatch):
    """La nota de voz entra por process_whatsapp_message: mismo fix, sin duplicar."""
    from services import whatsapp_media

    llamadas = []
    enviados = []

    async def _fake_voice_to_text(**kwargs):
        return "cancelar por favor"

    async def _fake_cancel(phone, company_id):
        llamadas.append((phone, company_id))
        return {"ok": True, "cancelados": 1, "con_conductor": 0}

    async def _fake_send(to, text):
        enviados.append(text)

    async def _sin_reactivacion(phone, company_id):
        return False, None

    monkeypatch.setattr(whatsapp_media, "voice_note_to_text", _fake_voice_to_text)
    monkeypatch.setattr(wa, "_cancelar_servicio_backend", _fake_cancel)
    monkeypatch.setattr(wa, "send_whatsapp_message", _fake_send)
    monkeypatch.setattr(wa, "_tiene_reactivacion_pendiente", _sin_reactivacion)

    asyncio.run(wa.process_whatsapp_voice_note("573001112233", "MEDIA-1", None, None, 5))

    assert llamadas == [("573001112233", 5)]
    assert enviados == [wa._CANCEL_OK]


def test_sesion_se_limpia_aunque_el_backend_falle(monkeypatch):
    enviados = []

    async def _fake_cancel(phone, company_id):
        return {"ok": False, "cancelados": 0, "con_conductor": 0}

    async def _fake_send(to, text):
        enviados.append(text)

    async def _sin_reactivacion(phone, company_id):
        return False, None

    monkeypatch.setattr(wa, "_cancelar_servicio_backend", _fake_cancel)
    monkeypatch.setattr(wa, "send_whatsapp_message", _fake_send)
    monkeypatch.setattr(wa, "_tiene_reactivacion_pendiente", _sin_reactivacion)

    sess = wa.get_wp_session("573009998877", 1)
    sess.state = wa.STATE_WAITING_ORIGIN

    asyncio.run(wa.process_whatsapp_message("573009998877", "cancelar", 1))

    assert wa.get_wp_session("573009998877", 1).state == wa.STATE_NEW
    assert enviados == [wa._CANCEL_FALLO]
