"""Normalización texto→habla y segmentación por oraciones."""

from services.voice.text_normalize import (
    normalize_for_speech,
    number_to_words_es,
    split_sentences,
)


def test_numbers_basic():
    assert number_to_words_es(0) == "cero"
    assert number_to_words_es(16) == "dieciséis"
    assert number_to_words_es(21) == "veintiuno"
    assert number_to_words_es(45) == "cuarenta y cinco"
    assert number_to_words_es(100) == "cien"
    assert number_to_words_es(109) == "ciento nueve"
    assert number_to_words_es(555) == "quinientos cincuenta y cinco"
    assert number_to_words_es(1000) == "mil"
    assert number_to_words_es(2026) == "dos mil veintiséis"


def test_address_with_suffix_letters():
    out = normalize_for_speech("Cra. 4 #70AN-09")
    assert "carrera cuatro" in out
    assert "número" in out
    assert "setenta A N" in out
    assert "cero nueve" not in out  # "09" se lee como número nueve
    assert "nueve" in out
    assert "#" not in out
    assert not any(ch.isdigit() for ch in out)


def test_street_abbreviations():
    out = normalize_for_speech("Cl 8C con Kr 17, apto 302")
    assert "calle ocho C" in out
    assert "carrera diecisiete" in out
    assert "apartamento" in out


def test_plain_text_untouched():
    assert normalize_for_speech("Valle del Ortigal") == "Valle del Ortigal"


def test_split_sentences_merges_short():
    parts = split_sentences("Listo. Te ubico en el barrio Pubenza. ¿Es correcto?")
    assert parts[0].startswith("Listo. Te ubico")  # fragmento corto fusionado
    assert parts[-1] == "¿Es correcto?"


def test_split_sentences_single():
    assert split_sentences("Un momento por favor...") == ["Un momento por favor..."]
