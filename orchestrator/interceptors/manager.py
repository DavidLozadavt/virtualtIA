import logging
from typing import Optional, Dict, Any
from orchestrator.interceptors import nexiservice
from orchestrator.interceptors import schoolsena

logger = logging.getLogger("lyra.interceptors.manager")

#: Interceptor de cada proyecto. Una petición sólo pasa por el de SU proyecto:
#: antes se probaban todos en cadena y cada uno debía acordarse de comprobar el
#: project_id; un olvido bastaba para que NexiService contestara en SchoolSena.
_PRE_LLM = {
    "nexiservice": nexiservice,
    "schoolsena": schoolsena,
}
_POST_EXEC = {
    "nexiservice": nexiservice.post_execution_interceptor,
    "schoolsena": schoolsena.post_execution_interceptor,
}

#: Clave con la que cada respuesta declara qué proyecto la produjo. ChatService
#: la contrasta con el proyecto de la petición (ver `project_scope`).
SOURCE_KEY = "_source_project"


def tag_source(result: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    """Marca la respuesta con el proyecto que la produjo."""
    fd = result.get("final_data")
    if not isinstance(fd, dict):
        fd = {}
        result["final_data"] = fd
    fd[SOURCE_KEY] = project_id
    return result


async def run_pre_llm_interceptors(project_id: str, intent_name: str, args: Dict[str, Any], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Runs the pre-LLM interceptor of THIS project. Returns a response dict if intercepted, else None.
    """
    module = _PRE_LLM.get(project_id)
    if module is None:
        return None
    res = await module.pre_llm_interceptor(project_id, intent_name, args, context)
    if res:
        logger.info(f"Interceptor de '{module.PROJECT}' resolvió el intent '{intent_name}' (petición de '{project_id}')")
        return tag_source(_with_text(res, intent_name), module.PROJECT)
    return None


#: Último recurso cuando un manejador resuelve el turno pero no redacta nada.
#: Un turno sin texto no se puede guardar —la columna no admite nulos— ni leer
#: en voz alta, así que el fallo aparecía como un error de base de datos a
#: mitad de la conversación, lejos del manejador que lo causó.
_SIN_TEXTO = (
    "Ya lo tengo, pero no logré redactarlo. ¿Me lo vuelves a pedir?"
)


def _with_text(result: Dict[str, Any], intent_name: str) -> Dict[str, Any]:
    if result.get("reply"):
        return result
    logger.error(
        "El manejador de '%s' resolvió el turno sin texto; se responde con el "
        "mensaje de último recurso.", intent_name,
    )
    return {**result, "reply": _SIN_TEXTO}

async def run_pre_execution_interceptors(tool_name: str, tool_args: Dict[str, Any], context: Dict[str, Any]) -> None:
    """
    Runs logic before tool execution (e.g. arg patching, guards).
    Called from tool_runner when LLM requests a tool call.
    """
    # Placeholder — add arg-patching logic here as needed
    pass

async def run_post_execution_interceptors(tool_name: str, tool_args: Dict[str, Any], tool_output: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs logic after tool execution (e.g. updating UI state, map center).
    """
    final_data = context.get("final_data", {})
    
    handler = _POST_EXEC.get(context.get("project_id"))
    if handler is not None:
        await handler(tool_name, tool_args, tool_output, context)

    # Common UI state injection logic
    if context.get("project_id") == "nexiservice" and tool_name == "search_businesses" and tool_output.get("success"):
        businesses = tool_output.get("businesses", [])
        if businesses:
            final_data["_last_businesses"] = businesses
            final_data["properties"] = [{"businesses": businesses}]


        coords = tool_output.get("target_city_coords")
        if coords:
            final_data["map_center"] = {"lat": coords.get("lat"), "lng": coords.get("lng"), "zoom": 13}

    return final_data
