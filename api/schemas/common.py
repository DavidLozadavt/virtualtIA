from pydantic import BaseModel

class HealthResponse(BaseModel):
    status: str = "ok"
    model_loaded: bool = False
    model_name: str = ""
    db_connected: bool = False

class ProjectResponse(BaseModel):
    id: int
    slug: str
    name: str
    is_active: bool
