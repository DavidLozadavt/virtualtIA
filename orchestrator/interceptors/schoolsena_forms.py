"""
orchestrator/interceptors/schoolsena_forms.py — Formularios y eventos de School desde la conversación.

Tres piezas que no existían:

1. MEMORIA DE REGISTROS (`state["entidades"]`): cada evento o formulario que Nexo
   crea, guarda, vincula o muestra queda con su TIPO, su ID REAL y su nombre. Así
   "ese formulario", "el evento que creamos más arriba", "el primero que hicimos"
   o "el evento de mañana" se resuelven aunque en medio se haya hablado de otra
   cosa. Nunca se decide sólo por el nombre: si varios coinciden, se pregunta.

2. CONSTRUCTOR CONVERSACIONAL (`state["fe"]`): un borrador estructurado del
   formulario con el contrato del constructor del front (ver
   `schoolsena_formdraft.py`). Se distingue lo propuesto (plantilla), lo que está
   en borrador (cambios sin guardar), lo guardado en School (verificado leyéndolo
   de vuelta) y lo publicado (sólo cuando se pide y se confirma).

3. VÍNCULO EVENTO ↔ FORMULARIO: School ya lo tenía (`evento.idFormularioInterno`,
   el formulario de inscripción del evento). Nexo lo usa por `lyra/eventos/{id}/formulario`:
   resuelve ambos registros, revalida que existan y el permiso, muestra el
   resumen y sólo con un sí inequívoco al turno siguiente lo ejecuta y verifica.

Toda confirmación es una `oferta` de tipo "accion" atada al turno: una
confirmación vieja no autoriza nada después de un cambio de tema.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.interceptors import schoolsena_formdraft as fd
from orchestrator.interceptors import schoolsena_ops as ops

FE_TTL_S = 4 * 60 * 60
ENT_TTL_S = 12 * 60 * 60
plano = fd.plano


# ── Utilidades ──────────────────────────────────────────────────────────────

def _reply(text, state, outcome, oferta=None, fe=None, **extra):
    from orchestrator.interceptors.schoolsena_modules import _reply as r
    if fe is not None:
        card = fd.tarjeta(fe)
        # La respuesta trae la lista completa (voz, clientes sin tarjeta); el chat muestra la tarjeta en su lugar.
        lista = fd.presentar(fe)
        card["texto"] = re.sub(r"\n{3,}", "\n\n", text.replace(lista, "")).strip() if lista in text else text
        extra["form_draft"] = card
    return r(text, state, outcome, oferta=oferta, **extra)


async def _tool(nombre: str, params: dict, context: dict) -> dict:
    from tools.schoolsena import TOOLS_REGISTRY
    return await TOOLS_REGISTRY[nombre]["handler"](params, context)


def _puede(caps: Optional[dict], clave: str) -> bool:
    consultas = (caps or {}).get("consultas")
    return consultas is None or clave in consultas


def _error(res: dict, que: str) -> str:
    kind = res.get("error_kind") or "server"
    if kind == "forbidden":
        return f"🔒 {res['error'][0].upper() + res['error'][1:]}. No {que}."
    if kind == "not_found":
        return f"No encontré ese registro en tu institución (puede que lo hayan eliminado). No {que}."
    if kind in ("unreachable", "timeout"):
        return f"⚠️ No pude comunicarme con SchoolSena ({res['error']}). No {que}; inténtalo de nuevo en un momento."
    if kind == "validation":
        return f"School no aceptó los datos: {res['error']}. No {que}."
    return f"⚠️ SchoolSena respondió con un error ({res.get('error')}). No {que}."


def _fe(state: dict) -> Optional[dict]:
    fe = state.get("fe")
    if not isinstance(fe, dict):
        return None
    if time.time() - float(fe.get("ts") or 0) > FE_TTL_S:
        return None
    return fe


def _con_fe(state: dict, fe: Optional[dict]) -> dict:
    st = dict(state)
    if fe is None:
        st.pop("fe", None)
    else:
        st["fe"] = {**fe, "ts": time.time()}
    return st


def _pausar_op(state: dict) -> dict:
    if not state.get("op"):
        return state
    from orchestrator.interceptors.schoolsena_modules import _op_pausar
    return _op_pausar(state)


def recordar(state: dict, tipo: str, rid, nombre: str, origen: str) -> dict:
    """Anota un registro real que salió en la conversación (el más reciente queda al final)."""
    if not rid:
        return state
    ahora = time.time()
    previas = [e for e in state.get("entidades") or [] if ahora - float(e.get("ts") or 0) < ENT_TTL_S]
    vieja = next((e for e in previas if e["tipo"] == tipo and str(e["id"]) == str(rid)), None)
    ent = {"tipo": tipo, "id": int(rid), "nombre": nombre or (vieja or {}).get("nombre"), "origen": origen, "ts": ahora,
           "c": (vieja or {}).get("c") or (ahora if origen == "creado" else None)}
    ents = [e for e in previas if e is not vieja] + [ent]
    st = {**state, "entidades": ents[-20:]}
    st["foco_" + tipo] = int(rid)
    return st


def _hora(h) -> str:
    return str(h or "")[:5]


def _fecha(f) -> str:
    from core.semantic.fechas import fecha_larga
    try:
        return fecha_larga(str(f)[:10])
    except Exception:  # noqa: BLE001
        return str(f or "")


# ── Referencias ─────────────────────────────────────────────────────────────

_CUE_PRIMERO = (r"\b(primer|primero|primera)\b.*\b(hicimos|creamos|cree|hice|armamos|creaste|hiciste)\b|"
                r"\b(hicimos|creamos|cree|hice|armamos|creaste|hiciste)\b.*\b(primero|primera|de primeras|al principio)\b")
_CUE_RECIENTE = (r"\b(ese|este|esa|esta|aquel|aquella|dicho|lo|la)\b|acab\w* de|\bque (creamos|hicimos|cree|hice|armamos|"
                 r"creaste|hiciste|hemos creado|vimos|mostraste)\b|\b(nuevo|nueva|recien\w*|anterior|ultimo|ultima|"
                 r"de arriba|mas arriba|de antes)\b")
_RELLENO = {"el", "la", "los", "las", "un", "una", "de", "del", "al", "a", "con", "que", "y", "en", "para", "por", "se",
            "lo", "le", "ese", "este", "esa", "esta", "aquel", "anterior", "nuevo", "nueva", "creamos", "hicimos",
            "cree", "hice", "acabamos", "acabo", "crear", "hacer", "mas", "arriba", "antes", "recien", "ultimo",
            "ultima", "primer", "primero", "primera", "llamado", "llamada", "nombre", "titulado", "titulada",
            "dicho", "armamos", "creaste", "hiciste", "hemos", "creado", "vimos", "mostraste", "numero", "mi", "mis",
            "nuestro", "nuestra", "tiene", "tengo", "esta", "publicado", "inscrito", "inscritos", "otra", "otro",
            "vez", "manana", "hoy", "pasado", "proximo", "proxima", "semana", "lunes", "martes", "miercoles",
            "jueves", "viernes", "sabado", "domingo", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre", "porfa", "favor", "vincula", "vinculalo",
            "vinculala", "asocia", "asocialo", "conecta", "pon", "ponle", "agregaselo", "agregasela", "muestrame",
            "ensename", "dime", "formulario", "evento"}
_FECHA_RX = r"\b(manana|hoy|pasado manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo|\d{1,2} de [a-z]+|" \
            r"proxima semana|este mes)\b"


_VERBOS = {"abre", "abrir", "abrelo", "abrela", "edita", "editar", "editarlo", "editemos", "modifica", "modificar",
           "modificarlo", "agrega", "agregale", "agregar", "agregarle", "pregunta", "preguntas", "cambia", "cambiar",
           "cambiarle", "ver", "verlo", "revisar", "revisarlo", "quiero", "necesito", "puedes", "completar",
           "completarlo", "arreglar", "otra", "conecta", "conectalo", "asignale", "usa", "quitale", "quita",
           "desvincula", "consulta", "revisa", "abreme"}


def _nombre_en(frase: str) -> List[str]:
    """Palabras del NOMBRE del registro: lo que sigue a "formulario"/"evento", sin verbos ni referencias."""
    q = re.search(r"[\"«“']([^\"»”']{2,120})[\"»”']", frase)
    if q:
        return [w for w in re.findall(r"[a-z0-9ñ]+", plano(q.group(1)))]
    t = plano(frase)
    m = re.search(r"\b(?:formulario|evento|encuesta)\b(.*)$", t)
    seg = m.group(1) if m else t
    seg = re.split(r"\b(?:para (?:editar|modificar|agregar|cambiar|ver|revisar|completar)\w*|con el evento|al evento|"
                   r"del evento|y (?:luego|despues|tambien)|que tenga)\b", seg)[0]
    seg = re.sub(_FECHA_RX, " ", seg)
    return [w for w in re.findall(r"[a-z0-9ñ]+", seg)
            if w not in _RELLENO and w not in _VERBOS and len(w) > 1 and not w.isdigit()]


async def _filas(tipo: str, context: dict) -> Tuple[Optional[List[dict]], Optional[dict]]:
    res = await _tool("get_eventos" if tipo == "evento" else "get_formularios", {}, context)
    if res.get("error"):
        return None, res
    filas = res.get("result")
    return (filas if isinstance(filas, list) else []), None


async def resolver(tipo: str, frase: str, state: dict, context: dict, fe: Optional[dict] = None) -> dict:
    """
    {"ok": registro} | {"ambiguo": [registros]} | {"ninguno": motivo} | {"borrador": fe} | {"error": res}
    registro = {"id", "nombre", ...}. Orden: número explícito, "el primero que hicimos",
    nombre/fecha (contra School, cruzado con lo recordado si dice "ese/el que creamos"),
    y por último lo más reciente de la conversación.
    """
    t = plano(frase)
    ents = [e for e in state.get("entidades") or [] if e["tipo"] == tipo
            and time.time() - float(e.get("ts") or 0) < ENT_TTL_S]
    m = re.search(rf"\b{tipo}\s+(?:numero|no|id)?\s*(\d{{1,7}})\b|\b(?:numero|id)\s+(\d{{1,7}})\b", t)
    if m:
        rid = int(m.group(1) or m.group(2))
        filas, err = await _filas(tipo, context)
        if err:
            return {"error": err}
        fila = next((f for f in filas if int(f.get("id") or 0) == rid), None)
        return {"ok": fila} if fila else {"ninguno": f"no encontré el {tipo} número {rid} en tu institución"}
    if re.search(_CUE_PRIMERO, t):
        creados = sorted([e for e in ents if e.get("c")], key=lambda e: e["c"])
        if creados:
            return {"ok": {"id": creados[0]["id"], "nombre": creados[0]["nombre"]}}
    nombre = _nombre_en(frase)
    fecha = None
    if tipo == "evento" and re.search(_FECHA_RX, t):
        from core.semantic.fechas import leer_fecha
        fecha = leer_fecha(frase)[0]
    reciente = re.search(_CUE_RECIENTE, t)
    if nombre or fecha:
        filas, err = await _filas(tipo, context)
        if err:
            return {"error": err}

        def puntaje(f) -> float:
            if fecha and str(f.get("fechaInicial") or "")[:10] != fecha:
                return 0.0
            if not nombre:
                return 1.0
            n = plano(str(f.get("nombre") or ""))
            return sum(1 for w in nombre if re.search(rf"\b{re.escape(w[:6])}", n)) / len(nombre)
        pts = [(puntaje(f), f) for f in filas]
        mejor = max((p for p, _ in pts), default=0.0)
        # Todas las palabras, o casi (una de más dicha al pasar); nunca una coincidencia parcial débil.
        cands = [f for p, f in pts if p == mejor] if mejor >= (1.0 if len(nombre) <= 2 else 0.75) else []
        if reciente and ents and len(cands) > 1:
            ids = {e["id"] for e in ents}
            propios = [f for f in cands if int(f["id"]) in ids]
            cands = propios or cands
        if len(cands) == 1:
            return {"ok": cands[0]}
        if len(cands) > 1:
            return {"ambiguo": cands[:6]}
        que = (" «" + " ".join(nombre) + "»" if nombre else "") + (f" para el {_fecha(fecha)}" if fecha else "")
        return {"ninguno": f"no encontré un {tipo}{que} en tu institución"}
    if tipo == "formulario" and fe:
        return {"ok": {"id": fe["id"], "nombre": fe.get("titulo")}} if fe.get("id") else {"borrador": fe}
    foco = state.get("foco_" + tipo)
    if foco:
        e = next((x for x in reversed(ents) if x["id"] == foco), None)
        return {"ok": {"id": foco, "nombre": (e or {}).get("nombre")}}
    if ents:
        return {"ok": {"id": ents[-1]["id"], "nombre": ents[-1]["nombre"]}}
    return {"ninguno": f"no sé a qué {tipo} te refieres"}


def _describir(tipo: str, f: dict) -> str:
    if tipo == "evento":
        return f"**{f.get('nombre')}** — {_fecha(f.get('fechaInicial'))}" + (
            f" a las {_hora(f.get('hora'))}" if f.get("hora") else "") + f" (número {f.get('id')})"
    return f"**{f.get('nombre')}** — {f.get('estado') or ''} (número {f.get('id')})"


def _aclarar(tipo: str, cands: List[dict], plantilla_cmd: str, state: dict) -> dict:
    lineas = [f"{i}. {_describir(tipo, c)}" for i, c in enumerate(cands, 1)]
    return _reply(f"Encontré {len(cands)} {tipo}s que coinciden. ¿Cuál?\n" + "\n".join(lineas), state, "clarify",
                  needs_clarification=True,
                  oferta={"tipo": "elegir", "opciones": [{"texto": plantilla_cmd.format(id=c["id"])} for c in cands]})


# ── Detección ───────────────────────────────────────────────────────────────

def _es_desvincular(t: str) -> bool:
    return bool(re.search(r"\b(desvincul|desasoci|desconect|desenlaz)\w*", t)
                or (re.search(r"\b(quita|quitale|saca|elimina|borra|remueve)\w*\b", t)
                    and re.search(r"\bformulario\b", t) and re.search(r"\bevento\b", t)
                    and not re.search(r"\bpregunta", t)))


def _es_vincular(t: str, fe, state: Optional[dict] = None) -> bool:
    if re.search(r"\bpregunta", t) or _es_desvincular(t):
        return False
    if (state or {}).get("foco_evento") and re.search(r"\b(ponle|agregale|asignale|anadele|usale|pongale)\b.*\bformulario\b", t):
        return True  # "ponle el formulario X" justo después de hablar de un evento
    if re.search(r"\b(vincul|asoci|conect|enlaz|relacion|lig)\w*", t):
        return bool(re.search(r"\b(formulario|evento)\b", t) or fe or re.search(r"\b\w+(lo|la)\b", t))
    if re.search(r"\bevento\b", t) and re.search(r"\b(agrega|pon|anade|mete|asigna|usa|ponle|agregale)\w*", t):
        return bool(re.search(r"\bformulario\b|\b\w+(selo|sela)\b", t))
    return False


# ── Entrada ─────────────────────────────────────────────────────────────────

async def handle(user_text: str, state: dict, caps: Optional[dict], context: dict, turno: int) -> Optional[Dict[str, Any]]:
    t = plano(user_text)
    if not t:
        return None
    fe = _fe(state)
    oferta = ops.oferta_vigente(state, turno)

    # 0. Varias operaciones en un mensaje ("crea el evento…, hazme el formulario… y vincúlalos").
    from orchestrator.interceptors import schoolsena_plan as plan_mod
    pasos = plan_mod.detectar(user_text)
    if pasos:
        return await plan_mod.construir(pasos, _pausar_op(state), caps, context)
    if state.get("plan"):
        hecho = await plan_mod.continuar(user_text, state, caps, context, turno)
        if hecho:
            return hecho
        if re.search(r"\b(continu|sigamos|seguimos|retom)\w*", t) and not state.get("op"):
            return plan_mod._siguiente(state)

    # A. Respuesta a una acción propuesta en el turno anterior.
    if oferta and oferta.get("tipo") == "accion":
        hecho = await _responder_accion(user_text, oferta, state, caps, context, fe)
        if hecho:
            return hecho
    if oferta and oferta.get("tipo") == "pedir_evento":
        hecho = await _vincular(user_text, state, caps, context, fe, evento_frase=user_text,
                                form_id=oferta.get("formulario"))
        if hecho and hecho["final_data"].get("school_outcome") != "link_missing":
            return hecho

    # A2. "No, déjalo así" sin nada propuesto: se reconoce, no es "fuera de alcance".
    if not oferta and not state.get("op") and re.fullmatch(
            r"(no|mejor no|no gracias)?\s*(dejalo asi|asi esta bien|asi dejalo|asi queda|no gracias|asi)", t):
        return _reply("Listo, lo dejo así.", state, "action_declined", fe=fe)

    # B. Un dato que el borrador está esperando (título, opciones, preguntas…).
    if fe and fe.get("espera"):
        hecho = await _dato_esperado(user_text, fe, state, caps, context)
        if hecho:
            return hecho

    # C. Vincular / desvincular formulario y evento.
    if _es_desvincular(t):
        return await _desvincular(user_text, _pausar_op(state), caps, context, fe)
    if _es_vincular(t, fe, state):
        return await _vincular(user_text, _pausar_op(state), caps, context, fe)

    # D. Preguntas sobre un registro o sobre la relación.
    hecho = await _consultas(user_text, t, state, caps, context, fe)
    if hecho:
        return hecho

    # E. Retomar el formulario que se estaba armando.
    borradores = state.get("borradores") or {}
    if fe and re.search(r"\b(continu|sigamos|seguimos|retom|volvamos|terminemos|terminar|sigue)\w*", t) \
            and re.search(r"\b(formulario|preguntas|borrador)\b", t) and "formularios" not in borradores:
        st = _con_fe(_pausar_op(state), fe)
        return _reply(f"Sigamos con el formulario. {fd.presentar(fe)}\n\n¿Qué más le cambio o le agrego?", st,
                      "form_edit", fe=fe)

    # E2. "Necesito un formulario para inscribir aprendices a una capacitación": propuesta de estructura.
    if re.search(r"\b(formulario|encuesta)\b", t) and quiere_generar(user_text) \
            and re.search(r"\b(necesito|quiero|crea|crear|hazme|armame|disename|proponme|sugiereme|ayudame)\b", t) \
            and not (fe and fd.sucio(fe) and fe.get("preguntas")):
        if not _puede(caps, "crear_formulario"):
            return _reply("🔒 Tu usuario no tiene permiso para crear formularios en School.", state, "denied",
                          access_denied=True)
        return generar(user_text, _pausar_op(state))

    # F. Instrucciones sobre el formulario.
    op = state.get("op")
    if op and op.get("recurso") == "formularios":
        ins = fd.interpretar(user_text, fe or fd.nuevo())
        # El título lo sigue tomando la operación ("ponle de nombre X" mientras pregunta el título).
        if ins and ins[0][0] not in ("publicar", "no_publicar", "estado", "deshacer", "descartar", "titulo"):
            # "Agrégale nombre, documento…" mientras Nexo pedía el título o confirmaba:
            # el formulario se arma como borrador (sin guardar) con lo que ya se dijo.
            nuevo = fd.nuevo(op["campos"].get("titulo"), op["campos"].get("descripcion"))
            st = {k: v for k, v in state.items() if k != "op"}
            return await _editar(ins, nuevo, st, caps, context)
        return None
    if op and op.get("pregunta") and not re.search(r"\b(pregunta|preguntas|formulario)\b", t):
        return None  # otra operación espera un dato: no se lo quitamos
    if fe and fe.get("conflicto"):
        actual = fe["conflicto"]
        if re.search(r"\b(carga|cargar|cargala|trae|usa)\b.*\b(version|school)\b", t):
            nuevo = fd.desde_estructura(actual)
            return _reply("Cargué la versión de School (se descartaron los cambios de la conversación).\n\n"
                          + fd.presentar(nuevo), _con_fe(state, nuevo), "form_edit", fe=nuevo)
        if re.search(r"\b(sobrescrib|sobreescrib|reemplaz|pisa)\w*", t):
            nuevo = {k: v for k, v in fe.items() if k != "conflicto"}
            nuevo["version"] = actual.get("version")
            return await _guardar(nuevo, state, caps, context, forzar=True)
    if fe:
        ins = fd.interpretar(user_text, fe)
        if ins:
            return await _editar(ins, fe, state, caps, context)
        return None
    # Sin borrador abierto: "agrégale una pregunta al formulario X", "editemos el formulario de inscripción".
    if re.search(r"\bformulario\b", t) and (re.search(r"\b(edit|modific|cambi|arregl|complet)\w*", t)
                                           or re.search(r"\bpreguntas?\b", t)) \
            and not re.search(r"\bformularios\b|\bcuant|\bcuales\b", t):
        return await _abrir(user_text, state, caps, context)
    if re.search(r"^(?:y\s+)?(agrega|agregale|anade|anadele|incluye|ponle|quita|elimina)\w*\b.*\bpreguntas?\b", t) \
            and state.get("foco_formulario"):
        return await _abrir(user_text, state, caps, context, por_foco=True)
    return None


# ── Preparar / generar (desde la operación de crear) ───────────────────────

_GENERA = re.compile(r"\bpara (inscribir|registrar|evaluar|medir|tomar|conocer|saber|recoger|encuestar|calificar|"
                     r"preguntar)\b|\bencuesta\b|\b(proponme|propon|sugiereme|sugiere|disename|disena|armame|hazme)\b")


def quiere_generar(texto: str) -> bool:
    return bool(_GENERA.search(plano(texto))) and fd.proposito(texto) is not None


def generar(texto: str, state: dict, titulo: Optional[str] = None) -> dict:
    tipo, objeto, aprendices = fd.proposito(texto)
    t0, pregs = fd.plantilla(tipo, objeto, aprendices)
    fe = fd.nuevo(titulo or t0, None, pregs)
    fe["proposito"] = [tipo, objeto, aprendices]
    st = _con_fe({k: v for k, v in state.items() if k != "op"}, fe)
    return _reply("Te propongo este formulario. Es solo una propuesta: no he guardado nada.\n\n" + fd.presentar(fe)
                  + "\n\nCámbiala como quieras («hazlo más corto», «quita las preguntas personales», «solo necesito "
                  "nombre, ficha y correo», «agrega…») y cuando esté lista dime «guárdalo».", st, "form_proposal", fe=fe)


def preparar(state: dict, campos: dict) -> dict:
    """El título ya está: se prepara el borrador (sin guardar) en vez de crear un formulario vacío."""
    fe = fd.nuevo(campos.get("titulo"), campos.get("descripcion"))
    st = _con_fe({k: v for k, v in state.items() if k != "op"}, fe)
    st["borradores"] = {k: v for k, v in (state.get("borradores") or {}).items() if k != "formularios"}
    return _reply(f"Preparé el borrador **{fe['titulo']}** (todavía no está guardado en School). Dime qué preguntas lleva "
                  "—puedes darme varias a la vez— o pídeme que te proponga una estructura. Si lo quieres crear vacío "
                  "por ahora, respóndeme *sí*.", st, "form_prepared", fe=fe,
                  oferta={"tipo": "accion", "accion": "guardar"})


# ── Edición ─────────────────────────────────────────────────────────────────

async def _editar(ins: List[tuple], fe: dict, state: dict, caps, context) -> dict:
    kind = ins[0][0]
    if kind == "mostrar":
        return await _mostrar(fe, state, context)
    if kind == "guardar":
        return await _guardar(fe, state, caps, context)
    if kind == "publicar":
        return await _proponer_publicar(fe, state, caps, context)
    if kind == "no_publicar":
        if fe.get("estado") == "publicado":
            return _reply(f"**{fe['titulo']}** ya está publicado. ¿Quieres que lo vuelva a borrador?",
                          _con_fe(state, fe), "form_edit", fe=fe,
                          oferta={"tipo": "accion", "accion": "estado", "estado": "borrador", "formulario": fe.get("id")})
        return _reply("Listo, no lo publico: sigue " + ("sin guardar" if fe.get("id") is None else "en borrador")
                      + ". Cuando quieras, dime «publícalo».", _con_fe(state, fe), "form_edit", fe=fe)
    if kind == "estado":
        if fe.get("id") is None:
            return _reply("Ese formulario todavía no está guardado en School.", _con_fe(state, fe), "form_edit", fe=fe)
        verbo = {"borrador": "devolver a borrador", "cerrado": "cerrar"}[ins[0][1]]
        return _reply(f"¿Quieres {verbo} **{fe['titulo']}**? Respóndeme *sí* o *no*.", _con_fe(state, fe), "form_confirm",
                      fe=fe, oferta={"tipo": "accion", "accion": "estado", "estado": ins[0][1], "formulario": fe["id"]})
    clave = "crear_formulario" if fe.get("id") is None else "editar_formulario"
    if not _puede(caps, clave):
        return _reply("🔒 Tu usuario no tiene permiso para modificar formularios en School.", state, "denied",
                      access_denied=True)
    nuevo, hecho, espera = fd.aplicar(fe, ins)
    nuevo["espera"] = espera
    st = _con_fe(state, nuevo)
    if nuevo.get("id"):
        st = recordar(st, "formulario", nuevo["id"], nuevo.get("titulo"), "editado")
    texto = " ".join(hecho)
    if espera:
        texto = (texto + " " if texto else "") + _pregunta_espera(nuevo, espera)
        return _reply(texto, st, "form_edit", fe=nuevo, needs_clarification=True)
    if not hecho:
        return _reply("No cambié nada.", st, "form_edit", fe=nuevo)
    pie = "Cambios en el borrador, todavía sin guardar en School." if fd.sucio(nuevo) else ""
    return _reply(f"{texto}\n\n{fd.presentar(nuevo)}" + (f"\n\n{pie}" if pie else ""), st, "form_edit", fe=nuevo)


def _pregunta_espera(fe: dict, espera: dict) -> str:
    c = espera["campo"]
    if c == "opciones":
        return f"¿Qué opciones tendrá **{fe['preguntas'][espera['indice']]['titulo']}**? Dímelas separadas por comas."
    if c == "titulo":
        return "¿Cuál será el título del formulario?"
    if c == "descripcion":
        return "¿Qué instrucciones quieres que lleve el formulario?"
    n = espera.get("cantidad") or 1
    return ("¿Cuál pregunta agrego?" if n == 1 else f"¿Cuáles son las {n} preguntas? Dímelas separadas por comas.")


async def _dato_esperado(user_text, fe, state, caps, context):
    espera = fe["espera"]
    ins = fd.interpretar(user_text, fe)
    if ins and ins[0][0] != "agregar":
        return None  # es otra instrucción: la atiende el flujo normal (y la espera se mantiene)
    t = plano(user_text)
    if re.search(r"\b(evento|eventos|regional|sedes|permisos|horario|notas)\b", t) and espera["campo"] != "titulo":
        return None
    fe = {**fe, "espera": None}
    if espera["campo"] == "titulo":
        valor = re.sub(r"^(?:se llamara|se va a llamar|que se llame|ponle|el titulo (?:es|sera)|titulo:?)\s+", "",
                       user_text.strip(" .!¡?¿"), flags=re.I)
        nuevo, hecho, _ = fd.aplicar(fe, [("titulo", valor)])
        if espera.get("guardar"):
            return await _guardar(nuevo, state, caps, context)
        return _reply(" ".join(hecho), _con_fe(state, nuevo), "form_edit", fe=nuevo)
    if espera["campo"] == "descripcion":
        nuevo, hecho, _ = fd.aplicar(fe, [("descripcion", user_text.strip())])
        return _reply(" ".join(hecho), _con_fe(state, nuevo), "form_edit", fe=nuevo)
    if espera["campo"] == "opciones":
        opciones = fd.lista(re.sub(r"^(?:las opciones (?:son|seran)|opciones:?)\s*", "", user_text, flags=re.I))
        if not opciones:
            return None
        return await _editar([("opciones", f"la pregunta {espera['indice'] + 1}", opciones)],
                             fe, state, caps, context)
    if espera["campo"] == "agregar":
        if ins and ins[0][0] == "agregar":
            items = ins[0][1]
        else:
            items = [fd.pregunta(x) for x in fd.lista(user_text)] if "," in user_text or " y " in user_text \
                else [fd.pregunta(user_text)]
        return await _editar([("agregar", items, espera.get("pos"))], fe, state, caps, context)
    return None


async def _abrir(user_text, state, caps, context, por_foco=False):
    """Abre en el borrador un formulario que ya existe en School y aplica lo que se pidió."""
    r = await resolver("formulario", user_text if not por_foco else "ese formulario", state, context)
    if "error" in r:
        return _reply(_error(r["error"], "pude abrir el formulario"), state, "error")
    if "ambiguo" in r:
        return _aclarar("formulario", r["ambiguo"], "edita el formulario numero {id}", state)
    if "ok" not in r:
        if _nombre_en(user_text):  # nombró un formulario que no existe: se dice, no se navega a ciegas
            return _reply(f"No pude abrirlo: {r.get('ninguno')}. ¿Cómo se llama exactamente?", state, "clarify",
                          needs_clarification=True)
        return None
    res = await _tool("get_formulario", {"id": r["ok"]["id"]}, context)
    if res.get("error"):
        return _reply(_error(res, "pude abrir el formulario"), state, "error")
    fe = fd.desde_estructura(res["result"])
    st = recordar(_con_fe(_pausar_op(state), fe), "formulario", fe["id"], fe["titulo"], "consultado")
    ins = fd.interpretar(user_text, fe)
    if ins and ins[0][0] not in ("mostrar",):
        return await _editar(ins, fe, st, caps, context)
    return _reply(f"Abrí el formulario. {fd.presentar(fe)}\n\n¿Qué le cambio?", st, "form_edit", fe=fe)


async def _mostrar(fe, state, context):
    """Muestra el borrador; si no hay cambios pendientes, lo relee de School (el constructor pudo cambiarlo)."""
    nota = ""
    if fe.get("id") and not fd.sucio(fe):
        res = await _tool("get_formulario", {"id": fe["id"]}, context)
        if res.get("error"):
            nota = "\n\n" + _error(res, "pude releerlo de School; te muestro lo último que tengo")
        elif res["result"].get("version") != fe.get("version"):
            fe = fd.desde_estructura(res["result"])
            nota = "\n\n(Lo releí de School: cambió desde la última vez, por ejemplo en el constructor.)"
        else:
            fe = {**fd.desde_estructura(res["result"]), "foco": fe.get("foco"), "hist": fe.get("hist") or []}
    pie = ""
    if fe.get("id") is None:
        pie = "\n\nTodavía no está guardado en School: dime «guárdalo» cuando quieras."
    elif fd.sucio(fe):
        pie = "\n\nTiene cambios sin guardar; dime «guárdalo» para guardarlos en School."
    return _reply(fd.presentar(fe) + pie + nota, _con_fe(state, fe), "form_preview", fe=fe)


# ── Guardar / publicar ──────────────────────────────────────────────────────

async def _guardar(fe, state, caps, context, forzar=False) -> dict:
    nuevo_form = fe.get("id") is None
    if not _puede(caps, "crear_formulario" if nuevo_form else "editar_formulario"):
        return _reply("🔒 Tu usuario no tiene permiso para guardar formularios en School.", state, "denied",
                      access_denied=True)
    if not fe.get("titulo"):
        fe = {**fe, "espera": {"campo": "titulo", "guardar": True}}
        return _reply("Antes de guardarlo necesito el título. ¿Cómo se va a llamar?", _con_fe(state, fe), "form_edit", fe=fe)
    k = fd.sin_opciones(fe)
    if k is not None:
        fe = {**fe, "espera": {"campo": "opciones", "indice": k}}
        return _reply(f"No puedo guardarlo todavía: **{fe['preguntas'][k]['titulo']}** es de selección y no tiene "
                      "opciones. ¿Cuáles son?", _con_fe(state, fe), "form_edit", fe=fe, needs_clarification=True)
    if not nuevo_form and not fd.sucio(fe) and not forzar:
        return _reply(f"**{fe['titulo']}** ya está guardado en School; no hay cambios nuevos.", _con_fe(state, fe),
                      "form_saved", fe=fe)
    if not nuevo_form and fe.get("estado") == "publicado" and (fe.get("respuestas") or 0) > 0 and not forzar:
        return _reply(f"**{fe['titulo']}** está publicado y ya tiene {fe['respuestas']} respuestas: los cambios se verán "
                      "de inmediato y, si quitaste preguntas, esas respuestas perderán su enunciado. ¿Guardo?",
                      _con_fe(state, fe), "form_confirm", fe=fe, oferta={"tipo": "accion", "accion": "guardar"})
    datos = {"titulo": fe["titulo"], "descripcion": fe.get("descripcion"), "preguntas": fd.payload(fe)}
    if nuevo_form:
        res = await _tool("crear_formulario", datos, context)
        if res.get("error_kind") in ("timeout", "unreachable"):
            # ¿Llegó a crearse? Se busca antes de dejar que el usuario reintente (no se duplica).
            filas, _ = await _filas("formulario", context)
            previo = next((f for f in filas or [] if plano(f.get("nombre") or "") == plano(fe["titulo"])
                           and int(f.get("preguntas") or 0) == len(fe["preguntas"])
                           and not any(e["id"] == f["id"] for e in state.get("entidades") or [])), None)
            if previo:
                res = await _tool("get_formulario", {"id": previo["id"]}, context)
    else:
        res = await _tool("guardar_formulario", {"id": fe["id"], "version": fe.get("version"), **datos}, context)
    if res.get("error_kind") == "conflict" and res.get("result"):
        actual = res["result"]
        st = _con_fe(state, {**fe, "conflicto": actual})
        return _reply(f"Alguien cambió **{actual.get('titulo')}** en School mientras lo editábamos (por ejemplo, desde el "
                      "constructor). No guardé nada para no pisar esos cambios. ¿Qué hago: cargo la versión de School "
                      "(se pierden los cambios de esta conversación) o la sobrescribo con la nuestra?", st, "form_conflict",
                      fe=fe, oferta={"tipo": "elegir", "opciones": [
                          {"texto": "carga la versión de school del formulario"},
                          {"texto": "sobrescribe el formulario con nuestra versión"}]})
    if res.get("error"):
        return _reply(_error(res, "guardé el formulario") + " El borrador sigue aquí; puedes reintentarlo.",
                      _con_fe(state, fe), "form_save_failed", fe=fe)
    guardado = res["result"]
    nuevo = fd.desde_estructura(guardado)
    # Verificación: lo que School tiene ahora es lo que se armó en la conversación.
    igual = fd._comparable(fd.foto(nuevo)) == fd._comparable(fd.foto(fe))
    nuevo["foco"] = fe.get("foco")
    st = recordar(_con_fe(state, nuevo), "formulario", nuevo["id"], nuevo["titulo"],
                  "creado" if nuevo_form else "editado")
    n = len(nuevo["preguntas"])
    texto = (f"Listo: {'creé' if nuevo_form else 'guardé'} **{nuevo['titulo']}** en School (número {nuevo['id']}), con "
             f"{n} pregunta{'s' if n != 1 else ''}, en {nuevo['estado']}. Lo verifiqué leyéndolo de vuelta"
             + ("." if igual else ", pero School lo guardó distinto a lo que armamos; revísalo abajo.")
             + " Se puede abrir y editar en el constructor (Formularios).")
    if nuevo_form and not n:
        texto += " Cuando quieras, dime las preguntas que lleva."
    return _reply(texto + "\n\n" + fd.presentar(nuevo), st, "form_saved", fe=nuevo, created_id=nuevo["id"])


async def _proponer_publicar(fe, state, caps, context):
    if not _puede(caps, "publicar_formulario"):
        return _reply("🔒 Tu usuario no tiene permiso para publicar formularios.", state, "denied", access_denied=True)
    if fe.get("estado") == "publicado" and not fd.sucio(fe):
        return _reply(f"**{fe['titulo']}** ya está publicado.", _con_fe(state, fe), "form_edit", fe=fe)
    if not fe["preguntas"]:
        return _reply("No se puede publicar un formulario sin preguntas. ¿Qué preguntas le agrego?", _con_fe(state, fe),
                      "form_edit", fe=fe)
    antes = " Primero lo guardaré con los cambios de esta conversación." if fd.sucio(fe) else ""
    return _reply(f"¿Publico **{fe['titulo']}**?{antes} Quedará abierto para que la gente lo responda. Respóndeme *sí* "
                  "o *no*.", _con_fe(state, fe), "form_confirm", fe=fe,
                  oferta={"tipo": "accion", "accion": "publicar", "formulario": fe.get("id")})


async def _cambiar_estado(fe, estado, state, caps, context):
    if fd.sucio(fe) or fe.get("id") is None:
        r = await _guardar(fe, state, caps, context, forzar=True)
        if r["final_data"].get("school_outcome") != "form_saved":
            return r
        state = r["final_data"]["_school"]
        fe = state["fe"]
    res = await _tool("estado_formulario", {"id": fe["id"], "estado": estado}, context)
    if res.get("error"):
        return _reply(_error(res, "cambié el estado del formulario"), _con_fe(state, fe), "form_save_failed", fe=fe)
    nuevo = {**fd.desde_estructura(res["result"]), "foco": fe.get("foco")}
    ok = nuevo["estado"] == estado
    st = recordar(_con_fe(state, nuevo), "formulario", nuevo["id"], nuevo["titulo"], "editado")
    if estado == "publicado":
        texto = (f"Listo: **{nuevo['titulo']}** quedó publicado (lo verifiqué en School). Se responde en "
                 f"/formulario-publico/{nuevo['slug']}" + (" y es el formulario de inscripción de "
                 + ", ".join(f"**{e['nombre']}**" for e in nuevo.get("eventos") or []) if nuevo.get("eventos") else "")
                 + ".") if ok else "School no confirmó la publicación; revísalo en Formularios."
    else:
        texto = f"Listo: **{nuevo['titulo']}** quedó en {nuevo['estado']}." if ok else "School no confirmó el cambio."
    return _reply(texto, st, "form_published" if estado == "publicado" else "form_state", fe=nuevo)


# ── Acciones confirmadas ────────────────────────────────────────────────────

async def _responder_accion(user_text, oferta, state, caps, context, fe):
    t = plano(user_text)
    breve = ops.respuesta_breve(user_text)
    accion = oferta.get("accion")
    niega = (breve == "niega" or ops.cancela(user_text)
             or re.match(r"(no|mejor no|nel|nah|para nada)\b", t) and len(t.split()) <= 6
             or re.search(r"\b(dejalo asi|asi esta bien|no lo hagas|no hagas nada|no lo (vincules|publiques|guardes))\b", t))
    if niega:
        textos = {"vincular": "Listo, no lo vinculo.", "desvincular": "Listo, lo dejo vinculado.",
                  "publicar": "Listo, no lo publico; sigue en borrador.", "estado": "Listo, no cambio nada.",
                  "guardar": "Listo, no lo guardo todavía." + (" El borrador sigue aquí." if fe else "")}
        return _reply(textos.get(accion, "Listo."), state, "action_declined", fe=fe)
    # Un sí claro, o el sí que nombra la acción propuesta ("sí, publícalo", "vincúlalo").
    verbo = {"publicar": r"publica\w*", "vincular": r"vincula\w*", "desvincular": r"desvincula\w*|quitalo",
             "guardar": r"guarda\w*|crealo", "estado": r"cambialo"}.get(accion, r"hazlo")
    firme = ops.afirma_firme(user_text) or re.fullmatch(
        rf"(?:(?:si|dale|claro que si|de una)\s*)?(?:por favor\s+)?(?:{verbo}|hazlo)(?:\s+(?:por favor|ya|ahora))?", t)
    if not firme:
        if breve == "afirma":
            return _reply("Para hacer cambios en School necesito un *sí* claro. ¿Lo hago?", state, "form_confirm",
                          oferta=oferta, fe=fe)
        return None
    if accion == "guardar" and fe:
        return await _guardar(fe, state, caps, context, forzar=True)
    if accion == "publicar" and fe:
        return await _cambiar_estado(fe, "publicado", state, caps, context)
    if accion == "estado":
        objetivo = fe if fe and fe.get("id") == oferta.get("formulario") else None
        if objetivo is None:
            res = await _tool("get_formulario", {"id": oferta["formulario"]}, context)
            if res.get("error"):
                return _reply(_error(res, "cambié el estado"), state, "error")
            objetivo = fd.desde_estructura(res["result"])
        return await _cambiar_estado(objetivo, oferta["estado"], state, caps, context)
    if accion in ("vincular", "desvincular"):
        return await _ejecutar_vinculo(oferta, state, caps, context, fe)
    return None


# ── Vínculo evento ↔ formulario ─────────────────────────────────────────────

def _partes(texto: str) -> Tuple[str, str]:
    """(frase del formulario, frase del evento) dentro del mensaje."""
    t = plano(texto)
    i_f, i_e = t.find("formulario"), t.find("evento")
    if i_f >= 0 and i_e >= 0:
        if i_f < i_e:
            return t[i_f:i_e], t[i_e:]
        return t[i_f:], t[i_e:i_f]
    if i_e >= 0:
        return t[:i_e], t[i_e:]
    if i_f >= 0:
        return t[i_f:], ""
    return t, ""


async def _evento_detalle(eid, context):
    return await _tool("get_evento", {"id": eid}, context)


async def _vincular(user_text, state, caps, context, fe, evento_frase=None, form_id=None):
    if not _puede(caps, "vincular_formulario"):
        return _reply("🔒 Tu usuario no tiene permiso para modificar eventos, así que no puedo vincularles formularios.",
                      state, "denied", access_denied=True)
    f_frase, e_frase = _partes(user_text)
    if "formulario" not in plano(user_text):
        f_frase = "ese formulario"  # "vincúlalo", "quiero vincularlo primero": el formulario en curso
    if "evento" not in plano(user_text):
        e_frase = ""
    if evento_frase is not None:
        e_frase = evento_frase
    # Formulario
    if form_id:
        rf = {"ok": {"id": form_id}}
    elif form_id is None and evento_frase is not None and fe and fe.get("id") is None:
        rf = {"borrador": fe}
    else:
        rf = await resolver("formulario", f_frase or "ese formulario", state, context, fe)
    if "error" in rf:
        return _reply(_error(rf["error"], "pude buscar el formulario"), state, "error")
    if "ambiguo" in rf:
        return _aclarar("formulario", rf["ambiguo"], "vincula el formulario numero {id} con el evento " + e_frase, state)
    if "ninguno" in rf:
        return _reply(f"No pude identificar el formulario: {rf['ninguno']}. ¿Cuál es? Dime su nombre.", state,
                      "clarify", needs_clarification=True)
    borrador = rf.get("borrador")
    fid = None if borrador else rf["ok"]["id"]
    # Evento
    if not plano(e_frase).replace("evento", "").strip() and not re.search(r"\bevento\b", plano(e_frase)):
        ents = [e for e in state.get("entidades") or [] if e["tipo"] == "evento"]
        if len(ents) == 1 or state.get("foco_evento"):
            e_frase = "ese evento"
        else:
            return _reply("¿Con qué evento lo vinculo? Dime su nombre o su fecha.", state, "link_missing",
                          needs_clarification=True, oferta={"tipo": "pedir_evento", "formulario": fid})
    re_ = await resolver("evento", e_frase or "ese evento", state, context)
    if "error" in re_:
        return _reply(_error(re_["error"], "pude buscar el evento"), state, "error")
    if "ambiguo" in re_:
        base = f"vincula el formulario numero {fid}" if fid else "vincula este formulario"
        return _aclarar("evento", re_["ambiguo"], base + " con el evento numero {id}", state)
    if "ninguno" in re_:
        return _reply(f"No pude identificar el evento: {re_['ninguno']}. ¿Cuál es? Dime su nombre o su fecha.", state,
                      "link_missing", needs_clarification=True, oferta={"tipo": "pedir_evento", "formulario": fid})
    eid = re_["ok"]["id"]
    # Revalidación contra School: existen, son de la institución y el usuario puede verlos.
    ev = await _evento_detalle(eid, context)
    if ev.get("error"):
        return _reply(_error(ev, "lo vinculé"), state, "error")
    ev = ev["result"]
    if fid:
        fr = await _tool("get_formulario", {"id": fid}, context)
        if fr.get("error"):
            return _reply(_error(fr, "lo vinculé"), state, "error")
        form = fr["result"]
    else:
        form = {"titulo": borrador.get("titulo"), "estado": "sin guardar", "preguntas": borrador["preguntas"]}
    st = recordar(state, "evento", eid, ev["nombre"], "consultado")
    if fid:
        st = recordar(st, "formulario", fid, form["titulo"], "consultado")
    actual = ev.get("formulario")
    if actual and fid and int(actual["id"]) == int(fid):
        return _reply(f"**{form['titulo']}** ya es el formulario de inscripción de **{ev['nombre']}**.", st, "link_done")
    partes = [f"Voy a vincular el formulario **{form.get('titulo')}**"
              + (f" (número {fid})" if fid else "") + f" como formulario de inscripción del evento **{ev['nombre']}** "
              f"({_fecha(ev.get('fechaInicial'))}" + (f" a las {_hora(ev.get('hora'))}" if ev.get("hora") else "")
              + f", número {eid})."]
    if not fid:
        partes.append("Como todavía no está guardado, primero lo guardaré en School (en borrador).")
    elif fe and fe.get("id") == fid and fd.sucio(fe):
        partes.append("Ojo: tiene cambios sin guardar en esta conversación; vincular no los guarda.")
    if actual:
        partes.append(f"El evento ya tiene vinculado **{actual['titulo']}**; lo reemplazaré.")
    elif ev.get("formUrl"):
        partes.append("El evento tiene un formulario externo; lo reemplazaré por este.")
    if form.get("estado") != "publicado":
        partes.append("El formulario no está publicado: quedará vinculado, pero nadie podrá inscribirse hasta que lo "
                      "publiques. No lo publicaré sin que me lo pidas.")
    if not form.get("preguntas"):
        partes.append("Además, todavía no tiene preguntas.")
    partes.append("¿Lo vinculo?")
    return _reply(" ".join(partes), st, "link_confirm",
                  oferta={"tipo": "accion", "accion": "vincular", "evento": eid, "formulario": fid},
                  fe=fe if fe and (fe.get("id") == fid or not fid) else None)


async def _desvincular(user_text, state, caps, context, fe):
    if not _puede(caps, "vincular_formulario"):
        return _reply("🔒 Tu usuario no tiene permiso para modificar eventos.", state, "denied", access_denied=True)
    _, e_frase = _partes(user_text)
    eid = None
    if e_frase.strip():
        r = await resolver("evento", e_frase, state, context)
        if "ambiguo" in r:
            return _aclarar("evento", r["ambiguo"], "desvincula el formulario del evento numero {id}", state)
        if "ok" in r:
            eid = r["ok"]["id"]
    if eid is None:
        # "Desvincula el formulario": el evento que tiene vinculado el formulario en foco.
        fid = (fe or {}).get("id") or state.get("foco_formulario")
        if fid:
            fr = await _tool("get_formulario", {"id": fid}, context)
            evs = ((fr.get("result") or {}).get("eventos") or []) if not fr.get("error") else []
            if len(evs) == 1:
                eid = evs[0]["id"]
            elif len(evs) > 1:
                return _aclarar("evento", evs, "desvincula el formulario del evento numero {id}", state)
        eid = eid or state.get("foco_evento")
    if not eid:
        return _reply("¿De qué evento quito el formulario?", state, "clarify", needs_clarification=True)
    ev = await _evento_detalle(eid, context)
    if ev.get("error"):
        return _reply(_error(ev, "lo desvinculé"), state, "error")
    ev = ev["result"]
    if not ev.get("formulario"):
        return _reply(f"**{ev['nombre']}** no tiene un formulario de School vinculado.", state, "link_done")
    n = ev.get("inscritos") or 0
    aviso = (f" Ya tiene {n} inscrito{'s' if n != 1 else ''}: las respuestas no se borran, siguen en el formulario."
             if n else "")
    return _reply(f"¿Quito **{ev['formulario']['titulo']}** del evento **{ev['nombre']}**?{aviso} El formulario no se "
                  "elimina. Respóndeme *sí* o *no*.", recordar(state, "evento", eid, ev["nombre"], "consultado"),
                  "link_confirm", oferta={"tipo": "accion", "accion": "desvincular", "evento": eid,
                                          "formulario": ev["formulario"]["id"]})


async def _ejecutar_vinculo(oferta, state, caps, context, fe):
    eid, fid = oferta["evento"], oferta.get("formulario")
    desvincular = oferta["accion"] == "desvincular"
    if not desvincular and not fid:
        if not fe or fe.get("id") is not None:
            return _reply("Perdí el borrador del formulario; dime de nuevo qué formulario vincular.", state, "error")
        r = await _guardar(fe, state, caps, context, forzar=True)
        if r["final_data"].get("school_outcome") != "form_saved":
            return r
        state, fe = r["final_data"]["_school"], r["final_data"]["_school"]["fe"]
        fid = fe["id"]
    res = await _tool("vincular_formulario", {"id": eid, "idFormulario": None if desvincular else fid}, context)
    if res.get("error"):
        if res.get("error_kind") == "forbidden":
            (context.get("final_data") or {}).pop("_school_caps", None)
        return _reply(_error(res, "lo desvinculé" if desvincular else "lo vinculé"), state, "link_failed")
    # Verificación: se relee el evento.
    ev = await _evento_detalle(eid, context)
    ev = ev.get("result") or {}
    ok = (not ev.get("formulario")) if desvincular else int((ev.get("formulario") or {}).get("id") or 0) == int(fid)
    st = recordar(state, "evento", eid, ev.get("nombre"), "editado")
    if not ok:
        return _reply("Envié el cambio pero al releer el evento no lo veo aplicado; revísalo en Gestión de eventos antes "
                      "de repetirlo.", st, "link_unknown")
    if desvincular:
        return _reply(f"Listo: **{ev['nombre']}** ya no tiene formulario de inscripción (lo verifiqué). El formulario "
                      "sigue existiendo en Formularios.", st, "link_done")
    f = ev["formulario"]
    st = recordar(st, "formulario", fid, f["titulo"], "editado")
    texto = (f"Listo: **{f['titulo']}** es ahora el formulario de inscripción de **{ev['nombre']}** (lo verifiqué en "
             "School).")
    if f["estado"] != "publicado":
        texto += " Sigue en borrador, así que todavía nadie puede inscribirse; cuando quieras, dime «publícalo»."
    else:
        texto += f" Ya está publicado: la gente se inscribe desde el evento o en /formulario-publico/{f['slug']}."
    return _reply(texto, st, "link_done", fe=st.get("fe") if (st.get("fe") or {}).get("id") == fid else None)


# ── Consultas sobre registros y su relación ─────────────────────────────────

async def _consultas(user_text, t, state, caps, context, fe):
    from orchestrator.interceptors.schoolsena_modules import read_act
    if read_act(user_text) in ("crear", "editar", "eliminar"):
        return None  # "quiero crear un formulario de inscripción para un evento" no es una consulta
    foco_f = (fe or {}).get("id") or state.get("foco_formulario")
    foco_e = state.get("foco_evento")
    ref = re.search(_CUE_RECIENTE, t) or re.search(_CUE_PRIMERO, t)

    # "¿Qué formulario tiene ese evento?" / "Muéstrame los formularios de ese evento".
    if re.search(r"\bformularios?\b.*\b(de|del|tiene|lleva)\b.*\bevento\b|\bque formulario tiene\b|"
                 r"\bevento\b.*\b(que|cual) formulario\b", t) and (ref or foco_e or re.search(r"\bevento\s+\w", t)):
        r = await resolver("evento", t[t.find("evento"):] if "evento" in t else "ese evento", state, context)
        if "ok" not in r:
            return None if "ninguno" in r else _respuesta_ref(r, "evento", "dime qué formulario tiene el evento numero {id}", state)
        return await _info_evento(r["ok"]["id"], state, context, "formulario")

    # "¿Cuántas personas se han inscrito?"
    if re.search(r"\bcuant\w*\b.*\b(inscri\w*|registrad\w*|apuntad\w*)\b|\binscritos\b|\bse han inscrito\b", t) \
            and not re.search(r"\b(solicitudes|aprendices de la ficha|matricul)\w*", t):
        frase = t[t.find("evento"):] if "evento" in t else None
        if frase or foco_e or (foco_f and fe and fe.get("eventos")):
            if frase:
                r = await resolver("evento", frase, state, context)
                if "ok" not in r:
                    return _respuesta_ref(r, "evento", "cuantos inscritos tiene el evento numero {id}", state)
                eid = r["ok"]["id"]
            else:
                eid = foco_e or (fe["eventos"][0]["id"] if fe and fe.get("eventos") else None)
            return await _info_evento(eid, state, context, "inscritos")
        return None

    # "¿Está publicado?" (el formulario en foco, o el del evento en foco).
    if re.search(r"\b(esta|quedo|sigue|ya esta|ya quedo)\s+(publicad\w*|en borrador|activo|abierto)\b|\bse publico\b", t) \
            and (foco_f or foco_e):
        if foco_f:
            res = await _tool("get_formulario", {"id": foco_f}, context)
            if res.get("error"):
                return _reply(_error(res, "pude consultarlo"), state, "error")
            f = res["result"]
            return _reply(f"**{f['titulo']}** está {'publicado' if f['estado'] == 'publicado' else 'en ' + f['estado']}"
                          + ("." if f["estado"] == "publicado" else ": todavía no se puede responder. Dime «publícalo» si "
                             "quieres abrirlo."), state, "form_info")
        return await _info_evento(foco_e, state, context, "formulario")

    # "¿Dónde puedo verlo?"
    if re.search(r"\bdonde\b.*\b(ver|verlo|verla|veo|encuentro|encontrarlo|esta|queda|abro|abrirlo|lo reviso|"
                 r"revisarlo|editarlo)\b|\bcomo (lo )?(abro|veo)\b", t) \
            and foco_f and not re.search(r"\b(sede|ambiente|clase|horario|nota)\w*", t):
        res = await _tool("get_formulario", {"id": foco_f}, context)
        if res.get("error"):
            return _reply(_error(res, "pude consultarlo"), state, "error")
        f = res["result"]
        texto = (f"**{f['titulo']}** se edita en **Formularios** (constructor: /formularios/builder/{f['id']})."
                 + (f" Las personas lo responden en /formulario-publico/{f['slug']}." if f["estado"] == "publicado"
                    else " Como está en borrador, todavía no tiene enlace público para responder."))
        if f.get("eventos"):
            texto += " Está vinculado a " + ", ".join(f"**{e['nombre']}**" for e in f["eventos"]) + "."
        return _reply(texto + " ¿Te llevo al constructor?", state, "form_info",
                      oferta={"tipo": "navegar", "ruta": "/formularios", "nombre": "Formularios"},
                      quick_replies=[{"title": "Abrir en el constructor", "text": "llévame"}])

    # "Muéstrame el evento que creamos" / "el formulario anterior".
    m = re.search(r"\b(muestrame|ensename|dime|como (quedo|es|esta)|ver|abre|abreme|consulta|revisa)\b.*\b(el|ese|este|del)\s+"
                  r"(evento|formulario)\b(?!s)", t)
    if m and m.group(4) == "formulario" and re.search(r"\b(edit|modific|cambi|complet|arregl)\w*", t):
        return await _abrir(user_text, state, caps, context)  # abrirlo PARA EDITAR, no sólo verlo
    if m and (ref or _nombre_en(t[m.start(4):]) or re.search(r"\b(evento|formulario)\s+(numero|de|del)\b", t)):
        tipo = m.group(4)
        r = await resolver(tipo, t[m.start(4):], state, context, fe)
        if "ok" not in r:
            if "borrador" in r:
                return await _mostrar(fe, state, context)
            return None if "ninguno" in r and not ref else _respuesta_ref(
                r, tipo, ("muestrame el evento numero {id}" if tipo == "evento" else "muestrame el formulario numero {id}"),
                state)
        st = _pausar_op(state)
        if tipo == "evento":
            return await _info_evento(r["ok"]["id"], st, context, "todo")
        res = await _tool("get_formulario", {"id": r["ok"]["id"]}, context)
        if res.get("error"):
            return _reply(_error(res, "pude consultarlo"), st, "error")
        f = fd.desde_estructura(res["result"])
        if fe and fd.sucio(fe) and fe.get("id") != f["id"]:
            return _reply(fd.presentar(f), recordar(st, "formulario", f["id"], f["titulo"], "consultado"), "form_preview")
        st = recordar(_con_fe(st, f), "formulario", f["id"], f["titulo"], "consultado")
        return _reply(fd.presentar(f), st, "form_preview", fe=f)
    return None


def _respuesta_ref(r, tipo, cmd, state):
    if "ambiguo" in r:
        return _aclarar(tipo, r["ambiguo"], cmd, state)
    if "error" in r:
        return _reply(_error(r["error"], "pude consultarlo"), state, "error")
    return _reply(f"No pude identificar el {tipo}: {r.get('ninguno')}.", state, "clarify", needs_clarification=True)


async def _info_evento(eid, state, context, que: str):
    res = await _evento_detalle(eid, context)
    if res.get("error"):
        return _reply(_error(res, "pude consultar el evento"), state, "error")
    ev = res["result"]
    st = recordar(state, "evento", ev["id"], ev["nombre"], "consultado")
    f = ev.get("formulario")
    if f:
        st = {**st, "foco_formulario": f["id"]}
    nombre = f"**{ev['nombre']}**"
    if que == "inscritos":
        if not f:
            texto = f"{nombre} no tiene formulario de inscripción de School, así que School no registra inscripciones."
            if ev.get("formUrl"):
                texto += " Usa un formulario externo; las inscripciones están allá."
        else:
            n = ev.get("inscritos") or 0
            texto = (f"{nombre} tiene {n} inscrito{'s' if n != 1 else ''} (respuestas a **{f['titulo']}**)."
                     + (" El formulario sigue en borrador, por eso nadie ha podido inscribirse." if f["estado"] != "publicado"
                        and not n else ""))
        return _reply(texto, st, "event_info")
    if not f:
        forma = " Usa un formulario externo." if ev.get("formUrl") else ""
        txt_f = f"no tiene formulario de inscripción de School.{forma}"
    else:
        txt_f = (f"tiene como formulario de inscripción **{f['titulo']}** (número {f['id']}, "
                 f"{'publicado' if f['estado'] == 'publicado' else f['estado']}, {f['preguntas']} preguntas).")
    if que == "formulario":
        return _reply(f"{nombre} {txt_f}", st, "event_info")
    texto = (f"{nombre} (número {ev['id']}): {_fecha(ev.get('fechaInicial'))}"
             + (f" a las {_hora(ev.get('hora'))}" if ev.get("hora") else "")
             + (f" hasta las {_hora(ev.get('hora_final'))}" if ev.get("hora_final") else "")
             + f", {str(ev.get('tipoEvento') or '').lower() or 'presencial'}, estado {str(ev.get('estado') or '').lower()}. "
             + f"El evento {txt_f}")
    if f:
        texto += f" Inscritos: {ev.get('inscritos') or 0}."
    return _reply(texto, st, "event_info")
