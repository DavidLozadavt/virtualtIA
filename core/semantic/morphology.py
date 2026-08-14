"""
core/semantic/morphology.py — Robustez léxica para español hablado y escrito.

El usuario escribe rápido, sin tildes, con errores, o su voz pasa por un STT que
confunde sonidos parecidos. Nada de eso debería cambiar lo que el sistema
entiende. Este módulo reduce una palabra a una forma comparable para que
"médico", "medico", "medicos" y "mediko" caigan en el mismo punto.

Son reglas de la lengua (flexión, ortografía, fonética), no listas de sinónimos
de dominio: sirven igual para "barbería" que para cualquier palabra que aparezca
mañana en la base de datos.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Set


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. NORMALIZACIÓN BÁSICA
# ═══════════════════════════════════════════════════════════════════════════════

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    """Quita tildes y diacríticos, conservando la ñ como carácter propio."""
    if not text:
        return ""
    # Protegemos la ñ: en español distingue palabras, no es un adorno.
    guarded = text.replace("ñ", "\x00").replace("Ñ", "\x00")
    decomposed = unicodedata.normalize("NFKD", guarded)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.replace("\x00", "ñ")


def normalize(text: str, keep_punctuation: bool = False) -> str:
    """Minúsculas, sin tildes, espacios colapsados."""
    if not text:
        return ""
    out = strip_accents(str(text)).lower()
    if not keep_punctuation:
        out = _PUNCT_RE.sub(" ", out)
    return _SPACES_RE.sub(" ", out).strip()


def tokens(text: str) -> List[str]:
    """Palabras normalizadas del texto."""
    norm = normalize(text)
    return [t for t in norm.split() if t]


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. PLEGADO FONÉTICO
# ═══════════════════════════════════════════════════════════════════════════════
# El español tiene homófonos ortográficos sistemáticos: b/v, s/z/c(e,i), ll/y,
# g(e,i)/j, h muda, qu/k/c. Un STT o un dedo rápido los intercambian todo el
# tiempo. Plegarlos a una sola letra hace que la comparación sea sorda a esa
# diferencia sin necesidad de registrar cada variante.

_PHONETIC_RULES = (
    (re.compile(r"h"), ""),                 # h muda: "aora" ~ "ahora"
    (re.compile(r"[bv]"), "b"),             # "bamos" ~ "vamos"
    (re.compile(r"ll|y"), "y"),             # yeísmo: "yamar" ~ "llamar"
    (re.compile(r"qu(?=[ei])"), "k"),       # "queso" -> "keso"
    (re.compile(r"c(?=[ei])"), "s"),        # seseo: "cita" -> "sita"
    (re.compile(r"z"), "s"),                # "sazon" ~ "sason"
    (re.compile(r"c(?![ei])"), "k"),        # "casa" -> "kasa"
    (re.compile(r"g(?=[ei])"), "j"),        # "gente" -> "jente"
    (re.compile(r"x"), "ks"),
    (re.compile(r"(.)\1+"), r"\1"),         # letras repetidas: "holaaa" -> "hola"
)


def phonetic(word: str) -> str:
    """Clave fonética aproximada de una palabra en español."""
    w = normalize(word)
    if not w:
        return ""
    for pattern, repl in _PHONETIC_RULES:
        w = pattern.sub(repl, w)
    return w


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. LEMATIZACIÓN LIGERA
# ═══════════════════════════════════════════════════════════════════════════════
# Reducimos flexión de número, género y las terminaciones verbales más comunes.
# No pretende ser un lematizador completo: pretende que "barberías", "barbería"
# y "barbero" compartan raíz, y que "cortarme" y "corte" se acerquen.

_VERB_SUFFIXES = (
    # Clíticos pospuestos primero: "cortarme", "atenderlos", "verla"
    "melo", "mela", "selo", "sela", "nosla", "noslo",
    "me", "te", "se", "nos", "os", "le", "les", "lo", "la", "los", "las",
)

_INFLECTION_SUFFIXES = (
    # Verbales (las más largas primero para no truncar de más)
    "abamos", "iamos", "eramos", "aramos", "asemos", "iesemos",
    "aremos", "eremos", "iremos", "ariamos", "eriamos", "iriamos",
    "andose", "iendose", "ando", "iendo", "ado", "ido", "ada", "ida",
    "aria", "eria", "iria", "aran", "eran", "iran", "aron", "ieron",
    "abas", "aban", "aba", "ias", "ian", "ia",
    "amos", "emos", "imos", "ais", "eis", "an", "en",
    "ar", "er", "ir", "as", "es", "os", "a", "e", "o", "s",
)

_MIN_STEM = 3


#: Terminaciones a las que se engancha un clítico en español: infinitivo,
#: gerundio o imperativo. "cortar-me", "ver-la", "atendiendo-le".
_CLITIC_HOSTS = ("ar", "er", "ir", "ando", "iendo")


def _strip_clitics(word: str) -> str:
    """
    Quita un pronombre pospuesto, si de verdad lo hay.

    El clítico sólo se separa cuando lo que queda puede alojarlo: un infinitivo,
    un gerundio o una forma lo bastante larga para ser un imperativo. Sin esa
    condición, cualquier palabra terminada en las letras de un pronombre perdía
    su final —"conose" se quedaba en "kono" y de ahí en "kon", que es la
    preposición "con"— y una palabra corriente activaba lecturas ajenas.
    """
    for suf in _VERB_SUFFIXES:
        if not word.endswith(suf):
            continue
        base = word[: -len(suf)]
        if len(base) < _MIN_STEM:
            continue
        if base.endswith(_CLITIC_HOSTS) or len(base) >= 5:
            return base
    return word


def stem(word: str) -> str:
    """
    Raíz ortográfica aproximada: sin clíticos, sin marca de número/género/persona.

    Se mantiene en ortografía —no en fonética— a propósito. Palabras que
    comparten raíz pero divergen en sonido ("médico" /k/ frente a "medicina"
    /s/) sólo siguen emparentadas si no se pliega la consonante antes de cortar
    los sufijos. El plegado fonético existe aparte, como segundo intento, para
    absorber erratas y ruido de STT.
    """
    w = normalize(word)
    if not w or len(w) <= _MIN_STEM:
        return w

    w = _strip_clitics(w)
    for suf in _INFLECTION_SUFFIXES:
        if len(w) - len(suf) >= _MIN_STEM and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w


def phonetic_stem(word: str) -> str:
    """
    Raíz plegada fonéticamente: absorbe erratas y confusiones del STT.

    El plegado va ANTES de cortar los sufijos, no después. Las reglas fonéticas
    del español dependen de la letra siguiente —la c de "ofrecer" suena /s/
    porque va antes de una e—, así que cortar primero destruye el contexto y
    "ofreser" dejaría de parecerse a "ofrecer".
    """
    return stem(phonetic(word))


_PREFIX_MIN = 4


def stem_compatible(a: str, b: str) -> bool:
    """
    True si dos raíces pertenecen plausiblemente a la misma familia de palabras.

    La derivación en español alarga la raíz ("medic-" → "medicin-", "barber-" →
    "barberi-"), así que una raíz que es prefijo de otra suele ser la misma
    familia. El plegado fonético cubre el caso de la errata.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= _PREFIX_MIN and len(b) >= _PREFIX_MIN and (a.startswith(b) or b.startswith(a)):
        return True
    pa, pb = phonetic(a), phonetic(b)
    if pa and pa == pb:
        return True
    if len(pa) >= _PREFIX_MIN and len(pb) >= _PREFIX_MIN and (pa.startswith(pb) or pb.startswith(pa)):
        return True
    if len(a) >= 5 and len(b) >= 5:
        return SequenceMatcher(None, pa, pb).ratio() >= 0.88
    return False


