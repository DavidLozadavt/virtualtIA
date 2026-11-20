"""
core/semantic/refinement.py — "De esos cuatro, ¿cuál tiene pescado?"

Una petición que REFINA lo ya mostrado no es una búsqueda nueva. Hasta ahora
ninguna capa reconocía el alcance "de esos / de los anteriores / de los cuatro /
entre esos / de los que me mostraste": esas palabras quedaban como contenido y
se buscaban en el catálogo. "De esos cuatros quiero uno que ofrezca pescado"
terminó en "No encontré cuatros pescado en Popayán".

Este módulo separa las tres partes de una petición así, sin mirar el catálogo:

- ALCANCE: el conjunto mostrado ("esos", "los anteriores", "los cuatro",
  "los que me mostraste", "entre ellos"), o un elemento de él ("el segundo").
- OPERACIÓN: qué hacer con ese conjunto — recomendar, filtrar por un requisito,
  descartar los que no lo cumplan, el más barato, el más cercano, cuáles abren.
- REQUISITO: lo que tiene que tener ("pescado", "comida de mar", "domicilio").

La resolución contra los datos (qué negocio cumple qué) la hace quien llama; si
la información no está registrada, se dice así en vez de suponerla.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from core.semantic.morphology import normalize

_DEM = r"(?:esos|esas|estos|estas|aquellos|aquellas)"
_CARDINALS = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10,
}
# El cardinal admite la "s" de más que pone la gente al hablar ("esos cuatros").
_CARD = r"(?:\d{1,2}|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)s?"
_SHOWN_VERBS = (r"(?:mostraste|mostro|diste|dijiste|salieron|encontraste|nombraste|recomendaste|"
                r"sugeriste|pasaste|listaste|ensenaste)")

#: Formas de referirse al conjunto que se acaba de mostrar. El orden importa:
#: primero las más largas, para que "de esos cuatro restaurantes" se consuma
#: entero y no deje "cuatro" suelto como contenido.
_SCOPE_PATTERNS = [
    rf"\b(?:de|entre)\s+(?:todos\s+)?(?:los|las)\s+(?:(?P<n1>{_CARD})\s+)?que\s+(?:me\s+|nos\s+)?(?:has\s+)?{_SHOWN_VERBS}\b",
    rf"\b(?:los|las)\s+(?:(?P<n2>{_CARD})\s+)?que\s+(?:me\s+|nos\s+)?(?:has\s+)?{_SHOWN_VERBS}\b",
    rf"\b(?:de|entre)\s+(?:todos\s+)?(?:los|las)\s+(?:(?P<n3>{_CARD})\s+)?anteriores\b",
    rf"\b(?:los|las)\s+(?:(?P<n4>{_CARD})\s+)?anteriores\b",
    rf"\b(?:de|entre)\s+(?:todos\s+)?{_DEM}(?:\s+(?P<n5>{_CARD}))?(?:\s+(?P<noun1>[a-z]+))?\b",
    rf"\b{_DEM}\s+(?P<n6>{_CARD})\b",
    rf"\b(?:de|entre)\s+(?:los|las)\s+(?P<n7>{_CARD})\b",
    r"\b(?:de|entre)\s+(?:ellos|ellas)\b",
    r"\b(?:cual|cuales)\s+de\s+(?:ellos|ellas)\b",
]
_SCOPE_RES = [re.compile(p) for p in _SCOPE_PATTERNS]

#: Palabras que, tras el demostrativo, NO son el sustantivo del conjunto sino
#: el arranque de la petición ("de esos QUIERO uno", "entre esos CUAL").
_NOT_A_NOUN = {"quiero", "que", "cual", "cuales", "dime", "me", "recomiendame", "recomienda",
               "dame", "muestrame", "uno", "una", "el", "la", "los", "las", "hay", "tienen",
               "tiene", "son", "abren", "descarta", "quita", "elimina", "saca", "cuantos", "y",
               "pero", "con", "sin", "por", "para", "a", "en", "mejor", "busca", "solo"}

#: Requisito introducido por un verbo de tener/ofrecer, o por "con"/"sin".
_REQ_VERB = (r"(?:tenga|tengan|tiene|tienen|tener|ofrezca|ofrezcan|ofrece|ofrecen|sirva|sirvan|sirve|"
             r"sirven|venda|vendan|vende|venden|haga|hagan|hace|hacen|maneje|manejen|maneja|manejan|"
             r"incluya|incluyan|incluye|incluyen|preste|presten|presta|prestan|hay)")
_REQ_RE = re.compile(rf"\b(?:{_REQ_VERB}|con|sin)\s+(?P<req>.+)$")

#: Palabras que acompañan al requisito sin ser el requisito: "pescado EN SU
#: MENÚ", "SERVICIO DE domicilio", "COMIDA de mar" no (comida de mar es el plato).
_REQ_FILLER = {"en", "su", "sus", "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
               "menu", "carta", "oferta", "lista", "catalogo", "por", "favor", "porfa", "porfavor",
               "registrado", "registrada", "disponible", "disponibles", "servicio", "servicios", "a",
               "al", "algo", "alguno", "alguna", "que", "y", "o", "tambien", "hoy", "ahora", "plato",
               "platos", "opcion", "opciones", "entre", "ellos", "ellas", "eso", "esos", "esas"}

#: Requisito especial: tener servicios o carta registrados.
CATALOG_REQUIREMENT = "__catalogo__"

_OPS = [
    ("nearest", re.compile(r"\b(?:mas\s+)?(?:cerca|cercan[oa]s?|proxim[oa]s?)\b")),
    ("cheapest", re.compile(r"\b(?:barat[oa]s?|economic[oa]s?|menos\s+car[oa]s?|menor\s+precio|precio\s+mas\s+bajo)\b")),
    ("priciest", re.compile(r"\bmas\s+car[oa]s?\b")),
    ("open_now", re.compile(r"\b(?:abren|abre|abiert[oa]s?|cierran|horarios?|atienden\s+hoy)\b")),
    ("exclude", re.compile(r"\b(?:descarta|descartame|descartes|quita|quitame|quitale|elimina|saca|excluye)\b")),
    ("best_rated", re.compile(r"\b(?:mejor\s+calificad[oa]s?|mejor\s+puntuad[oa]s?|mas\s+estrellas)\b")),
    ("recommend", re.compile(r"\b(?:recomiend\w*|recomend\w*|sugier\w*|suger\w*|el\s+mejor|la\s+mejor)\b")),
]


@dataclass
class SetRequest:
    """Lo que el usuario pide hacer sobre el conjunto que ya vio."""
    operation: str                       # recommend|filter|exclude|cheapest|priciest|nearest|open_now|best_rated|pick
    requirements: List[str] = field(default_factory=list)
    count: Optional[int] = None          # "de esos CUATRO"
    ordinal: Optional[int] = None        # "EL SEGUNDO, pero que tenga..."
    explicit_scope: bool = True          # dijo "de esos" (False: "mejor uno que tenga X")
    scope_text: str = ""


def _card_value(tok: Optional[str]) -> Optional[int]:
    if not tok:
        return None
    tok = tok.rstrip("s") if not tok.isdigit() else tok
    return int(tok) if tok.isdigit() else _CARDINALS.get(tok)


def find_scope(text_norm: str):
    """(span, count, noun) del primer alcance sobre lo mostrado, o None."""
    for rx in _SCOPE_RES:
        m = rx.search(text_norm)
        if not m:
            continue
        gd = m.groupdict()
        count = next((_card_value(gd[k]) for k in gd if k.startswith("n") and k[1:].isdigit() and gd[k]), None)
        noun = gd.get("noun1")
        span = (m.start(), m.end())
        if noun:
            if noun in _NOT_A_NOUN or noun in _CARDINALS or noun.rstrip("s") in _CARDINALS:
                # No es el sustantivo del conjunto: se deja fuera del alcance.
                span = (m.start(), m.start("noun1") - 1)
                noun = None
        return span, count, noun
    return None


def requirement_terms(text_norm: str) -> List[str]:
    """Los requisitos que siguen a "que tenga / ofrezca / con / sin", ya limpios."""
    m = _REQ_RE.search(text_norm)
    if not m:
        return []
    from core.semantic import lexicon as lx
    grupos = []
    for parte in re.split(r",|\by\b|\be\b|\bo\b|\bni\b", m.group("req")):
        def _relleno(w):
            return w in _REQ_FILLER or w.isdigit() or (lx.is_function_word(w) and w not in ("mar",))
        ws = parte.split()
        # Se recorta el relleno de los BORDES ("servicio de domicilio", "pescado
        # en su menú"); lo de dentro se conserva para decirlo como lo dijo el
        # usuario ("comida de mar", no "comida mar").
        while ws and _relleno(ws[0]):
            ws.pop(0)
        while ws and _relleno(ws[-1]):
            ws.pop()
        g = " ".join(ws)
        if g:
            grupos.append(g)
    if not grupos and re.search(r"\b(?:menu|carta|catalogo|servicios?)\b", m.group("req")):
        # "Los que no tengan menú": el requisito es tener carta registrada.
        grupos = [CATALOG_REQUIREMENT]
    return list(dict.fromkeys(grupos))


def read_set_request(message: str, ordinal: Optional[int] = None,
                     has_presented: bool = True) -> Optional[SetRequest]:
    """
    ¿El mensaje opera sobre lo ya mostrado? Devuelve la petición, o None.

    Sin alcance explícito sólo se acepta la forma "uno/alguno que tenga X" (o
    "el segundo, pero que tenga X"): elige DENTRO de lo que hay sobre la mesa.
    "Busca restaurantes con pescado" no es un refinamiento: pide buscar.
    """
    if not has_presented:
        return None
    t = normalize(message)
    if not t:
        return None
    alcance = find_scope(t)
    resto = t
    count = None
    if alcance:
        (a, b), count, _noun = alcance
        resto = (t[:a] + " " + t[b:]).strip()

    reqs = requirement_terms(resto)
    op = next((name for name, rx in _OPS if rx.search(resto)), None)

    if not alcance:
        # Sin "de esos": sólo "uno que tenga X", "alguno con X" o "el segundo,
        # pero que tenga X" eligen dentro de lo mostrado.
        if ordinal is not None and reqs:
            return SetRequest("pick", reqs, ordinal=ordinal, explicit_scope=False)
        if reqs and re.search(r"\b(?:uno|una|alguno|alguna|cual|cuales)\s+(?:que|con|sin)\b", t):
            return SetRequest("filter" if op in (None, "recommend") else op, reqs, explicit_scope=False)
        return None

    if op == "exclude" or (reqs and re.search(r"\bsin\b", resto)):
        return SetRequest("exclude", reqs, count=count, scope_text=t[alcance[0][0]:alcance[0][1]])
    if op in ("nearest", "cheapest", "priciest", "open_now", "best_rated"):
        return SetRequest(op, reqs, count=count, scope_text=t[alcance[0][0]:alcance[0][1]])
    if reqs:
        return SetRequest("filter", reqs, count=count, ordinal=ordinal,
                          scope_text=t[alcance[0][0]:alcance[0][1]])
    if op == "recommend" or re.search(r"\b(?:uno|una|cual|cuales)\b", resto):
        return SetRequest("recommend", [], count=count, scope_text=t[alcance[0][0]:alcance[0][1]])
    # "De esos" sin decir qué hacer: se pregunta, no se adivina.
    return SetRequest("unclear", [], count=count, scope_text=t[alcance[0][0]:alcance[0][1]])
