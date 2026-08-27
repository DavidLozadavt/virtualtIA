"""
core/speech_format.py — Cómo se escriben y cómo se dicen los números.

Un precio tiene dos formas legítimas y distintas, y confundirlas es lo que hacía
que Lyra leyera «ciento veinte coma cero cero cero»:

  · La forma ESCRITA, para el chat:      $120.000
  · La forma HABLADA, para la voz:       ciento veinte mil pesos

Este módulo es la autoridad única de las dos. Cualquier capa que muestre un
precio usa `format_price`; cualquier capa que lo mande a un motor de voz pasa el
texto entero por `humanize_for_speech`, que encuentra las cifras dentro de la
frase y las reemplaza por su lectura. Así no hace falta acordarse de convertir
en cada sitio donde aparece un valor: el paso a voz lo cubre todo.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

Number = Union[int, float, Decimal, str, None]


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. NÚMEROS EN LETRAS (español)
# ═══════════════════════════════════════════════════════════════════════════════

_UNITS = (
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
    "nueve", "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis",
    "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno",
    "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis",
    "veintisiete", "veintiocho", "veintinueve",
)

_TENS = {
    3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta",
    7: "setenta", 8: "ochenta", 9: "noventa",
}

_HUNDREDS = {
    1: "ciento", 2: "doscientos", 3: "trescientos", 4: "cuatrocientos",
    5: "quinientos", 6: "seiscientos", 7: "setecientos", 8: "ochocientos",
    9: "novecientos",
}


def _under_thousand(n: int) -> str:
    """0–999 en letras. `n` ya viene acotado por el llamador."""
    if n < 30:
        return _UNITS[n]
    if n < 100:
        decena, unidad = divmod(n, 10)
        if unidad == 0:
            return _TENS[decena]
        return f"{_TENS[decena]} y {_UNITS[unidad]}"
    if n == 100:
        return "cien"
    centena, resto = divmod(n, 100)
    if resto == 0:
        return _HUNDREDS[centena]
    return f"{_HUNDREDS[centena]} {_under_thousand(resto)}"


def _apocopate(words: str) -> str:
    """
    «uno» delante de un sustantivo pierde la o: un millón, veintiún mil.

    Sólo afecta al final del sintagma, que es donde el número toca al nombre que
    cuenta. «treinta y uno» → «treinta y un», «veintiuno» → «veintiún».
    """
    if words == "uno":
        return "un"
    if words.endswith(" uno"):
        return words[:-4] + " un"
    if words.endswith("veintiuno"):
        return words[:-len("veintiuno")] + "veintiún"
    return words


def spell_number_es(n: int) -> str:
    """
    Un entero en letras, tal como lo diría una persona.

    >>> spell_number_es(120000)
    'ciento veinte mil'
    >>> spell_number_es(1200000)
    'un millón doscientos mil'
    """
    if n < 0:
        return f"menos {spell_number_es(-n)}"
    if n < 1000:
        return _under_thousand(n)

    if n < 1_000_000:
        miles, resto = divmod(n, 1000)
        cabeza = "mil" if miles == 1 else f"{_apocopate(_under_thousand(miles))} mil"
        return cabeza if resto == 0 else f"{cabeza} {_under_thousand(resto)}"

    if n < 1_000_000_000_000:
        millones, resto = divmod(n, 1_000_000)
        if millones == 1:
            cabeza = "un millón"
        else:
            cabeza = f"{_apocopate(spell_number_es(millones))} millones"
        return cabeza if resto == 0 else f"{cabeza} {spell_number_es(resto)}"

    billones, resto = divmod(n, 1_000_000_000_000)
    cabeza = "un billón" if billones == 1 else f"{_apocopate(spell_number_es(billones))} billones"
    return cabeza if resto == 0 else f"{cabeza} {spell_number_es(resto)}"


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. DINERO
# ═══════════════════════════════════════════════════════════════════════════════

#: Cifras que piden «de» antes del sustantivo que cuentan.
_ENDS_IN_MILLION = re.compile(r"(?:mill[oó]n|millones|bill[oó]n|billones)$")


#: Moneda por defecto de NexiService. En singular y en plural, porque «un peso»
#: y «dos pesos» no se dicen igual y leer «1 pesos» delata la plantilla.
DEFAULT_CURRENCY = ("peso", "pesos")


def to_amount(value: Number) -> Optional[Decimal]:
    """
    El valor numérico que hay detrás de lo que llegue, o None.

    Los precios salen de MySQL como `Decimal`, del LLM como texto y de los YAML
    como `int`. Todos acaban aquí antes de escribirse o de decirse.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):          # bool es int en Python; no es un precio
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    # «120.000,50» y «120,000.50» significan lo mismo: el último separador con
    # dos dígitos detrás es el decimal, el resto son grupos de millar.
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in text:
        entero, _, resto = text.rpartition(",")
        text = f"{entero}.{resto}" if len(resto) == 2 and entero else text.replace(",", "")
    elif text.count(".") > 1 or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def format_price(value: Number, fallback: str = "Precio a consultar") -> str:
    """
    Un precio como se escribe en Colombia: `$120.000`.

    Los centavos no se muestran cuando no los hay, que es siempre en pesos
    colombianos. Un valor ausente o cero no se inventa: se dice que hay que
    consultarlo, porque un «$0» en pantalla se lee como gratis.
    """
    amount = to_amount(value)
    if amount is None or amount <= 0:
        return fallback
    entero = int(amount)
    centavos = amount - entero
    escrito = f"{entero:,}".replace(",", ".")
    if centavos:
        escrito += f",{int(round(centavos * 100)):02d}"
    return f"${escrito}"