def in_stem_set(word_stem: str, stem_set: Iterable[str]) -> bool:
    """Pertenencia tolerante de una raíz a un conjunto de raíces."""
    if not word_stem:
        return False
    if word_stem in stem_set:
        return True
    return any(stem_compatible(word_stem, s) for s in stem_set)


def stems(text: str) -> List[str]:
    """Raíces de todas las palabras del texto, sin vaciar duplicados."""
    return [stem(t) for t in tokens(text) if t]


def stem_set(text: str) -> Set[str]:
    return {s for s in stems(text) if s}


# ═══════════════════════════════════════════════════════════════════════════════
# § 4. SIMILITUD
# ═══════════════════════════════════════════════════════════════════════════════

def similarity(a: str, b: str) -> float:
    """Similitud 0..1 entre dos palabras, tolerante a erratas y a fonética."""
    if not a or not b:
        return 0.0
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return 1.0
    pa, pb = phonetic(na), phonetic(nb)
    if pa and pa == pb:
        return 0.97
    sa, sb = stem(na), stem(nb)
    if sa and sa == sb:
        return 0.94
    if stem_compatible(sa, sb):
        return 0.90
    return SequenceMatcher(None, pa, pb).ratio()


def best_match(word: str, candidates: Iterable[str], threshold: float = 0.84) -> Optional[str]:
    """Candidato más parecido a `word`, o None si ninguno supera el umbral."""
    best, best_score = None, threshold
    for cand in candidates:
        score = similarity(word, cand)
        if score >= best_score:
            best, best_score = cand, score
    return best


def phrase_overlap(query: str, target: str) -> float:
    """
    Cuánto del contenido de `query` aparece en `target`, por raíces.

    Se mide sobre la consulta —no sobre el objetivo— para que un nombre largo
    ("Fogón Criollo Norte Popayán") no se penalice cuando el usuario escribe
    sólo una parte ("fogón criollo").
    """
    q = [s for s in stems(query) if s]
    t = set(s for s in stems(target) if s)
    if not q or not t:
        return 0.0
    hits = 0.0
    for qs in q:
        if qs in t:
            hits += 1.0
        elif any(stem_compatible(qs, ts) for ts in t):
            hits += 0.8
    return hits / len(q)
