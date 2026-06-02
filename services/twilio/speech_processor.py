# services/twilio/speech_processor.py
import re
import json
import asyncio
import logging
from typing import Optional, Tuple, Literal

from tools.shared.utils import normalize_text  # ← una sola vez

logger = logging.getLogger("lyra.twilio.speech")


# ── Constantes de clase ───────────────────────────────────────────────────────

_FILLER_WORDS: frozenset[str] = frozenset({
    "eh", "ehh", "ehhh", "um", "umm", "ummm", "mm", "mmm",
    "este", "pues", "a ver", "como", "bueno", "es que",
    "digamos", "osea", "o sea", "verdad", "cierto",
    "mire", "vea", "sabe", "sabe qué",
})

_PREAMBLE_PATTERNS: tuple[str, ...] = (
    r'^(?:hola|buen[oa]s?\s*(?:tardes?|noches?|d[ií]as?)?|ey|oye|oiga|mira|ve)\s*[,.]?\s*',
    r'^(?:amig[oa]|mij[oa]|herman[oa]|parce|parcero|vecin[oa]|señor[ai]?|joven|jefe|pana)\s*[,.]?\s*',
    r'(?:me\s+encuentro|estoy|quedo|voy|ando)\s+(?:aqu[ií]\s+)?(?:en|por|cerca\s+de?)\s+',
    r'(?:necesito|mand[ae]me?|env[ií][ae]me?|quiero)\s+(?:un\s+)?(?:taxi|carro|servicio|veh[ií]culo)\s+(?:a|en|para|por|hacia)\s+',
    r'(?:rec[oó]j[ae]me|ll[eé]v[ae]me|ven|venga)\s+(?:a|en|por|para)\s+',
)

_STREET_PATTERNS: tuple[str, ...] = (
    r'(?:calle|cl|cll)\s*(\d+)\s*(?:con|y|#)\s*(?:carrera|cra|cr|kra|kr)\s*(\d+)',
    r'(?:carrera|cra|cr|kra|kr)\s*(\d+)\s*(?:con|y|#)\s*(?:calle|cl|cll)\s*(\d+)',
    r'(?:la)\s*(\d+)\s*(?:con)\s*(?:la)?\s*(\d+)',
    r'(?:calle|cl|cll|carrera|cra|cr|kra|kr)\s*\d+[a-z]?\s*(?:#|num|numero|número)\s*\d+[a-z]?\s*[-–]\s*\d+',
    r'(?:calle|cl|cll)\s*(\d+)',
    r'(?:carrera|cra|cr|kra|kr)\s*(\d+)',
)

_NO_PATTERNS: tuple[str, ...] = (
    r"^no$", r"\bno gracias\b", r"\bnop\b", r"\bmejor no\b",
    r"\bno quiero\b", r"\bal conductor\b", r"\bno deseo\b", r"\bnegativo\b",
    r"\bprefiero no\b", r"\bno por ahora\b",
)

_SI_PATTERNS: tuple[str, ...] = (
    r"^s[ií]$", r"^si$", r"\bclaro\b", r"\bpor supuesto\b",
    r"\bdale\b", r"\bok\b", r"\bvale\b", r"\blisto\b",
    r"\bafirmativo\b", r"\bquiero indicar\b", r"\bs[ií] quiero\b", r"\bsi quiero\b",
    r"\bdesde luego\b", r"\bclaro que s[ií]\b", r"\bs[ií] s[ií]\b",
    r"\bsi si\b", r"\bbueno\b", r"\bcorrecto\b",
)

_CORRECTION_PATTERNS: tuple[str, ...] = (
    r'(?:est[aá]|eso est[aá]|esa)\s*(?:no es|mal|no)',
    r'(?:mal|mala)\s*(?:la\s*)?(?:ubicaci[oó]n|direcci[oó]n)',
    r'(?:no es|no era)\s*(?:ah[ií]|esa?|eso)',
    r'(?:cambi[aá]r?|corregir|cambiar?)\s*(?:la\s*)?(?:ubicaci[oó]n|direcci[oó]n|el origen)',
    r'(?:me equivoqu[eé]|equivocad[oa])',
    r'(?:quer[ií]a|quiero)\s*(?:cambiar|corregir)',
    r'(?:otra|diferente)\s*(?:ubicaci[oó]n|direcci[oó]n)',
    r'(?:est[aá]s?\s+mal)',
    r'(?:no,?\s*(?:esa|eso) no)',
)

