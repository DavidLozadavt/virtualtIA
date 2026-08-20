"""
core/semantic/engine.py — Une las tres etapas y decide qué hacer.

Aquí se toma la decisión que antes se tomaba demasiado pronto: qué capacidad de
NexiService corresponde a lo que dijo el usuario, o si no corresponde ninguna.

La regla que gobierna todo el módulo:

    Una frase se convierte en búsqueda sólo si su estructura expresa una búsqueda
    Y su contenido se ancla a algo que existe. Si falta cualquiera de las dos
    condiciones, el sistema conversa o pregunta, pero no consulta la base de
    datos con el texto crudo.

Los nombres de `intent` y `args` que devuelve son los que el orquestador ya
maneja, para que esta capa mejore la comprensión sin obligar a reescribir los
interceptores ni las herramientas.
"""

import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

from core.semantic import dialogue
from core.semantic import lexicon as lx
from core.semantic import reference
from core.semantic.catalog import SemanticCatalog, get_catalog
from core.semantic.morphology import normalize, stem_compatible, stem
from core.semantic.speech_act import Analysis, analyze
from core.semantic.types import (
    Act,
    ConceptKind,
    ConversationState,
    Disposition,
    GroundedConcept,
    Grounding,
    PresentedItem,
    Understanding,
)

logger = logging.getLogger("lyra.semantic.engine")


#: Por encima de este puntaje, un nombre propio se considera identificado y se
#: navega directo al negocio. Por debajo, se trata como búsqueda para que el
#: usuario vea alternativas en vez de aterrizar en la empresa equivocada.
_BUSINESS_NAV_THRESHOLD = 0.68

#: Mínimo para aceptar un anclaje léxico como base de una acción. Por debajo, la
#: coincidencia suele venir de una palabra accesoria y conviene pedir ayuda al
#: resolutor semántico antes que actuar sobre ella.
_MIN_ACTIONABLE_SCORE = 0.55


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. ENTRADA PÚBLICA
# ═══════════════════════════════════════════════════════════════════════════════

def understand(
    message: str,
    state: Optional[ConversationState] = None,
    mentioned_city: Optional[str] = None,
    catalog: Optional[SemanticCatalog] = None,
    allow_llm: bool = True,
    temporal: Optional[Dict[str, Any]] = None,
) -> Understanding:
    """Comprende un mensaje del usuario en el contexto de su conversación."""
    state = state or ConversationState()
    analysis = analyze(message)
    return build_understanding(
        message=message,
        analysis=analysis,
        state=state,
        mentioned_city=mentioned_city,
        catalog=catalog,
        allow_llm=allow_llm,
        temporal=temporal,
    )


