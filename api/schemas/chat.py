from typing import Optional, Union
from pydantic import BaseModel, Field, field_validator

class ChatRequest(BaseModel):
    project_id: str = Field(..., max_length=50, description="Project slug, e.g. 'rentus'")
    user_id: Union[str, int] = Field(..., description="External user identifier")
    
    @field_validator('user_id')
    @classmethod
    def transform_user_id_to_string(cls, v):
        return str(v)
    message: str = Field(..., max_length=2000, description="User message text")
    conversation_id: Optional[str] = Field(
        None, max_length=36, description="Existing conversation UUID, or null for new"
    )
    role: Optional[str] = Field("client", max_length=20, description="User role: 'admin' or 'client'")
    lat: Optional[float] = Field(None, description="User current latitude")
    lng: Optional[float] = Field(None, description="User current longitude")
    init_voice: Optional[bool] = Field(False, description="Whether to return a voice initialization greeting")
    personality: Optional[str] = Field(None, max_length=50, description="Override assistant personality (e.g., 'lyra', 'nexo')")
    active_city: Optional[str] = Field(None, max_length=50, description="The currently selected city in the UI")
    active_company_id: Optional[str] = Field(None, description="The currently selected company ID in the business view")
    voice: Optional[bool] = Field(False, description="Request TTS audio response")

class ChatResponse(BaseModel):
    reply: str = Field(..., description="Assistant's text response")
    conversation_id: str = Field(..., description="Conversation UUID")
    trust_level: int = Field(1, description="User trust level (1-5)")
    latency_ms: int = Field(0, description="Processing time in milliseconds")
    properties: list = Field(default=[], description="List of searched properties")
    filters_applied: dict = Field(default={}, description="Filters used for search")
    map_center: Optional[dict] = Field(default=None, description="Coordinates to center the map")
    voice_action: Optional[str] = Field(default=None, description="Frontend action to trigger")
    voice_action_payload: Optional[dict] = Field(default=None, description="Payload for the frontend action")
    needs_clarification: bool = Field(default=False, description="Whether Lyra needs more info")
    needs_input: bool = Field(default=False, description="Whether the UI should show an input field (e.g. for name)")
    clarification_question: Optional[str] = Field(default=None, description="Question to ask the user")
    audio_url: Optional[str] = Field(default=None, description="URL to the generated TTS audio file")
    needs_auth: bool = Field(
        default=False,
        description=(
            "La acción quedó a la espera de que el usuario inicie sesión o se "
            "registre. El frontend debe abrir el acceso; lo acordado queda "
            "guardado y se completa solo cuando vuelva autenticado."
        ),
    )
    pending_reservation: Optional[dict] = Field(
        default=None,
        description="Reserva acordada que espera autenticación (negocio, servicio, día y hora).",
    )

