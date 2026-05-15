"""
gateway/router.py — FastAPI endpoints: POST /chat, GET /health, GET /projects.

These are the public API routes for the Lyra microservice.
"""

import logging
from pathlib import Path
from fastapi import APIRouter, Request, Depends

from api.schemas import ChatRequest, ChatResponse, HealthResponse, ProjectResponse
from api.dependencies import get_chat_service
from services.chat_service import ChatService
from core.database import get_connection, check_connection
from orchestrator.context_builder import load_project_config

from core.logger import setup_logger

logger = setup_logger("lyra.api.main")
router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    svc: ChatService = Depends(get_chat_service)
):
    """
    Main chat endpoint.
    Delegates processing to ChatService.
    """
    auth_header = request.headers.get("authorization")
    logger.info(f"AUTH DEBUG | Header present: {auth_header is not None}")
    return await svc.process_message(req, session_id=req.conversation_id, auth_header=auth_header, app_state=request.app.state)

@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """Health check — reports model and DB status."""
    engine = getattr(request.app.state, "llm_engine", None)
    model_loaded = engine is not None
    model_name = engine.model_name if engine else ""
    db_connected = check_connection()

    return HealthResponse(
        status="ok" if model_loaded else "degraded",
        model_loaded=model_loaded,
        model_name=model_name,
        db_connected=db_connected,
    )


@router.get("/reverse-geocode")
async def reverse_geocode_api(lat: float, lng: float):
    """Proxy for reverse geocoding to avoid CORS and handle API keys securely."""
    from services.geo import reverse_geocode
    return await reverse_geocode(lat, lng)


@router.get("/geocode")
async def geocode_api(q: str):
    """Proxy for forward geocoding with Google Maps support & OSM fallback."""
    from services.geo import forward_geocode
    return await forward_geocode(q)


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects():
    """List all active projects in the database."""
    projects: list[ProjectResponse] = []
    seen_slugs: set[str] = set()

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, slug, name, is_active FROM lyra_projects WHERE is_active = 1"
                )
                rows = cursor.fetchall()
                for row in rows:
                    slug = str(row["slug"])
                    seen_slugs.add(slug)
                    projects.append(
                        ProjectResponse(
                            id=row["id"],
                            slug=slug,
                            name=row["name"],
                            is_active=bool(row["is_active"]),
                        )
                    )
    except Exception as e:
        logger.error(f"Error listing projects: {e}")

    projects_dir = Path(__file__).parent.parent.parent / "projects"
    synthetic_id = -1
    for yaml_path in sorted(projects_dir.glob("*.yaml")):
        slug = yaml_path.stem
        if slug in seen_slugs:
            continue
        config = load_project_config(slug) or {}
        projects.append(
            ProjectResponse(
                id=synthetic_id,
                slug=slug,
                name=str(config.get("name") or slug),
                is_active=True,
            )
        )
        synthetic_id -= 1

    return projects


@router.get("/status")
async def public_status(request: Request):
    """Public status endpoint — lightweight check for frontend clients."""
    engine = getattr(request.app.state, "llm_engine", None)
    model_loaded = engine is not None

    # Check maintenance mode from config
    maintenance = False
    try:
        from core.database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT config_value FROM lyra_config WHERE config_key = 'maintenance_mode'"
                )
                row = cur.fetchone()
                if row and row["config_value"] in ("true", "True", "1"):
                    maintenance = True
    except Exception:
        pass

    status = "maintenance" if maintenance else ("online" if model_loaded else "degraded")

    return {
        "success": True,
        "data": {
            "status": status,
        },
    }
