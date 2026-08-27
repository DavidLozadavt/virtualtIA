"""
core/wording.py — Cómo nombra Lyra las cosas cuando habla con una persona.

La plataforma clasifica un hospital dentro de «Consultorios y Centros Médicos».
Esa etiqueta sirve para BUSCAR y no sirve para NADA más: quien escribe «necesito
un hospital» y recibe «encontré 6 opciones de médico» siente que no lo
entendieron, aunque los seis resultados sean exactamente los que quería.

Aquí viven las dos reglas que separan una cosa de la otra:

  · La clasificación interna se usa para consultar la base de datos.
  · La palabra del usuario se usa para contestarle.

El módulo no sabe nada de negocios ni de intenciones: sólo de español. Se puede
probar solo, y por eso las concordancias dejaron de ser una cadena de `if` dentro
de cada respuesta.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Sequence

#: Palabras que describen el acto de buscar, no lo que se busca. Si el usuario
#: dijo «opciones» o «negocios», ésa no es su forma de nombrar un rubro: es la
#: forma de no nombrarlo, y conviene caer a la etiqueta del catálogo.
_EMPTY_NOUNS = frozenset({
    "opcion", "opciones", "alternativa", "alternativas", "cosa", "cosas",
    "negocio", "negocios", "empresa", "empresas", "lugar", "lugares",
    "sitio", "sitios", "local", "locales", "establecimiento", "establecimientos",
    "algo", "todo", "todos", "todas", "encontrar", "buscar", "ver", "mostrar",
})

_VOWELS = "aeiouáéíóú"

#: Verbos que el análisis deja pasar como contenido porque nombran la necesidad
#: («un lugar para COMER», «quiero ENCONTRAR algo»). Nombran la acción, no lo que
#: se busca: pluralizarlos produce «6 comeres». Cuando el término del usuario es
#: uno de éstos se cae a la etiqueta del catálogo, que ahí sí nombra el rubro.
_INFINITIVE_ENDINGS = ("ar", "er", "ir")

#: …salvo estos sustantivos, que acaban igual que un infinitivo y son de los que
#: de verdad aparecen en un directorio comercial.
_NOUNS_LIKE_INFINITIVE = frozenset({
    "bar", "taller", "hogar", "bazar", "altar", "mujer", "placer", "collar",
    "militar", "particular", "familiar", "manicur", "souvenir",
})

#: Etiquetas cortas del catálogo tal como se escriben de verdad. La base guarda
#: la forma sin tildes porque es la que se usa para buscar; leerla así en una
#: respuesta delata la consulta SQL por debajo.
_DISPLAY_FORMS = {
    "medico": "médico",
    "barberia": "barbería",
    "gym": "gimnasio",
    "peluqueria": "peluquería",
    "veterinaria": "veterinaria",
    "cancha": "cancha deportiva",
    "farmacia": "farmacia",
    "tecnologia": "tecnología",
    "mecanica": "mecánica",
    "estetica": "estética",
    "educacion": "educación",
    "decoracion": "decoración",
    "panaderia": "panadería",
    "cafeteria": "cafetería",
    "pizzeria": "pizzería",
    "libreria": "librería",
    "optica": "óptica",
    "clinica": "clínica",
    "odontologia": "odontología",
    "estetica": "estética",
    "mecanico": "taller mecánico",
    "gimnasio": "gimnasio",
}

#: Sustantivos frecuentes cuyo género no se deduce de la terminación. La lista es
#: corta a propósito: sólo lo que de verdad aparece en un directorio comercial.
_MASCULINE_EXCEPTIONS = frozenset({"dia", "mapa", "problema", "sistema", "cine", "hotel", "bar"})
_FEMININE_EXCEPTIONS = frozenset({"mano", "moto", "foto", "sede", "clase", "carne", "noche", "salud"})


def _fold(word: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (word or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def pluralize_es(word: str) -> str:
    """
    El plural de una palabra española, con las excepciones que de verdad salen.

    >>> pluralize_es("hospital")
    'hospitales'
    >>> pluralize_es("veterinaria")
    'veterinarias'
    >>> pluralize_es("restauración")
    'restauraciones'
    """
    if not word:
        return word
    word = word.strip()
    if " " in word:
        # Sintagma: se pluraliza el núcleo, que en español va delante.
        # «centro médico» → «centros médicos»; «taller de motos» → «talleres de motos».
        partes = word.split()
        if len(partes) == 2 and partes[1].lower() not in ("de", "del", "para", "en"):
            return f"{pluralize_es(partes[0])} {pluralize_es(partes[1])}"
        return f"{pluralize_es(partes[0])} {' '.join(partes[1:])}"

    lower = word.lower()
    if _fold(lower).endswith("es") and len(lower) > 4:
        return word                                   # ya viene en plural
    if lower.endswith("s") and _fold(lower)[-2] in _VOWELS:
        return word                                   # «lunes», «crisis», plurales ya hechos
    if lower.endswith("z"):
        return word[:-1] + "ces"
    if lower.endswith(("ión", "ion")) and len(lower) > 4:
        return re.sub(r"i[oó]n$", "iones", word, flags=re.IGNORECASE)
    if _fold(lower).endswith(("a", "e", "o")):
        return word + "s"
    if _fold(lower).endswith(("i", "u")):
        return word + "es"
    return word + "es"


#: Consonantes con las que una palabra española puede terminar de verdad. Sirven
#: para decidir si "hospitales" viene de "hospital" (sí, acaba en l) o de
#: "hospitale" (no existe).
_VALID_FINAL_CONSONANTS = ("l", "r", "n", "d", "z", "j")


def singularize_es(word: str) -> str:
    """
    El singular de una palabra española.

    Hace falta porque el usuario pregunta en plural —"¿qué veterinarias
    tienes?"— y la respuesta puede necesitar el singular: "encontré UNA
    veterinaria". Guardar la forma que dijo y contarla mal es peor que no
    haberla guardado.

    >>> singularize_es("hospitales")
    'hospital'
    >>> singularize_es("restaurantes")
    'restaurante'
    """
    if not word:
        return word
    if " " in word:
        # El adjetivo concuerda con el núcleo: "centros médicos" → "centro
        # médico", pero "talleres de motos" sólo cambia el núcleo.
        partes = word.split()
        if len(partes) == 2 and partes[1].lower() not in ("de", "del", "para", "en"):
            return f"{singularize_es(partes[0])} {singularize_es(partes[1])}"
        return " ".join([singularize_es(partes[0])] + partes[1:])

    lower = word.lower()
    if not lower.endswith("s") or len(lower) < 4:
        return word
    if _fold(lower).endswith("ciones") or _fold(lower).endswith("siones"):
        return re.sub(r"([cs])iones$", lambda m: m.group(1) + "ión", word, flags=re.IGNORECASE)
    if _fold(lower).endswith("ces"):
        return word[:-3] + "z"
    if lower.endswith("es") and _fold(lower)[-3] in _VALID_FINAL_CONSONANTS:
        return word[:-2]
    return word[:-1]


def is_feminine(word: str) -> bool:
    """¿El sustantivo pide «una» y «cada una», o «un» y «cada uno»?"""
    if not word:
        return False
    nucleo = _fold(word.split()[0])
    if nucleo in _MASCULINE_EXCEPTIONS:
        return False
    if nucleo in _FEMININE_EXCEPTIONS:
        return True
    return nucleo.endswith(("a", "ad", "cion", "sion", "tud", "umbre", "eria", "ia"))


def user_facing_label(
    user_terms: Optional[Sequence[str]] = None,
    catalog_terms: Optional[Sequence[str]] = None,
    category: Optional[str] = None,
) -> Optional[str]:
    """
    Con qué palabra contarle al usuario lo que se encontró.

    Manda la suya siempre que exista y nombre algo. Sólo cuando el usuario no
    nombró nada —«¿qué hay?», «muéstrame opciones»— se recurre a la etiqueta del
    catálogo, que ahí sí aporta información en vez de contradecirlo.
    """
    for fuente in (user_terms or (), catalog_terms or (), (category,) if category else ()):
        for termino in fuente:
            if not termino:
                continue
            limpio = str(termino).strip()
            if len(limpio) < 3:
                continue
            plano = _fold(limpio)
            if plano in _EMPTY_NOUNS:
                continue
            if _is_infinitive(plano):
                continue
            # Se guarda en singular: es la forma desde la que se puede contar
            # tanto "una veterinaria" como "4 veterinarias". La forma escrita se
            # busca sobre el singular ya sin tildes, que es como llega tanto de
            # la base de datos como del reconocimiento de voz.
            singular = singularize_es(limpio)
            return _DISPLAY_FORMS.get(_fold(singular), singular)
    return None


def _is_infinitive(folded: str) -> bool:
    """¿La palabra nombra una acción en vez de una cosa?"""
    if len(folded) < 4 or folded in _NOUNS_LIKE_INFINITIVE:
        return False
    return folded.endswith(_INFINITIVE_ENDINGS)


def count_phrase(count: int, label: Optional[str], fallback: str = "opciones") -> str:
    """
    «6 hospitales», «una veterinaria», «12 opciones».

    El número y el sustantivo se acuerdan aquí una vez, en lugar de repartir la
    concordancia por cada plantilla de respuesta.
    """
    nombre = label or fallback
    if count == 1:
        return f"{'una' if is_feminine(nombre) else 'un'} {nombre}"
    return f"{count} {pluralize_es(nombre)}"


def each_one(label: Optional[str]) -> str:
    """«cada uno» o «cada una», según lo que se esté contando."""
    return "cada una" if is_feminine(label or "") else "cada uno"


def them(label: Optional[str]) -> str:
    """El clítico de objeto plural que corresponde: «los» o «las»."""
    return "las" if is_feminine(label or "") else "los"


def natural_list(items: Iterable[str], conjunction: str = "y") -> str:
    """«A, B y C» — una enumeración dicha como la diría una persona."""
    limpio: List[str] = [str(i).strip() for i in items if str(i).strip()]
    if not limpio:
        return ""
    if len(limpio) == 1:
        return limpio[0]
    return f"{', '.join(limpio[:-1])} {conjunction} {limpio[-1]}"


def one_line(text: str, limit: int) -> str:
    """
    Un texto de la base de datos, servido en una línea.

    Los perfiles y las descripciones llegan con su propia numeración y sus
    saltos ("1. Perfil profesional\\nOdontólogo… 2. Formación…"). Dentro de una
    lista eso produce dos numeraciones encajadas y varias frases sueltas donde
    debería haber una. Se conserva la primera sección, que es la que presenta.
    """
    clean = re.sub(r"\s+", " ", (text or "").strip())
    clean = re.sub(r"^\d+[.)]\s*(perfil profesional)?[:.]?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.split(r"\s\d+[.)]\s", clean)[0].strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rsplit(" ", 1)[0] + "…"
