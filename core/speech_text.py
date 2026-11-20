"""
core/speech_text.py — El texto que se escucha, a partir del que se lee.

La respuesta de Lyra se escribe para leerse: markdown, cifras con separadores,
"$9.000 COP", "87,3 %", "15/11/2026", "· vence el 01/12", identificadores con
guion bajo, marcadores internos [BIZ:12]. Un motor de voz lee todo eso tal cual:
"guion bajo", "nueve, cero, cero, cero COP peso", "tres de la mañana p. m.".

`to_speech()` produce la versión hablada SIN tocar la escrita ni los datos: se
aplica sólo en la frontera de la voz. Cada cifra se dice según lo que es por su
contexto inmediato:

    dinero      "$9.000 COP", "9000 COP", "$ 120.000"   → "nueve mil pesos"
    porcentaje  "87,3 %"                                 → "ochenta y siete coma tres por ciento"
    fecha       "15/11/2026", "2026-11-15", "15/11"      → "quince de noviembre de dos mil veintiséis"
    hora        "3:00 p. m.", "15:30"                    → "las tres de la tarde"
    decimal     "4,5", "2,9 km", "⭐ 4.8"                → "cuatro coma cinco", "kilómetros", "estrellas"
    cantidad    "24 registros", "5 contratos"            → "veinticuatro registros"
    código      "ficha 2558", "contrato CT-24", celulares → se dejan (no se pronuncian como cantidad)

Español de Colombia: la moneda por defecto es el peso y no se repite ("COP
pesos"); la hora va con franja ("de la tarde").

Sólo se aplica a NexiService y SchoolSena (`SPEECH_PROJECTS`); la telefonía y
los demás proyectos siguen con su limpieza de siempre.
"""

from __future__ import annotations

import re

from core.speech_format import spell_number_es

#: Proyectos cuya voz pasa por esta normalización.
SPEECH_PROJECTS = {"nexiservice", "schoolsena"}

# ── Marcas internas y formato ─────────────────────────────────────────────────

_INTERNAL = re.compile(r"\[[A-ZÁÉÍÓÚÑ_ ]{2,}(?::[^\]]*)?\]")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_BOLD = re.compile(r"(\*{1,2}|_{1,2})(\S(?:.*?\S)?)\1")
_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^\s*#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s*>\s?", re.M)
_BULLET = re.compile(r"^\s*(?:[-•·*]|\d+[.)])\s+", re.M)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️⃣]", re.UNICODE)

# Identificadores técnicos: snake_case y camelCase.
_SNAKE = re.compile(r"\b([A-Za-zÁÉÍÓÚáéíóúñÑ]+(?:_[A-Za-zÁÉÍÓÚáéíóúñÑ0-9]+)+)\b")
_CAMEL = re.compile(r"\b([a-záéíóúñ]+(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)\b")

# ── Cifras según su contexto ──────────────────────────────────────────────────

_NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
_MONEY = re.compile(
    rf"(?:\$\s?(?P<a>{_NUM})(?:\s*(?:COP|pesos?))?)|(?:(?P<b>{_NUM})\s*(?:COP|pesos?)\b)|(?:\bCOP\s*\$?\s?(?P<c>{_NUM}))",
    re.I)
_PERCENT = re.compile(rf"(?P<n>{_NUM})\s?%")
_DATE_DMY = re.compile(r"\b(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{2,4}))?\b")
_DATE_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?\b")
_TIME = re.compile(r"\b(?P<h>\d{1,2}):(?P<mi>\d{2})(?::\d{2})?\s*(?P<ap>[ap]\.?\s?m\.?)?(?![\w:])", re.I)
_RANGE_TIME = re.compile(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})")
_STARS = re.compile(rf"⭐\s*(?P<n>{_NUM})")
_UNITS = {"km": "kilómetros", "m": "metros", "min": "minutos", "h": "horas", "hrs": "horas", "kg": "kilos"}
_UNIT = re.compile(rf"(?P<n>{_NUM})\s?(?P<u>km|min|hrs|kg|h|m)\b")
# Palabras tras las cuales un número es un identificador y no una cantidad.
_ID_HEADS = ("ficha", "fichas", "codigo", "código", "contrato", "actividad", "documento", "cedula", "cédula",
             "nit", "telefono", "teléfono", "celular", "whatsapp", "numero", "número", "no.", "nº", "#",
             "id", "radicado", "orden", "factura", "calle", "carrera", "cra", "cl", "avenida", "diagonal",
             "transversal", "tv", "dg", "kr", "ak", "ac")
