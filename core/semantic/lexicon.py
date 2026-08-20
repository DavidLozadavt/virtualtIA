"""
core/semantic/lexicon.py — Inventario de CLASES CERRADAS del español.

Esto no es una lista de sinónimos de dominio y no debe convertirse en una.

Una clase cerrada es un conjunto gramatical finito de una lengua: sus miembros
no se inventan, no crecen con el negocio y son los mismos para hablar de
medicina, de mascotas o de cualquier cosa que NexiService registre mañana.
Pronombres, determinantes, interrogativos, preposiciones, ordinales y los verbos
que expresan modalidad (querer, poder, necesitar) pertenecen a esa clase.

Las palabras de CONTENIDO —sustantivos y verbos plenos: "médico", "perro",
"cortar"— son clase abierta y no aparecen aquí. Ésas nunca se enumeran: se
anclan contra el catálogo real (`catalog.py`) o se resuelven semánticamente
(`llm_resolver.py`).

Regla para quien mantenga este archivo: si una palabra nombra algo que se puede
vender, reservar o visitar, no va aquí.
"""

from typing import Dict, FrozenSet

from core.semantic.morphology import phonetic_stem


def _S(*words: str) -> FrozenSet[str]:
    """Congela un conjunto de formas ya normalizadas."""
    return frozenset(words)


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. DEIXIS Y ANÁFORA
# ═══════════════════════════════════════════════════════════════════════════════
# Formas que no significan nada por sí solas: apuntan a algo dicho antes.
# Su presencia es la marca gramatical de que el mensaje depende del contexto.

DEMONSTRATIVES = _S(
    "este", "esta", "estos", "estas", "esto",
    "ese", "esa", "esos", "esas", "eso",
    "aquel", "aquella", "aquellos", "aquellas", "aquello",
)

#: Pronombres de 3ª persona que SÍ apuntan a un antecedente.
#: "el", "la", "los", "las" y "lo" quedan fuera a propósito: en la abrumadora
#: mayoría de los usos son artículos ("la tarde", "el servicio"), y tratarlos
#: como anáfora hacía que "mañana en la tarde" pareciera una selección.
#: Los clíticos pospuestos ("verlo", "atenderla") se resuelven en la morfología.
PERSONAL_PRONOUNS_3P = _S("ella", "ellos", "ellas", "le", "les")

#: Clítico impersonal y reflexivo. "¿Cómo se hace?" no nombra a nadie: es
#: andamiaje, no contenido.
IMPERSONAL_CLITICS = _S("se", "si mismo")

#: Deixis de lugar. Apunta a un sitio, no a un elemento de una lista, así que se
#: mantiene separada de la anáfora: "¿qué hay por aquí?" es un descubrimiento,
#: no una selección.
LOCATIVE_ADVERBS = _S(
    "aqui", "aca", "alli", "alla", "ahi", "cerca", "cercano", "cercana",
    "cerquita", "alrededor", "zona", "por aqui", "por aca",
)

ANAPHORIC = DEMONSTRATIVES | PERSONAL_PRONOUNS_3P


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. ORDINALES Y SELECTORES DE POSICIÓN
# ═══════════════════════════════════════════════════════════════════════════════

ORDINALS: Dict[str, int] = {
    "primero": 1, "primer": 1, "primera": 1, "uno": 1, "una": 1, "1": 1,
    "segundo": 2, "segunda": 2, "dos": 2, "2": 2,
    "tercero": 3, "tercer": 3, "tercera": 3, "tres": 3, "3": 3,
    "cuarto": 4, "cuarta": 4, "cuatro": 4, "4": 4,
    "quinto": 5, "quinta": 5, "cinco": 5, "5": 5,
    "sexto": 6, "sexta": 6, "seis": 6, "6": 6,
    "septimo": 7, "septima": 7, "siete": 7, "7": 7,
    "octavo": 8, "octava": 8, "ocho": 8, "8": 8,
    "noveno": 9, "novena": 9, "nueve": 9, "9": 9,
    "decimo": 10, "decima": 10, "diez": 10, "10": 10,
}

