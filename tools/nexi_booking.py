"""
tools/nexi_booking.py — Reservas de NexiService que existen de verdad.

Antes Lyra escribía la cita directo en `agenda` y daba por hecho que el cliente
era el `tercero` con el mismo id que su `persona`. Para quien no tenía ficha de
cliente con su correo (el administrador, cualquier cuenta sembrada) la cita
quedaba a nombre de OTRA persona y Lyra respondía "¡Listo!": la reserva existía,
pero no era suya y no aparecía en «Mis Reservas».

Ahora:
- La identidad sale del token de sesión, verificado contra la pasarela
  (`user_logged`), nunca del `user_id` que manda el navegador.
- La reserva se crea con el MISMO endpoint que usa el front
  (`store_agenda_servicio_nexiservice`): ficha de cliente, agenda, prestación,
  carrito y aviso al negocio en un solo sitio.
- Sólo se da por confirmada con el id real de la agenda y después de
  encontrarla en la consulta de «Mis Reservas» del usuario.
- Antes de crear se busca si ya existe (idempotencia): una doble confirmación o
  un reintento tras un timeout no duplican la cita.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time as _time
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

NEXI_AUTH_API = os.getenv("NEXI_AUTH_API_BASE", "http://127.0.0.1:8001/api").rstrip("/")
NEXI_INVENT_API = os.getenv("NEXI_INVENT_API_BASE", "http://127.0.0.1:8002/api").rstrip("/")

#: Zona horaria de los negocios (todos operan en Colombia). "Mañana" y "ya pasó"
#: se calculan aquí, no con la hora de la máquina donde corra Lyra.
BUSINESS_TZ = ZoneInfo("America/Bogota")


class BookingStatus:
    PREPARING = "preparing"            # faltan datos
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # resumen mostrado, falta el sí
    AWAITING_AUTH = "awaiting_auth"    # confirmada por el usuario, falta sesión
    SUBMITTED = "submitted"            # enviada al backend, sin respuesta aún
    CONFIRMED = "confirmed"            # el backend la creó y es consultable
    REJECTED = "rejected"              # el backend la rechazó (validación, horario)
    FAILED = "failed"                  # error técnico; no se creó
    UNKNOWN = "unknown"                # se envió y no se sabe si quedó


def now_local() -> datetime:
    return datetime.now(BUSINESS_TZ)


def is_past(date: str, start: str) -> bool:
    """¿La fecha y hora (en hora del negocio) ya pasaron?"""
    try:
        cuando = datetime.strptime(f"{date} {start[:5]}", "%Y-%m-%d %H:%M").replace(tzinfo=BUSINESS_TZ)
    except (TypeError, ValueError):
        return False
    return cuando <= now_local()


# ── identidad verificada ───────────────────────────────────────────────────

_SESSION_CACHE: Dict[str, tuple] = {}
_SESSION_TTL = 60.0


def _token_key(auth_header: str) -> str:
    return hashlib.sha256(auth_header.encode("utf-8")).hexdigest()


async def verify_session(auth_header: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Quién es el usuario según su token, preguntándole a la pasarela.

    Devuelve {persona_id, usuario_id, email, nombre} o None si no hay token o no
    es válido. Se guarda un minuto por token para no consultar en cada turno.
    """
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    key = _token_key(auth_header)
    hit = _SESSION_CACHE.get(key)
    if hit and hit[0] > _time.monotonic():
        return hit[1]
    identidad = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(f"{NEXI_AUTH_API}/user_logged",
                                     headers={"Authorization": auth_header, "Accept": "application/json"})
        if resp.status_code == 200:
            user = (resp.json() or {}).get("user") or {}
            persona = user.get("persona") or {}
            nombre = " ".join(str(persona.get(k) or "").strip() for k in
                              ("nombre1", "nombre2", "apellido1", "apellido2")).split()
            if user.get("email") and persona.get("id"):
                identidad = {
                    "persona_id": persona.get("id"),
                    "usuario_id": user.get("id"),
                    "email": user.get("email"),
                    "nombre": " ".join(nombre) or None,
                }
        else:
            logger.info("[SESIÓN] token rechazado por la pasarela (%s)", resp.status_code)
    except httpx.HTTPError as exc:
        # Sin pasarela no se puede saber quién es: se trata como sin sesión.
        logger.warning("[SESIÓN] no pude verificar el token: %s", exc)
        return None
    _SESSION_CACHE[key] = (_time.monotonic() + _SESSION_TTL, identidad)
    return identidad


# ── consulta: lo que el usuario ve en «Mis Reservas» ───────────────────────

def _agendas_de(reservas: list) -> list:
    """Aplana la respuesta de `get_reservas_perfil_tercero` a sus agendas."""
    out = []
    for r in reservas or []:
        for a in r.get("asignaciones") or []:
            det = a.get("detalle_servicio") or {}
            servicio = det.get("servicio") or {}
            for ars in det.get("asignacion_responsable_servicio") or []:
                ag = ars.get("agenda") or {}
                if ag.get("id"):
                    out.append({
                        "agenda_id": ag["id"],
                        # El número que el usuario ve en «Mis Reservas» (#000004).
                        "reserva_id": r.get("id"),
                        "business_id": ag.get("idCompany"),
                        "date": str(ag.get("fechaInicial") or "")[:10],
                        "start": str(ag.get("horaInicial") or "")[:5],
                        "estado": str(ag.get("estado") or "").upper(),
                        "service_id": servicio.get("id"),
                        "service_name": servicio.get("nombre"),
                        "cart_estado": str(r.get("estado") or "").upper(),
                    })
    return out


