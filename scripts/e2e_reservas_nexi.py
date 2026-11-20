"""
scripts/e2e_reservas_nexi.py — Reservas de punta a punta contra los servicios LOCALES.

Sin simulaciones: conversa por ChatService (modelo apagado), verifica la sesión
contra la pasarela (8001), crea con backendInvent (8002) y comprueba en
«Mis Reservas». Al final cancela lo que creó.

Requisitos: MySQL, pasarela 8001, backendInvent 8002 (arrancado con
MAIL_MAILER=log para no enviar correos) y dos usuarios de prueba registrados con
el registro real (cliente.uno / cliente.dos @lyra-prueba.local).

    NEXI_TEST_PASSWORD=... python scripts/e2e_reservas_nexi.py
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import timedelta

import httpx

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")
logging.disable(logging.CRITICAL)

from api.schemas import ChatRequest  # noqa: E402
from core.config import settings  # noqa: E402
from core.llm_engine import LLMEngine  # noqa: E402
from orchestrator.tool_registry import ToolRegistry  # noqa: E402
from services.chat_service import ChatService  # noqa: E402
from tools.nexi_booking import NEXI_AUTH_API, NEXI_INVENT_API, list_user_bookings, now_local  # noqa: E402

settings.LLM_EXTERNAL_ENABLED = False
PW = os.getenv("NEXI_TEST_PASSWORD") or ""
USERS = {"uno": "cliente.uno@lyra-prueba.local", "dos": "cliente.dos@lyra-prueba.local"}
NEGOCIO = "Consultorio Médico Vida Sana"
SERVICIO = "consulta de medicina general"
RESULTADOS = []


def ok(nombre, cond, detalle=""):
    RESULTADOS.append((nombre, bool(cond)))
    print(("  OK   " if cond else "  FALLA ") + nombre + (f" — {detalle}" if detalle else ""))


async def login(email):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{NEXI_AUTH_API}/login", json={"email": email, "password": PW},
                         headers={"Accept": "application/json"})
        r.raise_for_status()
        d = r.json()
        return "Bearer " + d["access_token"], d["user"]["persona"]["id"]


async def chat(svc, cid, msg, auth=None, uid="user_client_demo"):
    r = await svc.process_message(ChatRequest(project_id="nexiservice", user_id=str(uid), conversation_id=cid,
                                              message=msg), session_id=cid, auth_header=auth)
    return r.model_dump()


async def hasta_resumen(svc, cid, hora, auth=None, uid="user_client_demo"):
    await chat(svc, cid, f"quiero agendar una cita en {NEGOCIO}", auth, uid)
    await chat(svc, cid, SERVICIO, auth, uid)
    return await chat(svc, cid, f"pasado mañana a las {hora}", auth, uid)


async def main():
    if not PW:
        sys.exit("Falta NEXI_TEST_PASSWORD")
    svc = ChatService(None, settings, LLMEngine(model_path=""), {"nexiservice": ToolRegistry.for_project("nexiservice")})
    tok1, p1 = await login(USERS["uno"])
    tok2, p2 = await login(USERS["dos"])
    dia = (now_local() + timedelta(days=2)).strftime("%Y-%m-%d")
    hora = f"{6 + (now_local().minute % 6) * 2}"  # varía entre corridas para no chocar con la anterior
    antes = {a["agenda_id"] for a in (await list_user_bookings(USERS["uno"]) or [])}

    print("1. Sin sesión: resumen → sí → login → se crea")
    cid = str(uuid.uuid4())
    r = await hasta_resumen(svc, cid, hora)
    ok("resumen pide confirmación", "¿La agendo?" in r["reply"], r["reply"][:90])
    r = await chat(svc, cid, "sí")
    ok("sin sesión pide iniciar sesión", r.get("voice_action") == "require_auth", r["reply"][:90])
    r = await chat(svc, cid, "__AUTH_COMPLETED__", tok1, p1)
    ok("tras el login se confirma con número real", "#" in r["reply"] and "Quedó agendada" in r["reply"], r["reply"][:120])
    despues = await list_user_bookings(USERS["uno"]) or []
    nuevas = [a for a in despues if a["agenda_id"] not in antes]
    ok("aparece en Mis Reservas del usuario correcto",
       any(a["date"] == dia and a["start"] == f"{int(hora):02d}:00" for a in nuevas), str(nuevas)[:120])

    print("2. Doble confirmación")
    r = await chat(svc, cid, "sí", tok1, p1)
    otra = await list_user_bookings(USERS["uno"]) or []
    ok("un segundo sí no crea otra", len(otra) == len(despues), r["reply"][:90])

    print("3. Mismo horario, otro usuario")
    cid2 = str(uuid.uuid4())
    r = await hasta_resumen(svc, cid2, hora, tok2, p2)
    ok("el horario ocupado se detecta antes de confirmar", "tomada" in r["reply"], r["reply"][:110])

    print("4. Token inválido (sesión expirada)")
    cid3 = str(uuid.uuid4())
    await hasta_resumen(svc, cid3, str(int(hora) + 1), "Bearer token.caducado.x", p1)
    r = await chat(svc, cid3, "sí", "Bearer token.caducado.x", p1)
    ok("con token inválido no reserva: pide sesión", r.get("voice_action") == "require_auth", r["reply"][:90])

    print("5. Cancelación (endpoint del front) y lectura posterior")
    async with httpx.AsyncClient(timeout=20) as c:
        crudo = (await c.post(f"{NEXI_INVENT_API}/get_reservas_perfil_tercero", json={"email": USERS["uno"]})).json()
        carritos = {r["id"] for r in crudo if not str(r.get("id")).startswith("lyra-")
                    and str(r.get("estado")).upper() != "CANCELADO"}
        for cart in carritos:
            await c.post(f"{NEXI_INVENT_API}/cancel_agenda_servicio_nexiservice",
                         json={"idShoppingCart": cart, "email": USERS["uno"]})
    final = await list_user_bookings(USERS["uno"]) or []
    vigentes = [a for a in final if a["estado"] != "CANCELADO" and a["cart_estado"] != "CANCELADO"]
    ok("tras cancelar no quedan reservas vigentes de prueba", not vigentes, str(vigentes)[:120])

    total = sum(1 for _, v in RESULTADOS if v)
    print(f"\n{total}/{len(RESULTADOS)} verificaciones reales correctas")
    print(json.dumps({"ok": total, "total": len(RESULTADOS)}))


asyncio.run(main())