#: Selectores relativos al extremo de la lista.
EDGE_SELECTORS = _S("ultimo", "ultima", "anterior", "siguiente")


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. INTERROGATIVOS
# ═══════════════════════════════════════════════════════════════════════════════

INTERROGATIVES = _S("que", "qué", "cual", "cuales", "quien", "quienes",
                    "donde", "cuando", "como", "cuanto", "cuanta",
                    "cuantos", "cuantas", "porque", "por que")

#: Interrogativos que preguntan por un lugar.
LOCATIVE_INTERROGATIVES = _S("donde", "adonde")

#: Interrogativos que preguntan por una persona.
PERSON_INTERROGATIVES = _S("quien", "quienes")

#: Interrogativos que seleccionan dentro de un conjunto ya dado.
SELECTIVE_INTERROGATIVES = _S("cual", "cuales")

#: Interrogativo de MANERA. Pregunta por el funcionamiento de algo, no por su
#: existencia: "¿cómo funciona esto?" quiere una explicación, no una búsqueda.
MANNER_INTERROGATIVES = _S("como")


# ═══════════════════════════════════════════════════════════════════════════════
# § 4. MODALIDAD (verbos auxiliares de deseo, necesidad y capacidad)
# ═══════════════════════════════════════════════════════════════════════════════
# Marcan CÓMO se relaciona el hablante con el contenido, no cuál es el contenido.
# Se registran por raíz para cubrir toda la conjugación sin listar formas.

#: Volitivos / necesitativos en 1ª persona: introducen una necesidad del usuario.
VOLITIVE_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "quiero", "querer", "queria", "quisiera",
        "necesito", "necesitar", "necesitaria",
        "busco", "buscar", "buscando",
        "ando", "andar",            # "ando buscando"
        "gustaria", "gustar",
        "deseo", "desear",
        "requiero", "requerir",
        "me sirve", "servir",
        "toca", "tocar",            # coloquial: "me toca ir a..."
    )
})

#: Modales de capacidad/posibilidad: "puedo", "puedes", "podría".
POTENTIAL_STEMS = frozenset({phonetic_stem(w) for w in ("puedo", "puedes", "puede", "poder", "podria", "podrias")})

#: Existenciales / posesivos: "hay", "tienen", "tienes", "existe", "conoces".
EXISTENTIAL_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "hay", "haber", "existe", "existen", "existir",
        "tiene", "tienen", "tienes", "tener",
        "maneja", "manejan", "manejas",
        "conoces", "conoce", "conocer",
        "queda", "quedan", "quedar",
    )
})

#: Verbos de presentación: piden que el sistema muestre algo.
DISPLAY_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "muestra", "muestrame", "mostrar", "mostrarme", "mostrando",
        "ensena", "ensename", "ensenar",
        "enseña", "enseñame", "enseñar", "enseñas",
        "ver", "veamos", "vea", "viendo", "mirar", "miremos", "mirando",
        "dame", "dar", "pasame", "listame", "lista",
        "indicame", "indicar", "dime", "decir", "digo", "diga", "dice",
        "cuenta", "cuentame", "contar", "cuentanos",
        "habla", "hablame", "hablar", "explica", "explicame", "explicar",
    )
})

#: Cópulas y auxiliares. Sostienen la oración sin aportar contenido; en
#: perífrasis ("estoy mirando") marcan aspecto, no una petición.
COPULA_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "soy", "eres", "es", "somos", "son", "ser", "era", "eran", "fue", "fueron",
        "estoy", "estas", "esta", "estamos", "estan", "estar", "estaba", "estuve",
        "he", "has", "ha", "hemos", "han",
    )
})

