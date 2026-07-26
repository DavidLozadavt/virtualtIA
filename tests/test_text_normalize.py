"""Normalización texto→habla, prosodia y segmentación por oraciones."""

from services.voice.text_normalize import (
    normalize_for_speech,
    number_to_words_es,
    polish_prosody,
    prepare_for_speech,
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


def test_more_street_abbreviations():
    out = normalize_for_speech("Transv. 9 con Diag. 4, Av. Panamericana, N° 12")
    assert "transversal nueve" in out
    assert "diagonal cuatro" in out
    assert "avenida" in out
    assert "número doce" in out
    assert "N°" not in out


def test_thousands_and_currency_read_as_one_number():
    out = normalize_for_speech("La tarifa es $12.000")
    assert "doce mil pesos" in out
    assert not any(ch.isdigit() for ch in out)
    assert "$" not in out


def test_prosody_adds_breathing_comma_and_final_stop():
    assert polish_prosody("Listo te ubico en Pubenza") == "Listo, te ubico en Pubenza."
    assert polish_prosody("Confirma la dirección por favor") == (
        "Confirma la dirección, por favor."
    )


def test_prosody_opens_questions():
    # En español la entonación ascendente la dispara el signo de apertura.
    assert polish_prosody("Es correcto?") == "¿Es correcto?"
    assert polish_prosody("Listo, es correcto?") == "Listo, ¿es correcto?"


def test_prosody_opens_only_the_final_clause():
    # La apertura marca dónde empieza la pregunta; puesta al principio
    # convertiría toda la frase en interrogación.
    assert polish_prosody("Te ubico en Pubenza. Es correcto?") == (
        "Te ubico en Pubenza. ¿Es correcto?"
    )
    assert polish_prosody("Hola, con gusto te ayudo. Cuentame, donde estas?") == (
        "Hola, con gusto te ayudo. Cuentame, ¿donde estas?"
    )


def test_prosody_opens_exclamations():
    assert polish_prosody("Listo. Que tengas un excelente viaje!") == (
        "Listo. ¡Que tengas un excelente viaje!"
    )


def test_prosody_preserves_existing_punctuation():
    text = "Perfecto, ya te busco un carro. ¿Vas al centro?"
    assert polish_prosody(text) == text


def test_prepare_for_speech_chains_both_stages():
    out = prepare_for_speech("Listo te recojo en la Cl 8C con Kr 17")
    assert out == "Listo, te recojo en la calle ocho C con carrera diecisiete."


def test_split_sentences_merges_short():
    parts = split_sentences("Listo. Te ubico en el barrio Pubenza. ¿Es correcto?")
    assert parts[0].startswith("Listo. Te ubico")  # fragmento corto fusionado
    assert parts[-1] == "¿Es correcto?"


def test_split_sentences_keeps_address_whole():
    # El punto de "Cra." no cierra oración: partirlo hacía que la operadora
    # dijera "te ubico en la carrera." y arrancara otra frase con el número.
    parts = split_sentences("Listo, te ubico en la Cra. 4 #70AN-09. Es correcto?")
    assert parts == ["Listo, te ubico en la Cra. 4 #70AN-09.", "Es correcto?"]


def test_split_sentences_keeps_initials_whole():
    parts = split_sentences("Pregunta por J. Pérez cuando llegues. Listo?")
    assert parts == ["Pregunta por J. Pérez cuando llegues.", "Listo?"]


def test_split_sentences_single():
    assert split_sentences("Un momento por favor...") == ["Un momento por favor..."]