def build_understanding(
    message: str,
    analysis: Analysis,
    state: ConversationState,
    mentioned_city: Optional[str] = None,
    catalog: Optional[SemanticCatalog] = None,
    allow_llm: bool = True,
    temporal: Optional[Dict[str, Any]] = None,
) -> Understanding:
    """
    Variante que reutiliza un análisis ya calculado.

    `temporal` trae la fecha y la hora ya extraídas del texto por el router, que
    conserva esos analizadores. La comprensión decide QUÉ significan —si
    completan una reserva en curso o no van a ninguna parte— sin duplicar el
    trabajo de reconocerlas.
    """
    temporal = temporal or {}
    act = analysis.act
    u = Understanding(act=act, disposition=Disposition.CLARIFY, confidence=analysis.confidence)
    u.note(f"acto={act} contenido={analysis.content_terms} marcos={sorted(analysis.frames)}")

    # ── 0. ¿Es la respuesta a lo que Lyra acaba de preguntar? ───────────────
    #
    # Va antes que todo lo demás. Una pregunta abierta es la evidencia
    # contextual más fuerte que hay: mientras siga en pie, el mensaje siguiente
    # le pertenece. Analizar "9 am" por su cuenta —sin esa pregunta delante— no
    # da ninguna lectura útil, y el sistema acababa tratándolo como un tema
    # nuevo y volviendo a preguntar lo mismo.
    #
    # Si el mensaje no contiene un valor del tipo esperado, este paso se aparta
    # y el análisis sigue su curso: así un cambio de tema real no queda atrapado
    # dentro de la reserva.
    answered = dialogue.read_answer(message, analysis, state, temporal)
    if answered is not None:
        answered.trace = u.trace + answered.trace
        return answered

    # ── 0a. "Sí" seguido de ruido sigue siendo un sí ────────────────────────
    #
    # "Sí por favot". La errata convierte una palabra en contenido desconocido,
    # el mensaje deja de agotarse en fórmulas sociales y el análisis lo lee como
    # un sintagma nominal: el usuario acepta la cita que se le acaba de ofrecer
    # y recibe un "¿cuál es tu solicitud?".
    #
    # La condición que lo hace seguro es doble: tiene que haber una pregunta
    # abierta —si no, nadie está afirmando nada— y lo que sigue al "sí" no puede
    # nombrar nada del catálogo. "Sí, un restaurante" sí nombra algo y sigue su
    # camino normal.
    apertura = _opens_with(message)
    if (
        apertura
        and act not in (Act.AFFIRM, Act.DENY)
        and not analysis.frames
        and analysis.content_terms
        and (state.pending_slot or state.booking or state.focus_id)
    ):
        if not _resolve_domain(
            analysis, message, mentioned_city=mentioned_city,
            catalog=catalog, allow_llm=False,
        ):
            u.note(f"{apertura} con ruido detrás → se lee como respuesta a lo pendiente")
            analysis = replace(analysis, act=apertura, content_terms=[])
            return _contextual(
                u, analysis, state, message, temporal,
                mentioned_city=mentioned_city, catalog=catalog, allow_llm=allow_llm,
            )

    # ── 0b. Un superlativo pelado sigue siendo una petición de criterio ─────
    #
    # "¿cuál es el mejor?", "el mejor", "recomiéndame uno". El análisis los
    # clasifica de tres formas distintas —capacidad del agente, atributo, sin
    # analizar— porque por sí solos no nombran nada. Lo que sí traen es el
    # marco: piden que alguien se moje. Con opciones sobre la mesa hay de qué
    # hablar; sin ellas, el mensaje sigue su curso normal.
    #
    # Antes, "¿cuál es el mejor?" justo después de una lista de seis médicos
    # respondía con el catálogo de capacidades del asistente.
    if "recommend" in analysis.frames and not analysis.content_terms:
        rubro = state.active_domain or next(
            (p.extra.get("category") for p in state.presented if p.extra.get("category")),
            None,
        )
        if rubro:
            u.args = {"category": rubro, "city": mentioned_city}
            u.note(f"superlativo sin contenido → recomendación sobre '{rubro}'")
            return _finish(u, Disposition.ACT, "recommend_businesses")

    # ── 1. Actos sociales y dirigidos al asistente ──────────────────────────
    if act in Act.CONVERSATIONAL:
        return _conversational(u, act)

    if act == Act.UNPARSEABLE:
        # Sin función derivable hacia el catálogo. Eso NO significa que no haya
        # nada que responder: "no entiendo", "estoy aburrido" o una interpelación
        # al asistente son conversación, y un asistente conversa. Pedir una
        # aclaración aquí convertía cualquier comentario en un interrogatorio
        # sobre qué negocio buscaba el usuario.
        u.note("sin intención de catálogo → conversación")
        return _finish(u, Disposition.CONVERSE, "conversation")

    # ── 2. Referencias a lo ya mostrado ─────────────────────────────────────
    if act in (Act.REFERENCE, Act.ATTRIBUTE, Act.PERSON_QUERY, Act.TEMPORAL,
               Act.AFFIRM, Act.DENY):
        return _contextual(
            u, analysis, state, message, temporal,
            mentioned_city=mentioned_city, catalog=catalog, allow_llm=allow_llm,
        )

    # ── 3. Agendamiento ─────────────────────────────────────────────────────
    if act == Act.BOOKING:
        return _booking(
            u, analysis, state, message, temporal,
            mentioned_city=mentioned_city, catalog=catalog, allow_llm=allow_llm,
        )

    # ── 4. Descubrimiento: necesidad, existencia, lugar, sintagma desnudo ───
    if act in Act.DISCOVERY:
        return _discovery(
            u, analysis, state, message,
            mentioned_city=mentioned_city,
            catalog=catalog,
            allow_llm=allow_llm,
        )

    u.clarification = "¿Me lo puedes decir de otra forma? Quiero asegurarme de entenderte bien."
    return u


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. RAMAS DE DECISIÓN
# ═══════════════════════════════════════════════════════════════════════════════

