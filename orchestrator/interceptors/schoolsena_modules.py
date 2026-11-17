"""
orchestrator/interceptors/schoolsena_modules.py — Nexo y los módulos reales de School.

Tres dimensiones que antes estaban sueltas:

1. AUTORIZACIÓN — qué puede hacer el usuario. Sale de sus permisos efectivos
   (`lyra/capacidades`, calculados en vivo en el backend) cruzados con el
   inventario de School: cada pantalla del front declara qué permisos la abren
   (`projects/school_inventario.json`, generado del código por
   `scripts/school_inventory.py`). School no tiene permisos por operación: un
   permiso abre un módulo y lo que se puede hacer allí es lo que ofrece la pantalla.
2. INTEGRACIÓN — qué sabe ejecutar Nexo. Un registro declarativo (`QUERIES`,
   `CREATES`) dice qué módulo tiene consulta o creación integradas, con qué
   herramienta y filtros. Un permiso nuevo con ruta queda navegable sin código;
   consultarlo o escribir en él exige registrar aquí su integración.
3. INTENCIÓN — qué pide ahora: navegar, listar, contar, leer, filtrar, crear,
   editar, eliminar, saber sus permisos; sobre qué módulo (recuperación por
   cobertura de TODAS las palabras, no por la primera que coincide) y con qué
   contexto (módulo activo, último resultado, operación en curso).

La autorización final la hace siempre el backend en cada consulta.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.semantic.morphology import normalize, strict_closeness

_INVENTARIO = Path(__file__).resolve().parents[2] / "projects" / "school_inventario.json"

# ── Integraciones registradas ────────────────────────────────────────────────
#: consulta del backend → cómo se presenta y filtra. `ruta` liga la consulta con
#: el módulo del inventario (y con su navegación).
QUERIES: Dict[str, Dict[str, Any]] = {
    "regionales": {"tool": "get_regionales", "ruta": "/regionales", "uno": "regional", "varios": "regionales", "g": "f",
                   "extra": ["departamento", "telefono"], "estado": None, "global": True},
    "centros": {"tool": "get_centros_formacion", "ruta": "/centrosFormacion", "uno": "centro de formación",
                "varios": "centros de formación", "g": "m", "extra": ["ciudad", "direccion"], "estado": None},
    "sedes": {"tool": "get_sedes", "ruta": "/sedesSena", "uno": "sede", "varios": "sedes", "g": "f",
              "extra": ["centro", "direccion"], "estado": None},
    "ambientes": {"tool": "get_ambientes", "ruta": "/ambientes", "uno": "ambiente", "varios": "ambientes", "g": "m",
                  "extra": ["tipo", "sede", "capacidad"], "estado": None, "de": "sede"},
    "periodos": {"tool": "get_periodos", "ruta": "/gestion-academica/configuracion/periodos", "uno": "periodo",
                 "varios": "periodos", "g": "m", "extra": ["fechaInicial", "fechaFinal"], "estado": "vigente",
                 # Sin acceso al módulo, el backend devuelve los periodos de la propia matrícula.
                 "propia": "periodos_propios"},
    "jornadas": {"tool": "get_jornadas", "ruta": "/gestion-academica/configuracion/jornadas", "uno": "jornada", "varios": "jornadas", "g": "f",
                 "extra": ["horaInicial", "horaFinal", "centro"], "estado": "estado"},
    "planeacion": {"tool": "get_planeacion", "ruta": "/gestion-academica/configuracion/redes", "uno": "red de conocimiento",
                   "varios": "redes de conocimiento", "g": "f", "extra": ["programas"], "estado": None},
    "eventos": {"tool": "get_eventos", "ruta": "/gestion-eventos", "uno": "evento", "varios": "eventos", "g": "m",
                "extra": ["fechaInicial", "hora", "estado"], "estado": "estado"},
    "formularios": {"tool": "get_formularios", "ruta": "/formularios", "uno": "formulario", "varios": "formularios",
                    "g": "m", "extra": ["estado", "fechaLimite"], "estado": "estado"},
    "carpetas": {"tool": "get_carpetas_viajeras", "ruta": "/carpetas-viajeras", "uno": "carpeta viajera",
                 "varios": "carpetas viajeras", "g": "f", "extra": ["modulo", "total_horas"], "estado": "aprobada"},
    "configuracion_pagos": {"tool": "get_configuracion_pagos", "ruta": "/pagos/configuracion-pagos",
                            "uno": "concepto de pago", "varios": "conceptos de pago", "g": "m",
                            "extra": ["valor", "estado", "frecuenciaPago"], "estado": "estado"},
    "pagos_pendientes": {"tool": "get_pagos_pendientes", "ruta": "/gestion-contratos/pagos-pendientes",
                         "uno": "pago pendiente", "varios": "pagos pendientes", "g": "m",
                         "extra": ["valor", "fechaPago", "estado"], "estado": None},
    "solicitudes_inscripcion": {"tool": "get_solicitudes_inscripcion", "ruta": "/gestion-academica/inscripciones/solicitudes",
                                "uno": "solicitud de inscripción", "varios": "solicitudes de inscripción", "g": "f",
                                "extra": ["estado", "fecha"], "estado": "estado"},
    "configuracion_inscripcion": {"tool": "get_configuracion_inscripcion",
                                  "ruta": "/gestion-academica/inscripciones/configuracion",
                                  "uno": "configuración", "varios": "configuración", "g": "f", "extra": [],
                                  "estado": None, "objeto": True},
    "multimedia": {"tool": "get_multimedia", "ruta": "/multimedia/gestion-multimedia", "uno": "historia o reel",
                   "varios": "historias y reels", "g": "f", "extra": ["tipo", "created_at"], "estado": None},
    "informes_instructores": {"tool": "get_informes_instructores", "ruta": "/informeInstructor",
                              "uno": "informe de instructor", "varios": "informes de instructores", "g": "m",
                              "extra": ["descripcion", "fechaInicial", "numeroHoras"], "estado": None},
}

#: Qué valor de estado significa "activo" en cada consulta (los datos reales).
_ACTIVO = {"vigente": {True, 1, "1", "true"}, "estado": {"ACTIVO", "ACTIVA", "PUBLICADO", "PENDIENTE", "1", "Activo"},
           "aprobada": {1, True, "1"}}

#: Operaciones de escritura integradas (con contrato y prueba).
# Las operaciones (campos, preguntas, ejecución) viven en schoolsena_ops.OPERATIONS;
# aquí sólo se liga cada una con su módulo.
from orchestrator.interceptors.schoolsena_ops import OPERATIONS  # noqa: E402

CREATES: Dict[str, Dict[str, Any]] = {k: {"consulta": v["consulta"], "ruta": v["ruta"]} for k, v in OPERATIONS.items()}

#: Consultas que ya resolvía la fase 4 bajo otro nombre de capacidad del NLU.
NLU_CAP_QUERY = {"centers": "centros", "sites": "sedes", "rooms": "ambientes", "periods": "periodos",
                 "shifts": "jornadas", "planning": "planeacion"}

_FILLER = {"gestion", "modulo", "seccion", "pantalla", "parte", "menu", "pagina", "opcion", "lista", "listado",
           "informacion", "datos", "registros", "registro", "todo", "todos", "todas", "nexo", "porfa", "favor",
           "sistema", "school", "schoolsena", "plataforma", "puedo", "puedes", "quiero", "dime", "dame", "muestrame",
           "mostrar", "ver", "enseñame", "llevame", "lleva", "abre", "abreme", "ir", "vamos", "ensename", "cuales", "cuantos",
           "cuantas", "hay", "tengo", "tenemos", "estan", "registrados", "registradas", "existen", "son", "mis", "mi",
           "crear", "nuevo", "nueva", "agregar", "registrar", "editar", "modificar", "eliminar", "borrar", "activos",
           "activas", "vigentes", "publicados", "leeme", "leemelos", "leemelas", "permiso", "permisos", "acceso",
           "entrar", "hacer", "habilitadas", "habilitados", "funcionalidades", "quiero", "necesito", "por", "que",
           "como", "cual", "donde", "pero", "tambien", "ahora", "mejor", "solo", "sobre", "para", "una", "uno",
           "un", "los", "las", "del", "con", "esos", "esas", "estos", "estas", "alla", "aqui", "ahi"}
_STOP = {"de", "la", "el", "y", "o", "a", "en", "al", "se", "me", "te", "lo", "le", "es", "si", "no", "tu", "su"}


def _words(text: str) -> List[str]:
    return [w for w in normalize(text).split() if len(w) > 2 and w not in _STOP]


def _content(text: str) -> List[str]:
    return [w for w in _words(text) if w not in _FILLER]


def _camel(text: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text or "")


@lru_cache(maxsize=1)
def modules() -> List[Dict[str, Any]]:
    """Módulos navegables de School (una entrada por ruta), del inventario del código."""
    data = json.loads(_INVENTARIO.read_text(encoding="utf-8"))
    vistos, out = set(), []
    for m in data["modulos"]:
        ruta = m["ruta"]
        if not m["permisos"] or ":" in ruta or ruta in vistos or ruta == "/":
            continue
        vistos.add(ruta)
        # El nombre: el del permiso cuya ruta ES esta pantalla; si no hay (la
        # pantalla se abre con un permiso genérico como GESTION_USUARIO), el del
        # componente ("GestionEventos" → "Gestion Eventos").
        propio = next((v for v in data["permisos"].values() if v.get("ruta") == ruta), None)
        m = {**m, "nombre": propio["descripcion"] if propio else
             (_camel(m.get("componente") or ruta).replace(" Page", "").strip() or m["nombre"])}
        texto = " ".join([m.get("nombre") or "", m.get("grupo") or "", ruta.replace("/", " ").replace("-", " "),
                          _camel(m.get("componente") or "").replace("Page", "")])
        query = next((q for q, spec in QUERIES.items() if spec["ruta"] == ruta), None)
        out.append({**m, "claves": set(_words(_camel(texto))) - _FILLER - {"page"}, "consulta": query,
                    "crear": next((k for k, c in CREATES.items() if c["ruta"] == ruta), None)})
    return out


@lru_cache(maxsize=1)
def _df() -> Dict[str, int]:
    """En cuántos módulos aparece cada palabra clave: las comunes no distinguen."""
    cuenta: Dict[str, int] = {}
    for m in modules():
        for c in m["claves"]:
            cuenta[c] = cuenta.get(c, 0) + 1
    return cuenta


def strong(m: Dict[str, Any], text: str) -> bool:
    """¿El mensaje nombra ESE módulo con palabras propias (y no sólo comunes)?"""
    pedidas = _content(text)
    if not pedidas:
        return False
    df = _df()
    distintivas = [w for w in pedidas if any(
        (strict_closeness(w, c) or (len(w) > 5 and c.startswith(w[:6]))) and df.get(c, 99) <= 3 for c in m["claves"])]
    if bool(distintivas) and len(distintivas) / len(pedidas) >= 0.6:
        return True
    # O nombra el módulo COMPLETO ("medios de pago" → «Medio Pago»), aunque sus
    # palabras sean comunes por separado.
    nombre = [w for w in _words(m.get("nombre") or "") if w not in _FILLER]
    completo = bool(nombre) and all(any(w == p or (strict_closeness(w, p) and abs(len(w) - len(p)) <= 1)
                                        for p in pedidas) for w in nombre)
    # Un nombre de una sola palabra ("Eventos") basta si el mensaje pregunta por
    # datos ("¿qué eventos tengo programados?"); suelto, una palabra no decide.
    # …siempre que esa palabra distinga el módulo ("general" aparece en cinco).
    return completo and (len(nombre) >= 2 or (query_words(text) and df.get(nombre[0], 99) <= 3))


def accessible(mods: List[Dict[str, Any]], caps: Optional[dict]) -> List[Dict[str, Any]]:
    """Módulos a los que el usuario puede entrar según sus permisos efectivos."""
    perms = set((caps or {}).get("permisos") or [])
    consultas = set((caps or {}).get("consultas") or [])
    # También cuenta lo que el BACKEND le habilita para ese módulo (su consulta o
    # su operación): la pantalla del front puede pedir otros permisos (eventos se
    # abre con GESTION_USUARIO, pero el backend también acepta GESTION_EVENTOS).
    return [m for m in mods if perms & set(m["permisos"])
            or (m.get("consulta") and m["consulta"] in consultas)
            or (m.get("crear") and CREATES[m["crear"]]["consulta"] in consultas)]


def match_module(text: str, mods: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    El módulo que mejor cubre lo que dice el usuario.

    Se cuenta CUÁNTAS palabras de contenido del mensaje cubre cada módulo (con
    tolerancia a plurales y erratas): "gestión de informes de instructores" cubre
    dos palabras de «Informe de Pago» (grupo Gestión Instructor) y una de la
    lista de instructores; gana el que explica más del mensaje.
    """
    pedidas = _content(text)
    if not pedidas:
        return None
    mejor, mejor_k, empates = None, (0, False, 0.0, False, 0), []
    for m in mods:
        hits = [w for w in pedidas if any(strict_closeness(w, c) or (len(w) > 5 and c.startswith(w[:6])) for c in m["claves"])]
        if not hits:
            continue
        # Primero lo que más explica; a igualdad, el módulo cuyo NOMBRE es justo lo
        # pedido ("Redes" es la pantalla Redes, no Planeación aunque su ruta diga
        # "redes"); después el que Nexo sabe consultar y el más específico.
        nombre = [w for w in _words(m.get("nombre") or "") if w not in _FILLER]
        exacto = bool(nombre) and set(nombre) <= {c for w in pedidas for c in [w] + [x for x in nombre if strict_closeness(w, x)]}
        k = (len(set(hits)), exacto, len(set(hits)) / len(pedidas), bool(m["consulta"]), -len(m["claves"]))
        if k > mejor_k:
            mejor, mejor_k, empates = m, k, []
        elif k[:3] == mejor_k[:3] and mejor is not None and m["nombre"] != mejor["nombre"]:
            empates.append(m)
    if not mejor:
        return None
    return {**mejor, "_cubre": mejor_k[0], "_cobertura": mejor_k[2],
            "_empates": [e for e in empates if not mejor_k[1]]}


