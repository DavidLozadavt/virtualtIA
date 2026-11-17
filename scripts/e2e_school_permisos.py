"""
scripts/e2e_school_permisos.py — Permisos efectivos de School de punta a punta (servicios LOCALES).

Conversa con Nexo (ChatService, modelo apagado) y llama al backend directamente
para comprobar que lo no autorizado lo rechaza School, no sólo la conversación.
Usa los usuarios sintéticos de `scripts/fixtures/school_local_lyra.php` y sus
tokens (login + set_company) en el JSON que se pasa como argumento.

    python scripts/e2e_school_permisos.py RUTA/tokens.json
"""

import asyncio
import json
import logging
import re
import subprocess
import sys
import uuid

import httpx

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")
logging.disable(logging.CRITICAL)
from api.schemas import ChatRequest  # noqa: E402
from core.config import settings  # noqa: E402
from scripts.replay_turns import service  # noqa: E402
from tools.schoolsena import SCHOOLSENA_API_BASE  # noqa: E402

settings.LLM_EXTERNAL_ENABLED = False
TOK = json.load(open(sys.argv[1]))
OK = []
MYSQL = ["C:/xampp/mysql/bin/mysql.exe", "-uroot", "virtualt_school-vt", "-N", "-e"]


def check(nombre, cond, detalle=""):
    OK.append(bool(cond))
    print(("  OK    " if cond else "  FALLA ") + nombre + (f" — {detalle[:150]}" if detalle else ""))


class Conv:
    def __init__(self, svc, perfil):
        self.svc, self.perfil, self.cid = svc, perfil, str(uuid.uuid4())

    async def __call__(self, msg):
        r = await self.svc.process_message(
            ChatRequest(project_id="schoolsena", user_id=f"e2e5-{self.perfil}", conversation_id=self.cid,
                        message=msg, personality="nexo"),
            session_id=self.cid, auth_header="Bearer " + TOK[self.perfil])
        return r.model_dump()


def api(perfil, verbo, ruta, **kw):
    return httpx.request(verbo, f"{SCHOOLSENA_API_BASE}/{ruta}", timeout=30,
                         headers={"Authorization": "Bearer " + TOK[perfil], "Accept": "application/json"}, **kw)


DENIED = re.compile(r"no tiene (acceso|permiso)|🔒")