_CONVERSATIONAL_INTENT = {
    Act.GREET: "greeting",
    Act.FAREWELL: "farewell",
    Act.THANKS: "conversation",
    Act.BACKCHANNEL: "conversation",
    Act.AGENT_CAPABILITY: "capabilities",
    Act.AGENT_IDENTITY: "identity",
}


def _opens_with(message: str) -> Optional[str]:
    """El acto que abre el mensaje, cuando es un sí o un no."""
    palabras = normalize(message).split()
    if not palabras:
        return None
    if palabras[0] in lx.AFFIRMATIVE_FORMS:
        return Act.AFFIRM
    if palabras[0] in lx.NEGATIVE_FORMS:
        return Act.DENY
    return None


def _conversational(u: Understanding, act: str) -> Understanding:
    intent = _CONVERSATIONAL_INTENT.get(act, "conversation")
    u.note(f"acto social → {intent} (sin herramientas)")
    return _finish(u, Disposition.CONVERSE, intent)


def _contextual(
    u: Understanding, analysis: Analysis, state: ConversationState, message: str,
    temporal: Dict[str, Any],
    mentioned_city: Optional[str] = None,
    catalog: Optional[SemanticCatalog] = None,
    allow_llm: bool = True,
) -> Understanding:
    """Actos que sólo significan algo contra lo que ya ocurrió en la conversación."""
    target = reference.resolve(analysis, state, message)
    if target:
        u.note(f"referencia resuelta → {target.kind}:{target.label} (#{target.position})")

    # — Pregunta por las personas que atienden —
    if analysis.act == Act.PERSON_QUERY:
        biz_id = _business_id_from(target, state)
        if biz_id:
            u.args = {"business_id": biz_id, "business_name": _business_label(target, state)}
            return _finish(u, Disposition.ACT, "get_business_professionals")
        u.clarification = "¿De qué negocio quieres ver el equipo?"
        u.note("consulta de personas sin negocio en contexto")
        return u

    # — Pregunta sobre una propiedad de algo ya mostrado —
    if analysis.act == Act.ATTRIBUTE:
        biz_id = _business_id_from(target, state)
        label = _business_label(target, state)

        # "¿Cuál es el mejor para medicina general?" pide un criterio, no una
        # propiedad de lo que hubiera en pantalla. Antes caía al final de esta
        # rama y salía como conversación: con un negocio en foco el usuario
        # recibía un "no estoy seguro de haberte entendido" por preguntar algo
        # perfectamente claro.
        if "recommend" in analysis.frames:
            dominio = _resolve_domain(
                analysis, message, mentioned_city=mentioned_city,
                catalog=catalog, allow_llm=allow_llm,
            )
            if dominio:
                u.grounding = dominio["grounding"]
                u.args = {"category": dominio["args"]["category"], "city": mentioned_city}
                u.note(f"superlativo sobre '{dominio['label']}' → recomendación")
                return _finish(u, Disposition.ACT, "recommend_businesses")
            if state.active_domain:
                # "¿y cuál es el mejor?" justo después de una lista: el rubro es
                # el que ya estaba sobre la mesa.
                u.args = {"category": state.active_domain, "city": mentioned_city}
                u.note("superlativo sobre el rubro que ya estaba en juego")
                return _finish(u, Disposition.ACT, "recommend_businesses")

        # Marcos que nombran una capacidad concreta sobre un negocio.
        for frame, intent in (
            ("identity", "get_business_mission_vision"),
            ("web", "open_business_web"),
            ("map", "fly_to_business"),
        ):
            if frame in analysis.frames:
                u.args = {"business_id": biz_id, "business_name": label}
                return _finish(u, Disposition.ACT, intent)
        if "compare" in analysis.frames:
            return _finish(u, Disposition.ACT, "compare_businesses")
        if "review" in analysis.frames:
            u.args = {"business_id": biz_id, "business_name": _business_label(target, state)}
            return _finish(u, Disposition.ACT, "get_business_reviews")
        if "offering" in analysis.frames:
            u.args = {"business_id": biz_id, "business_name": _business_label(target, state)}
            return _finish(u, Disposition.ACT, "get_business_services")
        if "appointment" in analysis.frames and biz_id:
            u.args = {"business_id": biz_id}
            return _finish(u, Disposition.ACT, "get_business_availability")
        if state.has_context:
            # Comparaciones y matices ("¿cuál queda más cerca?") los responde
            # mejor el modelo con los resultados delante que una plantilla.
            u.note("pregunta sobre resultados previos → respuesta con contexto")
            return _finish(u, Disposition.CONVERSE, None)
        if analysis.frames:
            # Preguntaba por algo de un negocio (servicios, agenda) pero no hay
            # ninguno sobre la mesa: hay que saber cuál.
            u.clarification = "¿Sobre cuál negocio me preguntas?"
            return u
        # Pregunta general sin nada a lo que referirse: se conversa.
        u.note("pregunta general sin contexto → conversación")
        return _finish(u, Disposition.CONVERSE, "conversation")

    # — Selección pura —
    if analysis.act == Act.REFERENCE:
        if not target:
            if not reference.has_resolvable_context(state):
                u.clarification = "Todavía no te he mostrado opciones. ¿Qué estás buscando?"
            else:
                u.clarification = "¿Cuál de las opciones te interesa?"
            u.note("referencia sin antecedente resoluble")
            return u

        if target.kind == "professional":
            u.args = _booking_args(state, professional_name=target.label)
            return _finish(u, Disposition.ACT, "request_appointment")
        if target.kind == ConceptKind.SERVICE:
            u.args = _booking_args(state, service_name=target.label)
            return _finish(u, Disposition.ACT, "request_appointment")

        # Selección de negocio: si la conversación venía agendando, la selección
        # continúa esa reserva en vez de reiniciar la navegación.
        if state.booking:
            u.args = _booking_args(state, business_id=target.entity_id, business_name=target.label)
            return _finish(u, Disposition.ACT, "request_appointment")
        u.args = {"business_id": target.entity_id, "business_name": target.label}
        return _finish(u, Disposition.ACT, "navigate_to_company")

    # — Complemento temporal suelto: "mañana en la tarde" —
    if analysis.act == Act.TEMPORAL:
        if state.booking or state.focus_id:
            u.args = _booking_args(state, **temporal)
            u.note(f"complemento temporal continúa la reserva en curso: {temporal}")
            return _finish(u, Disposition.ACT, "request_appointment")
        u.note("dato temporal sin proceso abierto")
        return _finish(u, Disposition.CONVERSE, None)

    # — Confirmación / negación —
    if analysis.act == Act.AFFIRM:
        if state.booking:
            u.args = _booking_args(state, **temporal)
            return _finish(u, Disposition.ACT, "request_appointment")
        u.note("confirmación sin proceso abierto")
        return _finish(u, Disposition.CONVERSE, None)

    if analysis.act == Act.DENY:
        u.note("negación")
        return _finish(u, Disposition.CONVERSE, None)

    return _finish(u, Disposition.CONVERSE, None)


