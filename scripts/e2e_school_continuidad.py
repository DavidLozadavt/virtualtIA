"""
scripts/e2e_school_continuidad.py — Continuidad conversacional en School (servicios LOCALES).

Los 10 casos obligatorios de la fase 6, por ChatService real contra el backend
8005 (sin simulaciones salvo el caso 10, donde se apaga el acceso a la red de
una consulta para provocar el fallo). Limpia lo que crea.

    python scripts/e2e_school_continuidad.py RUTA/tokens.json
"""

import asyncio
import json
import logging
import re
import subprocess
import sys
import uuid

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")
logging.disable(logging.CRITICAL)
from api.schemas import ChatRequest  # noqa: E402
from core.config import settings  # noqa: E402
from scripts.replay_turns import service  # noqa: E402

settings.LLM_EXTERNAL_ENABLED = False
TOK = json.load(open(sys.argv[1]))
MYSQL = ["C:/xampp/mysql/bin/mysql.exe", "-uroot", "virtualt_school-vt", "-N", "-e"]
OK = []


def check(nombre, cond, detalle=""):
    OK.append(bool(cond))
    print(("  OK    " if cond else "  FALLA ") + nombre + (f" — {str(detalle)[:160]}" if detalle else ""))


def sql(q):
    return subprocess.run(MYSQL + [q], capture_output=True, text=True).stdout.strip()


class Conv:
    def __init__(self, svc, perfil):
        self.svc, self.perfil, self.cid = svc, perfil, str(uuid.uuid4())

    async def __call__(self, msg):
        r = await self.svc.process_message(
            ChatRequest(project_id="schoolsena", user_id=f"e2e6-{self.perfil}", conversation_id=self.cid,
                        message=msg, personality="nexo"),
            session_id=self.cid, auth_header="Bearer " + TOK[self.perfil])
        return r.model_dump()


def url(r):
    return (r.get("voice_action_payload") or {}).get("url")


async def main():
    svc = service()

    print("1-3. Nexo recuerda lo que ofreció; el sí ejecuta ESO; el no lo descarta")
    c = Conv(svc, "admin")
    r = await c("muéstrame los medios de pago")
    check("ofrece abrir la pantalla", "¿Te llevo" in r["reply"], r["reply"])
    r = await c("dale")
    check("'dale' abre ESA pantalla", url(r) == "/pagos/medio-pagos", r["reply"])
    await c("muéstrame los medios de pago")
    r = await c("mejor no")
    check("'mejor no' descarta sin navegar", url(r) is None and "no la abro" in r["reply"], r["reply"])
    r = await c("sí")
    check("un sí sin propuesta vigente no ejecuta nada", url(r) is None, r["reply"])

    print("4-5. Un dato no es navegación; pausar y retomar")
    c = Conv(svc, "admin")
    await c("quiero crear un evento")
    r = await c("Eventos")
    check("'Eventos' como respuesta al nombre es el nombre", "¿Qué día" in r["reply"] and url(r) is None, r["reply"])
    r = await c("el viernes de la próxima semana a las diez de la noche")
    check("fecha y hora completas en el resumen", "a las **22:00**" in r["reply"] and "de 20" in r["reply"], r["reply"])
    r = await c("antes dime qué formularios tengo")
    check("cambio de tema: consulta sin perder el borrador", "formulario" in r["reply"], r["reply"])
    r = await c("continuemos con el evento")
    check("retoma con los datos ya dados", "**Eventos**" in r["reply"] and "¿Lo creo?" in r["reply"], r["reply"])
    r = await c("listo")
    check("'listo' no autoriza la creación", "sí" in r["reply"] and "número" not in r["reply"], r["reply"])
    r = await c("no")
    check("'no' cancela la creación", "no creo el evento" in r["reply"], r["reply"])

    print("6-7. Nuevo foco sustituye al anterior; referencia ambigua → aclaración")
    c = Conv(svc, "admin")
    r1 = await c("Planeación")
    r2 = await c("Redes")
    check("'Redes' no repite Planeación", url(r1) != url(r2) and url(r2) == "/redes", (url(r1), url(r2)))
    r = await c("pagos")
    check("'pagos' (varias pantallas con ese nombre) pide aclarar", "¿A cuál" in r["reply"], r["reply"])
    r = await c("la segunda")
    check("'la segunda' elige entre las opciones ofrecidas", url(r) is not None, r["reply"])

    print("8. Permiso revocado con una creación pendiente")
    c = Conv(svc, "plus")
    # plus no tiene acceso a eventos: se le concede uno directo, se arma el evento y se revoca antes del sí.
    act = sql("SELECT a.id FROM activation_company_users a JOIN usuario u ON u.id=a.user_id WHERE u.email='aprendiz.plus@lyra-prueba.local'")
    perm = sql("SELECT id FROM permissions WHERE name='GESTION_EVENTOS'")
    sql(f"INSERT INTO model_has_permissions (permission_id, model_type, model_id) VALUES ({perm}, 'App\\\\Models\\\\ActivationCompanyUser', {act})")
    try:
        await c("quiero crear un evento")
        await c("se llama Prueba Revocada")
        r = await c("el 15 de diciembre a las 10")
        check("con permiso: llega al resumen", "¿Lo creo?" in r["reply"], r["reply"])
        sql(f"DELETE FROM model_has_permissions WHERE model_id={act} AND permission_id={perm}")
        r = await c("sí")
        check("revocado antes del sí: el backend lo rechaza y no se crea",
              "permiso" in r["reply"].lower() and sql("SELECT COUNT(*) FROM evento WHERE nombre='Prueba Revocada'") == "0",
              r["reply"])
    finally:
        sql(f"DELETE FROM model_has_permissions WHERE model_id={act} AND permission_id={perm}")

    print("9. Una creación sólo se confirma persistida")
    c = Conv(svc, "admin")
    await c("quiero crear un formulario")
    await c("Encuesta continuidad")
    r = await c("sí")
    m = re.search(r"número (\d+)", r["reply"])
    fila = sql(f"SELECT titulo, estado FROM formularios WHERE id={m.group(1)}") if m else ""
    check("creado, con número real y leído de vuelta", m and fila.startswith("Encuesta continuidad"), r["reply"])
    sql("DELETE FROM formularios WHERE titulo='Encuesta continuidad'")

    print("10. Un fallo del backend no es 'no hay datos'")
    import tools.schoolsena as ts
    real = ts.SCHOOLSENA_API_BASE
    ts.SCHOOLSENA_API_BASE = "http://127.0.0.1:9"  # puerto cerrado
    try:
        r = await Conv(svc, "admin")("¿qué regionales hay?")
        check("se informa el fallo, no un vacío", "No pude consultar" in r["reply"] and "No hay" not in r["reply"], r["reply"])
    finally:
        ts.SCHOOLSENA_API_BASE = real

    print(f"\n{sum(OK)}/{len(OK)} verificaciones reales correctas")


asyncio.run(main())
