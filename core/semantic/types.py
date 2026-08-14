"""
core/semantic/types.py — Vocabulario tipado de la capa de comprensión.

Nada aquí conoce NexiService ni ninguna palabra de dominio. Son las formas que
viajan entre las etapas: qué hizo el usuario (Act), qué contenido abierto quedó,
contra qué se ancló (Grounding) y qué recuerda la conversación
(ConversationState).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. ACTOS DE HABLA
# ═══════════════════════════════════════════════════════════════════════════════

class Act:
    """
    Función comunicativa del mensaje, determinada por su estructura gramatical.

    No es una lista de intenciones de producto: es qué está HACIENDO el usuario
    con el lenguaje. La traducción a intents de NexiService ocurre después, y
    depende además de si el contenido logra anclarse al catálogo real.
    """

    # — Social / fático: no pide nada al sistema —
    GREET          = "greet"           # apertura de contacto
    FAREWELL       = "farewell"        # cierre de contacto
    THANKS         = "thanks"          # agradecimiento
    BACKCHANNEL    = "backchannel"     # "ok", "jajaja", "estoy mirando"

    # — Dirigido al agente mismo, no al catálogo —
    AGENT_CAPABILITY = "agent_capability"  # "¿qué me puedes ofrecer?"
    AGENT_IDENTITY   = "agent_identity"    # "¿quién eres?"

    # — Expresiones de necesidad / consulta sobre el mundo —
    NEED             = "need"              # "necesito un médico", "quiero cortarme el cabello"
    EXISTENTIAL      = "existential"       # "¿hay barberías?", "¿qué negocios tienen?"
    LOCATIVE         = "locative"          # "¿dónde puedo conseguir eso?", "¿qué hay por aquí?"

    # — Sobre entidades ya presentes en la conversación —
    REFERENCE        = "reference"         # "el primero", "ese", "con ella"
    ATTRIBUTE        = "attribute"         # "¿cuál queda más cerca?", "¿ese tiene citas?"
    PERSON_QUERY     = "person_query"      # "¿quiénes trabajan ahí?", "muéstrame los profesionales"

    # — Agendamiento —
    BOOKING          = "booking"           # "quiero separar una hora"
    TEMPORAL         = "temporal"          # "mañana en la tarde" (complemento suelto)

    # — Sintagma nominal desnudo: puede ser nombre propio o concepto —
    BARE_NOMINAL     = "bare_nominal"      # "Fogón Criollo", "alguna medicina"

    # — Confirmación / negación —
    AFFIRM           = "affirm"
    DENY             = "deny"

    # — No se pudo derivar función accionable —
    UNPARSEABLE      = "unparseable"

    #: Actos que expresan una búsqueda o descubrimiento sobre el catálogo.
    DISCOVERY = frozenset({NEED, EXISTENTIAL, LOCATIVE, BARE_NOMINAL})

    #: Actos puramente conversacionales: jamás deben disparar una herramienta.
    CONVERSATIONAL = frozenset({
        GREET, FAREWELL, THANKS, BACKCHANNEL,
        AGENT_CAPABILITY, AGENT_IDENTITY,
    })

    #: Actos que sólo tienen sentido contra el estado previo de la conversación.
    CONTEXTUAL = frozenset({REFERENCE, ATTRIBUTE, TEMPORAL, AFFIRM, DENY, PERSON_QUERY})


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. ANCLAJE AL CATÁLOGO REAL
# ═══════════════════════════════════════════════════════════════════════════════

class ConceptKind:
    """Tipo de cosa real que existe dentro de NexiService."""
    BUSINESS         = "business"           # una empresa concreta
    BUSINESS_CATEGORY = "business_category" # una categoría de empresas
    SERVICE          = "service"            # un servicio concreto
    SERVICE_CATEGORY = "service_category"   # una categoría de servicios


@dataclass
class GroundedConcept:
    """Un concepto del catálogo al que se ancló el contenido del usuario."""
    kind: str
    label: str
    score: float
    entity_id: Optional[int] = None
    #: Términos de búsqueda reales derivados del catálogo, nunca la frase cruda.
    search_terms: List[str] = field(default_factory=list)
    #: De dónde salió el anclaje: "lexical", "llm", "state".
    source: str = "lexical"
    #: True cuando varios conceptos del mismo tipo encajan igual de bien. La
    #: coincidencia es buena, pero no identifica UNA cosa: describe un rubro.
    ambiguous: bool = False

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return f"<{self.kind}:{self.label} score={self.score:.2f} via={self.source}>"


@dataclass
class Grounding:
    """Resultado de intentar anclar contenido abierto al catálogo."""
    concepts: List[GroundedConcept] = field(default_factory=list)
    #: Palabras de contenido que se intentaron anclar.
    content_terms: List[str] = field(default_factory=list)
    #: Subconjunto de `content_terms` que el catálogo sí reconoce. Son las
    #: únicas palabras del usuario que pueden llegar a una consulta: existen.
    matched_terms: List[str] = field(default_factory=list)
    #: True si había contenido anclable pero el catálogo no lo reconoce.
    attempted: bool = False

    @property
    def resolved(self) -> bool:
        return bool(self.concepts)

    @property
    def best(self) -> Optional[GroundedConcept]:
        return self.concepts[0] if self.concepts else None

    @property
    def unmatched_terms(self) -> List[str]:
        """Palabras del usuario que el catálogo no reconoce en absoluto."""
        known = set(self.matched_terms)
        return [t for t in self.content_terms if t not in known]


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. ESTADO CONVERSACIONAL ESTRUCTURADO
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PresentedItem:
    """Un elemento que Lyra ya le mostró al usuario y que puede referenciar."""
    kind: str                    # ConceptKind.BUSINESS | SERVICE | "professional"
    entity_id: Optional[int]
    label: str
    position: int                # 1-indexado, tal como lo vio el usuario
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "entity_id": self.entity_id,
            "label": self.label,
            "position": self.position,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PresentedItem":
        return cls(
            kind=raw.get("kind") or "",
            entity_id=raw.get("entity_id"),
            label=raw.get("label") or "",
            position=int(raw.get("position") or 0),
            extra=raw.get("extra") or {},
        )


@dataclass
class ConversationState:
    """
    Lo que la conversación ya estableció.

    Se persiste dentro de `final_data` (que el orquestador ya guarda por
    conversación), así que no introduce almacenamiento nuevo. Reemplaza el
    raspado por regex del markdown del asistente como fuente primaria; ese
    raspado sigue existiendo como respaldo para conversaciones antiguas.
    """
    #: Última lista mostrada al usuario, en el orden en que la vio.
    presented: List[PresentedItem] = field(default_factory=list)
    #: Entidad sobre la que gira la conversación ahora mismo.
    focus_kind: Optional[str] = None
    focus_id: Optional[int] = None
    focus_label: Optional[str] = None
    #: Dominio semántico activo (etiqueta real de categoría del catálogo).
    active_domain: Optional[str] = None
    #: Ranuras de agendamiento acumuladas a lo largo de varios mensajes.
    booking: Dict[str, Any] = field(default_factory=dict)
    #: Último acto de habla y si Lyra dejó una pregunta abierta.
    last_act: Optional[str] = None
    pending_question: Optional[str] = None
    #: Objetivo que la conversación está intentando completar ("booking").
    goal: Optional[str] = None
    #: Ranura concreta que Lyra acaba de pedir ("time", "date", "service"…).
    #: Es lo que convierte "9 am" en una respuesta y no en un mensaje suelto.
    pending_slot: Optional[str] = None
    #: Ranuras que el usuario afirmó explícitamente, no que dedujimos nosotros.
    confirmed: List[str] = field(default_factory=list)
    #: Reemplazos que hizo el usuario: [{"slot", "from", "to"}]. Sirven para
    #: saber que un dato ya fue corregido y no volver a proponer el anterior.
    corrections: List[Dict[str, Any]] = field(default_factory=list)

    # ── serialización sobre final_data ──────────────────────────────────────

    STORAGE_KEY = "_semantic_state"

    #: Cuántas correcciones se conservan. Interesa la última palabra del
    #: usuario, no el historial completo de cambios de opinión.
    MAX_CORRECTIONS = 8

    def to_dict(self) -> Dict[str, Any]:
        return {
            "presented": [p.to_dict() for p in self.presented],
            "focus_kind": self.focus_kind,
            "focus_id": self.focus_id,
            "focus_label": self.focus_label,
            "active_domain": self.active_domain,
            "booking": self.booking,
            "last_act": self.last_act,
            "pending_question": self.pending_question,
            "goal": self.goal,
            "pending_slot": self.pending_slot,
            "confirmed": self.confirmed,
            "corrections": self.corrections,
        }

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "ConversationState":
        raw = raw or {}
        return cls(
            presented=[PresentedItem.from_dict(p) for p in (raw.get("presented") or [])],
            focus_kind=raw.get("focus_kind"),
            focus_id=raw.get("focus_id"),
            focus_label=raw.get("focus_label"),
            active_domain=raw.get("active_domain"),
            booking=dict(raw.get("booking") or {}),
            last_act=raw.get("last_act"),
            pending_question=raw.get("pending_question"),
            goal=raw.get("goal"),
            pending_slot=raw.get("pending_slot"),
            confirmed=list(raw.get("confirmed") or []),
            corrections=list(raw.get("corrections") or []),
        )

    @classmethod
    def load(cls, final_data: Optional[Dict[str, Any]]) -> "ConversationState":
        return cls.from_dict((final_data or {}).get(cls.STORAGE_KEY))

    def save(self, final_data: Dict[str, Any]) -> Dict[str, Any]:
        final_data[self.STORAGE_KEY] = self.to_dict()
        return final_data

    # ── mutaciones ──────────────────────────────────────────────────────────

    def remember_list(self, kind: str, items: List[Dict[str, Any]]) -> None:
        """Registra la lista que se le acaba de mostrar al usuario."""
        self.presented = [
            PresentedItem(
                kind=kind,
                entity_id=_as_int(item.get("id")),
                label=str(item.get("name") or item.get("nombre") or item.get("label") or ""),
                position=idx,
                extra={
                    k: _as_json_safe(v)
                    for k, v in item.items()
                    if k in ("address", "distance_km", "valor", "category")
                },
            )
            for idx, item in enumerate(items, start=1)
        ]

    def set_focus(self, kind: str, entity_id: Optional[int], label: str) -> None:
        self.focus_kind = kind
        self.focus_id = _as_int(entity_id)
        self.focus_label = label

    def clear_booking(self) -> None:
        self.booking = {}
        self.goal = None
        self.pending_slot = None
        self.pending_question = None
        self.confirmed = []
        self.corrections = []

    # ── la pregunta abierta ─────────────────────────────────────────────────

    def expect(self, slot: Optional[str], question: Optional[str] = None,
               goal: Optional[str] = None) -> None:
        """
        Deja constancia de QUÉ acaba de preguntar Lyra.

        Sin esto, el turno siguiente no tiene forma de saber que "9 am" es una
        respuesta: se leía como un mensaje suelto y la conversación empezaba de
        cero. Es el dato que convierte una secuencia de peticiones sueltas en un
        diálogo.
        """
        self.pending_slot = slot
        self.pending_question = question
        if goal:
            self.goal = goal

    def fulfil(self, slot: str, value: Any, confirmed: bool = True) -> None:
        """
        Anota el valor de una ranura y registra si reemplaza a otro anterior.

        Una corrección sustituye SÓLO lo que el usuario nombró: el resto de lo
        acordado sigue en pie.
        """
        previous = self.booking.get(slot)
        if previous is not None and previous != value:
            self.corrections.append({"slot": slot, "from": previous, "to": value})
            del self.corrections[:-self.MAX_CORRECTIONS]
        self.booking[slot] = value
        if confirmed and slot not in self.confirmed:
            self.confirmed.append(slot)
        if self.pending_slot == slot:
            self.pending_slot = None
            self.pending_question = None

    def suspend(self) -> None:
        """
        El usuario cambió de tema.

        Se cierra la pregunta abierta, pero NO se tira lo acordado: si vuelve a
        la reserva, el negocio, el servicio y la hora siguen ahí. Reiniciar el
        contexto ante cualquier desvío es justo lo que obligaba a repetirlo todo.
        """
        self.pending_slot = None
        self.pending_question = None

    @property
    def is_collecting(self) -> bool:
        """¿Hay un objetivo abierto esperando un dato concreto?"""
        return bool(self.pending_slot)

    def missing(self, required: Sequence[str]) -> List[str]:
        """Ranuras que todavía faltan, en el orden en que se piden."""
        return [s for s in required if not self.booking.get(s)]

    @property
    def has_context(self) -> bool:
        return bool(self.presented or self.focus_id or self.active_domain or self.booking)


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_json_safe(value: Any) -> Any:
    """
    Convierte un valor a algo que sobreviva el viaje a la base de datos.

    El estado se guarda como JSON dentro de `final_data`, y lo que llega de MySQL
    no siempre lo es: los precios vienen como `Decimal` y las fechas como
    `datetime`. Uno solo de esos valores rompía el guardado de la conversación
    entera con un 500.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