def _booking(
    u: Understanding, analysis: Analysis, state: ConversationState, message: str,
    temporal: Dict[str, Any],
    mentioned_city: Optional[str] = None,
    catalog: Optional[SemanticCatalog] = None,
    allow_llm: bool = True,
) -> Understanding:
    """
    Marco de cita. La información puede venir repartida en varios mensajes, así
    que se acumula sobre lo que la conversación ya sabía.
    """
    target = reference.resolve(analysis, state, message)
    biz_id = _business_id_from(target, state)
    biz_name = _business_label(target, state)

    args = _booking_args(state, business_id=biz_id, business_name=biz_name, **temporal)
    if target and target.kind == "professional":
        args["professional_name"] = target.label
    if target and target.kind == ConceptKind.SERVICE:
        args["service_name"] = target.label

    # Con el negocio ya elegido, lo que nombre el usuario es el servicio.
    # "Quiero una consulta de medicina general" en mitad de una reserva no es
    # una búsqueda nueva: está diciendo QUÉ quiere que le agenden.
    #
    # Se mira SIEMPRE, aunque ya hubiera un servicio acordado. Antes se miraba
    # sólo cuando la ranura estaba vacía, y una reserva vieja que siguiera en
    # memoria se imponía sobre lo que el usuario acababa de pedir: "reservar una
    # medicina general" agendaba el cambio de aceite de la conversación
    # anterior. Lo que el usuario dice ahora manda sobre lo que dijo antes.
    if biz_id or biz_name:
        servicio = _resolve_service_in_message(
            analysis, message, mentioned_city=mentioned_city,
            catalog=catalog, allow_llm=allow_llm,
        )
        if servicio and servicio.label != args.get("service_name"):
            anterior = args.get("service_name")
            args["service_name"] = servicio.label
            if anterior:
                u.corrections.append(
                    {"slot": "service_name", "from": anterior, "to": servicio.label}
                )
                u.note(f"el servicio nombrado reemplaza al anterior: {anterior} → {servicio.label}")
            else:
                u.note(f"servicio nombrado durante la reserva: {servicio.label}")

    # El usuario quiere reservar pero todavía no hay un negocio: si en el mismo
    # mensaje describió dónde ("...para un hospital"), hay que enseñarle esos
    # sitios en vez de preguntarle secamente en cuál quiere agendar. La reserva
    # no se pierde: queda anotada para que elegir un resultado la retome.
    if not biz_id and not biz_name:
        domain = _resolve_domain(
            analysis, message,
            mentioned_city=mentioned_city, catalog=catalog, allow_llm=allow_llm,
        )
        # Si nombró un negocio concreto ("agendar en Consultorio Médico Vida
        # Sana"), ése ES el sitio: no hay que ofrecerle una lista de la que
        # elegir lo que acaba de elegir.
        if domain and domain["concept"].kind == ConceptKind.BUSINESS and not domain["concept"].ambiguous:
            elegido = domain["concept"]
            args["business_id"] = elegido.entity_id
            args["business_name"] = elegido.label
            u.grounding = domain["grounding"]
            u.args = args
            if "human_agent" in analysis.frames:
                u.args["_wants_professional"] = True
            u.note(f"cita en el negocio nombrado: {elegido.label}")
            return _finish(u, Disposition.ACT, "request_appointment")

        if domain:
            u.grounding = domain["grounding"]
            u.args = {
                **domain["args"],
                "_pending_booking": {
                    k: v for k, v in args.items()
                    if v and k in ("service_name", "professional_name", "time", "date")
                },
                "_wants_professional": "human_agent" in analysis.frames,
            }
            u.note(f"reserva sin negocio pero con dominio '{domain['label']}' → mostrar opciones")
            return _finish(u, Disposition.ACT, "search_businesses")

    u.args = args
    if "human_agent" in analysis.frames:
        u.args["_wants_professional"] = True
    u.note(f"marco de cita → negocio={biz_id or biz_name} ranuras={ {k: v for k, v in args.items() if v} }")
    return _finish(u, Disposition.ACT, "request_appointment")


