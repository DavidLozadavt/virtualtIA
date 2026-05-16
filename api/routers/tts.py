from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from typing import AsyncGenerator

router = APIRouter()

@router.get("/tts/{audio_id}")
async def serve_audio(audio_id: str, request: Request):
    cache = getattr(request.app.state, "tts_cache", {})
    item = cache.get(audio_id)
    
    if not item:
        return Response(status_code=404)
    
    # Si es un generador (stream), usamos StreamingResponse
    if isinstance(item, AsyncGenerator):
        return StreamingResponse(
            item,
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-store", "Connection": "keep-alive"}
        )
    
    # Si son bytes estáticos
    return Response(
        content=item,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"}
    )
