"""
core/semantic/catalog.py — Etapa B: anclar el contenido a lo que existe de verdad.

El catálogo semántico se construye LEYENDO la base de datos de NexiService:
categorías de empresa, nombres de empresa, servicios y categorías de servicio.
No hay un diccionario de conceptos escrito a mano, y por eso el sistema entiende
automáticamente cualquier rubro que se registre en el futuro sin tocar código.

Anclar sirve para dos cosas a la vez:

  1. Traducir lo que el usuario dice a los términos con los que la plataforma
     realmente guarda las cosas ("médico" → "Consultorios y Centros Médicos").
  2. Decidir que algo NO existe. Si el contenido de un mensaje no se ancla a
     nada, el sistema lo sabe y puede decirlo, en vez de mandar la frase entera
     a un LIKE de SQL.
"""

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from core.semantic.morphology import (
    normalize,
    phrase_overlap,
    stem,
    stem_compatible,
    stems,
)
from core.semantic.types import ConceptKind, GroundedConcept, Grounding

logger = logging.getLogger("lyra.semantic.catalog")


# ═══════════════════════════════════════════════════════════════════════════════
# § 1. CONCEPTOS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Concept:
    """Algo que existe dentro de NexiService y a lo que se puede apuntar."""
    kind: str
    label: str
    entity_id: Optional[int] = None
    #: Texto adicional que describe el concepto (descripciones, categoría padre).
    aliases: List[str] = field(default_factory=list)
    #: Término con el que las herramientas actuales deben buscarlo.
    search_term: str = ""
    #: Si los alias nombran el concepto en lugar de describirlo.
    #: Un rubro se llama "hospital" tanto como "medico"; en cambio la
    #: descripción de una empresa sólo la acompaña. Los primeros deben pesar
    #: como el nombre; los segundos, mucho menos.
    aliases_are_names: bool = False

    def searchable_text(self) -> str:
        return " ".join([self.label] + self.aliases)


#: Prioridad por tipo cuando dos conceptos empatan en evidencia léxica.
#: Un nombre propio es más específico que una categoría, y una categoría es una
#: respuesta más útil que un servicio suelto cuando el usuario describe una
#: necesidad general.
_KIND_PRIOR = {
    ConceptKind.BUSINESS: 1.00,
    ConceptKind.SERVICE: 0.92,
    ConceptKind.BUSINESS_CATEGORY: 0.90,
    ConceptKind.SERVICE_CATEGORY: 0.86,
}


# ═══════════════════════════════════════════════════════════════════════════════
# § 2. ÍNDICE
# ═══════════════════════════════════════════════════════════════════════════════

