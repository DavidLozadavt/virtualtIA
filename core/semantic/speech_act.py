"""
core/semantic/speech_act.py — Etapa A: ¿qué está HACIENDO el usuario?

Antes de preguntarse "¿de qué negocio habla?", el sistema tiene que preguntarse
"¿esto es siquiera una petición?". El router anterior nunca hacía esa pregunta:
asumía que todo texto era el nombre de una empresa mientras no se demostrara lo
contrario, y por eso "necesito que te mueras" terminaba en un LIKE de SQL.

Aquí se invierte la carga de la prueba. Un mensaje se convierte en búsqueda sólo
si su ESTRUCTURA expresa una búsqueda y, además, su contenido logra anclarse a
algo real (etapas B y C). Este módulo se encarga de la primera mitad.

El análisis usa exclusivamente clases cerradas del español —pronombres,
interrogativos, modalidad, deixis, ordinales, morfología de persona— más marcos
semánticos universales (cita, agente humano, tiempo). No conoce ni una sola
palabra del catálogo de NexiService: eso es deliberado, y es lo que hace que
generalice a formulaciones nunca vistas.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from core.semantic import lexicon as lx
from core.semantic.morphology import in_stem_set, normalize, phonetic_stem, tokens
from core.semantic.types import Act


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. RESULTADO DEL ANÁLISIS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Analysis:
    """Lectura estructural de un mensaje, sin ninguna referencia al dominio."""
    act: str
    #: Palabras de clase abierta que quedaron tras quitar el andamiaje gramatical.
    content_terms: List[str] = field(default_factory=list)
    #: Marcos semánticos universales detectados (cita, agente humano, tiempo...).
    frames: Set[str] = field(default_factory=set)
    #: Posición pedida explícitamente ("el segundo" → 2).
    ordinal: Optional[int] = None
    #: El mensaje apunta a algo dicho antes ("ese", "ahí", "con ella").
    anaphoric: bool = False
    #: El mensaje se dirige al asistente mismo, no al catálogo.
    agent_directed: bool = False
    #: Rasgos gramaticales crudos, útiles para depurar decisiones.
    markers: Dict[str, bool] = field(default_factory=dict)
    confidence: float = 0.0

    @property
    def has_content(self) -> bool:
        return bool(self.content_terms)


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. MORFOLOGÍA DE PERSONA
# ═══════════════════════════════════════════════════════════════════════════════
# En español la persona verbal está en la desinencia, no en una lista de verbos.
# 1ª singular termina en -o ("puedo", "busco", "quiero"); 2ª singular en -s
# ("puedes", "buscas", "tienes"). Con eso basta para saber si el usuario habla de
# sí mismo o le está hablando al asistente.

_SECOND_PERSON_ENDINGS = ("as", "es", "ias", "aras", "eras", "irias", "iste", "aste")
_FIRST_PERSON_ENDINGS = ("o", "e", "i", "aba", "ia", "are", "ere", "ire")


def verb_person(token: str) -> Optional[int]:
    """
    Persona gramatical probable de una forma verbal: 1, 2 o None.

    Sólo se consulta para tokens que ya se sabe que son verbos de clase cerrada,
    así que no necesita distinguir sustantivos.
    """
    t = normalize(token)
    if len(t) < 3:
        return None
    if t.endswith(_SECOND_PERSON_ENDINGS):
        return 2
    if t.endswith(_FIRST_PERSON_ENDINGS):
        return 1
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. EXTRACCIÓN DE RASGOS
# ═══════════════════════════════════════════════════════════════════════════════

_LAUGHTER_RE = re.compile(lx.LAUGHTER_PATTERN)


#: Marcos, en orden de especificidad. El primero que reclama un token se lo
#: queda: así "servicios" cuenta como marco de oferta y no como el verbo
#: "servir", con el que comparte raíz.
_FRAME_ORDER = (
    ("compare",       lambda: lx.COMPARE_FRAME_STEMS),
    ("recommend",     lambda: lx.RECOMMEND_FRAME_STEMS),
    ("identity",      lambda: lx.IDENTITY_FRAME_STEMS),
    ("web",           lambda: lx.WEB_FRAME_STEMS),
    ("review",        lambda: lx.REVIEW_FRAME_STEMS),
    ("appointment",   lambda: lx.APPOINTMENT_FRAME_STEMS),
    ("human_agent",   lambda: lx.HUMAN_AGENT_FRAME_STEMS),
    ("map",           lambda: lx.MAP_FRAME_STEMS),
    ("offering",      lambda: lx.OFFERING_FRAME_STEMS),
    ("generic_place", lambda: lx.GENERIC_PLACE_STEMS),
    ("temporal",      lambda: lx.TEMPORAL_FRAME_STEMS),
)


def _assign_roles(toks: List[str], text_norm: str) -> tuple:
    """
    Reparte cada palabra entre marcos semánticos y clases verbales.

    Una palabra desempeña UN papel. Sin ese reparto, raíces que se solapan
    ("servicios"/"servir", "atender"/"tener") activaban dos lecturas a la vez y
    la cascada elegía la equivocada.

    Devuelve (marcos, raíces de verbos libres, tokens de verbos libres).
    """
    frames: Set[str] = set()
    free_stems: Set[str] = set()
    free_tokens: List[str] = []

    for idx, tok in enumerate(toks):
        s = phonetic_stem(tok)
        claimed = False
        if tok in lx.CALENDAR_WORDS or tok in lx.MERIDIEM_FORMS:
            frames.add("temporal")
            claimed = True
        elif is_clock_numeral(toks, idx):
            # El numeral pertenece a una expresión horaria: es CUÁNDO, no QUÉ.
            frames.add("temporal")
            claimed = True
        else:
            for name, getter in _FRAME_ORDER:
                if in_stem_set(s, getter()):
                    frames.add(name)
                    claimed = True
                    break
        if not claimed:
            free_stems.add(s)
            free_tokens.append(tok)

    if _contains_phrase(text_norm, lx.REVIEW_PHRASES):
        frames.add("review")

    return frames, free_stems, free_tokens


def state_bearing_frames(frames: Set[str]) -> bool:
    """
    ¿Alguno de los marcos apunta a una entidad concreta de la conversación?

    "¿Qué es una reserva?" pregunta por un concepto; "¿qué tal es?" o "¿quién
    atiende?" preguntan por algo que ya está sobre la mesa. Sólo los segundos
    deben resolverse contra el estado.
    """
    return bool(frames & {"review", "human_agent"})


def _contains_phrase(text: str, phrases) -> bool:
    """
    ¿Aparece alguna locución completa en el texto?

    Con límite de palabra, no como subcadena: "como esta" está dentro de "como
    estás", y sin esta distinción un saludo se leía como consulta de reseñas.
    """
    return any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text) for p in phrases)


def is_clock_numeral(toks: List[str], idx: int) -> bool:
    """
    ¿El numeral de esta posición es la hora del reloj?

    "a las 9", "9 am", "las 3 de la tarde" dicen CUÁNDO. Sin esta distinción el
    numeral se leía como una posición dentro de la última lista mostrada —"a las
    9" pedía el noveno negocio— y la hora nunca llegaba a la reserva.

    La marca decisiva es el determinante PLURAL: "las 9" no puede señalar un
    elemento, porque un elemento no es plural. Lo demás son los acompañantes
    típicos del reloj: meridiano, parte del día y la fracción "y media".
    """
    tok = toks[idx] if 0 <= idx < len(toks) else ""
    if tok not in lx.ORDINALS and not tok.isdigit():
        return False

    prev  = toks[idx - 1] if idx >= 1 else ""
    prev2 = toks[idx - 2] if idx >= 2 else ""
    nxt   = toks[idx + 1] if idx + 1 < len(toks) else ""
    nxt2  = toks[idx + 2] if idx + 2 < len(toks) else ""
    nxt3  = toks[idx + 3] if idx + 3 < len(toks) else ""

    # "a las 9" / "a la 1": el numeral va detrás de la locución del reloj.
    if prev == "las" or (prev2 == "a" and prev in ("la", "las")):
        return True
    # "9 am", "9 pm"
    if nxt in lx.MERIDIEM_FORMS:
        return True
    # "9 de la mañana", "3 de la tarde"
    if nxt == "de" and nxt2 in ("la", "el") and nxt3 in lx.DAYPART_FORMS:
        return True
    # "9 y media", "9 y cuarto"
    if nxt == "y" and nxt2 in ("media", "cuarto"):
        return True
    # "la hora 9", "9 horas"
    if nxt in ("horas", "hrs") or prev in ("hora", "horas"):
        return True
    return False


def _extract_ordinal(toks: List[str], text_norm: str) -> Optional[int]:
    """
    Posición pedida explícitamente.

    Un ordinal sólo selecciona cuando actúa como pronombre ("el primero", "quiero
    el segundo"), no cuando cuenta ("dos personas") ni cuando es un artículo
    ("una barbería"). El determinante previo es lo que marca la diferencia.
    """
    for idx, tok in enumerate(toks):
        if tok not in lx.ORDINALS:
            continue
        # Una hora del día no selecciona nada.
        if is_clock_numeral(toks, idx):
            continue
        prev = toks[idx - 1] if idx > 0 else ""
        # "uno"/"una"/"dos" sólo seleccionan si van precedidos de determinante.
        if tok in ("uno", "una", "dos", "tres", "1", "2", "3") and prev not in lx.ARTICLES:
            continue
        # Un ordinal seguido de contenido es modificador, no selector.
        nxt = toks[idx + 1] if idx + 1 < len(toks) else ""
        if nxt and not lx.is_function_word(nxt):
            continue
        return lx.ORDINALS[tok]
    if "ultimo" in text_norm or "ultima" in text_norm:
        return -1
    return None


def _content_residue(toks: List[str]) -> List[str]:
    """
    Palabras de clase abierta: lo que el mensaje aporta como significado propio.

    Se descarta el andamiaje gramatical y también las palabras que ya quedaron
    explicadas por un marco universal (si el usuario dijo "cita", eso es marco,
    no un concepto que haya que buscar en el catálogo).
    """
    residue: List[str] = []
    for tok in toks:
        if lx.is_function_word(tok):
            continue
        if in_stem_set(phonetic_stem(tok), lx.FRAME_STEMS):
            continue
        if len(tok) <= 1:
            continue
        residue.append(tok)
    return residue


def analyze(message: str) -> Analysis:
    """Lee la estructura de un mensaje y devuelve su acto de habla."""
    text_norm = normalize(message)
    if not text_norm:
        return Analysis(act=Act.UNPARSEABLE, confidence=1.0)

    # La cortesía no aporta función; se retira antes de analizar.
    for formula in lx.POLITENESS:
        text_norm = text_norm.replace(formula, " ")
    text_norm = " ".join(text_norm.split())
    if not text_norm:
        return Analysis(act=Act.THANKS, confidence=0.9)

    toks = [t for t in text_norm.split() if t]
    token_set = set(toks)
    # Cada palabra recibe un papel: o pertenece a un marco semántico, o queda
    # libre para leerse como verbo de clase cerrada.
    frames, word_stems, verb_tokens = _assign_roles(toks, text_norm)

    # ── Rasgos gramaticales ─────────────────────────────────────────────────
    interrogative = bool(token_set & lx.INTERROGATIVES) or "?" in message
    locative_q = bool(token_set & lx.LOCATIVE_INTERROGATIVES)
    person_q = bool(token_set & lx.PERSON_INTERROGATIVES)
    selective_q = bool(token_set & lx.SELECTIVE_INTERROGATIVES)
    manner_q = bool(token_set & lx.MANNER_INTERROGATIVES)

    def _has(stem_set) -> bool:
        return any(in_stem_set(s, stem_set) for s in word_stems)

    has_volitive = _has(lx.VOLITIVE_STEMS)
    has_potential = _has(lx.POTENTIAL_STEMS)
    has_existential = _has(lx.EXISTENTIAL_STEMS)
    has_display = _has(lx.DISPLAY_STEMS)
    has_navigation = _has(lx.NAVIGATION_STEMS)
    has_agent_action = _has(lx.AGENT_ACTION_STEMS)

    anaphoric = bool(token_set & lx.ANAPHORIC)
    locative_deixis = bool(token_set & lx.LOCATIVE_ADVERBS)
    second_person_mark = bool(token_set & lx.SECOND_PERSON_CLITICS)

    # Perífrasis progresiva: cópula + gerundio. "estoy mirando" describe lo que
    # el usuario está haciendo, no le pide nada al sistema.
    has_copula = bool(word_stems & lx.COPULA_STEMS)
    has_gerund = any(t.endswith(("ando", "iendo", "yendo")) for t in toks)
    progressive = has_copula and has_gerund

    # ¿Alguna forma verbal de clase cerrada está en 2ª persona? Ésa es la marca
    # de que el usuario le habla AL asistente y no del mundo. Es lo que separa
    # "¿qué ofreces TÚ?" de "¿qué ofrecen ELLOS?", sin listar ninguna frase.
    _addressable = (
        lx.POTENTIAL_STEMS | lx.AGENT_ACTION_STEMS
        | lx.EXISTENTIAL_STEMS | lx.COPULA_STEMS
    )
    second_person_verb = any(
        verb_person(t) == 2
        for t in verb_tokens
        if in_stem_set(phonetic_stem(t), _addressable)
    )
    # Verbo de agente en 3ª persona: habla de un tercero, típicamente el negocio
    # del que se venía hablando.
    third_person_agent = any(
        verb_person(t) is None and t.endswith(("a", "an", "e", "en"))
        for t in verb_tokens
        if in_stem_set(phonetic_stem(t), lx.AGENT_ACTION_STEMS)
    )
    ordinal = _extract_ordinal(toks, text_norm)
    residue = _content_residue(toks)
    hedged = any(h in text_norm for h in lx.HEDGES)

    markers = {
        "interrogative": interrogative,
        "locative_q": locative_q,
        "person_q": person_q,
        "selective_q": selective_q,
        "volitive": has_volitive,
        "potential": has_potential,
        "existential": has_existential,
        "display": has_display,
        "navigation": has_navigation,
        "agent_action": has_agent_action,
        "second_person": second_person_mark or second_person_verb,
        "hedged": hedged,
        "locative_deixis": locative_deixis,
        "progressive": progressive,
    }

    def result(act: str, confidence: float, **over) -> Analysis:
        return Analysis(
            act=act,
            content_terms=over.get("content_terms", residue),
            frames=over.get("frames", frames),
            ordinal=ordinal,
            anaphoric=anaphoric or locative_deixis,
            agent_directed=over.get("agent_directed", False),
            markers=markers,
            confidence=confidence,
        )

    # ── Cascada de decisión ─────────────────────────────────────────────────
    # Se ordena de la evidencia estructural más específica a la más difusa.

    # 1. Rutinas fáticas: el mensaje se agota en fórmulas sociales.
    social = _social_act(text_norm, toks, token_set, residue)
    if social:
        return result(social, 0.95, content_terms=[])

    # 2. El mensaje se dirige al asistente sobre sí mismo.
    #    "¿qué me puedes ofrecer?", "ayúdame", "¿cómo funciona esto?"
    #    Un marco de servicio activo (cita, personas, valoraciones) desmiente esa
    #    lectura: "estoy buscando dónde hacerme atender" habla del mundo, no de mí.
    #    La 3ª persona también la desmiente: "¿qué ofrecen?" pregunta por ellos.
    # Aceptar algo ya mostrado: "ese me sirve", "el primero me parece bien".
    # El verbo expresa conformidad con un antecedente, no una pregunta al
    # asistente; sin esto "ese me sirve" se leía como "¿para qué sirves?".
    if (anaphoric or ordinal is not None) and not residue and not interrogative:
        return result(Act.REFERENCE, 0.88)

    blocking_frames = frames - {"offering"}
    # "¿Cómo funciona esto?" pregunta por el funcionamiento del propio sistema.
    # La manera es la clave: no se está preguntando qué hay, sino cómo se usa.
    if manner_q and has_agent_action and not residue and not blocking_frames:
        return result(Act.AGENT_CAPABILITY, 0.85, agent_directed=True)

    if has_agent_action and not residue and not blocking_frames and not third_person_agent:
        if second_person_verb or second_person_mark or has_potential or not toks[0:1]:
            return result(Act.AGENT_CAPABILITY, 0.9, agent_directed=True)
        return result(Act.AGENT_CAPABILITY, 0.75, agent_directed=True)

    # Verbo de oferta en 3ª persona: pregunta por lo que ofrece un tercero.
    if third_person_agent and not residue and not blocking_frames:
        return result(Act.ATTRIBUTE, 0.8, frames=frames | {"offering"})

    # "Háblame de ti", "cuéntame sobre ti": el complemento es el asistente.
    # Sin esto, el verbo de exposición hacía que se listara el directorio.
    if second_person_mark and not residue and (has_display or has_existential):
        return result(Act.AGENT_IDENTITY, 0.85, agent_directed=True)

    # Pregunta por una definición: "¿qué es una reserva?", "¿qué es una cita?".
    # Se pide una explicación, no que se abra el proceso correspondiente.
    if interrogative and has_copula and not residue and frames:
        if not anaphoric and not state_bearing_frames(frames):
            return result(Act.AGENT_CAPABILITY, 0.8, agent_directed=True)

    if person_q and second_person_verb and not residue and "human_agent" not in frames:
        return result(Act.AGENT_IDENTITY, 0.85, agent_directed=True)

    # 3. Contenido dirigido al asistente en 2ª persona sin marco de servicio:
    #    no es una necesidad de catálogo, es una interpelación (o una grosería).
    #    "necesito que te mueras" y "eres un inútil" caen aquí y nunca llegan a
    #    la base de datos.
    if (second_person_mark or second_person_verb) and residue and not frames and not has_existential:
        if not (has_display or has_navigation):
            return result(Act.UNPARSEABLE, 0.8, agent_directed=True)

    # 4. Aspecto progresivo sin objeto: el usuario narra que está mirando.
    #    "estoy mirando", "solo estoy viendo" no piden nada.
    if progressive and not residue and not interrogative and not frames:
        return result(Act.BACKCHANNEL, 0.85, content_terms=[])

    # 5. Preguntar POR UN LUGAR domina sobre cualquier otro marco: "¿dónde
    #    puedo agendar?" busca sitios, no abre todavía una reserva.
    if locative_q:
        return result(Act.LOCATIVE, 0.85)

    # 6. Marcos que definen la acción por sí mismos.
    if "review" in frames:
        return result(Act.ATTRIBUTE, 0.85)

    # 7. Selección dentro de un conjunto ya dado. Va antes de los existenciales
    #    porque "¿cuál queda más cerca?" compara lo mostrado; el verbo "queda"
    #    no la convierte en una búsqueda nueva.
    if selective_q:
        return result(Act.ATTRIBUTE, 0.85)

    if ordinal is not None and not residue:
        return result(Act.REFERENCE, 0.9)

    # 8. Petición explícita de cita. Va antes que cualquier lectura existencial:
    #    en "quisiera hacer una reserva para un hospital", lo que el usuario pide
    #    es la reserva, y el hospital es dónde. Leerlo como "¿hay hospitales?"
    #    devuelve una lista y deja la petición sin atender.
    if "appointment" in frames and has_volitive and not person_q:
        return result(Act.BOOKING, 0.9)

    # 9. Pregunta por PERSONAS. El interrogativo de persona es una señal fuerte:
    #    "¿con quién puedo atenderme?" pide el equipo, no una hora.
    if person_q and not residue and (frames & {"human_agent", "appointment"} or anaphoric):
        return result(Act.PERSON_QUERY, 0.85)

    # 9. Consulta existencial sobre el catálogo. Precede a los marcos de cita:
    #    "¿hay médicos disponibles?" busca médicos, no abre una reserva.
    if has_existential and (residue or "generic_place" in frames or locative_deixis):
        return result(Act.EXISTENTIAL, 0.85)

    # 10. Pregunta por lo que ofrece un tercero: "¿qué servicios tiene?",
    #     "¿qué precios manejan?". El marco de oferta manda sobre el existencial.
    if "offering" in frames and not residue and (interrogative or third_person_agent):
        return result(Act.ATTRIBUTE, 0.85)

    # Existencial interrogativo sin contenido propio ni antecedente: es una
    # pregunta abierta por el directorio ("¿qué hay disponible?").
    if has_existential and interrogative and not anaphoric and "human_agent" not in frames:
        return result(Act.EXISTENTIAL, 0.8)

    if "appointment" in frames:
        # Preguntar SI hay cita disponible no es lo mismo que pedirla.
        if has_existential and (interrogative or anaphoric):
            return result(Act.ATTRIBUTE, 0.85)
        return result(Act.BOOKING, 0.9)

    # 9. Personas que atienden. El marco sólo manda si el mensaje trata DE ellas:
    #    en "un corte de cabello profesional", "profesional" es un adjetivo.
    if (person_q and anaphoric and not residue) or ("human_agent" in frames and not residue):
        return result(Act.PERSON_QUERY, 0.85)

    if "offering" in frames and not residue:
        return result(Act.ATTRIBUTE, 0.8)

    if anaphoric and not residue:
        # "ese me interesa", "quiero ir a ese", "con ella"
        return result(Act.REFERENCE, 0.85)

    if interrogative and anaphoric:
        return result(Act.ATTRIBUTE, 0.8)

    # 10. Complemento temporal suelto: "mañana en la tarde", "a las 3".
    if "temporal" in frames and not residue and not has_volitive:
        return result(Act.TEMPORAL, 0.85)

    # 11. Deixis de lugar sin más contenido: "algo cerca", "por aquí".
    if locative_deixis and not residue:
        return result(Act.LOCATIVE, 0.8)

    # 12. Necesidad y presentación.
    if has_volitive:
        # Necesidad expresada en primera persona. El contenido decide si es
        # accionable; aquí sólo se registra que ES una necesidad.
        return result(Act.NEED, 0.85)

    if has_existential:
        # "¿qué tienen?" sin más: pregunta al asistente, no al catálogo.
        return result(Act.AGENT_CAPABILITY, 0.7, agent_directed=True)

    if has_display or has_navigation:
        if residue or "generic_place" in frames:
            return result(Act.NEED if has_navigation else Act.EXISTENTIAL, 0.8)
        if anaphoric or ordinal is not None or locative_deixis:
            return result(Act.REFERENCE, 0.8)
        return result(Act.EXISTENTIAL, 0.65)

    # 8. Sintagma nominal desnudo: sin verbo, con contenido propio.
    #    Puede ser el nombre de un negocio o el concepto de una necesidad.
    #    NO se asume ninguna de las dos: lo decide el anclaje al catálogo.
    if residue:
        return result(Act.BARE_NOMINAL, 0.6 if hedged else 0.7)

    # 9. Sin función derivable.
    return result(Act.UNPARSEABLE, 0.5)


def _social_act(text_norm: str, toks: List[str], token_set: Set[str], residue: List[str]) -> Optional[str]:
    """
    Rutinas de contacto. Sólo se aplican si el mensaje se AGOTA en ellas:
    "hola" es un saludo, "hola busco un médico" no lo es.
    """
    if _LAUGHTER_RE.match(text_norm.replace(" ", "")):
        return Act.BACKCHANNEL

    # Las fórmulas de apertura se encadenan con toda naturalidad: "hola buenas
    # tardes", "hola qué más", "buenas, cómo estás". Se retiran una a una y se
    # mira qué queda; si no queda nada, el mensaje era saludo y nada más.
    remainder = text_norm
    matched_greeting = False
    for phrase in sorted(lx.GREETING_PHRASES, key=len, reverse=True):
        pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
        if re.search(pattern, remainder):
            remainder = re.sub(pattern, " ", remainder)
            matched_greeting = True
    if matched_greeting:
        leftover = {t for t in remainder.split() if t}
        if not leftover or leftover <= (lx.GREETING_FORMS | lx.BACKCHANNEL_FORMS | lx.THANKS_FORMS):
            return Act.GREET

    if residue:
        return None

    if token_set and token_set <= lx.FAREWELL_FORMS:
        return Act.FAREWELL
    if any(text_norm == f for f in lx.FAREWELL_FORMS):
        return Act.FAREWELL

    if token_set and token_set <= lx.THANKS_FORMS:
        return Act.THANKS

    if token_set and token_set <= (lx.GREETING_FORMS | lx.GREETING_FORMS):
        return Act.GREET

    if token_set and token_set <= lx.AFFIRMATIVE_FORMS:
        return Act.AFFIRM
    if token_set and token_set <= lx.NEGATIVE_FORMS:
        return Act.DENY

    if token_set and token_set <= lx.BACKCHANNEL_FORMS:
        return Act.BACKCHANNEL

    # Mezclas de fórmulas: "ok gracias", "hola buenas".
    if token_set and token_set <= (lx.BACKCHANNEL_FORMS | lx.THANKS_FORMS | lx.AFFIRMATIVE_FORMS):
        return Act.BACKCHANNEL
    if token_set and token_set <= (lx.GREETING_FORMS | lx.BACKCHANNEL_FORMS):
        return Act.GREET

    return None
