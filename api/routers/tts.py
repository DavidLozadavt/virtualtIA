from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()

@router.get("/tts/{audio_id}")
async def serve_audio(audio_id: str, request: Request):
    cache = getattr(request.app.state, "tts_cache", {})
    audio_bytes = cache.get(audio_id)
    if not audio_bytes:
        return Response(status_code=404)
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"}
    )