class SemanticCatalog:
    """
    Índice invertido raíz → conceptos, con pesos por poder discriminante.

    Palabras que aparecen en casi todo ("Popayán", "Norte", "de") pesan casi
    nada; palabras raras ("odontológico", "criollo") pesan mucho. Es el mismo
    principio del IDF, y evita tener que mantener una lista de palabras a
    ignorar: los datos deciden cuáles son irrelevantes.
    """

    #: Cuánto vale una coincidencia que ocurre en el texto accesorio de un
    #: concepto (su categoría, su descripción) en lugar de en su nombre.
    #: "médico" aparece en la descripción de veinte empresas, pero eso identifica
    #: un rubro, no una empresa concreta; sin este descuento, cualquier término
    #: genérico llevaba directo al perfil de un negocio cualquiera.
    ALIAS_WEIGHT = 0.30

    #: Cuánto pesa, en contra, una palabra que el catálogo no reconoce.
    #: Calibrado para que un sintagma normal con un modificador desconocido
    #: ("atención médica") siga anclándose, mientras que varias palabras ajenas
    #: seguidas sí invaliden la coincidencia.
    UNKNOWN_TERM_WEIGHT = 0.35

    #: Cuántas palabras desconocidas llegan a contar en contra.
    #: Al hablar con naturalidad la gente cuenta cosas: "me siento muy mal de la
    #: cabeza, quisiera una cita para un hospital". Sólo "hospital" está en el
    #: catálogo, y sin este tope el relato ahogaba la única palabra que sí
    #: identificaba el rubro. Pasado el tope, más narración no cambia nada:
    #: significa que la frase es conversacional, no que apunte a otro sitio.
    MAX_UNKNOWN_PENALTY_TERMS = 2

    def __init__(self, concepts: Sequence[Concept]):
        self.concepts: List[Concept] = list(concepts)
        self._index: Dict[str, Dict[int, float]] = {}
        self._weights: Dict[str, float] = {}
        self._build()

    def _build(self) -> None:
        total = max(len(self.concepts), 1)
        for idx, concept in enumerate(self.concepts):
            name_stems = {s for s in stems(concept.label) if s}
            alias_stems = {s for s in stems(" ".join(concept.aliases)) if s}
            alias_weight = 1.0 if concept.aliases_are_names else self.ALIAS_WEIGHT
            for s in name_stems:
                self._index.setdefault(s, {})[idx] = 1.0
            for s in alias_stems - name_stems:
                self._index.setdefault(s, {})[idx] = alias_weight
        for s, owners in self._index.items():
            # Peso logarítmico inverso a la frecuencia documental.
            self._weights[s] = math.log(1.0 + total / len(owners))
        self._max_weight = max(self._weights.values()) if self._weights else 1.0

    # ── consulta ────────────────────────────────────────────────────────────

    def _lookup_stem(self, query_stem: str) -> List[str]:
        """Claves del índice compatibles con una raíz de la consulta."""
        if query_stem in self._index:
            return [query_stem]
        return [key for key in self._index if stem_compatible(query_stem, key)]

    def ground(
        self,
        terms: Sequence[str],
        limit: int = 3,
        min_score: float = 0.42,
    ) -> List[GroundedConcept]:
        """
        Ancla palabras de contenido a los conceptos más plausibles del catálogo.

        Devuelve lista vacía cuando el catálogo no reconoce nada: ése es un
        resultado legítimo y necesario, no un fallo.
        """
        query_stems = [s for s in (stem(t) for t in terms) if s]
        if not query_stems or not self.concepts:
            return []

        scores: Dict[int, float] = {}
        matched_weight: Dict[int, float] = {}
        total_weight = 0.0

        unknown_seen = 0
        for qs in query_stems:
            keys = self._lookup_stem(qs)
            if not keys:
                unknown_seen += 1
                if unknown_seen > self.MAX_UNKNOWN_PENALTY_TERMS:
                    continue
                # Una palabra que el catálogo NO conoce sigue contando en el
                # denominador: si el usuario dijo "ojos" y aquí no existe nada
                # con esa palabra, el anclaje que sólo acertó "revisar" no puede
                # considerarse bueno. Pesa la mitad que un término reconocido,
                # para que una palabra suelta ("atención médica", "el pelo…
                # digo, el cabello") no eche abajo una coincidencia buena, pero
                # varias juntas sí terminen invalidándola.
                total_weight += self._max_weight * self.UNKNOWN_TERM_WEIGHT
                continue
            # El peso del término es el de su mejor clave.
            term_weight = max(self._weights.get(k, 0.0) for k in keys)
            total_weight += term_weight
            for key in keys:
                w = self._weights.get(key, 0.0)
                for idx, field_weight in self._index[key].items():
                    contribution = w * field_weight
                    scores[idx] = scores.get(idx, 0.0) + contribution
                    matched_weight[idx] = max(matched_weight.get(idx, 0.0), contribution)

        if not scores or total_weight <= 0:
            return []

        scored: List[tuple] = []
        recognized_text = " ".join(self.matched_terms(terms)) or " ".join(terms)
        for idx, raw in scores.items():
            concept = self.concepts[idx]
            coverage = raw / total_weight
            # Cuánto del concepto quedó cubierto por lo que el usuario SÍ nombró.
            # Se mide sobre los términos reconocidos, no sobre la frase entera:
            # el relato que acompaña a una petición ("me siento mal de la
            # cabeza…") ya se descuenta una vez en `coverage`, y volver a
            # penalizarlo aquí hundía la única palabra que identificaba el rubro.
            # Los alias cuentan menos que el nombre: reconocer un rubro por uno
            # de sus nombres populares ("hospital") es buena señal, pero menor
            # que acertar la etiqueta con la que está guardado.
            precision = phrase_overlap(recognized_text, concept.label)
            if concept.aliases:
                alias_precision = phrase_overlap(recognized_text, " ".join(concept.aliases))
                if not concept.aliases_are_names:
                    alias_precision *= self.ALIAS_WEIGHT
                precision = max(precision, alias_precision)
            discriminative = matched_weight.get(idx, 0.0) / self._max_weight

            score = (
                0.50 * min(coverage, 1.0)
                + 0.28 * precision
                + 0.22 * discriminative
            ) * _KIND_PRIOR.get(concept.kind, 0.8)

            if score < min_score:
                continue
            scored.append((score, idx, concept))

        if not scored:
            return []

        scored.sort(key=lambda item: item[0], reverse=True)
        ambiguous = self._is_ambiguous(scored)

        return [
            GroundedConcept(
                kind=concept.kind,
                label=concept.label,
                score=round(score, 4),
                entity_id=concept.entity_id,
                search_terms=[concept.search_term or concept.label],
                source="lexical",
                ambiguous=ambiguous and concept.kind == scored[0][2].kind,
            )
            for score, _idx, concept in scored[:limit]
        ]

    @staticmethod
    def _is_ambiguous(scored: List[tuple]) -> bool:
        """
        True si varios conceptos del mismo tipo encajan igual de bien.

        Cuando alguien escribe "óptica" y encajan por igual dos sucursales, la
        coincidencia es correcta pero no identifica una: lo que describe es un
        rubro. Se marca como ambigua en vez de castigar su puntaje, para que el
        motor elija mostrar la lista sin dejar de reconocer el término.
        """
        if len(scored) < 2:
            return False
        top_score, _, top_concept = scored[0]
        tied = sum(
            1 for score, _, concept in scored
            if concept.kind == top_concept.kind and abs(score - top_score) <= 0.02
        )
        return tied >= 2

    def matched_terms(self, terms: Sequence[str]) -> List[str]:
        """Palabras del usuario que el catálogo reconoce como existentes."""
        return [t for t in terms if self._lookup_stem(stem(t))]

    def ground_all(self, terms: Sequence[str], **kwargs) -> Grounding:
        concepts = self.ground(terms, **kwargs)
        return Grounding(
            concepts=concepts,
            content_terms=list(terms),
            matched_terms=self.matched_terms(terms),
            attempted=bool(terms),
        )

    # ── utilidades para la etapa C ──────────────────────────────────────────

    def domain_labels(self) -> List[str]:
        """Etiquetas de dominio reales, para ofrecérselas al resolutor semántico."""
        seen, out = set(), []
        for c in self.concepts:
            if c.kind not in (ConceptKind.BUSINESS_CATEGORY, ConceptKind.SERVICE_CATEGORY):
                continue
            if c.label in seen:
                continue
            seen.add(c.label)
            out.append(c.label)
        return out

    def concept_by_label(self, label: str) -> Optional[Concept]:
        target = normalize(label)
        for c in self.concepts:
            if normalize(c.label) == target:
                return c
        return None

    def __len__(self) -> int:  # pragma: no cover - conveniencia
        return len(self.concepts)


