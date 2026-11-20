"""
tests/test_reservation_auth.py — Una reserva necesita una cuenta, no un nombre.

Antes bastaba con escribir un nombre en el chat para que la cita quedara creada.
Eso deja una reserva que nadie puede verificar: no hay a quién avisar, el cliente
no puede consultarla ni cancelarla, y el negocio recibe una cita a nombre de un
texto suelto.

Ahora lo acordado se guarda entero y se pide entrar a la cuenta. Cuando el
usuario vuelve autenticado, la cita se cierra sola: no se le pregunta nada dos
veces.

    python -m pytest tests/test_reservation_auth.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.interceptors.nexiservice import (
    BookingState,
    _remember_pending_reservation,
    _resume_pending_reservation,
    get_booking_state,
    is_authenticated,
)
from tools.nexiservice import _format_slot, _natural_list


# ═══════════════════════════════════════════════════════════════════════════════
# 1. QUIÉN CUENTA COMO AUTENTICADO
# ═══════════════════════════════════════════════════════════════════════════════

SESION = {"external_user_id": "61",
          "verified_identity": {"persona_id": 61, "email": "cliente.uno@lyra-prueba.local"}}


@pytest.mark.parametrize("user_data,expected", [
    # Sólo cuenta la identidad verificada con el token, no el id que manda el navegador.
    (SESION, True),
    ({"external_user_id": "1"}, False),
    ({"external_user_id": 42}, False),
    ({"external_user_id": "1", "verified_identity": None}, False),
    ({"external_user_id": "user_client_demo"}, False),
    ({"external_user_id": "anon_9f3c"}, False),
    ({}, False),
])
def test_reconocimiento_de_sesion(user_data, expected):
    assert is_authenticated(user_data) is expected


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LA RESERVA SE GUARDA ENTERA MIENTRAS ESPERA
# ═══════════════════════════════════════════════════════════════════════════════

PENDIENTE = {
    "business_id": 103,
    "business_name": "Consultorio Médico Vida Sana Popayán",
    "service_name": "Consulta de Medicina General",
    "professional_name": "Valentina García",
    "time": "09:00",
    "date": "tomorrow",
}


def test_lo_acordado_se_guarda_completo():
    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)

    assert get_booking_state(final_data) == BookingState.WAITING_AUTH
    assert final_data["needs_auth"] is True
    # Nada de lo hablado se pierde: al volver no hay que repetirlo.
    for campo, valor in PENDIENTE.items():
        assert final_data["_pending_reservation"][campo] == valor


def test_la_reserva_pendiente_sobrevive_al_guardado():
    """El estado viaja a la base entre turnos; debe volver intacto."""
    import json

    from core.semantic.types import ConversationState

    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)
    restaurado = json.loads(json.dumps(final_data))

    assert restaurado["_pending_reservation"]["time"] == "09:00"
    estado = ConversationState.load(restaurado)
    assert estado.booking["service_name"] == "Consulta de Medicina General"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. AL ENTRAR, LA CITA SE CIERRA SOLA
# ═══════════════════════════════════════════════════════════════════════════════

def test_al_iniciar_sesion_la_reserva_aceptada_se_crea(monkeypatch):
    """Aceptada antes del login: al entrar se vuelve a validar y se crea, sin preguntar."""
    llamadas = []

    async def fake_confirm(**kwargs):
        llamadas.append(kwargs)
        return {
            "success": True, "status": "confirmed", "agenda_id": 77,
            "message": "¡Listo! Quedó agendada. Tu número de reserva es #77.",
            "url": "/perfil/mis-reservas",
        }

    monkeypatch.setattr(
        "orchestrator.interceptors.nexiservice._call_confirm_appointment", fake_confirm
    )

    final_data = {}
    _remember_pending_reservation({**PENDIENTE, "authorized": True}, final_data)

    out = asyncio.run(_resume_pending_reservation(SESION, final_data))

    assert out is not None
    assert "#77" in out["reply"]
    # Se crea (execute) con lo acordado, revalidándolo en la herramienta.
    assert llamadas[0]["execute"] is True
    assert llamadas[0]["business_id"] == 103
    assert llamadas[0]["service_name"] == "Consulta de Medicina General"
    assert llamadas[0]["time"] == "09:00"
    assert get_booking_state(out["final_data"]) == BookingState.IDLE
    assert "needs_auth" not in out["final_data"]


def test_entrar_no_autoriza_una_reserva_no_aceptada(monkeypatch):
    """Sin un sí previo, iniciar sesión sólo trae el resumen: no crea nada."""
    async def no_debe_llamarse(**kwargs):
        raise AssertionError("iniciar sesión no es autorizar la reserva")

    monkeypatch.setattr(
        "orchestrator.interceptors.nexiservice._call_confirm_appointment", no_debe_llamarse
    )
    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)
    out = asyncio.run(_resume_pending_reservation(SESION, final_data))
    assert "¿La agendo?" in out["reply"]


def test_la_reserva_de_otra_sesion_no_se_ejecuta(monkeypatch):
    async def no_debe_llamarse(**kwargs):
        raise AssertionError("no se reserva a nombre de otra persona")

    monkeypatch.setattr(
        "orchestrator.interceptors.nexiservice._call_confirm_appointment", no_debe_llamarse
    )
    final_data = {}
    _remember_pending_reservation({**PENDIENTE, "authorized": True}, final_data, owner=99)
    out = asyncio.run(_resume_pending_reservation(SESION, final_data))
    assert "descarté" in out["reply"]
    assert not out["final_data"].get("_pending_reservation")


def test_la_reserva_en_espera_caduca(monkeypatch):
    final_data = {}
    _remember_pending_reservation({**PENDIENTE, "authorized": True}, final_data)
    final_data["_pending_reservation"]["expires_at"] = 1.0
    assert asyncio.run(_resume_pending_reservation(SESION, final_data)) is None
    assert get_booking_state(final_data) == BookingState.IDLE


def test_si_el_horario_se_ocupo_el_proceso_no_se_pierde(monkeypatch):
    async def fake_confirm(**kwargs):
        return {"success": False, "asking": "time", "needs_input": True,
                "message": "Esa hora ya está tomada. Puedo dejártela a las 10:00."}

    monkeypatch.setattr(
        "orchestrator.interceptors.nexiservice._call_confirm_appointment", fake_confirm
    )

    from core.semantic.types import ConversationState

    final_data = {}
    _remember_pending_reservation({**PENDIENTE, "authorized": True}, final_data)
    out = asyncio.run(_resume_pending_reservation(SESION, final_data))

    assert "tomada" in out["reply"]
    # Lo acordado sigue en curso: el usuario elige otra hora sin empezar de cero.
    estado = ConversationState.load(out["final_data"])
    assert estado.booking["business_id"] == 103
    assert estado.pending_slot == "time"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. NADA DE "None" NI DE LISTAS TELEGRÁFICAS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("slot,expected", [
    ({"start": "07:00", "end": "07:30"}, "de 07:00 a 07:30"),
    ({"start": "07:00", "end": None}, "a las 07:00"),      # horaFinal nula
    ({"start": "10:00", "end": "10:00"}, "a las 10:00"),
    ({"start": None, "end": None}, ""),
])
def test_un_tramo_ocupado_se_dice_en_castellano(slot, expected):
    """El caso que se veía en pantalla: "07:00 - None"."""
    resultado = _format_slot(slot)
    assert resultado == expected
    assert "None" not in resultado


@pytest.mark.parametrize("items,expected", [
    ([], ""),
    (["a las 07:00"], "a las 07:00"),
    (["a las 07:00", "a las 10:00"], "a las 07:00 y a las 10:00"),
    (["a", "b", "c"], "a, b y c"),
])
def test_las_enumeraciones_llevan_y_al_final(items, expected):
    assert _natural_list(items) == expected


def test_ningun_mensaje_del_flujo_lleva_saltos_escapados():
    """
    En la confirmación se coló un "\\n\\n" literal, que el usuario vio impreso.
    """
    import io

    fuente = io.open("tools/nexiservice.py", encoding="utf-8").read()
    assert "\\\\n" not in fuente, "hay un salto de línea escapado en un mensaje"
