# orchestrator/tool_registry.py
import logging
import importlib
import inspect
from typing import Any, Dict, List, Optional, Callable
from orchestrator.tool_adapter import LegacyToolAdapter

logger = logging.getLogger("lyra.orchestrator.tool_registry")

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Any] = {}
        self._schemas: List[Dict[str, Any]] = []

    def register(self, tool_instance: Any) -> None:
        """
        Registra una herramienta moderna que cumple con el contrato:
        - TOOL_NAME: str
        - TOOL_SCHEMA: dict
        - execute(params, context): coroutine
        """
        name = getattr(tool_instance, "TOOL_NAME", None)
        schema = getattr(tool_instance, "TOOL_SCHEMA", None)
        
        if not name or not schema:
            logger.warning(f"Intento de registro de herramienta inválida: {tool_instance}")
            return

        self._tools[name] = tool_instance
        self._schemas.append(schema)
        logger.debug(f"Herramienta registrada: {name}")

    def register_legacy(self, name: str, func: Callable, schema: Dict[str, Any]) -> None:
        """
        Registra una función antigua envolviéndola en un LegacyToolAdapter.
        """
        adapter = LegacyToolAdapter(name, func, schema)
        self._tools[name] = adapter
        self._schemas.append(schema)
        logger.debug(f"Herramienta legacy registrada: {name}")

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Retorna todos los esquemas registrados para el LLM."""
        return self._schemas

    async def execute(self, tool_name: str, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta una herramienta por nombre con manejo de errores estandarizado.
        """
        if tool_name not in self._tools:
            logger.error(f"Herramienta no encontrada: {tool_name}")
            return {"result": None, "error": f"Tool '{tool_name}' not found"}

        try:
            tool = self._tools[tool_name]
            # Todas las tools (modernas o adapters) deben tener .execute(params, context)
            return await tool.execute(params, context)
        except Exception as e:
            logger.error(f"Error en ejecución de tool '{tool_name}': {e}", exc_info=True)
            return {"result": None, "error": str(e)}

    def list_tools(self) -> List[str]:
        """Lista nombres de herramientas registradas."""
        return list(self._tools.keys())

    @classmethod
    def for_project(cls, project_id: str) -> "ToolRegistry":
        """
        Factory: Crea un registry y auto-descubre las herramientas del proyecto.
        Para la fase de transición (Opción B), registra herramientas legacy.
        """
        registry = cls()
        
        # Intentar cargar el módulo de herramientas del proyecto
        try:
            module_path = f"tools.{project_id}"
            module = importlib.import_module(module_path)
            
            # TODO: En el futuro, buscar clases que hereden de BaseTool.
            # Por ahora (Migración Gradual), registramos funciones conocidas 
            # que tengan un esquema definido en el módulo o externamente.
            
            # Si el módulo tiene un diccionario 'SCHEMAS', lo usamos para registrar legacy tools
            if hasattr(module, "SCHEMAS"):
                schemas = getattr(module, "SCHEMAS")
                for name, schema in schemas.items():
                    func = getattr(module, name, None)
                    if func and callable(func):
                        registry.register_legacy(name, func, schema)
            
            # También buscamos herramientas modernas (que ya tengan TOOL_NAME/TOOL_SCHEMA)
            for name, obj in inspect.getmembers(module):
                if hasattr(obj, "TOOL_NAME") and hasattr(obj, "TOOL_SCHEMA"):
                    registry.register(obj)
                    
        except Exception as e:
            logger.error(f"Error cargando herramientas para el proyecto '{project_id}': {e}")
            
        return registry
