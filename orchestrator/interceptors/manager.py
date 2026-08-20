import logging
from typing import Optional, Dict, Any
from orchestrator.interceptors import nexiservice
from orchestrator.interceptors import schoolsena

logger = logging.getLogger("lyra.interceptors.manager")

async def run_pre_llm_interceptors(project_id: str, intent_name: str, args: Dict[str, Any], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Runs all registered pre-LLM interceptors. Returns a response dict if intercepted, else None.
    """
    # NexiService Interceptor
    res = await nexiservice.pre_llm_interceptor(project_id, intent_name, args, context)
    if res:
        logger.info(f"Interceptor caught intent '{intent_name}' for project '{project_id}'")
        return res

    # SchoolSena Interceptor
    res = await schoolsena.pre_llm_interceptor(project_id, intent_name, args, context)
    if res:
        logger.info(f"SchoolSena interceptor caught intent for project '{project_id}'")
        return res
        
    return None

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
    
    # NexiService post-execution
    await nexiservice.post_execution_interceptor(tool_name, tool_args, tool_output, context)
    
    # SchoolSena post-execution
    await schoolsena.post_execution_interceptor(tool_name, tool_args, tool_output, context)
    
    # Common UI state injection logic
    if tool_name == "search_businesses" and tool_output.get("success"):
        businesses = tool_output.get("businesses", [])
        if businesses:
            final_data["_last_businesses"] = businesses
            final_data["properties"] = [{"businesses": businesses}]


        coords = tool_output.get("target_city_coords")
        if coords:
            final_data["map_center"] = {"lat": coords.get("lat"), "lng": coords.get("lng"), "zoom": 13}

    return final_data
