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
from core import wording
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

    # ── 0. Una corrección explícita cancela la lectura anterior ─────────────
    #
    # Va la primera de todas, por delante incluso de la pregunta abierta. Cuando
    # el usuario dice "no, yo no quiero agendar", está diciendo que la
    # interpretación anterior fue equivocada: ése es el dato más fiable del
    # turno, y ninguna ranura pendiente puede reclamar el mensaje frente a él.
    #
    # Lo acordado no se tira entero por cualquier "no": sólo cuando lo que se
    # rechaza es el objetivo mismo. "No, mejor a las 10" corrige la hora y la
    # reserva sigue viva; "no quiero agendar" la termina.
    if analysis.corrective:
        u.note("corrección explícita: la lectura anterior pierde toda autoridad")
        if analysis.rejects_booking:
            u.cancels_goal = True
            state.clear_booking()
            u.note("el usuario rechaza agendar → se abandona la reserva en curso")

    # ── 0.1. ¿Es la respuesta a lo que Lyra acaba de preguntar? ─────────────
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

    # ── 0c. "¿Y los precios?" con un negocio delante pregunta por ESE negocio ─
    #
    # Sin nada en pantalla, "quiero saber los precios" es una pregunta sobre lo
    # que hace el asistente. Con un negocio en foco no lo es: el usuario
    # pregunta por su oferta, y responderle con el catálogo de capacidades de
    # Lyra es cambiarle de tema.
    if (
        act == Act.AGENT_CAPABILITY
        and "offering" in analysis.frames
        and (state.focus_id or state.presented)
    ):
        analysis = replace(analysis, act=Act.ATTRIBUTE)
        act = Act.ATTRIBUTE
        u.act = act
        u.note("pregunta por la oferta con un negocio delante → atributo del negocio")

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
    #
    # …salvo que el mensaje pida ver el directorio. "Muéstrame las opciones
    # DISPONIBLES" activa el marco de cita por esa palabra, y sin contenido
    # propio ni negocio delante acababa preguntando "¿en qué negocio te gustaría
    # agendar?" a quien sólo quería mirar qué hay.
    if (
        act == Act.BOOKING
        and "generic_place" in analysis.frames
        and not analysis.content_terms
        and not (state.booking.get("business_id") or state.focus_id)
    ):
        u.args = {"category": "", "city": mentioned_city}
        u.note("petición de ver opciones sin negocio en juego → directorio")
        return _finish(u, Disposition.ACT, "search_businesses")

    if act == Act.BOOKING:
        return _booking(
            u, analysis, state, message, temporal,
            mentioned_city=mentioned_city, catalog=catalog, allow_llm=allow_llm,
        )

    # ── 3b. Preguntar SI hay citas no es pedirlas ───────────────────────────
    #
    # "¿Tienen citas mañana?", "¿hay horarios disponibles?" consultan la agenda
    # del negocio que está sobre la mesa. Sin esta rama caían en descubrimiento y
    # se respondían con una búsqueda de negocios, que no es lo que se preguntó.
    if (
        act == Act.EXISTENTIAL
        and "appointment" in analysis.frames
        and not analysis.content_terms
    ):
        biz_id = state.booking.get("business_id") or (
            state.focus_id if state.focus_kind == ConceptKind.BUSINESS else None
        )
        if biz_id:
            u.args = {"business_id": biz_id}
            u.note("consulta de disponibilidad sobre el negocio en foco")
            return _finish(u, Disposition.ACT, "get_business_availability")

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
                u.args = {
                    "category": dominio["args"]["category"],
                    "city": mentioned_city,
                    "_user_terms": dominio["args"].get("_user_terms"),
                }
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
        # La agenda con un día delante gana sobre las reseñas. "¿Cómo está mi
        # agenda mañana?" activa los dos marcos —"cómo está" es una fórmula de
        # opinión— y el orden por defecto lo mandaba a las reseñas del negocio.
        if {"appointment", "temporal"} <= analysis.frames and biz_id:
            u.args = {"business_id": biz_id}
            u.note("agenda con referencia temporal → disponibilidad")
            return _finish(u, Disposition.ACT, "get_business_availability")
        if "review" in analysis.frames:
            u.args = {"business_id": biz_id, "business_name": _business_label(target, state)}
            return _finish(u, Disposition.ACT, "get_business_reviews")
        if "offering" in analysis.frames:
            u.args = {"business_id": biz_id, "business_name": _business_label(target, state)}
            return _finish(u, Disposition.ACT, "get_business_services")
        if "appointment" in analysis.frames and biz_id:
            u.args = {"business_id": biz_id}
            return _finish(u, Disposition.ACT, "get_business_availability")
        # "¿Cuál está más cerca?", "¿cuál queda más cerquita?". Es una
        # comparación por proximidad sobre lo que ya se mostró, y se puede
        # contestar con los datos que cada resultado ya trae. Antes se delegaba
        # al modelo "porque lo responde mejor con los resultados delante"; con
        # el modelo externo apagado eso significaba no responder.
        if state.presented and _wants_proximity(message):
            u.args = {"criterion": "distance"}
            u.note("comparación por cercanía sobre lo ya mostrado")
            return _finish(u, Disposition.ACT, "compare_businesses")

        if state.has_context:
            # Matices que no encajan en ningún criterio conocido. El
            # interceptor los atiende como conversación, con el hilo delante.
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
        # "El más cercano" es una selección POR UN CRITERIO, no un señalamiento.
        # El resolutor de referencias se quedaba con lo que estuviera en foco y
        # devolvía un negocio que no era el más cercano de la lista, que es
        # justo lo único que el usuario había pedido.
        if state.presented and _wants_proximity(message):
            u.args = {"criterion": "distance"}
            u.note("selección por cercanía sobre lo ya mostrado")
            return _finish(u, Disposition.ACT, "compare_businesses")

        if not target:
            if not reference.has_resolvable_context(state):
                u.clarification = "Todavía no te he mostrado opciones. ¿Qué estás buscando?"
            else:
                u.clarification = "¿Cuál de las opciones te interesa?"
            u.note("referencia sin antecedente resoluble")
            return u

        # Una referencia CON marco dice qué hacer con lo referido: "háblame de
        # ese lugar", "muéstrame ese en el mapa", "¿qué ofrece ése?". Va por
        # delante de la reserva porque no es una selección, es una pregunta.
        # Cuando se miraba después, cualquier negocio que hubiera quedado en
        # foco convertía "háblame de ese sitio" en una cita que nadie pidió.
        if target.kind == ConceptKind.BUSINESS:
            marco = _frame_intent(analysis, target.entity_id, target.label, u)
            if marco is not None:
                return marco

        if target.kind == "professional":
            u.args = _booking_args(state, professional_name=target.label)
            return _finish(u, Disposition.ACT, "request_appointment")
        if target.kind == ConceptKind.SERVICE:
            u.args = _booking_args(state, service_name=target.label)
            return _finish(u, Disposition.ACT, "request_appointment")

        # Selección de negocio: si la conversación venía agendando, la selección
        # continúa esa reserva en vez de reiniciar la navegación.
        #
        # "Venía agendando" es tener el OBJETIVO abierto, no tener ranuras. Un
        # negocio queda anotado en `booking` por el mero hecho de entrar en
        # foco, así que la condición anterior daba por iniciada una reserva que
        # el usuario nunca había pedido.
        if state.goal == dialogue.GOAL_BOOKING and state.booking:
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
        # Un "a las 8" sin nada en marcha no es conversación: es un dato que no
        # tiene dónde ir. Devolverlo como charla hacía que Lyra saludara a quien
        # acababa de darle una hora.
        u.note("dato temporal sin proceso abierto → se pregunta a qué corresponde")
        u.clarification = (
            "Todavía no tenemos una cita en marcha. ¿En qué negocio quieres que "
            "te agende, y para qué servicio?"
        )
        return u

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
            # Sólo cuenta como corrección lo que el usuario había ACORDADO. El
            # servicio que venía del tema de la conversación es una suposición
            # nuestra, y sustituirla no es que él haya cambiado de idea.
            anterior = state.booking.get(dialogue.Slot.SERVICE)
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

        # Describió QUÉ quiere y no existe aquí. Preguntarle "¿en qué negocio
        # te gustaría agendar?" es ignorar la mitad de su mensaje y dejarle
        # creer que el sitio está y sólo falta nombrarlo.
        if analysis.content_terms:
            u.note("reserva de algo que el catálogo no reconoce → aclaración")
            u.clarification = _unrecognized_message(analysis.content_terms)
            return u

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
            "_user_terms": _user_terms_for(grounding),
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
        # …salvo que la selección venga con un criterio. "El más cercano" no
        # señala a nada: pide que se ordene lo mostrado por distancia. El
        # resolutor de referencias se quedaba con lo que hubiera en foco y
        # devolvía un negocio que podía ser el más lejano de la lista.
        if state.presented and _wants_proximity(message):
            u.args = {"criterion": "distance"}
            u.note("selección por cercanía sobre lo ya mostrado")
            return _finish(u, Disposition.ACT, "compare_businesses")

        target = reference.resolve(analysis, state, message)
        if target:
            u.note(f"necesidad sobre elemento ya mostrado → {target.label}")
            if target.kind == "professional":
                u.args = _booking_args(state, professional_name=target.label)
                return _finish(u, Disposition.ACT, "request_appointment")
            u.args = {"business_id": target.entity_id, "business_name": target.label}
            return _finish(u, Disposition.ACT, "navigate_to_company")

    # "¿Qué servicios tengo publicados?", "¿qué ofrecen?". El marco nombra la
    # oferta de un negocio concreto; con uno delante no hay nada que buscar en
    # el directorio. Es la pregunta que más hace un empresario sobre lo suyo, y
    # sin esta rama se anclaba la palabra "servicios" contra el catálogo, no
    # encajaba con nada y salía por conversación.
    terms = _content_for_grounding(analysis.content_terms, mentioned_city)
    catalog = catalog if catalog is not None else get_catalog()

    if (
        "offering" in analysis.frames
        # "¿Qué NEGOCIOS ofrecen medicina general?" pregunta por el directorio,
        # no por el negocio que se tenga delante. El marco de lugar genérico es
        # lo que separa una pregunta de la otra.
        and "generic_place" not in analysis.frames
        and (state.focus_id or state.booking.get("business_id"))
        # …y siempre que el mensaje no nombre otra cosa que sí exista: si el
        # usuario dice un servicio o un rubro concreto, está buscando con él.
        and not _grounds_to_something(terms, catalog)
    ):
        biz_id = state.focus_id or state.booking.get("business_id")
        u.args = {"business_id": biz_id, "business_name": state.focus_label}
        u.note("oferta del negocio que está sobre la mesa")
        return _finish(u, Disposition.ACT, "get_business_services")

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
        # "Muéstrame dónde quedan", "¿y en el mapa?", "quiero verlas en el
        # plano". Con resultados en pantalla, esto pregunta DÓNDE están, no si
        # hay más. Repetir la búsqueda contestaba a otra cosa —"son esas mismas
        # seis, no tengo más"— cuando lo que faltaba era mover el mapa.
        if state.presented and (
            "map" in analysis.frames or analysis.act == Act.LOCATIVE
        ):
            u.args = {}
            u.note("ubicación de lo ya mostrado → se lleva al mapa")
            return _finish(u, Disposition.ACT, "fit_all_businesses")

        # …salvo que la conversación ya tenga un tema. "¿Hay más?", "¿es el
        # único?" preguntan por MÁS DE LO MISMO: buscar entonces en el
        # directorio entero devuelve cualquier cosa y deja la pregunta sin
        # responder. El rubro que ya estaba sobre la mesa es la consulta.
        # …y salvo que el usuario haya ensanchado la pregunta a propósito.
        # "¿Qué NEGOCIOS tienes?" nombra el directorio entero: seguir contestando
        # con el rubro anterior es no haberse enterado de que cambió el tema.
        tema = state.topic_service or state.active_domain
        if "generic_place" in analysis.frames:
            tema = None
        if tema and analysis.act in (Act.EXISTENTIAL, Act.LOCATIVE):
            u.args = {
                "category": tema, "city": mentioned_city,
                "_grounded_terms": [tema], "_user_terms": [tema],
            }
            if state.topic_service:
                u.args["_grounded_kind"] = ConceptKind.SERVICE
            u.note(f"pregunta por más de lo mismo → se repite la búsqueda de '{tema}'")
            return _finish(u, Disposition.ACT, "search_businesses")

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

        # "¿Qué puedo encontrar aquí?", "muéstrame qué hay". Las únicas palabras
        # de contenido nombran el acto de buscar, no lo buscado: anclarlas no
        # podía funcionar nunca, y decir "no manejo nada relacionado con
        # «encontrar»" es contestar a una pregunta que nadie hizo.
        if wording.user_facing_label(terms) is None:
            u.args = {"category": "", "city": mentioned_city}
            u.note("el contenido sólo nombra el acto de buscar → directorio general")
            return _finish(u, Disposition.ACT, "search_businesses")

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
        u.args = {
            "category": _search_terms_for(best, grounding)[0],
            "city": mentioned_city,
            "_user_terms": _user_terms_for(grounding),
        }
        u.note("recomendación sobre el rubro anclado")
        return _finish(u, Disposition.ACT, "recommend_businesses")

    # Hay una reserva abierta y el usuario nombra un servicio: lo está eligiendo,
    # no buscando otra cosa. Antes esto reiniciaba la búsqueda y el proceso se
    # quedaba dando vueltas sin llegar nunca a la cita.
    #
    # Dos condiciones lo delimitan, y las dos hacen falta:
    #
    # 1. La reserva tiene que estar REALMENTE abierta. Que haya un negocio en
    #    foco no basta: un negocio queda en foco por el mero hecho de aparecer en
    #    una búsqueda, y con esa condición sola cualquier pregunta posterior
    #    sobre un servicio se convertía en una cita que nadie había pedido.
    # 2. El mensaje no puede estar pidiendo opciones. "¿Qué otros negocios
    #    ofrecen medicina general?" nombra el servicio para BUSCAR con él, no
    #    para agendarlo.
    booking_open = state.goal == dialogue.GOAL_BOOKING and bool(
        state.booking.get("business_id") or state.booking.get("business_name")
    )
    asks_for_options = (
        analysis.act in (Act.EXISTENTIAL, Act.LOCATIVE)
        or "generic_place" in analysis.frames
    )
    if (
        best.kind in (ConceptKind.SERVICE, ConceptKind.SERVICE_CATEGORY)
        and booking_open
        and not asks_for_options
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
    pide_lista = _asks_for_a_list(analysis) or _names_a_kind(message, grounding)
    if (
        best.kind == ConceptKind.BUSINESS
        and not best.ambiguous
        and not pide_lista
        and best.score >= _BUSINESS_NAV_THRESHOLD
    ):
        u.args = {"business_id": best.entity_id, "business_name": best.label, "city": mentioned_city}
        u.note("nombre de negocio identificado")
        return _finish(u, Disposition.ACT, "navigate_to_company")

    # ── Cualquier otro concepto → búsqueda con términos REALES del catálogo ─
    search_terms = _search_terms_for(best, grounding, prefer_domain=pide_lista)
    u.args = {
        "category": search_terms[0],
        "city": mentioned_city,
        "_grounded_kind": best.kind,
        "_grounded_terms": search_terms,
        "_user_terms": _user_terms_for(grounding),
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


#: Marco reconocido → qué capacidad de NexiService le corresponde.
_FRAME_INTENTS = (
    ("identity", "get_business_mission_vision"),
    ("web", "open_business_web"),
    ("map", "fly_to_business"),
    ("review", "get_business_reviews"),
    ("offering", "get_business_services"),
)


def _frame_intent(
    analysis: Analysis, biz_id: Optional[int], label: Optional[str], u: Understanding
) -> Optional[Understanding]:
    """
    La capacidad que nombra el marco del mensaje, si nombra alguna.

    Es la misma tabla para una pregunta ("¿qué ofrece?") y para una referencia
    ("háblame de ése"): lo que cambia es a qué apunta, no qué se pide.
    """
    for frame, intent in _FRAME_INTENTS:
        if frame in analysis.frames:
            u.args = {"business_id": biz_id, "business_name": label}
            u.note(f"{frame} sobre el elemento referido")
            return _finish(u, Disposition.ACT, intent)
    return None


def _grounds_to_something(terms: Sequence[str], catalog: SemanticCatalog) -> bool:
    """¿Las palabras del mensaje apuntan a algo que existe en el catálogo?"""
    if not terms:
        return False
    conceptos = catalog.ground(terms, limit=1)
    return bool(conceptos) and conceptos[0].score >= _MIN_ACTIONABLE_SCORE


#: Determinantes que presentan una clase en vez de señalar un individuo.
_INDEFINITE = ("un", "una", "unos", "unas", "algun", "alguna", "algunos", "algunas")


def _names_a_kind(message: str, grounding: Grounding) -> bool:
    """
    ¿El mensaje pide UNA DE ESAS COSAS, o pide ESA COSA?

    Es la diferencia entre "necesito una veterinaria" y "llévame a Veterinaria
    El Guardián". La marca está en el determinante, que en español lo dice sin
    ambigüedad: el indefinido presenta una clase.

    Importa porque el anclaje encuentra la palabra "veterinaria" dentro de la
    razón social de UNA empresa —en otra ciudad— y sin esta comprobación quien
    pedía una veterinaria cerca aterrizaba en la ficha de aquélla.
    """
    palabras = normalize(message).split()
    for termino in grounding.matched_terms or grounding.content_terms:
        raiz = normalize(termino).split()
        if not raiz:
            continue
        try:
            posicion = palabras.index(raiz[0])
        except ValueError:
            continue
        if posicion > 0 and palabras[posicion - 1] in _INDEFINITE:
            return True
    return False


def _asks_for_a_list(analysis: Analysis) -> bool:
    """
    ¿La pregunta espera varias opciones en vez de una empresa concreta?

    "¿Qué veterinarias tienes?" nombra un rubro en plural. El anclaje encuentra
    ahí una empresa —"Veterinaria El Guardián"— porque la palabra está en su
    razón social, y sin esta comprobación el usuario que preguntaba qué hay
    aterrizaba en la ficha de un negocio de otra ciudad.
    """
    if analysis.act != Act.EXISTENTIAL:
        return False
    return any(
        len(t) > 3 and normalize(t).endswith("s") for t in analysis.content_terms
    )


def _search_terms_for(
    best: GroundedConcept, grounding: Grounding, prefer_domain: bool = False
) -> List[str]:
    """
    Con qué términos consultar la base de datos.

    Lo normal es usar la etiqueta del concepto anclado. Hay dos excepciones, y
    las dos se resuelven igual —bajando de la empresa concreta al rubro—:

      · Nombres ambiguos: si "fogón criollo" encaja con cuatro sucursales,
        buscar por el nombre completo de una sola dejaría fuera a las demás.
      · Preguntas en plural: quien pide "veterinarias" quiere el rubro, aunque
        la palabra viva dentro de la razón social de una empresa.
    """
    if best.kind == ConceptKind.BUSINESS and (best.ambiguous or prefer_domain):
        # El rubro va primero: es lo que de verdad se preguntó.
        for candidate in grounding.concepts:
            if candidate.kind in (ConceptKind.BUSINESS_CATEGORY, ConceptKind.SERVICE_CATEGORY):
                return list(candidate.search_terms) or [candidate.label]
        # Y si ningún rubro se ancló por sí solo, sirve el de la propia empresa:
        # "veterinaria" sólo existe dentro de una razón social, pero esa empresa
        # está clasificada en "Mascotas", que es el rubro que hay que buscar.
        if prefer_domain and best.domain:
            return [best.domain]
        if grounding.matched_terms:
            return [" ".join(grounding.matched_terms)]
        # Sin palabras reconocidas propias, describe el rubro con el mejor
        # concepto que no sea una empresa concreta.
        for candidate in grounding.concepts:
            if candidate.kind != ConceptKind.BUSINESS:
                return list(candidate.search_terms) or [candidate.label]
    return list(best.search_terms) or [best.label]


def _user_terms_for(grounding: Grounding) -> List[str]:
    """
    Las palabras con las que el USUARIO nombró lo que busca.

    No son las mismas con las que se consulta la base de datos, y ésa es
    justamente la distinción que faltaba: la plataforma guarda los hospitales
    bajo «Consultorios y Centros Médicos», así que buscar por la etiqueta del
    catálogo es correcto, pero contestar con ella —«encontré 6 opciones de
    médico»— le dice al usuario que no se le entendió.

    Se prefieren los términos que el catálogo reconoció, porque son suyos Y
    existen. Si no hay ninguno reconocido se devuelve lo que dijo tal cual: el
    llamador decide si le sirve.
    """
    return list(grounding.matched_terms or grounding.content_terms)


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
    # Sin pregunta al final a propósito: el interceptor añade después qué SÍ
    # hay y cierra él. Encadenar aquí una pregunta y allí una oferta producía
    # dos remates seguidos y la respuesta sonaba a dos mensajes pegados.
    return f"No tengo nada relacionado con «{quoted}» dentro de NexiService."
