"""
tests/test_stt_repair.py — Cobertura de la capa STT anterior al resolver:
reparación fonética de transcripción y generación de hints model-aware.

No prueba la precisión real de Deepgram (requiere llamadas en vivo); prueba que
el repair no corrompe texto y corrige misspellings claros, y que el generador de
hints produce el formato correcto por modelo.
"""

import pytest

from core.stt_enhancer import (
    repair_location_transcription as repair,
    correct_stt_errors,
    strip_conversational_prefix,
    _collapse_adjacent_duplicate_phrases,
)
from core.streaming_pipeline import _get_contextual_hints, _build_hint_vocab


# ── Limpieza de intención de dirección (strip_conversational_prefix) ───────────

def test_strip_greeting_and_proper_noun():
    # Saludo + nombre propio suelto al inicio → solo la dirección reconocible.
    assert strip_conversational_prefix(
        "buenas tardes osvaldo valle del ortigal"
    ) == "valle del ortigal"


def test_strip_greeting_keeps_full_address():
    # Saludo fuera; el resto (incl. nombre de quien recibe) es la dirección → intacto.
    assert strip_conversational_prefix(
        "hola, calle cuarta número 26 camilo torres"
    ) == "calle cuarta número 26 camilo torres"


def test_strip_no_greeting_intact():
    # Ya parece lugar, sin saludo → devuelve intacto.
    assert strip_conversational_prefix("valle del ortigal") == "valle del ortigal"


def test_strip_greeting_only_returns_original():
    # Saludo sin dirección después → nunca degradar, devuelve original.
    assert strip_conversational_prefix("buenos días") == "buenos días"


def test_strip_alo_prefix_street():
    assert strip_conversational_prefix("aló cra 5 número 12") == "cra 5 número 12"


def test_strip_empty_and_whitespace_safe():
    assert strip_conversational_prefix("") == ""
    assert strip_conversational_prefix("   ") == "   "


def test_strip_does_not_degrade_to_nonsense():
    # Saludo + palabra suelta sin sentido → no hay dirección → original intacto.
    assert strip_conversational_prefix("hola gracias") == "hola gracias"


# ── Corrección fonética: reemplazo limpio sin duplicar/corromper vecinos ────────

def test_correct_hortigal_no_word_duplication():
    # Caso 1 real: "Valle del Hortigal" NO debe duplicar "valle del".
    out = correct_stt_errors("Valle del Hortigal").lower()
    assert out == "valle del ortigal"
    assert out.count("valle del") == 1
    assert "hvalle" not in out


def test_correct_popayan_hortigal_clean():
    # Caso 2 real: "Popayán, Hortigal" → corrige solo "Hortigal"; "popayán" es
    # texto legítimo de Whisper y el corrector NO lo toca ni lo duplica.
    out = correct_stt_errors("Popayán, Hortigal").lower()
    assert out == "popayán, valle del ortigal"
    assert out.count("valle del ortigal") == 1
    assert out.count("popayán") == 1
    assert "hvalle" not in out


def test_correct_standalone_hortigal():
    assert correct_stt_errors("Hortigal").lower() == "valle del ortigal"
    assert correct_stt_errors("ortigal").lower() == "valle del ortigal"


def test_correct_word_boundary_no_inner_match():
    # "ospital" ⊂ "hospital": el límite de palabra impide corromper "hospital"
    # a "hhospital"; pero "ospital" suelto sí se corrige.
    assert correct_stt_errors("hospital") == "hospital"
    assert correct_stt_errors("el ospital").lower() == "el hospital"


def test_correct_full_chain_no_corruption():
    # Cadena de producción: correct_stt_errors → repair (preprocess_stt).
    for raw in ("Valle del Hortigal", "valle del hortigal", "Hortigal"):
        full = repair(correct_stt_errors(raw)).lower()
        assert full == "valle del ortigal", f"{raw!r} → {full!r}"
        assert "hvalle" not in full
        assert full.count("valle del") == 1


def test_correct_leaves_normal_speech_unchanged():
    for t in ("no pueden venir", "quiero un taxi para mi casa",
              "villa del carmen", "el exito"):
        assert correct_stt_errors(t) == t


def test_collapse_adjacent_duplicate_phrases():
    assert _collapse_adjacent_duplicate_phrases(
        "valle del valle del ortigal") == "valle del ortigal"
    assert _collapse_adjacent_duplicate_phrases("popayán popayán") == "popayán"
    # No colapsa cuando no hay duplicado adyacente.
    assert _collapse_adjacent_duplicate_phrases(
        "calle 4 carrera 5") == "calle 4 carrera 5"


# ── Reparación fonética ─────────────────────────────────────────────────────────