#: Verbos de actitud o preferencia. Expresan la postura del hablante frente a
#: algo ya mencionado ("ese me interesa"), no un contenido nuevo que buscar.
PREFERENCE_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "interesa", "interesar", "interesan",
        "gusta", "gustan", "encanta", "encantan",
        "prefiero", "preferir", "prefiere",
        "conviene", "convenir", "sirve",
        "parece", "parecen", "importa", "llama",
    )
})

#: Verbos de desplazamiento hacia una entidad concreta.
NAVIGATION_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "ir", "voy", "vamos", "llevar", "llevame", "llevame",
        "visitar", "visita", "entrar", "abrir", "abre", "acceder",
    )
})

MODAL_STEMS = (
    VOLITIVE_STEMS | POTENTIAL_STEMS | EXISTENTIAL_STEMS
    | DISPLAY_STEMS | NAVIGATION_STEMS | COPULA_STEMS | PREFERENCE_STEMS
)


# ═══════════════════════════════════════════════════════════════════════════════
# § 5. REFERENCIA AL AGENTE
# ═══════════════════════════════════════════════════════════════════════════════
# Marcas morfológicas de 2ª persona (el agente) frente a 1ª (el usuario).
# Sirven para separar "¿qué me puedes ofrecer TÚ?" de "¿qué hay ALLÁ AFUERA?".

SECOND_PERSON_CLITICS = _S("te", "ti", "tu", "tuyo", "tuya", "usted", "contigo", "vos")
FIRST_PERSON_CLITICS = _S("me", "mi", "yo", "conmigo", "nos", "nosotros")

#: Verbos cuyo sujeto natural, en esta aplicación, es el propio asistente.
AGENT_ACTION_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "ofreces", "ofrecer", "ofrece",
        "haces", "hacer", "hace", "haga", "hagan", "hago", "hizo",
        "sabes", "saber",
        "ayudas", "ayudar", "ayudame", "ayuda",
        "sirves", "servir", "sirve",
        "funcionas", "funcionar", "funciona",
    )
})

#: Verbos de apoyo. Aportan estructura, no contenido: lo que significa "sacar
#: una cita" está en "cita", no en "sacar". Dejarlos dentro del contenido hacía
#: que el anclaje persiguiera el verbo en vez del sustantivo.
LIGHT_VERB_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "sacar", "saco", "saque", "conseguir", "consigo", "consiga",
        "obtener", "obtengo", "adquirir", "tomar", "tomo",
        "poner", "pongo", "meter", "traer", "traiga",
        "pedir", "pido", "pida", "solicitar", "solicito",
        "usar", "uso", "realizar", "realizo", "efectuar",
    )
})


# ═══════════════════════════════════════════════════════════════════════════════
# § 6. FÓRMULAS SOCIALES
# ═══════════════════════════════════════════════════════════════════════════════
# Rutinas fáticas: abren, cierran o mantienen el canal sin pedir nada.

GREETING_FORMS = _S(
    "hola", "holi", "holis", "ola", "alo", "buenas", "buenos", "buen",
    "saludos", "hey", "ey", "epa", "quiubo", "quihubo", "qubo",
)

#: Fórmulas de apertura. Van en 2ª persona porque preguntan por el interlocutor,
#: no por una entidad del catálogo; es la contraparte de REVIEW_PHRASES.
GREETING_PHRASES = (
    "buenos dias", "buenas tardes", "buenas noches", "buen dia",
    "como estas", "como esta usted", "como te va", "como va",
    "que tal", "que mas", "que hubo", "que has hecho", "como has estado",
)

FAREWELL_FORMS = _S("chao", "chau", "adios", "bye", "nos vemos", "hasta luego", "hasta pronto")

THANKS_FORMS = _S("gracias", "grax", "thanks", "agradecido", "agradecida")

#: Retroalimentación de canal: el usuario responde sin pedir nada nuevo.
BACKCHANNEL_FORMS = _S(
    "ok", "oka", "okay", "vale", "listo", "bueno", "bien", "perfecto",
    "genial", "excelente", "entiendo", "entendido", "ya", "ajam", "aja",
    "mmm", "hmm", "uy", "wow", "nada", "nada mas",
)

