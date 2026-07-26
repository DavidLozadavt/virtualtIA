"""
core/conversation_repair.py — Motor de reparación conversacional inteligente para Lyra.

En vez de "No entendí", genera respuestas contextuales como:
  "¿Estás cerca del Campanario?"
  "¿Tu destino sería hacia el norte, por el ortigal?"
  "Te escuché algo de la 15 — ¿calle 15 con carrera 9?"

Implementa:
- Hipótesis parciales con confirmación
- Memoria conversacional de ubicaciones mencionadas
- Inferencia de intención con frases incompletas
- Reparación progresiva (no solo "repite")
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

from core.stt_enhancer import (
    strip_accents,
    fuzzy_match_location,
    resolve_human_reference,
    HUMAN_REFERENCES,
)

logger = logging.getLogger("lyra.conversation_repair")


# ── Memoria conversacional ────────────────────────────────────────────────────

@dataclass
class ConversationMemory:
    """
    Memoria de la conversación actual.
    Registra ubicaciones mencionadas, hipótesis previas y correcciones.
    """
    call_sid:              str
    mentioned_locations:   list[str]  = field(default_factory=list)
    last_confirmed_origin: Optional[str] = None
    last_confirmed_dest:   Optional[str] = None
    partial_hypotheses:    list[dict]  = field(default_factory=list)
    correction_attempts:   int         = 0
    user_corrections:      list[str]   = field(default_factory=list)
    intent_history:        list[str]   = field(default_factory=list)

    def add_location_mention(self, location: str) -> None:
        """Registra una ubicación mencionada (sin confirmar)."""
        loc = location.strip()
        if loc and loc not in self.mentioned_locations:
            self.mentioned_locations.append(loc)
            # Máximo 10 ubicaciones en memoria
            if len(self.mentioned_locations) > 10:
                self.mentioned_locations.pop(0)

    def add_hypothesis(self, text: str, extracted: str, confidence: float) -> None:
        self.partial_hypotheses.append({
            "raw":       text,
            "extracted": extracted,
            "confidence": confidence,
        })
        if len(self.partial_hypotheses) > 5:
            self.partial_hypotheses.pop(0)

    def last_location(self) -> Optional[str]:
        """Última ubicación mencionada en la conversación."""
        return self.mentioned_locations[-1] if self.mentioned_locations else None

    def second_to_last_location(self) -> Optional[str]:
        if len(self.mentioned_locations) >= 2:
            return self.mentioned_locations[-2]
        return None

    def best_hypothesis(self) -> Optional[dict]:
        """Hipótesis con mayor confianza."""
        if not self.partial_hypotheses:
            return None
        return max(self.partial_hypotheses, key=lambda h: h["confidence"])


# ── Extracción de ubicación parcial (para hipótesis de reparación) ───────────


def _extract_partial_location(text: str) -> Optional[str]:
    """
    Extrae cualquier fragmento que parezca un lugar (aún si el texto es incompleto).
    Útil para hipótesis parciales.
    """
    t = text.strip()

    # Patrones de ubicación directa
    patterns = [
        r"(?:calle|carrera|cl|cra|kr)\s*\d+[a-záéíóú]?(?:\s*(?:con|y|#)\s*\d+)?",
        r"(?:barrio|sector|conjunto|urbanizaci[oó]n)\s+[\w\s]+",
        r"(?:parque|plaza|plazuela|hospital|clínica|clinica|colegio|estadio|terminal|aeropuerto)\s*[\w\s]*",
    ]

    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return m.group(0).strip()

    # Buscar en referencias humanas conocidas
    ref = resolve_human_reference(t)
    if ref:
        return ref["canonical"]

    return None


# ── Generador de reparaciones conversacionales ────────────────────────────────

class ConversationRepair:
    """
    Genera respuestas de reparación inteligentes cuando el STT falla o
    el texto es ambiguo, en lugar de solo decir "No entendí".
    """

    # Barrios de Popayán agrupados por zona (para sugerencias contextuales)
    ZONES: dict[str, list[str]] = {
        "norte": [
            "los sauces", "prados del norte", "villa del norte", "la primavera",
            "el tablazo", "bello horizonte", "loma linda", "modelo", "pubenza",
        ],
        "sur": [
            "yanaconas", "pandiguando", "las américas", "santa rosa", "el uvo",
            "cinco de abril", "la libertad", "nueva colombia",
        ],
        "centro": [
            "centro", "parque caldas", "la pamba", "los hoyos", "sindical",
            "berlin", "la estancia", "los andes",
        ],
        "oriente": [
            "campanario", "valle del ortigal", "polideportivo", "valle vertical",
            "belalcázar", "la esmeralda", "los comuneros",
        ],
        "occidente": [
            "maría oriente", "camilo torres", "yambitará", "pueblillo",
            "los álamos", "la floresta", "santa clara",
        ],
    }

    # Templates de reparación por tipo de problema
    REPAIR_TEMPLATES: dict[str, list[str]] = {
        "partial_location": [
            "Te escuché algo de {partial} — ¿es ahí donde estás?",
            "¿Estás por {partial}?",
            "Me parece que dijiste {partial} — ¿correcto?",
        ],
        # Nunca culpar al usuario ni pedirle que suba la voz / vocalice. El
        # sistema se adapta al usuario, no al revés. Pedimos un dato más simple.
        "noisy_audio": [
            "¿Me dices solo el barrio o la calle?",
            "Para ubicarte mejor, ¿cuál es el barrio?",
            "¿En qué barrio o calle estás?",
        ],
        "too_fast": [
            "Ibas un poco rápido. ¿Me repites la dirección despacio?",
            "¿Puedes decirme el barrio o la calle más despacio?",
        ],
        "cut_off": [
            "Se cortó. ¿Me completas la dirección?",
            "No te escuché completo. ¿Dónde queda exactamente?",
        ],
        "no_location": [
            "¿En qué barrio o calle de Popayán estás?",
            "¿Dónde te recogemos? Dime el barrio o la calle.",
            "¿Cuál es tu punto de recogida en Popayán?",
        ],
        "ambiguous_zone": [
            "¿Eso queda en el norte o en el sur de la ciudad?",
            "¿Estás por el sector de {zone_hint} o por otro lado?",
            "¿Cerca del centro o más hacia afuera?",
        ],
        "hypothesis_confirm": [
            "¿Te recogemos en {hypothesis}?",
            "¿Estás por {hypothesis}, correcto?",
            "El punto sería {hypothesis} — ¿sí?",
        ],
        "last_location_relative": [
            "¿Eso sería cerca de {last_location}?",
            "¿Más hacia {last_location} o diferente zona?",
            "¿Seguimos por {last_location}?",
        ],
        # Reintentos consecutivos (>=2): el usuario ya falló varias veces. En vez
        # de repetir "no entendí", simplificar la tarea y pedir la dirección por
        # partes pequeñas, empezando por el barrio. Pensado para adultos mayores.
        "retry_step_by_step": [
            "Tranquilo, vamos paso a paso. Primero dígame solo el nombre del barrio.",
            "No se preocupe, hagámoslo simple. ¿Cuál es solo el nombre del barrio?",
            "Vamos despacio y por partes. Dígame nada más el barrio donde está.",
        ],
    }

    def __init__(self):
        self._template_indices: dict[str, int] = {}

    def _next_template(self, key: str) -> str:
        """Rota entre templates para no repetir siempre la misma frase."""
        templates = self.REPAIR_TEMPLATES.get(key, ["¿Me lo repites?"])
        idx = self._template_indices.get(key, 0)
        template = templates[idx % len(templates)]
        self._template_indices[key] = (idx + 1) % len(templates)
        return template

    def generate_repair(
        self,
        text:       str,
        confidence: float,
        state:      str,
        memory:     ConversationMemory,
        intent:     Optional[dict] = None,
    ) -> str:
        """
        Genera el mensaje de reparación más apropiado para la situación.
        
        Prioridad:
        1. Si hay hipótesis parcial → confirmar con el usuario
        2. Si hay ubicación mencionada antes → usar como referencia
        3. Si audio es muy ruidoso → pedir solo el barrio
        4. Si frase cortada → pedir que completen
        5. Fallback contextual por estado
        """
        t = (text or "").strip()
        words = t.split()

        # ── 1. Hipótesis parcial: hay algo identificable en el texto ──
        partial = _extract_partial_location(t)
        if partial and confidence >= 0.25:
            # Suficiente señal para proponer hipótesis
            memory.add_hypothesis(t, partial, confidence)
            template = self._next_template("hypothesis_confirm")
            return template.format(hypothesis=partial)

        # ── 2. Ubicación parcial débil pero mencionada antes ──
        if partial and memory.last_location():
            last = memory.last_location()
            template = self._next_template("last_location_relative")
            return template.format(last_location=last)

        # ── 3. Audio muy ruidoso (confianza muy baja consistentemente) ──
        if confidence < 0.25 and len(words) > 2:
            return self._next_template("noisy_audio")

        # ── 4. Frase muy larga con nada extraíble → habló rápido ──
        if len(words) > 6 and not partial:
            return self._next_template("too_fast")

        # ── 5. Frase cortada (termina en preposición/artículo) ──
        cut_tokens = {"en", "de", "del", "la", "el", "las", "los", "con", "por", "al", "a", "hacia", "para"}
        if words and words[-1].lower().rstrip(".,;") in cut_tokens:
            return self._next_template("cut_off")

        # ── 6. Solo tiene número sin contexto de calle ──
        if re.match(r"^\d+$", t.strip()):
            if state == "waiting_origin":
                return "¿Es calle o carrera? Por ejemplo, calle quince."
            return "¿Es calle o carrera?"

        # ── 7. Tiene número pero falta tipo de vía ──
        has_number = bool(re.search(r"\d+", t))
        has_street = bool(re.search(r"\b(calle|carrera|cl|cra|kr)\b", t.lower()))
        if has_number and not has_street:
            return "¿Es calle o carrera? Dime el número y el tipo de vía."

        # ── 8. Fallback por estado ──
        if state == "waiting_origin":
            return self._next_template("no_location")
        elif state == "waiting_dest_or_skip":
            return "¿A dónde vas? O dime no para que le digas al conductor."
        elif state == "confirming_origin":
            hyp = memory.best_hypothesis()
            if hyp:
                return f"¿Es {hyp['extracted']}?"
            return "¿Me confirmas el barrio o la dirección?"

        return "¿Me repites, por favor?"

# ── Manejador de interrupciones (barge-in) ────────────────────────────────────

class BargeInHandler:
    """
    Detecta y maneja interrupciones del usuario mientras Lyra habla.
    
    Permite al usuario cortar a Lyra y cambiar el flujo sin perder el estado.
    """

    # Palabras que claramente indican que el usuario quiere interrumpir
    INTERRUPT_SIGNALS = [
        r"\b(espera|espere|para|párate|detente|stop)\b",
        r"\b(no|oye|ey|eh|mira|mire|ve)\s+(?:un momento|momento|espera)",
        r"\b(me equivoqu[eé]|cambiar|corregir|eso no)\b",
        r"\b(ya|suficiente|ok ok|sí sí|dale dale)\b",
    ]

    @classmethod
    def is_interruption(cls, partial_text: str) -> bool:
        """
        Detecta si el texto parcial indica intención de interrumpir.
        Se usa con partialResultCallback de Twilio.
        """
        if not partial_text:
            return False
        t = strip_accents(partial_text.lower().strip())
        return any(re.search(pat, t) for pat in cls.INTERRUPT_SIGNALS)

    @classmethod
    def extract_post_interrupt_content(cls, text: str) -> str:
        """
        Después de una interrupción, extrae el contenido real.
        "espera, quiero ir a campanario" → "quiero ir a campanario"
        """
        t = text.strip()
        # Remover señal de interrupción al inicio
        for pat in cls.INTERRUPT_SIGNALS:
            t = re.sub(r"^" + pat + r"[,.]?\s*", "", t, flags=re.IGNORECASE).strip()
        return t or text.strip()


# ── Instancia singleton del motor de reparación ───────────────────────────────

_repair_engine = ConversationRepair()

# A partir de cuántos reintentos consecutivos conviene simplificar la pregunta.
RETRY_SIMPLIFY_THRESHOLD = 2


def get_progressive_retry_message(retry_count: int) -> Optional[str]:
    """Mensaje de reparación simplificado para reintentos consecutivos.

    Cuando el usuario ya falló `RETRY_SIMPLIFY_THRESHOLD` (2) o más veces, en vez
    de repetir "no entendí" se le pide la dirección por partes (barrio primero).
    Devuelve None si todavía no aplica, para que el caller use su mensaje normal.
    """
    if retry_count < RETRY_SIMPLIFY_THRESHOLD:
        return None
    return _repair_engine._next_template("retry_step_by_step")


def get_repair_message(
    text:       str,
    confidence: float,
    state:      str,
    memory:     ConversationMemory,
    intent:     Optional[dict] = None,
) -> str:
    """
    API pública: obtiene un mensaje de reparación conversacional contextual.
    """
    return _repair_engine.generate_repair(text, confidence, state, memory, intent)