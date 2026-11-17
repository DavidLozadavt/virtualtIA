"""
core/semantic/fechas.py — Fechas de calendario para operaciones (crear un evento…).

`temporal.read_date` sirve a las reservas de NexiService y se deja como está.
Aquí se distinguen expresiones que allí se confundían; la más grave:
"el viernes de la próxima semana" se calculaba como el viernes de ESTA semana.

Semana = lunes a domingo (como en Colombia). Con `hoy` martes 17/11/2026:
  este viernes / el viernes / el próximo viernes   → 20/11 (el que viene)
  el viernes de la próxima semana                  → 27/11 (semana siguiente)
  el lunes siguiente / el próximo lunes            → 23/11
  dentro de ocho días / en ocho días               → 24/11 (uso colombiano: una semana)
  dentro de N días (N ≠ 8, 15)                     → hoy + N
  el último viernes del mes                        → 27/11
  el primer lunes del próximo mes                  → 07/12
  el 5 de enero                                    → 05/01/2027 (si ya pasó este año)
Devuelve (fecha ISO o None, aviso o None). El aviso pide confirmar cuando la
expresión admite dos lecturas; quien llama siempre muestra la fecha completa.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from core.semantic.morphology import normalize

TZ = ZoneInfo("America/Bogota")
DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
         "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
_NUM = {"uno": 1, "un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
        "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "veinte": 20,
        "treinta": 30, "primero": 1}
_ORD = {"primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercer": 3, "tercero": 3, "tercera": 3,
        "cuarto": 4, "cuarta": 4}
NOMBRE_DIA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
NOMBRE_MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
              "octubre", "noviembre", "diciembre"]


def hoy_local() -> date:
    return datetime.now(TZ).date()


def fecha_larga(iso: str) -> str:
    """'2026-11-27' → 'viernes 27 de noviembre de 2026'."""
    d = date.fromisoformat(iso)
    return f"{NOMBRE_DIA[d.weekday()]} {d.day} de {NOMBRE_MES[d.month]} de {d.year}"


def _n(tok: str) -> Optional[int]:
    return int(tok) if tok.isdigit() else _NUM.get(tok)


def _proximo(hoy: date, dia: int, incluye_hoy: bool = False) -> date:
    delta = (dia - hoy.weekday()) % 7
    if delta == 0 and not incluye_hoy:
        delta = 7
    return hoy + timedelta(days=delta)


def _sin_meridiano(t: str) -> str:
    # "a las 9 de la mañana": esa "mañana" es la franja del día, no el día siguiente.
    return re.sub(r"\b(de|en|por|esta)\s+la\s+manana\b", " ", t)


def leer_fecha(texto: str, hoy: Optional[date] = None) -> Tuple[Optional[str], Optional[str]]:
    t = normalize(texto or "").replace("ñ", "n")
    crudo = (texto or "").lower()
    hoy = hoy or hoy_local()
    if not t:
        return None, None
    s = _sin_meridiano(t)

    # Fechas escritas: 2026-12-20, 20/12, 20/12/2026.
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", crudo)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), None
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", crudo)
    if m:
        dd, mm = int(m.group(1)), int(m.group(2))
        yy = int(m.group(3)) if m.group(3) else hoy.year
        yy = yy + 2000 if yy < 100 else yy
        try:
            d = date(yy, mm, dd)
        except ValueError:
            return None, None
        if not m.group(3) and d < hoy:
            d = date(yy + 1, mm, dd)
        return d.isoformat(), None

    if re.search(r"\bpasado\s+manana\b", s):
        return (hoy + timedelta(days=2)).isoformat(), None
    if re.search(r"\bmanana\b", s):
        return (hoy + timedelta(days=1)).isoformat(), None
    if re.search(r"\bhoy\b", s):
        return hoy.isoformat(), None

    # "dentro de ocho días", "en quince días", "dentro de 3 días", "en una semana".
    m = re.search(r"\b(?:dentro\s+de|en)\s+(\w+)\s+(dias|semanas?)\b", s)
    if m:
        n = _n(m.group(1))
        if n:
            if m.group(2).startswith("semana"):
                return (hoy + timedelta(days=7 * n)).isoformat(), None
            # En Colombia "en ocho días" es una semana y "en quince días", dos.
            dias = {8: 7, 15: 14}.get(n, n)
            aviso = f"tomé «{n} días» como {dias // 7} semana{'s' if dias > 7 else ''}" if n in (8, 15) else None
            return (hoy + timedelta(days=dias)).isoformat(), aviso

    # "el primer lunes del próximo mes", "el último viernes del mes".
    m = re.search(r"\b(primer|primero|primera|segundo|segunda|tercer|tercero|tercera|cuarto|cuarta|ultimo|ultima)\s+"
                  r"(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+(?:del|de)\s+"
                  r"(?:(proximo|siguiente)\s+mes|mes\s+(?:que viene|siguiente|entrante)|(este\s+)?mes|(\w+))", s)
    if m:
        ordn, dia = m.group(1), DIAS[m.group(2)]
        mes, anio = hoy.month, hoy.year
        if m.group(3) or "viene" in m.group(0) or "siguiente" in m.group(0) or "entrante" in m.group(0):
            mes, anio = (1, anio + 1) if mes == 12 else (mes + 1, anio)
        elif m.group(5) in MESES:
            mes = MESES[m.group(5)]
            if mes < hoy.month:
                anio += 1
        dias_mes = [date(anio, mes, k) for k in range(1, calendar.monthrange(anio, mes)[1] + 1)
                    if date(anio, mes, k).weekday() == dia]
        d = dias_mes[-1] if ordn.startswith("ultim") else dias_mes[_ORD[ordn] - 1]
        return d.isoformat(), None

    # "el viernes de la próxima semana" / "el viernes de la otra semana".
    for nombre, idx in DIAS.items():
        if not re.search(rf"\b{nombre}\b", s):
            continue
        if re.search(rf"\b{nombre}\s+(?:de\s+la\s+)?(?:proxima|siguiente|otra)\s+semana\b|"
                     rf"\b{nombre}\s+de\s+la\s+semana\s+(?:que viene|siguiente|entrante)\b", s):
            lunes_sig = hoy - timedelta(days=hoy.weekday()) + timedelta(days=7)
            return (lunes_sig + timedelta(days=idx)).isoformat(), None
        if re.search(rf"\beste\s+{nombre}\b", s):
            return _proximo(hoy, idx, incluye_hoy=True).isoformat(), None
        d = _proximo(hoy, idx)
        aviso = None
        if re.search(rf"\b(proximo|siguiente)\s+{nombre}\b|\b{nombre}\s+(proximo|siguiente)\b", s) and (d - hoy).days <= 3:
            # "El próximo viernes" dicho un martes: unos entienden este viernes y
            # otros el de la semana siguiente. Se toma el que viene y se avisa.
            aviso = (f"entendí el {fecha_larga(d.isoformat())}; si te referías al de la semana siguiente, "
                     f"dime «el {nombre} de la próxima semana»")
        return d.isoformat(), aviso

    # "el 20 de diciembre", "el primero de enero" (si ya pasó, el del año siguiente).
    m = re.search(r"\b(\w+)\s+de\s+(" + "|".join(MESES) + r")(?:\s+(?:de|del)\s+(\d{4}))?\b", s)
    if m and _n(m.group(1)):
        dd, mm = _n(m.group(1)), MESES[m.group(2)]
        yy = int(m.group(3)) if m.group(3) else hoy.year
        try:
            d = date(yy, mm, dd)
        except ValueError:
            return None, None
        if not m.group(3) and d < hoy:
            d = date(yy + 1, mm, dd)
        return d.isoformat(), None

    m = re.search(r"\bel\s+(\d{1,2})\b(?!\s*(?:am|pm|:|y media|de la))", s)
    if m:
        dd = int(m.group(1))
        try:
            d = date(hoy.year, hoy.month, dd)
        except ValueError:
            return None, None
        if d < hoy:
            mm, yy = (1, hoy.year + 1) if hoy.month == 12 else (hoy.month + 1, hoy.year)
            d = date(yy, mm, dd)
        return d.isoformat(), None
    return None, None
