"""
tests/test_capacidades_atendidas.py — Ninguna intención se queda sin quien la ejecute.

Éste es el fallo que más caro salía y el más difícil de ver leyendo el código:
el router y la comprensión sabían producir `fly_to_business`, `compare_businesses`
o `admin_navigate`, pero el interceptor no las conocía. Caían al bucle del
agente, que para NexiService no tiene herramientas registradas
(`tools/nexiservice.py` no declara `SCHEMAS`), y con `LLM_EXTERNAL_ENABLED=false`
el bucle contesta siempre lo mismo:

    "No estoy seguro de haberte entendido. ¿Buscas un negocio, quieres ver
     servicios o prefieres agendar una cita?"

Es decir: peticiones perfectamente comprendidas muriendo en el último paso, y
todas con el mismo síntoma, que parece un fallo de comprensión y no lo es.

La prueba compara las intenciones que las capas de entrada saben producir con
las que el interceptor sabe atender. Si mañana alguien añade una intención
nueva y se olvida del manejador, esto falla aquí y no en la presentación.

    python -m pytest tests/test_capacidades_atendidas.py -q
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTER = os.path.join(RAIZ, "orchestrator", "intent_router.py")
MOTOR = os.path.join(RAIZ, "core", "semantic", "engine.py")
INTERCEPTOR = os.path.join(RAIZ, "orchestrator", "interceptors", "nexiservice.py")


def _leer(ruta: str) -> str:
    with open(ruta, encoding="utf-8") as f:
        return f.read()


#: Intenciones que NO le corresponden al interceptor de NexiService, con el
#: motivo. Cualquier otra que las capas de entrada produzcan tiene que estar
#: atendida.
DELEGADAS = {
    # La conversación libre la redacta el modelo cuando está disponible; sin él,
    # `_handle_conversational` deja una respuesta local preparada.
    "conversation",
    "greeting",
    "identity",
    "capabilities",
    "farewell",
    # Se resuelven dentro del propio flujo de reserva, no como intención suelta.
    "request_appointment",
    "confirm_appointment",
    "semantic_clarify",
}


def _intenciones_producidas() -> set:
    fuente = _leer(ROUTER) + _leer(MOTOR)
    del_router = set(re.findall(r'"intent":\s*"([a-z_]+)"', fuente))
    del_motor = set(re.findall(r'_finish\(u, Disposition\.\w+, "([a-z_]+)"\)', fuente))
    return del_router | del_motor


def _intenciones_atendidas() -> set:
    fuente = _leer(INTERCEPTOR)
    # Comparaciones explícitas del enrutado y las tablas que agrupan varias.
    directas = set(re.findall(r'intent_name == "([a-z_]+)"', fuente))
    en_tabla = set(re.findall(r'intent_name in \(([^)]*)\)', fuente))
    for grupo in en_tabla:
        directas |= set(re.findall(r'"([a-z_]+)"', grupo))
    # Las órdenes de pantalla y los avisos de GPS viven en diccionarios.
    for tabla in ("_UI_ACTIONS", "_GPS_REPLIES"):
        bloque = re.search(rf"{tabla}[^=]*=\s*\{{(.*?)\n\}}", fuente, re.S)
        if bloque:
            directas |= set(re.findall(r'"([a-z_]+)":', bloque.group(1)))
    return directas


def test_toda_intencion_producida_tiene_quien_la_atienda():
    producidas = _intenciones_producidas()
    atendidas = _intenciones_atendidas() | DELEGADAS
    huerfanas = sorted(producidas - atendidas)
    assert not huerfanas, (
        "Estas intenciones se producen y nadie las ejecuta; acabarán en la frase "
        f"de último recurso del bucle del agente: {huerfanas}"
    )


def test_las_ordenes_de_pantalla_son_las_que_el_frontend_escucha():
    """
    El contrato está escrito en `projects/nexiservice.yaml`, bajo `map_actions`.

    Aquí vivía `map_highlight`, que no aparece en esa lista: Lyra anunciaba el
    mapa y el mapa no se movía, porque nadie escuchaba esa acción.
    """
    import yaml

    with open(os.path.join(RAIZ, "projects", "nexiservice.yaml"), encoding="utf-8") as f:
        config = yaml.safe_load(f)

    soportadas = set()
    for entrada in config.get("map_actions") or []:
        soportadas |= set(entrada.keys() if isinstance(entrada, dict) else [entrada])
    # Acciones que no son del mapa y por tanto no se declaran en el YAML. Los
    # nombres salen de `src/components/lyra/LyraAssistant.tsx` en
    # postandserviceFront, que es quien las atiende de verdad.
    soportadas |= {
        "locate_me",          # centra el mapa en el GPS del usuario
        "gps_granted",        # confirma la ciudad detectada
        "show_city_input",    # abre la caja de ciudad manual
        "set_city",           # fija la ciudad que dijo el usuario
        "open_url",           # abre un enlace externo en otra pestaña
        "require_auth",           # pendiente en el frontend (ver HANDOFF)
        "request_reservation_name",
    }

    fuente = _leer(INTERCEPTOR)
    emitidas = set(re.findall(r'"voice_action"\]?\s*[:=]\s*"([a-z_]+)"', fuente))
    desconocidas = sorted(emitidas - soportadas)
    assert not desconocidas, (
        f"El backend emite acciones que el frontend no declara escuchar: {desconocidas}"
    )
