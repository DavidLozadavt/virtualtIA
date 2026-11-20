"""
Fase 2: voz, narración, presentación de lo dictado, salud, requisitos de una
recomendación, menciones al propio asistente y presentadores de SchoolSena.

Las frases de los casos reales sirven de regresión, pero cada bloque prueba
también variantes que no se usaron para escribir las reglas.
"""

import re
from datetime import date, timedelta

import pytest

from core.narration import build_narration
from core.semantic.display_text import format_transcript
from core.semantic.health import asks_for_care, health_concern
from core.semantic.self_reference import strip_self_reference
from core.speech_text import to_speech

# ── Voz ───────────────────────────────────────────────────────────────────────

_RESIDUE = re.compile(r"_|\*|\[[A-Z]+|\$\d|\bCOP\b|\d\s?%|\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}|\d{1,3}\.\d{3}")


@pytest.mark.parametrize("written, spoken", [
    ("Mostrando 24 registros", "Mostrando veinticuatro registros"),
    ("$9.000 COP", "nueve mil pesos"),
    ("15/11/2026", "quince de noviembre de dos mil veintiséis"),
    ("87,3 %", "ochenta y siete coma tres por ciento"),
    ("Configuracion_Pagos", "Configuración pagos"),
    ("a las **3:00 p. m.** hay espacio", "a las tres de la tarde hay espacio"),
    ("⭐ 4.8 de 3 reseñas", "cuatro coma ocho estrellas de tres reseñas"),
    ("Hay 1 opción y 21 días", "Hay una opción y veintiún días"),
])
def test_la_voz_dice_cada_dato_segun_lo_que_es(written, spoken):
    assert to_speech(written) == spoken


@pytest.mark.parametrize("written", [
    "💰 La nómina mensual es de **$1.500.000**, repartida en 3 contratos activos.",
    "- **Martes (17/11) | 07:00–09:00** · Ficha 2558",
    "📊 **Tus calificaciones:**\n- **Examen** (Programación): 4,5 · calificada el 16/11/2026",
    "Perfecto.\n\n[BIZ:11] [CONFIRMACIÓN NECESARIA] [HORA:tres de la tarde]",
    "total_nomina_mensual: 0 · timestamp 2026-11-17T10:00:00.000000Z",
    "Precio: 120.000 pesos (25 % de descuento)",
])
def test_no_quedan_restos_tecnicos_en_la_voz(written):
    assert not _RESIDUE.search(to_speech(written)), to_speech(written)


def test_no_se_pronuncian_como_cantidad_los_identificadores():
    out = to_speech("La ficha 2558 y el celular 3156614275 en Calle 16 # 31")
    assert "2558" in out and "3156614275" in out and "número 31" in out


def test_la_voz_no_cambia_lo_escrito():
    text = "**$9.000** el 15/11/2026"
    to_speech(text)
    assert text == "**$9.000** el 15/11/2026"


# ── Narración ─────────────────────────────────────────────────────────────────

def _biz(n):
    return [{"businesses": [{"id": i, "name": f"Negocio Número {chr(64 + i)} Popayán"} for i in range(1, n + 1)]}]


def _reply(n):
    lines = [f"Encontré {n} negocios:"] + [f"{i}. **Negocio Número {chr(64 + i)} Popayán** (a {i},5 km)" for i in range(1, n + 1)]
    return "\n".join(lines + ["Te los muestro en el mapa.", "[BIZ:1]"])


@pytest.mark.parametrize("n", [1, 2, 4, 5, 8])
def test_un_negocio_por_segmento_en_el_orden_en_que_se_nombra(n):
    segs = build_narration(_reply(n), _biz(n))
    ids = [s["business_id"] for s in segs if s["business_id"] is not None]
    assert ids == list(range(1, min(n, 5) + 1))           # como mucho 5 en voz
    assert all("[BIZ" not in s["text"] and "**" not in s["text"] for s in segs)
    if n > 5:
        assert any(f"{n - 5} más" in s["text"] or "más en la lista" in s["text"] for s in segs)


def test_respuesta_sin_negocios_no_mueve_el_mapa():
    segs = build_narration("Hola, ¿en qué te ayudo?", [])
    assert segs and all(s["business_id"] is None for s in segs)


# ── Texto mostrado de lo dictado ──────────────────────────────────────────────

@pytest.mark.parametrize("raw, shown", [
    ("recomiéndame hospitales para medicina general", "Recomiéndame hospitales para medicina general."),
    ("como me fue en el examen de ayer", "¿Cómo me fue en el examen de ayer?"),
    ("hola como estas", "Hola, ¿cómo estas?"),
    ("tienes restaurantes en cali", "¿Tienes restaurantes en cali?"),
    ("mira que hace dias me duele la cabeza", "Mira que hace dias me duele la cabeza."),
    ("Ya terminé.", "Ya terminé."),
])
def test_lo_dictado_se_muestra_con_forma_sin_cambiar_palabras(raw, shown):
    assert format_transcript(raw) == shown


