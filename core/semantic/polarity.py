"""
core/semantic/polarity.py — Qué parte del mensaje el usuario AFIRMA y cuál RECHAZA.

El análisis del acto de habla mira qué marcos aparecen en el mensaje, pero no
mira con qué signo aparecen. Por eso "quiero agendar" y "no quiero agendar"
producían exactamente el mismo resultado: el marco de cita estaba en ambos, y la
cascada lo leía como una petición de reserva en los dos casos.

    Usuario: no, yo no quiero agendar, yo quiero saber qué negocios
             ofrecen medicina general
    Lyra:    Claro, ¿a qué hora te viene mejor?

Este módulo pone el signo. Parte el mensaje en cláusulas, decide cuáles caen
bajo el alcance de una negación y devuelve por separado lo que el usuario
rechaza y lo que afirma en su lugar. Con eso, la corrección deja de ser ruido
dentro de la frase y pasa a ser la información más fiable del turno.

La distinción que lo hace seguro:

    "no quiero agendar"  → el usuario RECHAZA el marco de cita.
    "no tienen citas?"   → el usuario PREGUNTA por la ausencia de citas.

Sólo la primera es un rechazo, y sólo ella retira el marco. La marca es que la
negación cae sobre un verbo de voluntad o sobre la cópula ("no quiero", "no
necesito", "no era eso"), que es como se corrige a alguien en español. Negar un
verbo de existencia o de posesión ("no hay", "no tienen") sigue siendo una
pregunta sobre el mundo y se analiza como siempre.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set

from core.semantic import lexicon as lx
from core.semantic.morphology import in_stem_set, normalize, phonetic_stem

# ═══════════════════════════════════════════════════════════════════════════════
# § 1. FRONTERAS DE CLÁUSULA
# ═══════════════════════════════════════════════════════════════════════════════
# Una corrección casi siempre viene en dos tiempos: primero se rechaza, después
# se dice qué se quería. Lo que separa las dos mitades es la puntuación o un
# conector contrastivo. Ambos son clase cerrada: no hay que conocer el dominio
# para reconocerlos.

#: Conectores que abren la mitad AFIRMATIVA de una corrección. Después de ellos
#: viene lo que el usuario sí quiere.
RECTIFYING_CONNECTIVES = (
    "sino que", "sino", "mas bien", "mejor dicho", "en realidad", "realmente",
    "lo que quiero es", "lo que quiero", "lo que necesito es", "lo que necesito",
    "lo que busco es", "lo que busco", "me refiero a", "me refiero",
    "quise decir", "quiero decir", "solamente quiero", "solamente", "solo quiero",
    "solo", "unicamente", "nada mas quiero", "simplemente",
)

#: Conectores neutros: separan cláusulas sin marcar corrección ("pero", "y").
NEUTRAL_CONNECTIVES = ("pero", "aunque", "ademas", "tambien")

#: Marcadores de discurso que ordenan el turno sin aportar contenido: "antes de
#: eso", "primero". Sin retirarlos, "antes" viajaba como palabra de contenido y
#: se intentaba anclar al catálogo.
DISCOURSE_OPENERS = (
    "antes de eso", "antes que nada", "antes que todo", "primero que todo",
    "primero que nada", "una cosa", "una pregunta", "por cierto", "oye", "mira",
    "espera", "espera un momento", "de una vez", "ah", "eh", "bueno", "pues",
    "primero", "ahora",
)

#: Formas que niegan. Se suman a las del inventario social las que sólo aparecen
#: dentro de la oración ("tampoco", "ni").
NEGATORS = lx.NEGATIVE_FORMS | frozenset({"tampoco", "ni"})

#: Demostrativos que pueden ser el objeto de un rechazo: "eso no", "así no".
_REJECTABLE_DEMONSTRATIVES = frozenset({"eso", "esa", "ese", "esto", "asi", "aquello"})

#: Cuántos tokens después del negador se sigue considerando que la negación cae
#: sobre el verbo. Cubre "no lo quiero", "no me interesa", "yo no quiero".
_NEGATION_REACH = 3

#: El negador cae sobre uno de estos verbos → el usuario corrige, no pregunta.
_REJECTION_VERB_STEMS = lx.VOLITIVE_STEMS | lx.COPULA_STEMS | lx.PREFERENCE_STEMS

_SPLIT_PUNCTUATION = re.compile(r"[,;.!¡¿?]+")


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. RESULTADO
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Clause:
    """Un tramo del mensaje con su signo."""
    text: str
    tokens: List[str] = field(default_factory=list)
    #: El tramo cae bajo una negación que rechaza lo que nombra.
    rejecting: bool = False
    #: El tramo abre la mitad afirmativa de una corrección.
    rectifying: bool = False
    #: El tramo se agota en un marcador de discurso o en el negador pelado.
    empty: bool = False


@dataclass
class Reading:
    """Lo que el usuario rechaza y lo que afirma en su lugar."""
    clauses: List[Clause] = field(default_factory=list)
    #: Texto que queda tras retirar lo rechazado y el andamiaje de discurso.
    affirmed: str = ""
    #: Texto de las cláusulas rechazadas, para saber QUÉ se está descartando.
    rejected: str = ""
    #: El mensaje corrige una interpretación anterior.
    corrective: bool = False

    @property
    def rejects(self) -> bool:
        return any(c.rejecting for c in self.clauses)

    @property
    def has_affirmation(self) -> bool:
        return bool(self.affirmed.strip())


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. LECTURA
# ═══════════════════════════════════════════════════════════════════════════════

def read(message: str) -> Reading:
    """Reparte el mensaje entre lo que el usuario afirma y lo que rechaza."""
    clauses = [_classify(text) for text in _segment(message) if text]
    if not clauses:
        return Reading()

    corrective = any(c.rejecting or c.rectifying for c in clauses)

    # Una cláusula rectificativa ("me refiero a…", "lo que quiero es…") anuncia
    # que lo dicho antes ya no vale: lo anterior se descarta aunque no llevara
    # negación. Es la misma operación que hace un hablante al reformular.
    primera_rectificacion = next(
        (i for i, c in enumerate(clauses) if c.rectifying), None
    )

    afirmadas: List[Clause] = []
    rechazadas: List[Clause] = []
    for idx, clause in enumerate(clauses):
        if clause.rejecting:
            rechazadas.append(clause)
            continue
        if clause.empty:
            continue
        if primera_rectificacion is not None and idx < primera_rectificacion:
            continue
        afirmadas.append(clause)

    return Reading(
        clauses=clauses,
        affirmed=" ".join(c.text for c in afirmadas).strip(),
        rejected=" ".join(c.text for c in rechazadas).strip(),
        corrective=corrective,
    )


def _segment(message: str) -> List[str]:
    """Parte el mensaje por puntuación y por conectores contrastivos."""
    texto = normalize(message, keep_punctuation=True)
    if not texto:
        return []

    tramos = [t.strip() for t in _SPLIT_PUNCTUATION.split(texto)]
    salida: List[str] = []
    for tramo in tramos:
        if tramo:
            salida.extend(_split_on_connectives(tramo))
    return [normalize(s) for s in salida if s.strip()]


def _split_on_connectives(tramo: str) -> List[str]:
    """
    Corta un tramo delante de un conector, conservando el conector.

    Se conserva a propósito: es lo que permite reconocer después que la cláusula
    es la mitad afirmativa de una corrección ("…, sino que quiero…").
    """
    conectores = sorted(
        RECTIFYING_CONNECTIVES + NEUTRAL_CONNECTIVES, key=len, reverse=True
    )
    for conector in conectores:
        patron = rf"(?<!\w){re.escape(conector)}(?!\w)"
        match = re.search(patron, tramo)
        if not match or match.start() == 0:
            continue
        izquierda = tramo[: match.start()].strip()
        derecha = tramo[match.start():].strip()
        return ([izquierda] if izquierda else []) + _split_on_connectives(derecha)
    return [tramo]


def _classify(text: str) -> Clause:
    """Decide el signo de una cláusula."""
    toks = [t for t in text.split() if t]
    clause = Clause(text=text, tokens=toks)
    if not toks:
        clause.empty = True
        return clause

    clause.rectifying = _opens_with_any(text, RECTIFYING_CONNECTIVES)

    nucleo = {t for t in toks if t not in NEGATORS}
    solo_negacion = bool(set(toks) & NEGATORS) and not (
        nucleo - lx.ARTICLES - lx.PREPOSITIONS - lx.CONJUNCTIONS - lx.FIRST_PERSON_CLITICS
    )

    if solo_negacion:
        # "no" pelado, "no, ...", "nunca". Rechaza la lectura anterior sin
        # nombrar nada: no retira ningún marco, sólo marca la corrección.
        clause.rejecting = True
        clause.empty = True
        return clause

    if _is_rejection(toks):
        clause.rejecting = True
        return clause

    if _opens_with_any(text, DISCOURSE_OPENERS) and _is_only_discourse(toks):
        clause.empty = True

    return clause


def _is_rejection(toks: List[str]) -> bool:
    """
    ¿La negación de esta cláusula cae sobre lo que el usuario quiere?

    Sólo entonces es un rechazo. "No quiero agendar" retira el marco de cita;
    "no tienen citas" pregunta por la disponibilidad y lo conserva.
    """
    for idx, tok in enumerate(toks):
        if tok not in NEGATORS:
            continue
        # "eso no", "así no": el objeto del rechazo va delante.
        if idx > 0 and toks[idx - 1] in _REJECTABLE_DEMONSTRATIVES:
            return True
        for siguiente in toks[idx + 1: idx + 1 + _NEGATION_REACH]:
            if siguiente in _REJECTABLE_DEMONSTRATIVES:
                return True
            if in_stem_set(phonetic_stem(siguiente), _REJECTION_VERB_STEMS):
                return True
    return False


def _opens_with_any(text: str, phrases) -> bool:
    return any(
        re.match(rf"{re.escape(p)}(?!\w)", text) for p in phrases
    )


def _is_only_discourse(toks: List[str]) -> bool:
    """La cláusula no aporta nada más que el marcador de discurso."""
    return all(
        lx.is_function_word(t) or any(
            re.fullmatch(rf"{re.escape(w)}", t) for p in DISCOURSE_OPENERS for w in p.split()
        )
        for t in toks
    )