def _resolve_service_in_message(
    analysis: Analysis,
    message: str,
    mentioned_city: Optional[str],
    catalog: Optional[SemanticCatalog],
    allow_llm: bool,
) -> Optional[GroundedConcept]:
    """El servicio que nombra el mensaje, si nombra alguno."""
    terms = _content_for_grounding(analysis.content_terms, mentioned_city)
    if not terms:
        return None
    catalog = catalog if catalog is not None else get_catalog()
    for concept in catalog.ground(terms, limit=5):
        if concept.kind in (ConceptKind.SERVICE, ConceptKind.SERVICE_CATEGORY):
            if concept.score >= _MIN_ACTIONABLE_SCORE:
                return concept
    return None


def _resolve_domain(
    analysis: Analysis,
    message: str,
    mentioned_city: Optional[str],
    catalog: Optional[SemanticCatalog],
    allow_llm: bool,
) -> Optional[Dict[str, Any]]:
    """
    Rubro al que apunta el contenido del mensaje, si lo hay.

    Es el mismo anclaje que usa el descubrimiento, aislado para que el flujo de
    reserva pueda aprovecharlo sin repetirlo.
    """
    terms = _content_for_grounding(analysis.content_terms, mentioned_city)
    if not terms:
        return None

    catalog = catalog if catalog is not None else get_catalog()
    grounding = Grounding(content_terms=terms, attempted=True)
    grounding.matched_terms = catalog.matched_terms(terms)
    grounding.concepts = catalog.ground(terms)

    if (not grounding.concepts or grounding.best.score < _MIN_ACTIONABLE_SCORE) and allow_llm:
        from core.semantic import llm_resolver

        semantic = llm_resolver.resolve(message, terms, catalog.domain_labels())
        if semantic:
            grounding.concepts = semantic

    if not grounding.concepts or grounding.best.score < _MIN_ACTIONABLE_SCORE:
        return None

    best = grounding.best
    search_terms = _search_terms_for(best, grounding)
    return {
        "label": best.label,
        "concept": best,
        "grounding": grounding,
        "args": {
            "category": search_terms[0],
            "city": mentioned_city,
            "_grounded_kind": best.kind,
            "_grounded_terms": search_terms,
        },
    }


