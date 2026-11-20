"""
scripts/e2e_school_formularios.py — Fase 7: formularios y eventos de punta a punta (servicios LOCALES).

Conversaciones de varios turnos por ChatService real contra el backend 8005. Cada
afirmación se comprueba en la BASE (no basta con que Nexo diga "listo"). Crea
registros identificables ("F7PRUEBA") y los borra al final.

    python scripts/e2e_school_formularios.py RUTA/tokens.json
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
MYSQL = ["C:/xampp/mysql/bin/mysql.exe", "-uroot", "virtualt_school-vt", "-N", "--default-character-set=utf8mb4", "-e"]
OK = []
MARCA = "F7PRUEBA"


def check(nombre, cond, detalle=""):
    OK.append(bool(cond))
    print(("  OK    " if cond else "  FALLA ") + nombre + (f" — {str(detalle)[:220]}" if detalle and not cond else ""))


def sql(q):
    return subprocess.run(MYSQL + [q], capture_output=True, text=True, encoding="utf-8").stdout.strip()


class Conv:
    def __init__(self, svc, perfil):
        self.svc, self.perfil, self.cid = svc, perfil, str(uuid.uuid4())
        self.last = None

    async def __call__(self, msg):
        r = await self.svc.process_message(
            ChatRequest(project_id="schoolsena", user_id=f"e2e7-{self.perfil}", conversation_id=self.cid,
                        message=msg, personality="nexo"),
            session_id=self.cid, auth_header="Bearer " + TOK[self.perfil])
        self.last = r.model_dump()
        return self.last


def out(r):
    return r.get("school_outcome")


def num(texto):
    m = re.search(r"número (\d+)", texto)
    return int(m.group(1)) if m else None


def preguntas_db(fid):
    filas = sql(f"SELECT p.id, p.tipo, p.titulo, p.esObligatoria, IFNULL(p.configuracion,''), "
                f"(SELECT GROUP_CONCAT(o.texto ORDER BY o.orden SEPARATOR '|') FROM formulario_opciones o "
                f"WHERE o.idFormularioPregunta=p.id) FROM formulario_preguntas p WHERE p.idFormulario={fid} ORDER BY p.orden")
    return [f.split("\t") for f in filas.splitlines()] if filas else []


async def main():
    svc = service()
    creados_ev, creados_f = [], []
    try:
        print("1. Flujo principal: evento → otra consulta → formulario armado en la conversación → guardar → vincular")
        c = Conv(svc, "admin")
        await c("quiero crear un evento")
        await c(f"se llama Feria {MARCA}")
        await c("el 15 de diciembre a las 10")
        r = await c("sí")
        ev = num(r["reply"])
        creados_ev.append(ev)
        check("evento creado y en la base", ev and sql(f"SELECT nombre FROM evento WHERE idEvento={ev}") == f"Feria {MARCA}", r["reply"])
        r = await c("¿qué regionales hay?")
        check("cambio de tema intermedio", "regional" in r["reply"].lower(), r["reply"])
        r = await c("Quiero crear un formulario de inscripción para un evento")
        check("pide el nombre", "título" in r["reply"], r["reply"])
        r = await c(f"Se llamará Registro {MARCA}")
        check("prepara el borrador sin guardarlo", out(r) == "form_prepared" and sql(
            f"SELECT COUNT(*) FROM formularios WHERE titulo='Registro {MARCA}'") == "0", r["reply"])
        r = await c("Agrégale nombre completo, documento, correo y teléfono")
        fd_ = r.get("form_draft") or {}
        check("4 campos en un mensaje, tipos del constructor", [p["titulo"] for p in fd_.get("preguntas", [])] ==
              ["Nombre completo", "Número de documento", "Correo electrónico", "Teléfono"]
              and fd_["preguntas"][3]["tipo"] == "número", fd_)
        r = await c("Que todos sean obligatorios menos el teléfono")
        req = [p["obligatoria"] for p in r["form_draft"]["preguntas"]]
        check("obligatorias menos el teléfono", req == [True, True, True, False], req)
        r = await c("Agrega una pregunta sobre el programa de formación")
        check("agrega sin perder las anteriores", len(r["form_draft"]["preguntas"]) == 5, r["reply"])
        r = await c("Mejor que sea una lista desplegable")
        check("cambia el tipo de ESA pregunta y pide opciones", "lista desplegable" in r["reply"] and "opciones" in r["reply"], r["reply"])
        r = await c("ADSI, Contabilidad y Gestión empresarial")
        check("toma las opciones", "ADSI, Contabilidad, Gestión empresarial" in r["reply"], r["reply"])
        r = await c("Quiero que la pregunta del documento aparezca primero")
        check("reordena", r["form_draft"]["preguntas"][0]["titulo"] == "Número de documento", r["reply"])
        r = await c("Muéstrame cómo quedó")
        check("muestra la estructura, marcada sin guardar", out(r) == "form_preview" and "sin guardar" in r["reply"], r["reply"])
        r = await c("Perfecto, guárdalo")
        fid = num(r["reply"])
        creados_f.append(fid)
        db = preguntas_db(fid) if fid else []
        check("guardado y verificado en la base", out(r) == "form_saved" and [x[2] for x in db] ==
              ["Número de documento", "Nombre completo", "Correo electrónico", "Teléfono", "Programa de formación"], db)
        check("tipos/obligatorias/opciones/config exactos en la base",
              db and db[3][1] == "texto_corto" and '"soloNumeros":true' in db[3][4].replace(" ", "")
              and db[3][3] == "0" and db[0][3] == "1" and db[4][1] == "desplegable"
              and db[4][5] == "ADSI|Contabilidad|Gestión empresarial", db)
        check("queda en borrador (guardar no publica)", sql(f"SELECT estado FROM formularios WHERE id={fid}") == "borrador")
        r = await c("guárdalo")
        check("guardar dos veces no duplica", sql(f"SELECT COUNT(*) FROM formularios WHERE titulo='Registro {MARCA}'") == "1"
              and "no hay cambios" in r["reply"], r["reply"])
        r = await c("Quiero vincular ese formulario con el evento que creamos más arriba")
        check("resuelve AMBOS registros y pide confirmación", out(r) == "link_confirm" and f"Registro {MARCA}" in r["reply"]
              and f"Feria {MARCA}" in r["reply"] and "no está publicado" in r["reply"], r["reply"])
        r = await c("listo")
        check("'listo' no autoriza la escritura", sql(f"SELECT IFNULL(idFormularioInterno,0) FROM evento WHERE idEvento={ev}") == "0"
              and "sí" in r["reply"], r["reply"])
        r = await c("sí")
        check("vinculado y verificado en la base", out(r) == "link_done" and
              sql(f"SELECT CONCAT(idFormularioInterno,'|',formProvider) FROM evento WHERE idEvento={ev}") == f"{fid}|interno", r["reply"])
        check("vincular no publica", sql(f"SELECT estado FROM formularios WHERE id={fid}") == "borrador")
        r = await c("¿Qué formulario tiene ese evento?")
        check("¿qué formulario tiene ese evento?", f"Registro {MARCA}" in r["reply"], r["reply"])
        r = await c("¿Está publicado?")
        check("¿está publicado? → borrador", "borrador" in r["reply"], r["reply"])
        r = await c("¿Dónde puedo verlo?")
        check("¿dónde puedo verlo? → constructor", f"/formularios/builder/{fid}" in r["reply"], r["reply"])
        r = await c("¿Cuántas personas se han inscrito?")
        check("¿cuántos inscritos? → 0 real", "0 inscritos" in r["reply"], r["reply"])
        # Lo que abre el constructor del front (GET formularios/{id}) es lo mismo.
        import httpx
        api = "http://127.0.0.1:8005/api"
        front = httpx.get(f"{api}/formularios/{fid}", headers={"Authorization": "Bearer " + TOK["admin"],
                                                               "Accept": "application/json"}).json()
        check("el constructor carga exactamente esas preguntas", [p["titulo"] for p in front["preguntas"]] ==
              [x[2] for x in db] and [o["texto"] for o in front["preguntas"][4]["opciones"]] ==
              ["ADSI", "Contabilidad", "Gestión empresarial"], front.get("preguntas"))

        print("2. Edición posterior, deshacer, publicar")
        r = await c("Elimina la última")
        check("elimina en el borrador", "Eliminé" in r["reply"] and len(preguntas_db(fid)) == 5, r["reply"])
        r = await c("Deshaz el último cambio")
        check("deshace", len(r["form_draft"]["preguntas"]) == 5, r["reply"])
        r = await c("La segunda pregunta mejor que sea opcional")
        r = await c("guárdalo")
        check("cambio persistido con los mismos ids", out(r) == "form_saved" and preguntas_db(fid)[1][3] == "0"
              and [x[0] for x in preguntas_db(fid)] == [x[0] for x in db], r["reply"])
        r = await c("publícalo")
        check("publicar pide confirmación", out(r) == "form_confirm" and
              sql(f"SELECT estado FROM formularios WHERE id={fid}") == "borrador", r["reply"])
        r = await c("mejor no publiques todavía")
        check("'mejor no' no publica", sql(f"SELECT estado FROM formularios WHERE id={fid}") == "borrador", r["reply"])
        await c("publícalo")
        r = await c("sí, publícalo")
        check("publicado tras el sí", sql(f"SELECT estado FROM formularios WHERE id={fid}") == "publicado", r["reply"])

        print("3. Cambios concurrentes: el constructor cambia el formulario mientras Nexo lo edita")
        await c("agrega una pregunta sobre observaciones")
        sql(f"UPDATE formulario_preguntas SET titulo='Nombre (editado en el constructor)' WHERE idFormulario={fid} AND orden=2")
        r = await c("guárdalo")
        check("409: no pisa el cambio ajeno y lo explica", out(r) == "form_conflict" and
              sql(f"SELECT COUNT(*) FROM formulario_preguntas WHERE idFormulario={fid}") == "5", r["reply"])
        r = await c("la primera")
        check("cargar la versión de School", "editado en el constructor" in r["reply"], r["reply"])

        print("4. Fallo del backend al guardar: el borrador se conserva y el reintento no duplica")
        c2 = Conv(svc, "admin")
        await c2("quiero crear un formulario")
        await c2(f"Encuesta {MARCA}")
        await c2("agrega nombre y correo")
        import tools.schoolsena as ts
        real = ts.SCHOOLSENA_API_BASE
        ts.SCHOOLSENA_API_BASE = "http://127.0.0.1:9"
        try:
            r = await c2("guárdalo")
            check("se informa el fallo (no 'listo')", out(r) == "form_save_failed" and "No guardé" in r["reply"], r["reply"])
        finally:
            ts.SCHOOLSENA_API_BASE = real
        r = await c2("guárdalo")
        f2 = num(r["reply"])
        creados_f.append(f2)
        check("reintento guarda una sola vez", out(r) == "form_saved" and
              sql(f"SELECT COUNT(*) FROM formularios WHERE titulo='Encuesta {MARCA}'") == "1", r["reply"])

        print("5. Duplicados y referencias antiguas")
        dup = [int(sql(f"INSERT INTO formularios (idCompany,idUser,titulo,slug,estado,created_at,updated_at) VALUES "
                       f"(1,1,'Duplicado {MARCA}',CONCAT('dup-{MARCA}-{i}-',UNIX_TIMESTAMP()),'borrador',NOW(),NOW()); "
                       f"SELECT LAST_INSERT_ID();")) for i in (1, 2)]
        creados_f += dup
        c3 = Conv(svc, "admin")
        r = await c3(f"vincula el formulario Duplicado {MARCA} con el evento Feria {MARCA}")
        check("nombre duplicado → pide aclarar con números", out(r) == "clarify" and all(str(d) in r["reply"] for d in dup), r["reply"])
        r = await c3("el segundo")
        check("elige por la lista ofrecida", out(r) == "link_confirm" and f"número {dup[1]}" in r["reply"], r["reply"])
        r = await c3("¿qué hora es?")
        r = await c3("sí")
        check("una confirmación vieja no autoriza tras cambiar de tema",
              sql(f"SELECT idFormularioInterno FROM evento WHERE idEvento={ev}") == str(fid), r["reply"])
        evd = int(sql(f"INSERT INTO evento (nombre,fechaInicial,hora,estado,idCompany,idUser,created_at,updated_at) VALUES "
                      f"('Feria {MARCA}',DATE_ADD(CURDATE(),INTERVAL 40 DAY),'09:00','PENDIENTE',1,1,NOW(),NOW()); SELECT LAST_INSERT_ID();"))
        creados_ev.append(evd)
        r = await c3(f"¿qué formulario tiene el evento Feria {MARCA}?")
        check("evento con nombre duplicado → aclara", out(r) == "clarify" and str(evd) in r["reply"], r["reply"])
        c4 = Conv(svc, "admin")
        for nombre in ("Primero", "Segundo"):
            await c4("quiero crear un formulario")
            await c4(f"{nombre} {MARCA}")
            r = await c4("sí")
            creados_f.append(num(r["reply"]))
        await c4("¿qué sedes hay?")
        r = await c4("muéstrame el primero que hicimos")
        r = await c4("muéstrame el formulario que creamos primero")
        check("'el primero que hicimos' = el más antiguo de la conversación", f"Primero {MARCA}" in r["reply"], r["reply"])

        print("6. Cambio de intención en medio de una operación")
        c5 = Conv(svc, "admin")
        await c5("quiero crear un evento")
        await c5(f"se llama Taller {MARCA}")
        await c5("mañana a las 3 de la tarde")
        r = await c5("sí")
        creados_ev.append(num(r["reply"]))
        r = await c5("Crea un formulario")
        r = await c5("Antes muéstrame el evento que creamos")
        check("muestra el evento creado (pausa el formulario)", f"Taller {MARCA}" in r["reply"] and out(r) == "event_info", r["reply"])
        r = await c5("Ahora sigamos con el formulario")
        check("retoma la creación del formulario", "título" in r["reply"], r["reply"])
        await c5(f"Asistencia {MARCA}")
        r = await c5("Agrega nombre, documento y ficha")
        r = await c5("Espera, quiero vincularlo primero")
        check("vincular un borrador sin guardar: lo explica y propone guardarlo", out(r) == "link_confirm"
              and "primero lo guardaré" in r["reply"] and f"Taller {MARCA}" in r["reply"], r["reply"])
        r = await c5("Entonces terminemos las preguntas")
        check("retoma la edición sin perder las preguntas", out(r) == "form_edit" and
              len(r["form_draft"]["preguntas"]) == 3, r["reply"])
        r = await c5("sí")
        check("el sí viejo ya no vincula ni guarda", sql(f"SELECT COUNT(*) FROM formularios WHERE titulo='Asistencia {MARCA}'") == "0", r["reply"])
        r = await c5("vincúlalo con el evento que creamos")
        r = await c5("sí")
        fa = sql(f"SELECT id FROM formularios WHERE titulo='Asistencia {MARCA}'")
        creados_f.append(int(fa) if fa else None)
        check("guarda y vincula con un solo sí", fa and sql(
            f"SELECT idFormularioInterno FROM evento WHERE idEvento={creados_ev[-1]}") == fa, r["reply"])
        r = await c5("desvincula el formulario")
        r = await c5("sí")
        check("desvincular", sql(f"SELECT IFNULL(idFormularioInterno,'NULL') FROM evento WHERE idEvento={creados_ev[-1]}") == "NULL", r["reply"])

        print("7. Generación asistida y validación")
        c6 = Conv(svc, "admin")
        r = await c6("Necesito un formulario para inscribir aprendices a una capacitación")
        pregs = [p["titulo"] for p in r["form_draft"]["preguntas"]]
        check("propone una estructura sin datos sensibles", out(r) == "form_proposal" and "Número de ficha" in pregs
              and not any(x in pregs for x in ("Fecha de nacimiento", "Dirección", "Género")), pregs)
        r = await c6("quita las preguntas personales")
        check("quita teléfono", "Teléfono" not in [p["titulo"] for p in r["form_draft"]["preguntas"]], r["reply"])
        r = await c6("Solo necesito nombre, ficha y correo")
        check("deja solo esos", [p["titulo"] for p in r["form_draft"]["preguntas"]] ==
              ["Nombre completo", "Número de ficha", "Correo electrónico"], r["reply"])
        r = await c6("agrega una pregunta de jornada como lista desplegable")
        r = await c6("guárdalo")
        check("no guarda una selección sin opciones (pide las opciones)", "opciones" in r["reply"] and
              sql("SELECT COUNT(*) FROM formularios WHERE titulo LIKE 'Inscripción a la capacitación%' AND created_at > NOW() - INTERVAL 5 MINUTE") == "0", r["reply"])

        print("8. Permisos")
        c7 = Conv(svc, "basico")
        r = await c7(f"vincula el formulario Registro {MARCA} con el evento Feria {MARCA}")
        check("sin permiso de eventos: no ofrece vincular", "🔒" in r["reply"] and out(r) != "link_confirm", r["reply"])

        print("9. Varias operaciones en UN mensaje (evento + formulario + vínculo)")
        pedido = ("Créame un evento para el próximo viernes a las 10 de la mañana, hazme un formulario de inscripción "
                  "con nombre, documento, correo y ficha, deja el teléfono opcional y vincula el formulario con el evento")
        c8 = Conv(svc, "admin")
        r = await c8(pedido)
        check("pide sólo lo que falta (el nombre del evento)", out(r) == "plan_flow" and "llamar" in r["reply"], r["reply"])
        r = await c8(f"Bienvenida {MARCA}")
        check("un solo resumen con los tres pasos", out(r) == "plan_confirm" and "1. Crear el evento" in r["reply"]
              and "Teléfono (opcional)" in r["reply"] and "3. Vincular" in r["reply"], r["reply"])
        r = await c8("mejor a las 11")
        check("corrige la hora en el resumen", "**11:00**" in r["reply"], r["reply"])
        r = await c8("ok")
        check("'ok' no ejecuta", sql(f"SELECT COUNT(*) FROM evento WHERE nombre='Bienvenida {MARCA}'") == "0", r["reply"])
        r = await c8("sí")
        evp = sql(f"SELECT CONCAT(idEvento,'|',fechaInicial,'|',hora,'|',IFNULL(idFormularioInterno,0)) FROM evento "
                  f"WHERE nombre='Bienvenida {MARCA}'").split("|")
        fp = sql(f"SELECT id FROM formularios WHERE titulo LIKE '%Bienvenida {MARCA}%'")
        db = preguntas_db(fp) if fp else []
        check("todo creado, vinculado y verificado", out(r) == "plan_done" and len(evp) == 4 and evp[3] == fp
              and evp[2] == "11:00:00", (r["reply"], evp))
        check("preguntas exactas: 4 obligatorias + teléfono opcional",
              [(x[2], x[3]) for x in db] == [("Nombre completo", "1"), ("Número de documento", "1"),
                                             ("Correo electrónico", "1"), ("Número de ficha", "1"), ("Teléfono", "0")], db)
        check("no publica", fp and sql(f"SELECT estado FROM formularios WHERE id={fp}") == "borrador")
        r = await c8("¿cuántos inscritos tiene?")
        check("después responde sobre lo creado", "0 inscritos" in r["reply"], r["reply"])

        c9 = Conv(svc, "admin")
        await c9(f"crea un evento llamado Caída {MARCA} el 10 de diciembre a las 3 de la tarde, hazle un formulario "
                 "de inscripción con nombre y correo y vincúlalos")
        import tools.schoolsena as ts2
        n_llamadas = {"n": 0}
        orig = ts2.TOOLS_REGISTRY["crear_formulario"]["handler"]

        async def falla_form(p, c):
            n_llamadas["n"] += 1
            return {"result": None, "error": "No se pudo conectar al servidor de SchoolSena", "error_kind": "unreachable"}
        ts2.TOOLS_REGISTRY["crear_formulario"]["handler"] = falla_form
        try:
            r = await c9("sí")
        finally:
            ts2.TOOLS_REGISTRY["crear_formulario"]["handler"] = orig
        check("falla a mitad: dice qué quedó hecho", out(r) == "plan_failed" and "Ya quedó hecho" in r["reply"]
              and "evento" in r["reply"], r["reply"])
        r = await c9("sí")
        check("reintento: no duplica el evento y termina", out(r) == "plan_done" and
              sql(f"SELECT COUNT(*) FROM evento WHERE nombre LIKE 'Ca_da {MARCA}' OR nombre LIKE 'Ca__da {MARCA}'") == "1", r["reply"])

        c10 = Conv(svc, "basico")
        r = await c10(pedido)
        check("sin permisos: lo dice antes y no crea nada", out(r) == "denied" and "No hice ningún cambio" in r["reply"], r["reply"])
    finally:
        ids_f = ",".join(str(x) for x in creados_f if x)
        ids_e = ",".join(str(x) for x in creados_ev if x)
        if ids_e:
            sql(f"DELETE FROM evento WHERE idEvento IN ({ids_e})")
        if ids_f:
            sql(f"DELETE FROM formularios WHERE id IN ({ids_f})")
        sql(f"DELETE FROM formularios WHERE titulo LIKE '%{MARCA}%'; DELETE FROM evento WHERE nombre LIKE '%{MARCA}%'")
    print(f"\n{sum(OK)}/{len(OK)} verificaciones reales correctas")


asyncio.run(main())