_REPEAT_PATTERNS: tuple[str, ...] = (
    r"\brepite\b", r"\brepetir\b", r"\brepiteme\b", r"\brepíteme\b",
    r"\bme puede repetir\b", r"\bme puedes repetir\b",
    r"\bme repite\b", r"\bme repites\b",
    r"\bque dijiste\b", r"\bqué dijiste\b", r"\bque dijo\b", r"\bqué dijo\b",
    r"\bno te escuche\b", r"\bno te escuché\b", r"\bno escuche\b",
    r"\bno entendi\b", r"\bno entendí\b", r"\bno le entendi\b",
    r"\bcomo asi\b", r"\bcómo así\b",
    r"\bperdon\b.*\bescuche\b", r"\bperdón\b.*\bescuché\b",
    r"\bme puede recordar\b", r"\bme puedes recordar\b",
    r"\bque me dijo\b", r"\bqué me dijo\b",
    r"\bque me dijiste\b", r"\bqué me dijiste\b",
    r"\botra vez\b", r"\bde nuevo\b",
    r"\bno oi\b", r"\bno oí\b",
)

# Prompt templates por tipo de extracción
_ADDRESS_PROMPTS: dict[str, str] = {
    "pickup": (
        "Eres un asistente para taxi en Popayán, Cauca, Colombia.\n"
        "El usuario habla por teléfono. Extrae SOLO el punto de RECOGIDA (origen).\n"
        "Si dice 'de X a Y', 'desde X hasta Y' o 'de X hacia Y', el origen es X (no Y).\n"
        "Ignora saludos y palabras de relleno.\n"
        "Prioriza: cruce (calle 5 con carrera 9), nomenclatura (carrera 6 # 12-34), "
        "una sola vía (calle 15). Abreviaturas: cl, cra, kr, k.\n"
        "Barrios y lugares conocidos solo si no hay calle/carrera clara.\n"
        'Responde SOLO JSON: {{"origen": "texto normalizado o null", "nota": "breve"}}\n'
        "Texto del usuario:\n{text}"
    ),
    "destination": (
        "Eres un asistente para taxi en Popayán, Cauca, Colombia.\n"
        "El usuario habla por teléfono. Extrae SOLO el DESTINO del viaje.\n"
        "Ignora saludos y palabras de relleno.\n"
        "Prioriza: cruce (calle 5 con carrera 9), nomenclatura (carrera 6 # 12-34), "
        "una sola vía (calle 15). Abreviaturas: cl, cra, kr, k.\n"
        "Barrios y lugares conocidos solo si no hay calle/carrera clara.\n"
        'Responde SOLO JSON: {{"destino": "texto normalizado o null", "nota": "breve"}}\n'
        "Texto del usuario:\n{text}"
    ),
}

_ADDRESS_FIELDS:   dict[str, str] = {"pickup": "origen",    "destination": "destino"}
_ADDRESS_FALLBACK: dict[str, str] = {
    "pickup":      "No alcanzamos a entender el punto de recogida. ¿Nos lo repites?",
    "destination": "¿Cuál es tu destino? Puedes decir calle, carrera, barrio o un lugar conocido.",
}