_ID_HEAD_RE = re.compile(r"(?:^|[\s(])(?:" + "|".join(re.escape(h) for h in _ID_HEADS) + r")\s*#?\s*$", re.I)
_PLAIN_INT = re.compile(r"(?<![\w.,/:#-])(\d{1,6})(?![\w.,/:-]|\d)")
_DECIMAL = re.compile(r"(?<![\w.,/])(\d+),(\d+)(?![\w.,/])")

#: Sustantivos masculinos terminados en -a (los demás en -a son femeninos).
_MASC_A = {"dia", "día", "mapa", "problema", "sistema", "programa", "tema", "clima", "idioma",
           "planeta", "esquema", "dilema", "drama", "poema", "aroma", "cupo"}

_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
           "octubre", "noviembre", "diciembre"]


def _to_int(raw: str) -> tuple[int, str]:
    """'9.000' → (9000, ''); '87,3' → (87, '3'); '4.8' → (4, '8') si no es millar."""
    raw = raw.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", raw):
        ent, _, dec = raw.partition(",")
        return int(ent.replace(".", "")), dec
    if "," in raw:
        ent, _, dec = raw.partition(",")
        return int(ent), dec
    if "." in raw:
        ent, _, dec = raw.partition(".")
        return int(ent), dec
    return int(raw), ""


def _say_number(raw: str) -> str:
    n, dec = _to_int(raw)
    words = spell_number_es(n)
    if dec and int(dec) != 0:
        dec_words = " ".join(spell_number_es(int(d)) for d in dec) if dec.startswith("0") else spell_number_es(int(dec))
        words += f" coma {dec_words}"
    return words


def _money(m: re.Match) -> str:
    raw = m.group("a") or m.group("b") or m.group("c")
    n, dec = _to_int(raw)
    words = spell_number_es(n)
    if words.endswith("uno"):
        words = words[:-1]                          # "veintiún pesos"
    if n and n % 1_000_000 == 0:
        words += " de"                              # "dos millones de pesos"
    return f"{words} {'peso' if n == 1 else 'pesos'}"


def _date(d: str, m: str, y: str | None) -> str:
    try:
        di, mi = int(d), int(m)
        if not (1 <= di <= 31 and 1 <= mi <= 12):
            return None
    except ValueError:
        return None
    day = "primero" if di == 1 else spell_number_es(di)
    out = f"{day} de {_MONTHS[mi - 1]}"
    if y:
        yi = int(y) + (2000 if len(y) == 2 else 0)
        out += f" de {spell_number_es(yi)}"
    return out


def _time(m: re.Match) -> str:
    h, mi = int(m.group("h")), int(m.group("mi"))
    ap = (m.group("ap") or "").lower().replace(".", "").replace(" ", "")
    if h > 23 or mi > 59:
        return m.group(0)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    franja = "de la mañana" if h < 12 else ("de la tarde" if h < 19 else "de la noche")
    h12 = h % 12 or 12
    hora = "una" if h12 == 1 else spell_number_es(h12)
    if mi == 0:
        minutos = ""
    elif mi == 30:
        minutos = " y media"
    elif mi == 15:
        minutos = " y cuarto"
    else:
        minutos = f" y {spell_number_es(mi)}"
    if h == 12 and mi == 0:
        return "las doce del mediodía"
    return f"{'la' if h12 == 1 else 'las'} {hora}{minutos} {franja}"


def _identifier(m: re.Match) -> str:
    words = m.group(1).replace("_", " ").split()
    words = [re.sub(r"cion$", "ción", w, flags=re.I) for w in words]
    return " ".join(w.lower() if i else w for i, w in enumerate(words))


def _camel(m: re.Match) -> str:
    return re.sub(r"(?<!^)([A-ZÁÉÍÓÚÑ])", lambda x: " " + x.group(1).lower(), m.group(1))


