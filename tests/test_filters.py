"""Filtros post-STT preservados de V1 (alucinación / eco / normalización)."""

from services.voice import filters


def test_hallucination_filter():
    assert filters.is_stt_hallucination(
        "Subtítulos realizados por la comunidad de Amara.org"
    )
    assert filters.is_stt_hallucination("gracias por ver el video")
    assert not filters.is_stt_hallucination("estoy en Valle del Ortigal")


def test_bot_echo_detection():
    last = "El punto de recogida es calle cinco, barrio Centro. ¿Me confirmas?"
    assert filters.looks_like_bot_echo("punto de recogida es calle", last)
    # Cortas nunca se descartan (un "sí" jamás es eco).
    assert not filters.looks_like_bot_echo("sí", last)
    # Entidad real del catálogo nombrada por el usuario no es eco.
    assert not filters.looks_like_bot_echo("barrio valle del ortigal sí",
                                           "¿Confirmas barrio Valle del Ortigal?")


def test_normalize_transcript_never_empty():
    assert filters.normalize_transcript("", 0.5) == ""
    out = filters.normalize_transcript("estoy en pubenza", 0.9)
    assert isinstance(out, str) and out