class SpeechProcessor:
    """
    Procesamiento de voz para el flujo de taxi en Popayán.
    Responsabilidades: limpieza de texto, corrección fonética,
    match local de lugares, y extracción de direcciones vía LLM.
    """

    def __init__(
        self,
        llm_client,
        model:       str,
        corrections: dict,
        places:      dict,
    ):
        self.llm         = llm_client
        self.model       = model
        self.corrections = corrections
        self.places      = places
        self.alias_index = self._build_alias_index(places)

    # ── Inicialización ────────────────────────────────────────────────────────

    @staticmethod
    def _build_alias_index(places: dict) -> list[tuple[str, str]]:
        """
        Construye un índice (alias_normalizado, canónico) ordenado de mayor a menor longitud,
        para que matches más específicos tengan prioridad.
        """
        pairs = [
            (normalize_text(alias), canonical)
            for canonical, aliases in places.items()
            for alias in aliases
            if normalize_text(alias)
        ]
        return sorted(pairs, key=lambda x: len(x[0]), reverse=True)

    # ── Limpieza de texto ─────────────────────────────────────────────────────

    def clean_text(self, text: str) -> str:
        """Elimina artefactos comunes del STT de Twilio."""
        if not text:
            return text

        t = text.strip()
        t = re.sub(r'^[.?!,;:\s]+', '', t)
        t = re.sub(r'[.?!]+$', '', t)

        for filler in sorted(_FILLER_WORDS, key=len, reverse=True):
            t = re.sub(
                r'^' + re.escape(filler) + r'[,.]?\s*',
                '', t, flags=re.IGNORECASE,
            ).strip()

        t = re.sub(r'\b(\w+)\s+\1\b', r'\1', t, flags=re.IGNORECASE)
        t = re.sub(r'\s+(?:por favor|gracias|dale|listo|ya|pues|sí)\s*$', '', t, flags=re.IGNORECASE)
        t = re.sub(r'\s+', ' ', t).strip()

        return t if len(t) >= 2 else text.strip()

    def strip_preamble(self, text: str) -> str:
        """Elimina saludos y preámbulos conversacionales."""
        if not text:
            return text

        t = text.strip()
        for _ in range(3):
            changed = False
            for pat in _PREAMBLE_PATTERNS:
                new_t = re.sub(pat, '', t, count=1, flags=re.IGNORECASE).strip()
                if new_t != t and len(new_t) >= 3:
                    t, changed = new_t, True
            t = re.sub(r'^[,.;:\s]+', '', t).strip()
            if not changed:
                break

        return t if len(t) >= 3 else text.strip()

    # ── Corrección fonética ───────────────────────────────────────────────────

    def _phonetic_key(self, text: str) -> str:
        """Normalización fonética simple para español colombiano."""
        t = normalize_text(text)
        t = t.replace('v', 'b')
        t = re.sub(r'c(?=[ei])', 's', t)
        t = t.replace('z', 's').replace('ll', 'y').replace('h', '')
        t = re.sub(r'g(?=[ei])', 'j', t)
        t = t.replace('qu', 'k').replace('q', 'k')
        t = re.sub(r'(.)\1+', r'\1', t)
        return t.replace(' ', '')

    def correct_speech(self, text: str) -> str:
        """
        Aplica correcciones STT para topónimos de Popayán.
        Estrategias en orden de prioridad:
          1. Match exacto en diccionario
          2. Match parcial en diccionario
          3. Fuzzy bigramático + fonético
          4. Match de sub-frases
        """
        if not text:
            return text

        t       = self.clean_text(text)
        t_lower = t.lower().strip()

        result = (
            self._correct_exact(t_lower)
            or self._correct_partial(t_lower)
            or self._correct_fuzzy(t_lower)
            or self._correct_subphrase(t_lower)
        )
        return result or t

    def _correct_exact(self, t_lower: str) -> Optional[str]:
        return self.corrections.get(t_lower)

    def _correct_partial(self, t_lower: str) -> Optional[str]:
        for wrong, right in self.corrections.items():
            if wrong in t_lower and wrong != right and right.lower() not in t_lower:
                return t_lower.replace(wrong, right)
        return None

    def _correct_fuzzy(self, t_lower: str, threshold: float = 0.50) -> Optional[str]:
        t_norm     = normalize_text(t_lower)
        t_phonetic = self._phonetic_key(t_lower)

        if len(t_norm) < 4:
            return None

        t_bigrams  = set(t_norm[i:i+2]     for i in range(len(t_norm) - 1))
        tp_bigrams = set(t_phonetic[i:i+2] for i in range(len(t_phonetic) - 1))

        best_match, best_score = None, 0.0

        for canonical, aliases in self.places.items():
            for alias in aliases:
                a_norm = normalize_text(alias)
                if len(a_norm) < 4:
                    continue

                a_bigrams = set(a_norm[i:i+2] for i in range(len(a_norm) - 1))
                if not t_bigrams or not a_bigrams:
                    continue

                overlap      = len(t_bigrams & a_bigrams)
                total        = len(t_bigrams | a_bigrams)
                bigram_score = overlap / total if total else 0

                len_ratio = min(len(t_norm), len(a_norm)) / max(len(t_norm), len(a_norm))

                a_phonetic  = self._phonetic_key(alias)
                ap_bigrams  = set(a_phonetic[i:i+2] for i in range(len(a_phonetic) - 1))
                p_overlap   = len(tp_bigrams & ap_bigrams) if tp_bigrams and ap_bigrams else 0
                p_total     = len(tp_bigrams | ap_bigrams) if tp_bigrams and ap_bigrams else 0
                phonetic_score = p_overlap / p_total if p_total else 0

                score = bigram_score * 0.35 + phonetic_score * 0.40 + len_ratio * 0.25

                if score > best_score and score > threshold:
                    best_score, best_match = score, canonical

        return best_match

    def _correct_subphrase(self, t_lower: str) -> Optional[str]:
        words = t_lower.split()
        if len(words) < 2:
            return None
        for window in range(len(words), 1, -1):
            for start in range(len(words) - window + 1):
                sub = ' '.join(words[start:start + window])
                if sub in self.corrections:
                    return self.corrections[sub]
        return None

    # ── Match local ───────────────────────────────────────────────────────────

    def try_local_match(self, user_text: str) -> Optional[str]:
        """Hace match del texto contra lugares conocidos de Popayán."""
        from services.twilio.navigation import NavigationParser
        
        # Extrae modificadores relativos y aísla el landmark real
        nav_data = NavigationParser.extract_relative_context(user_text)
        base_landmark = nav_data.get("landmark") or user_text
        modifier = nav_data.get("modifier")
        
        norm = normalize_text(base_landmark)
        if len(norm) < 3:
            return None

        matched_canonical = None

        # Alias index (más rápido, sin I/O)
        for alias_norm, canonical in self.alias_index:
            if alias_norm in norm:
                matched_canonical = canonical
                break

        if not matched_canonical:
            # Geocodificación local
            geo_result = self._try_geocode_local(base_landmark)
            if geo_result:
                matched_canonical = geo_result

        if not matched_canonical:
            # Calles / carreras por patrón
            if any(re.search(pat, norm) for pat in _STREET_PATTERNS):
                matched_canonical = base_landmark.strip()

        # Reconstruir con el modificador si se encontró algo
        if matched_canonical:
            if modifier:
                return f"{modifier} {matched_canonical}"
            return matched_canonical

        return None

    def _try_geocode_local(self, user_text: str) -> Optional[str]:
        try:
            from tools.popayan_geodata import geocode_local, ALL_BARRIOS, LANDMARKS, CORREGIMIENTOS
            geo = geocode_local(user_text)
            if not geo:
                return None
            display   = geo[2]
            name_part = display.split(", Popay")[0].strip() if ", Popay" in display else display
            name_norm = normalize_text(name_part)
            known     = list(ALL_BARRIOS) + list(LANDMARKS) + list(CORREGIMIENTOS)
            if any(normalize_text(n) == name_norm for n in known):
                return name_part
            return user_text.strip()
        except Exception:
            return None

    # ── Intenciones ───────────────────────────────────────────────────────────

    def is_correction_request(self, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        return any(re.search(pat, t) for pat in _CORRECTION_PATTERNS)

    def is_repeat_request(self, text: str) -> bool:
        t = normalize_text(text)
        if len(t) < 3:
            return False
        return any(re.search(pat, t) for pat in _REPEAT_PATTERNS)

    def parse_si_no(self, text: str) -> Optional[bool]:
        t = re.sub(r'[^\w\s]', '', (text or "").lower().strip())
        if not t:
            return None
        if any(re.search(p, t) for p in _NO_PATTERNS):
            return False
        if any(re.search(p, t) for p in _SI_PATTERNS):
            return True
        if re.search(r'\bs[ií]\b', t):
            return True
        if re.search(r'\bno\b', t):
            return False
        return None

    # ── Extracción de dirección vía LLM ──────────────────────────────────────

    async def extract_address(
        self,
        text:         str,
        address_type: Literal["pickup", "destination"],
    ) -> Tuple[Optional[str], str]:
        """
        Extrae una dirección del texto del usuario.
        Intenta match local primero; si falla, consulta al LLM.
        El LLM se llama en un executor para no bloquear el event loop
        (clientes síncronos como openai v0 no son async-nativos).
        """
        for candidate in (self.strip_preamble(text), text):
            local = self.try_local_match(candidate)
            if local:
                return local, ""

        if not self.llm:
            return None, _ADDRESS_FALLBACK[address_type]

        return await self._llm_extract(text, address_type)

    async def _llm_extract(
        self,
        text:         str,
        address_type: Literal["pickup", "destination"],
    ) -> Tuple[Optional[str], str]:
        prompt     = _ADDRESS_PROMPTS[address_type].format(text=text)
        field_name = _ADDRESS_FIELDS[address_type]

        def _sync_call() -> dict:
            result = self.llm.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                timeout=8.0,
            )
            content = result.choices[0].message.content or "{}"
            return json.loads(content)

        try:
            # Ejecutar llamada síncrona sin bloquear el event loop
            data = await asyncio.get_event_loop().run_in_executor(None, _sync_call)
            addr = data.get(field_name)

            if not addr or str(addr).strip().lower() in ("null", "none", ""):
                return None, _ADDRESS_FALLBACK[address_type]

            return str(addr).strip(), ""

        except Exception as exc:
            logger.error(f"LLM extract [{address_type}] error: {exc}")
            return None, "Hubo un problema técnico. ¿Puedes repetir?"