"""Clasificador de interrupción: energía sostenida + contenido + contexto."""

import numpy as np

from services.telephony.session_store import (
    STATE_CONFIRMING_ORIGIN,
    STATE_WAITING_ORIGIN,
)
from services.voice.barge_in import InterruptionClassifier


def _loud_frame(ms: int = 20) -> bytes:
    n = int(8000 * ms / 1000)
    rng = np.random.default_rng(3)
    return (rng.normal(0, 3000, n)).astype(np.int16).tobytes()


def _feed_speech(c: InterruptionClassifier, ms: int) -> None:
    for _ in range(ms // 20):
        c.feed_audio(_loud_frame())


def test_energy_alone_never_interrupts():
    c = InterruptionClassifier(min_ms=250)
    _feed_speech(c, 600)
    assert not c.should_interrupt()  # sin señal de texto no hay corte


def test_text_alone_never_interrupts():
    c = InterruptionClassifier(min_ms=250)
    c.feed_partial("no espere mejor en la carrera quinta", STATE_WAITING_ORIGIN)
    assert not c.should_interrupt()  # sin habla sostenida no hay corte


def test_meaningful_speech_interrupts():
    c = InterruptionClassifier(min_ms=250)
    _feed_speech(c, 400)
    c.feed_partial("no espere mejor en la carrera quinta", STATE_WAITING_ORIGIN)
    assert c.should_interrupt()


def test_backchannel_does_not_interrupt():
    c = InterruptionClassifier(min_ms=250)
    _feed_speech(c, 400)
    c.feed_partial("ajá ok", STATE_WAITING_ORIGIN)
    assert not c.should_interrupt()
    c.feed_partial("sí", STATE_WAITING_ORIGIN)
    assert not c.should_interrupt()  # "sí" suelto fuera de confirmación = cortesía


def test_yes_during_confirmation_interrupts():
    c = InterruptionClassifier(min_ms=250)
    _feed_speech(c, 400)
    c.feed_partial("sí", STATE_CONFIRMING_ORIGIN)
    assert c.should_interrupt()  # la respuesta esperada llegó: cortar y confirmar


def test_reset_clears_state():
    c = InterruptionClassifier(min_ms=250)
    _feed_speech(c, 400)
    c.feed_partial("espera espera", STATE_WAITING_ORIGIN)
    assert c.should_interrupt()
    c.reset()
    assert not c.should_interrupt()