#: Risa: cualquier alternancia de j/a/h/e, no una lista de variantes.
LAUGHTER_PATTERN = r"^(?:[jha]{2,}|(?:ja|je|ji|ha|he|hi|lol|xd)+)$"

AFFIRMATIVE_FORMS = _S("si", "sip", "claro", "obvio", "correcto", "exacto",
                       "dale", "hagale", "de acuerdo", "afirmativo", "confirmo", "acepto")

NEGATIVE_FORMS = _S("no", "nop", "nunca", "jamas", "negativo", "para nada", "mejor no")


# ═══════════════════════════════════════════════════════════════════════════════
# § 7. DETERMINANTES, CUANTIFICADORES Y APROXIMADORES
# ═══════════════════════════════════════════════════════════════════════════════

ARTICLES = _S("el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del")

QUANTIFIERS = _S(
    "algun", "alguna", "alguno", "algunos", "algunas", "algo", "alguien",
    "cualquier", "cualquiera", "todo", "toda", "todos", "todas",
    "otro", "otra", "otros", "otras", "mas", "menos", "varios", "varias",
    "poco", "poca", "mucho", "mucha", "muchos", "muchas", "ningun", "ninguna",
    "solo", "solamente", "nomas", "apenas", "tambien", "tampoco", "ya", "aun",
    "siempre", "nunca", "bastante", "demasiado", "casi", "medio",
    "tal", "tales", "tan", "asi", "muy",
    # Proformas: sustituyen a cualquier sustantivo sin nombrar nada. "otra cosa"
    # no es un rubro que buscar.
    "cosa", "cosas", "tema", "asunto",
)

#: Marcas de vaguedad: "hospital o algo", "algo así", "más o menos".
HEDGES = _S("o algo", "algo asi", "mas o menos", "tipo", "como que", "no se", "quizas", "tal vez")

PREPOSITIONS = _S("a", "ante", "bajo", "con", "contra", "de", "desde", "en", "entre",
                  "hacia", "hasta", "para", "por", "segun", "sin", "sobre", "tras")

CONJUNCTIONS = _S("y", "e", "o", "u", "ni", "pero", "sino", "aunque", "que", "si",
                  "porque", "pues", "mientras", "cuando", "donde")

POLITENESS = _S("por favor", "porfa", "porfis", "please", "amable", "disculpa", "perdon")

#: Marcadores de meridiano. Son clase cerrada pura: "am" y "pm" no nombran nada
#: que se pueda vender, reservar ni visitar. Sin registrarlos aquí, "9 am" dejaba
#: "am" como palabra de contenido y el mensaje se iba a buscar al catálogo.
MERIDIEM_FORMS = _S("am", "pm", "a m", "p m", "meridiano", "hrs", "hs")

#: Partes del día que hacen de meridiano en el habla: "a las 9 de la MAÑANA".
DAYPART_FORMS = _S("manana", "mañana", "tarde", "noche", "madrugada", "mediodia")

#: Determinantes que pueden seleccionar UN elemento de una lista. El plural no
#: está: "las 9" no señala el noveno elemento de nada, es una hora del día.
SELECTING_DETERMINERS = _S("el", "la", "lo", "un", "una", "al", "del")

#: Todo lo que es puro andamiaje gramatical y nunca contenido.
FUNCTION_WORDS = (
    ARTICLES | QUANTIFIERS | PREPOSITIONS | CONJUNCTIONS
    | DEMONSTRATIVES | PERSONAL_PRONOUNS_3P | INTERROGATIVES | IMPERSONAL_CLITICS
    | SECOND_PERSON_CLITICS | FIRST_PERSON_CLITICS
    | BACKCHANNEL_FORMS | AFFIRMATIVE_FORMS | NEGATIVE_FORMS
    | GREETING_FORMS | FAREWELL_FORMS | THANKS_FORMS | POLITENESS
    | frozenset(ORDINALS.keys()) | EDGE_SELECTORS | LOCATIVE_ADVERBS
    | MERIDIEM_FORMS
)