# ── Salud ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, level, care", [
    ("Antes de eso, mira que hace dias me esta dando un dolor de cabeza muy fuerte", "alarm", False),
    ("me duele un poco la garganta", "concern", False),
    ("mi hijo tiene fiebre", "concern", False),
    ("tengo dolor en el pecho y me falta el aire", "alarm", False),
    ("me siento mal, necesito un medico", "concern", True),
    ("busco un restaurante", None, True),
    ("tengo hambre", None, False),
])
def test_un_sintoma_contado_se_reconoce(text, level, care):
    assert health_concern(text) == level
    assert asks_for_care(text) == care


# ── Requisitos de una recomendación ──────────────────────────────────────────

@pytest.mark.parametrize("text, args, label, kind, reqs", [
    ("quisiera saber que restaurantes me recomiendas, me gusta mucho el pescado",
     {"_user_terms": ["restaurantes"]}, "Restaurantes y Gastronomía", "restaurantes", ["pescado"]),
    ("recomiéndame hospitales para medicina general",
     {"_user_terms": ["hospitales", "medicina", "general"]}, "Consulta de Medicina General", "hospitales", ["medicina general"]),
    ("que restaurante me recomiendas que tenga almuerzo ejecutivo",
     {"_user_terms": ["restaurante"]}, "Restaurantes y Gastronomía", "restaurante", ["almuerzo ejecutivo"]),
    ("recomiendame una veterinaria con servicio de peluqueria",
     {"_user_terms": ["veterinaria"]}, "Mascotas", "veterinaria", ["peluqueria"]),  # "servicio" es marco, no requisito
    ("¿cuál me recomiendas?", {}, "", "", []),
    ("recomiendame un restaurante en popayan", {"city": "Popayán", "_user_terms": ["restaurante"]},
     "Restaurantes y Gastronomía", "", []),
])
def test_la_recomendacion_separa_tipo_y_requisitos(text, args, label, kind, reqs):
    from orchestrator.interceptors.nexiservice import request_constraints
    got_kind, got_reqs = request_constraints(text, args, label)
    assert got_reqs == reqs
    if reqs:
        assert got_kind == kind


# ── Menciones al propio asistente ─────────────────────────────────────────────

_NAMES = ["lyra", "nexo", "nexiservice"]


@pytest.mark.parametrize("raw, expected", [
    ("necesito saber que puede hacer en nexiservice", "necesito saber que puede hacer"),
    ("que negocios tiene activos esta web", "que negocios tiene activos"),
    ("que es messi service", "que puedes hacer"),          # STT
    ("hola nequi service", "hola"),
    ("que servicios ofrece nexiservice", "que servicios ofrece"),
    ("quiero ver la web de fogon criollo", "quiero ver la web de fogon criollo"),   # web de UN negocio
    ("busco una lira", "busco una lira"),                  # sin nombre propio que quitar
])
def test_las_menciones_al_asistente_no_son_contenido(raw, expected):
    assert strip_self_reference(raw, _NAMES) == expected


# ── Presentadores de SchoolSena ───────────────────────────────────────────────

def test_notas_de_ayer_por_fecha_de_calificacion():
    from orchestrator.interceptors.schoolsena_present import present_notas
    ayer = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d 10:00:00")
    rows = [
        {"tituloActividad": "EXAMEN DE LOGICA", "nombreMateria": "Programación", "calificacionNumerica": "4.5",
         "calificacionEstandart": None, "fechaCalificacion": ayer},
        {"tituloActividad": "TALLER 1", "nombreMateria": "Inglés", "calificacionNumerica": "3.0",
         "calificacionEstandart": None, "fechaCalificacion": "2026-05-04 10:00:00"},
    ]
    out = present_notas(rows, {"grades_filter": {"date": date.today() - timedelta(days=1), "words": [], "mentions_exam": True}})
    assert "4,5" in out and "Taller" not in out and "no marca qué actividades son exámenes" in out
    nada = present_notas(rows[1:], {"grades_filter": {"date": date.today() - timedelta(days=1), "words": []}})
    assert nada.startswith("No encontré calificaciones registradas ayer")


def test_contratos_muestran_persona_y_fecha_y_nunca_la_cuenta():
    from orchestrator.interceptors.schoolsena_present import present_contratos
    out = present_contratos([{"numeroContrato": "CT-24", "fechaFinalContrato": "2026-12-01",
                              "numeroCuentaBancaria": "123456789012",
                              "persona": {"nombre1": "Carlos", "apellido1": "Gómez"}}], {})
    assert "Carlos Gómez" in out and "01/12/2026" in out and "123456789012" not in out
    assert not re.search(r"^\s*-\s*$", out, re.M)


def test_nomina_sin_claves_crudas():
    from orchestrator.interceptors.schoolsena_present import present_nomina
    out = present_nomina({"total_nomina_mensual": 9000, "total_contratos": 1, "moneda": "COP",
                          "timestamp": "2026-11-17T10:00:00Z"}, {})
    assert "COP" not in out and "timestamp" not in out and "1 contrato activo" in out
