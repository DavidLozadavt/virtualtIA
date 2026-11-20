"""
core/semantic/utterance.py — Del mensaje tal como llega a la petición que expresa.

La gente no escribe ni habla en comandos. Antes de la petición suele haber un
arranque ("hola, mira, es que quería preguntarte algo, lo que pasa es que..."),
en medio hay muletillas y cortesías ("por fa", "eh", "pues") y a veces la
persona se corrige ("cuánto vale... bueno no, espera, mejor dime cómo
funciona"). El STT del navegador transcribe todo eso literal y quien escribe
rápido lo abrevia ("q", "x", "xq").

Esta función devuelve sólo la petición final, sin cambiar su contenido. Son
fenómenos del español conversacional, no palabras de ningún dominio, así que
sirven igual para SchoolSena que para NexiService. Lo que no reconoce lo deja
tal cual: si limpiar dejaría el mensaje vacío, se devuelve el original.
"""

import re

_SPACES = re.compile(r"\s+")

# ── Autocorrección: "X ... (bueno|no|espera)+ mejor Y" → "Y" ──────────────────
# Exige al menos un marcador de rectificación antes de "mejor": "el mejor
# restaurante" o "es mejor así" no son correcciones.
_CORRECTION = re.compile(
    r"(?:^|[\s,.;:!?¿¡…]+)"
    r"(?:(?:bueno|no|nono|espera|esperate|espere|perd[oó]n|disculpa|ah)\b[\s,.;:!?…]*)+"
    r"(?:mejor|m[aá]s\s+bien)\b[\s,:]*",
    re.IGNORECASE,
)
_CORRECTION_EXPLICIT = re.compile(
    r"(?:^|[\s,.;:!?…]+)(?:mejor\s+dicho|quise\s+decir|quiero\s+decir|me\s+equivoqu[eé])\b[\s,:]*",
    re.IGNORECASE,
)

# ── Arranques sin contenido al inicio del mensaje ─────────────────────────────
_GREETING = re.compile(
    r"^\s*((?:hola|holi|holis|buenas|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches|hey|qu[eé]\s+m[aá]s)\b[\s,.!¡]*)",
    re.IGNORECASE,
)
_PREAMBLE = re.compile(
    r"^\s*(?:"
    r"mira|mire|oye|oiga|oe|ey|eh+|em+|mm+|pues|"
    # Éstas también son palabras plenas ("este restaurante", "bueno el de
    # ayer"): sólo cuentan como muletilla seguidas de una pausa.
    r"(?:este|bueno|ve|vea|vean|ok|okey|listo|dale)(?=\s*[,.;:!…])|"
    r"sabes\s+qu[eé]|una\s+pregunta|tengo\s+una\s+pregunta|"
    r"(?:te\s+)?quer[ií]a\s+preguntar(?:te)?\s+(?:algo|una\s+cosa)|"
    r"(?:te\s+)?quiero\s+preguntar(?:te)?\s+(?:algo|una\s+cosa)|"
    r"es\s+que|lo\s+que\s+pasa\s+es\s+que|resulta\s+que|la\s+cosa\s+es\s+que|fijate\s+que|f[ií]jese\s+que|"
    # "Déjame ver negocios": el permiso no es contenido; lo pedido es "ver negocios".
    r"d[eé]ja(?:me|nos)(?=\s+\w+[aei]r\b)|d[eé]je(?:me|nos)(?=\s+\w+[aei]r\b)"
    r")\b[\s,.;:!…]*",
    re.IGNORECASE,
)

# ── Muletillas y cortesías en cualquier posición ──────────────────────────────
_FILLERS = re.compile(
    r"(?<![\w])(?:por\s+fa(?:vor|vorcito)?|porfa(?:vor)?|porfis|porfi|x\s*fa(?:vor)?|plis|pls|please|"
    r"eh+|ehm+|em+|mm+|mmm+|o\s+sea|pues)(?![\w])",
    re.IGNORECASE,
)

# ── Abreviaturas de chat (sólo como palabra completa) ─────────────────────────
_ABBREV = {
    "q": "que", "k": "que", "qe": "que", "x": "por", "xq": "porque", "pq": "porque", "porq": "porque",
    "d": "de", "dnd": "donde", "tb": "también", "tmb": "también", "tbn": "también",
    "toy": "estoy", "pa": "para", "nd": "nada", "msj": "mensaje", "info": "información",
    "hr": "hora", "hrs": "horas", "xfa": "", "xfavor": "",
}
_ABBREV_RE = re.compile(r"(?<![\w])(" + "|".join(sorted(map(re.escape, _ABBREV), key=len, reverse=True)) + r")(?![\w])",
                        re.IGNORECASE)

_ELLIPSIS = re.compile(r"\.{2,}|…")


