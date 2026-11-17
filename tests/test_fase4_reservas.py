"""
tests/test_fase4_reservas.py — Una reserva confirmada existe en el backend.

Antes Lyra escribía directo en `agenda`, tomaba el id de la persona como si
fuera el de su ficha de cliente (la cita quedaba a nombre de OTRA persona) y
respondía "¡Listo!" sin comprobar nada. Ahora:

- la identidad sale del token verificado, no del `user_id` del navegador;
- se crea con el endpoint del backend y sólo se anuncia con el id real;
- nada se crea sin un "sí" a un resumen concreto;
- un reintento o una doble confirmación no duplican la cita;
- una respuesta perdida se resuelve consultando, no reenviando a ciegas.

El backend se simula con `httpx.MockTransport`; la prueba contra los servicios
locales reales está en `scripts/e2e_reservas_nexi.py`.

    python -m pytest tests/test_fase4_reservas.py -q
"""

import asyncio
import os
import sys
from datetime import timedelta

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.nexi_booking as nb
from tools.nexi_booking import BookingStatus

ID = {"persona_id": 61, "usuario_id": 127, "email": "cliente.uno@lyra-prueba.local", "nombre": "Cliente"}


def _manana():
    return (nb.now_local() + timedelta(days=1)).strftime("%Y-%m-%d")


def _reserva(**kw):
    return {"business_id": 103, "service_id": 1219, "date": _manana(), "start": "10:00", "end": "10:30", **kw}


def _mis_reservas(agendas):
    """Respuesta de get_reservas_perfil_tercero con esas agendas."""
    return [{"estado": "PENDIENTE", "asignaciones": [{"detalle_servicio": {
        "servicio": {"id": a.get("srv", 1219), "nombre": "Consulta"},
        "asignacion_responsable_servicio": [{"agenda": {
            "id": a["id"], "idCompany": a.get("biz", 103), "fechaInicial": a["date"],
            "horaInicial": a["start"] + ":00", "estado": a.get("estado", "AGENDADO")}}]}}]} for a in agendas]