#: Raíces de todos los verbos de clase cerrada registrados arriba.
FUNCTION_STEMS = MODAL_STEMS | AGENT_ACTION_STEMS | LIGHT_VERB_STEMS


# ═══════════════════════════════════════════════════════════════════════════════
# § 8. MARCOS SEMÁNTICOS UNIVERSALES
# ═══════════════════════════════════════════════════════════════════════════════
# Un "marco" es una relación abstracta que existe en cualquier lengua y en
# cualquier dominio: concertar un encuentro, referirse a un agente humano,
# situar en el tiempo. No nombran productos de NexiService.

#: Marco de CITA: fijar un punto de encuentro futuro con alguien que atiende.
APPOINTMENT_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "cita", "citas", "reserva", "reservar", "reservame",
        "agenda", "agendar", "agendame", "agendamiento",
        "turno", "turnos", "separar", "apartar", "aparta",
        "atiendan", "atender", "atenderme", "atienden",
        "disponibilidad", "disponible", "cupo", "hueco", "espacio",
    )
})

#: Marco de AGENTE HUMANO: la persona que presta el servicio.
HUMAN_AGENT_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "profesional", "profesionales", "prestador", "prestadores",
        "especialista", "especialistas", "equipo", "personal",
        "trabajan", "trabaja", "trabajar", "atiende", "atienden",
        "encargado", "encargada", "staff",
    )
})

#: Nombres de días y meses. Se comparan como formas EXACTAS, no por raíz: son
#: un inventario cerrado y recortarlos los hace chocar con palabras corrientes
#: ("sábado" y "sabes" comparten la raíz "sab").
CALENDAR_WORDS = _S(
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
)

#: Marco TEMPORAL: sitúa un evento en el tiempo.
TEMPORAL_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "hoy", "manana", "mañana", "ayer", "tarde", "noche", "madrugada",
        "hora", "horas", "fecha", "semana", "mes", "dia", "dias",
        # Adverbios que sitúan sin nombrar un momento concreto.
        "temprano", "pronto", "luego", "despues", "rato", "ahorita", "enseguida",
    )
})

#: Marco de OFERTA: pregunta por lo que una entidad pone a disposición.
OFFERING_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "servicio", "servicios", "catalogo", "carta", "menu",
        "precio", "precios", "valor", "costo", "tarifa", "cobran",
    )
})

#: Marco de ENTIDAD COMERCIAL genérica, sin decir de qué rubro.
GENERIC_PLACE_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "negocio", "negocios", "empresa", "empresas", "local", "locales",
        "lugar", "lugares", "sitio", "sitios", "establecimiento",
        "opcion", "opciones", "alternativa", "alternativas", "directorio",
    )
})

#: Marco de VALORACIÓN: opiniones de terceros sobre una entidad.
REVIEW_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "resena", "resenas", "reseña", "reseñas",
        "opinion", "opiniones", "opina", "opinan", "opinas",
        "comentario", "comentarios", "comentan",
        "calificacion", "calificaciones", "calificado",
        "valoracion", "estrellas", "reputacion", "recomiendan",
    )
})

#: Giros fijos que preguntan por la reputación de algo. Son locuciones, no
#: sinónimos: su significado no se deduce sumando las palabras que las forman.
#:
#: Todas están en TERCERA persona, y eso es lo que las separa del saludo. "¿Cómo
#: estás?" pregunta por el interlocutor; "¿cómo está?" pregunta por un negocio.
#: Se comparan con límite de palabra: sin él, "como esta" se encuentra dentro de
#: "como estas" y un saludo acababa leído como consulta de reseñas.
REVIEW_PHRASES = (
    "que tal es", "que tal esta", "que tal son",
    "como es", "como esta", "como son",
    "que dicen", "que piensan", "que opinan",
)

