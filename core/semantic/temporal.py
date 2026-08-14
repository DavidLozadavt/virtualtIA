"""
core/semantic/temporal.py — Autoridad única para leer el tiempo del habla.

Una hora y una fecha se dicen de muchas maneras, y hasta ahora cada capa las
reconocía por su cuenta: el router con sus regex, el bloque de citas con las
suyas, el interceptor raspando el historial. Cuando tres sitios interpretan lo
mismo con reglas distintas, tarde o temprano discrepan — y discreparon: "a las 9
de la mañana" salía como *día de mañana*, y "8;30" no salía en absoluto.

Aquí se lee una sola vez y se devuelve un resultado tipado que dice también
CUÁNTA precisión trae. Esa distinción importa: "a las 3" es una hora; "después
del almuerzo" es una franja, y con una franja no se agenda — se pregunta.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from core.semantic.morphology import normalize as _normalize_text


def normalize(text: str, keep_punctuation: bool = False) -> str:
    """
    Normaliza y además pliega la ñ.

    `morphology.normalize` conserva la ñ a propósito: en español distingue
    palabras. Pero el vocabulario del calendario es cerrado y se escribe de las
    dos maneras —"mañana" y "manana", según venga del teclado o del STT—, así
    que aquí conviene que ambas caigan en la misma forma.
    """
    return _normalize_text(text, keep_punctuation=keep_punctuation).replace("ñ", "n")


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. RESULTADO
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TimeReading:
    """Una hora leída del mensaje."""
    #: "HH:MM" cuando la hora es exacta; None si sólo se nombró una franja.
    value: Optional[str] = None
    #: Franja del día nombrada sin hora concreta: "manana", "tarde", "noche".
    daypart: Optional[str] = None
    #: Texto que la produjo, para poder repetírselo al usuario.
    literal: str = ""

    @property
    def exact(self) -> bool:
        return bool(self.value)

    @property
    def vague(self) -> bool:
        return not self.value and bool(self.daypart)

    def __bool__(self) -> bool:
        return bool(self.value or self.daypart)


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. HORA
# ═══════════════════════════════════════════════════════════════════════════════

#: Franjas del día y el meridiano que implican. La hora central es a la que se
#: traduce una franja cuando el usuario acepta que se la concretemos.
DAYPARTS = {
    "madrugada": ("am", 5),
    "manana":    ("am", 9),
    "mediodia":  ("pm", 12),
    "tarde":     ("pm", 15),
    "noche":     ("pm", 19),
}

#: Locuciones que sitúan sin nombrar la franja. Se traducen a franja, no a hora:
#: "después del almuerzo" es la tarde, pero no son las 15:00 exactas.
VAGUE_MOMENTS = (
    ("despues del almuerzo", "tarde"),
    ("despues de almorzar",  "tarde"),
    ("antes del almuerzo",   "manana"),
    ("a la hora del almuerzo", "mediodia"),
    ("al mediodia",          "mediodia"),
    ("mas tarde",            "tarde"),
    ("por la manana",        "manana"),
    ("en la manana",         "manana"),
    ("de la manana",         "manana"),
    ("por la tarde",         "tarde"),
    ("en la tarde",          "tarde"),
    ("de la tarde",          "tarde"),
    ("por la noche",         "noche"),
    ("en la noche",          "noche"),
    ("de la noche",          "noche"),
    ("temprano",             "manana"),
)

#: "de la mañana" es meridiano, no el día siguiente. Sin esta distinción, "a las
#: 9 de la mañana" agendaba para mañana sin que nadie lo pidiera.
_MORNING_AS_MERIDIEM = re.compile(r"\b(?:de|por|en)\s+la\s+manana\b")

_TIME_PATTERNS = (
    # "a las 8:30", "a las 8;30" — el separador aparece como sea, y la
    # normalización previa a veces lo borra del todo.
    r"a\s+las?\s+(\d{1,2})[:;.\-](\d{2})\s*(am|pm)?",
    r"\b(\d{1,2})[:;.\-](\d{2})\s*(am|pm)?",
    r"\b(\d{1,2})(\d{2})\s*(am|pm)\b",
    r"a\s+las?\s+(\d{1,2})(\d{2})\b\s*(am|pm)?",
    r"a\s+las?\s+(\d{1,2})()\s*(am|pm)?(?!\d)",
    r"\b(\d{1,2})()\s*(am|pm)\b",
)

#: Numerales escritos con letra, tal como se dicen las horas.
_SPELLED_HOURS = {
    "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
}


def read_time(text: str) -> TimeReading:
    """
    Lee la hora de un texto en español hablado o escrito.

    Devuelve una hora exacta cuando el mensaje la da, y una franja cuando sólo
    la insinúa. Quien llame decide qué hacer con cada caso: agendar o preguntar.
    """
    t = normalize(text or "", keep_punctuation=True)
    if not t:
        return TimeReading()

    daypart = _daypart_of(t)
    meridiem_hint = DAYPARTS.get(daypart or "", (None, None))[0]

    match = None
    for pattern in _TIME_PATTERNS:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            break

    if not match:
        spelled = _spelled_hour(t)
        if spelled is not None:
            return TimeReading(
                value=_to_24h(spelled, "00", None, meridiem_hint),
                daypart=daypart,
                literal=t,
            )
        # Sin cifras: lo único que queda es la franja, si la hay.
        return TimeReading(daypart=daypart, literal=t)

    try:
        hour = int(match.group(1))
    except (TypeError, ValueError):
        return TimeReading(daypart=daypart, literal=t)

    minutes = match.group(2) or "00"
    if not 0 <= hour <= 23 or not 0 <= int(minutes) <= 59:
        return TimeReading(daypart=daypart, literal=t)

    return TimeReading(
        value=_to_24h(hour, minutes, (match.group(3) or "").lower(), meridiem_hint),
        daypart=daypart,
        literal=match.group(0).strip(),
    )


def _to_24h(hour: int, minutes: str, meridiem: Optional[str], hint: Optional[str]) -> str:
    """Lleva una hora a formato de 24 horas usando el meridiano disponible."""
    marker = meridiem or hint
    if marker == "pm" and hour < 12:
        hour += 12
    elif marker == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minutes}"


def _daypart_of(text: str) -> Optional[str]:
    """Franja del día que nombra el texto, si nombra alguna."""
    for phrase, part in VAGUE_MOMENTS:
        if phrase in text:
            return part
    for part in DAYPARTS:
        if re.search(rf"\b{part}\b", text):
            # "mañana" a secas es el día siguiente, no la franja matinal. Sólo
            # cuenta como franja cuando va introducida ("de la mañana").
            if part == "manana":
                continue
            return part
    return None


def _spelled_hour(text: str) -> Optional[int]:
    """Hora dicha con letra: "a las ocho", "a la una"."""
    match = re.search(r"a\s+las?\s+([a-z]+)", text)
    if match and match.group(1) in _SPELLED_HOURS:
        return _SPELLED_HOURS[match.group(1)]
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. FECHA
# ═══════════════════════════════════════════════════════════════════════════════

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}

_SPELLED_DAYS = {
    "primero": 1, "primer": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "veinte": 20, "treinta": 30,
}


def read_date(text: str, today: Optional[datetime] = None) -> Optional[str]:
    """
    Lee la fecha de un texto: "today", "tomorrow" o "YYYY-MM-DD".

    "mañana" sólo es el día siguiente cuando actúa como adverbio. En "a las 9 de
    la mañana" es la franja matinal, y tomarla por el día siguiente cambiaba la
    cita de fecha sin que el usuario lo pidiera.
    """
    t = normalize(text or "")
    if not t:
        return None
    now = today or datetime.now()

    if re.search(r"\bhoy\b", t):
        return "today"
    if re.search(r"\bpasado\s+manana\b", t):
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")

    # Las dos "mañanas" conviven en la misma frase: "mañana a las 9 de la
    # mañana" fija día Y meridiano. Se retiran primero las que hacen de
    # meridiano; si queda alguna suelta, ésa sí es el día siguiente.
    sin_meridiano = _MORNING_AS_MERIDIEM.sub(" ", t)
    if re.search(r"\bmanana\b", sin_meridiano):
        return "tomorrow"

    # "el viernes", "el próximo lunes"
    for name, index in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            return _next_weekday(now, index, upcoming="proximo" in t or "siguiente" in t)

    # "el 30 de abril", "el primero de mayo"
    month_match = re.search(r"\b([a-z0-9]+)\s+de\s+([a-z]+)\b", t)
    if month_match:
        raw_day, month_name = month_match.group(1), month_match.group(2)
        if month_name in MONTHS:
            day = int(raw_day) if raw_day.isdigit() else _SPELLED_DAYS.get(raw_day)
            if day and 1 <= day <= 31:
                return f"{now.year}-{MONTHS[month_name]:02d}-{day:02d}"

    # "el 30" a secas: día del mes en curso.
    day_match = re.search(r"\bel\s+(\d{1,2})\b", t)
    if day_match:
        day = int(day_match.group(1))
        if 1 <= day <= 31:
            return f"{now.year}-{now.month:02d}-{day:02d}"

    return None


def _next_weekday(now: datetime, weekday: int, upcoming: bool = False) -> str:
    """
    Próxima aparición de ese día de la semana.

    Decir "el viernes" un viernes se refiere al siguiente, no a hoy: quien ya
    está en ese día no lo nombra para hablar del momento presente.
    """
    delta = (weekday - now.weekday()) % 7
    if delta == 0 or (upcoming and delta < 7):
        delta = delta or 7
    return (now + timedelta(days=delta)).strftime("%Y-%m-%d")


def describe_date(value: Optional[str]) -> str:
    """Cómo se le nombra una fecha al usuario."""
    if not value:
        return ""
    if value == "today":
        return "hoy"
    if value == "tomorrow":
        return "mañana"
    return f"el {value}"
