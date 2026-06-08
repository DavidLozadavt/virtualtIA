"""
tests/test_stt_repair.py — Cobertura de la capa STT anterior al resolver:
reparación fonética de transcripción y generación de hints model-aware.

No prueba la precisión real de Deepgram (requiere llamadas en vivo); prueba que
el repair no corrompe texto y corrige misspellings claros, y que el generador de
hints produce el formato correcto por modelo.
"""

import pytest

from core.stt_enhancer import repair_location_transcription as repair
from core.streaming_pipeline import _get_contextual_hints, _build_hint_vocab


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