# ═══════════════════════════════════════════════════════════════════════════════
# § 3. CARGA DESDE LA BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════════

_CACHE_TTL_SECONDS = 900  # el catálogo cambia poco; 15 minutos es holgado
_cache_lock = threading.Lock()
_cached_catalog: Optional[SemanticCatalog] = None
_cached_at: float = 0.0


def _fetch_concepts() -> List[Concept]:
    """Lee el catálogo real. Devuelve lista vacía si la base no está disponible."""
    from core.database import get_connection

    concepts: List[Concept] = []
    try:
        with get_connection("vt_inventario") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, nombre FROM categoriaempresa")
                for row in cur.fetchall() or []:
                    name = (row.get("nombre") or "").strip()
                    if name:
                        concepts.append(Concept(
                            kind=ConceptKind.BUSINESS_CATEGORY,
                            label=name,
                            entity_id=row.get("id"),
                            search_term=name,
                        ))

                cur.execute(
                    """
                    SELECT e.id, e.razonSocial, e.acercaDeNosotros,
                           COALESCE(ce.nombre, ce2.nombre) AS categoria
                    FROM empresa e
                    LEFT JOIN categoriaempresa ce ON e.idCategoriaEmpresa = ce.id
                    LEFT JOIN asignacionCompanyCategoria acc ON e.id = acc.idCompany
                    LEFT JOIN categoriaempresa ce2 ON acc.idCategoriaCompany = ce2.id
                    WHERE e.idEstado = 1 AND e.publicado = 1
                    GROUP BY e.id, e.razonSocial, e.acercaDeNosotros, categoria
                    """
                )
                for row in cur.fetchall() or []:
                    name = (row.get("razonSocial") or "").strip()
                    if not name:
                        continue
                    aliases = [a for a in (row.get("categoria"), row.get("acercaDeNosotros")) if a]
                    concepts.append(Concept(
                        kind=ConceptKind.BUSINESS,
                        label=name,
                        entity_id=row.get("id"),
                        aliases=[str(a)[:200] for a in aliases],
                        search_term=name,
                    ))

                cur.execute("SELECT DISTINCT nombre FROM categoriaservicios")
                for row in cur.fetchall() or []:
                    name = (row.get("nombre") or "").strip()
                    if name:
                        concepts.append(Concept(
                            kind=ConceptKind.SERVICE_CATEGORY,
                            label=name,
                            search_term=name,
                        ))

                cur.execute(
                    "SELECT DISTINCT nombre, descripcion FROM servicios WHERE idEstado = 1"
                )
                for row in cur.fetchall() or []:
                    name = (row.get("nombre") or "").strip()
                    if not name:
                        continue
                    desc = (row.get("descripcion") or "").strip()
                    concepts.append(Concept(
                        kind=ConceptKind.SERVICE,
                        label=name,
                        aliases=[desc[:200]] if desc else [],
                        search_term=name,
                    ))
    except Exception as exc:
        logger.warning("Catálogo semántico no disponible (%s). Se opera sin anclaje léxico.", exc)
        return []

    return concepts


