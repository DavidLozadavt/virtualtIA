"""
tests/test_semantic_understanding.py — La comprensión debe generalizar.

Estas pruebas no comprueban frases: comprueban que la MISMA intención se
entienda venga como venga. Cada capacidad se prueba en formulación directa,
indirecta, coloquial, corta, larga, con erratas y sin tildes. Si una sola de
esas formas se hubiera resuelto agregándola a una lista de palabras clave, el
resto del grupo la delataría.

El catálogo es un doble construido en memoria: las pruebas describen la
comprensión, no el contenido de la base de datos de nadie.

    python -m pytest tests/test_semantic_understanding.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic import llm_resolver
from core.semantic.catalog import Concept, SemanticCatalog, _legacy_category_concepts
from core.semantic.engine import understand
from core.semantic.types import Act, ConceptKind, ConversationState, Disposition


# ═══════════════════════════════════════════════════════════════════════════════
# Catálogo de prueba: mismo perfil que la base real (rubros, sucursales, servicios)
# ═══════════════════════════════════════════════════════════════════════════════

def _catalog() -> SemanticCatalog:
    def biz(i, label, cat):
        return Concept(kind=ConceptKind.BUSINESS, label=label, entity_id=i,
                       aliases=[cat], search_term=label)

    return SemanticCatalog([
        # Categorías de empresa
        Concept(kind=ConceptKind.BUSINESS_CATEGORY, label="Consultorios y Centros Médicos", entity_id=9, search_term="Consultorios y Centros Médicos"),
        Concept(kind=ConceptKind.BUSINESS_CATEGORY, label="Salud y Bienestar", entity_id=3, search_term="Salud y Bienestar"),
        Concept(kind=ConceptKind.BUSINESS_CATEGORY, label="Belleza y Estética", entity_id=2, search_term="Belleza y Estética"),
        Concept(kind=ConceptKind.BUSINESS_CATEGORY, label="Mecánica y Automotriz", entity_id=4, search_term="Mecánica y Automotriz"),
        Concept(kind=ConceptKind.BUSINESS_CATEGORY, label="Restaurantes y Gastronomía", entity_id=1, search_term="Restaurantes y Gastronomía"),
        Concept(kind=ConceptKind.BUSINESS_CATEGORY, label="Mascotas", entity_id=8, search_term="Mascotas"),
        # Empresas
        biz(101, "Consultorio Médico Vida Sana Popayán", "Consultorios y Centros Médicos"),
        biz(102, "Centro Médico Salud Norte Popayán", "Consultorios y Centros Médicos"),
        biz(103, "Óptica Claridad Este Popayán", "Salud y Bienestar"),
        biz(104, "Óptica Claridad Oeste Popayán", "Salud y Bienestar"),
        biz(105, "Fogón Criollo Norte Popayán", "Restaurantes y Gastronomía"),
        biz(106, "Auto Doctor Sur Popayán", "Mecánica y Automotriz"),
        biz(107, "Corte de Clase Oeste Popayán", "Belleza y Estética"),
        biz(108, "FS", "Tecnología y Electrónica"),
        # Servicios
        Concept(kind=ConceptKind.SERVICE, label="Corte de Cabello Profesional", search_term="Corte de Cabello Profesional"),
        Concept(kind=ConceptKind.SERVICE, label="Consulta Médica General", search_term="Consulta Médica General"),
        Concept(kind=ConceptKind.SERVICE, label="Masaje Relajante", search_term="Masaje Relajante"),
        Concept(kind=ConceptKind.SERVICE, label="Bandeja Paisa", search_term="Bandeja Paisa"),
        Concept(kind=ConceptKind.SERVICE, label="Cambio de Aceite", search_term="Cambio de Aceite"),
        # Los rubros con sus nombres populares se añaden igual que en producción,
        # para que el doble no se comporte mejor ni peor que el catálogo real.
        *_legacy_category_concepts(),
    ])


CATALOG = _catalog()


@pytest.fixture(autouse=True)
def _no_live_llm():
    """
    Por defecto el resolutor semántico no encuentra nada.

    Así las pruebas miden lo que el sistema entiende POR SÍ MISMO, sin que una
    llamada al modelo tape un fallo del análisis o del anclaje.
    """
    llm_resolver.set_completion_fn(lambda prompt: '{"categorias": [], "confianza": 0.0}')
    yield
    llm_resolver.set_completion_fn(None)


def u(message, state=None, city=None, allow_llm=True):
    return understand(message, state=state, mentioned_city=city,
                      catalog=CATALOG, allow_llm=allow_llm)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CONVERSACIÓN: lo que NUNCA debe consultar la base de datos
# ═══════════════════════════════════════════════════════════════════════════════

NEVER_SEARCH = [
    "hola", "Hola!", "buenas", "buenos dias", "buenas tardes", "qué tal",
    "gracias", "muchas gracias", "jajaja", "jajajaja", "ok", "listo", "vale",
    "estoy mirando", "solo estoy viendo", "solamente estoy mirando",
    "qué puedes hacer", "que puedes hacer?", "qué me puedes ofrecer",
    "que me puedes ofrecer?", "en qué me puedes ayudar", "ayúdame", "ayudame",
    "cómo funciona esto", "como funciona", "para qué sirves", "qué sabes hacer",
    "quién eres", "quien eres tu", "cómo te llamas",
    "chao", "adiós", "hasta luego", "nos vemos",
    "necesito que te mueras", "eres un inútil", "no sirves para nada",
]


@pytest.mark.parametrize("message", NEVER_SEARCH)
def test_conversacion_nunca_consulta_el_catalogo(message):
    result = u(message)
    assert result.intent not in ("search_businesses", "navigate_to_company"), (
        f"{message!r} terminó en {result.intent} — no debe consultar el catálogo"
    )
    assert result.disposition != Disposition.ACT or result.intent is None


GREETING_FORMS_IN_THE_WILD = [
    "hola", "Hola!", "hola como estas", "hola cómo estás?",
    "como estas", "cómo estás", "hola que mas", "qué más",
    "hola buenas tardes", "buenas, como va", "que hubo", "que tal",
    "hola como has estado",
]


@pytest.mark.parametrize("message", GREETING_FORMS_IN_THE_WILD)
def test_los_saludos_se_reconocen_encadenados(message):
    """
    Las fórmulas de apertura se acumulan sin cambiar de acto: "hola", "hola qué
    más" y "hola, ¿cómo estás?" son todos el mismo gesto de abrir conversación.
    """
    result = u(message)
    assert result.act == Act.GREET, f"{message!r} → {result.act}"
    assert result.intent == "greeting"


@pytest.mark.parametrize("message,expected_act", [
    # Segunda persona: pregunta por el interlocutor → saludo.
    ("como estas", Act.GREET),
    ("hola como estas", Act.GREET),
    # Tercera persona: pregunta por una entidad → consulta de reputación.
    ("como es ese negocio", Act.ATTRIBUTE),
    ("que tal es fogon criollo", Act.ATTRIBUTE),
    ("como esta el servicio", Act.ATTRIBUTE),
])
def test_la_persona_verbal_separa_saludo_de_resena(message, expected_act):
    """
    "¿Cómo estás?" y "¿cómo está?" se diferencian en una letra, y en quién es el
    sujeto. Confundirlos convertía un saludo en una búsqueda de reseñas.
    """
    assert u(message).act == expected_act, f"{message!r}"


@pytest.mark.parametrize("message", [
    "necesito que te mueras", "ojalá te mueras", "eres un idiota",
])
def test_agresion_no_llega_a_la_base(message):
    """El caso de los logs: una grosería no es el nombre de una empresa."""
    result = u(message)
    assert result.intent != "navigate_to_company"
    assert result.intent != "search_businesses"
    assert result.disposition == Disposition.CONVERSE


@pytest.mark.parametrize("message", [
    "qué me puedes ofrecer", "que me puedes ofrecer", "que me puedes ofreser",
    "qué me ofreces", "en qué me ayudas", "que sabes hacer",
])
def test_pregunta_por_capacidades_es_conversacion(message):
    result = u(message)
    assert result.act == Act.AGENT_CAPABILITY
    assert result.intent == "capabilities"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DESCUBRIMIENTO GENERAL: sin decir de qué rubro
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message", [
    "qué negocios tienen",
    "qué negocios hay",
    "muéstrame opciones",
    "muestrame las opciones",
    "quiero ver los negocios",
    "qué hay disponible",
    "enséñame qué empresas hay",
    "hay negocios por aquí",
])
def test_descubrimiento_general_del_directorio(message):
    result = u(message)
    assert result.intent == "search_businesses", f"{message!r} → {result.intent}"
    # Sin rubro declarado, la búsqueda va vacía: trae el directorio.
    assert not result.args.get("category")


@pytest.mark.parametrize("message", [
    "qué hay por aquí", "que hay por aca", "algo cerca", "quiero algo cerca",
    "qué hay cerca de mí", "hay algo cerquita",
])
def test_descubrimiento_por_cercania_activa_proximidad(message):
    result = u(message)
    assert result.intent == "search_businesses"
    assert result.args.get("near_me") is True, f"{message!r} debería buscar por cercanía"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BÚSQUEDA POR SIGNIFICADO: la misma necesidad, muchas formas
# ═══════════════════════════════════════════════════════════════════════════════

MEDICO = [
    "necesito un médico",                    # directa
    "necesito un medico",                    # sin tildes
    "nesesito un medico",                    # errata
    "busco un doctor... bueno, un médico",   # coloquial + relleno
    "ando buscando un médico",               # perífrasis regional
    "quiero una consulta médica",            # por el servicio
    "hay médicos disponibles",               # existencial
    "me gustaría ver un médico",             # cortesía
    "medico",                                # sintagma desnudo
]


@pytest.mark.parametrize("message", MEDICO)
def test_misma_necesidad_medica_en_muchas_formas(message):
    result = u(message)
    assert result.is_actionable, f"{message!r} no resultó accionable: {result.trace}"
    assert result.intent in ("search_businesses", "navigate_to_company")
    # Lo que llega a la consulta sale del catálogo, no de la frase del usuario.
    query = result.args.get("category") or result.args.get("business_name") or ""
    assert len(query.split()) <= 5


BELLEZA = [
    "quiero cortarme el cabello",
    "quiero cortarme el pelo... digo, el cabello",
    "necesito un corte de cabello",
    "donde me corto el cabello",
    "quiero un corte de cabello profesional",
]


@pytest.mark.parametrize("message", BELLEZA)
def test_necesidad_de_corte_de_cabello(message):
    result = u(message)
    assert result.is_actionable, f"{message!r}: {result.trace}"


@pytest.mark.parametrize("message", [
    "bandeja paisa", "quiero una bandeja paisa", "tienen bandeja paisa",
    "vendes bandeja paisa",
])
def test_busqueda_por_nombre_de_servicio(message):
    result = u(message)
    assert result.is_actionable, f"{message!r}: {result.trace}"


def test_consulta_conversacional_nunca_se_convierte_en_query_literal():
    """El defecto original: la frase entera terminando en un LIKE."""
    for message in ("que me puedes ofrecer", "necesito que te mueras", "alguna medicina"):
        result = u(message)
        category = (result.args.get("category") or "")
        assert "puedes" not in category and "mueras" not in category
        # Si hay consulta, viene del catálogo y es corta.
        assert len(category.split()) <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# 4. NOMBRES PROPIOS Y ERRATAS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message,expected_id", [
    ("fogón criollo", 105),
    ("fogon criollo",  105),
    ("fogon criolo",   105),          # errata
    ("quiero ir a Fogón Criollo", 105),
    ("llévame a fogon criollo norte", 105),
])
def test_nombre_propio_identificado_lleva_al_negocio(message, expected_id):
    result = u(message)
    assert result.intent == "navigate_to_company", f"{message!r} → {result.intent}"
    assert result.args.get("business_id") == expected_id


def test_nombre_ambiguo_muestra_las_opciones():
    """Dos sucursales con el mismo nombre: se listan, no se elige una al azar."""
    result = u("óptica claridad")
    assert result.intent == "search_businesses"
    assert result.args.get("business_id") is None


def test_concepto_inexistente_no_inventa_busqueda():
    """Se entiende la forma, pero NexiService no tiene nada así."""
    result = u("necesito un criadero de dragones", allow_llm=False)
    assert result.disposition == Disposition.CLARIFY
    assert result.intent is None
    assert result.clarification


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. ASISTENTE, NO BUSCADOR
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message", [
    "no entiendo",
    "tengo una duda",
    "estoy aburrido",
    "puedes repetir",
    "y eso cómo se hace",
    "me puedes ayudar con otra cosa",
    "cuál es tu nombre",
    "háblame de ti",
    "qué día es hoy",
    "qué es una reserva",
])
def test_lo_que_no_es_del_catalogo_se_conversa(message):
    """
    Un mensaje que no pide nada del catálogo no se responde con "no manejo eso".
    Se conversa: eso es lo que separa a un asistente de un buscador.
    """
    result = u(message)
    assert result.disposition == Disposition.CONVERSE, (
        f"{message!r} → {result.disposition} ({result.trace})"
    )
    assert result.intent != "search_businesses"


def test_una_peticion_explicita_sin_resultado_si_avisa():
    """
    La contraparte: si el usuario pidió algo concreto y no existe, se le dice.
    Conversar aquí sería esquivar la pregunta.
    """
    result = u("necesito un criadero de dragones", allow_llm=False)
    assert result.disposition == Disposition.CLARIFY
    assert "dragones" in (result.clarification or "")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. RESERVAS: muchas formas, información repartida en varios mensajes
# ═══════════════════════════════════════════════════════════════════════════════

BOOKING_FORMS = [
    "quiero una cita",
    "quiero agendar",
    "me gustaría reservar",
    "quiero separar una hora",
    "quiero sacar turno",
    "necesito que me atiendan",
    "quiero sacar una cita",
    "necesito agendar",
    "quiero apartar un turno",
    "kiero agendar",              # errata
    "quiero agendar una cita porfa",
]


@pytest.mark.parametrize("message", BOOKING_FORMS)
def test_intencion_de_reserva_en_muchas_formas(message):
    result = u(message)
    assert result.intent == "request_appointment", f"{message!r} → {result.intent}"


NATURAL_BOOKING = [
    # Cómo pide cita alguien que no conoce la aplicación: cuenta lo que le pasa,
    # dice dónde quiere ir y con quién. La palabra del rubro va enterrada en el
    # relato, y aun así debe mandar la petición de cita.
    "me siento muy mal de la cabeza, quisiera hacer una reserva para un hospital "
    "o algo con una profesional que me pueda atender",
    "quiero agendar una cita en un centro médico",
    "necesito que me atienda un doctor, ¿me puedes reservar?",
    "quisiera separar una hora en una clínica",
]


@pytest.mark.parametrize("message", NATURAL_BOOKING)
def test_una_peticion_de_cita_no_se_degrada_a_listado(message):
    """
    Que el mensaje mencione un rubro no lo convierte en una búsqueda a secas.
    Antes, una sola palabra ("hospital") disparaba la lista de resultados y la
    petición de cita se perdía por el camino.
    """
    result = u(message)
    assert result.is_actionable, f"{message!r}: {result.trace}"
    # O bien se abre la reserva, o bien se muestran opciones PARA reservar,
    # pero la intención de agendar queda registrada en cualquiera de los casos.
    if result.intent == "search_businesses":
        assert "_pending_booking" in result.args or result.args.get("_wants_professional"), (
            f"{message!r} perdió la intención de reservar: {result.args}"
        )
    else:
        assert result.intent == "request_appointment"


def test_la_peticion_de_profesional_sobrevive_a_la_busqueda():
    """"…con una profesional que me pueda atender" no se puede perder."""
    result = u(
        "me siento mal, quiero una cita en un hospital con una profesional que me atienda"
    )
    assert result.args.get("_wants_professional") is True


@pytest.mark.parametrize("message,expected", [
    # Palabras que la gente usa y que no están en la base tal cual.
    ("hospital o algo?", "medico"),
    ("necesito un hospital", "medico"),
    ("quiero arreglar la moto", "taller"),
    ("busco un gimnasio", "gym"),
])
def test_los_nombres_populares_de_un_rubro_se_reconocen(message, expected):
    """
    "Hospital" no aparece en la base —allí dice "Consultorios y Centros
    Médicos"—, pero es como habla la gente. El puente ya existía en el proyecto
    y ahora lo usa también la comprensión.
    """
    result = u(message)
    assert result.is_actionable, f"{message!r}: {result.trace}"
    assert result.args.get("category") == expected


def test_reserva_se_completa_entre_varios_mensajes():
    """El usuario reparte los datos; el sistema no se los vuelve a pedir."""
    state = ConversationState()
    state.set_focus(ConceptKind.BUSINESS, 105, "Fogón Criollo Norte Popayán")
    state.booking = {"business_id": 105, "business_name": "Fogón Criollo Norte Popayán"}

    first = u("quiero una cita", state=state)
    assert first.intent == "request_appointment"
    assert first.args.get("business_id") == 105

    # El turno siguiente sólo aporta el momento.
    state.booking["service_name"] = "Bandeja Paisa"
    later = u("mañana en la tarde", state=state)
    assert later.intent == "request_appointment"
    assert later.args.get("service_name") == "Bandeja Paisa"
    assert later.args.get("business_id") == 105


def test_dato_temporal_sin_reserva_abierta_no_agenda_nada():
    result = u("mañana en la tarde", state=ConversationState())
    assert result.intent != "request_appointment"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. REFERENCIAS A LO YA MOSTRADO
# ═══════════════════════════════════════════════════════════════════════════════

def _state_with_results():
    state = ConversationState()
    state.remember_list(ConceptKind.BUSINESS, [
        {"id": 101, "name": "Consultorio Médico Vida Sana Popayán"},
        {"id": 102, "name": "Centro Médico Salud Norte Popayán"},
    ])
    state.active_domain = "Consultorios y Centros Médicos"
    return state


@pytest.mark.parametrize("message,expected_id", [
    ("quiero el primero",   101),
    ("el primero",          101),
    ("quiero el segundo",   102),
    ("el segundo por favor", 102),
    ("dame el último",      102),
])
def test_seleccion_por_posicion(message, expected_id):
    result = u(message, state=_state_with_results())
    assert result.args.get("business_id") == expected_id, f"{message!r}: {result.trace}"


def test_referencia_por_nombre_parcial():
    result = u("quiero el de vida sana", state=_state_with_results())
    assert result.args.get("business_id") == 101


def test_referencia_sin_contexto_pide_aclaracion():
    result = u("quiero el segundo", state=ConversationState())
    assert result.disposition == Disposition.CLARIFY
    assert result.intent is None


def test_pronombre_femenino_resuelve_a_profesional():
    state = ConversationState()
    state.set_focus(ConceptKind.BUSINESS, 105, "Fogón Criollo Norte Popayán")
    state.remember_list("professional", [
        {"id": 7, "name": "Lina Marcela"},
        {"id": 8, "name": "Carlos Ruiz"},
    ])
    result = u("con ella", state=state)
    assert result.intent == "request_appointment"
    assert result.args.get("professional_name") == "Lina Marcela"


def test_reservar_ahi_usa_el_negocio_en_foco():
    state = ConversationState()
    state.set_focus(ConceptKind.BUSINESS, 106, "Auto Doctor Sur Popayán")
    result = u("quiero reservar ahí", state=state)
    assert result.intent == "request_appointment"
    assert result.args.get("business_id") == 106


# ═══════════════════════════════════════════════════════════════════════════════
# 7. PROFESIONALES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message", [
    "quiero ver los profesionales",
    "muéstrame el equipo",
    "quiénes trabajan ahí",
    "quienes trabajan ahi",
    "quién atiende",
    "con quién puedo atenderme",
    "quiero ver los prestadores",
])
def test_consulta_por_el_equipo(message):
    state = ConversationState()
    state.set_focus(ConceptKind.BUSINESS, 105, "Fogón Criollo Norte Popayán")
    result = u(message, state=state)
    assert result.intent in ("get_business_professionals", "request_appointment"), (
        f"{message!r} → {result.intent}"
    )


def test_consulta_por_equipo_sin_negocio_pide_aclaracion():
    result = u("quiénes trabajan ahí", state=ConversationState())
    assert result.disposition == Disposition.CLARIFY


# ═══════════════════════════════════════════════════════════════════════════════
# 8. ATRIBUTOS DE RESULTADOS PREVIOS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message", [
    "cuál queda más cerca", "cual es el mas cercano", "cuál me recomiendas",
])
def test_comparacion_entre_resultados_no_reinicia_la_busqueda(message):
    result = u(message, state=_state_with_results())
    assert result.intent != "search_businesses"
    assert result.understood


@pytest.mark.parametrize("message", [
    "qué servicios tiene", "qué ofrecen", "muéstrame el catálogo", "qué precios manejan",
])
def test_consulta_de_servicios_del_negocio_en_foco(message):
    state = ConversationState()
    state.set_focus(ConceptKind.BUSINESS, 105, "Fogón Criollo Norte Popayán")
    result = u(message, state=state)
    assert result.intent == "get_business_services", f"{message!r} → {result.intent}"
    assert result.args.get("business_id") == 105


@pytest.mark.parametrize("message", [
    "qué opinan de ese", "cómo son las reseñas", "qué tal es",
])
def test_consulta_de_resenas(message):
    state = ConversationState()
    state.set_focus(ConceptKind.BUSINESS, 105, "Fogón Criollo Norte Popayán")
    result = u(message, state=state)
    assert result.intent == "get_business_reviews", f"{message!r} → {result.intent}"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. RESOLUCIÓN SEMÁNTICA (etapa C): el salto de sentido
# ═══════════════════════════════════════════════════════════════════════════════

def test_necesidad_sin_palabras_compartidas_se_resuelve_semanticamente():
    """
    "dónde llevar a mi perro" no comparte ninguna palabra con "Mascotas".
    Ese salto lo da el resolutor, y sólo puede elegir categorías que existen.
    """
    llm_resolver.set_completion_fn(
        lambda prompt: '{"categorias": ["Mascotas"], "confianza": 0.9}'
    )
    result = u("necesito dónde llevar a mi perro")
    assert result.is_actionable
    assert result.args.get("category") == "Mascotas"
    assert result.grounding.best.source == "llm"


def test_el_resolutor_no_puede_inventar_categorias():
    """Si el modelo propone algo que no existe, se descarta."""
    llm_resolver.set_completion_fn(
        lambda prompt: '{"categorias": ["Servicios Funerarios Espaciales"], "confianza": 0.9}'
    )
    result = u("necesito enterrar un satélite")
    assert result.disposition == Disposition.CLARIFY
    assert result.intent is None


def test_ninguno_es_respuesta_valida_del_resolutor():
    llm_resolver.set_completion_fn(lambda prompt: '{"categorias": [], "confianza": 0.0}')
    result = u("necesito un unicornio de tres cabezas")
    assert result.disposition == Disposition.CLARIFY


def test_el_resolutor_no_se_invoca_para_conversacion():
    """Un saludo no debe gastar una llamada al modelo."""
    calls = []

    def spy(prompt):
        calls.append(prompt)
        return '{"categorias": []}'

    llm_resolver.set_completion_fn(spy)
    for message in ("hola", "gracias", "qué me puedes ofrecer", "jajaja"):
        u(message)
    assert calls == [], "la conversación social no debe llegar al resolutor"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. ROBUSTEZ ANTE RUIDO DE VOZ (STT)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message", [
    "nesesito un medico",          # seseo
    "kiero agendar una sita",      # k/qu + seseo
    "boy a buscar un medico",      # b/v
    "nesecito un medico manana temprano",   # sin tildes ni puntuación
    "NECESITO UN MÉDICO",          # mayúsculas
    "necesito   un    médico",     # espacios irregulares
])
def test_tolerancia_a_transcripciones_imperfectas(message):
    result = u(message)
    assert result.understood, f"{message!r}: {result.trace}"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. CONVERSACIÓN COMPLETA, TURNO A TURNO
# ═══════════════════════════════════════════════════════════════════════════════

def test_conversacion_completa_de_descubrimiento_a_reserva():
    """
    Recorre el camino real: saludo → necesidad → selección → equipo →
    profesional → momento. Ningún turno repite lo ya dicho.
    """
    state = ConversationState()

    saludo = u("hola", state=state)
    assert saludo.intent == "greeting"

    necesidad = u("necesito un médico", state=state)
    assert necesidad.is_actionable

    # El sistema muestra resultados; eso pasa a ser contexto.
    state.remember_list(ConceptKind.BUSINESS, [
        {"id": 101, "name": "Consultorio Médico Vida Sana Popayán"},
        {"id": 102, "name": "Centro Médico Salud Norte Popayán"},
    ])

    cercania = u("cuál queda más cerca", state=state)
    assert cercania.understood and cercania.intent != "search_businesses"

    seleccion = u("quiero el segundo", state=state)
    assert seleccion.args.get("business_id") == 102

    # Se fija el negocio elegido y se muestra el equipo.
    state.set_focus(ConceptKind.BUSINESS, 102, "Centro Médico Salud Norte Popayán")
    state.booking = {"business_id": 102, "business_name": "Centro Médico Salud Norte Popayán"}

    equipo = u("muéstrame los profesionales", state=state)
    assert equipo.intent in ("get_business_professionals", "request_appointment")

    state.remember_list("professional", [
        {"id": 7, "name": "Ana Torres"},
        {"id": 8, "name": "Luis Peña"},
    ])

    eleccion = u("quiero reservar con el segundo", state=state)
    assert eleccion.intent == "request_appointment"
    assert eleccion.args.get("professional_name") == "Luis Peña"

    state.booking["professional_name"] = "Luis Peña"
    momento = u("mañana en la tarde", state=state)
    assert momento.intent == "request_appointment"
    # El negocio y la persona siguen ahí: no hubo que repetirlos.
    assert momento.args.get("business_id") == 102
    assert momento.args.get("professional_name") == "Luis Peña"


def test_el_contexto_sobrevive_al_nombre_completo_de_un_negocio():
    """
    Escribir el nombre entero de una opción es la forma más natural de elegirla.
    Y era la que peor funcionaba: "En Consultorio Médico Vida Sana Popayán"
    contiene "médico", así que la regla de categorías devolvía otra vez la lista
    en lugar de ese negocio, y la conversación volvía al principio.
    """
    state = ConversationState()
    state.remember_list(ConceptKind.BUSINESS, [
        {"id": 101, "name": "Consultorio Médico Vida Sana Popayán"},
        {"id": 102, "name": "Centro Médico Salud Norte Popayán"},
    ])
    result = u("En Consultorio Médico Vida Sana Popayán", state=state)
    assert result.intent == "navigate_to_company", result.trace
    assert result.args.get("business_id") == 101


@pytest.mark.parametrize("message,expected_id", [
    ("el primero me parece bien", 101),
    ("el primero está bien", 101),
    ("me quedo con el segundo", 102),
    ("dale el primero", 101),
    ("prefiero el segundo", 102),
    ("listo, el primero", 101),
])
def test_conformidad_en_lenguaje_natural(message, expected_id):
    """
    Elegir no es sólo decir un número: se acepta, se prefiere, se da el visto
    bueno. Todas esas formas apuntan a la misma opción.
    """
    result = u(message, state=_state_with_results())
    assert result.args.get("business_id") == expected_id, f"{message!r}: {result.trace}"


def test_una_referencia_ambigua_pregunta_cual():
    """
    "Ese me sirve" con seis opciones delante no identifica ninguna. Preguntar
    cuál es la respuesta correcta; antes acababa en un intent que nadie atendía
    y el usuario se quedaba sin contestación.
    """
    result = u("ese me sirve", state=_state_with_results())
    assert result.disposition == Disposition.CLARIFY
    assert "opciones" in (result.clarification or "").lower()


def test_usuario_sin_contexto_previo_no_hereda_nada():
    result = u("quiero reservar", state=ConversationState())
    assert result.intent == "request_appointment"
    assert not result.args.get("business_id")


def test_reformulacion_llega_al_mismo_lugar():
    """Decirlo de otra forma no debe cambiar el resultado."""
    formas = ["necesito un médico", "busco atención médica", "quiero una consulta médica"]
    intents = {u(f).intent for f in formas}
    assert intents <= {"search_businesses", "navigate_to_company"}, intents


# ═══════════════════════════════════════════════════════════════════════════════
# 12. EL ESTADO SOBREVIVE ENTRE TURNOS
# ═══════════════════════════════════════════════════════════════════════════════

def test_el_estado_se_serializa_y_se_recupera():
    """final_data viaja a la base entre turnos: debe ir y volver intacto."""
    state = ConversationState()
    state.remember_list(ConceptKind.BUSINESS, [{"id": 101, "name": "Consultorio Médico Vida Sana Popayán"}])
    state.set_focus(ConceptKind.BUSINESS, 101, "Consultorio Médico Vida Sana Popayán")
    state.booking = {"business_id": 101, "service_name": "Consulta Médica General"}

    final_data = {}
    state.save(final_data)
    restored = ConversationState.load(final_data)

    assert restored.focus_id == 101
    assert restored.booking["service_name"] == "Consulta Médica General"
    assert restored.presented[0].label == "Consultorio Médico Vida Sana Popayán"

    result = u("quiero el primero", state=restored)
    assert result.args.get("business_id") == 101
