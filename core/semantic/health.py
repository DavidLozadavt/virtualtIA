"""
core/semantic/health.py — Cuando alguien cuenta un síntoma.

"Hace días me está dando un dolor de cabeza muy fuerte" no es una búsqueda ni
una reserva, y tampoco una charla que se responde con un saludo. Es información
personal que puede ser seria. Lyra no diagnostica ni tiene capacidades médicas:
lo que sí debe hacer es reconocerlo, orientar con prudencia y, ante signos de
alarma, recomendar atención urgente.

Este módulo sólo DETECTA el relato de un síntoma y arma una respuesta prudente.
No busca negocios ni toca el estado de la conversación: lo que estaba en curso
(una lista, un negocio en foco) sigue ahí si el usuario quiere retomarlo.

La lista de síntomas es una clase cerrada a propósito: en salud, una regla
explícita y revisable es preferible a una inferencia que se equivoque en
silencio. El número de emergencias de Colombia es el 123.
"""

from __future__ import annotations

import re
from typing import Optional

from core.semantic.morphology import normalize

# Relato en primera persona o sobre alguien cercano.
_FIRST_PERSON = re.compile(
    r"\b(me|tengo|siento|estoy|ando|mi|mis|a mi|mi hijo|mi hija|mi mama|mi papa|mi bebe|mi esposa|mi esposo)\b"
)

# Síntomas y quejas de salud frecuentes (formas normalizadas, sin tildes).
_SYMPTOM = re.compile(
    # "molestia" sólo con lugar del cuerpo ("molestia en el pecho"): "disculpa la
    # molestia" es cortesía. "sangr-" sólo como sangre/sangrado: "sangría" es una bebida.
    r"\b(dolor\w*|duele\w*|dolio|molestia en\b|fiebre|calentura|mareo\w*|mareado|mareada|nausea\w*|vomit\w*|"
    r"diarrea|tos|gripa|gripe|resfriado|migrana\w*|jaqueca|sangre|sangrado|sangrando|hemorragia|desmay\w*|convulsi\w*|"
    r"ahog\w*|asfixia|me siento mal|me siento muy mal|enferm[oa]|malestar|infeccion|herida|quemadura|"
    r"golpe|fractura|alergia|hinchad[oa]|inflamad[oa]|palpitaciones|presion alta)\b"
)

# Signos de alarma: piden atención urgente, no una cita.
_ALARM = re.compile(
    r"\b(dolor (?:muy )?fuerte|muy fuerte|el peor dolor|insoportable|no aguanto|dolor (?:en el|de) pecho|"
    r"impresionante|terrible|horrible|tremendo|intensisimo|intenso|fortisimo|"
    r"de repente|de golpe|subito|"
    r"pecho|no puedo respirar|me falta el aire|dificultad para respirar|ahog\w*|desmay\w*|perdi el conocimiento|"
    r"convulsi\w*|sangr\w* mucho|hemorragia|no puedo mover|se me durmio (?:la cara|el brazo|media cara)|"
    r"vision borrosa|no veo|confusion|rigidez (?:en el|de) cuello|fiebre muy alta|hace (?:varios )?dias)\b"
)

# Si el mensaje pide explícitamente buscar o reservar atención, eso manda: la
# persona ya decidió qué quiere hacer con su síntoma.
_EXPLICIT_REQUEST = re.compile(
    r"\b(busc\w*|necesito (?:un|una) (?:medico|doctor\w*|hospital|clinica|consultorio|cita)|"
    r"recomienda\w*|agend\w*|reserv\w*|cita|turno|donde (?:hay|queda|encuentro))\b"
)


def health_concern(text: str) -> Optional[str]:
    """'alarm' | 'concern' si el mensaje relata un síntoma; None si no."""
    t = normalize(text or "")
    if not t or not _SYMPTOM.search(t) or not _FIRST_PERSON.search(t):
        return None
    # Un síntoma de una mascota es una necesidad de veterinaria, no de urgencias.
    if _ANIMAL.search(t):
        return None
    return "alarm" if _ALARM.search(t) else "concern"


_ANIMAL = re.compile(r"\b(perr[oa]s?|gat[oa]s?|mascotas?|cachorr[oa]s?|minino|loro|conejo|caballo|vaca|animal\w*)\b")


def asks_for_care(text: str) -> bool:
    """¿Además del síntoma, pide buscar o reservar atención?"""
    return bool(_EXPLICIT_REQUEST.search(normalize(text or "")))


def health_reply(level: str, can_search_care: bool) -> str:
    """Respuesta prudente: sin diagnóstico, con orientación y, si hay alarma, urgencia."""
    if level == "alarm":
        text = ("Lamento que te sientas así. Un dolor muy fuerte, o que lleva varios días, merece que lo "
                "revise un profesional pronto. Si además tienes dificultad para respirar, dolor en el pecho, "
                "desmayo, confusión, debilidad en un lado del cuerpo o fiebre muy alta, ve a urgencias o "
                "llama al **123**.")
    else:
        text = ("Lamento que te sientas así. No puedo darte un diagnóstico, pero si el malestar sigue o "
                "empeora, lo mejor es que te vea un profesional de la salud.")
    if can_search_care:
        text += " Si quieres, te busco consultorios o centros médicos cercanos."
    return text
