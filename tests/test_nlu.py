"""Parseo/validación del NLU y clasificador determinista de degradación."""

from services.voice.nlu import (
    INTENTS,
    NLU_JSON_SCHEMA,
    fallback_classify,
    parse_nlu_payload,
)


def test_schema_is_strict_and_closed():
    assert NLU_JSON_SCHEMA["strict"] is True
    schema = NLU_JSON_SCHEMA["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "intent", "pickup_span", "destination_span", "landmark_reference", "confidence",
    }
    assert schema["properties"]["intent"]["enum"] == list(INTENTS)


def test_parse_valid_payload():
    res = parse_nlu_payload({
        "intent": "provide_pickup",
        "pickup_span": "Valle del Hortigal",
        "destination_span": None,
        "landmark_reference": None,
        "confidence": {"pickup_span": 0.9, "destination_span": 0.0},
    })
    assert res.intent == "provide_pickup"
    assert res.pickup_span == "Valle del Hortigal"
    assert res.best_pickup == "Valle del Hortigal"
    assert res.pickup_confidence == 0.9
    assert res.source == "llm"


def test_parse_defends_against_garbage():
    res = parse_nlu_payload({
        "intent": "hacked",
        "pickup_span": "   ",
        "destination_span": 42,
        "landmark_reference": "frente al hospital",
        "confidence": {"pickup_span": 7, "destination_span": -3},
    })
    assert res.intent == "unclear"
    assert res.pickup_span is None
    assert res.destination_span is None
    assert res.landmark_reference == "frente al hospital"
    assert res.best_pickup == "frente al hospital"
    assert res.pickup_confidence == 1.0  # clamp superior
    assert res.destination_confidence == 0.0  # clamp inferior


def test_fallback_yes_no_and_repeat():
    assert fallback_classify("sí señora").intent == "confirm_yes"
    assert fallback_classify("no").intent == "confirm_no"
    assert fallback_classify("¿me repites?").intent == "repeat_request"
    assert fallback_classify("").intent == "unclear"


def test_fallback_greeting_and_pickup():
    assert fallback_classify("hola buenas").intent == "greeting"
    res = fallback_classify("buenas tardes estoy en valle del ortigal")
    assert res.intent == "provide_pickup"
    assert res.pickup_span  # texto limpio, sin quedar vacío
    assert res.source == "fallback"