def _legacy_category_concepts() -> List[Concept]:
    """
    Rubros que el sistema ya sabía nombrar, expuestos a la comprensión.

    La base de datos guarda "Consultorios y Centros Médicos", pero la gente dice
    "hospital", "doctor" o "clínica", y ninguna de esas palabras aparece en el
    catálogo. Ese puente ya existía en `tools.shared.utils.CATEGORY_KEYWORDS`,
    donde el router lo usaba desde siempre; aquí se reutiliza tal cual en lugar
    de dejar que sólo el resolutor semántico pueda cruzarlo.

    No es una lista nueva de sinónimos: es la que el proyecto ya mantiene, ahora
    disponible también para la capa que comprende. Lo que no esté aquí sigue
    resolviéndose por significado en la etapa C.
    """
    from tools.shared.utils import CATEGORY_KEYWORDS

    return [
        Concept(
            kind=ConceptKind.BUSINESS_CATEGORY,
            label=category,
            aliases=list(keywords),
            search_term=category,
            aliases_are_names=True,
        )
        for category, keywords in CATEGORY_KEYWORDS.items()
    ]


def get_catalog(force_reload: bool = False) -> SemanticCatalog:
    """Catálogo semántico vigente, cacheado en memoria."""
    global _cached_catalog, _cached_at
    with _cache_lock:
        fresh = _cached_catalog is not None and (time.time() - _cached_at) < _CACHE_TTL_SECONDS
        if fresh and not force_reload:
            return _cached_catalog
        concepts = _fetch_concepts() + _legacy_category_concepts()
        _cached_catalog = SemanticCatalog(concepts)
        _cached_at = time.time()
        logger.info("Catálogo semántico cargado: %d conceptos", len(concepts))
        return _cached_catalog


def set_catalog(catalog: SemanticCatalog) -> None:
    """Inyecta un catálogo (pruebas, o precarga desde otro proceso)."""
    global _cached_catalog, _cached_at
    with _cache_lock:
        _cached_catalog = catalog
        _cached_at = time.time()


def reset_catalog() -> None:
    """Invalida la caché para que se relea de la base."""
    global _cached_catalog, _cached_at
    with _cache_lock:
        _cached_catalog = None
        _cached_at = 0.0
