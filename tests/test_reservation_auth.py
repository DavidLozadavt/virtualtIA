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

@pytest.mark.parametrize("user_data,expected", [
    ({"external_user_id": "1"}, True),
    ({"external_user_id": 42}, True),
    ({"external_user_id": "user_client_demo"}, False),
    ({"external_user_id": "anon_9f3c"}, False),
    ({"external_user_id": "guest"}, False),
    ({"external_user_id": "unknown"}, False),
    ({"external_user_id": ""}, False),
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

def test_al_iniciar_sesion_la_reserva_se_confirma(monkeypatch):
    llamadas = []

    async def fake_confirm(**kwargs):
        llamadas.append(kwargs)
        return {
            "success": True,
            "message": "¡Listo, Sofía Restrepo! Tu cita queda para mañana a las 09:00.",
            "url": "/perfil/mis-reservas",
        }

    monkeypatch.setattr(
        "orchestrator.interceptors.nexiservice._call_confirm_appointment", fake_confirm
    )

    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)

    out = asyncio.run(_resume_pending_reservation({"external_user_id": "1"}, final_data))

    assert out is not None
    assert "Listo" in out["reply"]
    # Se confirmó con lo que ya se había acordado, sin volver a preguntar.
    assert llamadas[0]["business_id"] == 103
    assert llamadas[0]["service_name"] == "Consulta de Medicina General"
    assert llamadas[0]["time"] == "09:00"
    # Y el nombre sale de la cuenta, no de un texto escrito en el chat.
    assert llamadas[0]["reservation_name"] is None
    # El proceso queda cerrado.
    assert get_booking_state(out["final_data"]) == BookingState.IDLE
    assert "needs_auth" not in out["final_data"]


def test_si_la_confirmacion_falla_el_proceso_no_se_pierde(monkeypatch):
    async def fake_confirm(**kwargs):
        return {"success": False, "message": "El horario ya no está libre."}

    monkeypatch.setattr(
        "orchestrator.interceptors.nexiservice._call_confirm_appointment", fake_confirm
    )

    final_data = {}
    _remember_pending_reservation(PENDIENTE, final_data)
    out = asyncio.run(_resume_pending_reservation({"external_user_id": "1"}, final_data))

    assert "horario" in out["reply"]
    # Sigue guardada: el usuario puede elegir otra hora sin empezar de cero.
    assert out["final_data"]["_pending_reservation"]["business_id"] == 103


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
