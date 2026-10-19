"""
tests/test_whatsapp_conversacion.py — Entender al usuario, no solo direcciones.

Dos fallas reales que cubren estas pruebas:

1. Una nota de voz con "Hola, buenos días" respondía "no encontré esa dirección".
   El STT escribe con tilde ("días") y los vocabularios de saludos estaban sin
   tilde, así que el saludo no se reconocía y el texto acababa en el extractor
   de direcciones.

2. Al corregir ("no, es 3C-6") el bot borraba el origen y volvía a pedir la
   dirección completa, en vez de aplicar la corrección sobre la que ya tenía.
"""

import asyncio

import pytest

from api.routers import whatsapp as wa


@pytest.fixture(autouse=True)
def _sesion_limpia():
    wa.reset_wp_session("573001112233")
    yield
    wa.reset_wp_session("573001112233")


@pytest.fixture(autouse=True)
def _sin_reactivacion(monkeypatch):
    async def _no(phone, company_id):
        return False, None

    monkeypatch.setattr(wa, "_tiene_reactivacion_pendiente", _no)


def _capturar_salida(monkeypatch):
    enviados = []

    async def _send(to, text):
        enviados.append(text)

    async def _send_buttons(to, text, buttons):
        enviados.append(text)

    async def _send_loc(to, text):
        enviados.append(text)

    monkeypatch.setattr(wa, "send_whatsapp_message", _send)
    monkeypatch.setattr(wa, "send_whatsapp_interactive_buttons", _send_buttons)
    monkeypatch.setattr(wa, "send_whatsapp_location_request", _send_loc)
    return enviados


# ── Plegado de tildes ───────────────────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    "Hola, buenos días",
    "hola buenos dias",
    "Buenos días",
    "Buenas tardes",
    "¡Hola!",
    "Buenas noches",
])
def test_saludos_con_y_sin_tilde_se_reconocen(monkeypatch, texto):
    """Un saludo NUNCA debe terminar en el extractor de direcciones."""
    enviados = _capturar_salida(monkeypatch)

    async def _no_debe_extraer(*a, **k):
        raise AssertionError("un saludo no debe llegar al extractor de direcciones")

    monkeypatch.setattr(wa, "extract_address_llm", _no_debe_extraer)

    asyncio.run(wa.process_whatsapp_message("573001112233", texto, 1))

    assert len(enviados) == 1
    assert "servicio necesitas" in enviados[0].lower()


def test_fold_pliega_tildes_y_puntuacion():
    assert wa._fold("¡Hola, buenos días!") == "hola buenos dias"
    assert wa._fold("¿Cuánto vale?") == "cuanto vale"
    assert wa._fold("") == ""


def test_pregunta_con_tilde_se_detecta_como_conversacion():
    assert wa.is_conversational_query("cuanto cobran") is True
    assert wa.is_conversational_query("cuánto cobran") is True
    assert wa.is_conversational_query("Cra 9 #73N-200") is False


# ── Nota de voz saludando ───────────────────────────────────────────────────

def test_nota_de_voz_saludando_no_pide_direccion(monkeypatch):
    from services import whatsapp_media

    enviados = _capturar_salida(monkeypatch)

    async def _transcribe(**kwargs):
        return "Hola, buenos días"

    async def _no_debe_extraer(*a, **k):
        raise AssertionError("un saludo no debe llegar al extractor de direcciones")

    monkeypatch.setattr(whatsapp_media, "voice_note_to_text", _transcribe)
    monkeypatch.setattr(wa, "extract_address_llm", _no_debe_extraer)

    asyncio.run(wa.process_whatsapp_voice_note("573001112233", "MEDIA-1", None, None, 1))

    assert len(enviados) == 1
    assert "servicio necesitas" in enviados[0].lower()


# ── Corrección parcial durante la confirmación ──────────────────────────────

def _sesion_confirmando(direccion="Cra. 52 #3B-6"):
    sess = wa.get_wp_session("573001112233", 1)
    sess.state = wa.STATE_CONFIRMING_ORIGIN
    sess.tipo_servicio = "taxi ahora"
    sess.origen_text = direccion
    return sess


@pytest.mark.parametrize("correccion,esperado", [
    ("no, es 3C-6", "Cra. 52 #3C-6"),
    ("no es #3c", "Cra. 52 #3C-6"),
    ("No 17-25", "Cra. 52 #17-25"),
])
def test_correccion_parcial_actualiza_la_direccion_sin_pedirla_de_nuevo(
    monkeypatch, correccion, esperado
):
    enviados = _capturar_salida(monkeypatch)

    async def _no_debe_finalizar(phone, sess):
        raise AssertionError("debe re-confirmar, no crear el servicio aún")

    monkeypatch.setattr(wa, "_finalizar_taxi", _no_debe_finalizar)
    _sesion_confirmando()

    asyncio.run(wa.process_whatsapp_message("573001112233", correccion, 1))

    sess = wa.get_wp_session("573001112233", 1)
    assert sess.origen_text == esperado, "la dirección debe quedar actualizada"
    assert sess.state == wa.STATE_CONFIRMING_ORIGIN
    assert len(enviados) == 1
    assert esperado in enviados[0]
    assert "Corrijo" in enviados[0]


def test_correccion_completa_en_el_mismo_mensaje_no_vuelve_a_preguntar(monkeypatch):
    enviados = _capturar_salida(monkeypatch)

    async def _extract(texto, kind="pickup"):
        return "Calle 5 #12-20"

    monkeypatch.setattr(wa, "extract_address_llm", _extract)
    monkeypatch.setattr(wa, "looks_like_place", lambda t: True)
    _sesion_confirmando()

    asyncio.run(wa.process_whatsapp_message("573001112233", "no, es en la calle 5 #12-20", 1))

    sess = wa.get_wp_session("573001112233", 1)
    assert sess.origen_text == "Calle 5 #12-20"
    assert sess.state == wa.STATE_CONFIRMING_ORIGIN
    assert "Calle 5 #12-20" in enviados[0]


def test_no_a_secas_si_vuelve_a_pedir_la_direccion(monkeypatch):
    enviados = _capturar_salida(monkeypatch)
    _sesion_confirmando()

    asyncio.run(wa.process_whatsapp_message("573001112233", "no", 1))

    sess = wa.get_wp_session("573001112233", 1)
    assert sess.origen_text is None
    assert sess.state == wa.STATE_WAITING_ORIGIN
    assert "dirección te recogemos" in enviados[0]


def test_si_confirma_crea_el_servicio(monkeypatch):
    _capturar_salida(monkeypatch)
    llamado = []

    async def _finalizar(phone, sess):
        llamado.append(sess.origen_text)

    monkeypatch.setattr(wa, "_finalizar_taxi", _finalizar)
    _sesion_confirmando()

    asyncio.run(wa.process_whatsapp_message("573001112233", "sí", 1))

    assert llamado == ["Cra. 52 #3B-6"], "confirmar no debe alterar la dirección"
