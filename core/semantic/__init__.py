"""
core/semantic — Capa de comprensión semántica de Lyra/Nexo.

Esta capa responde a UNA pregunta antes de que el sistema actúe:

    ¿Qué está intentando hacer el usuario, y hay algo real en NexiService
    que corresponda a eso?

Se apoya en etapas encadenadas, no en listas de frases:

    0. Lectura dialógica (dialogue)
       Si Lyra dejó una pregunta abierta, el mensaje se lee primero como su
       respuesta. Es la evidencia contextual más fuerte que existe y por eso va
       antes que todo: "9 am" no significa nada por sí solo, y lo significa todo
       después de "¿a qué hora?". Si el mensaje no responde a lo preguntado,
       esta etapa se aparta y el análisis sigue su curso normal.

    A. Análisis de acto de habla (speech_act)
       Usa la GRAMÁTICA del español —clases cerradas: pronombres, interrogativos,
       verbos de modalidad, deícticos, ordinales— para decidir la FUNCIÓN del
       mensaje y aislar el contenido abierto. No conoce ningún dominio.

    B. Anclaje sobre el catálogo real (catalog)
       El contenido abierto se compara contra un índice construido desde la base
       de datos de NexiService (categorías, negocios, servicios). Si el sistema
       no tiene ese concepto, no existe.

    C. Resolución semántica asistida (llm_resolver)
       Sólo cuando A dice "esto es una necesidad" y B no logró anclarla, se pide
       al LLM que traduzca la necesidad a un concepto REAL del catálogo, con
       NINGUNO como respuesta legítima.

El resultado es un objeto `Understanding` tipado que el resto del orquestador
consume. Si nada se ancla, el sistema lo declara explícitamente en vez de
convertir la frase del usuario en una consulta SQL literal.
"""

from core.semantic.types import (
    Act,
    Understanding,
    Grounding,
    GroundedConcept,
    ConversationState,
    PresentedItem,
)
from core.semantic import temporal
from core.semantic.dialogue import Slot, next_missing_slot, read_answer, SLOT_QUESTIONS
from core.semantic.engine import understand, build_understanding

__all__ = [
    "Act",
    "Understanding",
    "Grounding",
    "GroundedConcept",
    "ConversationState",
    "PresentedItem",
    "Slot",
    "SLOT_QUESTIONS",
    "next_missing_slot",
    "read_answer",
    "temporal",
    "understand",
    "build_understanding",
]
