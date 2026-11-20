"""
orchestrator/interceptors/schoolsena_formdraft.py — Borrador estructurado de un formulario de School.

El constructor conversacional NO es una implementación paralela: el borrador usa
el mismo contrato que el constructor del front (`formBuilderTypes.ts`): tipos del
enum `formulario_preguntas.tipo`, opciones en los tipos de selección, "Número"
como respuesta corta con `configuracion.soloNumeros`, escala con min/max.

Cada instrucción del usuario se traduce a OPERACIONES sobre el borrador
(agregar, eliminar, mover, cambiar tipo, obligatoriedad, opciones, renombrar,
duplicar, deshacer…). Los ordinales ("la segunda", "la última") siempre se
resuelven contra la versión VIGENTE del borrador. Antes de cada cambio se guarda
una instantánea, así que "deshaz el último cambio" y "volvamos a como estaba"
funcionan sin tocar School. Nada de esto se guarda en School hasta "guárdalo".

Este archivo es puro (sin red): interpreta y aplica. La conversación, el guardado
y el vínculo con eventos viven en `schoolsena_forms.py`.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from core.semantic.morphology import normalize

TIPOS = {"texto_corto": "respuesta corta", "texto_largo": "párrafo", "opcion_multiple": "opción única",
         "casillas": "casillas (varias respuestas)", "desplegable": "lista desplegable", "escala_lineal": "escala",
         "fecha": "fecha", "hora": "hora", "archivo": "subir archivo"}
CON_OPCIONES = {"opcion_multiple", "casillas", "desplegable"}
HIST_MAX = 25

# Opciones que School ya usa en su propia plantilla de inscripción (FormBuilderPage, "registro").
DOCUMENTOS = ["Cédula de Ciudadanía (CC)", "Tarjeta de Identidad (TI)", "Registro Civil (RC)",
              "Cédula de Extranjería (CE)", "Permiso Especial de Permanencia (PEP)"]
ESCALA = {"min": 1, "max": 5, "minLabel": "Muy insatisfecho", "maxLabel": "Muy satisfecho"}

#: Campos frecuentes: (clave, regex sobre texto plano, título, tipo, configuración, opciones, ¿dato personal?)
CAMPOS: List[Tuple[str, str, str, str, Optional[dict], Optional[list], bool]] = [
    ("tipo_doc", r"tipo de (documento|identificacion|doc)", "Tipo de documento", "desplegable", None, DOCUMENTOS, False),
    ("documento", r"(numero de )?(documento|cedula|identificacion|\bdoc\b|\bcc\b)", "Número de documento", "texto_corto",
     None, None, False),
    ("apellidos", r"\bapellidos?\b(?! y nombre)", "Apellidos", "texto_corto", None, None, False),
    ("nombre", r"\bnombres?\b", "Nombre completo", "texto_corto", None, None, False),
    ("correo", r"\b(correo|e ?mail|email|mail)\b", "Correo electrónico", "texto_corto", None, None, False),
    ("telefono", r"\b(telefono|celular|movil|whatsapp|numero de contacto|contacto telefonico)\b", "Teléfono",
     "texto_corto", {"soloNumeros": True}, None, True),
    ("nacimiento", r"\b(fecha de nacimiento|nacimiento)\b", "Fecha de nacimiento", "fecha", None, None, True),
    ("edad", r"\bedad\b", "Edad", "texto_corto", {"soloNumeros": True}, None, True),
    ("direccion", r"\b(direccion|domicilio|barrio)\b", "Dirección", "texto_corto", None, None, True),
    ("genero", r"\b(genero|sexo)\b", "Género", "desplegable", None, None, True),
    ("ficha", r"\b(numero de )?ficha\b", "Número de ficha", "texto_corto", {"soloNumeros": True}, None, False),
    ("programa", r"\bprograma\b", "Programa de formación", "texto_corto", None, None, False),
    ("jornada", r"\bjornada\b", "Jornada", "texto_corto", None, None, False),
    ("cargo", r"\b(cargo|ocupacion|profesion)\b", "Cargo u ocupación", "texto_corto", None, None, False),
    ("empresa", r"\b(empresa|institucion|entidad|organizacion)\b", "Empresa o institución", "texto_corto", None, None, False),
    ("observaciones", r"\b(observacion|observaciones|comentario|comentarios|sugerencia|sugerencias)\b", "Observaciones",
     "texto_largo", None, None, False),
    ("archivo", r"\b(certificado|adjunt\w*|archivo|soporte|subir)\b", "Archivo adjunto", "archivo", None, None, False),
    ("calificacion", r"\b(califica\w*|satisfaccion|puntua\w*|del 1 al 5|escala)\b", "Calificación general",
     "escala_lineal", ESCALA, None, False),
    ("fecha", r"\bfecha\b", "Fecha", "fecha", None, None, False),
    ("hora", r"\bhora\b", "Hora", "hora", None, None, False),
]

#: Frases que nombran un tipo del constructor ("que sea una lista desplegable").
TIPO_FRASES = [
    (r"lista desplegable|desplegable|menu desplegable|lista de opciones|dropdown", "desplegable"),
    (r"casillas|seleccion multiple|varias opciones|varias respuestas|multiples respuestas|checkbox", "casillas"),
    (r"opcion multiple|seleccion unica|una sola opcion|unica respuesta|una opcion", "opcion_multiple"),
    (r"\bsi o no\b|\bsi\/no\b", "sino"),
    (r"\bseleccion\b|\bopciones\b", "seleccion"),
    (r"texto largo|parrafo|respuesta larga|abierta larga", "texto_largo"),
    (r"texto corto|respuesta corta|respuesta abierta|\btexto\b|abierta", "texto_corto"),
    (r"\bnumero\b|numerica|solo numeros", "numero"),
    (r"\bfecha\b", "fecha"), (r"\bhora\b", "hora"), (r"\bescala\b|calificacion|del 1 al", "escala_lineal"),
    (r"\barchivo\b|adjunto|subir", "archivo"),
]

_ORD = {"primer": 1, "primera": 1, "primero": 1, "segunda": 2, "segundo": 2, "tercera": 3, "tercero": 3, "tercer": 3,
        "cuarta": 4, "cuarto": 4, "quinta": 5, "quinto": 5, "sexta": 6, "sexto": 6, "septima": 7, "septimo": 7,
        "octava": 8, "octavo": 8, "novena": 9, "noveno": 9, "decima": 10, "decimo": 10,
        "penultima": -2, "penultimo": -2, "ultima": -1, "ultimo": -1}
_NUM = {"una": 1, "un": 1, "otra": 1, "otro": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
        "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}


def plano(texto: str) -> str:
    return normalize(texto or "").replace("ñ", "n").strip()


def _cap(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip(" .,;:"))
    return s[:1].upper() + s[1:] if s else s


# ── Borrador ────────────────────────────────────────────────────────────────

def nuevo(titulo: Optional[str] = None, descripcion: Optional[str] = None, preguntas=None) -> dict:
    return {"id": None, "titulo": titulo, "descripcion": descripcion, "estado": "borrador", "slug": None,
            "version": None, "preguntas": preguntas or [], "base": None, "hist": [], "foco": None, "espera": None,
            "respuestas": 0, "eventos": []}


def desde_estructura(e: dict) -> dict:
    """Borrador a partir de lo que School tiene guardado (GET/PUT lyra/formularios/{id})."""
    pregs = [{"id": p["id"], "tipo": p["tipo"], "titulo": p["titulo"], "descripcion": p.get("descripcion"),
              "esObligatoria": bool(p.get("esObligatoria")), "configuracion": p.get("configuracion") or None,
              "opciones": [{"id": o["id"], "texto": o["texto"]} for o in p.get("opciones") or []]}
             for p in e.get("preguntas") or []]
    fe = nuevo(e.get("titulo"), e.get("descripcion"), pregs)
    fe.update({"id": e["id"], "estado": e.get("estado"), "slug": e.get("slug"), "version": e.get("version"),
               "respuestas": e.get("respuestas") or 0, "eventos": e.get("eventos") or []})
    fe["base"] = foto(fe)
    return fe


def foto(fe: dict) -> dict:
    return copy.deepcopy({"titulo": fe.get("titulo"), "descripcion": fe.get("descripcion"),
                          "preguntas": fe.get("preguntas") or []})


def sucio(fe: dict) -> bool:
    return fe.get("base") is None or _comparable(foto(fe)) != _comparable(fe["base"])


def _comparable(f: dict):
    return (f.get("titulo"), f.get("descripcion") or None,
            [(p["tipo"], p["titulo"], bool(p.get("esObligatoria")), p.get("configuracion") or None,
              [o["texto"] for o in p.get("opciones") or []]) for p in f.get("preguntas") or []])


def payload(fe: dict) -> List[dict]:
    """Preguntas en el contrato del backend (ids reales para conservar respuestas y opciones)."""
    out = []
    for i, p in enumerate(fe["preguntas"], 1):
        q = {"tipo": p["tipo"], "titulo": p["titulo"], "descripcion": p.get("descripcion"),
             "esObligatoria": bool(p.get("esObligatoria")), "orden": i, "configuracion": p.get("configuracion"),
             "opciones": [({"id": o["id"]} if o.get("id") else {}) | {"texto": o["texto"], "orden": j}
                          for j, o in enumerate(p.get("opciones") or [], 1)]}
        if p.get("id"):
            q["id"] = p["id"]
        out.append(q)
    return out


def sin_opciones(fe: dict) -> Optional[int]:
    """Índice de la primera pregunta de selección sin opciones (no se puede guardar así)."""
    return next((i for i, p in enumerate(fe["preguntas"]) if p["tipo"] in CON_OPCIONES and not p.get("opciones")), None)


# ── Campos ──────────────────────────────────────────────────────────────────

def clave_de(texto: str) -> Optional[str]:
    t = plano(texto)
    for clave, rx, *_ in CAMPOS:
        if re.search(rx, t):
            return clave
    return None


def _campo(clave: str):
    return next(c for c in CAMPOS if c[0] == clave)


def tipo_de_frase(texto: str) -> Optional[str]:
    t = plano(texto)
    for rx, tipo in TIPO_FRASES:
        if re.search(rx, t):
            return tipo
    return None


def aplicar_tipo(p: dict, tipo: str, opciones: Optional[List[str]] = None) -> dict:
    """Cambia el tipo como lo hace el constructor (QuestionCard.handleTypeChange)."""
    p = copy.deepcopy(p)
    config = p.get("configuracion") or {}
    propias = {k: v for k, v in config.items() if k in ("valorUnico", "campoEscarapela")} if p["tipo"] == "texto_corto" else {}
    if tipo == "sino":
        tipo, opciones = "opcion_multiple", opciones or ["Sí", "No"]
    if tipo == "seleccion":
        n = len(opciones or p.get("opciones") or [])
        tipo = "desplegable" if n == 0 or n > 4 else "opcion_multiple"
    if tipo == "numero":
        p.update(tipo="texto_corto", configuracion={"soloNumeros": True, **propias})
    elif tipo == "texto_corto":
        p.update(tipo="texto_corto", configuracion=propias or None)
    else:
        p.update(tipo=tipo, configuracion=ESCALA.copy() if tipo == "escala_lineal" else None)
    if tipo in CON_OPCIONES:
        if opciones:
            p["opciones"] = [{"texto": o} for o in opciones]
        elif not p.get("opciones"):
            clave = clave_de(p["titulo"])
            pred = _campo(clave)[5] if clave else None
            p["opciones"] = [{"texto": o} for o in pred] if pred else []
    return p


def pregunta(texto: str) -> dict:
    """Una pregunta a partir de lo que dijo el usuario ("correo", "¿En qué jornada estudias?",
    "una pregunta de sí o no sobre si asistirá", "programa como lista desplegable con opciones A, B")."""
    original = texto.strip(" .,;:")
    opciones = None
    m = re.search(r"\b(?:con\s+(?:las\s+)?opciones?|opciones?\s*:)\s*(.+)$", original, re.I)
    if m:
        opciones = lista(m.group(1))
        original = original[:m.start()].strip(" .,;:")
    tipo = None
    m = re.search(r"\b(?:como|de tipo|tipo|en)\s+(?:una?\s+)?(lista desplegable|desplegable|casillas|seleccion multiple|"
                  r"selección múltiple|opcion multiple|opción múltiple|parrafo|párrafo|texto largo|texto corto|"
                  r"respuesta corta|fecha|hora|escala|archivo|numero|número)\s*$", original, re.I)
    if m:
        tipo = tipo_de_frase(m.group(1))
        original = original[:m.start()].strip(" .,;:")
    m = re.match(r"(?:(?:una\s+)?pregunta\s+de\s+)?(si o no|sí o no|opcion multiple|opción múltiple|seleccion|selección|"
                 r"escala)\s+(?:sobre|de|acerca de|para)\s*(.*)$", original, re.I)
    if m and m.group(2):
        tipo = tipo or tipo_de_frase(m.group(1))
        original = m.group(2)
        if tipo == "sino":
            # "sobre si necesita certificado" → "¿Necesita certificado?"
            original = "¿" + _cap(re.sub(r"^si\s+", "", original, flags=re.I)) + "?"
    # "una pregunta sobre el programa de formación" → "programa de formación"
    original = re.sub(r"^(?:y\s+)?(?:(?:una|otra|la)\s+)?(?:pregunta|campo|casilla)?\s*(?:sobre|de|acerca de|para|que diga|"
                      r"que pregunte|que pida|por|con)\s+(?:el|la|los|las|su|sus)?\s*", "", original, flags=re.I) or original
    original = re.sub(r"^(?:el|la|los|las|su|sus|un|una)\s+", "", original, flags=re.I).strip(" .,;:")
    es_pregunta = original.startswith("¿") or original.endswith("?")
    clave = clave_de(original)
    if clave and not es_pregunta and len(plano(original).split()) <= 4:
        _, _, titulo, t, conf, ops_, _ = _campo(clave)
        p = {"tipo": t, "titulo": titulo, "esObligatoria": False, "configuracion": copy.deepcopy(conf),
             "opciones": [{"texto": o} for o in ops_ or []]}
    else:
        t = _campo(clave)[3] if clave and clave in ("fecha", "hora", "nacimiento", "archivo", "observaciones",
                                                     "calificacion") else "texto_corto"
        p = {"tipo": t, "titulo": _cap(original), "esObligatoria": False,
             "configuracion": ESCALA.copy() if t == "escala_lineal" else None, "opciones": []}
    if tipo or opciones:
        p = aplicar_tipo(p, tipo or "seleccion", opciones)
    return p


def lista(texto: str) -> List[str]:
    """'A, B y C' → [A, B, C] (también con 'o', punto y coma o saltos de línea)."""
    partes = re.split(r"\s*(?:,|;|\n|\s+y\s+|\s+e\s+(?=[a-záéíóú])|\s+o\s+)\s*", texto.strip(" .:"))
    return [_cap(p) for p in partes if p and p.strip(" .")]


# ── Referencias a preguntas del borrador ────────────────────────────────────

def _indice(fe: dict, n: int) -> Optional[int]:
    total = len(fe["preguntas"])
    i = total + n if n < 0 else n - 1
    return i if 0 <= i < total else None


def ordinales(t: str) -> List[int]:
    """Números de pregunta mencionados ("la segunda y la tercera", "la pregunta 4", "la última")."""
    out = []
    for m in re.finditer(r"\b(?:pregunta|campo|numero|la|el)\s+(\d{1,2})\b|\b(" + "|".join(_ORD) + r")\b", t):
        out.append(int(m.group(1)) if m.group(1) else _ORD[m.group(2)])
    return out


def por_contenido(fe: dict, frase: str) -> List[int]:
    """Preguntas que la frase nombra por su contenido ("el teléfono", "la del documento")."""
    t = plano(frase)
    clave = clave_de(t)
    if clave:
        hits = [i for i, p in enumerate(fe["preguntas"]) if clave_de(p["titulo"]) == clave]
        if hits:
            return hits
    stop = {"la", "el", "de", "del", "pregunta", "preguntas", "campo", "los", "las", "que", "y", "a", "en", "por", "mejor",
            "sea", "sean", "esta", "este", "esa", "ese", "aparezca", "quede", "primero", "primera", "al", "final", "con"}
    palabras = [w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in stop]
    if not palabras:
        return []
    puntaje = []
    for i, p in enumerate(fe["preguntas"]):
        tp = plano(p["titulo"])
        s = sum(1 for w in palabras if re.search(rf"\b{w[:5]}", tp))
        if s:
            puntaje.append((s, i))
    if not puntaje:
        return []
    mejor = max(s for s, _ in puntaje)
    return [i for s, i in puntaje if s == mejor]


def objetivos(fe: dict, texto: str, defecto_foco: bool = True) -> Tuple[List[int], Optional[str]]:
    """(índices, error) de las preguntas a las que se refiere el mensaje."""
    t = plano(texto)
    if not fe["preguntas"]:
        return [], "el formulario todavía no tiene preguntas"
    if re.search(r"\b(todas|todos)\b", t):
        return list(range(len(fe["preguntas"]))), None
    ords = ordinales(t)
    if ords:
        idx = [_indice(fe, n) for n in ords]
        if any(i is None for i in idx):
            return [], f"el formulario tiene {len(fe['preguntas'])} preguntas"
        return idx, None
    hits = por_contenido(fe, texto)
    if len(hits) == 1:
        return hits, None
    if len(hits) > 1:
        return [], "ambigua:" + ",".join(str(i) for i in hits)
    if defecto_foco and fe.get("foco") is not None and fe["foco"] < len(fe["preguntas"]):
        return [fe["foco"]], None
    return [], "no sé a qué pregunta te refieres"


# ── Plantillas (generación asistida, local) ─────────────────────────────────

def _p(clave: str, obligatoria: bool = True, titulo: Optional[str] = None) -> dict:
    _, _, t0, tipo, conf, ops_, _ = _campo(clave)
    return {"tipo": tipo, "titulo": titulo or t0, "esObligatoria": obligatoria, "configuracion": copy.deepcopy(conf),
            "opciones": [{"texto": o} for o in ops_ or []]}


def plantilla(tipo: str, objeto: str, aprendices: bool) -> Tuple[str, List[dict]]:
    """Estructura razonable para el propósito. Sin datos sensibles: el teléfono va opcional y no se
    piden fecha de nacimiento, dirección, género ni datos de salud."""
    obj = objeto or "el evento"
    if tipo == "asistencia":
        pregs = [_p("nombre"), _p("documento")] + ([_p("ficha")] if aprendices else [])
        return f"Asistencia a {obj}", pregs
    if tipo == "satisfaccion":
        return f"Encuesta de satisfacción: {obj}", [
            {**_p("calificacion"), "titulo": f"¿Qué tan satisfecho quedaste con {obj}?"},
            {"tipo": "opcion_multiple", "titulo": f"¿Recomendarías {obj}?", "esObligatoria": True,
             "configuracion": None, "opciones": [{"texto": "Sí"}, {"texto": "No"}]},
            {**_p("observaciones", False), "titulo": "¿Qué podríamos mejorar?"}]
    if tipo == "evaluacion":
        return f"Evaluación de {obj}", [
            {**_p("calificacion"), "titulo": "¿Cómo calificas el contenido?"},
            {**_p("calificacion"), "titulo": "¿Cómo calificas a quien dirigió la actividad?"},
            {**_p("observaciones", False), "titulo": "Comentarios"}]
    pregs = [_p("nombre"), _p("tipo_doc"), _p("documento"), _p("correo")]
    if aprendices:
        pregs += [_p("ficha"), _p("programa")]
    pregs.append(_p("telefono", False))
    return f"Inscripción a {obj}", pregs


def proposito(texto: str) -> Optional[Tuple[str, str, bool]]:
    """(tipo, objeto, ¿aprendices?) si el mensaje describe PARA QUÉ es el formulario."""
    t = plano(texto)
    tipo = None
    if re.search(r"\b(satisfaccion|que tal les parecio|opinion)\b", t):
        tipo = "satisfaccion"
    elif re.search(r"\basistencia\b", t):
        tipo = "asistencia"
    elif re.search(r"\b(evaluar|evaluacion)\b", t):
        tipo = "evaluacion"
    elif re.search(r"\b(inscrib\w*|inscripcion|registr\w*|apunt\w*)\b", t):
        tipo = "inscripcion"
    if not tipo:
        return None
    m = re.search(r"\b(?:a|al|para|de|del|en)\s+((?:una|un|la|el|nuestra|nuestro)\s+)?(capacitacion|evento|taller|curso|"
                  r"charla|jornada|feria|conferencia|seminario|actividad|formacion|reunion|induccion|convocatoria|"
                  r"salida|visita|olimpiada|torneo|webinar)\b(\s+(?:de|sobre)\s+[a-z ]{3,40})?", t)
    objeto = ""
    if m:
        nucleo = {"capacitacion": "la capacitación", "formacion": "la formación", "reunion": "la reunión",
                  "induccion": "la inducción", "convocatoria": "la convocatoria"}.get(m.group(2))
        art = "la" if m.group(2).endswith("a") or m.group(2) in ("actividad",) else "el"
        objeto = (nucleo or f"{art} {m.group(2)}") + (m.group(3) or "")
    return tipo, objeto.strip(), bool(re.search(r"\baprendic|\bestudiant|\bficha", t))


# ── Interpretación de instrucciones ─────────────────────────────────────────

_V_AGREGAR = r"(?:agrega|agregale|agregar|agreguemos|agregue|anade|anadele|anadir|pon|ponle|incluye|incluyele|incluir|" \
             r"mete|metele|suma|sumale|adiciona|crea|creale|necesito|quiero|tambien)"
_V_AGREGAR_REAL = r"(?:agrega|agregale|agregar|agreguemos|agregue|anade|anadele|anadir|incluye|incluyele|incluir|" \
                  r"mete|metele|suma|sumale|adiciona)"
_TIPO_RX = "|".join(rx for rx, _ in TIPO_FRASES)
_V_ELIMINAR = r"(?:elimina|eliminala|eliminalo|eliminar|quita|quitala|quitalo|quitale|borra|borrala|borralo|saca|" \
              r"sacala|sacalo|suprime|remueve)"


def interpretar(texto: str, fe: dict) -> Optional[List[tuple]]:
    """Operaciones que pide el mensaje sobre el borrador, o None si no habla del formulario."""
    t = plano(texto)
    if not t:
        return None
    # 1. Historial.
    if re.search(r"\b(deshaz|deshacer|deshaga|devuelve el ultimo cambio|revierte el ultimo|revierte\w*)\b|"
                 r"\bcomo estaba\b|\bvuelve atras\b|\bversion anterior\b", t) and not re.search(r"\bguardado|school\b", t):
        return [("deshacer",)]
    if re.search(r"\b(descarta|descartar|bota)\b.*\bcambios\b|como estaba guardado|como esta en school", t):
        return [("descartar",)]
    # 2. Ver / guardar / publicar.
    if re.search(r"\b(como (quedo|va|esta|queda|lo llevamos)|vista previa|previsualiza\w*|muestramelo|muestralo|"
                 r"ensenamelo|que preguntas (tiene|lleva|van)|la estructura)\b|\b(muestrame|ensename|ver|dime)\b.*\b"
                 r"(formulario|preguntas|borrador)\b", t) and not re.search(r"\bevento", t):
        return [("mostrar",)]
    if re.search(r"\b(no (lo )?publiques|sin publicar|no publicar|todavia no (lo )?publiques|aun no)\b", t):
        return [("no_publicar",)]
    if re.search(r"\b(despublica\w*|vuelve\w* a borrador|pasalo a borrador|regresalo a borrador)\b", t):
        return [("estado", "borrador")]
    if re.search(r"\b(cierralo|cierra el formulario|cerrar el formulario)\b", t):
        return [("estado", "cerrado")]
    if re.search(r"\b(publicalo|publica|publicar|publiquemos|ponlo a disposicion)\b", t):
        return [("publicar",)]
    if re.search(r"\b(guardalo|guarda|guardar|guardemos|salvalo|grabalo|graba|crealo|crea el formulario|"
                 r"dejalo guardado)\b", t) and not re.search(r"\bpregunta|\bcampo", t):
        return [("guardar",)]
    # 3. Título y descripción del formulario.
    if re.search(r"\b(titulo|nombre)( del formulario)?\b", t) and re.search(r"\b(cambia|cambiale|pon|ponle|renombra|"
                                                                          r"modifica|corrige|cambiar)\b", t) \
            and not re.search(r"\b(pregunta|campo)\b|\b(la|el) (\d+|" + "|".join(_ORD) + r")\b", t):
        valor = _valor_tras(texto, r"\b(?:a|por|que diga|que sea|sea|de nombre|como nombre|de titulo|como titulo)\b|:")
        return [("titulo", valor)]
    if re.search(r"\b(instrucciones|descripcion|introduccion)\b", t) and re.search(r"\b(agrega\w*|pon\w*|cambia\w*|"
                                                                                r"incluye\w*|anade\w*)\b", t) \
            and not re.search(r"\b(pregunta|campo)\b", t):
        return [("descripcion", _valor_tras(texto, r"\b(?:que diga|diciendo)\b|:"))]
    # 4. Generación asistida.
    if re.search(r"\b(mas corto|mas cortico|resumelo|reducelo|acortalo|menos preguntas)\b", t):
        return [("acortar",)]
    if re.search(r"\b(preguntas|datos|campos) (personales|sensibles|privados)\b", t) and re.search(_V_ELIMINAR, t):
        return [("quitar_personales",)]
    m = re.search(r"\bsolo (?:necesito|quiero|pide|pidele|pedir|dejale|deja|con)\s+(.+)$", texto, re.I)
    if m:
        return [("solo", lista(m.group(1)))]
    m = re.search(r"\bque sea (?:una |un )?(encuesta de satisfaccion|formulario de (?:asistencia|satisfaccion|evaluacion|"
                  r"inscripcion)|encuesta|evaluacion|registro de asistencia)\b", t)
    if m:
        return [("plantilla", proposito(m.group(1))[0] if proposito(m.group(1)) else "satisfaccion")]
    if re.search(r"\bseccion\b", t):
        return [("seccion", _cap(_valor_tras(texto, r"\bsecci[oó]n\s+(?:de|para|sobre)?") or "Observaciones"))]
    # 5. Conversión masiva.
    if re.search(r"\btodas\b.*\b(de texto|abiertas)\b.*\b(seleccion|opciones|desplegable|cerradas)\b", t):
        return [("convertir_seleccion",)]
    # 6. Obligatoriedad.
    m = re.search(r"\b(todas|todos)\b.*\bobligatori\w*\b(?:\s+(?:menos|excepto|salvo|a excepcion de|excepto)\s+(.+))?", t)
    if m:
        return [("obligatorias_menos", m.group(2) or "")]
    if re.search(r"\b(todas|todos)\b.*\bopcional\w*\b|\bninguna (sea )?obligatori", t):
        return [("requerir", "todas", False)]
    if re.search(r"\bobligatori\w*|\bopcional\w*|\brequerid\w*", t):
        return [("requerir", texto, not re.search(r"\bopcional|\bno (sea |es )?obligatori|\bno requerid", t))]
    # 7. Opciones.
    m = re.search(r"\b(?:las )?opciones?\s*(?:son|seran|serian|:|deben ser|que sean)\s*(.+)$", texto, re.I)
    if m and not re.search(rf"\b{_V_AGREGAR}\b.*\bpregunta", t):
        return [("opciones", texto[:m.start()], lista(m.group(1)))]
    m = re.search(r"\b(?:agrega|agregale|anade|pon|ponle|incluye)\w*\s+(?:la |una |otra )?opcion\s+(.+?)(?:\s+(?:a|en)\s+"
                  r"la\s+(pregunta.*|\w+))?$", texto, re.I)
    if m:
        return [("opcion_mas", m.group(2) or "", m.group(1).strip(" «»\"'"))]
    # 8. Tipo ("que sea desplegable", "cambia la tercera a número"): lo que sigue es SOLO un tipo.
    m = re.search(r"\b(?:que sea|sea|(?:cambia|convierte|pasa|pon|deja)\w*(?:\s+\S+){0,4}?\s+(?:a|en|por|como)|hazla|"
                  r"hazlo|tipo)\s+(?:una?\s+)?(.+)$", t)
    tipo_solo = m and re.fullmatch(rf"(?:{_TIPO_RX})(?:\s+(?:con|y)\b.*)?", m.group(1).strip())
    if tipo_solo and not re.search(rf"^{_V_AGREGAR_REAL}\b", t):
        resto = re.search(r"\b(?:con\s+(?:las\s+)?opciones?|opciones?\s*:)\s*(.+)$", texto, re.I)
        return [("tipo", t[:m.start(1)], tipo_de_frase(m.group(1)), lista(resto.group(1)) if resto else None)]
    # 9. Duplicar.
    if re.search(r"\b(duplica|duplicala|duplicalo|copia la|copia el|repite la)\b", t):
        return [("duplicar", texto)]
    # 10. Mover.
    m = re.search(r"\b(primero|primera|de primer\w*|al principio|al inicio|de ultim\w*|al final|antes de|despues de|"
                  r"arriba de|debajo de|encima de)\b(.*)$", t)
    if m and re.search(r"\b(aparezca|vaya|quede|este|ponla|ponlo|pon|mueve\w*|pasa\w*|sube\w*|baja\w*|coloca\w*|"
                       r"lleva\w*)\b", t) and not re.search(rf"^(?:y\s+)?{_V_AGREGAR_REAL}\b", t):
        return [("mover", t[:m.start()], m.group(1), m.group(2))]
    # 11. Renombrar una pregunta.
    m = re.search(r"\b(?:cambia|modifica|corrige|renombra|reescribe)\w*\s+(?:el texto |el enunciado |el titulo |"
                  r"la redaccion )?(?:de\s+)?(la .+?|el .+?)\s+(?:por|a|para que diga|que diga)\s+(.+)$", texto, re.I)
    if m:
        return [("renombrar", m.group(1), _cap(m.group(2).strip(" «»\"'")))]
    m = re.search(r"^(la .+?)\s+que diga\s+(.+)$", texto, re.I)
    if m:
        return [("renombrar", m.group(1), _cap(m.group(2).strip(" «»\"'")))]
    # 12. Eliminar.
    if re.search(rf"\b{_V_ELIMINAR}\b", t) and not re.search(r"\bevento\b|\bformulario\b(?!.*\bpregunta)", t):
        return [("eliminar", texto)]
    # 13. Agregar. Continuaciones elípticas ("y otra abierta para sugerencias", "otra de fecha")
    #     y verbos de contenido ("va a pedir…", "que pida…", "debe tener…").
    if re.match(r"(?:y\s+)?(?:tambien\s+)?(?:otra|otro|una mas|otra mas)\b", t) and not re.search(r"\bevento\b", t):
        return _agregar("agrega " + texto)
    m = re.match(r"(?:y\s+)?(?:que\s+)?(?:va a pedir|vamos a pedir|va a preguntar|debe pedir|debe tener|que pida|"
                 r"que tenga|que pregunte|pide|pidele|preguntale|lleva|llevara|tendra)\s+(.+)$", texto.strip(), re.I)
    if m and not re.search(r"\b(evento|permiso)", t):
        return _agregar("agrega " + m.group(1))
    m = re.search(rf"^(?:y\s+)?(?:espera,?\s+)?(?:tambien\s+)?{_V_AGREGAR}\b\s*(.*)$", t)
    if m and (re.search(r"\b(pregunta|preguntas|campo|campos|casilla)\b", t)
              or not re.match(r"(?:y\s+)?(?:espera\s+)?(?:tambien\s+)?(quiero|necesito|crea|creale|tambien)\b", t)):
        return _agregar(texto)
    return None


def _valor_tras(texto: str, rx: str) -> Optional[str]:
    q = re.search(r"[\"«“']([^\"»”']{1,200})[\"»”']", texto)
    if q:
        return q.group(1).strip()
    m = list(re.finditer(rx, texto, re.I))
    if m:
        v = texto[m[-1].end():].strip(" .,;:")
        return v or None
    return None


def _agregar(texto: str) -> List[tuple]:
    t = texto.strip()
    pos = None
    mpos = re.search(r"\s*\b(despu[eé]s de|antes de)\s+la\s+(?:pregunta\s+)?(\d{1,2}|\w+)\b|\s*\b(al (?:principio|inicio)|"
                     r"de primer[ao]|al final|de [uú]ltim[ao])\b", t, re.I)
    if mpos:
        if mpos.group(1):
            n = int(mpos.group(2)) if mpos.group(2).isdigit() else _ORD.get(plano(mpos.group(2)))
            if n:
                pos = ("despues" if plano(mpos.group(1)).startswith("despues") else "antes", n)
        else:
            pos = ("inicio", 0) if re.search(r"principio|inicio|primer", plano(mpos.group(3))) else None
        t = (t[:mpos.start()] + " " + t[mpos.end():]).strip()
    resto = re.sub(rf"^(?:y\s+)?(?:espera,?\s+)?(?:tambi[eé]n\s+)?\w+\b\s*", "", t, flags=re.I)  # quita el verbo
    resto = re.sub(r"^(?:le\s+)?(?:al formulario\s+)?", "", resto, flags=re.I)
    mcant = re.match(r"(?:(\d{1,2})|(una|un|otra|otro|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez))\s+"
                     r"(?:preguntas?|campos?|casillas?)\b\s*(?:m[aá]s\b)?\s*(?::|que (?:sean|digan|pregunten)|sobre|de|"
                     r"para|con)?\s*", resto, re.I)
    cantidad = None
    if mcant:
        cantidad = int(mcant.group(1)) if mcant.group(1) else _NUM[plano(mcant.group(2))]
        resto = resto[mcant.end():]
    else:
        resto = re.sub(r"^(?:otra|otro)\b\s*(?:m[aá]s\b)?\s*:?\s*", "", resto, flags=re.I)
        resto = re.sub(r"^(?:las\s+|los\s+)?(?:preguntas?|campos?)\s*(?::|de|sobre|para)?\s*", "", resto, flags=re.I)
    resto = resto.strip(" .,;:")
    if not resto:
        return [("pedir_preguntas", cantidad or 1, pos)]
    # Una sola pregunta si trae opciones ("con opciones A, B") o es una pregunta escrita completa.
    if re.search(r"\bopciones?\b", resto, re.I) or resto.startswith("¿") or (cantidad == 1 and "," not in resto):
        items = [pregunta(resto)]
    else:
        items = [pregunta(x) for x in lista(resto)]
    return [("agregar", items, pos)]


# ── Aplicación ──────────────────────────────────────────────────────────────

def _guardar_hist(fe: dict):
    fe["hist"] = (fe.get("hist") or [])[-(HIST_MAX - 1):] + [foto(fe)]


def aplicar(fe: dict, ops_: List[tuple]) -> Tuple[dict, List[str], Optional[dict]]:
    """(borrador, frases de lo hecho, pregunta pendiente) — nunca toca School."""
    fe = copy.deepcopy(fe)
    hecho: List[str] = []
    for op in ops_:
        kind = op[0]
        if kind == "deshacer":
            if not fe.get("hist"):
                return fe, ["No hay cambios que deshacer."], None
            prev = fe["hist"].pop()
            fe.update(copy.deepcopy(prev))
            fe["foco"] = None
            hecho.append("Deshice el último cambio.")
            continue
        if kind == "descartar":
            if fe.get("base") is None:
                return fe, ["Este formulario todavía no está guardado; no hay una versión anterior en School."], None
            _guardar_hist(fe)
            fe.update(copy.deepcopy(fe["base"]))
            hecho.append("Descarté los cambios: el borrador quedó como está guardado en School.")
            continue
        _guardar_hist(fe)
        pregs = fe["preguntas"]
        if kind == "agregar":
            _, items, pos = op
            i = len(pregs)
            if pos:
                donde, n = pos
                if donde == "inicio":
                    i = 0
                else:
                    j = _indice(fe, n)
                    if j is None:
                        fe["hist"].pop()
                        return fe, [f"El formulario tiene {len(pregs)} preguntas; no hay una número {n}."], None
                    i = j + 1 if donde == "despues" else j
            pregs[i:i] = items
            fe["foco"] = i + len(items) - 1
            nombres = ", ".join(f"**{p['titulo']}**" for p in items)
            hecho.append(f"Agregué {nombres}" + (f" en la posición {i + 1}" if pos else "") + ".")
            for k, p in enumerate(items):
                if p["tipo"] in CON_OPCIONES and not p["opciones"]:
                    return fe, hecho, {"campo": "opciones", "indice": i + k}
        elif kind == "pedir_preguntas":
            fe["hist"].pop()
            return fe, [], {"campo": "agregar", "cantidad": op[1], "pos": op[2]}
        elif kind in ("requerir", "obligatorias_menos"):
            if kind == "obligatorias_menos":
                excepto = []
                if op[1]:
                    for parte in lista(op[1]):
                        hits = por_contenido(fe, parte) or [x for x in [_indice(fe, n) for n in ordinales(plano(parte))] if x is not None]
                        excepto += hits
                for k, p in enumerate(pregs):
                    p["esObligatoria"] = k not in excepto
                hecho.append("Todas quedaron obligatorias" + (
                    " menos " + ", ".join(f"**{pregs[k]['titulo']}**" for k in sorted(set(excepto))) if excepto else "") + ".")
                continue
            idx, err = (list(range(len(pregs))), None) if op[1] == "todas" else objetivos(fe, op[1])
            if err:
                fe["hist"].pop()
                return fe, [_err(fe, err)], None
            for k in idx:
                pregs[k]["esObligatoria"] = op[2]
            fe["foco"] = idx[-1]
            hecho.append(", ".join(f"**{pregs[k]['titulo']}**" for k in idx) + (" quedó" if len(idx) == 1 else " quedaron")
                         + (" obligatoria" if op[2] else " opcional") + ("s" if len(idx) > 1 else "") + ".")
        elif kind == "tipo":
            idx, err = objetivos(fe, op[1])
            if err:
                fe["hist"].pop()
                return fe, [_err(fe, err)], None
            for k in idx:
                pregs[k] = aplicar_tipo(pregs[k], op[2], op[3])
            fe["foco"] = idx[-1]
            k = idx[-1]
            hecho.append(f"**{pregs[k]['titulo']}** ahora es {TIPOS[pregs[k]['tipo']]}"
                         + (" (solo números)" if (pregs[k].get("configuracion") or {}).get("soloNumeros") else "") + ".")
            if pregs[k]["tipo"] in CON_OPCIONES and not pregs[k]["opciones"]:
                return fe, hecho, {"campo": "opciones", "indice": k}
        elif kind in ("opciones", "opcion_mas"):
            idx, err = objetivos(fe, op[1])
            if err:
                fe["hist"].pop()
                return fe, [_err(fe, err)], None
            k = idx[0]
            if pregs[k]["tipo"] not in CON_OPCIONES:
                pregs[k] = aplicar_tipo(pregs[k], "seleccion", op[2] if kind == "opciones" else [op[2]])
            elif kind == "opciones":
                previas = {plano(o["texto"]): o for o in pregs[k]["opciones"]}
                pregs[k]["opciones"] = [{**previas.get(plano(x), {}), "texto": x} for x in op[2]]
            else:
                pregs[k]["opciones"].append({"texto": _cap(op[2])})
            fe["foco"] = k
            hecho.append(f"**{pregs[k]['titulo']}** tiene ahora las opciones: "
                         + ", ".join(o["texto"] for o in pregs[k]["opciones"]) + ".")
        elif kind == "eliminar":
            idx, err = objetivos(fe, op[1], defecto_foco=False)
            if err:
                fe["hist"].pop()
                return fe, [_err(fe, err)], None
            nombres = ", ".join(f"**{pregs[k]['titulo']}**" for k in idx)
            for k in sorted(idx, reverse=True):
                pregs.pop(k)
            fe["foco"] = None
            hecho.append(f"Eliminé {nombres}.")
        elif kind == "mover":
            _, quien, donde, ref = op
            idx, err = objetivos(fe, quien)
            if err:
                fe["hist"].pop()
                return fe, [_err(fe, err)], None
            p = pregs.pop(idx[0])
            d = plano(donde)
            if re.search(r"primer|principio|inicio", d):
                destino = 0
            elif re.search(r"ultim|final", d):
                destino = len(pregs)
            else:
                pregs.insert(idx[0], p)
                ri, rerr = objetivos(fe, ref, defecto_foco=False)
                if rerr or ri[0] == idx[0]:
                    fe["hist"].pop()
                    return fe, [_err(fe, rerr or "no puedo mover una pregunta respecto de sí misma")], None
                pregs.pop(idx[0])
                r = ri[0] - (1 if ri[0] > idx[0] else 0)
                destino = r + 1 if re.search(r"despues|debajo", d) else r
            pregs.insert(destino, p)
            fe["foco"] = destino
            hecho.append(f"Moví **{p['titulo']}** a la posición {destino + 1}.")
        elif kind == "duplicar":
            idx, err = objetivos(fe, op[1])
            if err:
                fe["hist"].pop()
                return fe, [_err(fe, err)], None
            k = idx[0]
            copia = copy.deepcopy(pregs[k])
            copia.pop("id", None)
            for o in copia.get("opciones") or []:
                o.pop("id", None)
            copia["titulo"] += " (copia)"
            pregs.insert(k + 1, copia)
            fe["foco"] = k + 1
            hecho.append(f"Dupliqué **{pregs[k]['titulo']}**.")
        elif kind == "renombrar":
            idx, err = objetivos(fe, op[1])
            if err:
                fe["hist"].pop()
                return fe, [_err(fe, err)], None
            antes = pregs[idx[0]]["titulo"]
            pregs[idx[0]]["titulo"] = op[2]
            fe["foco"] = idx[0]
            hecho.append(f"Cambié **{antes}** por **{op[2]}**.")
        elif kind == "titulo":
            if not op[1]:
                fe["hist"].pop()
                return fe, [], {"campo": "titulo"}
            fe["titulo"] = _cap(op[1])
            hecho.append(f"El formulario ahora se llama **{fe['titulo']}**.")
        elif kind == "descripcion":
            if not op[1]:
                fe["hist"].pop()
                return fe, [], {"campo": "descripcion"}
            fe["descripcion"] = op[1].strip()
            hecho.append("Actualicé las instrucciones del formulario.")
        elif kind == "acortar":
            antes = len(pregs)
            fe["preguntas"] = [p for p in pregs if p.get("esObligatoria")][:5] or pregs[:3]
            hecho.append(f"Lo dejé en {len(fe['preguntas'])} preguntas (antes {antes}): quedan las obligatorias.")
        elif kind == "quitar_personales":
            personales = {c[0] for c in CAMPOS if c[6]}
            quitadas = [p["titulo"] for p in pregs if clave_de(p["titulo"]) in personales]
            fe["preguntas"] = [p for p in pregs if clave_de(p["titulo"]) not in personales]
            hecho.append(("Quité " + ", ".join(f"**{x}**" for x in quitadas) + ". Dejé los datos para identificar a la "
                          "persona (nombre, documento, correo); si tampoco los quieres, dime cuáles quitar.")
                         if quitadas else "No había preguntas de datos personales (teléfono, dirección, edad, género…).")
        elif kind == "solo":
            nuevas = []
            for nombre in op[1]:
                hits = por_contenido(fe, nombre)
                nuevas.append(pregs[hits[0]] if len(hits) == 1 else pregunta(nombre))
            fe["preguntas"] = nuevas
            hecho.append("Dejé solo: " + ", ".join(f"**{p['titulo']}**" for p in nuevas) + ".")
        elif kind == "plantilla":
            titulo, pregs_n = plantilla(op[1], "", False)
            fe["preguntas"] = pregs_n
            hecho.append(f"Cambié las preguntas por las de un formulario de {op[1]} (el título sigue siendo "
                         f"**{fe.get('titulo') or titulo}**; dime si lo cambio).")
        elif kind == "seccion":
            pregs.append({"tipo": "texto_largo", "titulo": op[1], "esObligatoria": False, "configuracion": None,
                          "opciones": []})
            fe["foco"] = len(pregs) - 1
            hecho.append(f"El constructor de School no tiene secciones, así que agregué **{op[1]}** como una pregunta "
                         "de párrafo al final.")
        elif kind == "convertir_seleccion":
            cambiadas, pendientes = [], []
            for k, p in enumerate(pregs):
                clave = clave_de(p["titulo"])
                if p["tipo"] != "texto_corto" or clave in ("nombre", "apellidos", "documento", "correo", "telefono",
                                                            "edad", "ficha", "direccion"):
                    continue
                if clave in ("tipo_doc", "genero", "programa", "jornada") or re.search(r"\b(tipo|cual|cuál)\b", plano(p["titulo"])):
                    pregs[k] = aplicar_tipo(p, "desplegable")
                    cambiadas.append(k)
                    if not pregs[k]["opciones"]:
                        pendientes.append(k)
            if not cambiadas:
                fe["hist"].pop()
                return fe, ["Ninguna pregunta de texto tiene sentido como selección: son datos que cada persona escribe "
                            "(nombre, documento, correo…)."], None
            hecho.append("Pasé a lista desplegable: " + ", ".join(f"**{pregs[k]['titulo']}**" for k in cambiadas)
                         + ". Dejé como texto las que son datos que cada persona escribe.")
            if pendientes:
                return fe, hecho, {"campo": "opciones", "indice": pendientes[0]}
    return fe, hecho, None


def _err(fe: dict, err: str) -> str:
    if err.startswith("ambigua:"):
        idx = [int(x) for x in err.split(":", 1)[1].split(",")]
        return "¿Cuál de estas? " + "; ".join(f"{i + 1}. {fe['preguntas'][i]['titulo']}" for i in idx)
    return f"No pude aplicarlo: {err}."


# ── Presentación ────────────────────────────────────────────────────────────

def linea(i: int, p: dict) -> str:
    tipo = "número" if (p.get("configuracion") or {}).get("soloNumeros") else TIPOS.get(p["tipo"], p["tipo"])
    ops_ = p.get("opciones") or []
    extra = f" ({', '.join(o['texto'] for o in ops_[:5])}{'…' if len(ops_) > 5 else ''})" if ops_ else ""
    if p["tipo"] in CON_OPCIONES and not ops_:
        extra = " (⚠️ sin opciones)"
    return f"{i}. {p['titulo']} · {tipo}{extra}" + (" · obligatoria" if p.get("esObligatoria") else "")


def estado_texto(fe: dict) -> str:
    if fe.get("id") is None:
        return "sin guardar en School"
    return ("con cambios sin guardar" if sucio(fe) else "guardado") + f" · {fe.get('estado') or 'borrador'}"


def presentar(fe: dict) -> str:
    cab = f"**{fe.get('titulo') or '(sin título)'}** — {estado_texto(fe)}"
    if not fe["preguntas"]:
        return cab + "\nTodavía no tiene preguntas."
    return cab + "\n" + "\n".join(linea(i, p) for i, p in enumerate(fe["preguntas"], 1))


def tarjeta(fe: dict) -> dict:
    """Datos para la vista compacta del chat (el texto ya es suficiente sin ella)."""
    return {"id": fe.get("id"), "titulo": fe.get("titulo"), "estado": fe.get("estado") or "borrador",
            "guardado": fe.get("id") is not None and not sucio(fe), "nuevo": fe.get("id") is None,
            "ruta": f"/formularios/builder/{fe['id']}" if fe.get("id") else None,
            "preguntas": [{"n": i, "titulo": p["titulo"], "tipo": "número" if (p.get("configuracion") or {}).get("soloNumeros")
                           else TIPOS.get(p["tipo"], p["tipo"]), "obligatoria": bool(p.get("esObligatoria")),
                           "opciones": [o["texto"] for o in p.get("opciones") or []][:8],
                           "foco": fe.get("foco") == i - 1}
                          for i, p in enumerate(fe["preguntas"], 1)]}