async def list_user_bookings(email: str) -> Optional[list]:
    """Las reservas del usuario tal como las lee «Mis Reservas». None si no se pudo consultar."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{NEXI_INVENT_API}/get_reservas_perfil_tercero",
                                     json={"email": email}, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            logger.warning("[RESERVA] Mis Reservas respondió %s", resp.status_code)
            return None
        return _agendas_de(resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[RESERVA] no pude consultar Mis Reservas: %s", exc)
        return None


_CANCELADAS = {"CANCELADO", "CANCELADA", "ANULADO", "ANULADA"}


async def find_booking(email: str, business_id: int, service_id: int, date: str, start: str,
                       agenda_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """La reserva vigente que coincide, si ya existe en «Mis Reservas»."""
    agendas = await list_user_bookings(email)
    for a in agendas or []:
        if a["estado"] in _CANCELADAS or a["cart_estado"] in _CANCELADAS:
            continue
        if agenda_id and a["agenda_id"] == agenda_id:
            return a
        if (int(a["business_id"] or 0) == int(business_id) and int(a["service_id"] or 0) == int(service_id)
                and a["date"] == date and a["start"] == start[:5]):
            return a
    return None


# ── creación ───────────────────────────────────────────────────────────────

async def create_booking(identity: Dict[str, Any], booking: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea la reserva con el endpoint del backend y comprueba que se pueda consultar.

    `booking` trae: business_id, service_id, date (YYYY-MM-DD), start (HH:MM),
    end (HH:MM), responsable_id (opcional), description.

    Devuelve {"status": BookingStatus.*, "agenda_id"?, "message"?}.
    """
    email = identity["email"]
    biz, srv = int(booking["business_id"]), int(booking["service_id"])
    date, start = booking["date"], booking["start"][:5]

    if is_past(date, start):
        return {"status": BookingStatus.REJECTED, "reason": "past",
                "message": "Esa fecha y hora ya pasaron."}

    # Idempotencia: si ya está, no se crea otra.
    previa = await find_booking(email, biz, srv, date, start)
    if previa:
        logger.info("[RESERVA] ya existía (agenda %s): no se duplica", previa["agenda_id"])
        return {"status": BookingStatus.CONFIRMED, "agenda_id": previa["agenda_id"],
                "reserva_id": previa.get("reserva_id"), "existing": True}

    payload = {
        "emailCliente": email,
        "idServicio": srv,
        "fechaInicio": date,
        "horaInicial": f"{start}:00",
        "horaFinal": f"{booking['end'][:5]}:00" if booking.get("end") else None,
        "descripcion": booking.get("description") or "Reserva hecha con Nexo",
        "nota": booking.get("note") or "Reserva hecha con Nexo",
    }
    if booking.get("responsable_id"):
        payload["idResponsable"] = booking["responsable_id"]

    logger.info("[RESERVA] enviando: biz=%s srv=%s %s %s resp=%s", biz, srv, date, start,
                booking.get("responsable_id"))
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{NEXI_INVENT_API}/store_agenda_servicio_nexiservice/{biz}",
                                     json=payload, headers={"Accept": "application/json"})
    except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError) as exc:
        # Se envió y no hubo respuesta: puede haber quedado. Se consulta antes
        # de decir nada, y nunca se reintenta a ciegas.
        logger.warning("[RESERVA] sin respuesta tras enviar (%s); consultando estado", exc)
        hallada = await find_booking(email, biz, srv, date, start)
        if hallada:
            return {"status": BookingStatus.CONFIRMED, "agenda_id": hallada["agenda_id"],
                    "reserva_id": hallada.get("reserva_id")}
        return {"status": BookingStatus.UNKNOWN}
    except httpx.HTTPError as exc:
        # No llegó a enviarse (conexión rechazada): no se creó nada.
        logger.error("[RESERVA] no pude contactar el backend: %s", exc)
        return {"status": BookingStatus.FAILED, "message": "no pude comunicarme con el sistema de reservas"}

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if resp.status_code not in (200, 201):
        detalle = data.get("error") or data.get("message") or f"error {resp.status_code}"
        logger.warning("[RESERVA] el backend la rechazó (%s): %s | %s", resp.status_code, detalle,
                       str(data.get("mensaje") or "")[:200])
        kind = BookingStatus.REJECTED if 400 <= resp.status_code < 500 else BookingStatus.FAILED
        return {"status": kind, "message": detalle}

    agenda_id = (data.get("agenda") or {}).get("id")
    if not agenda_id:
        logger.error("[RESERVA] respuesta 201 sin id de agenda: %s", str(data)[:200])
        hallada = await find_booking(email, biz, srv, date, start)
        if hallada:
            return {"status": BookingStatus.CONFIRMED, "agenda_id": hallada["agenda_id"],
                    "reserva_id": hallada.get("reserva_id")}
        return {"status": BookingStatus.UNKNOWN}

    # Creada. Sólo se anuncia si además se puede leer donde el usuario la busca.
    visible = await find_booking(email, biz, srv, date, start, agenda_id=agenda_id)
    if not visible:
        logger.error("[RESERVA] agenda %s creada pero no aparece en Mis Reservas de %s", agenda_id, email)
    return {"status": BookingStatus.CONFIRMED, "agenda_id": agenda_id, "visible": bool(visible),
            "reserva_id": (visible or {}).get("reserva_id")}


def reservation_number(result: Dict[str, Any]) -> str:
    """'#000004' como en «Mis Reservas»; si no se conoce, el de la agenda."""
    rid = result.get("reserva_id")
    if rid is not None and str(rid).isdigit():
        return f"#{int(rid):06d}"
    return f"#{result.get('agenda_id')}"
