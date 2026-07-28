"""
tests/test_intencion_si_no_cancelar.py — Entender la intención como habla la gente.

Cubre dos huecos reales:

1. Confirmaciones naturales ("mmm sí, está bien", "ahí está", "de una") que
   `_parse_si_no` devolvía None, dejando la llamada colgada esperando un "sí"
   literal.

2. Cancelaciones sin la palabra "cancelar" ("ya no necesito el servicio") y
   formas con pronombre enclítico ("cancélalo", "anúlalo") que el regex viejo
   no casaba porque el \b caía en medio de "cancela|lo".
"""

import pytest

from api.routers.whatsapp import is_cancel_request
from core.address_utils import _parse_si_no


# ── Confirmaciones ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    "sí",
    "si",
    "claro",
    "correcto",
    "exacto",
    "listo",
    "perfecto",
    "confirmo",
    "sí señor",
    "claro que sí",
    # Naturales que antes fallaban:
    "mmm sí está bien",
    "mmm, sí, está bien así",
    "está bien",
    "está bien así",
    "eso está bien",
    "ahí está",
    "ahí es",
    "así es",
    "así mismo",
    "esa es",
    "eso es",
    "de una",
    "hágale",
    "tal cual",
    "todo bien",
    "muy bien",
    "pues bien",
    "sipi",
    "simón",
    "ujum",
    "ajá",
    "ok está bien",
])
def test_confirmaciones_naturales_se_entienden_como_si(texto):
    assert _parse_si_no(texto) is True, f"{texto!r} debería confirmar"


@pytest.mark.parametrize("texto", [
    "no",
    "nop",
    "negativo",
    "incorrecto",
    "para nada",
    "no, esa está mal",
    "no es ahí",
])
def test_negativas_se_entienden_como_no(texto):
    assert _parse_si_no(texto) is False


@pytest.mark.parametrize("texto", [
    # Nombran un lugar → es contenido, no una confirmación.
    "eso queda en el norte",
    "así por la galería",
    "esa es la calle 5",
    "la calle 5",
    "bien parqueado el carro en la carrera 9",
    # Incertidumbre explícita.
    "no sé",
    "no lo sé",
])
def test_no_confunde_contenido_ni_dudas_con_un_si(texto):
    assert _parse_si_no(texto) is None


def test_negativa_gana_sobre_positiva_en_el_mismo_turno():
    # "No, sí Sena Norte": el "no" inicial corrige; el "sí" es parte del contenido.
    assert _parse_si_no("No, sí Sena Norte") is False


# ── Cancelaciones ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    # Explícitas (incluye enclíticos, que antes NO casaban).
    "cancelar",
    "cancela",
    "cancelalo",
    "cancélalo",
    "anulalo",
    "anúlalo",
    "quiero cancelar el servicio",
    "mejor cancela",
    "olvídalo",
    "déjalo así",
    "reiniciar",
    "empezar de nuevo",
    # Desistir sin decir "cancelar".
    "ya no necesito el servicio",
    "Ya no necesito el servicio, muchas gracias",
    "ya no lo necesito",
    "ya no necesito el taxi",
    "ya no lo quiero",
    "ya no me sirve",
    "ya no hace falta",
    "ya no va a ser necesario",
    "no lo necesito",
    "no necesito el servicio",
    "ya conseguí otro carro",
    "ya cogí otro",
    "ya me voy en otro",
])
def test_reconoce_la_intencion_de_cancelar(texto):
    assert is_cancel_request(texto) is True, f"{texto!r} debería cancelar"


@pytest.mark.parametrize("texto", [
    "necesito un taxi",
    "buenos días",
    "Calle 5 #12-20",
    "sí, está bien",
    "no",
    "no es 3c",
    "ya voy para allá",
    # "ya no" a secas seguido de una corrección NO es cancelar.
    "ya no, mejor en la calle 5",
    # Falsos amigos que deben sobrevivir.
    "avenida los adioses",
    "déjalo en la puerta",
    "",
])
def test_no_cancela_por_error(texto):
    assert is_cancel_request(texto) is False, f"{texto!r} NO debería cancelar"


# ── La llamada siempre pregunta cuando repite la dirección ──────────────────

def test_toda_frase_que_espera_si_o_no_termina_en_pregunta():
    """Si Lyra repite la dirección y se queda esperando, tiene que preguntar."""
    import re
    from pathlib import Path

    src = Path("services/voice/orchestrator.py").read_text(encoding="utf-8")

    # Frases asignadas a `msg` que repiten la dirección capturada.
    frases = re.findall(r'^\s+msg = (f?"[^"]+")', src, re.MULTILINE)
    assert frases, "no se encontraron frases de diálogo"

    repiten_direccion = [f for f in frases if "{origen}" in f or "{orig_q}" in f]
    assert repiten_direccion, "se esperaba al menos una frase que repita la dirección"

    sin_pregunta = [f for f in repiten_direccion if "?" not in f]
    assert not sin_pregunta, f"repiten la dirección sin preguntar: {sin_pregunta}"