def money_to_words(value: Number, currency: tuple = DEFAULT_CURRENCY) -> Optional[str]:
    """
    Un precio dicho en voz alta: `120000` → «ciento veinte mil pesos».

    Devuelve None cuando no hay un importe que decir, para que el llamador
    decida qué hacer con el hueco en vez de leer un cero.
    """
    amount = to_amount(value)
    if amount is None:
        return None
    entero = int(amount)
    centavos = int(round((amount - entero) * 100))
    singular, plural = currency
    cifra = _apocopate(spell_number_es(entero))
    # «dos millones DE pesos», pero «un millón doscientos mil pesos»: la
    # preposición sólo aparece cuando el millón es la última palabra de la
    # cifra. Sin ella la frase suena a traducción automática.
    enlace = " de " if _ENDS_IN_MILLION.search(cifra) else " "
    palabras = f"{cifra}{enlace}{singular if entero == 1 else plural}"
    if centavos:
        palabras += f" con {spell_number_es(centavos)} centavos"
    return palabras


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. UN TEXTO ENTERO, LISTO PARA DECIRSE
# ═══════════════════════════════════════════════════════════════════════════════

#: `$120.000`, `$ 120,000.00`, `COP 45000`. El símbolo o el código marcan que la
#: cifra es dinero sin lugar a dudas, así que se lee con su moneda detrás.
_CURRENCY_PREFIXED = re.compile(
    r"(?<![\w])(?:\$|COP\s*\$?|cop\s*\$?)\s*(\d(?:[\d.,]*\d)?)",
)

#: `120.000 pesos`, `45000 COP`. La moneda va detrás y ya está dicha, así que se
#: reemplaza el bloque entero para no repetirla.
_CURRENCY_SUFFIXED = re.compile(
    r"(?<![\w])(\d(?:[\d.,]*\d)?)\s*(pesos?|COP|cop)\b",
)

#: Una cifra agrupada de tres en tres (`120.000`, `1,200,000`). No lleva moneda,
#: pero tampoco se lee dígito a dígito: es una cantidad.
_GROUPED_NUMBER = re.compile(
    r"(?<![\w.,:/-])(\d{1,3}(?:[.,]\d{3})+)(?![\d.,:/-]*\d)",
)

#: Una hora. Se protege explícitamente: `08:30` no es una cantidad y leerlo como
#: tal («ochocientos treinta») rompe justo el dato que el usuario esperaba.
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _spoken_amount(raw: str, with_currency: bool) -> str:
    if with_currency:
        return money_to_words(raw) or raw
    amount = to_amount(raw)
    if amount is None:
        return raw
    entero = int(amount)
    palabras = spell_number_es(entero)
    centavos = int(round((amount - entero) * 100))
    if centavos:
        palabras += f" con {spell_number_es(centavos)}"
    return palabras


#: Cómo se dicen los minutos de una hora en una conversación normal.
_MINUTE_FORMS = {15: "y cuarto", 30: "y media"}


def _spoken_time(match: re.Match) -> str:
    """
    «08:30» → «ocho y media de la mañana».

    La hora es el dato que el usuario tiene que retener de una cita, y es el que
    peor sale de un motor de voz cuando se le entrega como dígitos.
    """
    hora, minuto = int(match.group(1)), int(match.group(2))
    if hora > 23 or minuto > 59:
        return match.group(0)

    if minuto == 45:
        # «menos cuarto» pertenece a la hora siguiente: las 8:45 son las nueve
        # menos cuarto, no las ocho menos cuarto.
        cabeza, franja = _hour_words((hora + 1) % 24)
        return f"{cabeza} menos cuarto {franja}"

    cabeza, franja = _hour_words(hora)
    if minuto == 0:
        return f"{cabeza} {franja}"
    if minuto in _MINUTE_FORMS:
        return f"{cabeza} {_MINUTE_FORMS[minuto]} {franja}"
    return f"{cabeza} y {spell_number_es(minuto)} {franja}"