def _apply_correction(text: str) -> str:
    """Si el mensaje se rectifica a sí mismo, se queda la última versión."""
    cut, start = None, None
    for rx in (_CORRECTION, _CORRECTION_EXPLICIT):
        for m in rx.finditer(text):
            if cut is None or m.end() > cut:
                cut, start = m.end(), m.start()
    if cut is None:
        return text
    # Sin nada delante ("no, mejor el primero"), la rectificación es sobre el
    # turno ANTERIOR: el "no" es información para la comprensión (qué lectura se
    # descarta) y se conserva. Sólo se recorta lo que el propio mensaje descartó.
    # El "mejor" de "no, mejor..." es un marcador de discurso, no un
    # superlativo: se quita para que no se lea como "el mejor restaurante".
    if not text[:start].strip(" ,.;:¿¡!?…"):
        tail = text[cut:].strip(" ,.;:")
        if not re.search(r"\w{3,}", tail):
            return text
        negated = re.search(r"\bno\b", text[start:cut], re.IGNORECASE)
        return f"no, {tail}" if negated else tail
    tail = text[cut:].strip(" ,.;:")
    # Una rectificación sin contenido ("no, mejor no") no reemplaza nada.
    if not re.search(r"\w{3,}", tail) or tail.lower() in ("nada", "nada mas", "nada más"):
        return text
    return tail


def _strip_preamble(text: str) -> str:
    greeting = ""
    m = _GREETING.match(text)
    if m:
        greeting, text = m.group(1).strip(), text[m.end():]
    for _ in range(6):  # "mira, es que, lo que pasa es que ..."
        m = _PREAMBLE.match(text)
        if not m or m.end() == 0:
            break
        rest = text[m.end():]
        if not rest.strip():
            break  # "mira" a secas no se borra: es todo el mensaje
        text = rest
    return (greeting + " " + text).strip() if greeting else text.strip()


def _collapse_repeats(text: str) -> str:
    """'lyra lyra lyra' → 'lyra'; 'barbería vip barbería vip' → 'barbería vip'."""
    words = text.split()
    for n in (4, 3, 2, 1):
        i, out = 0, []
        while i < len(words):
            chunk = words[i:i + n]
            out.extend(chunk)
            j = i + n
            while len(chunk) == n and [w.lower().strip(",.!?") for w in words[j:j + n]] == [w.lower().strip(",.!?") for w in chunk]:
                j += n
            i = j
        words = out
    return " ".join(words)


def clean_utterance(text: str) -> str:
    """Petición final del usuario, sin arranques, muletillas ni versiones descartadas."""
    if not text or not text.strip():
        return text
    original = text
    out = _ELLIPSIS.sub(", ", text)
    out = _apply_correction(out)
    if len(out.split()) > 1:  # "x" o "q" a secas no se reinterpretan
        out = _ABBREV_RE.sub(lambda m: _ABBREV[m.group(1).lower()], out)
    out = _FILLERS.sub(" ", out)
    out = _strip_preamble(out)
    out = _collapse_repeats(out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])\s*(?=[,;:.!?]|$)", "", out)
    out = _SPACES.sub(" ", out).strip(" ,;:")
    return out if re.search(r"\w", out) else original


def self_names(project_config: dict) -> list:
    """Cómo puede llamar el usuario al asistente o a la plataforma de este proyecto."""
    from core.semantic.morphology import normalize
    names = [project_config.get("name"), project_config.get("assistant_name"), project_config.get("slug")]
    names += list((project_config.get("personalities") or {}).keys())
    slug = project_config.get("slug") or project_config.get("project_id")
    if slug:
        # Las personalidades del proyecto (Lyra, Nexo, Sena...) también son el asistente.
        try:
            import yaml
            from pathlib import Path
            path = Path(__file__).resolve().parents[2] / "projects" / f"personalities_{slug}.yaml"
            if path.exists():
                personas = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                names += list(personas.keys()) + [v.get("assistant_name") for v in personas.values() if isinstance(v, dict)]
        except Exception:
            pass
    out = []
    for n in names:
        if n:
            out.append(n)
            # "NexiService Colombia" también se dice "NexiService".
            # sólo la parte que es el nombre del producto, no un lugar ("Colombia").
            out.extend(w for w in str(n).split() if len(w) >= 6 and slug and normalize(w) == normalize(slug))
    return list(dict.fromkeys(out))


def prepare_for_understanding(text: str, project_id: str, project_config: dict = None) -> str:
    """
    Lo que la comprensión debe leer de un mensaje: la petición final (sin
    arranques ni muletillas) y, en NexiService, sin las menciones al propio
    asistente o a la plataforma. Es el mismo paso en /chat y en la evaluación.
    """
    out = clean_utterance(text)
    if project_id == "nexiservice":
        from core.semantic.self_reference import strip_self_reference
        names = self_names(project_config or {}) or ["lyra", "nexo", "nexiservice"]
        out = strip_self_reference(out, names)
    return out