def test_repair_fixes_clear_misspelling():
    # "pubensa" → grafía correcta del alias "pubenza" (resolver → Pubenza)
    assert "pubenza" in repair("recogeme en pubensa").lower()
    # "belalcasar" → "belalcazar"
    assert "belalcazar" in repair("belalcasar").lower()


def test_repair_leaves_correct_text_unchanged():
    for t in ["villa del carmen", "el exito", "voy para el carmen"]:
        assert repair(t) == t


def test_repair_does_not_corrupt_normal_speech():
    for t in ["no pueden venir", "quiero un taxi para mi casa", "dime el siento"]:
        assert repair(t) == t


def test_repair_respects_ambiguous_collision():
    # "valle del viento" NO debe snap a "villa ..." (colisión valle/villa → se deja)
    out = repair("valle del viento")
    assert "villa" not in out.lower()


def test_repair_no_word_duplication():
    # Regresión: un token suelto no debe expandirse y duplicar palabras vecinas.
    out = repair("estoy en villa del carmen")
    assert out.lower().count("carmen") == 1
    assert "villa del villa" not in out.lower()


def test_repair_empty_and_short():
    assert repair("") == ""
    assert repair("ok") == "ok"


# ── Generador de hints model-aware ──────────────────────────────────────────────

def test_deepgram_hints_are_single_tokens():
    dg = _build_hint_vocab("deepgram_nova-2")
    entries = dg.split(",")
    assert len(entries) <= 100
    assert all(" " not in e for e in entries), "Deepgram keywords deben ser palabras sueltas"


def test_deepgram_hints_include_key_places():
    dg = _build_hint_vocab("deepgram_nova-2").lower()
    for kw in ["pubenza", "yanaconas", "ortigal", "campanario", "xito", "viento"]:
        assert kw in dg, f"falta keyword {kw!r}"


def test_deepgram_hints_drop_generic_words():
    entries = [e.lower() for e in _build_hint_vocab("deepgram_nova-2").split(",")]
    for generic in ["del", "villa", "centro", "norte"]:
        assert generic not in entries


def test_googlev2_hints_are_phrases():
    ph = _build_hint_vocab("googlev2")
    entries = ph.split(",")
    assert len(entries) <= 200
    assert any(" " in e for e in entries), "googlev2 debe emitir frases"
    assert "Valle del Ortigal" in entries


def test_confirm_state_hints_are_focused():
    h = _get_contextual_hints("confirming_origin", "Campanario", model="deepgram_nova-2")
    assert "sí" in h and "no" in h and "Campanario" in h


def test_dest_state_appends_no():
    h = _get_contextual_hints("waiting_dest_or_skip", model="deepgram_nova-2")
    assert h.split(",")[-1] == "no"


# ── Guard anti-default-city en el extractor LLM ─────────────────────────────────

def _stub_openai(content):
    class _M:
        def __init__(self, c): self.content = c
    class _Choice:
        def __init__(self, c): self.message = _M(c)
    class _Resp:
        def __init__(self, c): self.choices = [_Choice(c)]
    class _Comp:
        async def create(self, **k): return _Resp(content)
    class _Chat:
        completions = _Comp()
    class _Cli:
        chat = _Chat()
    return _Cli()


@pytest.mark.parametrize("greeting", ["Hola", "buenos días", "gracias"])
def test_llm_never_returns_default_city(monkeypatch, greeting):
    """'Hola' (u otro saludo) jamás debe convertirse en 'Popayán' aunque el LLM
    alucine la ciudad: el guard anti-nivel-ciudad lo bloquea."""
    import asyncio
    import api.routers.twilio as tw
    monkeypatch.setattr(tw, "_get_async_openai", lambda: _stub_openai("Popayán"))
    monkeypatch.setattr(tw, "_get_model", lambda: "stub")
    name, _ = asyncio.run(tw.extract_address(greeting, "origen"))
    assert (name or "").strip().lower() not in {"popayán", "popayan", "cauca", "colombia"}


def test_filler_is_detected_for_greetings():
    from core.location_match import is_filler
    for g in ["hola", "buenos días", "buenas tardes", "gracias", "por favor",
              "ok", "listo", "sí", "no"]:
        assert is_filler(g), f"{g!r} debería ser filler"
    for place in ["campanario", "villa del viento", "hola campanario"]:
        assert not is_filler(place), f"{place!r} no debería ser filler"


# ── Integración: tras agregar "Villa del Viento" al catálogo ────────────────────

def test_villa_del_viento_now_resolves():
    from core.location_match import resolve_location_entity, decide, Decision
    m = resolve_location_entity("villa del viento")
    assert decide(m) == Decision.ACCEPT
    assert m.canonical == "Villa del Viento"