async def main():
    svc = service()

    print("1. Mismo rol (APRENDIZ SENA), permisos distintos")
    r1 = await Conv(svc, "basico")("¿qué centros de formación hay?")
    r2 = await Conv(svc, "plus")("¿qué centros de formación hay?")
    check("básico: negado", DENIED.search(r1["reply"]), r1["reply"])
    check("plus: los lista", "centros de formación" in r2["reply"] and not DENIED.search(r2["reply"]), r2["reply"])
    check("backend: básico recibe 403", api("basico", "GET", "lyra/centros-formacion").status_code == 403)

    print("2. Permiso nuevo (LYRA_PRUEBA_NUEVA, sólo plus)")
    r = await Conv(svc, "plus")("llévame a la sección nueva de prueba")
    check("plus navega a la ruta nueva", (r.get("voice_action_payload") or {}).get("url") == "/lyra-prueba/nueva", r["reply"])

    print("3. Instructor con permisos administrativos directos")
    c = Conv(svc, "instradmin")
    r = await c("¿qué regionales hay registradas?")
    check("consulta regionales (permiso directo)", re.search(r"regional", r["reply"]) and not DENIED.search(r["reply"]), r["reply"])
    r = await c("resumen de nómina")
    check("nómina permitida por permiso DIRECTO (su rol no la tiene)", "nómina" in r["reply"].lower() and not DENIED.search(r["reply"]), r["reply"])
    r = await c("contratos por vencer")
    check("contratos negados (ni su rol ni sus permisos directos lo incluyen)", DENIED.search(r["reply"]), r["reply"])

    print("4. Administrador restringido (ADMIN CENTRO)")
    c = Conv(svc, "admincentro")
    r = await c("la nómina")
    check("nómina negada", DENIED.search(r["reply"]), r["reply"])
    r = await c("contratos por vencer")
    check("contratos permitidos", not DENIED.search(r["reply"]), r["reply"])
    check("backend: nómina 403", api("admincentro", "GET", "lyra/nomina/resumen").status_code == 403)

    print("5. Escritura no autorizada se rechaza en el backend")
    r = await Conv(svc, "basico")("quiero crear un evento")
    check("conversación: básico no puede crear eventos", DENIED.search(r["reply"]), r["reply"])
    p = api("basico", "POST", "lyra/eventos", json={"nombre": "x", "fechaInicial": "2030-01-01", "hora": "09:00"})
    check("backend: POST lyra/eventos de básico → 403", p.status_code == 403, str(p.status_code))

    print("6. Permisos explicados")
    r = await Conv(svc, "basico")("¿qué permisos tengo?")
    check("básico: lista corta y real", "Tienes acceso a" in r["reply"] and "Mis clases" in r["reply"], r["reply"])
    r = await Conv(svc, "basico")("¿tengo permiso para crear eventos?")
    check("básico: no puede crear eventos", r["reply"].startswith("No"), r["reply"])
    r = await Conv(svc, "admin")("¿tengo permiso para crear eventos?")
    check("admin: sí, y Nexo puede hacerlo", r["reply"].startswith("Sí") and "aquí mismo" in r["reply"], r["reply"])
    r = await Conv(svc, "basico")("muéstrame los permisos que no tengo")
    check("básico: sin catálogo no se listan los que faltan", "No puedo mostrarte" in r["reply"], r["reply"])
    r = await Conv(svc, "admin")("muéstrame los permisos que no tengo")
    check("admin (gestiona permisos): los lista", re.search(r"pantallas a las que no tienes acceso|todas las pantallas", r["reply"]), r["reply"])

    print("7. Operación inexistente y herramienta no integrada")
    r = await Conv(svc, "admin")("quiero modificar una regional")
    check("regionales no ofrece modificar", "no ofrece" in r["reply"], r["reply"])
    r = await Conv(svc, "admin")("muéstrame los medios de pago")
    check("medios de pago: acceso sí, consulta no integrada", "todavía no consulto" in r["reply"], r["reply"])

    print("8. Revocación en caliente (plus pierde GESTION_CENTROS_FORMACION)")
    c = Conv(svc, "plus")
    r = await c("¿qué centros de formación hay?")
    check("antes: permitido", not DENIED.search(r["reply"]), r["reply"])
    sql_id = ("SELECT x.permission_id FROM model_has_permissions x JOIN permissions p ON p.id=x.permission_id "
              "JOIN activation_company_users a ON a.id=x.model_id JOIN usuario u ON u.id=a.user_id "
              "WHERE u.email='aprendiz.plus@lyra-prueba.local' AND p.name='GESTION_CENTROS_FORMACION'")
    act = subprocess.run(MYSQL + ["SELECT a.id FROM activation_company_users a JOIN usuario u ON u.id=a.user_id "
                                  "WHERE u.email='aprendiz.plus@lyra-prueba.local'"], capture_output=True, text=True).stdout.strip()
    perm = subprocess.run(MYSQL + [sql_id], capture_output=True, text=True).stdout.strip()
    subprocess.run(MYSQL + [f"DELETE FROM model_has_permissions WHERE model_id={int(act)} AND permission_id={int(perm)}"])
    try:
        r = await c("¿y cuántos centros de formación hay?")
        check("después: negado sin esperar a la caché", DENIED.search(r["reply"]), r["reply"])
        check("backend: 403 tras revocar", api("plus", "GET", "lyra/centros-formacion").status_code == 403)
    finally:
        subprocess.run(MYSQL + [f"INSERT INTO model_has_permissions (permission_id, model_type, model_id) VALUES "
                                f"({int(perm)}, 'App\\\\Models\\\\ActivationCompanyUser', {int(act)})"])

    print("9. Crear un evento real (instructor con acceso a eventos)")
    c = Conv(svc, "instradmin")
    await c("quiero crear un evento")
    await c("se llama Taller Nexo de Prueba")
    r = await c("el 15 de diciembre de 2 a 4 pm, virtual")
    check("resumen antes de crear", "¿Lo creo?" in r["reply"], r["reply"])
    r = await c("sí")
    m = re.search(r"número (\d+)", r["reply"])
    check("creado con número real y verificado", bool(m), r["reply"])
    if m:
        fila = subprocess.run(MYSQL + [f"SELECT nombre, fechaInicial, hora, tipoEvento FROM evento WHERE idEvento={m.group(1)}"],
                              capture_output=True, text=True).stdout.strip()
        check("persistido con los datos pedidos", "Taller Nexo de Prueba\t2026-12-15\t14:00" in fila and "VIRTUAL" in fila, fila)
        subprocess.run(MYSQL + [f"DELETE FROM evento WHERE idEvento={m.group(1)} AND nombre='Taller Nexo de Prueba'"])

    print(f"\n{sum(OK)}/{len(OK)} verificaciones reales correctas")


asyncio.run(main())
