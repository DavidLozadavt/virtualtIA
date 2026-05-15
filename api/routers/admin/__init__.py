from fastapi import APIRouter

from .sessions import router as sessions_router
from .stats import router as stats_router
from .config import router as config_router

# Root admin router
admin_router = APIRouter(prefix="/admin", tags=["admin"])

# Include sub-routers (the sub-routers define their own prefixes where needed,
# or we can attach them directly. sessions and stats have prefixes, config is root)
admin_router.include_router(sessions_router)
admin_router.include_router(stats_router)
admin_router.include_router(config_router)