@pytest.fixture
def backend(monkeypatch):
    """Backend simulado: registra las peticiones y responde según `estado`."""
    estado = {"reservas": [], "crear": "ok", "llamadas": []}

    def handler(request: httpx.Request):
        ruta = request.url.path
        estado["llamadas"].append(ruta)
        if ruta.endswith("/user_logged"):
            if request.headers.get("authorization") == "Bearer bueno":
                return httpx.Response(200, json={"user": {"id": 127, "email": ID["email"],
                                                          "persona": {"id": 61, "nombre1": "Cliente"}}})
            return httpx.Response(401, json={"error": "Unauthorized"})
        if ruta.endswith("/get_reservas_perfil_tercero"):
            return httpx.Response(200, json=_mis_reservas(estado["reservas"]))
        if "/store_agenda_servicio_nexiservice/" in ruta:
            modo = estado["crear"]
            if modo == "timeout_creada":
                estado["reservas"].append({"id": 900, "date": _manana(), "start": "10:00"})
                raise httpx.ReadTimeout("sin respuesta", request=request)
            if modo == "timeout_nada":
                raise httpx.ReadTimeout("sin respuesta", request=request)
            if modo == "caido":
                raise httpx.ConnectError("conexión rechazada", request=request)
            if modo == "ocupado":
                return httpx.Response(409, json={"error": "Ese horario ya no está disponible"})
            if modo == "error":
                return httpx.Response(500, json={"error": "Error al guardar la agenda"})
            nuevo = 800 + len(estado["reservas"])
            estado["reservas"].append({"id": nuevo, "date": _manana(), "start": "10:00"})
            return httpx.Response(201, json={"agenda": {"id": nuevo}})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(nb.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    nb._SESSION_CACHE.clear()
    return estado


def _crear(reserva=None):
    return asyncio.run(nb.create_booking(ID, reserva or _reserva()))


# ── identidad ───────────────────────────────────────────────────────────────

def test_sesion_verificada_con_el_token(backend):
    ident = asyncio.run(nb.verify_session("Bearer bueno"))
    assert ident["persona_id"] == 61 and ident["email"] == ID["email"]


@pytest.mark.parametrize("header", [None, "", "Bearer malo", "Basic xyz"])
def test_sin_token_valido_no_hay_identidad(backend, header):
    assert asyncio.run(nb.verify_session(header)) is None


# ── creación ────────────────────────────────────────────────────────────────

def test_se_confirma_con_id_real_y_visible_en_mis_reservas(backend):
    r = _crear()
    assert r["status"] == BookingStatus.CONFIRMED
    assert r["agenda_id"] == 800 and r["visible"] is True


def test_una_doble_confirmacion_no_duplica(backend):
    primera, segunda = _crear(), _crear()
    assert primera["agenda_id"] == segunda["agenda_id"]
    assert segunda.get("existing") is True
    assert sum("/store_agenda" in c for c in backend["llamadas"]) == 1


def test_respuesta_perdida_pero_creada_se_confirma_consultando(backend):
    backend["crear"] = "timeout_creada"
    r = _crear()
    assert r["status"] == BookingStatus.CONFIRMED and r["agenda_id"] == 900


def test_respuesta_perdida_y_no_creada_queda_en_duda(backend):
    backend["crear"] = "timeout_nada"
    assert _crear()["status"] == BookingStatus.UNKNOWN


def test_el_reintento_tras_la_duda_no_duplica(backend):
    backend["crear"] = "timeout_creada"
    _crear()
    backend["crear"] = "ok"
    r = _crear()
    assert r.get("existing") and r["agenda_id"] == 900


def test_backend_caido_no_crea_nada(backend):
    backend["crear"] = "caido"
    assert _crear()["status"] == BookingStatus.FAILED


def test_horario_tomado_lo_rechaza_el_backend(backend):
    backend["crear"] = "ocupado"
    r = _crear()
    assert r["status"] == BookingStatus.REJECTED and "disponible" in r["message"]


def test_error_del_backend_se_informa(backend):
    backend["crear"] = "error"
    r = _crear()
    assert r["status"] == BookingStatus.FAILED and r["message"]


def test_un_horario_pasado_no_se_envia(backend):
    ayer = (nb.now_local() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert _crear(_reserva(date=ayer))["status"] == BookingStatus.REJECTED
    assert not any("/store_agenda" in c for c in backend["llamadas"])


def test_una_reserva_cancelada_no_cuenta_como_existente(backend):
    backend["reservas"].append({"id": 700, "date": _manana(), "start": "10:00", "estado": "CANCELADO"})
    r = _crear()
    assert r["agenda_id"] != 700 and not r.get("existing")


# ── diálogo: el sí, el login y lo que no basta ──────────────────────────────

from core.semantic.types import ConversationState  # noqa: E402
from orchestrator.interceptors import nexiservice as icpt  # noqa: E402

RESUMEN = {"business_id": 103, "business_name": "Vida Sana", "service_id": 1219,
           "service_name": "Consulta de Medicina General", "professional_name": None,
           "responsable_id": None, "date": "2030-01-10", "start": "09:00", "end": "09:30",
           "reservation_name": None}


def _con_resumen():
    fd = {}
    out = icpt._booking_outcome({"ready": True, "status": BookingStatus.AWAITING_CONFIRMATION,
                                 "booking": RESUMEN, "message": "¿La agendo?"}, fd)
    assert "¿La agendo?" in out["reply"]
    st = ConversationState.load(fd)
    assert st.offer["intent"] == "execute_booking"
    return fd


def test_el_resumen_queda_propuesto_no_creado():
    fd = _con_resumen()
    assert icpt.get_booking_state(fd) == icpt.BookingState.WAITING_CONFIRM


def test_listo_no_basta_para_crear(monkeypatch):
    async def no(**kw):
        raise AssertionError("un 'listo' no crea reservas")
    monkeypatch.setattr(icpt, "_call_confirm_appointment", no)
    fd = _con_resumen()
    st = ConversationState.load(fd)
    out = asyncio.run(icpt._handle_execute_booking(dict(st.offer["args"]), "listo", {}, fd, st))
    assert "sí" in out["reply"] and "no" in out["reply"]


def test_aceptar_sin_sesion_pide_login_y_guarda_la_autorizacion(monkeypatch):
    async def fake(**kw):
        assert kw["execute"] is True
        return {"success": False, "needs_auth": True, "status": BookingStatus.AWAITING_AUTH,
                "pending_reservation": {**RESUMEN, "authorized": True}, "message": "Inicia sesión"}
    monkeypatch.setattr(icpt, "_call_confirm_appointment", fake)
    fd = _con_resumen()
    st = ConversationState.load(fd)
    out = asyncio.run(icpt._handle_execute_booking(dict(st.offer["args"]), "sí", {}, fd, st))
    assert out["voice_action"] == "require_auth"
    # Lo que viaja al navegador no lleva ids internos.
    assert "business_id" not in out["voice_action_payload"]["pending"]
    pend = out["final_data"]["_pending_reservation"]
    assert pend["authorized"] is True and pend["expires_at"] > 0


@pytest.mark.parametrize("args,cambia", [
    ({"time": "09"}, False), ({"time": "9:00"}, False), ({"date": "2030-01-10"}, False),
    ({"time": "10:00"}, True), ({"business_id": 5}, True), ({"service_name": "Consulta"}, False),
    ({"service_name": "Odontología"}, True), ({}, False),
])
def test_cambia_algun_dato_del_resumen(args, cambia):
    ofrecido = icpt._booking_args_from_pending(RESUMEN)
    assert icpt._changes_booking(args, ofrecido) is cambia


def test_aceptar_un_resumen_por_voz_con_cliticos():
    from core.semantic.engine import offer_reply
    from core.semantic.speech_act import analyze
    for frase in ("confírmala", "sí, agéndala", "agéndalo por favor", "dale"):
        assert offer_reply(frase, analyze(frase)) == "accept", frase