def _discovery(
    u: Understanding,
    analysis: Analysis,
    state: ConversationState,
    message: str,
    mentioned_city: Optional[str],
    catalog: Optional[SemanticCatalog],
    allow_llm: bool,
) -> Understanding:
    """Necesidades y consultas sobre el catálogo."""
    # Una referencia explícita gana sobre cualquier intento de búsqueda nueva:
    # "quiero ir a ese" no es una búsqueda de la palabra "ese".
    #
    # Pero sólo cuando el mensaje NO aporta contenido propio. "¿qué restaurantes
    # hay cerca?" trae un deíctico —"cerca"— y sin esta condición se lo llevaba
    # el negocio que estuviera en foco: el usuario preguntaba por restaurantes y
    # aterrizaba en la clínica de la que se venía hablando. Cuando el mensaje
    # nombra algo, ese algo decide.
    if (
        (analysis.anaphoric or analysis.ordinal is not None)
        and not analysis.content_terms
        and reference.has_resolvable_context(state)
    ):
        target = reference.resolve(analysis, state, message)
        if target:
            u.note(f"necesidad sobre elemento ya mostrado → {target.label}")
            if target.kind == "professional":
                u.args = _booking_args(state, professional_name=target.label)
                return _finish(u, Disposition.ACT, "request_appointment")
            u.args = {"business_id": target.entity_id, "business_name": target.label}
            return _finish(u, Disposition.ACT, "navigate_to_company")

    terms = _content_for_grounding(analysis.content_terms, mentioned_city)
    catalog = catalog if catalog is not None else get_catalog()

    grounding = Grounding(content_terms=terms, attempted=bool(terms))
    if terms:
        grounding.matched_terms = catalog.matched_terms(terms)
        grounding.concepts = catalog.ground(terms)
        if grounding.concepts:
            u.note(f"anclaje léxico → {grounding.concepts[0]!r}")

    # Etapa C: cuando el catálogo no reconoció el contenido por sus palabras, o
    # lo reconoció con evidencia floja. Un anclaje flojo suele venir de una
    # coincidencia con una palabra accesoria ("revisar los ojos" encajando con
    # "Revisión de Alternador" porque "ojos" no existe en el catálogo), y el
    # salto de sentido que hace falta ahí es justo el que aporta el resolutor.
    #
    # No se intenta descartar esos casos con más heurística léxica: se probó
    # exigir que ninguna palabra quedara sin reconocer y el remedio resultó peor
    # —tumbaba sintagmas perfectamente claros como "atención médica"—. Cuando el
    # resolutor no está disponible, se prefiere una respuesta del dominio más
    # cercano, que el usuario puede corregir, antes que negarle el servicio.
    lexical_is_weak = not grounding.concepts or grounding.best.score < _MIN_ACTIONABLE_SCORE
    if terms and lexical_is_weak and allow_llm:
        from core.semantic import llm_resolver

        semantic = llm_resolver.resolve(message, terms, catalog.domain_labels())
        if semantic:
            grounding.concepts = semantic
            u.note(f"resolución semántica → {semantic[0]!r}")
        else:
            u.note("resolución semántica sin correspondencia")

    u.grounding = grounding

    # ── Sin contenido propio: descubrimiento general del directorio ─────────
    if not terms:
        wants_near = analysis.markers.get("locative_q") or _wants_proximity(message)
        if (
            "generic_place" in analysis.frames
            or analysis.act in (Act.EXISTENTIAL, Act.LOCATIVE)
            or wants_near
        ):
            u.args = {"category": "", "city": mentioned_city}
            if wants_near:
                u.args["near_me"] = True
            u.note("descubrimiento general del directorio")
            return _finish(u, Disposition.ACT, "search_businesses")
        u.note("sin contenido identificable → conversación")
        return _finish(u, Disposition.CONVERSE, "conversation")

    # ── Contenido presente pero sin correspondencia real ────────────────────
    #
    # Aquí se separan dos situaciones que antes se trataban igual. Si el usuario
    # PIDIÓ algo explícitamente ("necesito…", "¿hay…?", "¿dónde…?") y el catálogo
    # no lo tiene, corresponde decírselo. Pero un sintagma suelto que no encaja
    # con nada —"estoy aburrido", "tengo una duda"— casi nunca era una búsqueda:
    # es conversación, y responderle con "no manejo nada relacionado con eso"
    # es justo lo que hacía que Lyra pareciera un buscador y no un asistente.
    asked_explicitly = analysis.act in (Act.NEED, Act.EXISTENTIAL, Act.LOCATIVE)

    if not grounding.concepts or grounding.best.score < _MIN_ACTIONABLE_SCORE:
        reason = "no reconocido en el catálogo" if not grounding.concepts else "anclaje demasiado débil"
        if asked_explicitly:
            u.note(f"{reason} tras una petición explícita → aclaración")
            u.clarification = _unrecognized_message(terms)
            return u
        u.note(f"{reason} sin petición explícita → conversación")
        return _finish(u, Disposition.CONVERSE, "conversation")

    best = grounding.best

    # Marcos que cambian QUÉ se hace con el rubro identificado.
    #
    # Va por delante de la reserva en curso: "recomiéndame el mejor para corte
    # de cabello" pide una opinión, no elige un servicio. Cuando se miraba
    # después, un negocio que hubiera quedado en foco se llevaba la petición y
    # el usuario acababa agendando en un sitio que nadie le había recomendado.
    if "recommend" in analysis.frames:
        u.args = {"category": _search_terms_for(best, grounding)[0], "city": mentioned_city}
        u.note("recomendación sobre el rubro anclado")
        return _finish(u, Disposition.ACT, "recommend_businesses")

    # Hay una reserva abierta y el usuario nombra un servicio: lo está eligiendo,
    # no buscando otra cosa. Antes esto reiniciaba la búsqueda y el proceso se
    # quedaba dando vueltas sin llegar nunca a la cita.
    if (
        best.kind in (ConceptKind.SERVICE, ConceptKind.SERVICE_CATEGORY)
        and (state.booking.get("business_id") or state.focus_id)
    ):
        u.args = _booking_args(state, service_name=best.label)
        u.note(f"servicio elegido para la reserva en curso: {best.label}")
        return _finish(u, Disposition.ACT, "request_appointment")

    if "map" in analysis.frames and best.kind == ConceptKind.BUSINESS:
        u.args = {"business_id": best.entity_id, "business_name": best.label, "city": mentioned_city}
        u.note("situar en el mapa el negocio identificado")
        return _finish(u, Disposition.ACT, "fly_to_business")

    for frame, intent in (
        ("identity", "get_business_mission_vision"),
        ("web", "open_business_web"),
    ):
        if frame in analysis.frames and best.kind == ConceptKind.BUSINESS:
            u.args = {"business_id": best.entity_id, "business_name": best.label}
            u.note(f"{frame} sobre el negocio identificado")
            return _finish(u, Disposition.ACT, intent)

    u.confidence = round(min(0.99, analysis.confidence * 0.5 + best.score * 0.5), 3)

    # ── Nombre propio identificado → ir a ese negocio ───────────────────────
    # Sólo si señala a UNA empresa. Si varias sucursales encajan igual, se
    # muestran todas: aterrizar en una al azar es peor que dar a elegir.
    if (
        best.kind == ConceptKind.BUSINESS
        and not best.ambiguous
        and best.score >= _BUSINESS_NAV_THRESHOLD
    ):
        u.args = {"business_id": best.entity_id, "business_name": best.label, "city": mentioned_city}
        u.note("nombre de negocio identificado")
        return _finish(u, Disposition.ACT, "navigate_to_company")

    # ── Cualquier otro concepto → búsqueda con términos REALES del catálogo ─
    search_terms = _search_terms_for(best, grounding)
    u.args = {
        "category": search_terms[0],
        "city": mentioned_city,
        "_grounded_kind": best.kind,
        "_grounded_terms": search_terms,
    }
    if _wants_proximity(message):
        u.args["near_me"] = True
    u.note(f"búsqueda anclada a '{search_terms[0]}' ({best.kind})")
    return _finish(u, Disposition.ACT, "search_businesses")


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. AUXILIARES
# ═══════════════════════════════════════════════════════════════════════════════

