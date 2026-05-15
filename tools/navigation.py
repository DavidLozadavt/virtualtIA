"""
tools/navigation.py — Herramientas para la navegación programática en la UI.
"""
import logging

logger = logging.getLogger("lyra.tools.navigation")

async def navigate_to_company(company_id: str = None, company_name: str = None, city: str = None) -> dict:
    """
    Activa la navegación automática hacia el perfil de una empresa específica.
    
    Args:
        company_id: El ID único de la empresa.
        company_name: El nombre de la empresa si el ID no es conocido.
        city: Ciudad donde se encuentra el negocio (opcional).
    """
    from tools.nexiservice import search_businesses
    
    target_id = company_id
    target_name = company_name

    if not target_id and company_name:
        logger.info(f"Resolviendo nombre '{company_name}' en city='{city}' para navegación...")
        res = await search_businesses(category=company_name, city=city)
        if res.get("success") and res.get("businesses"):
            biz = res["businesses"][0]
            target_id = biz["id"]
            target_name = biz["name"]
    
    if not target_id:
        return {
            "success": False,
            "message": f"No pude encontrar el negocio '{company_name or company_id}' para ir a su perfil."
        }

    logger.info(f"Triggering navigation to company: {target_id} ({target_name or 'unknown'})")
    
    msg_name = f" de **{target_name}**" if target_name else ""
    return {
        "success": True,
        "action": "navigate",
        "url": f"/empresa/{target_id}",
        "business_name": target_name,
        "message": f"¡Claro! Llevándote al perfil{msg_name}..."
    }
