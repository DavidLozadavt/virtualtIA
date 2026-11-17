"""Reproduce conversaciones por ChatService (camino real, modelo apagado). Escribe en lyra_db local."""
import asyncio, base64, json, sys, uuid, logging
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()  # igual que main.py: las herramientas leen os.getenv al importarse (p. ej. SCHOOLSENA_API_BASE)
logging.disable(logging.CRITICAL)
from api.schemas import ChatRequest
from services.chat_service import ChatService
from core.config import settings
from core.llm_engine import LLMEngine
from orchestrator.tool_registry import ToolRegistry

settings.LLM_EXTERNAL_ENABLED = False

def token(roles):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return "Bearer " + b({"alg": "HS256"}) + "." + b({"roles": roles, "sub": 1}) + ".x"

def service():
    return ChatService(None, settings, LLMEngine(model_path=""),
                       {p: ToolRegistry.for_project(p) for p in ("nexiservice", "schoolsena")})

async def conversation(svc, project, msgs, roles=None, user="replay", **extra):
    cid = str(uuid.uuid4()); out = []
    for m in msgs:
        r = await svc.process_message(ChatRequest(project_id=project, user_id=user, conversation_id=cid, message=m, **extra),
                                      session_id=cid, auth_header=token(roles) if roles else None)
        out.append((m, r))
    return out

if __name__ == "__main__":
    convs = json.load(open(sys.argv[1], encoding="utf-8"))
    async def main():
        svc = service()
        for c in convs:
            print(f"=== {c['project']} {c.get('roles')}")
            for m, r in await conversation(svc, c["project"], c["msgs"], c.get("roles"), personality=c.get("personality")):
                print(f"  U: {m}\n  L: {r.reply[:260]!r}")
    asyncio.run(main())
