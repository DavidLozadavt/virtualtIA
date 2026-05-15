# orchestrator/tool_adapter.py
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("lyra.orchestrator.tool_adapter")

class LegacyToolAdapter:
    """
    Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute.
    Permite la migración gradual sin romper la lógica actual.
    """
    def __init__(self, name: str, func: Callable, schema: Dict[str, Any]):
        self.TOOL_NAME = name
        self.TOOL_SCHEMA = schema
        self._func = func

    async def execute(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta la función legacy inyectando el contexto si es necesario 
        o simplemente pasando los params.
        """
        try:
            # Algunas herramientas legacy esperan user_data u otros campos del contexto.
            # Intentamos inyectarlos si la función los acepta o si están en params.
            
            # Nota: La mayoría de las herramientas legacy en tools/*.py 
            # ya esperan kwargs o parámetros específicos.
            
            # Combinamos params con datos relevantes del contexto si la tool los usa
            # (ej: user_data, role, project_config)
            full_params = dict(params)
            
            # Inyección selectiva para compatibilidad con tools/nexiservice.py etc.
            if "user_data" in context and "user_data" not in full_params:
                full_params["user_data"] = context["user_data"]
            
            if "role" in context and "role" not in full_params:
                 full_params["role"] = context.get("role")

            if "auth_header" in context and "auth_header" not in full_params:
                 full_params["auth_header"] = context.get("auth_header")

            # Ejecución de la función (soportando tanto sync como async)
            import asyncio
            if asyncio.iscoroutinefunction(self._func):
                result = await self._func(**full_params)
            else:
                result = self._func(**full_params)
            
            return {"result": result, "error": None}
        except Exception as e:
            logger.error(f"Error ejecutando legacy tool '{self.TOOL_NAME}': {e}", exc_info=True)
            return {"result": None, "error": str(e)}

    def get_schema(self) -> Dict[str, Any]:
        return self.TOOL_SCHEMA
