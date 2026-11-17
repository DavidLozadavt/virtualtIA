"""
orchestrator/interceptors/schoolsena_nlu.py — Comprensión local de SchoolSena.

Reemplaza la cascada de palabras clave en orden fijo, donde la primera regla
que tocaba una subcadena ganaba ("cuánto cuesta" iba a nómina; cualquier "hoy",
incluido "el clima de hoy", iba a clases de hoy; "general" iba al dashboard).

Cómo decide ahora:

1. Cada capacidad declara los CONCEPTOS que la evocan con un peso. El mensaje se
   compara por raíz y con tolerancia a erratas y a ruido de STT (la misma
   morfología que usa NexiService), así que "intructores", "calificasiones" u
   "horarioa" se reconocen sin escribir variantes.
2. Lo temporal (hoy, mañana, el viernes, la semana, el mes) es una RANURA, no
   una intención: "actividades para hoy" es una consulta de actividades.
3. Las capacidades compiten por puntaje. Unas pocas relaciones estructurales
   resuelven los choques reales (una ficha + un momento es el horario del
   instructor; una pregunta de dinero sobre instructores es nómina).
4. Si nada alcanza el umbral, se usa la conversación: un "¿y mañana?" o un
   "¿y de la 2601?" continúan la consulta anterior con la ranura nueva.
5. Lo que no corresponde a SchoolSena se rechaza aquí con una frase; lo ambiguo
   se pregunta. Ninguno de los dos necesita al modelo.

El resultado es un `Reading`; el interceptor decide permisos y ejecuta.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from difflib import SequenceMatcher

from core.semantic.morphology import normalize, phonetic, stem, strict_closeness

# ── Inventario de capacidades ─────────────────────────────────────────────────
# Peso 1.0 = el concepto basta por sí solo. Pesos menores sólo suman.

CAPABILITIES: dict[str, dict[str, float]] = {
    "schedule": {
        "clase": 1.0, "horario": 1.0, "materia": 0.8, "calendario": 0.9, "cronograma": 0.9,
        "agenda": 0.7, "aula": 0.7, "salon": 0.7, "ambiente": 0.4, "jornada": 0.6,
        "hora": 0.5, "entrar": 0.5, "toca": 0.6, "tocar": 0.6,
    },
    "activities": {
        "actividad": 1.0, "tarea": 1.0, "taller": 0.9, "evidencia": 1.0, "pendiente": 0.6,
        "trabajo": 0.5, "faltan": 0.5, "faltar": 0.5, "deber": 0.4, "entregar": 0.5,
    },
    "grades": {
        "nota": 1.0, "calificacion": 1.0, "promedio": 1.0, "rendimiento": 0.9, "aprobar": 0.8,
        "reprobar": 0.8, "perder": 0.5, "pasar": 0.4, "saque": 0.8, "sacar": 0.6, "voy": 0.3,
    },
    "fichas": {"ficha": 1.0, "grupo": 0.9},
    "students": {"estudiante": 1.0, "aprendiz": 1.0, "alumno": 1.0, "inscrito": 0.7, "integrante": 0.7},
    "deliveries": {"entrega": 0.8, "entregaron": 1.0, "entrego": 1.0},
    "payroll": {"nomina": 1.0, "salario": 0.9, "sueldo": 0.9, "gana": 0.6, "ganan": 0.6, "paga": 0.5, "pago": 0.4},
    "instructors": {"instructor": 1.0, "docente": 1.0, "profesor": 0.9},
    "contracts": {"contrato": 1.0, "vencimiento": 0.9, "vencer": 0.8, "vencen": 0.8, "expirar": 0.8},
    "dashboard": {
        "dashboard": 1.0, "estadistica": 1.0, "indicador": 0.9, "resumen": 0.7,
        "estado": 0.4, "general": 0.5, "centro": 0.3,
    },
    # Gestión académica: lo que existe en School más allá del aula.
    "centers": {"formacion": 0.5, "centro": 0.5},
    "sites": {"sede": 1.0},
    "rooms": {"ambiente": 1.0, "infraestructura": 1.0},
    "periods": {"periodo": 1.0, "trimestre": 0.9, "semestre": 0.9},
    "shifts": {"jornada": 1.0},
    "planning": {"planeacion": 1.0, "planificacion": 1.0, "red": 0.5, "redes": 0.5},
}

#: Capacidades que devuelven una LISTA que se puede mostrar, contar o leer.
LISTABLE = {"centers", "sites", "rooms", "periods", "shifts", "planning", "instructors", "fichas", "contracts"}

#: Qué hace el usuario con lo que pide. Navegar lo resuelve el interceptor.
_COUNT_RE = re.compile(r"\b(cuant[oa]s|numero de|cantidad de|total de)\b")
_READ_RE = re.compile(r"\b(lee|leeme|leemelos|leemelas|leelos|leelas|leer|leerlos|leermelos|dictame|"
                      r"dimelos|dimelas|en voz alta|nombramelos)\b")


def read_act(text: str) -> str:
    """'count' (¿cuántos?), 'read' (léemelos) o 'list' (muéstrame / qué hay)."""
    if _READ_RE.search(text):
        return "read"
    if _COUNT_RE.search(text):
        return "count"
    return "list"


#: Un examen con verbo de resultado o en pasado pregunta por la nota.
_RESULT_RE = re.compile(r"\b(saque|saco|sacaste|sacamos|fue|salio|sali|nota|notas|resultado|resultados|"
                        r"calificacion|aprobe|pase|perdi|gane|me fue|como me)\b")

# Capacidades que el usuario puede pedir según su rol (además de las de lectura
# propia). Coincide con `allowed_tools` del interceptor.
_THRESHOLD = 0.9

_STOP = {
    "que", "q", "de", "del", "la", "las", "el", "los", "un", "una", "unos", "unas", "mi", "mis", "me",
    "tu", "tus", "y", "o", "a", "al", "en", "con", "por", "para", "es", "son", "hay", "tengo", "tienes",
    "tiene", "quiero", "quisiera", "saber", "ver", "dime", "dame", "muestrame", "mostrar", "cual",
    "cuales", "como", "cuando", "donde", "quien", "quienes", "cuanto", "cuantos", "cuanta", "cuantas",
    "lo", "le", "les", "se", "si", "no", "ya", "muy", "mas", "algun", "alguna", "alguno", "todo", "todos",
    "todas", "esta", "este", "esto", "eso", "ese", "esa", "sobre", "hola", "favor", "porfa", "lyra",
    "sena", "schoolsena", "por", "ahi", "aqui", "consultar", "necesito", "puedes", "podrias",
    "gustaria", "seria", "estan", "esta", "ser", "estar", "hacer", "tener", "ir", "voy" , "nexo",
}
#: Palabras de tiempo: un "¿y mañana?" suelto sólo trae esto.
_TIME_WORDS = {"hoy", "manana", "ayer", "anteayer", "antier", "semana", "mes", "ahora", "tarde", "noche",
               "pasado", "proximo", "proxima", "siguiente", "lunes", "martes", "miercoles", "jueves",
               "viernes", "sabado", "domingo", "dia"}
# "voy" va en _STOP pero también evoca notas en "cómo voy": se mira en frases.

_GREETING_RE = re.compile(r"^(hola|holi|holis|buenas|buenos dias|buenas tardes|buenas noches|hey|que tal|que mas|saludos|ey)\b")
_SMALLTALK_RE = re.compile(r"\b(como estas|como vas|como te va|que tal estas|todo bien)\b")
_THANKS_RE = re.compile(r"^(muchas )?(gracias|grax|thanks|listo gracias|ok gracias|vale gracias|perfecto)\b")
_LAUGH_RE = re.compile(r"^(ja|je|ji|jo){2,}|^(lol|xd|jsjs)")
_HELP_RE = re.compile(
    r"\b(ayuda|ayudame|ayudas|que (puedes|puedo|sabes|se puede) hacer|para que sirves|para que sirve|"
    r"como funciona(s)?|que haces|quien eres|que eres|que puedo preguntar|en que me (ayudas|puedes ayudar))\b"
)
_PROFILE_RE = re.compile(r"\b(quien soy|mi perfil|mis datos|mi rol|mi usuario|mi correo|con que (usuario|rol|cuenta))\b")
_MONEY_RE = re.compile(r"\b(cuanto|cuantos|valor|costo|precio)\b.*\b(cuesta|cuestan|vale|valen|gana|ganan|paga|pagan|cobra|cobran|cuestan|sale|salen)\b|\b(precio|costo|tarifa|valor)\b")
_COURSE_INFO_RE = re.compile(r"\b(curso|cursos|programa|programas|carrera|carreras|tecnico|tecnologo|inscribirme|inscripcion)\b")
_GRADE_PHRASE_RE = re.compile(
    r"\bcomo voy\b|\bcomo me fue\b|\bcuanto (saque|saco|tengo)\b|\bpase\b|\bperdi\b|\bgane\b"
    # "Los resultados de mi examen", "el resultado del parcial": resultado + evaluación es una nota.
    r"|\bresultados?\b.*\b(examen(es)?|pruebas?|parcial(es)?|evaluacion(es)?|quiz|cuestionarios?|test)\b"
    r"|\b(examen(es)?|pruebas?|parcial(es)?|evaluacion(es)?|quiz|cuestionarios?)\b.*\bresultados?\b")
_NUMBER_RE = re.compile(r"\b(\d{1,7})\b")
_ORDINALS = {"primero": 1, "primera": 1, "1ro": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercera": 3,
             "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "ultimo": -1, "ultima": -1}
_WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
_DAY_NAMES = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]

#: Tiempo tras el cual un seguimiento corto ya no continúa la consulta anterior.
_CONTEXT_TTL = 30 * 60


@dataclass
class Reading:
    kind: str                     # capability | greeting | thanks | help | profile | out_of_scope | clarify | none
    capability: Optional[str] = None
    timeframe: Optional[str] = None   # today | tomorrow | week | month | day
    day: Optional[str] = None         # LUNES..DOMINGO cuando timeframe == day/tomorrow
    ficha: Optional[str] = None
    actividad: Optional[str] = None
    scope: Optional[str] = None       # fichas: mine | active
    reason: str = ""
    from_context: bool = False
    candidates: list = field(default_factory=list)
    on_date: Optional["date"] = None        # fecha pasada concreta ("ayer")
    filter_words: list = field(default_factory=list)  # palabras que nombran la actividad/materia
    mentions_exam: bool = False
    act: str = "list"             # list | count | read


# ── Lectura de ranuras ────────────────────────────────────────────────────────

_EXAM_WORDS = {"examen", "examenes", "prueba", "pruebas", "parcial", "parciales", "quiz", "evaluacion",
               "evaluaciones", "cuestionario", "test"}
_PAST_WORDS = {"ayer": 1, "anteayer": 2, "antier": 2}


def _fold_repeats(word: str) -> str:
    """'ayyer' → 'ayer': una letra repetida por error de tecleo o de STT."""
    return re.sub(r"(.)\1+", r"\1", word)


def read_past_date(text: str, now: Optional[datetime] = None):
    """La fecha pasada que nombra el mensaje ("ayer", "el lunes pasado"), o None."""
    now = now or datetime.now()
    for w in text.split():
        # "ayyer", "ayerr": se pliegan las letras repetidas antes de comparar.
        folded = _fold_repeats(w)
        if folded in _PAST_WORDS:
            return (now - timedelta(days=_PAST_WORDS[folded])).date()
    m = re.search(r"\b(" + "|".join(_WEEKDAYS) + r")\s+pasado\b", text)
    if m:
        delta = (now.weekday() - _WEEKDAYS[m.group(1)]) % 7 or 7
        return (now - timedelta(days=delta)).date()
    return None


def read_timeframe(text: str, now: Optional[datetime] = None) -> tuple[Optional[str], Optional[str]]:
    """(timeframe, día) que expresa el mensaje, o (None, None)."""
    now = now or datetime.now()
    t = text
    # normalize() conserva la ñ: se aceptan las dos grafías.
    if re.search(r"\bpasado ma[nñ]ana\b", t):
        return "day", _DAY_NAMES[(now.weekday() + 2) % 7]
    # "de la mañana" / "por la mañana" es una franja del día, no mañana.
    if re.search(r"(?<!la )\bma[nñ]ana\b", t):
        return "tomorrow", _DAY_NAMES[(now.weekday() + 1) % 7]
    for name, idx in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            return "day", _DAY_NAMES[idx]
    if re.search(r"\b(hoy|ahora|ahorita|ya mismo|en este momento|este momento|proxima clase|clase mas cercana|"
                 r"mas cercana|mas temprana|primera clase)\b", t):
        return "today", _DAY_NAMES[now.weekday()]
    if re.search(r"\bseman\w*\b", t):
        return "week", None
    if re.search(r"\b(mes|mensual)\b", t):
        return "month", None
    return None, None


def _content_words(text: str) -> list[str]:
    return [w for w in text.split() if w not in _STOP and not w.isdigit() and len(w) > 1]


def _concept_hits(words: list[str]) -> dict[str, float]:
    """Puntaje de cada capacidad: la mejor coincidencia de cada palabra, una vez por concepto."""
    scores: dict[str, float] = {}
    for cap, concepts in CAPABILITIES.items():
        used = set()
        total = 0.0
        for w in words:
            ws = stem(w)
            best, best_c = 0.0, None
            for c, weight in concepts.items():
                if c in used:
                    continue
                sim = _closeness(w, ws, c)
                if not sim:
                    continue
                if sim * weight > best:
                    best, best_c = sim * weight, c
            if best_c:
                used.add(best_c)
                total += best
        if total:
            scores[cap] = round(total, 3)
    return scores


def _closeness(word: str, word_stem: str, concept: str) -> float:
    """Comparación estricta compartida (core.semantic.morphology.strict_closeness)."""
    return strict_closeness(word, concept)


def _ordinal(words: list[str]) -> Optional[int]:
    for w in words:
        if w in _ORDINALS:
            return _ORDINALS[w]
    return None


# ── Lectura principal ─────────────────────────────────────────────────────────

def read(message: str, state: Optional[dict] = None, role_flags: tuple = (False, False, False),
         now: Optional[datetime] = None) -> Reading:
    """Interpreta un mensaje de SchoolSena con el estado que dejó la conversación."""
    is_coord, is_instr, is_student = role_flags
    text = normalize((message or "").replace("_", " "))
    if not text:
        return Reading(kind="clarify", reason="empty")
    words = text.split()
    content = _content_words(text)
    timeframe, day = read_timeframe(text, now)
    past = read_past_date(text, now)
    if past and timeframe in (None, "day"):
        # "¿Qué clases tuve ayer?" es el día de la semana de ayer.
        timeframe, day = "day", _DAY_NAMES[past.weekday()]
    numbers = _NUMBER_RE.findall(text)
    state = _live_state(state)

    # Social: saludo puro, agradecimiento, risa, "¿cómo estás?".
    scores = _concept_hits(content)
    if _GRADE_PHRASE_RE.search(text):
        scores["grades"] = max(scores.get("grades", 0), 1.0)
    has_cap = any(s >= _THRESHOLD for s in scores.values())

    if not has_cap and not timeframe:
        if _HELP_RE.search(text):
            return Reading(kind="help")
        if _THANKS_RE.search(text):
            return Reading(kind="thanks")
        if _GREETING_RE.search(text) or _SMALLTALK_RE.search(text) or _LAUGH_RE.search(text):
            return Reading(kind="greeting")
        if _PROFILE_RE.search(text):
            return Reading(kind="profile")
    elif _HELP_RE.search(text) and not content:
        return Reading(kind="help")

    if _PROFILE_RE.search(text) and not has_cap:
        return Reading(kind="profile")
    # "¿Cuál es MI centro de formación?" es un dato del perfil, no la lista de
    # todos los centros (que además puede no tener permiso de ver).
    if re.search(r"\b(mi|mis) (centro|sede|regional)\b", text) and not re.search(
            r"\b(horario|clases?|notas?|actividad\w*|fichas?)\b", text):
        return Reading(kind="profile")
    # "Como instructor, ¿qué puedo hacer?": el rol dice desde dónde pregunta.
    if _HELP_RE.search(text) and re.search(
            r"\bcomo (instructor|aprendiz|coordinador|administrador|estudiante|docente|admin)\b", text):
        return Reading(kind="help")

    # Dinero: SchoolSena sólo conoce la nómina. "¿Cuánto gana un instructor?"
    # es nómina; "¿cuánto cuesta el servicio?" no es nada de este sistema.
    if _MONEY_RE.search(text):
        if scores.get("payroll") or scores.get("instructors") or scores.get("contracts"):
            scores["payroll"] = max(scores.get("payroll", 0), 1.0)
            scores.pop("instructors", None)
        elif not has_cap:
            return Reading(kind="out_of_scope", reason="money")

    if any(w in _EXAM_WORDS for w in words):
        # "¿Qué saqué en el parcial de ayer?": el examen ya ocurrió → su nota.
        # "Tengo examen el viernes": todavía no → es una actividad por presentar.
        if past or _RESULT_RE.search(text):
            scores["grades"] = max(scores.get("grades", 0), 1.0)
            scores.pop("schedule", None)
        elif timeframe in ("today", "tomorrow", "day", "week", "month"):
            scores["activities"] = max(scores.get("activities", 0), 1.0)
    if re.search(r"\bcentros?\s+de\s+formacion\b", text) or (
            re.search(r"\bcentros\b", text) and not re.search(r"\b(resumen|como va|estado)\b", text)):
        scores["centers"] = max(scores.get("centers", 0), 1.0)
        scores.pop("dashboard", None)

    ficha_num = _number_after(text, ("ficha", "fichas", "grupo"))
    act_num = _number_after(text, ("actividad", "actividades", "tarea"))

    # Relaciones estructurales entre capacidades.
    if ficha_num and re.search(r"\b(quien|quienes|cuantos|cuantas|lista|integrantes)\b", text):
        scores["students"] = max(scores.get("students", 0), 1.0)
    if re.search(r"\bcomo (va|esta|anda|vamos)\b", text) and re.search(r"\b(centro|sede|sistema|general)\b", text):
        scores["dashboard"] = max(scores.get("dashboard", 0), 1.0)
    if scores.get("deliveries") and (act_num or scores.get("activities")) and not is_student_reading(text):
        scores["deliveries"] += 1.0
    if "fichas" in scores and timeframe:
        # "¿Qué fichas tengo hoy?": las fichas en un momento son el horario.
        scores["schedule"] = max(scores.get("schedule", 0), scores.pop("fichas") + 0.1)
    if scores.get("students") and ("fichas" in scores or ficha_num):
        scores["students"] += scores.pop("fichas", 0)
    if scores.get("schedule") and scores.get("instructors"):
        # "horario instructor" es el horario propio, no la lista de instructores.
        scores.pop("instructors")
    if scores.get("activities") and scores.get("schedule") and timeframe and scores["activities"] >= _THRESHOLD:
        # "actividades pendientes para hoy": el momento acota las actividades.
        if scores["activities"] >= scores["schedule"]:
            scores.pop("schedule")

    ranked = sorted(((s, c) for c, s in scores.items() if s >= _THRESHOLD), reverse=True)

    if ranked:
        best_score, best = ranked[0]
        rivals = [c for s, c in ranked[1:] if s >= best_score - 0.05]
        if rivals and {best, *rivals} == {"schedule", "activities"}:
            return Reading(kind="clarify", reason="schedule_or_activities", candidates=[best, *rivals],
                           timeframe=timeframe, day=day)
        r = _build(best, text, timeframe, day, ficha_num, act_num, words, state, is_coord, is_instr)
        r.act = read_act(text)
        if past and r.kind == "capability":
            r.on_date = past
        if r.capability == "grades":
            r.on_date = past
            r.mentions_exam = any(w in _EXAM_WORDS for w in words)
            grade_words = set(CAPABILITIES["grades"])
            r.filter_words = [w for w in content if w not in _EXAM_WORDS and w not in _PAST_WORDS
                              and w not in ("resultado", "resultados")
                              and _fold_repeats(w) not in _PAST_WORDS
                              and not any(_closeness(w, stem(w), c) for c in grade_words)
                              and w not in ("fue", "salio", "saque", "sacaste", "como", "pasado")
                              and w not in _WEEKDAYS and len(w) > 3
                              # "materias", "clases": nombran una capacidad, no una actividad.
                              and not any(_closeness(w, stem(w), c) for cs in CAPABILITIES.values() for c in cs)][:3]
        return r

    # Sin capacidad propia: ¿continúa la consulta anterior?
    follow = _follow_up(text, words, content, timeframe, day, numbers, state)
    if follow and past:
        follow.on_date = past
    if follow:
        return follow

    # Ficha suelta ("ficha 21", "fichas") sin más: según el rol.
    if ficha_num:
        return Reading(kind="capability", capability="students", ficha=ficha_num)

    if _COURSE_INFO_RE.search(text):
        return Reading(kind="out_of_scope", reason="course_catalog")

    if not content:
        return Reading(kind="clarify", reason="no_content")
    # Una petición con contenido propio que no toca nada de SchoolSena es de
    # otro tema ("quién ganó el partido", "escríbeme un poema"); una palabra
    # suelta que no se reconoce puede ser una errata y se pregunta.
    if len(content) >= 2 and not any(v >= 0.7 for v in scores.values()):
        return Reading(kind="out_of_scope", reason="unrelated")
    return Reading(kind="none", reason="unmatched")


def is_student_reading(text: str) -> bool:
    """'Tengo actividades sin entregar' habla de lo propio, no de quién entregó."""
    return bool(re.search(r"\b(tengo|me falta|me faltan|debo|sin entregar|por entregar)\b", text))


def _number_after(text: str, heads: tuple) -> Optional[str]:
    m = re.search(r"\b(?:" + "|".join(heads) + r")\s*(?:n(?:umero|o|ro)?\.?\s*)?(\d{1,7})\b", text)
    return m.group(1) if m else None


def _build(cap: str, text: str, timeframe, day, ficha_num, act_num, words, state, is_coord, is_instr) -> Reading:
    r = Reading(kind="capability", capability=cap, timeframe=timeframe, day=day)
    if cap == "schedule":
        r.timeframe = timeframe or "week"
    elif cap == "fichas":
        mine = bool(re.search(r"\b(mis|mi|asignad\w*|tengo|doy)\b", text))
        active = bool(re.search(r"\b(activ\w*|del centro|todas|todos|centro)\b", text))
        r.scope = "active" if (active and not mine) or (not mine and is_coord and not is_instr) else (
            "mine" if is_instr or mine else "active")
    elif cap == "students":
        r.ficha = ficha_num or _ref_from_state(words, state, "ficha")
        if not r.ficha:
            return Reading(kind="clarify", reason="missing_ficha", capability="students")
    elif cap == "deliveries":
        r.actividad = act_num or _ref_from_state(words, state, "actividad")
        if not r.actividad:
            return Reading(kind="clarify", reason="missing_actividad", capability="deliveries")
    return r


def _ref_from_state(words: list[str], state: dict, kind: str) -> Optional[str]:
    """'El primero', 'la segunda' sobre lo último que se mostró, o el último valor usado."""
    pos = _ordinal(words)
    presented = [p for p in (state.get("presented") or []) if p.get("type") == kind]
    if pos and presented:
        idx = pos - 1 if pos > 0 else len(presented) - 1
        if 0 <= idx < len(presented):
            return str(presented[idx]["id"])
    if re.search(r"\b(esa|ese|esa misma|la misma|el mismo)\b", " ".join(words)):
        return state.get(kind)
    return None


def _follow_up(text, words, content, timeframe, day, numbers, state) -> Optional[Reading]:
    """Mensaje corto que sólo cambia una ranura de la consulta anterior."""
    last = state.get("cap")
    act = read_act(text)
    if last and act != "list" and len(content) <= 3:
        # "Pero léemelos tú", "¿y cuántos son?": lo mismo de antes, contado o leído.
        r = Reading(kind="capability", capability=last, from_context=True, timeframe=state.get("timeframe"))
        r.act = act
        if last == "students":
            r.ficha = state.get("ficha")
        if last == "deliveries":
            r.actividad = state.get("actividad")
        return r
    if not last:
        if timeframe and not [w for w in content if w not in _TIME_WORDS and not _concept_hits([w])]:
            # "¿y mañana?" sin consulta previa: lo temporal suelto es horario.
            return Reading(kind="capability", capability="schedule", timeframe=timeframe, day=day,
                           from_context=True, reason="bare_timeframe")
        return None
    if len(content) > 3:
        return None
    if timeframe:
        # La consulta anterior sigue si admite un momento: "¿y la de ayer?"
        # después de las notas sigue siendo notas, no el horario.
        cap = last if last in ("schedule", "activities", "grades") else "schedule"
        return Reading(kind="capability", capability=cap, timeframe=timeframe, day=day, from_context=True)
    if numbers and last in ("students", "deliveries"):
        r = Reading(kind="capability", capability=last, from_context=True)
        if last == "students":
            r.ficha = numbers[0]
        else:
            r.actividad = numbers[0]
        return r
    pos = _ordinal(words)
    if pos and last == "fichas":
        ficha = _ref_from_state(words, state, "ficha")
        if ficha:
            return Reading(kind="capability", capability="students", ficha=ficha, from_context=True)
    return None


def _live_state(state: Optional[dict]) -> dict:
    if not isinstance(state, dict):
        return {}
    if time.time() - float(state.get("ts") or 0) > _CONTEXT_TTL:
        return {}
    return state


def remember(state: Optional[dict], reading: Reading, presented: Optional[list] = None) -> dict:
    """Estado que la próxima lectura necesita para entender un seguimiento."""
    new = dict(_live_state(state))
    if reading.kind == "capability":
        new["cap"] = "fichas" if reading.capability == "fichas" else reading.capability
        new["timeframe"] = reading.timeframe
        if reading.ficha:
            new["ficha"] = reading.ficha
        if reading.actividad:
            new["actividad"] = reading.actividad
        if presented is not None:
            new["presented"] = presented[:20]
    new["ts"] = time.time()
    return new


def target_day(reading: Reading, now: Optional[datetime] = None) -> Optional[str]:
    now = now or datetime.now()
    if reading.timeframe == "today":
        return _DAY_NAMES[now.weekday()]
    if reading.timeframe == "tomorrow":
        return _DAY_NAMES[(now.weekday() + 1) % 7]
    if reading.timeframe == "day":
        return reading.day
    return None


def day_label(day: Optional[str], now: Optional[datetime] = None, on_date=None) -> str:
    """'el viernes 20/11' para un día de esta semana o la siguiente; 'Lunes 16/11' si ya pasó."""
    if on_date is not None:
        nombre = _DAY_NAMES[on_date.weekday()].capitalize().replace("Miercoles", "Miércoles").replace("Sabado", "Sábado")
        return f"{nombre} {on_date.strftime('%d/%m')}"
    if not day or day not in _DAY_NAMES:
        return ""
    now = now or datetime.now()
    delta = (_DAY_NAMES.index(day) - now.weekday()) % 7
    date = now + timedelta(days=delta)
    return f"{day.capitalize().replace('Miercoles', 'Miércoles').replace('Sabado', 'Sábado')} {date.strftime('%d/%m')}"
