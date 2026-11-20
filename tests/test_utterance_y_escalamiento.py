"""Limpieza del habla, compuerta de escalamiento y aislamiento de conversaciones."""

import pytest

from core.semantic.utterance import clean_utterance
from orchestrator.escalation import decide


@pytest.mark.parametrize("raw, expected", [
    ("el segundo por fa", "el segundo"),
    ("cuanto vale... bueno no, espera, mejor dime como funciona el primero", "dime como funciona el primero"),
    ("hola mira es que queria preguntarte algo, lo que pasa es que no se que clases tengo hoy",
     "hola no se que clases tengo hoy"),
    ("eh pues busco una barberia en cali", "busco una barberia en cali"),
    ("lyra lyra lyra lyra", "lyra"),
    ("barbería vip barbería vip", "barbería vip"),
    ("cuanto me cobran x eso", "cuanto me cobran por eso"),
    # Rectificación sobre el turno anterior: el "no" se conserva, el "mejor" no.
    ("no, mejor explicame el primero", "no, explicame el primero"),
    ("bueno, mejor mis notas", "mis notas"),
])
def test_limpia_arranques_muletillas_y_rectificaciones(raw, expected):
    assert clean_utterance(raw) == expected


@pytest.mark.parametrize("text", [
    "cual es el mejor restaurante",   # "mejor" superlativo, no rectificación
    "es mejor así",
    "Este restaurante me gusta",      # "este" demostrativo, no muletilla
    "bueno el de ayer",
    "no tengo clase hoy?",
    "hola", "mira", "no", "x",        # mensajes de una palabra no se tocan
    "no, mejor no",                   # rectificación sin contenido
])
def test_no_toca_lo_que_no_es_ruido(text):
    assert clean_utterance(text) == text


def test_compuerta_no_escala_ruido_ni_mensajes_sin_contexto():
    assert decide("nexiservice", "asdsasaddas", {"intent": "conversation"}, {}).escalate is False
    assert decide("nexiservice", "asdsasaddas", {}, {}).reason == "noise"
    assert decide("schoolsena", "ok", {}, {}).escalate is False
    d = decide("nexiservice", "quiero algo para el fin de semana con mi familia", {"intent": "conversation"}, {})
    assert d.escalate is True and d.reason == "unresolved:conversation"


def test_compuerta_respeta_motivo_del_interceptor_y_no_toca_otros_proyectos():
    d = decide("schoolsena", "por que me pusieron esa nota en el taller", {}, {"_escalation_reason": "school_unmatched"})
    assert (d.escalate, d.reason) == (True, "school_unmatched")
    # IntelliTaxi y RentUs siguen escalando como antes.
    assert decide("intellitaxi", "asd", {}, {}).escalate is True
    assert decide("rentus", "x", {}, {}).reason == "legacy"


def test_conversacion_ajena_abre_un_hilo_nuevo(monkeypatch):
    from services import chat_service

    created = []

    def fake_get_or_create(conversation_id, user_id, project_slug):
        if conversation_id == "conv-de-otro":
            return {"id": conversation_id, "user_id": 99, "project_slug": "schoolsena", "final_data": {"x": 1}}
        created.append(conversation_id)
        return {"id": conversation_id, "user_id": user_id, "project_slug": project_slug, "final_data": {}}

    monkeypatch.setattr(chat_service, "get_or_create_conversation", fake_get_or_create)

    conv_id, conv = chat_service._own_conversation("conv-de-otro", 7, "nexiservice")
    assert conv_id != "conv-de-otro"
    assert conv["final_data"] == {} and conv["user_id"] == 7
    # La propia se continúa tal cual.
    same_id, _ = chat_service._own_conversation(conv_id, 7, "nexiservice")
    assert same_id == conv_id