def _finish(u: Understanding, disposition: str, intent: Optional[str]) -> Understanding:
    u.disposition = disposition
    u.intent = intent
    return u


def _search_terms_for(best: GroundedConcept, grounding: Grounding) -> List[str]:
    """
    Con qué términos consultar la base de datos.

    Lo normal es usar la etiqueta del concepto anclado. La excepción son los
    nombres ambiguos: si "fogón criollo" encaja con cuatro sucursales, buscar
    por el nombre completo de una sola ("Fogón Criollo Norte Popayán") dejaría
    fuera a las demás. En ese caso se usan las palabras del propio usuario, que
    a esas alturas ya se sabe que existen en el catálogo.
    """
    if best.kind == ConceptKind.BUSINESS and best.ambiguous:
        if grounding.matched_terms:
            return [" ".join(grounding.matched_terms)]
        # Sin palabras reconocidas propias, describe el rubro con el mejor
        # concepto que no sea una empresa concreta.
        for candidate in grounding.concepts:
            if candidate.kind != ConceptKind.BUSINESS:
                return list(candidate.search_terms) or [candidate.label]
    return list(best.search_terms) or [best.label]


def _content_for_grounding(terms: Sequence[str], mentioned_city: Optional[str]) -> List[str]:
    """Quita del contenido la ciudad, que ya se maneja como filtro aparte."""
    if not mentioned_city:
        return list(terms)
    city_stem = stem(mentioned_city)
    return [t for t in terms if not stem_compatible(stem(t), city_stem)]


