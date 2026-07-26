# core/co_address_parser.py
"""
Colombian address parser — the SINGLE authority for interpreting Colombian
addresses before the geocoder.

Spec: docs/superpowers/specs/colombian_address_parser_design.md
Scope: Popayán, Cauca, Colombia only.

Pipeline (mandatory order, §6):
    text → preprocess → tokenizer → lexical → parser → AST
         → repair → validate → reconstruct → ParsedAddress

Invariants:
  - AST is built before validation; validation is never first.
  - Order is always Parse → Repair → Validate (repair-first).
  - Reconstruction reads the AST only (never the raw token list / input string).
  - Regex is confined to lexical recognition in the tokenizer (D6).
  - The parser never adds city/department/country and never shapes a Google query.
  - No transformation is invisible: every stage is logged.

This module owns ALL address normalization/repair/validation/reconstruction.
Other modules consume ParsedAddress; none re-derive address structure.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("lyra.core.co_address_parser")


# ══════════════════════════════════════════════════════════════════════════════
# Contracts (types)
# ══════════════════════════════════════════════════════════════════════════════

class AddressState(str, Enum):
    STREET_ADDRESS            = "street_address"
    INTERSECTION              = "intersection"
    NEIGHBORHOOD              = "neighborhood"
    LANDMARK                  = "landmark"
    PLACE_NAME                = "place_name"
    INVALID_ADDRESS_STRUCTURE = "invalid_address_structure"
    UNKNOWN                   = "unknown"


class RepairKind(str, Enum):
    TOKEN_SPLIT                = "TOKEN_SPLIT"
    TOKEN_MERGE                = "TOKEN_MERGE"
    LETTER_ATTACHMENT          = "LETTER_ATTACHMENT"
    NUMBER_ATTACHMENT          = "NUMBER_ATTACHMENT"
    REMOVED_FILLER             = "REMOVED_FILLER"
    NORMALIZED_SEPARATOR       = "NORMALIZED_SEPARATOR"
    ABBREVIATION_NORMALIZATION = "ABBREVIATION_NORMALIZATION"
    STREET_ORDER_REBUILD       = "STREET_ORDER_REBUILD"
    PLACA_SEPARATOR_RECOVERY   = "PLACA_SEPARATOR_RECOVERY"


class TokenKind(str, Enum):
    VIA        = "VIA"
    NUMBER     = "NUMBER"
    NUM_LETTER = "NUM_LETTER"   # digits glued to trailing letter(s): 73B, 3C
    GLUED      = "GLUED"        # digit-letter-digit: 3C6, 8A14
    LETTER     = "LETTER"       # standalone single spelled letter
    BIS        = "BIS"
    CARDINAL   = "CARDINAL"
    HASH       = "HASH"
    DASH       = "DASH"
    CON        = "CON"
    Y          = "Y"            # connector for compound numbers ("cuarenta y uno")
    FILLER     = "FILLER"
    WORD       = "WORD"


@dataclass
class Token:
    kind: TokenKind
    value: str          # canonical/normalized value (e.g. "Cra.", "52", "3C")
    raw: str            # as seen after preprocess


@dataclass
class NumeroCore:
    digits: int
    letter: Optional[str] = None      # "A".."Z"
    bis: Optional[str] = None         # "Bis" | "Bis A" | "Bis B"
    cardinal: Optional[str] = None    # Norte|Sur|Este|Oeste|Oriente|Occidente

    def render(self) -> str:
        out = str(self.digits)
        if self.letter:
            out += self.letter
        if self.bis:
            out += f" {self.bis}"
        if self.cardinal:
            out += f" {self.cardinal}"
        return out


@dataclass
class Via:
    tipo: str                          # canonical emit, e.g. "Cra."
    numero: Optional[NumeroCore] = None


@dataclass
class Placa:
    cruce: Optional[NumeroCore] = None
    distancia: Optional[NumeroCore] = None


@dataclass
class AddressAST:
    kind: str                          # "DOOR" | "INTERSECTION" | "SEGMENT" | "NONE"
    via: Optional[Via] = None          # generadora
    placa: Optional[Placa] = None      # DOOR only
    via2: Optional[Via] = None         # INTERSECTION only
    place_text: Optional[str] = None   # NONE (non-street candidate)


@dataclass
class Repair:
    kind: RepairKind
    before: str
    after: str
    reason: str


@dataclass
class ParsedAddress:
    state: AddressState
    canonical: Optional[str]
    confidence: float
    repaired: bool
    repairs: list
    ast: AddressAST
    tokens: list
    components: dict
    invalid_reason: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Lexicon (verified data — single home; §4.2)
# ══════════════════════════════════════════════════════════════════════════════

# alias (accent-stripped, lowercase) → canonical emit (period-abbreviated).
VIA_ALIASES: dict[str, str] = {
    "calle": "Cl.", "cl": "Cl.", "cll": "Cl.", "clle": "Cl.",
    "carrera": "Cra.", "cra": "Cra.", "kra": "Cra.", "kr": "Cra.", "cr": "Cra.", "k": "Cra.",
    "avenida": "Av.", "av": "Av.", "avda": "Av.", "ave": "Av.",
    "diagonal": "Diag.", "diag": "Diag.", "dg": "Diag.",
    "transversal": "Tr.", "transv": "Tr.", "tv": "Tr.", "tr": "Tr.", "trans": "Tr.", "tranv": "Tr.",
    "circular": "Circ.", "circ": "Circ.",
    "circunvalar": "Circunv.", "circunv": "Circunv.",
    "pasaje": "Pje.", "pje": "Pje.", "pas": "Pje.",
    "autopista": "Autop.", "autop": "Autop.",
    "via": "Vía",
    "manzana": "Mz.", "mz": "Mz.", "mza": "Mz.", "mzn": "Mz.",
}

# The "already-canonical root" per canonical emit (accent-stripped, no period).
# If an input via alias equals this root, no ABBREVIATION_NORMALIZATION repair is
# recorded (case/period differences are formatting, not repairs) — keeps a
# canonical input repair-free and idempotent.
VIA_CANONICAL_ROOT: dict[str, str] = {
    "Cl.": "cl", "Cra.": "cra", "Av.": "av", "Diag.": "diag", "Tr.": "tr",
    "Circ.": "circ", "Circunv.": "circunv", "Pje.": "pje", "Autop.": "autop",
    "Vía": "via", "Mz.": "mz",
}

# Full-word render for the legacy WhatsApp/Nominatim path (§7.1 render_full).
VIA_FULL_WORD: dict[str, str] = {
    "Cl.": "Calle", "Cra.": "Carrera", "Av.": "Avenida", "Diag.": "Diagonal",
    "Tr.": "Transversal", "Circ.": "Circular", "Circunv.": "Circunvalar",
    "Pje.": "Pasaje", "Autop.": "Autopista", "Vía": "Vía", "Mz.": "Manzana",
    "Av. Cra.": "Avenida Carrera", "Av. Cl.": "Avenida Calle",
}

SEP_HASH_WORDS = {"numero", "numeral", "almohadilla", "gato", "nro", "no", "n"}
SEP_DASH_WORDS = {"guion", "raya", "menos"}

CARDINALS: dict[str, str] = {
    "norte": "Norte", "nte": "Norte",
    "sur": "Sur",
    "este": "Este", "oriente": "Oriente",
    "oeste": "Oeste", "occidente": "Occidente",
}

# Institution keywords → classify as PLACE_NAME (takes precedence over a landmark
# substring, e.g. "Centro Comercial Campanario").
PLACE_NAME_KEYWORDS = {
    "centro comercial", "universidad", "hospital", "clinica", "clínica",
    "colegio", "institucion educativa", "institución educativa", "terminal",
    "aeropuerto", "cc ", "sena", "ips", "eps",
}

_TENS = {"veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
         "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_UNITS = {"un": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
          "seis": 6, "siete": 7, "ocho": 8, "nueve": 9}
_ORDINALS = {
    "primera": 1, "primero": 1, "segunda": 2, "segundo": 2, "tercera": 3, "tercero": 3,
    "cuarta": 4, "cuarto": 4, "quinta": 5, "sexta": 6, "septima": 7, "octava": 8,
    "novena": 9, "decima": 10,
}
_TEENS = {
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
}

# Courtesy / preamble filler that may sit mid-string. Kept minimal; leading
# preamble is handled by address_utils._strip_preamble in stage 1.
FILLER_WORDS = {
    "por", "favor", "aqui", "aca", "en", "el", "la", "los", "las", "del", "de",
    "hola", "buenas", "buenos", "dias", "tardes", "noches", "mira", "vea", "oiga",
    "estoy", "necesito", "quiero", "un", "una", "taxi", "movil", "carro", "para",
    "que", "es",
}


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 — preprocess (§6)
# ══════════════════════════════════════════════════════════════════════════════

def _strip_accents(s: str) -> str:
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    return s


def _preprocess(text: str) -> str:
    """Fold case/accents/whitespace and strip leading courtesy preamble.

    No address structure is built here; only lexical folding. Canonical output is
    regenerated downstream, so losing input case/accents is harmless.
    """
    if not text:
        return ""
    t = text.strip()
    try:
        from core.address_utils import _strip_preamble
        t = _strip_preamble(t)
    except Exception:  # pragma: no cover - defensive; parser stays self-sufficient
        pass
    t = _strip_accents(t.lower())
    t = re.sub(r"[.,;:]", " ", t)     # drop sentence punctuation (keep # and -)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — tokenizer (regex ONLY for lexical recognition; §6, D6)
# ══════════════════════════════════════════════════════════════════════════════

_TOKEN_RE = re.compile(
    r"""
      (?P<glued>\d+[a-z]+\d+)      # 3c6, 8a14, 17b28
    | (?P<numletter>\d+[a-z]+)     # 73b, 3c, 4a
    | (?P<number>\d+)              # 52, 17
    | (?P<hash>\#)
    | (?P<dash>[-–])
    | (?P<word>[a-z]+)             # words / abbreviations (accents already stripped)
    """,
    re.VERBOSE,
)


def _tokenize(pre: str) -> list:
    """Split preprocessed text into raw (kind-hint, value) pairs."""
    raw_tokens = []
    for m in _TOKEN_RE.finditer(pre):
        kind = m.lastgroup
        raw_tokens.append((kind, m.group()))
    return raw_tokens


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 — lexical classification (§6)
# ══════════════════════════════════════════════════════════════════════════════

def _classify(raw_tokens: list, repairs: list) -> list:
    """Assign a TokenKind to each raw token; fold number-words and separators.

    Records ABBREVIATION_NORMALIZATION / NORMALIZED_SEPARATOR / REMOVED_FILLER as
    it canonicalizes lexical items. Compound number-words are merged here (token
    level, not regex string-rewriting).
    """
    # First pass: map raw tokens to Tokens (before number-word compounding).
    toks: list = []
    for kind, val in raw_tokens:
        if kind == "glued":
            toks.append(Token(TokenKind.GLUED, val.upper(), val))
        elif kind == "numletter":
            toks.append(Token(TokenKind.NUM_LETTER, val.upper(), val))
        elif kind == "number":
            toks.append(Token(TokenKind.NUMBER, val, val))
        elif kind == "hash":
            toks.append(Token(TokenKind.HASH, "#", val))
        elif kind == "dash":
            toks.append(Token(TokenKind.DASH, "-", val))
        else:  # word
            toks.append(_classify_word(val, repairs))

    # Second pass: fold compound / ordinal / teen number-words into NUMBER.
    return _fold_number_words(toks, repairs)


def _classify_word(val: str, repairs: list) -> Token:
    v = val  # already lowercased + accent-stripped
    if v in VIA_ALIASES:
        canon = VIA_ALIASES[v]
        if v != VIA_CANONICAL_ROOT.get(canon):
            repairs.append(Repair(RepairKind.ABBREVIATION_NORMALIZATION, v, canon,
                                  "via abbreviation normalized"))
        return Token(TokenKind.VIA, canon, val)
    if v == "con":
        return Token(TokenKind.CON, "con", val)
    if v == "bis":
        return Token(TokenKind.BIS, "Bis", val)
    if v == "y":
        return Token(TokenKind.Y, "y", val)
    if v in CARDINALS:
        canon = CARDINALS[v]
        if v != canon.lower():
            repairs.append(Repair(RepairKind.ABBREVIATION_NORMALIZATION, v, canon,
                                  "cardinal normalized"))
        return Token(TokenKind.CARDINAL, canon, val)
    if v in SEP_HASH_WORDS:
        repairs.append(Repair(RepairKind.NORMALIZED_SEPARATOR, v, "#",
                              "spoken numeral -> #"))
        return Token(TokenKind.HASH, "#", val)
    if v in SEP_DASH_WORDS:
        repairs.append(Repair(RepairKind.NORMALIZED_SEPARATOR, v, "-",
                              "spoken separator -> -"))
        return Token(TokenKind.DASH, "-", val)
    if len(v) == 1 and v.isalpha():
        return Token(TokenKind.LETTER, v.upper(), val)
    if v in FILLER_WORDS:
        return Token(TokenKind.FILLER, v, val)
    return Token(TokenKind.WORD, val, val)


def _fold_number_words(toks: list, repairs: list) -> list:
    out: list = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.kind in (TokenKind.WORD, TokenKind.FILLER):
            w = t.raw
            # compound tens + "y" + unit
            if (w in _TENS and i + 2 < n and toks[i + 1].kind == TokenKind.Y
                    and toks[i + 2].raw in _UNITS):
                val = _TENS[w] + _UNITS[toks[i + 2].raw]
                repairs.append(Repair(RepairKind.ABBREVIATION_NORMALIZATION,
                                      f"{w} y {toks[i + 2].raw}", str(val),
                                      "number word -> digit"))
                out.append(Token(TokenKind.NUMBER, str(val), w))
                i += 3
                continue
            mapped = _ORDINALS.get(w) or _TEENS.get(w) or _TENS.get(w) or _UNITS.get(w)
            if mapped is not None:
                repairs.append(Repair(RepairKind.ABBREVIATION_NORMALIZATION, w,
                                      str(mapped), "number word -> digit"))
                out.append(Token(TokenKind.NUMBER, str(mapped), w))
                i += 1
                continue
        out.append(t)
        i += 1
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4 — structural parser: tokens → AST (no repair here; §6.1)
# ══════════════════════════════════════════════════════════════════════════════

def _read_numero_core(toks: list, i: int, repairs: list) -> tuple:
    """Read a numero_core beginning at index i. Returns (NumeroCore|None, next_i)."""
    n = len(toks)
    if i >= n:
        return None, i
    t = toks[i]
    if t.kind == TokenKind.NUMBER:
        core = NumeroCore(digits=int(t.value))
        i += 1
    elif t.kind == TokenKind.NUM_LETTER:
        m = re.match(r"(\d+)([A-Z]+)", t.value)
        core = NumeroCore(digits=int(m.group(1)), letter=m.group(2))
        i += 1
    else:
        return None, i
    # optional standalone spelled letter directly after the digits (3 C -> 3C)
    if core.letter is None and i < n and toks[i].kind == TokenKind.LETTER:
        core.letter = toks[i].value
        repairs.append(Repair(RepairKind.LETTER_ATTACHMENT, toks[i].raw,
                              f"{core.digits}{core.letter}", "spaced letter attached"))
        i += 1
    # optional Bis [letter]
    if i < n and toks[i].kind == TokenKind.BIS:
        bis = "Bis"
        i += 1
        if i < n and toks[i].kind == TokenKind.LETTER:
            bis = f"Bis {toks[i].value}"
            i += 1
        core.bis = bis
    # optional cardinal
    if i < n and toks[i].kind == TokenKind.CARDINAL:
        core.cardinal = toks[i].value
        i += 1
    return core, i


def _parse(toks: list, repairs: list) -> AddressAST:
    """Build the AST. Structure as-heard; repairs that MATERIALIZE structure
    (attach placa, rebuild order, split glued) are recorded as they are applied,
    but the final canonical string is produced only from this AST (stage 7)."""
    n = len(toks)
    # locate the first via type
    first_via_idx = next((k for k, t in enumerate(toks) if t.kind == TokenKind.VIA), None)

    if first_via_idx is None:
        # No via type. If there is address-ish signal, it's an incomplete address;
        # otherwise a non-street candidate name.
        has_addr_signal = any(
            t.kind in (TokenKind.HASH, TokenKind.DASH, TokenKind.GLUED,
                       TokenKind.NUM_LETTER)
            for t in toks
        ) or sum(1 for t in toks if t.kind == TokenKind.NUMBER) >= 1
        if has_addr_signal and any(t.kind in (TokenKind.HASH, TokenKind.DASH,
                                              TokenKind.GLUED, TokenKind.NUM_LETTER)
                                   for t in toks):
            return AddressAST(kind="SEGMENT")   # will validate to missing_tipo_via
        place = " ".join(t.raw for t in toks).strip()
        return AddressAST(kind="NONE", place_text=place or None)

    tipo1 = toks[first_via_idx].value
    core1, j = _read_numero_core(toks, first_via_idx + 1, repairs)
    via1 = Via(tipo=tipo1, numero=core1)

    ast = AddressAST(kind="DOOR", via=via1)
    cruce_from_via: Optional[NumeroCore] = None
    placa_cores: list = []
    seen_hash = False

    while j < n:
        t = toks[j]
        if t.kind in (TokenKind.FILLER, TokenKind.WORD, TokenKind.Y, TokenKind.LETTER):
            j += 1
            continue
        if t.kind == TokenKind.CON:
            via2_core, k = _read_numero_core(toks, j + 2, repairs) if (
                j + 1 < n and toks[j + 1].kind == TokenKind.VIA) else (None, j + 1)
            if j + 1 < n and toks[j + 1].kind == TokenKind.VIA:
                ast.kind = "INTERSECTION"
                ast.via2 = Via(tipo=toks[j + 1].value, numero=via2_core)
                j = k
                continue
            j += 1
            continue
        if t.kind == TokenKind.VIA:
            core, k = _read_numero_core(toks, j + 1, repairs)
            if core is not None:
                cruce_from_via = core
                repairs.append(Repair(RepairKind.STREET_ORDER_REBUILD,
                                      f"{t.value} {core.render()}", f"#{core.render()}",
                                      "second via number -> cruce"))
                j = k
                continue
            # via type with no number → filler
            repairs.append(Repair(RepairKind.REMOVED_FILLER, t.raw, "",
                                  "via keyword without number dropped"))
            j += 1
            continue
        if t.kind == TokenKind.HASH:
            seen_hash = True
            j += 1
            continue
        if t.kind == TokenKind.DASH:
            j += 1
            continue
        if t.kind == TokenKind.GLUED:
            cruce_c, dist_c = _split_glued(t, repairs)
            placa_cores.append(cruce_c)
            placa_cores.append(dist_c)
            j += 1
            continue
        if t.kind in (TokenKind.NUMBER, TokenKind.NUM_LETTER):
            core, j = _read_numero_core(toks, j, repairs)
            if core is not None:
                placa_cores.append(core)
            continue
        j += 1

    # assemble placa from what was found
    if ast.kind == "INTERSECTION":
        return ast

    cruce = cruce_from_via if cruce_from_via is not None else (
        placa_cores[0] if placa_cores else None)
    if cruce_from_via is not None:
        distancia = placa_cores[0] if placa_cores else None
    else:
        distancia = placa_cores[1] if len(placa_cores) >= 2 else None

    # ── Recuperación de placa pegada por STT (task 3) ──
    # "#1725" → "#17-25": el STT eliminó el separador. Alta confianza: hay '#',
    # una placa cruce numérica de 4 dígitos SIN letra/bis/cardinal y SIN
    # distancia, y no se vio otro separador. Una placa colombiana válida SIEMPRE
    # lleva separador; "#NNNN" no es una placa válida → se restaura partiendo 2-2.
    # Solo placas numéricas: letras (17A-25, 67E-20) y los casos ya con separador
    # NUNCA se tocan (llegan con distancia ya definida o vía _split_glued).
    if (
        seen_hash
        and cruce is not None
        and distancia is None
        and cruce.letter is None
        and cruce.bis is None
        and cruce.cardinal is None
    ):
        _digs = str(cruce.digits)
        if len(_digs) == 4:
            new_cruce = NumeroCore(digits=int(_digs[:2]))
            new_dist = NumeroCore(digits=int(_digs[2:]))
            repairs.append(Repair(
                RepairKind.PLACA_SEPARATOR_RECOVERY,
                f"#{cruce.render()}",
                f"#{new_cruce.render()}-{new_dist.render()}",
                "glued numeric placa split (STT dropped '-')",
            ))
            cruce, distancia = new_cruce, new_dist

    if cruce is not None and distancia is not None:
        if not seen_hash:
            repairs.append(Repair(RepairKind.NUMBER_ATTACHMENT,
                                  f"{cruce.render()} {distancia.render()}",
                                  f"#{cruce.render()}-{distancia.render()}",
                                  "materialized # and - separators"))
        ast.placa = Placa(cruce=cruce, distancia=distancia)
    else:
        ast.placa = Placa(cruce=cruce, distancia=distancia)
        ast.kind = "SEGMENT" if (cruce is None and distancia is None) else "DOOR"
    return ast


def _split_glued(t: Token, repairs: list) -> tuple:
    """Split a digit-letter-digit glued token: 3C6 -> (3C, 6); 8A14 -> (8A, 14)."""
    m = re.match(r"(\d+)([A-Z]+)(\d+)", t.value)
    cruce = NumeroCore(digits=int(m.group(1)), letter=m.group(2))
    dist = NumeroCore(digits=int(m.group(3)))
    repairs.append(Repair(RepairKind.TOKEN_SPLIT, t.raw,
                          f"{cruce.render()}-{dist.render()}",
                          "glued digit-letter-digit split into cruce/placa"))
    return cruce, dist


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5/6 — merge compound via + validate → AddressState (§6.3)
# ══════════════════════════════════════════════════════════════════════════════

def _validate(ast: AddressAST) -> tuple:
    """Return (AddressState, invalid_reason|None). AST is already built + repaired."""
    if ast.kind == "INTERSECTION":
        if ast.via and ast.via2:
            return AddressState.INTERSECTION, None
        return AddressState.INVALID_ADDRESS_STRUCTURE, "ambiguous_multiple_via"

    if ast.kind == "NONE":
        return AddressState.UNKNOWN, None   # non-street; refined by classify()

    # SEGMENT with address signal but no via type
    if ast.kind == "SEGMENT" and ast.via is None:
        return AddressState.INVALID_ADDRESS_STRUCTURE, "missing_tipo_via"

    via = ast.via
    if via is None:
        return AddressState.INVALID_ADDRESS_STRUCTURE, "missing_tipo_via"
    if via.numero is None:
        return AddressState.INVALID_ADDRESS_STRUCTURE, "ambiguous_multiple_via"

    placa = ast.placa
    if placa is None or (placa.cruce is None and placa.distancia is None):
        return AddressState.INVALID_ADDRESS_STRUCTURE, "segment_without_placa"
    if placa.cruce is not None and placa.distancia is None:
        return AddressState.INVALID_ADDRESS_STRUCTURE, "missing_placa_distance"
    if placa.cruce is None:
        return AddressState.INVALID_ADDRESS_STRUCTURE, "segment_without_placa"

    return AddressState.STREET_ADDRESS, None


# ══════════════════════════════════════════════════════════════════════════════
# Stage 7 — reconstruction from AST only (§6.4)
# ══════════════════════════════════════════════════════════════════════════════

def _reconstruct(ast: AddressAST) -> str:
    if ast.kind == "INTERSECTION":
        return f"{ast.via.tipo} {ast.via.numero.render()} con {ast.via2.tipo} {ast.via2.numero.render()}"
    return f"{ast.via.tipo} {ast.via.numero.render()} #{ast.placa.cruce.render()}-{ast.placa.distancia.render()}"


def render_full(parsed: ParsedAddress) -> str:
    """Full-word render for the legacy WhatsApp/Nominatim path (§7.1).
    Pure formatter over the AST — no parsing logic outside the parser."""
    ast = parsed.ast
    if ast is None or ast.via is None or ast.via.numero is None:
        return parsed.canonical or ""

    def full(tipo: str) -> str:
        return VIA_FULL_WORD.get(tipo, tipo)

    if ast.kind == "INTERSECTION" and ast.via2 and ast.via2.numero:
        return (f"{full(ast.via.tipo)} {ast.via.numero.render()} con "
                f"{full(ast.via2.tipo)} {ast.via2.numero.render()}")
    if ast.placa and ast.placa.cruce and ast.placa.distancia:
        return (f"{full(ast.via.tipo)} {ast.via.numero.render()} "
                f"#{ast.placa.cruce.render()}-{ast.placa.distancia.render()}")
    # via-only (incomplete) → full-word via, preserving the legacy
    # normalize_address contract ("cra 5" → "Carrera 5"). Caller falls back to
    # the original text only when there is no via at all.
    return f"{full(ast.via.tipo)} {ast.via.numero.render()}"


# ══════════════════════════════════════════════════════════════════════════════
# Compound via recognition (Av. + Cra./Cl.) — token level, before parse
# ══════════════════════════════════════════════════════════════════════════════

def _merge_compound_via(toks: list, repairs: list) -> list:
    """Merge 'Av.' immediately followed by 'Cra.'/'Cl.' (no number between) into
    the compound generating road 'Av. Cra.' / 'Av. Cl.'."""
    out: list = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if (t.kind == TokenKind.VIA and t.value == "Av." and i + 1 < n
                and toks[i + 1].kind == TokenKind.VIA
                and toks[i + 1].value in ("Cra.", "Cl.")):
            compound = f"Av. {toks[i + 1].value}"
            out.append(Token(TokenKind.VIA, compound, f"{t.raw} {toks[i + 1].raw}"))
            i += 2
            continue
        out.append(t)
        i += 1
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Non-street classification (delegates to catalog infra; §9, D8)
# ══════════════════════════════════════════════════════════════════════════════

def _classify_nonstreet(text: str) -> tuple:
    """Return (AddressState, canonical|None) for a non-street candidate."""
    t = (text or "").strip()
    if len(t) < 3:
        return AddressState.UNKNOWN, None

    low = _strip_accents(t.lower())
    for kw in PLACE_NAME_KEYWORDS:
        if kw in low:
            return AddressState.PLACE_NAME, t

    try:
        from core.address_utils import _try_local_match, looks_like_place
    except Exception:  # pragma: no cover
        return AddressState.UNKNOWN, None

    canon = _try_local_match(t)
    if canon:
        if _is_landmark(canon):
            return AddressState.LANDMARK, canon
        return AddressState.NEIGHBORHOOD, canon

    if looks_like_place(t):
        return AddressState.PLACE_NAME, t
    return AddressState.UNKNOWN, None


def _is_landmark(name: str) -> bool:
    try:
        from tools.popayan_geodata import LANDMARKS
    except Exception:  # pragma: no cover
        return False
    target = _strip_accents(name.lower().strip())
    for lm in LANDMARKS:
        if _strip_accents(str(lm).lower().strip()) == target:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Engine — parse_co_address (§6)
# ══════════════════════════════════════════════════════════════════════════════

_REPAIR_COST = {
    RepairKind.NORMALIZED_SEPARATOR: 0.0,
    RepairKind.ABBREVIATION_NORMALIZATION: 0.0,
    RepairKind.REMOVED_FILLER: 0.0,
    RepairKind.LETTER_ATTACHMENT: 0.05,
    RepairKind.NUMBER_ATTACHMENT: 0.05,
    RepairKind.STREET_ORDER_REBUILD: 0.05,
    RepairKind.TOKEN_MERGE: 0.10,
    RepairKind.TOKEN_SPLIT: 0.15,
    RepairKind.PLACA_SEPARATOR_RECOVERY: 0.10,
}


def _components(ast: AddressAST) -> dict:
    comp: dict = {"tipo": None, "numero": None, "cruce": None, "distancia": None,
                  "letter": None, "bis": None, "cardinal": None}
    if ast.via:
        comp["tipo"] = ast.via.tipo
        if ast.via.numero:
            comp["numero"] = ast.via.numero.digits
            comp["letter"] = ast.via.numero.letter
            comp["bis"] = ast.via.numero.bis
            comp["cardinal"] = ast.via.numero.cardinal
    if ast.placa:
        if ast.placa.cruce:
            comp["cruce"] = ast.placa.cruce.digits
        if ast.placa.distancia:
            comp["distancia"] = ast.placa.distancia.digits
    return comp


def parse_co_address(text: str) -> ParsedAddress:
    repairs: list = []

    pre = _preprocess(text)
    raw_tokens = _tokenize(pre)
    toks = _classify(raw_tokens, repairs)
    toks = _merge_compound_via(toks, repairs)
    ast = _parse(toks, repairs)
    state, reason = _validate(ast)

    canonical: Optional[str] = None
    if state in (AddressState.STREET_ADDRESS, AddressState.INTERSECTION):
        canonical = _reconstruct(ast)
    elif state == AddressState.UNKNOWN and ast.kind == "NONE":
        # non-street candidate → delegate classification
        ns_state, ns_canon = _classify_nonstreet(ast.place_text or pre)
        state = ns_state
        canonical = ns_canon
        reason = None

    confidence = max(0.0, 1.0 - sum(_REPAIR_COST.get(r.kind, 0.0) for r in repairs))
    parsed = ParsedAddress(
        state=state,
        canonical=canonical,
        confidence=round(confidence, 3),
        repaired=bool(repairs),
        repairs=repairs,
        ast=ast,
        tokens=toks,
        components=_components(ast),
        invalid_reason=reason if state == AddressState.INVALID_ADDRESS_STRUCTURE else None,
    )
    _log(text, pre, toks, ast, repairs, state, canonical, confidence)
    return parsed


def _log(raw, pre, toks, ast, repairs, state, canonical, conf):
    logger.info("[co_addr] state=%s conf=%.2f repaired=%s repairs=%d raw=%r canonical=%r",
                state.value, conf, bool(repairs), len(repairs), raw, canonical)
    if logger.isEnabledFor(logging.DEBUG):
        tok_s = " ".join(f"{t.kind.name}({t.value})" for t in toks)
        rep_s = " · ".join(f"{r.kind.name}({r.before}->{r.after})" for r in repairs) or "—"
        logger.debug(
            "RAW       : %r\nPREPROCESS: %r\nTOKENS    : %s\nAST       : %s\n"
            "REPAIRS   : %s\nCANONICAL : %s\nSTATE     : %s (conf=%.2f)",
            raw, pre, tok_s, ast, rep_s, canonical or "—", state.value, conf,
        )


__all__ = ["parse_co_address", "ParsedAddress", "AddressState", "RepairKind",
           "render_full"]
