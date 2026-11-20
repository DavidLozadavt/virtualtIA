"""
orchestrator/interceptors/schoolsena_ops.py — Continuidad: propuestas y operaciones.

Dos piezas que antes no existían como tales:

1. PROPUESTA (`state["oferta"]`): lo que Nexo acaba de ofrecer ("¿Te llevo a la
   pantalla?", "¿A cuál te refieres: A o B?"). Queda atada al turno en que se
   hizo y sólo vale en el siguiente. Un "sí", "dale", "llévame", "no", "después"
   o "la segunda" se interpretan CONTRA ella. Antes la pregunta se escribía en el
   texto y se olvidaba: el "sí" siguiente no significaba nada.

2. OPERACIÓN (`state["op"]`, `state["borradores"]`): un formulario conversacional
   declarado en `OPERATIONS` (campos, preguntas, cómo se ejecuta y se verifica).
   El mismo motor sirve para crear eventos y formularios y para lo que se integre
   después. Mientras Nexo espera un dato, el mensaje siguiente se lee primero
   como ESE dato ("NU EVENT" es el nombre, no una navegación); un cambio de tema
   explícito pausa la operación y la guarda como borrador; "continuemos con el
   evento" la recupera.

Las escrituras sólo se ejecutan con un sí inequívoco a un resumen con la fecha
completa; el backend vuelve a autorizar y se verifica que quedó guardado.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

from core.semantic.morphology import normalize

OFERTA_TTL_S = 10 * 60
BORRADOR_TTL_S = 2 * 60 * 60

OPERATIONS: Dict[str, Dict[str, Any]] = {
    "eventos": {
        "nombre": "evento", "art": "el", "consulta": "crear_evento", "tool": "crear_evento", "verificar": "eventos",
        "id": ("evento", ("idEvento", "id")), "ruta": "/gestion-eventos",
        "campos": [
            {"id": "nombre", "tipo": "texto", "req": True, "pregunta": "¿Cómo se va a llamar el evento?"},
            {"id": "fechaInicial", "tipo": "fecha", "req": True,
             "pregunta": "¿Qué día será? Puedes decirme, por ejemplo, «el viernes de la próxima semana»."},
            {"id": "hora", "tipo": "hora", "req": True, "pregunta": "¿A qué hora empieza?"},
            {"id": "hora_final", "tipo": "hora_fin", "req": False},
            {"id": "tipoEvento", "tipo": "modalidad", "req": False},
        ],
        "hecho": "Queda en estado pendiente en Gestión de eventos.",
    },
    "formularios": {
        "nombre": "formulario", "art": "el", "consulta": "crear_formulario", "tool": "crear_formulario",
        "verificar": "formularios", "id": (None, ("id",)), "ruta": "/formularios",
        "campos": [
            {"id": "titulo", "tipo": "texto", "req": True, "pregunta": "¿Qué título tendrá el formulario?"},
            {"id": "descripcion", "tipo": "descripcion", "req": False},
        ],
        "hecho": "Queda como borrador en Formularios; las preguntas se agregan en su pantalla.",
    },
}

# ── Respuestas breves ───────────────────────────────────────────────────────

_AFIRMA = r"(si|claro|dale|de una|hazlo|hagale|ok|okay|listo|bueno|va|vale|perfecto|por favor|porfa|sale|obvio|" \
          r"correcto|eso|confirmo|adelante|sip)"
_AFIRMA_FIRME = r"(si|claro|dale|de una|hazlo|hagale|confirmo|adelante|crealo|crealo ya|sip|obvio|por favor)"
_NIEGA = r"(no|mejor no|despues|luego|ahora no|no gracias|dejalo|olvidalo|nada|cancela|cancelar|ya no|tampoco)"
_NAVEGA = r"(llevame|llevame alla|abrela|abrelo|abre|vamos|ve|ir|abrir)"
_MUESTRA = r"(muestrame|mostrar|dime|dimelo|consultalo|consultala|ensename|leeme|leemelo)"


def _plano(texto: str) -> str:
    return normalize(texto or "").replace("ñ", "n").strip(" .!¡?¿")


def respuesta_breve(texto: str) -> Optional[str]:
    """'afirma' | 'niega' | 'navega' | 'muestra' | None (si dice algo más que eso)."""
    t = _plano(texto)
    if not t or len(t.split()) > 6:
        return None
    for nombre, rx in (("niega", _NIEGA), ("navega", _NAVEGA), ("muestra", _MUESTRA), ("afirma", _AFIRMA)):
        if re.fullmatch(rf"{rx}(\s+(por favor|porfa|gracias|ya|pues|entonces|si|ahi|alla|esa|ese|eso|"
                        rf"la pantalla|el modulo))*", t):
            return nombre
    return None


def afirma_firme(texto: str) -> bool:
    """Un sí inequívoco: lo único que autoriza una escritura ("listo"/"ok"/"bueno" no)."""
    return bool(re.fullmatch(rf"{_AFIRMA_FIRME}(\s+(por favor|porfa|gracias|crealo|hazlo|confirmo|dale|"
                             rf"crealo ya|de una|si))*", _plano(texto)))


# ── Propuestas ──────────────────────────────────────────────────────────────

def proponer(state: dict, turno: int, tipo: str, **datos) -> dict:
    """Anota en el estado lo que Nexo acaba de ofrecer (vale sólo en el turno siguiente)."""
    return {**state, "oferta": {"tipo": tipo, "turn": turno, "ts": time.time(), **datos}}


def oferta_vigente(state: dict, turno: int) -> Optional[dict]:
    o = state.get("oferta")
    if not isinstance(o, dict):
        return None
    if time.time() - float(o.get("ts") or 0) > OFERTA_TTL_S:
        return None
    if turno and o.get("turn") and int(o["turn"]) != int(turno) - 1:
        return None
    return o


# ── Operaciones ─────────────────────────────────────────────────────────────

def _extraer(spec: dict, texto: str, campos: dict, pregunta: Optional[str]) -> dict:
    """Valores de los campos de la operación que trae el mensaje."""
    from core.semantic import temporal as tp
    from core.semantic.fechas import leer_fecha
    nuevos = dict(campos)
    t = _plano(texto)
    tipos = {c["id"]: c["tipo"] for c in spec["campos"]}
    fecha, aviso = leer_fecha(texto)
    _h = r"(\d{1,2}|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
    rango = re.search(rf"\bde\s+(?:las?\s+)?{_h}(?::(\d{{2}}))?\s+(?:a|hasta)\s+(?:las?\s+)?{_h}(?::(\d{{2}}))?\s*"
                      r"(am|pm|de la tarde|de la manana|de la noche)?\b", t)
    hora = tp.read_time(texto).value
    for cid, tipo in tipos.items():
        if tipo == "fecha" and fecha:
            nuevos[cid] = fecha
            if aviso:
                nuevos["_aviso_fecha"] = aviso
        elif tipo == "hora" and rango:
            h1 = tp.read_time(f"a las {rango.group(1)}{':' + rango.group(2) if rango.group(2) else ''} {rango.group(5) or ''}").value
            if h1:
                nuevos[cid] = h1
        elif tipo == "hora" and hora and (pregunta == cid or re.search(r"\b(a las|a la|desde las|hora)\b", t)):
            nuevos[cid] = hora
        elif tipo == "hora_fin" and rango:
            h2 = tp.read_time(f"a las {rango.group(3)}{':' + rango.group(4) if rango.group(4) else ''} {rango.group(5) or ''}").value
            if h2:
                nuevos[cid] = h2
        elif tipo == "modalidad":
            for clave, valor in (("virtual", "VIRTUAL"), ("hibrid", "HIBRIDO"), ("presencial", "PRESENCIAL")):
                if re.search(rf"\b{clave}", t):
                    nuevos[cid] = valor
        elif tipo == "descripcion":
            m = re.search(r"\b(?:descripcion|de que se trata|trata de|sobre)\s*:?\s+(.{5,500})$", texto, re.I)
            if m:
                nuevos[cid] = m.group(1).strip()
    # El nombre/título: entre comillas, tras "se llama…", o el mensaje entero
    # cuando eso es lo que se preguntó y no trae fecha ni hora.
    texto_id = next((c["id"] for c in spec["campos"] if c["tipo"] == "texto"), None)
    if texto_id:
        m = re.search(r"[\"«“]([^\"»”]{2,120})[\"»”]", texto)
        if m:
            nuevos[texto_id] = m.group(1).strip()
        else:
            m = re.search(r"\b(?:llamado|llamada|titulado|titulada|que se llame|se llama|se va a llamar|se llamar[aá]|"
                          r"con el nombre|nombre|titulo)\s*:?\s+(.{2,120}?)(?:\s*[,.]|\s+(?:el|para el|a las|de las|"
                          r"manana|hoy|virtual|presencial)\b|$)", texto, re.I)
            if m:
                nuevos[texto_id] = m.group(1).strip(" .,")
            elif pregunta == texto_id and not fecha and not hora and len(texto.strip()) >= 2:
                nuevos[texto_id] = texto.strip(" .,¿?¡!")
    return nuevos


def _falta(spec: dict, campos: dict) -> Optional[dict]:
    return next((c for c in spec["campos"] if c["req"] and not campos.get(c["id"])), None)


def resumen_op(spec: dict, campos: dict) -> str:
    from core.semantic.fechas import fecha_larga
    partes = []
    for c in spec["campos"]:
        v = campos.get(c["id"])
        if not v:
            continue
        if c["tipo"] == "texto":
            partes.append(f"**{v}**")
        elif c["tipo"] == "fecha":
            partes.append(f"el **{fecha_larga(v)}**")
        elif c["tipo"] == "hora":
            partes.append(f"a las **{v}**")
        elif c["tipo"] == "hora_fin":
            partes.append(f"hasta las **{v}**")
        elif c["tipo"] == "modalidad":
            partes.append(v.lower())
        elif c["tipo"] == "descripcion":
            partes.append(f"descripción: «{v}»")
    return ", ".join(partes)


def siguiente(recurso: str, op: dict) -> str:
    """Pregunta el siguiente dato que falta, o presenta el resumen para confirmar."""
    spec = OPERATIONS[recurso]
    falta = _falta(spec, op["campos"])
    if falta:
        op["pregunta"], op["confirmar"] = falta["id"], False
        return falta["pregunta"]
    op["pregunta"], op["confirmar"] = None, True
    aviso = op["campos"].pop("_aviso_fecha", None)
    texto = f"Voy a crear {spec['art']} {spec['nombre']} {resumen_op(spec, op['campos'])}. ¿Lo creo?"
    return texto + (f" (Ojo: {aviso}.)" if aviso else "")


def iniciar(recurso: str, texto: str) -> dict:
    spec = OPERATIONS[recurso]
    campos = _extraer(spec, texto, {}, None)
    texto_id = next(c["id"] for c in spec["campos"] if c["tipo"] == "texto")
    if campos.get(texto_id) and re.search(rf"\b({spec['nombre']}|crear|nuevo|nueva)\b", _plano(campos[texto_id])):
        campos.pop(texto_id)
    return {"recurso": recurso, "campos": campos, "pregunta": None, "confirmar": False}


_RETOMA = re.compile(r"\b(continu\w*|sigamos|seguimos|retom\w*|volvamos|volver|reanud\w*)\b")


def quiere_retomar(texto: str) -> Optional[str]:
    """'continuemos con el evento' → 'eventos'; None si no pide retomar."""
    t = _plano(texto)
    if not _RETOMA.search(t):
        return None
    for recurso, spec in OPERATIONS.items():
        if re.search(rf"\b{spec['nombre']}s?\b", t):
            return recurso
    return "*" if re.search(r"\b(lo que (estabamos|ibamos|estaba)|eso|lo anterior|lo de antes|proceso|tramite|"
                            r"operacion)\b", t) else None


def cancela(texto: str) -> bool:
    # Una o varias expresiones de cancelar seguidas ("no, cancélalo", "no, olvídalo").
    return bool(re.fullmatch(r"(?:(no|cancela|cancelalo|cancelar|olvidalo|dejalo|ya no|mejor no|no lo crees|"
                             r"no quiero|descartalo)\s*)+(\s*(el|la|lo|evento|formulario|gracias|por favor))*",
                             _plano(texto)))