def _wants_proximity(message: str) -> bool:
    """El usuario pide cercanía física ("algo cerca", "por aquí")."""
    norm_tokens = set(normalize(message).split())
    return bool(norm_tokens & lx.LOCATIVE_ADVERBS)


def _business_id_from(target: Optional[PresentedItem], state: ConversationState) -> Optional[int]:
    if target and target.kind == ConceptKind.BUSINESS and target.entity_id:
        return target.entity_id
    if target and target.extra.get("business_id"):
        return target.extra["business_id"]
    if state.focus_kind == ConceptKind.BUSINESS and state.focus_id:
        return state.focus_id
    if state.focus_id:
        return state.focus_id
    if state.booking.get("business_id"):
        return state.booking["business_id"]
    businesses = [p for p in state.presented if p.kind == ConceptKind.BUSINESS]
    if len(businesses) == 1:
        return businesses[0].entity_id
    return None


def _business_label(target: Optional[PresentedItem], state: ConversationState) -> Optional[str]:
    if target and target.kind == ConceptKind.BUSINESS:
        return target.label
    if state.focus_kind == ConceptKind.BUSINESS and state.focus_label:
        return state.focus_label
    return state.booking.get("business_name")


#: Acumulación de ranuras. Vive en `dialogue` porque es la etapa que la
#: necesita primero; aquí se reexporta con el nombre que ya usaba este módulo.
_booking_args = dialogue.booking_args


def _unrecognized_message(terms: Sequence[str]) -> str:
    """
    Mensaje para el caso "entendí la forma, pero esto no existe aquí".

    Es distinto de "no encontré resultados": aquí el sistema ni siquiera
    reconoce el concepto, y decírselo así al usuario le permite reformular.
    """
    quoted = " ".join(terms[:4])
    return (
        f"No manejo nada relacionado con «{quoted}» dentro de NexiService. "
        "¿Me cuentas qué necesitas resolver y te digo si tenemos algo así?"
    )
