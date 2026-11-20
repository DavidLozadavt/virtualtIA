"""
scripts/eval_school_fase7.py — Corre un conjunto de diálogos de School contra los servicios LOCALES reales.

Crea los fixtures del conjunto (marcados F7IND), corre cada diálogo por ChatService
con el token del perfil, compara el `school_outcome` de cada turno con lo esperado,
mide la latencia y las llamadas a GPT (`lyra_llm_usage`) y borra todo lo creado.

    python scripts/eval_school_fase7.py eval/schoolsena_fase7_independiente.yaml RUTA/tokens.json [--verbose]
"""

import asyncio
import json
import logging
import statistics
import subprocess
import sys
import time
import uuid

import yaml

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")
logging.disable(logging.CRITICAL)
from api.schemas import ChatRequest  # noqa: E402
from core.config import settings  # noqa: E402
from scripts.replay_turns import service  # noqa: E402

settings.LLM_EXTERNAL_ENABLED = False
CASOS = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
TOK = json.load(open(sys.argv[2]))
VERBOSE = "--verbose" in sys.argv
MY = ["C:/xampp/mysql/bin/mysql.exe", "-uroot", "virtualt_school-vt", "-N", "--default-character-set=utf8mb4", "-e"]
MARCA = "F7IND"


def sql(q):
    return subprocess.run(MY + [q], capture_output=True, text=True, encoding="utf-8").stdout.strip()


def limpiar():
    sql(f"DELETE FROM evento WHERE nombre LIKE '%{MARCA}%'; DELETE FROM formularios WHERE titulo LIKE '%{MARCA}%'")


def fixtures():
    limpiar()
    for nombre, dias, hora in (("Congreso", 20, "08:00"), ("Expo", 25, "09:00"), ("Expo", 45, "15:00"),
                               ("Cine foro", 1, "18:00")):
        sql(f"INSERT INTO evento (nombre,fechaInicial,hora,estado,idCompany,idUser,created_at,updated_at) VALUES "
            f"('{nombre} {MARCA}',DATE_ADD(CURDATE(),INTERVAL {dias} DAY),'{hora}','PENDIENTE',1,1,NOW(),NOW())")
    fid = sql(f"INSERT INTO formularios (idCompany,idUser,titulo,slug,estado,created_at,updated_at) VALUES "
              f"(1,1,'Registro congreso {MARCA}',CONCAT('reg-{MARCA}-',UNIX_TIMESTAMP()),'borrador',NOW(),NOW()); "
              f"SELECT LAST_INSERT_ID();")
    sql(f"INSERT INTO formulario_preguntas (idFormulario,tipo,titulo,esObligatoria,orden,created_at,updated_at) VALUES "
        f"({fid},'texto_corto','Nombre completo',1,1,NOW(),NOW()),({fid},'texto_corto','Documento',1,2,NOW(),NOW())")


async def main():
    svc = service()
    fixtures()
    gpt0 = sql("SELECT COUNT(*) FROM lyra_db.lyra_llm_usage WHERE llm_calls > 0") or "0"
    ok = total = 0
    lat = []
    fallos = []
    try:
        for caso in CASOS:
            cid = str(uuid.uuid4())
            for i, turno in enumerate(caso["turns"], 1):
                t0 = time.time()
                r = (await svc.process_message(
                    ChatRequest(project_id="schoolsena", user_id=f"eval7-{caso['role']}", conversation_id=cid,
                                message=turno["say"], personality="nexo"),
                    session_id=cid, auth_header="Bearer " + TOK[caso["role"]])).model_dump()
                lat.append((time.time() - t0) * 1000)
                got = r.get("school_outcome") or "none"
                exp = turno["expect"] if isinstance(turno["expect"], list) else [turno["expect"]]
                bien = got in exp
                ok += bien
                total += 1
                if not bien:
                    fallos.append(f"{caso['id']}#{i} «{turno['say']}» esperado {exp} → {got}: {r['reply'][:160]}")
                if VERBOSE or not bien:
                    print(f"[{'OK' if bien else 'FALLA'}] {caso['id']}#{i} «{turno['say']}» → {got}")
                    if VERBOSE:
                        print("      " + r["reply"][:300].replace("\n", " | "))
    finally:
        limpiar()
    gpt1 = sql("SELECT COUNT(*) FROM lyra_db.lyra_llm_usage WHERE llm_calls > 0") or "0"
    print(f"\nTurnos correctos: {ok}/{total} = {100 * ok / total:.1f} %")
    print(f"Latencia por turno (con backend real): p50 {statistics.median(lat):.0f} ms, "
          f"p95 {sorted(lat)[int(len(lat) * 0.95) - 1]:.0f} ms")
    print(f"Turnos con llamadas a GPT durante la corrida: {int(gpt1) - int(gpt0)}")
    for f in fallos:
        print("  - " + f)


asyncio.run(main())