#: Marco de IDENTIDAD DE LA EMPRESA: quién es, de dónde viene, qué la mueve.
IDENTITY_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "mision", "vision", "historia", "trayectoria", "filosofia", "valores",
    )
})

#: Marco de PRESENCIA EN LÍNEA: dónde encontrarla fuera de la aplicación.
WEB_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "web", "pagina", "sitio", "website", "url", "enlace", "link",
        "facebook", "instagram", "tiktok", "whatsapp", "redes",
    )
})

#: Marco de RECOMENDACIÓN: pide un criterio de calidad, no un nombre.
RECOMMEND_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "recomienda", "recomiendame", "recomendacion", "recomendar",
        "sugiere", "sugiereme", "sugerencia", "sugerir",
        "mejor", "mejores", "destacado", "destacados", "top", "popular", "populares",
    )
})

#: Marco de COMPARACIÓN: pone dos cosas frente a frente.
COMPARE_FRAME_STEMS = frozenset({
    phonetic_stem(w) for w in (
        "compara", "comparar", "comparacion", "comparativa",
        "diferencia", "diferencias", "versus",
    )
})

#: Marco de MAPA: pide situar algo en el plano.
MAP_FRAME_STEMS = frozenset({phonetic_stem(w) for w in ("mapa", "plano", "ubicacion", "ubicar")})

#: Todos los marcos, para saber si una palabra de contenido ya está explicada
#: por la estructura y no necesita anclarse al catálogo.
FRAME_STEMS = (
    APPOINTMENT_FRAME_STEMS | HUMAN_AGENT_FRAME_STEMS | TEMPORAL_FRAME_STEMS
    | OFFERING_FRAME_STEMS | GENERIC_PLACE_STEMS | REVIEW_FRAME_STEMS
    | IDENTITY_FRAME_STEMS | WEB_FRAME_STEMS | RECOMMEND_FRAME_STEMS
    | COMPARE_FRAME_STEMS | MAP_FRAME_STEMS
)


def is_function_word(word: str) -> bool:
    """
    True si la palabra es andamiaje gramatical y no contenido.

    La comparación por raíz es tolerante: una errata o una transcripción
    imperfecta ("ofreser" por "ofrecer") sigue reconociéndose como el mismo
    verbo de clase cerrada.
    """
    from core.semantic.morphology import in_stem_set, normalize
    w = normalize(word)
    if not w:
        return True
    if w in FUNCTION_WORDS or w in CALENDAR_WORDS:
        return True
    return in_stem_set(phonetic_stem(w), FUNCTION_STEMS)


#: Raíces que nombran el ACTO de agendar o la idea abstracta de "servicio",
#: nunca un servicio concreto del catálogo.
_CONTENTLESS_STEMS = (
    APPOINTMENT_FRAME_STEMS | OFFERING_FRAME_STEMS
    | GENERIC_PLACE_STEMS | TEMPORAL_FRAME_STEMS
)


def names_nothing_concrete(text: str) -> bool:
    """
    True si el texto pide agendar sin decir QUÉ.

    "agendar cita", "quiero una reserva" y "cita para mañana" describen el acto,
    no lo que se agenda. Guardarlos como nombre de servicio producía una
    búsqueda de un servicio llamado "agendar cita", y de ahí el "no encontré el
    servicio 'agendar cita'" que veía el usuario justo después de pedir una.

    La comprobación es por palabra: el filtro anterior comparaba la frase entera
    contra una lista de palabras sueltas, así que "agendar" se detenía y
    "agendar cita" pasaba de largo.
    """
    from core.semantic.morphology import in_stem_set, normalize
    palabras = normalize(text or "").split()
    if not palabras:
        return True
    for palabra in palabras:
        if is_function_word(palabra):
            continue
        if in_stem_set(phonetic_stem(palabra), _CONTENTLESS_STEMS):
            continue
        return False
    return True