# ── Qué pide ────────────────────────────────────────────────────────────────

_ACTS = [
    ("permisos_consultables", r"\b(cuales|que) (modulos |pantallas )?(puedo|puedes) consultar (desde aqui|tu|aqui)\b|"
                              r"\bque puedes consultar\b|\bcuales consultas tu\b"),
    ("permisos_pantalla", r"\b(cuales|que) (modulos |pantallas )?(requieren|necesitan) (que )?abr"),
    ("permisos_crear", r"\bque (puedo|puedes) crear\b"),
    ("permisos_no_modificar", r"\bque no (puedo|puedes) (modificar|editar|cambiar)\b"),
    ("resumen", r"\b(resumen|resume|resumeme|resumir|panorama|como va todo|como vamos)\b"),
    ("pendientes", r"\bque (tengo|hay|tenemos) pendiente"),
    ("permisos_faltan", r"\b(permisos?|accesos?) (que )?no tengo\b|\bque (permisos|modulos) me faltan\b"),
    ("permisos_por_que", r"\bpor ?que no (puedo|tengo|me deja)\b"),
    ("permisos_puedo", r"\b(tengo permiso|puedo|me deja|tengo acceso)\b.*\b(crear|editar|modificar|eliminar|borrar|"
                       r"entrar|ver|consultar|registrar|agregar|gestionar)\b"),
    ("permisos", r"\b(que|cuales|mis) (permisos|accesos)\b|\bpermisos (tengo|administrativos|sobre)\b|"
                 r"\ba que modulos\b|\bque modulos (tengo|puedo)\b|\bque funcionalidades\b|"
                 r"\bque puedo hacer (en|con) (?!(lyra|nexo|esta|la plataforma|school|el sistema|aqui))|"
                 r"\bmuestrame mis permisos\b"),
    # Verbos de crear; los que también son sustantivos ("el programa") sólo con artículo detrás.
    ("crear", r"\b(crear|crea|creame|nuevo|nueva|agregar|agrega|registrar|registra|anadir|programar|montar|"
              r"organizar|armar|publicar)\b|\b(programa|monta|organiza|arma|haz|hagamos|abre|publica)\s+(un|una)\b"),
    ("editar", r"\b(editar|edita|modificar|modifica|cambiar|actualizar|actualiza)\b"),
    ("eliminar", r"\b(eliminar|elimina|borrar|borra|quitar|dar de baja)\b"),
    ("navegar", r"\b(llevame|lleva me|llevarme|ir a|ir al|vamos a|abre|abreme|abrir|entrar a|ingresa a|navega|"
                r"muestrame la (seccion|pantalla|pagina)|llevame alla|alla)\b"),
    ("contar", r"\b(cuant[oa]s|numero de|cantidad de|total de)\b"),
    ("leer", r"\b(leeme|leemelos|leemelas|leelos|leer|dictame|en voz alta)\b"),
]
_FILTRO_ACTIVO = re.compile(r"\b(activ[oa]s?|vigentes?|publicad[oa]s?|abiert[oa]s?|habilitad[oa]s?|aprobad[oa]s?)\b")
_ORDINAL = {"primero": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercera": 3, "cuarto": 4,
            "cuarta": 4, "quinto": 5, "quinta": 5, "ultimo": -1, "ultima": -1}


