"""
Configuración común de las pruebas.

Las pruebas no deben depender del .env de la máquina: con el modelo externo
encendido en el servidor, un test que no lo pide acabaría llamando a la API real
(con gasto y resultados no deterministas). Por defecto todo corre en modo local;
las pruebas que miden el camino del modelo lo encienden con su propio fixture.
"""

import sys
from pathlib import Path

import pytest

# Algunos módulos de prueba importan a otros por nombre (p. ej. el catálogo de
# test_semantic_understanding); con tests/ como paquete hace falta esta ruta.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _modo_local_por_defecto(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "LLM_EXTERNAL_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_DAILY_BUDGET_USD", 0.0)
    yield
