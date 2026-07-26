"""Phrase Manager — banco de formulaciones y selección sin repetición.

Cada intención conversacional tiene varias formulaciones, y cada formulación
se guarda ya dividida en etapas (`parts`): así una respuesta importante nunca
sale como un bloque monolítico, sino en ideas separadas por pausas.

`form` identifica la construcción sintáctica de la variante. Dos variantes con
la misma forma dicen lo mismo de la misma manera aunque cambien las palabras,
así que no se usan seguidas dentro de una llamada.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Sequence

from services.voice.conversation.memory import ConversationMemory


@dataclass(frozen=True)
class Phrase:
    form: str
    parts: tuple[str, ...]

    @property
    def key(self) -> str:
        return "|".join(self.parts)

    def fill(self, **slots: object) -> tuple[str, ...]:
        out = []
        for part in self.parts:
            try:
                out.append(part.format(**slots).strip())
            except (KeyError, IndexError):
                # Slot ausente: la variante no aplica; el llamador ya filtró,
                # pero nunca se rompe una llamada por una plantilla.
                return ()
        return tuple(p for p in out if p)


def _bank(*entries: tuple[str, Sequence[str]]) -> tuple[Phrase, ...]:
    return tuple(Phrase(form=form, parts=tuple(parts)) for form, parts in entries)


# ── Banco de expresiones (es-CO, trato de "tú", registro de operadora) ────────

PHRASE_BANK: dict[str, tuple[Phrase, ...]] = {
    # Apertura de llamada.
    "greeting": _bank(
        ("plain", ("Hola, buenas.", "Soy Lyra, tu asistente de Tax Belalcázar.",
                   "Cuéntame, ¿en dónde te recogemos hoy?")),
        ("warm", ("Hola, muy buenas.", "Te habla Lyra, de Tax Belalcázar.",
                  "Dime, ¿en dónde te recogemos?")),
        ("direct", ("Buenas.", "Soy Lyra, la asistente de Tax Belalcázar.",
                    "¿En dónde te recogemos hoy?")),
        ("soft", ("Hola.", "Soy Lyra, de Tax Belalcázar.",
                  "Cuéntame, ¿dónde te recogemos?")),
    ),
    # Acuse breve de escucha, al retomar la palabra.
    "ack": _bank(
        ("mm", ("Mmm.",)),
        ("aja", ("Ajá.",)),
        ("listo", ("Listo.",)),
        ("ya", ("Ya.",)),
        ("claro", ("Claro.",)),
        ("bien", ("Bien.",)),
        ("ah", ("Ah, ya.",)),
        ("perfecto", ("Perfecto.",)),
        ("ok", ("Ok.",)),
        ("bueno", ("Bueno.",)),
    ),
    # Transición hacia una consulta que sí se va a ejecutar.
    "transition": _bank(
        ("check", ("Déjame revisar.",)),
        ("second", ("Un segundo que reviso.",)),
        ("look", ("Voy a mirar.",)),
        ("allow", ("Permíteme un momento.",)),
        ("here", ("Déjame ver acá.",)),
        ("tell", ("Ya te digo.",)),
    ),
    # Resultado encontrado (solo tras trabajo real).
    "found": _bank(
        ("listo", ("Listo.",)),
        ("found", ("Ya la encontré.",)),
        ("here", ("Aquí está.",)),
        ("appeared", ("Ya me apareció.",)),
        ("ready", ("Listo, ya está.",)),
    ),
    # Narración de trabajo en curso, por proceso real.
    "narrate_address": _bank(
        ("validate", ("Estoy validando esa dirección.",)),
        ("confirm", ("Déjame confirmar esa dirección.",)),
        ("verify", ("Estoy verificando la ubicación.",)),
        ("system", ("La estoy buscando en el sistema.",)),
    ),
    "narrate_place": _bank(
        ("check", ("Estoy verificando ese lugar.",)),
        ("review", ("Estoy revisando lo que me indicaste.",)),
        ("locate", ("Déjame ubicar ese punto.",)),
        ("system", ("Lo estoy buscando en el sistema.",)),
    ),
    "narrate_geo": _bank(
        ("validate", ("Permíteme validar esa referencia.",)),
        ("review", ("Estoy revisando el sector.",)),
        ("verify", ("Estoy verificando esa ubicación.",)),
        ("cross", ("Déjame cruzar esa referencia.",)),
    ),
    "narrate_service": _bank(
        ("register", ("Te estoy registrando el servicio.",)),
        ("create", ("Estoy creando la solicitud.",)),
        ("send", ("Ya lo estoy pasando a despacho.",)),
    ),
    "narrate_generic": _bank(
        ("moment", ("Un momento por favor.",)),
        ("second", ("Dame un segundo.",)),
        ("allow", ("Permíteme un momento.",)),
    ),
    # La operación se alargó: mantener viva la conversación, sin repetir.
    "wait_more": _bank(
        ("still", ("Sigo en eso, un momentico.",)),
        ("almost", ("Ya casi, dame un segundo.",)),
        ("checking", ("Todavía estoy revisando.",)),
        ("patience", ("Un momentico más, por favor.",)),
        ("holding", ("Aquí sigo contigo, ya te confirmo.",)),
    ),
    # Confirmación de origen CON barrio.
    "confirm_pickup_barrio": _bank(
        ("appears", ("Me aparece {place}.", "Barrio {barrio}.",
                     "¿Es ahí donde estás?")),
        ("point", ("El punto de recogida sería {place}.",
                   "En el barrio {barrio}.", "¿Correcto?")),
        ("then", ("Entonces {place}, barrio {barrio}.",
                  "¿Te recogemos ahí?")),
        ("have", ("Tengo {place}, barrio {barrio}.", "¿Está bien así?")),
        ("plain", ("{place}, barrio {barrio}.", "¿Es correcto?")),
    ),
    # Confirmación de origen SIN barrio.
    "confirm_pickup": _bank(
        ("appears", ("Me aparece {place}.", "¿Es ahí donde te recogemos?")),
        ("then", ("Entonces {place}.", "¿Te recogemos ahí?")),
        ("have", ("Tengo {place}.", "¿Está bien así?")),
        ("plain", ("{place}.", "¿Es correcto?")),
        ("soft", ("Sería {place}, ¿cierto?",)),
    ),
    # El usuario corrigió: se acepta el cambio y se vuelve a confirmar.
    "confirm_correction": _bank(
        ("ah", ("Ah, {place}.", "¿Te recogemos ahí?")),
        ("ok", ("Listo, {place} entonces.", "¿Confirmas?")),
        ("change", ("Cambio a {place}.", "¿Así está bien?")),
        ("then", ("Entonces sería {place}.", "¿Correcto?")),
    ),
    # Pedir el punto de recogida.
    "ask_pickup": _bank(
        ("where", ("¿En dónde te recogemos?",)),
        ("tell", ("Cuéntame, ¿dónde estás?",)),
        ("addr", ("Dime el barrio o la dirección donde te recogemos.",)),
        ("point", ("¿Cuál es el punto de recogida?",)),
    ),
    # Entrega al conductor con barrio (el punto exacto lo afina él).
    "handoff": _bank(
        ("locate", ("Listo, te ubico en el barrio {barrio}.",
                    "El conductor te llama para afinar el punto exacto.",
                    "Un momento por favor.")),
        ("ok", ("Perfecto, queda en el barrio {barrio}.",
                "El conductor te confirma el punto exacto cuando vaya llegando.",
                "Dame un momento.")),
        ("note", ("Anotado, barrio {barrio}.",
                  "El conductor te llama para ubicarte bien.",
                  "Permíteme un momento.")),
    ),
    # Se acepta la confirmación y se crea el servicio.
    "ack_create": _bank(
        ("moment", ("Listo.", "Dame un momento que te lo registro.")),
        ("ok", ("Perfecto.", "Un momento por favor.")),
        ("go", ("Vale.", "Ya mismo te lo dejo pedido.")),
        ("done", ("Hecho.", "Permíteme un segundo.")),
    ),
    # Lead-in de cierre (el resultado real llega como payload del backend).
    "closing_lead": _bank(
        ("ready", ("Listo.",)),
        ("done", ("Ya quedó.",)),
        ("perfect", ("Perfecto.",)),
    ),
}


class PhraseManager:
    """Elige una formulación evitando lo dicho recientemente.

    Responsabilidad única: escoger texto. No decide si se dice ni cuándo.
    """

    def __init__(
        self,
        memory: ConversationMemory,
        rng: Optional[random.Random] = None,
        bank: Optional[dict[str, tuple[Phrase, ...]]] = None,
    ):
        self._memory = memory
        self._rng = rng or random.Random()
        self._bank = bank if bank is not None else PHRASE_BANK

    def categories(self) -> tuple[str, ...]:
        return tuple(self._bank)

    def variants(self, category: str) -> tuple[Phrase, ...]:
        return self._bank.get(category, ())

    def pick(self, category: str, **slots: object) -> tuple[str, ...]:
        """Etapas de habla para `category`, o () si la categoría no aplica.

        Filtra en dos niveles: primero las formulaciones recientes, luego las
        construcciones sintácticas recientes. Si el filtro deja el banco vacío
        (categorías pequeñas), se relaja de forma escalonada — nunca se queda
        sin decir nada, pero tampoco repite si tiene alternativa.
        """
        options = [p for p in self._bank.get(category, ()) if p.fill(**slots)]
        if not options:
            return ()

        fresh = [p for p in options if not self._memory.is_recent(category, p.key)]
        pool = fresh or options
        if len(pool) > 1:
            varied = [
                p for p in pool if not self._memory.is_recent_form(category, p.form)
            ]
            pool = varied or pool

        chosen = self._rng.choice(pool)
        self._memory.remember_phrase(category, chosen.key, chosen.form)
        return chosen.fill(**slots)
