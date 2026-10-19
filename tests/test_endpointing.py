"""Endpointer híbrido: cierre acústico, retención semántica y UtteranceEnd."""

from services.voice.endpointing import (
    HybridEndpointer,
    StablePartial,
    TurnReady,
    ends_in_continuation,
)
from services.voice.stt_stream import TranscriptEvent, UtteranceEndEvent


def _ep() -> HybridEndpointer:
    return HybridEndpointer(hold_ms=900, hold_max_ms=2200)


def _final(text, speech_final=False, conf=0.9):
    return TranscriptEvent(
        text=text, confidence=conf, is_final=True, speech_final=speech_final
    )


def _interim(text, conf=0.5):
    return TranscriptEvent(
        text=text, confidence=conf, is_final=False, speech_final=False
    )


def test_ends_in_continuation():
    assert ends_in_continuation("estoy en la calle")
    assert ends_in_continuation("calle dieciséis")
    assert ends_in_continuation("carrera 17")
    assert ends_in_continuation("estoy en")
    assert not ends_in_continuation("Valle del Ortigal")
    assert not ends_in_continuation("sí señora")


def test_speech_final_commits_complete_phrase():
    ep = _ep()
    signals = ep.on_event(_final("estoy en Valle del Ortigal", speech_final=True), 10.0)
    turns = [s for s in signals if isinstance(s, TurnReady)]
    assert len(turns) == 1
    assert turns[0].text == "estoy en Valle del Ortigal"
    assert not ep.has_speech()  # estado limpio para el próximo turno


def test_semantic_hold_then_more_speech():
    ep = _ep()
    signals = ep.on_event(_final("estoy en la calle", speech_final=True), 10.0)
    assert not any(isinstance(s, TurnReady) for s in signals)
    assert ep.pending_deadline is not None

    # El usuario continúa: la retención se cancela y el turno sigue abierto.
    ep.on_event(_interim("dieciséis"), 10.4)
    assert ep.pending_deadline is None

    signals = ep.on_event(
        _final("dieciséis número tres, barrio Santa Teresa", speech_final=True), 11.0
    )
    turns = [s for s in signals if isinstance(s, TurnReady)]
    assert len(turns) == 1
    assert turns[0].text == (
        "estoy en la calle dieciséis número tres, barrio Santa Teresa"
    )


def test_semantic_hold_expires_via_timer():
    ep = _ep()
    ep.on_event(_final("estoy por la carrera", speech_final=True), 10.0)
    deadline = ep.pending_deadline
    assert deadline is not None and 10.0 < deadline <= 10.91

    assert ep.on_timer(deadline - 0.05) == []  # aún no vence
    turns = ep.on_timer(deadline + 0.01)
    assert len(turns) == 1 and isinstance(turns[0], TurnReady)
    assert turns[0].text == "estoy por la carrera"


def test_utterance_end_overrides_hold():
    ep = _ep()
    ep.on_event(_final("estoy en la calle", speech_final=True), 10.0)
    assert ep.pending_deadline is not None
    turns = ep.on_event(UtteranceEndEvent(), 10.3)
    assert len(turns) == 1 and isinstance(turns[0], TurnReady)


def test_stable_partial_on_repeated_interim():
    ep = _ep()
    assert not any(
        isinstance(s, StablePartial) for s in ep.on_event(_interim("valle del"), 1.0)
    )
    signals = ep.on_event(_interim("valle del"), 1.2)
    stables = [s for s in signals if isinstance(s, StablePartial)]
    assert len(stables) == 1 and stables[0].text == "valle del"
    # No re-emite por el mismo texto repetido una tercera vez.
    assert not ep.on_event(_interim("valle del"), 1.4)


def test_is_final_segment_emits_stable_partial():
    ep = _ep()
    signals = ep.on_event(_final("estoy en Pubenza"), 5.0)
    stables = [s for s in signals if isinstance(s, StablePartial)]
    assert len(stables) == 1 and stables[0].text == "estoy en Pubenza"
    assert not any(isinstance(s, TurnReady) for s in signals)