def _plain_int(text: str) -> str:
    """Cantidades sueltas a palabras, salvo cuando el número es un identificador."""
    out, last = [], 0
    for m in _PLAIN_INT.finditer(text):
        before = text[max(0, m.start() - 14):m.start()]
        raw = m.group(1)
        if _ID_HEAD_RE.search(before) or (len(raw) >= 5 and not raw.startswith("0") and False):
            continue
        out.append(text[last:m.start()])
        words = spell_number_es(int(raw))
        if words.endswith("uno"):
            # Ante un sustantivo, "uno" se apocopa y concuerda: "un contrato",
            # "una opción", "veintiún días".
            nxt = re.match(r"\s+([a-záéíóúñ]+)", text[m.end():], re.I)
            if nxt:
                noun = nxt.group(1).lower()
                sing = noun[:-1] if noun.endswith("s") and not noun.endswith(("ones", "des")) else noun
                fem = (sing.endswith(("a", "ión", "dad")) or noun.endswith(("iones", "dades"))) \
                    and sing.rstrip("s") not in _MASC_A
                words = words[:-3] + ("una" if fem else ("ún" if words != "uno" else "un"))
        out.append(words)
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def to_speech(text: str) -> str:
    """Versión hablada de una respuesta escrita (los datos y lo escrito no cambian)."""
    if not text:
        return text
    # Una lista larga no se dice entera (los 3 primeros y cuántos quedan). Va
    # antes que nada: después de quitar viñetas y saltos ya no se reconoce.
    from core.speech_format import condense_lists_for_speech
    t = condense_lists_for_speech(text)
    t = _INTERNAL.sub("", t)
    t = _LINK.sub(r"\1", t)
    t = _CODE.sub(r"\1", t)
    # Los identificadores van antes que la cursiva: "total_nomina_mensual" no
    # es "_nomina_" en cursiva.
    t = _SNAKE.sub(_identifier, t)
    t = _CAMEL.sub(_camel, t)
    for _ in range(2):
        t = _BOLD.sub(r"\2", t)
    t = _HEADING.sub("", t)
    t = _QUOTE.sub("", t)
    # La estrella dice qué es la cifra: se lee antes de quitar los emoji.
    t = _STARS.sub(lambda m: f"{_say_number(m.group('n'))} estrellas", t)
    t = _EMOJI.sub("", t)

    # Teléfonos escritos por grupos ("315 661 4275", "+57 315…") se dejan como
    # están: leídos como cantidades son "trescientos quince seiscientos…".
    phones: list = []

    def _keep_phone(m: re.Match) -> str:
        phones.append(m.group(0))
        return f"\x00P{len(phones) - 1}\x00"

    t = re.sub(r"(?:\+?57[\s-]?)?\b\d{3}[\s-]\d{3}[\s-]\d{4}\b", _keep_phone, t)
    t = re.sub(r"\b24\s?/\s?7\b", "las veinticuatro horas, todos los días", t)

    # Cifras, de lo más específico a lo más general.
    t = _MONEY.sub(_money, t)
    t = _PERCENT.sub(lambda m: f"{_say_number(m.group('n'))} por ciento", t)
    t = _DATE_ISO.sub(lambda m: _date(m.group("d"), m.group("m"), m.group("y")) or m.group(0), t)
    t = _DATE_DMY.sub(lambda m: _date(m.group("d"), m.group("m"), m.group("y")) or m.group(0), t)
    t = _RANGE_TIME.sub(lambda m: f"{m.group(1)} a {m.group(2)}", t)
    t = _TIME.sub(_time, t)
    # "a las las tres": la hora ya trae su artículo.
    t = re.sub(r"\b(?:la|las)\s+(la|las)\b(?=\s+\w+\s+(?:y\s+\w+\s+)?de\s+la\s+(?:mañana|tarde|noche)|\s+doce del)",
               r"\1", t)
    t = _UNIT.sub(lambda m: f"{_say_number(m.group('n'))} {_UNITS[m.group('u')]}", t)
    t = _DECIMAL.sub(lambda m: _say_number(f"{m.group(1)},{m.group(2)}"), t)
    # "# 31" en una dirección se dice "número".
    t = re.sub(r"#\s*(?=\d)", "número ", t)
    t = re.sub(r"\bCOP\b", "pesos colombianos", t)
    t = _plain_int(t)

    # Puntuación que no se pronuncia, y listas como pausas.
    t = _BULLET.sub("", t)
    t = re.sub(r"\s*[·|•]\s*", ", ", t)
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = re.sub(r"[«»\"]", "", t)
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"([.!?¿¡,;:])\s*\.(?=\s|$)", r"\1", t)   # "?." y ",." de los saltos
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    for i, phone in enumerate(phones):
        t = t.replace(f"\x00P{i}\x00", phone)
    return t.strip()
