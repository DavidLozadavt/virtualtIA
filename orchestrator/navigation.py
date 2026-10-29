"""
orchestrator/navigation.py — Navegar a secciones reales de la aplicación.

El front envía en cada turno `nav_catalog`: las secciones que ESTE usuario ve en
su menú (ya filtradas por sus permisos). Lyra sólo puede llevarlo a una de ellas;
nunca construye una ruta por su cuenta. La coincidencia es determinista (0
tokens) y tolera erratas, plurales y sinónimos frecuentes.

El front ejecuta la navegación con el router de la aplicación y confirma después
que la ruta cambió; si una guarda de permisos la rechaza, lo informa en el chat.
Por eso la respuesta de Lyra dice "te llevo", no "ya estás".
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

#: Formato aceptado para una ruta interna: empieza por "/", sin esquema ni "//".
_SAFE_PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9\-_/.~%:=&?#]*$")

_NAV_VERBS = re.compile(
    r"\b(llevame|lleva me|ll[eé]vame|llevarme|ir a|ir al|ire a|vamos a|vamos al|abre|abrir|abreme|entrar a|entrar al|"
    r"entra a|ingresar a|ingresa a|navega a|navegar a|redirige|redirigeme|regresa|regresar|regresame|regreso|volver|vuelve|vuelvo|"
    r"devuelveme|como llego|como regreso|como vuelvo|como entro|como ingreso|como accedo|como abro|donde encuentro|donde esta el modulo|"
    r"donde esta la seccion|donde estan|donde queda el modulo|donde queda la seccion|donde veo|muestrame la seccion|"
    r"muestrame el modulo|quiero ir|quiero entrar|quiero ver la seccion|quiero ver el modulo|ensename donde|"
    r"en que parte|por donde)\b"
)

#: Palabras que no identifican la sección.
_STOP = {
    "a", "al", "el", "la", "los", "las", "de", "del", "en", "mi", "mis", "un", "una", "me", "por", "favor",
    "llevame", "lleva", "ir", "ire", "vamos", "abre", "abrir", "abreme", "entrar", "entra", "ingresar", "ingresa",
    "navega", "navegar", "redirige", "redirigeme", "regresa", "regresar", "regresame", "volver", "vuelve",
    "devuelveme", "regreso", "vuelvo", "como", "llego", "entro", "ingreso", "accedo", "abro", "donde", "encuentro", "esta", "estan",
    "queda", "veo", "muestrame", "quiero", "ver", "ensename", "que", "parte", "seccion", "modulo", "modulos",
    "pagina", "pantalla", "menu", "opcion", "lo", "le", "puedo", "puedes", "porfa", "y", "o", "con", "para", "se",
    "hay", "disponible", "disponibles", "esa", "ese", "eso", "este", "esta", "aqui", "alla", "ahi",
}

#: Sinónimos de uso común → palabras que sí aparecen en los títulos del menú.
_SYNONYMS = {
    "inicio": ["dashboard", "inicio"],
    "principal": ["dashboard", "inicio"],
    "home": ["dashboard", "inicio"],
    "tablero": ["dashboard"],
    "escritorio": ["dashboard"],
    "programa": ["formacion", "programa", "planeacion"],
    "programas": ["formacion", "programa", "planeacion"],
    "curso": ["formacion", "clase", "curso"],
    "cursos": ["formacion", "clase", "curso"],
    "inscripcion": ["inscripcion"],
    "inscripciones": ["inscripcion"],
    "matricula": ["inscripcion", "matricula"],
    "matriculas": ["inscripcion", "matricula"],
    "tarea": ["actividad"],
    "tareas": ["actividad"],
    "nota": ["calificacion", "nota"],
    "notas": ["calificacion", "nota"],
    "calendario": ["horario"],
    "agenda": ["horario", "agenda"],
    "biblioteca": ["biblioteca"],
    "libros": ["biblioteca"],
    "usuario": ["usuario"],
    "videollamada": ["videoconferencia"],
    "reunion": ["videoconferencia"],
    "reuniones": ["videoconferencia"],
    "nomina": ["nomina"],
    "pagos": ["pago"],
}

_HOME_WORDS = {"inicio", "principal", "home", "dashboard", "tablero", "escritorio"}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ/ ]", " ", text)).strip()


def _stem(word: str) -> str:
    w = word
    for suf in ("aciones", "acion", "iones", "ion", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _close(a: str, b: str) -> bool:
    """Misma palabra salvo plural, errata de una letra o prefijo largo."""
    if a == b:
        return True
    sa, sb = _stem(a), _stem(b)
    if sa == sb or (len(sa) >= 5 and len(sb) >= 5 and (sa.startswith(sb) or sb.startswith(sa))):
        return True
    if abs(len(a) - len(b)) <= 1 and min(len(a), len(b)) >= 5:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 1
    return False


def sanitize_catalog(raw) -> list[dict]:
    """Descarta entradas mal formadas o rutas que no sean internas."""
    out, seen = [], set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:120]
        path = str(item.get("path") or "").strip()[:200]
        if not title or not _SAFE_PATH_RE.match(path) or "javascript:" in path.lower():
            continue
        if path in seen:
            continue
        seen.add(path)
        out.append({"title": title, "path": path})
        if len(out) >= 300:
            break
    return out


def is_navigation_request(text: str) -> bool:
    return bool(_NAV_VERBS.search(fold(text)))


def _query_terms(text: str) -> list[str]:
    words = [w for w in fold(text).replace("/", " ").split() if w not in _STOP and len(w) > 1]
    expanded: list[str] = []
    for w in words:
        expanded.append(w)
        expanded.extend(_SYNONYMS.get(w, []))
    return expanded


def match_section(text: str, catalog: list[dict]) -> dict:
    """
    Busca la sección pedida dentro del catálogo del usuario.

    Devuelve {"status": "match"|"ambiguous"|"none", "item", "options", "terms"}.
    """
    terms = _query_terms(text)
    if not catalog:
        return {"status": "none", "item": None, "options": [], "terms": terms}

    if any(w in _HOME_WORDS for w in fold(text).split()):
        home = next((c for c in catalog if c["path"] == "/"), None)
        if home:
            return {"status": "match", "item": home, "options": [], "terms": terms}

    if not terms:
        return {"status": "none", "item": None, "options": [], "terms": terms}

    scored = []
    for item in catalog:
        # La hoja del menú ("Usuarios") identifica la sección; el grupo padre
        # ("Gestión de Usuarios › Roles") y la ruta sólo la sitúan.
        segments = [seg.strip() for seg in item["title"].split("›")]
        leaf_words = [w for w in fold(segments[-1]).split() if w not in _STOP]
        parent_words = [w for seg in segments[:-1] for w in fold(seg).split() if w not in _STOP]
        path_words = [w for w in re.split(r"[/\-_]", item["path"].lower()) if w]
        hits = 0.0
        for t in set(terms):
            if any(_close(t, w) for w in leaf_words):
                hits += 1.0
            elif any(_close(t, w) for w in parent_words):
                hits += 0.4
            elif any(_close(t, w) for w in path_words):
                hits += 0.3
        if hits:
            # Más peso a coincidir con un título corto (más específico).
            scored.append((hits / (1 + 0.15 * len(leaf_words)), hits, item))
    if not scored:
        return {"status": "none", "item": None, "options": [], "terms": terms}

    scored.sort(key=lambda x: (-x[0], len(x[2]["title"])))
    best = scored[0]
    # Rivales: otras secciones que coinciden tanto como la mejor. Si dos
    # títulos empatan ("Pagos pendientes" / "Historial de pagos"), se pregunta.
    rivals = [s for s in scored[1:] if s[1] >= best[1] and s[0] >= best[0] * 0.75]
    if rivals:
        options = [best[2]] + [r[2] for r in rivals[:3]]
        return {"status": "ambiguous", "item": None, "options": options, "terms": terms}
    return {"status": "match", "item": best[2], "options": [], "terms": terms}


def navigation_reply(text: str, catalog: list[dict], current_path: Optional[str] = None) -> dict:
    """
    Resuelve un pedido de navegación de punta a punta, sin modelo.

    Devuelve el dict de turno que entienden ChatService y el front.
    """
    if not catalog:
        return {
            "reply": (
                "Desde este chat todavía no puedo abrir secciones de la aplicación. "
                "Usa el menú lateral; si me dices qué buscas, te digo qué opción elegir."
            ),
            "final_data": {"nav_unavailable": True},
        }

    result = match_section(text, catalog)
    if result["status"] == "match":
        item = result["item"]
        if current_path and current_path.rstrip("/") == item["path"].rstrip("/"):
            return {
                "reply": f"Ya estás en **{item['title']}**.",
                "final_data": {"nav_target": item},
            }
        return {
            "reply": f"Te llevo a **{item['title']}**.",
            "final_data": {
                "voice_action": "navigate",
                "voice_action_payload": {"url": item["path"], "title": item["title"], "verify": True},
                "nav_target": item,
            },
        }
    if result["status"] == "ambiguous":
        lines = "\n".join(f"- {o['title']}" for o in result["options"])
        return {
            "reply": f"Encontré varias secciones parecidas. ¿A cuál quieres ir?\n{lines}",
            "final_data": {
                "needs_clarification": True,
                "nav_options": result["options"],
                "quick_replies": [{"title": o["title"], "text": f"llévame a {o['title']}"} for o in result["options"]],
            },
        }
    wanted = " ".join(w for w in fold(text).split() if w not in _STOP) or "esa sección"
    top = [c["title"] for c in catalog if c["path"] != "/"][:6]
    suggestion = (" Algunas que sí tienes: " + ", ".join(top) + ".") if top else ""
    return {
        "reply": (
            f"No encontré «{wanted}» entre las secciones habilitadas para tu usuario."
            f"{suggestion} Si debería aparecerte, pídele acceso a la coordinación."
        ),
        "final_data": {"nav_not_found": True},
    }