def read_act(text: str) -> Optional[str]:
    t = normalize(text)
    for name, rx in _ACTS:
        if re.search(rx, t):
            return name
    return None


def ordinal(text: str) -> Optional[int]:
    t = normalize(text)
    for w, n in _ORDINAL.items():
        if re.search(rf"\b{w}\b", t):
            return n
    m = re.search(r"\b(?:la|el|numero|#)\s*(\d{1,2})\b", t)
    return int(m.group(1)) if m else None


def split_compound(text: str):
    """
    ("alternativas"|"ambas", parte1, parte2) si el mensaje une dos peticiones
    con acciones distintas ("llévame a formularios o cuáles tengo activos").
    """
    t = normalize(text)
    # El conector puede ir con relleno: "y también preguntarte cuáles…", "y además dime…", "o mejor llévame…".
    relleno = r"(?:(?:tambien|ademas|luego|despues|mejor|de paso)\s+)?(?:(?:preguntarte|decirme|saber|que me digas|quiero saber)\s+)?"
    inicio = r"(?=(?:que|cuales|cuantos|cuantas|dime|muestrame|llevame|abre|me dices|crea|crear|quiero crear)\b)"
    for sep, tipo in ((rf"\s+o\s+{relleno}{inicio}", "alternativas"),
                      (rf"\s+y\s+{relleno}{inicio}", "ambas")):
        partes = re.split(sep, t, maxsplit=1)
        if len(partes) == 2 and all(p.strip() for p in partes):
            a, b = read_act(partes[0]) or "listar", read_act(partes[1]) or "listar"
            if a != b:
                return tipo, partes[0].strip(), partes[1].strip()
    return None


# ── Datos de un registro para presentar ─────────────────────────────────────

def _valor(r: dict, k: str) -> str:
    v = r.get(k)
    if v in (None, ""):
        return ""
    if k in ("fechaInicial", "fechaFinal", "fechaLimite"):
        return str(v)[:10]
    if k in ("horaInicial", "horaFinal", "hora"):
        return str(v)[:5]
    if k == "valor":
        from core.speech_format import format_price
        return format_price(v)
    if k in ("aprobada",):
        return "aprobada" if str(v) in ("1", "True", "true") else "sin aprobar"
    return str(v)