# ═══════════════════════════════════════════════════════════════════════════════
# § 4. RESULTADO DE LA COMPRENSIÓN
# ═══════════════════════════════════════════════════════════════════════════════

class Disposition:
    """Qué debe hacer el sistema con lo comprendido."""
    ACT       = "act"       # comprendido y accionable: ejecutar la capacidad
    CONVERSE  = "converse"  # comprendido, pero no requiere herramienta
    CLARIFY   = "clarify"   # no se pudo determinar con seguridad qué quiere


@dataclass
class Understanding:
    """
    Lo que Lyra entendió de un mensaje, en términos accionables.

    `intent` y `args` son deliberadamente los mismos nombres que ya usa el
    orquestador, para que la capa mejore la comprensión sin obligar a reescribir
    los interceptores ni las herramientas.
    """
    act: str
    disposition: str
    intent: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    grounding: Grounding = field(default_factory=Grounding)
    confidence: float = 0.0
    #: Traza auditable de por qué se decidió esto.
    trace: List[str] = field(default_factory=list)
    #: Pregunta mínima a hacer cuando disposition == CLARIFY.
    clarification: Optional[str] = None
    #: Ranura que queda esperando respuesta tras este turno. Es lo que permite
    #: que el mensaje siguiente se lea como su respuesta y no como algo nuevo.
    expects: Optional[str] = None
    #: Ranuras que este turno reemplazó: [{"slot", "from", "to"}].
    corrections: List[Dict[str, Any]] = field(default_factory=list)

    def note(self, message: str) -> "Understanding":
        self.trace.append(message)
        return self

    @property
    def is_actionable(self) -> bool:
        return self.disposition == Disposition.ACT and bool(self.intent)

    @property
    def understood(self) -> bool:
        """True si sabemos qué quiere el usuario, aunque no haya resultados."""
        return self.disposition in (Disposition.ACT, Disposition.CONVERSE)

    def to_intent_dict(self) -> Dict[str, Any]:
        """Forma que consume `run_agent_loop` / los interceptores existentes."""
        return {
            "intent": self.intent,
            "args": dict(self.args),
            "_understanding": self,
        }