def _hour_words(hora: int) -> tuple:
    """La hora y su franja del día. La una es femenina: «la una», no «el uno»."""
    if hora == 0:
        return "doce", "de la noche"
    if hora == 12:
        return "doce", "del mediodía"
    numero = hora if hora < 12 else hora - 12
    palabra = "una" if numero == 1 else spell_number_es(numero)
    if hora < 12:
        return palabra, "de la mañana"
    return palabra, "de la tarde" if hora < 20 else "de la noche"


def humanize_for_speech(text: str) -> str:
    """
    Deja un texto listo para un motor de voz: las cifras pasan a letras.

    Es el único punto donde hay que acordarse de esto. Todo lo que Lyra dice —el
    chat con voz, el teléfono, el navegador— pasa por aquí, así que un precio
    nuevo en cualquier respuesta se lee bien sin tocar nada más.

    Lo que NO se toca, a propósito:
      · las horas (`08:30`), que ya se dicen bien y son el dato de una cita;
      · los números largos sin separadores (un celular, un documento), donde
        leer dígito a dígito es justamente lo correcto.
    """
    if not text:
        return text

    # Las horas se apartan antes de tocar nada y vuelven al final: cualquier
    # regla sobre cifras las estropearía.
    horas: list = []

    def _guardar_hora(m: re.Match) -> str:
        horas.append(m.group(0))
        return f"\x00H{len(horas) - 1}\x00"

    protegido = _TIME.sub(_guardar_hora, text)

    protegido = _CURRENCY_PREFIXED.sub(
        lambda m: _spoken_amount(m.group(1), with_currency=True), protegido
    )
    protegido = _CURRENCY_SUFFIXED.sub(
        lambda m: _spoken_amount(m.group(1), with_currency=True), protegido
    )
    protegido = _GROUPED_NUMBER.sub(
        lambda m: _spoken_amount(m.group(1), with_currency=False), protegido
    )

    # Las horas vuelven ya dichas: «a las 09:00» sale del motor de voz como una
    # ristra de dígitos, y es justo el dato que el usuario necesita retener.
    for idx, hora in enumerate(horas):
        dicha = _TIME.sub(_spoken_time, hora)
        protegido = protegido.replace(f"\x00H{idx}\x00", dicha)
    return re.sub(r"\ba las (una\b)", r"a la \1", protegido)


# ═══════════════════════════════════════════════════════════════════════════════
# § 4. UNA LISTA LARGA NO SE DICE ENTERA
# ═══════════════════════════════════════════════════════════════════════════════

#: Una viñeta al principio de línea. Es como se escriben los catálogos de
#: servicios y las listas de opciones en las respuestas de Lyra.
_BULLET = re.compile(r"^\s*[•·*-]\s+(.*)$", re.MULTILINE)

#: Cuántos elementos de una lista se dicen antes de resumir el resto. Tres es lo
#: que una persona retiene de viva voz; a partir de ahí sólo estorban.
SPOKEN_LIST_LIMIT = 3


def condense_lists_for_speech(text: str, limit: int = SPOKEN_LIST_LIMIT) -> str:
    """
    Deja las listas en algo que se pueda escuchar.

    Un catálogo de dieciocho servicios ocupa cuatro líneas en pantalla, donde el
    usuario las recorre con la vista, y casi un minuto de audio, donde no puede
    hacer otra cosa que esperar. Leerlo entero es la versión más mecánica
    posible de una respuesta correcta.

    Lo que se dice sigue siendo verdad y sigue siendo lo mismo: los primeros
    elementos, y cuántos quedan. Lo escrito no se toca — quien lea la respuesta
    la tiene completa.
    """
    if not text:
        return text
    lineas = text.split("\n")
    salida, bloque = [], []

    def _volcar():
        if not bloque:
            return
        if len(bloque) <= limit:
            salida.extend(bloque)
        else:
            salida.extend(bloque[:limit])
            restantes = len(bloque) - limit
            # Sin punto final: el salto de línea ya se convierte en pausa más
            # adelante, y con los dos sale un "más.." que el motor de voz
            # arrastra como un tropiezo.
            salida.append(f"y {restantes} más" if restantes > 1 else "y una más")
        bloque.clear()

    for linea in lineas:
        if _BULLET.match(linea) or re.match(r"^\s*\d+[.)]\s+\S", linea):
            bloque.append(linea)
        else:
            _volcar()
            salida.append(linea)
    _volcar()
    return "\n".join(salida)