def rows_of(data) -> List[dict]:
    if isinstance(data, dict):
        for key in ("periodos", "redes", "data"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
        return []
    return [r for r in (data or []) if isinstance(r, dict)]


def is_active(spec: dict, r: dict) -> Optional[bool]:
    campo = spec.get("estado")
    if not campo:
        return None
    v = r.get(campo)
    return v in _ACTIVO.get(campo, set()) or str(v).upper() in {str(x).upper() for x in _ACTIVO.get(campo, set())}


def present(query: str, rows: List[dict], act: str, filtro: bool) -> str:
    """Lista, conteo o lectura (prosa para voz) de lo consultado."""
    from tools.nexiservice import natural_list
    spec = QUERIES[query]
    if spec.get("objeto"):
        # Un solo registro de configuración: se describe, no se enumera.
        c = rows[0] if rows else {}
        if not c:
            return "No encontré la configuración."
        abierta = "habilitadas" if c.get("inscripcionHabilitada") else "deshabilitadas"
        rango = (f", del {str(c.get('fechaInicioInscripcion'))[:10]} al {str(c.get('fechaFinInscripcion'))[:10]}"
                 if c.get("fechaInicioInscripcion") else "")
        return (f"Las inscripciones están **{abierta}**{rango}"
                + (f", con el formulario «{c['formulario']}»." if c.get("formulario") else "."))
    n = len(rows)
    nombre = spec["uno"] if n == 1 else spec["varios"]
    adj = (" activ" + ("a" if spec["g"] == "f" else "o") + ("s" if n != 1 else "")) if filtro else ""
    if not n:
        donde = "en School" if spec.get("global") else "en tu empresa"
        return (f"No hay {spec['varios']}{' activos' if filtro else ''} registrados {donde}." if spec["g"] == "m" else
                f"No hay {spec['varios']}{' activas' if filtro else ''} registradas {donde}.")
    if act == "contar":
        return f"Hay {n} {nombre}{adj}."
    etiqueta = lambda r: str(r.get("nombre") or r.get("titulo") or f"#{r.get('id')}").strip()
    if act == "leer":
        return f"{'Es' if n == 1 else 'Son'} {n} {nombre}{adj}: {natural_list([etiqueta(r) for r in rows[:15]])}."
    lineas = []
    for i, r in enumerate(rows[:15], start=1):
        extra = " · ".join(x for x in (_valor(r, k) for k in spec["extra"]) if x)
        lineas.append(f"{i}. **{etiqueta(r)}**" + (f" · {extra}" if extra else ""))
    mas = f"\n(mostrando 15 de {n})" if n > 15 else ""
    return f"**{n} {nombre}{adj}:**\n" + "\n".join(lineas) + mas


# ── Permisos explicados ─────────────────────────────────────────────────────

def explain_permissions(caps: dict, mods_user: List[Dict[str, Any]], grupo: Optional[str] = None) -> str:
    """Módulos del usuario agrupados, con lo que permite cada pantalla y lo que Nexo integra."""
    por_grupo: Dict[str, List[dict]] = {}
    for m in mods_user:
        g = m.get("grupo") or "General"
        if grupo and normalize(grupo) not in normalize(g) and normalize(grupo) not in normalize(m["nombre"]):
            continue
        por_grupo.setdefault(g, []).append(m)
    if not por_grupo:
        return f"No tienes módulos habilitados en «{grupo}»." if grupo else "Tu usuario no tiene módulos habilitados en School."
    total = sum(len(v) for v in por_grupo.values())
    consultas = set(caps.get("consultas") or [])
    if not grupo and total > 12:
        # Muchas pantallas: primero el panorama, y el detalle de un área si lo pide.
        nexo = sorted({m["nombre"] for ms in por_grupo.values() for m in ms if m.get("consulta") in consultas})
        from orchestrator.interceptors.schoolsena_ops import OPERATIONS as _OPS
        crear = [o["nombre"] + "s" for o in _OPS.values() if o["consulta"] in consultas]
        areas = ", ".join(f"{g} ({len(ms)})" for g, ms in sorted(por_grupo.items(), key=lambda x: -len(x[1]))[:8])
        return (f"Tienes acceso a **{total} pantallas** en {len(por_grupo)} áreas: {areas}.\n\n"
                + (f"**Desde aquí consulto:** {', '.join(nexo[:12])}{' y más' if len(nexo) > 12 else ''}.\n" if nexo else "")
                + (f"**Conmigo puedes crear:** {', '.join(crear)}.\n" if crear else "")
                + "Las demás las abres en su pantalla (te llevo si quieres).\n\n"
                  "Dime un área («¿qué puedo hacer en Gestión académica?») y te muestro el detalle. Las opciones de "
                  "cada pantalla (crear, editar…) son de la interfaz: School autoriza por módulo, no por operación.")
    lineas = []
    for g, ms in sorted(por_grupo.items(), key=lambda x: -len(x[1]))[:8]:
        items = []
        for m in ms[:6]:
            ops = "/".join(o for o in ("consultar", "crear", "editar", "eliminar") if o in m["operaciones_ui"])
            nexo = " — Nexo lo consulta" if m["consulta"] in (caps.get("consultas") or []) else ""
            items.append(f"  - {m['nombre']}" + (f" ({ops})" if ops else "") + nexo)
        mas = f"\n  - … y {len(ms) - 6} más" if len(ms) > 6 else ""
        lineas.append(f"**{g}**\n" + "\n".join(items) + mas)
    total = sum(len(v) for v in por_grupo.values())
    return (f"Tienes acceso a {total} pantalla{'s' if total != 1 else ''} de School"
            + (f" en «{grupo}»" if grupo else "") + ":\n\n" + "\n".join(lineas)
            + "\n\nEn School cada permiso abre un módulo; lo que puedes hacer dentro (consultar, crear, editar, "
              "eliminar) es lo que ofrece esa pantalla.")


# ── Ejecución ───────────────────────────────────────────────────────────────

def _reply(text: str, state: dict, outcome: str, oferta: Optional[dict] = None, **extra) -> Dict[str, Any]:
    """Respuesta de este nivel. Cada respuesta REEMPLAZA la propuesta anterior:
    la que traiga (`oferta`) es la única que el turno siguiente puede aceptar."""
    import time as _t
    state = {**state, "ts": _t.time(), "oferta": ({**oferta, "ts": _t.time()} if oferta else None)}
    fd = {"school_outcome": outcome, "_school": state, **extra}
    return {"reply": text, "final_data": fd}


def _nav(m: Dict[str, Any], state: dict, prefix: str = "") -> Dict[str, Any]:
    state = {**state, "module": m["ruta"], "query": m.get("consulta"), "cap": None}
    item = {"title": m["nombre"], "path": m["ruta"]}
    return _reply(f"{prefix}Te llevo a **{m['nombre']}**.", state, "nav", voice_action="navigate",
                  voice_action_payload={"url": m["ruta"], "title": m["nombre"], "verify": True}, nav_target=item)


async def run_query(query: str, context: dict, params: Optional[dict] = None):
    """(filas, error, error_kind) de una consulta registrada."""
    from tools.schoolsena import TOOLS_REGISTRY
    res = await TOOLS_REGISTRY[QUERIES[query]["tool"]]["handler"](params or {}, context)
    if res.get("error"):
        if res.get("error_kind") == "forbidden":
            # Un permiso revocado no puede seguir vivo en la caché de capacidades.
            (context.get("final_data") or {}).pop("_school_caps", None)
        return None, res["error"], res.get("error_kind") or "server"
    return rows_of(res.get("result")), None, None


def _error_text(err: str, kind: str) -> str:
    if kind == "forbidden":
        return f"🔒 {err[0].upper() + err[1:]}."
    if kind in ("unreachable", "timeout"):
        return f"⚠️ No pude consultar SchoolSena: {err}. No es que no haya datos; inténtalo de nuevo en un momento."
    return f"⚠️ No pude completar la consulta: {err}."


def _find_route(ruta: Optional[str], mods: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((m for m in mods if m["ruta"] == ruta), None) if ruta else None


def query_words(text: str) -> bool:
    """¿El mensaje pregunta por datos (qué/cuáles/dime/muéstrame) además de nombrar el módulo?"""
    return bool(re.search(r"\b(que|cuales|cual|dime|dame|muestrame|ense[nñ]ame|lista|listado|hay|tengo|tenemos|"
                          r"registrad[oa]s|existen|son|ver|como va|como van|estado de)\b", normalize(text)))


def _evoked(text: str, cap: str) -> bool:
    """¿Las palabras del mensaje nombran esa capacidad, o sólo la arrastra el contexto?"""
    from orchestrator.interceptors import schoolsena_nlu as nlu
    t = normalize(text)
    hits = nlu._concept_hits(nlu._content_words(t))
    if cap == "grades" and nlu._GRADE_PHRASE_RE.search(t):
        return True
    return hits.get(cap, 0) >= nlu._THRESHOLD * 0.5


def _pick(rows: List[dict], pos: int) -> Optional[dict]:
    idx = len(rows) - 1 if pos == -1 else pos - 1
    return rows[idx] if 0 <= idx < len(rows) else None


def _go(m, st, label):
    return [{"title": f"Ir a {m['nombre']}", "text": f"llévame a {m['nombre']}"}]


async def handle(user_text: str, reading, state: dict, caps: Optional[dict], context: dict) -> Optional[Dict[str, Any]]:
    """Resuelve la petición contra los módulos de School, o None si no es de este nivel."""
    t = normalize(user_text)
    act = read_act(user_text)
    mods = modules()
    mine = accessible(mods, caps) if caps else None
    pos = ordinal(user_text)
    filtro = bool(_FILTRO_ACTIVO.search(t))

    turno = int((context.get("final_data") or {}).get("_turn") or 0)

    # 00. Formularios y eventos: confirmaciones pendientes, constructor del
    #     formulario, referencias a registros ya creados y su vínculo.
    from orchestrator.interceptors import schoolsena_forms as forms
    hecho = await forms.handle(user_text, state, caps, context, turno)
    if hecho:
        return hecho

    # 0. ¿Responde a lo que Nexo acaba de proponer?
    oferta = ops.oferta_vigente(state, turno)
    if oferta and not state.get("op"):
        hecho = await _responder_oferta(user_text, oferta, state, caps, context, mods, mine)
        if hecho:
            return hecho

    # 0b. Operación en curso: el mensaje se lee primero como el dato pedido.
    if state.get("op"):
        hecho = await _op_continuar(user_text, state, caps, context, mods)
        if hecho:
            return hecho
        state = _op_pausar(state)  # cambio de tema: la operación queda como borrador

    # 0c. "Continuemos con el evento": retomar un borrador.
    retomar = ops.quiere_retomar(user_text)
    if retomar:
        hecho = _op_retomar(retomar, state)
        if hecho:
            return hecho

    # 1. Varias peticiones: "o" → se pregunta; "y (también)" → se atienden ambas.
    comp = split_compound(user_text)
    if comp and comp[0] == "alternativas":
        m = match_module(user_text, mine or mods) or _find_route(state.get("module"), mods)
        if m:
            segunda = comp[2]
            st = {**state, "module": m["ruta"], "query": m.get("consulta"), "cap": None}
            return _reply(f"¿Qué prefieres: que te lleve a **{m['nombre']}** o que te diga {segunda}?", st, "clarify",
                          quick_replies=[{"title": f"Ir a {m['nombre']}", "text": f"llévame a {m['nombre']}"},
                                         {"title": segunda.capitalize(), "text": f"{segunda} en {m['nombre']}"}],
                          needs_clarification=True,
                          oferta={"tipo": "elegir", "opciones": [
                              {"texto": f"llévame a {m['nombre']}"}, {"texto": f"{segunda} en {m['nombre']}"}]})
    if comp and comp[0] == "ambas" and not context.get("_compuesta"):
        return await _ambas(comp, reading, state, caps, context)

    # 1b. Resúmenes y pendientes.
    if act in ("resumen", "pendientes") and getattr(reading, "kind", "") != "capability":
        hecho = await _resumen(act, user_text, state, caps, context, mods, mine)
        if hecho:
            return hecho

    # 2. Permisos del usuario.
    if act and act.startswith("permisos"):
        return await _permissions(act, user_text, state, caps, mods, mine)

    # 3. ¿Sobre qué módulo?
    kind = getattr(reading, "kind", "")
    escritura = act in ("crear", "editar", "eliminar")
    if not escritura and (kind in ("greeting", "thanks", "help", "profile")
                          or (kind == "clarify" and str(getattr(reading, "reason", "")).startswith("missing"))):
        return None
    nlu_cap = reading.capability if kind == "capability" else None
    if (nlu_cap and nlu_cap not in NLU_CAP_QUERY and nlu_cap != "instructors" and not escritura
            and act != "navegar" and (_evoked(user_text, nlu_cap) or not getattr(reading, "from_context", False))):
        # Lo que el NLU ya responde con datos (horario, notas, nómina, una ficha…)
        # no se le quita: el módulo sólo entra si piden navegar o escribir.
        return None
    nombrado = match_module(user_text, mine) if mine else None
    nombrado_todos = match_module(user_text, mods)
    if nlu_cap in NLU_CAP_QUERY:
        # El NLU ya identificó el recurso ("ambientes de formación" son los
        # ambientes, no «Mis formaciones»): manda su módulo.
        q = NLU_CAP_QUERY[nlu_cap]
        propio = next((m for m in mods if m.get("consulta") == q), None)
        if propio:
            nombrado = nombrado_todos = {**propio, "_cubre": 1, "_cobertura": 1.0}
    elif nlu_cap == "instructors" and not (nombrado_todos and nombrado_todos.get("_cubre", 0) >= 2):
        # "Lista de instructores" la resuelve el NLU; "informes de instructores" es otro módulo.
        return None
    activo = _find_route(state.get("module"), mods)
    # Gana el módulo que explica MÁS del mensaje, aunque el usuario no tenga
    # acceso a él: así se le dice eso, en vez de mostrarle otro parecido.
    if nombrado and nombrado_todos and nombrado_todos.get("_cubre", 0) > nombrado.get("_cubre", 0):
        nombrado = nombrado_todos
    explicito = nombrado or nombrado_todos
    if explicito and not nlu_cap and not escritura and act != "navegar" and not strong(explicito, user_text):
        # Una palabra común ("servicio", "general", "proceso") no basta para
        # decidir que hablan de un módulo: sin señal fuerte, no es de este nivel.
        explicito = None
    if nlu_cap in NLU_CAP_QUERY and not explicito:
        return None
    seguimiento = (not explicito and activo is not None
                   and (act in ("contar", "leer", "navegar") or filtro or pos is not None))
    m = explicito or (activo if seguimiento else None)
    if not m:
        if pos is not None and state.get("rows") and state.get("perm_list"):
            fila = _pick(state["rows"], pos)
            mm = _find_route((fila or {}).get("ruta"), mods)
            if mm:
                return _nav(mm, state)
        return None
    propia = QUERIES.get(m.get("consulta") or "", {}).get("propia")
    if propia and propia in ((caps or {}).get("consultas") or []) and act not in ("crear", "editar", "eliminar", "navegar"):
        # Sin acceso al módulo pero con su versión propia (p. ej. los periodos de la matrícula).
        mine = (mine or []) + [m]
    empates = [e for e in (m.get("_empates") or []) if e["ruta"] != m["ruta"]]
    if explicito and empates and not seguimiento:
        opciones = [m] + empates[:3]
        nombres = [f"**{o['nombre']}**" + (f" ({o['grupo']})" if o.get("grupo") else "") for o in opciones]
        return _reply("¿A cuál te refieres: " + ", ".join(nombres[:-1]) + f" o {nombres[-1]}?", state, "clarify",
                      needs_clarification=True,
                      oferta={"tipo": "elegir", "opciones": [{"texto": f"llévame a {o['nombre']}", "ruta": o["ruta"],
                                                              "nombre": o["nombre"]} for o in opciones]})
    if mine is not None and m.get("permisos") and m["ruta"] not in {x["ruta"] for x in mine}:
        return _reply(f"🔒 Tu usuario no tiene acceso a **{m['nombre']}** (se abre con: {', '.join(m['permisos'])}). "
                      "Si lo necesitas, pídelo a quien administra los roles y permisos.", state, "denied",
                      access_denied=True)

    query = m.get("consulta")
    consultas = (caps or {}).get("consultas")
    puede_consultar = bool(query) and (consultas is None or query in consultas
                                       or QUERIES.get(query, {}).get("propia") in consultas)
    st = {**state, "module": m["ruta"], "query": query, "cap": None, "perm_list": False}

    # 4. Escrituras.
    if act == "crear":
        if m.get("crear") and (consultas is None or CREATES[m["crear"]]["consulta"] in consultas):
            return _op_iniciar(m["crear"], user_text, st)
        if "crear" in m["operaciones_ui"]:
            return _reply(f"En **{m['nombre']}** puedes crear registros desde la pantalla; desde aquí todavía no "
                          "lo hago por ti. ¿Te llevo?", st, "not_integrated", quick_replies=_go(m, st, ""),
                          oferta={"tipo": "navegar", "ruta": m["ruta"], "nombre": m["nombre"], "consulta": m.get("consulta")})
        return _reply(f"**{m['nombre']}** no ofrece la opción de crear registros.", st, "not_integrated")
    if act in ("editar", "eliminar"):
        verbo = "modificar" if act == "editar" else "eliminar"
        if act in m["operaciones_ui"]:
            return _reply(f"En **{m['nombre']}** puedes {verbo} registros desde la pantalla. Por seguridad, desde la "
                          f"conversación todavía no lo hago. ¿Te llevo?", st, "not_integrated",
                          quick_replies=_go(m, st, ""), oferta={"tipo": "navegar", "ruta": m["ruta"], "nombre": m["nombre"], "consulta": m.get("consulta")})
        return _reply(f"**{m['nombre']}** no ofrece la opción de {verbo} registros.", st, "not_integrated")

    # 5. Navegar (incluye "llévame a la segunda" y "llévame allá"); el nombre
    #    pelado del módulo también es la sección.
    # Nombre pelado del módulo (nada más que su nombre): es la sección. Si trae
    # algo más ("conceptos de la configuración de pagos"), pide sus datos.
    pelado = explicito and not (set(_content(user_text)) - set(_words(m.get("nombre") or "")) - set(m["claves"]))
    # "¿Y los formularios?" tras una consulta: lo mismo, sobre otro módulo (no es navegar).
    sigue_consulta = bool(re.match(r"y\s", normalize(user_text))) and bool(state.get("query") or state.get("rows"))
    if act == "navegar" or (not act and not filtro and pos is None and pelado and not query_words(user_text)
                            and not sigue_consulta):
        if pos is not None and state.get("rows"):
            fila = _pick(state["rows"], pos)
            if fila:
                return _nav(m, st, f"**{fila['nombre']}** está en {m['nombre']}. ")
        return _nav(m, st)

    # 6. Un elemento del último resultado: "¿cuál es la primera?".
    if pos is not None and state.get("rows") and not explicito:
        fila = _pick(state["rows"], pos)
        if not fila:
            return _reply(f"Sólo te mostré {len(state['rows'])}. ¿Cuál de ellos?", st, "clarify",
                          needs_clarification=True)
        st["selected"] = fila
        return _reply(f"La número {pos if pos > 0 else len(state['rows'])} es **{fila['nombre']}**"
                      + (f" ({fila['extra']})" if fila.get("extra") else "") + ".", st, "module_row")

    # 7. Consultar / contar / leer / filtrar.
    if not puede_consultar:
        return _reply(f"Tienes acceso a **{m['nombre']}**, pero todavía no consulto sus datos desde aquí. "
                      "¿Te llevo a la pantalla?", st, "not_integrated", quick_replies=_go(m, st, ""),
                      oferta={"tipo": "navegar", "ruta": m["ruta"], "nombre": m["nombre"], "consulta": m.get("consulta")})
    spec = QUERIES[query]
    params = {}
    if filtro and query == "eventos":
        params["estado"] = "PENDIENTE"
    if filtro and query == "formularios":
        params["estado"] = "publicado"
    filas, err, kind = await run_query(query, context, params)
    if err:
        return _reply(_error_text(err, kind), st, "error", error=err, error_kind=kind)
    if filtro and query not in ("eventos", "formularios"):
        if spec.get("estado") is None:
            art = "Las" if spec["g"] == "f" else "Los"
            act_w = "activas" if spec["g"] == "f" else "activos"
            return _reply(f"{art} {spec['varios']} no tienen un estado registrado en School, así que no puedo separar "
                          f"{'las' if spec['g'] == 'f' else 'los'} {act_w}. Hay {len(filas)} en total.", st,
                          f"module_query:{query}")
        filas = [r for r in filas if is_active(spec, r)]
    sel = state.get("selected")
    if spec.get("de") and sel and state.get("query") == "sedes":
        filas = [r for r in filas if normalize(str(r.get(spec["de"]) or "")) == normalize(sel["nombre"])]
    st["rows"] = [{"id": r.get("id"), "nombre": str(r.get("nombre") or r.get("titulo") or r.get("id")),
                   "extra": " · ".join(x for x in (_valor(r, k) for k in spec["extra"]) if x)} for r in filas[:20]]
    if not (spec.get("de") and sel):
        st.pop("selected", None)
    modo = "contar" if act == "contar" else "leer" if act == "leer" else "listar"
    texto = present(query, filas, modo, filtro)
    if spec.get("de") and sel and state.get("query") == "sedes":
        texto = f"En la sede **{sel['nombre']}**: " + (texto if filas else f"no hay {spec['varios']} registrados.")
    return _reply(texto, st, f"module_query:{query}")


# ── Permisos ────────────────────────────────────────────────────────────────

async def _permissions(act, user_text, state, caps, mods, mine) -> Dict[str, Any]:
    if caps is None:
        return _reply("No pude consultar tus permisos en SchoolSena en este momento. Inténtalo de nuevo en un momento.",
                      state, "error")
    t = normalize(user_text)
    if act in ("permisos_consultables", "permisos_pantalla", "permisos_crear", "permisos_no_modificar"):
        return _permisos_nivel(act, state, caps, mine or [])
    if act == "permisos_faltan":
        catalogo = caps.get("catalogo")
        if not catalogo:
            return _reply("No puedo mostrarte los permisos que no tienes: el catálogo completo de permisos sólo lo ve "
                          "quien administra roles y permisos en School.", state, "permissions")
        tengo = set(caps.get("permisos") or [])
        faltan = [c for c in catalogo if c["permiso"] not in tengo and c.get("ruta")]
        grupos: Dict[str, List[str]] = {}
        for c in faltan:
            grupos.setdefault(c.get("grupo") or "General", []).append(c["nombre"])
        texto = "\n".join(f"**{g}**: " + ", ".join(v[:6]) + (f" y {len(v) - 6} más" if len(v) > 6 else "")
                          for g, v in sorted(grupos.items()))
        return _reply(f"Hay {len(faltan)} pantallas a las que no tienes acceso:\n\n{texto}" if faltan else
                      "Tienes acceso a todas las pantallas con permiso registrado.", state, "permissions")
    m = match_module(user_text, mods)
    acceso = {x["ruta"] for x in (mine or [])}
    if act == "permisos_por_que" and m:
        if m["ruta"] in acceso:
            return _reply(f"Sí tienes acceso a **{m['nombre']}**. Si no te deja entrar, puede ser una sesión vieja: "
                          "cierra sesión y vuelve a entrar.", {**state, "module": m["ruta"]}, "permissions")
        return _reply(f"No puedes entrar a **{m['nombre']}** porque tu usuario no tiene ninguno de los permisos que la "
                      f"abren ({', '.join(m['permisos'])}). Se asignan en Roles y permisos, a tu rol o a tu usuario.",
                      state, "permissions")
    if act == "permisos_puedo" and m:
        op = next((o for o, rx in (("crear", r"crear|registrar|agregar"), ("editar", r"editar|modificar"),
                                   ("eliminar", r"eliminar|borrar")) if re.search(rx, t)), "consultar")
        if m["ruta"] not in acceso:
            return _reply(f"No: tu usuario no tiene acceso a **{m['nombre']}** (se abre con {', '.join(m['permisos'])}).",
                          state, "permissions")
        en_ui = op in m["operaciones_ui"] or op == "consultar"
        aqui = (op == "crear" and m.get("crear")) or (op == "consultar" and m.get("consulta"))
        txt = (f"Sí: tienes acceso a **{m['nombre']}**" + (f" y esa pantalla permite {op}." if en_ui else
               f", pero esa pantalla no ofrece la opción de {op}."))
        if en_ui:
            txt += " Puedo hacerlo contigo aquí mismo." if aqui else " Desde la conversación todavía no lo hago; te llevo si quieres."
        return _reply(txt, {**state, "module": m["ruta"], "query": m.get("consulta")}, "permissions")
    grupo = None
    g = re.search(r"\b(?:en|sobre)\s+(.+)$", t)
    if g:
        grupo = g.group(1).strip(" ?")
    filas = [{"id": None, "nombre": x["nombre"], "extra": x["ruta"], "ruta": x["ruta"],
              "ops": x["operaciones_ui"]} for x in (mine or [])
             if not grupo or normalize(grupo) in normalize((x.get("grupo") or "") + " " + x["nombre"])]
    texto = explain_permissions(caps, mine or [], grupo)
    if grupo and not filas and m and m["ruta"] in acceso:
        texto = (f"En **{m['nombre']}** la pantalla permite: " + ", ".join(m["operaciones_ui"] or ["consultar"]) + "."
                 + (" Nexo puede consultarlo." if m.get("consulta") else ""))
        filas = [{"id": None, "nombre": m["nombre"], "extra": m["ruta"], "ruta": m["ruta"], "ops": m["operaciones_ui"]}]
    return _reply(texto, {**state, "rows": filas[:40], "perm_list": True, "query": None}, "permissions")


def edit_followup(user_text: str, state: dict) -> Optional[Dict[str, Any]]:
    """"¿Cuáles de esos son de edición?" sobre la última lista de permisos."""
    t = normalize(user_text)
    if not state.get("perm_list") or not re.search(r"\b(edicion|editar|crear|creacion|eliminar|eliminacion)\b", t):
        return None
    # Tiene que hablar de ESA lista ("¿cuáles de esos…?"), no pedir algo nuevo
    # ("quiero crear un evento" es una creación, no un filtro de permisos).
    if not re.search(r"\b(esos|esas|estos|estas|ellos|ellas|cuales|cual de)\b", t) or read_act(user_text) == "crear" \
            and not re.search(r"\b(esos|esas|estos|estas|ellos|ellas)\b", t):
        return None
    op = "editar" if re.search(r"edic|editar", t) else "crear" if re.search(r"crea", t) else "eliminar"
    filas = [f for f in state.get("rows") or [] if op in (f.get("ops") or [])]
    if not filas:
        return _reply(f"Ninguno de esos módulos permite {op} desde su pantalla.", state, "permissions")
    return _reply(f"Permiten {op}: " + ", ".join(f["nombre"] for f in filas[:12])
                  + (f" y {len(filas) - 12} más" if len(filas) > 12 else "") + ".",
                  {**state, "rows": filas[:40]}, "permissions")


# ── Continuidad: propuestas y operaciones ──────────────────────────────────

from orchestrator.interceptors import schoolsena_ops as ops  # noqa: E402


async def _responder_oferta(user_text, oferta, state, caps, context, mods, mine):
    """Interpreta una respuesta breve contra la propuesta del turno anterior."""
    tipo = oferta.get("tipo")
    if tipo == "elegir":
        pos = ordinal(user_text)
        opciones = oferta.get("opciones") or []
        elegida = opciones[pos - 1] if pos and 0 < pos <= len(opciones) else None
        if not elegida:
            t = normalize(user_text)
            elegida = next((o for o in opciones if o.get("nombre") and normalize(o["nombre"]) in t), None)
        if elegida:
            nuevo = {**state, "oferta": None}
            if elegida.get("ruta"):
                mm = _find_route(elegida["ruta"], mods)
                if mm:
                    return _nav(mm, nuevo)
            return await handle(elegida["texto"], _lectura_vacia(), nuevo, caps, context)
        breve = ops.respuesta_breve(user_text)
        if breve == "niega":
            return _reply("Listo, lo dejamos ahí.", state, "offer_declined")
        if breve:
            return _reply("¿Cuál de las opciones? Dime el número o el nombre.", state, "clarify",
                          needs_clarification=True, oferta=oferta)
        return None
    breve = ops.respuesta_breve(user_text)
    if not breve:
        return None
    if breve == "niega":
        return _reply("Listo, no la abro. Si necesitas algo más, dime.", state, "offer_declined")
    m = _find_route(oferta.get("ruta"), mods)
    if not m:
        return None
    if tipo == "navegar":
        if breve == "muestra" and m.get("consulta"):
            return await handle(f"muéstrame {m['nombre']}", _lectura_vacia(), {**state, "oferta": None}, caps, context)
        return _nav(m, {**state, "oferta": None})
    return None


def _lectura_vacia():
    from orchestrator.interceptors.schoolsena_nlu import Reading
    return Reading(kind="none")


def _op_iniciar(recurso: str, user_text: str, state: dict):
    from orchestrator.interceptors import schoolsena_forms as forms
    if recurso == "formularios" and forms.quiere_generar(user_text):
        # "Necesito un formulario para inscribir aprendices a una capacitación": se propone una estructura.
        return forms.generar(user_text, state)
    op = ops.iniciar(recurso, user_text)
    st = {**state, "op": op}
    st.setdefault("borradores", {})
    return _op_avanzar(recurso, op, st)


def _op_avanzar(recurso: str, op: dict, st: dict):
    """Siguiente paso de la operación. Un formulario con título no se crea vacío: se prepara su
    borrador para armar las preguntas en la conversación (schoolsena_forms)."""
    if recurso == "formularios" and not ops._falta(ops.OPERATIONS[recurso], op["campos"]):
        from orchestrator.interceptors import schoolsena_forms as forms
        return forms.preparar(st, op["campos"])
    return _reply(ops.siguiente(recurso, op), {**st, "op": op}, "create_flow")


def _es_cambio_de_tema(user_text: str, mods, op: Optional[dict] = None) -> bool:
    """
    ¿El mensaje pide otra cosa, en vez de dar el dato que Nexo espera?

    Por defecto se lee como el dato ("NU EVENT" es un nombre). Cambia de tema
    cuando: trae una acción propia (navegar, consultar, contar, permisos…), crea
    OTRO recurso, encadena peticiones, empieza con un marcador de interrupción
    ("antes…", "espera…") y pregunta algo, pregunta por un módulo, o —si se
    esperaba una fecha u hora y no la trae— nombra un módulo.
    """
    t = normalize(user_text)
    act = read_act(user_text)
    m = match_module(user_text, mods)
    if act == "crear":
        return bool(op and m and m.get("crear") and m["crear"] != op.get("recurso"))
    if act:
        return True
    if split_compound(user_text) or ops.quiere_retomar(user_text):
        return True
    if re.match(r"(antes|primero|espera|esperate|un momento|oye|mientras|por cierto)\b", t) and query_words(user_text):
        return True
    if m and query_words(user_text):
        return True
    if op and (op.get("pregunta") or op.get("confirmar")):
        # Si no se esperaba un texto libre (sino una fecha, una hora o un sí/no) y
        # el mensaje nombra un módulo, es otro tema ("Planeación").
        tipo = next((c["tipo"] for c in ops.OPERATIONS[op["recurso"]]["campos"] if c["id"] == op.get("pregunta")),
                    "confirmacion")
        if tipo != "texto" and m and (strong(m, user_text) or m.get("_cubre", 0) >= 1 and len(_content(user_text)) <= 2):
            return True
    return False


async def _op_continuar(user_text, state, caps, context, mods):
    op = dict(state["op"])
    recurso = op["recurso"]
    spec = ops.OPERATIONS[recurso]
    if ops.cancela(user_text):
        st = {k: v for k, v in state.items() if k != "op"}
        st["borradores"] = {k: v for k, v in (state.get("borradores") or {}).items() if k != recurso}
        return _reply(f"Listo, no creo {spec['art']} {spec['nombre']}.", st, "create_cancelled")
    if re.fullmatch(r"(despues|luego|mas tarde|ahora no)(\s+\w+)?", normalize(user_text).strip(" .!")):
        st = _op_pausar(state)
        return _reply(f"Dejo {spec['art']} {spec['nombre']} guardado como borrador. Cuando quieras, dime "
                      f"«continuemos con {spec['art']} {spec['nombre']}».", st, "create_paused")
    if op.get("confirmar"):
        if ops.afirma_firme(user_text):
            return await _op_ejecutar(state, context)
        cambios = ops._extraer(spec, user_text, op["campos"], None)
        if cambios != op["campos"]:
            op["campos"] = cambios
            return _reply(ops.siguiente(recurso, op), {**state, "op": op}, "create_flow")
        if ops.respuesta_breve(user_text) == "afirma":
            return _reply(f"¿Entonces lo creo? Respóndeme *sí* o *no*.", state, "create_flow")
        return None if _es_cambio_de_tema(user_text, mods, op) else _reply(
            "No cambié nada. ¿Lo creo? Respóndeme *sí* o *no*, o dime qué dato corregir.", state, "create_flow")
    # Esperando un dato: se lee primero como ese dato. Si el mensaje trae datos de
    # la operación (una fecha, una hora…), es respuesta aunque alguna palabra se
    # parezca a un módulo ("virtual"); si no trae nada, puede ser otro tema.
    nuevos = ops._extraer(spec, user_text, op["campos"], op.get("pregunta"))
    trae_datos = ({k: v for k, v in nuevos.items() if not k.startswith("_")}
                  != {k: v for k, v in op["campos"].items() if not k.startswith("_")})
    tipo = next((c["tipo"] for c in spec["campos"] if c["id"] == op.get("pregunta")), "texto")
    if (not trae_datos or tipo == "texto") and _es_cambio_de_tema(user_text, mods, op):
        return None
    if nuevos == op["campos"]:
        return _reply("No entendí ese dato. " + ops.siguiente(recurso, op), {**state, "op": op}, "create_flow")
    op["campos"] = nuevos
    return _op_avanzar(recurso, op, {**state, "op": op})


def _op_pausar(state: dict) -> dict:
    import time as _t
    op = state.get("op")
    st = {k: v for k, v in state.items() if k != "op"}
    if op:
        st["borradores"] = {**(state.get("borradores") or {}), op["recurso"]: {**op, "ts": _t.time()}}
    return st


def _op_retomar(recurso: str, state: dict):
    import time as _t
    borradores = {k: v for k, v in (state.get("borradores") or {}).items()
                  if _t.time() - float(v.get("ts") or 0) < ops.BORRADOR_TTL_S}
    if recurso == "*":
        recurso = next(iter(borradores), None)
        if not recurso:
            return _reply("No tengo ninguna operación a medias contigo. ¿Qué quieres hacer?", state, "clarify",
                          needs_clarification=True)
    if not recurso or recurso not in borradores:
        if recurso in ops.OPERATIONS:
            spec = ops.OPERATIONS[recurso]
            return _reply(f"No tengo {spec['art']} {spec['nombre']} a medias. ¿Quieres crear uno nuevo?", state,
                          "clarify", oferta={"tipo": "elegir", "opciones": [{"texto": f"quiero crear {spec['nombre']}"}]})
        return None
    op = {k: v for k, v in borradores[recurso].items() if k != "ts"}
    st = {**state, "op": op, "borradores": {k: v for k, v in borradores.items() if k != recurso}}
    if recurso == "formularios" and not ops._falta(ops.OPERATIONS[recurso], op["campos"]):
        return _op_avanzar(recurso, op, st)
    spec = ops.OPERATIONS[recurso]
    hecho = ops.resumen_op(spec, op["campos"])
    return _reply((f"Sigamos con {spec['art']} {spec['nombre']} ({hecho}). " if hecho else "") + ops.siguiente(recurso, op),
                  st, "create_flow")


async def _op_ejecutar(state: dict, context: dict):
    from tools.schoolsena import TOOLS_REGISTRY
    op = state["op"]
    recurso = op["recurso"]
    spec = ops.OPERATIONS[recurso]
    campos = {k: v for k, v in op["campos"].items() if v and not k.startswith("_")}
    st = {k: v for k, v in state.items() if k != "op"}
    # El backend vuelve a comprobar el permiso: si se revocó durante la conversación, se rechaza aquí.
    res = await TOOLS_REGISTRY[spec["tool"]]["handler"](campos, context)
    if res.get("error"):
        if res.get("error_kind") == "validation":
            st["op"] = {**op, "confirmar": False}
            return _reply(f"School no aceptó los datos: {res['error']}. ¿Lo corregimos?", st, "create_rejected")
        if res.get("error_kind") == "forbidden":
            (context.get("final_data") or {}).pop("_school_caps", None)
        return _reply(_error_text(res["error"], res.get("error_kind") or "server")
                      + f" No se creó {spec['art']} {spec['nombre']}.", st, "create_failed")
    clave, ids = spec["id"]
    obj = ((res.get("result") or {}).get(clave) if clave else res.get("result")) or {}
    nuevo_id = next((obj.get(i) for i in ids if obj.get(i)), None)
    filas, _, _ = await run_query(spec["verificar"], context)
    visto = nuevo_id and any(str(r.get("id")) == str(nuevo_id) for r in (filas or []))
    st.update({"module": spec["ruta"], "query": spec["verificar"]})
    if not visto:
        return _reply(f"Envié {spec['art']} {spec['nombre']} pero todavía no lo veo en la lista; revísalo en su "
                      "pantalla antes de crearlo otra vez.", st, "create_unknown")
    from orchestrator.interceptors import schoolsena_forms as forms
    # Queda en la memoria de la conversación con su id real ("el evento que creamos más arriba").
    st = forms.recordar(st, spec["nombre"], nuevo_id, campos.get("nombre") or campos.get("titulo"), "creado")
    return _reply(f"Listo: creé {spec['art']} {spec['nombre']} {ops.resumen_op(spec, campos)} (número {nuevo_id}). "
                  + spec["hecho"], st, "create_done", created_id=nuevo_id)


async def _ambas(comp, reading, state, caps, context):
    """'Crea un formulario y también dime cuáles tengo activos': se atienden las dos.
    Primero la consulta (no cambia nada) y después la operación, que queda en curso."""
    _, a, b = comp
    orden = sorted([a, b], key=lambda x: 1 if read_act(x) in ("crear", "editar", "eliminar", "navegar") else 0)
    ctx = {**context, "_compuesta": True}
    # Lo que una parte no nombra ("cuáles tengo activos") es de la OTRA parte
    # ("un nuevo formulario"), no del módulo que hubiera antes.
    mods_ = modules()
    nombrados = [match_module(x, mods_) for x in orden]
    if not nombrados[0] and nombrados[1]:
        state = {**state, "module": nombrados[1]["ruta"], "query": nombrados[1].get("consulta")}
    r1 = await handle(orden[0], _lectura_vacia(), state, caps, ctx)
    st1 = (r1 or {}).get("final_data", {}).get("_school", state)
    # Lo que la segunda parte no nombra lo hereda de la primera ("cuáles tengo activos").
    r2 = await handle(orden[1], _lectura_vacia(), st1, caps, ctx)
    partes = [r["reply"] for r in (r1, r2) if r]
    if not partes:
        return None
    final = r2 or r1
    extra = {k: v for k, v in final["final_data"].items() if k not in ("_school", "school_outcome")}
    return _reply("\n\n".join(partes), final["final_data"]["_school"], "compound",
                  oferta=final["final_data"]["_school"].get("oferta"), **extra)


# ── Resúmenes ───────────────────────────────────────────────────────────────

#: Fuentes de un resumen general, en orden de interés (todas con permiso propio).
_RESUMEN_GENERAL = ["pagos_pendientes", "solicitudes_inscripcion", "eventos", "formularios", "informes_instructores",
                    "carpetas", "multimedia", "periodos"]


async def _resumen(act, user_text, state, caps, context, mods, mine):
    import asyncio
    from core.semantic.fechas import hoy_local
    consultas = set((caps or {}).get("consultas") or [])
    m = match_module(user_text, mods)
    if m and m.get("consulta") and strong(m, user_text) and act == "resumen":
        fuentes = [m["consulta"]]
    else:
        fuentes = [q for q in _RESUMEN_GENERAL if q in consultas]
        if act == "pendientes":
            fuentes = [q for q in ("pagos_pendientes", "solicitudes_inscripcion", "eventos", "formularios") if q in consultas]
    if not fuentes:
        return None
    mes = bool(re.search(r"\beste mes\b|\bdel mes\b", normalize(user_text)))
    hoy = hoy_local()
    resultados = await asyncio.gather(*(run_query(q, context, {"estado": "PENDIENTE"} if act == "pendientes" and q == "eventos" else None)
                                        for q in fuentes[:6]))
    lineas = []
    for q, (filas, err, kind) in zip(fuentes, resultados):
        spec = QUERIES[q]
        if err:
            lineas.append(f"- **{spec['varios'].capitalize()}**: no pude consultarlo ({err}).")
            continue
        if q == "eventos" and mes:
            filas = [r for r in filas if str(r.get("fechaInicial") or "")[:7] == hoy.isoformat()[:7]]
        if act == "pendientes" and q == "formularios":
            filas = [r for r in filas if str(r.get("estado")) == "borrador"]
        n = len(filas)
        activos = sum(1 for r in filas if is_active(spec, r)) if spec.get("estado") else None
        detalle = f" ({activos} activ{'as' if spec['g'] == 'f' else 'os'})" if activos is not None and act == "resumen" else ""
        if act == "pendientes" and q == "formularios":
            nombre = "formularios en borrador"
        elif act == "pendientes" and q == "eventos":
            nombre = "eventos por realizar"
        else:
            nombre = spec["varios"] + (" de este mes" if q == "eventos" and mes else "")
        lineas.append(f"- **{nombre.capitalize()}**: {n}{detalle}")
    titulo = ("Lo que tienes pendiente en lo que puedo consultar:" if act == "pendientes" else
              "Resumen de lo que puedo consultar con tus permisos:")
    nota = "" if len(fuentes) == 1 else "\n\nDime cuál quieres ver en detalle."
    st = {**state, "cap": None, "perm_list": False}
    if len(fuentes) == 1:
        st.update({"module": QUERIES[fuentes[0]]["ruta"], "query": fuentes[0]})
    return _reply(titulo + "\n" + "\n".join(lineas) + nota, st, "summary")


def _permisos_nivel(act, state, caps, mine):
    consultas = set(caps.get("consultas") or [])
    nexo = [m for m in mine if m.get("consulta") and (m["consulta"] in consultas
                                                       or QUERIES.get(m["consulta"], {}).get("propia") in consultas)]
    if act == "permisos_consultables":
        nombres = sorted({m["nombre"] for m in nexo})
        return _reply("Desde aquí puedo consultar: " + ", ".join(nombres) + "." if nombres else
                      "Con tus permisos todavía no hay módulos que consulte desde aquí.", state, "permissions")
    if act == "permisos_pantalla":
        resto = [m for m in mine if m not in nexo]
        grupos: Dict[str, int] = {}
        for m in resto:
            grupos[m.get("grupo") or "General"] = grupos.get(m.get("grupo") or "General", 0) + 1
        top = ", ".join(f"{g} ({n})" for g, n in sorted(grupos.items(), key=lambda x: -x[1])[:6])
        return _reply(f"{len(resto)} de tus pantallas las abres tú (te llevo si quieres): {top}.", state, "permissions")
    if act == "permisos_crear":
        aqui = [o["nombre"] + "s" for k, o in ops.OPERATIONS.items() if o["consulta"] in consultas]
        en_ui = sum(1 for m in mine if "crear" in m["operaciones_ui"])
        txt = (f"Conmigo, aquí mismo, puedes crear: {', '.join(aqui)}. " if aqui else "")
        return _reply(txt + f"Además, {en_ui} de tus pantallas tienen opción de crear (la creación se hace en ellas).",
                      state, "permissions")
    sin_editar = sorted({m["nombre"] for m in mine if "editar" not in m["operaciones_ui"]})
    return _reply(f"Estas pantallas a las que tienes acceso no ofrecen editar: {', '.join(sin_editar[:15])}"
                  + (f" y {len(sin_editar) - 15} más" if len(sin_editar) > 15 else "") + ".", state, "permissions")
