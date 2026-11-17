"""
orchestrator/interceptors/schoolsena_plan.py — Varias operaciones de School en un solo mensaje.

"Créame un evento para el próximo viernes a las 10, hazme un formulario de inscripción
con nombre, documento, correo y ficha, deja el teléfono opcional y vincula el formulario
con el evento" es UN pedido con tres pasos que se refieren entre sí. Antes cada pieza
se leía por separado y "el formulario" se buscaba como un nombre.

El mensaje se parte en cláusulas; cada una es un paso (crear evento, crear formulario,
vincular) o un ajuste del paso anterior ("deja el teléfono opcional"). Los pasos se
refieren a lo que crea el mismo plan; lo que no se crea aquí se busca en School.
Nexo pide sólo los datos que faltan, muestra UN resumen de todo y, con un sí
inequívoco al turno siguiente, ejecuta en orden verificando cada paso. Si uno falla
se dice qué quedó hecho; reintentar no repite lo ya hecho. Nada se publica.
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any, Dict, List, Optional

from orchestrator.interceptors import schoolsena_formdraft as fd
from orchestrator.interceptors import schoolsena_ops as ops

PLAN_TTL_S = 60 * 60
plano = fd.plano

# Verbo al inicio de una cláusula (con o sin tilde: se compara contra el texto original).
_V = (r"(?:cr[eé]a(?:me|le|nos)?|crear|h[aá]z(?:me|le|nos)?|haga(?:me)?|[aá]rma(?:me|le)?|arma|m[oó]nta(?:me)?|"
      r"programa(?:me)?|organiza(?:me)?|agenda(?:me)?|prepara(?:me)?|dise[ñn]a(?:me)?|"
      r"vinc[uú]la(?:lo|la|los)?|vincular|asocia(?:lo|la)?|con[eé]cta(?:lo|la)?|enlaza(?:lo|la)?|"
      r"d[eé]ja(?:le|lo|la)?|p[oó]n(?:le|lo|la)?|que\s+(?:el|la|los|las|sea|tenga|pida))")
_SPLIT = re.compile(rf"\s*(?:,|;|\.)\s*(?:y\s+(?:luego\s+|despu[eé]s\s+|tambi[eé]n\s+)?)?(?={_V}\b)|"
                    rf"\s+y\s+(?:luego\s+|despu[eé]s\s+|tambi[eé]n\s+|adem[aá]s\s+)?(?={_V}\b)", re.I)
_CREA = r"\b(crea\w*|crear|haz\w*|haga\w*|arma\w*|monta\w*|programa\w*|organiza\w*|agenda\w*|prepara\w*|disena\w*|nuevo|nueva)\b"
_LIGA = r"\b(vincul\w*|asoci\w*|conect\w*|enlaz\w*)\b"


def _clase(c: str) -> Optional[str]:
    t = plano(c)
    if re.search(_LIGA, t):
        return "vincular"
    if re.search(r"\b(formulario|encuesta)\b", t) and re.search(_CREA, t):
        return "formulario"
    if re.search(r"\bevento\b", t) and re.search(_CREA, t):
        return "evento"
    if re.search(r"\b(opcional\w*|obligatori\w*|requerid\w*)\b", t) or re.search(r"\b(agrega\w*|incluye\w*|ponle|pidele)\b", t):
        return "ajuste"
    return None


def detectar(texto: str) -> Optional[List[tuple]]:
    """[(clase, cláusula)] si el mensaje pide al menos dos pasos distintos; si no, None."""
    partes = [p.strip(" .,;") for p in _SPLIT.split(texto) if p and p.strip(" .,;")]
    pasos = [(_clase(p), p) for p in partes]
    clases = {c for c, _ in pasos if c in ("evento", "formulario", "vincular")}
    if len(clases) < 2:
        return None
    return [(c or "ajuste", p) for c, p in pasos]


# ── Construcción del plan ───────────────────────────────────────────────────

def _preguntas_de(clausula: str) -> List[dict]:
    m = re.search(r"\b(?:con|que (?:pida|tenga|pregunte)|pidiendo|para pedir)\s+(?:los\s+campos\s+|las\s+preguntas\s+)?"
                  r"(?:de\s+)?(.+)$", clausula, re.I)
    if not m:
        return []
    items = []
    for x in fd.lista(m.group(1)):
        p = fd.pregunta(x)
        p["esObligatoria"] = True  # en un pedido así, lo que se enumera se pide (lo opcional se dice)
        items.append(p)
    return items


def _ajustar(form: dict, clausula: str) -> List[str]:
    """'deja el teléfono opcional', 'que el correo sea obligatorio', 'agrégale la jornada'."""
    t = plano(clausula)
    notas = []
    m = re.search(r"\b(?:deja\w*|pon\w*|que)\s+(?:el|la|los|las|su)?\s*(.+?)\s+(?:sea[n]?\s+|como\s+|quede[n]?\s+)?"
                  r"(opcional\w*|obligatori\w*|requerid\w*)\b", t)
    if m:
        requerida = not m.group(2).startswith("opcional")
        for nombre in fd.lista(m.group(1)):
            hits = fd.por_contenido(form, nombre)
            if hits:
                for i in hits:
                    form["preguntas"][i]["esObligatoria"] = requerida
            else:
                p = fd.pregunta(nombre)
                p["esObligatoria"] = requerida
                form["preguntas"].append(p)
        return notas
    ins = fd.interpretar(clausula, form)
    if ins and ins[0][0] not in ("guardar", "publicar", "mostrar", "no_publicar", "estado", "deshacer", "descartar"):
        nuevo, hecho, _ = fd.aplicar(form, ins)
        form.update(nuevo)
        form["hist"] = []
    return notas


async def construir(pasos: List[tuple], state: dict, caps, context) -> Dict[str, Any]:
    from orchestrator.interceptors import schoolsena_forms as forms
    plan: Dict[str, Any] = {"evento": None, "form": None, "vincular": False, "ref_evento": None, "ref_form": None,
                            "hecho": {}, "pregunta": None, "ts": time.time()}
    ultimo = None
    for clase, c in pasos:
        if clase == "evento":
            plan["evento"] = ops._extraer(ops.OPERATIONS["eventos"], c, {}, None)
            nombre = plan["evento"].get("nombre")
            if nombre and re.search(r"\b(evento|crea\w*|nuevo|nueva)\b", plano(nombre)):
                plan["evento"].pop("nombre")
            ultimo = "evento"
        elif clase == "formulario":
            prop = fd.proposito(c)
            titulo = None
            m = re.search(r"\b(?:llamado|titulado|que se llame|con el nombre|con el t[ií]tulo)\s+[\"«“]?([^\"»”,]{2,120})", c, re.I)
            if m:
                titulo = m.group(1).strip(" .")
            pregs = _preguntas_de(c)
            if not pregs and prop:
                pregs = fd.plantilla(prop[0], "el evento", prop[2])[1]
            plan["form"] = fd.nuevo(titulo, None, pregs)
            plan["form"]["tipo"] = (prop or ("inscripcion",))[0]
            ultimo = "formulario"
        elif clase == "vincular":
            plan["vincular"] = True
            t = plano(c)
            # "con el evento Feria" / "el formulario Registro X": registros que NO crea este plan.
            if "formulario" in t and not any(cl == "formulario" for cl, _ in pasos):
                plan["ref_form"] = t[t.find("formulario"):]
            if "evento" in t and not any(cl == "evento" for cl, _ in pasos):
                plan["ref_evento"] = t[t.find("evento"):]
        elif clase == "ajuste" and plan["form"] is not None and ultimo in ("formulario", "vincular"):
            _ajustar(plan["form"], c)
        elif clase == "ajuste" and plan["evento"] is not None:
            plan["evento"] = ops._extraer(ops.OPERATIONS["eventos"], c, plan["evento"], None)
    # Permisos: se dice ANTES de pedir datos qué paso no se puede hacer.
    faltan = []
    if plan["evento"] is not None and not forms._puede(caps, "crear_evento"):
        faltan.append("crear eventos")
    if plan["form"] is not None and not forms._puede(caps, "crear_formulario"):
        faltan.append("crear formularios")
    if plan["vincular"] and not forms._puede(caps, "vincular_formulario"):
        faltan.append("vincular formularios a eventos")
    if faltan:
        return forms._reply("🔒 No puedo hacer todo eso con tu usuario: no tiene permiso para " + " ni para ".join(faltan)
                            + ". No hice ningún cambio.", state, "denied", access_denied=True)
    # Registros existentes que se nombran ("vincúlalo con el evento Feria").
    for tipo, clave in (("evento", "ref_evento"), ("formulario", "ref_form")):
        if plan[clave]:
            r = await forms.resolver(tipo, plan[clave], state, context)
            if "ok" not in r:
                motivo = r.get("ninguno") or ("hay varios con ese nombre" if "ambiguo" in r else "no pude consultarlo")
                return forms._reply(f"No pude identificar el {tipo} para vincular: {motivo}. Dímelo con su nombre exacto "
                                    "o su número. No hice ningún cambio.", state, "clarify", needs_clarification=True)
            plan["hecho"][tipo] = int(r["ok"]["id"])
            plan["nombre_" + tipo] = r["ok"].get("nombre")
    st = {**{k: v for k, v in state.items() if k != "op"}, "plan": plan}
    return _siguiente(st)


# ── Avance ──────────────────────────────────────────────────────────────────

def _falta(plan: dict) -> Optional[dict]:
    if plan["evento"] is None:
        return None
    return ops._falta(ops.OPERATIONS["eventos"], plan["evento"])


def _titulo_form(plan: dict) -> str:
    f = plan["form"]
    if f.get("titulo"):
        return f["titulo"]
    destino = (plan["evento"] or {}).get("nombre") or plan.get("nombre_evento") or "el evento"
    base = {"satisfaccion": "Encuesta de satisfacción", "asistencia": "Asistencia", "evaluacion": "Evaluación"}.get(
        f.get("tipo"), "Inscripción")
    return f"{base}: {destino}"


def _resumen(plan: dict) -> str:
    from orchestrator.interceptors import schoolsena_forms as forms
    pasos, n = [], 0
    ev = plan["evento"]
    if ev is not None:
        n += 1
        pasos.append(f"{n}. Crear el evento {ops.resumen_op(ops.OPERATIONS['eventos'], ev)}.")
    if plan["form"] is not None:
        n += 1
        f = plan["form"]
        f["titulo"] = _titulo_form(plan)
        lineas = "; ".join(f"{p['titulo']} ({'obligatoria' if p.get('esObligatoria') else 'opcional'})"
                           for p in f["preguntas"]) or "sin preguntas"
        pasos.append(f"{n}. Crear el formulario **{f['titulo']}** (en borrador) con: {lineas}.")
    if plan["vincular"]:
        n += 1
        fn = f"**{plan['form']['titulo']}**" if plan["form"] is not None else f"**{plan.get('nombre_formulario')}**"
        en = (f"**{ev['nombre']}**" if ev is not None else f"**{plan.get('nombre_evento')}**")
        pasos.append(f"{n}. Vincular {fn} como formulario de inscripción de {en}. No lo publico: nadie podrá "
                     "inscribirse hasta que me pidas publicarlo.")
    aviso = (ev or {}).pop("_aviso_fecha", None)
    return ("Esto es lo que voy a hacer:\n" + "\n".join(pasos)
            + (f"\n\nOjo: {aviso}." if aviso else "") + "\n\n¿Lo hago? Puedes corregir cualquier dato antes.")


def _siguiente(st: dict) -> Dict[str, Any]:
    from orchestrator.interceptors import schoolsena_forms as forms
    plan = st["plan"]
    falta = _falta(plan)
    if falta:
        plan["pregunta"] = falta["id"]
        intro = "Va. " if not plan.get("preguntado") else ""
        plan["preguntado"] = True
        return forms._reply(intro + falta["pregunta"], {**st, "plan": plan}, "plan_flow", needs_clarification=True,
                            fe=plan["form"])
    plan["pregunta"] = None
    return forms._reply(_resumen(plan), {**st, "plan": plan}, "plan_confirm", fe=plan["form"],
                        oferta={"tipo": "accion", "accion": "plan"})


def _hora_de_tarde(texto: str, antes: dict, nuevos: dict) -> dict:
    """'mejor a las 4' para un evento de las 15:00 son las 16:00. Sin am/pm ni "de la mañana", una
    hora de 1 a 6, o la corrección de una hora de la tarde, se lee de la tarde (horario de eventos)."""
    h = nuevos.get("hora")
    if not h or h == antes.get("hora") or re.search(r"\b(am|manana|madrugada|pm|tarde|noche)\b", plano(texto)):
        return nuevos
    hh, mm = (int(x) for x in str(h)[:5].split(":"))
    previa = int(str(antes.get("hora") or "00")[:2])
    if 1 <= hh <= 11 and (hh <= 6 or previa >= 12):
        return {**nuevos, "hora": f"{hh + 12:02d}:{mm:02d}"}
    return nuevos


async def continuar(user_text: str, state: dict, caps, context, turno: int) -> Optional[Dict[str, Any]]:
    """Respuesta mientras hay un plan: dato pedido, corrección, confirmación o cancelación."""
    from orchestrator.interceptors import schoolsena_forms as forms
    plan = copy.deepcopy(state["plan"])
    if time.time() - float(plan.get("ts") or 0) > PLAN_TTL_S:
        return None
    t = plano(user_text)
    if ops.cancela(user_text) or re.fullmatch(r"(no|mejor no|nada|olvidalo|cancela todo|no hagas nada)(\s+\w+){0,2}", t):
        st = {k: v for k, v in state.items() if k != "plan"}
        hecho = plan["hecho"]
        resto = (" Ya había quedado hecho: " + ", ".join(f"{k} número {v}" for k, v in hecho.items()) + "."
                 if hecho and plan.get("ejecutado") else "")
        return forms._reply("Listo, no hago nada más." + resto, st, "plan_cancelled")
    oferta = ops.oferta_vigente(state, turno)
    if plan["pregunta"] is None and oferta and oferta.get("accion") == "plan":
        if ops.afirma_firme(user_text) or re.fullmatch(r"(si\s+)?(hazlo|dale|de una|hagale|procede|reintentalo|"
                                                        r"intentalo de nuevo)(\s+(por favor|ya))?", t):
            return await ejecutar(state, caps, context)
        if ops.respuesta_breve(user_text) == "afirma":
            return forms._reply("Para crear todo eso en School necesito un *sí* claro. ¿Lo hago?", state, "plan_confirm",
                                oferta=oferta, fe=plan["form"])
    # Datos y correcciones.
    cambio = False
    if plan["evento"] is not None:
        nuevos = ops._extraer(ops.OPERATIONS["eventos"], user_text, plan["evento"], plan["pregunta"])
        if plan["pregunta"] is None and "formulario" in t:
            nuevos = {**nuevos, "nombre": plan["evento"].get("nombre")}  # "el formulario se llama…" no es el evento
        nuevos = _hora_de_tarde(user_text, plan["evento"], nuevos)
        if {k: v for k, v in nuevos.items() if not k.startswith("_")} != \
                {k: v for k, v in plan["evento"].items() if not k.startswith("_")}:
            plan["evento"], cambio = nuevos, True
    if plan["form"] is not None and not (plan["pregunta"] and not re.search(r"\b(pregunta|campo|formulario|opcional|"
                                                                            r"obligatori)\w*", t)):
        m = re.search(r"\bformulario\b.*\b(?:se llama|se llamara|llamado|titulado|titulo)\s+(.+)$", user_text, re.I)
        if m:
            plan["form"]["titulo"], cambio = fd._cap(m.group(1).strip(" .«»\"")), True
        else:
            antes = fd._comparable(fd.foto(plan["form"]))
            _ajustar(plan["form"], user_text)
            cambio = cambio or fd._comparable(fd.foto(plan["form"])) != antes
    if cambio:
        plan["ts"] = time.time()
        return _siguiente({**state, "plan": plan})
    return None  # no trae datos del plan: puede ser otra cosa; el plan sigue guardado


async def ejecutar(state: dict, caps, context) -> Dict[str, Any]:
    from orchestrator.interceptors import schoolsena_forms as forms
    plan = copy.deepcopy(state["plan"])
    plan["ejecutado"] = True
    st = {k: v for k, v in state.items() if k not in ("plan",)}
    hechos: List[str] = []

    def fallo(paso: str, res: dict) -> Dict[str, Any]:
        st2 = {**st, "plan": plan}
        previo = (" Ya quedó hecho: " + " ".join(hechos)) if hechos else " No se hizo ningún cambio."
        return forms._reply(forms._error(res, paso) + previo + " ¿Lo reintento? (No repetiré lo que ya quedó hecho.)",
                            st2, "plan_failed", oferta={"tipo": "accion", "accion": "plan"})

    ev = plan["evento"]
    if ev is not None and not plan["hecho"].get("evento"):
        campos = {k: v for k, v in ev.items() if v and not k.startswith("_")}
        res = await forms._tool("crear_evento", campos, context)
        if res.get("error"):
            return fallo("creé el evento", res)
        obj = (res.get("result") or {}).get("evento") or {}
        eid = obj.get("idEvento") or obj.get("id")
        det = await forms._tool("get_evento", {"id": eid}, context) if eid else {"error": "sin número", "error_kind": "server"}
        if det.get("error"):
            return fallo("pude verificar el evento", det)
        plan["hecho"]["evento"] = int(eid)
        st = forms.recordar(st, "evento", eid, ev.get("nombre"), "creado")
        hechos.append(f"Creé el evento **{ev['nombre']}** (número {eid}).")
    elif ev is None and plan["hecho"].get("evento"):
        st = forms.recordar(st, "evento", plan["hecho"]["evento"], plan.get("nombre_evento"), "consultado")
    f = plan["form"]
    if f is not None and not plan["hecho"].get("formulario"):
        f["titulo"] = _titulo_form(plan)
        res = await forms._tool("crear_formulario", {"titulo": f["titulo"], "preguntas": fd.payload(f)}, context)
        if res.get("error"):
            return fallo("creé el formulario", res)
        guardado = fd.desde_estructura(res["result"])
        if fd._comparable(fd.foto(guardado)) != fd._comparable(fd.foto(f)):
            plan["hecho"]["formulario"] = guardado["id"]
            return fallo("pude verificar el formulario", {"error": "School lo guardó distinto", "error_kind": "server"})
        plan["hecho"]["formulario"] = guardado["id"]
        st = forms.recordar(forms._con_fe(st, guardado), "formulario", guardado["id"], guardado["titulo"], "creado")
        hechos.append(f"Creé el formulario **{guardado['titulo']}** (número {guardado['id']}) con "
                      f"{len(guardado['preguntas'])} preguntas, en borrador.")
    elif f is None and plan["hecho"].get("formulario"):
        st = forms.recordar(st, "formulario", plan["hecho"]["formulario"], plan.get("nombre_formulario"), "consultado")
    if plan["vincular"] and not plan["hecho"].get("vinculo"):
        eid, fid = plan["hecho"].get("evento"), plan["hecho"].get("formulario")
        res = await forms._tool("vincular_formulario", {"id": eid, "idFormulario": fid}, context)
        if res.get("error"):
            return fallo("vinculé el formulario", res)
        det = await forms._tool("get_evento", {"id": eid}, context)
        vinc = ((det.get("result") or {}).get("formulario") or {}) if not det.get("error") else {}
        if int(vinc.get("id") or 0) != int(fid):
            return fallo("pude verificar el vínculo", {"error": "al releer el evento no aparece", "error_kind": "server"})
        plan["hecho"]["vinculo"] = True
        st = forms.recordar(st, "evento", eid, (det.get("result") or {}).get("nombre"), "editado")
        hechos.append("Los vinculé: es el formulario de inscripción del evento.")
    texto = "Listo, todo quedó hecho y lo verifiqué en School: " + " ".join(hechos)
    if plan["vincular"]:
        texto += " El formulario sigue en borrador; cuando quieras que la gente se inscriba, dime «publícalo»."
    fe = st.get("fe") if (st.get("fe") or {}).get("id") == plan["hecho"].get("formulario") else None
    return forms._reply(texto, st, "plan_done", fe=fe, created_id=plan["hecho"].get("evento"))
