"""
Aislamiento entre SchoolSena y NexiService durante todo el ciclo de una petición.

Corre conversaciones de los dos proyectos A LA VEZ (asyncio.gather) por
ChatService, el mismo camino que POST /chat, incluidos los mensajes que ningún
proyecto entiende (donde vivían los fallbacks compartidos). Comprueba:

  * la respuesta declara el proyecto de la petición (`_source_project`);
  * el trip-wire de project_scope no tuvo que descartar nada;
  * una respuesta de School no ofrece nada de NexiService, y al revés.

Usa la base local (lyra_db para conversaciones, vt_inventario para el catálogo
de NexiService). Las herramientas de SchoolSena se sustituyen: su API exige JWT.
"""

import asyncio
import base64
import json
import re
import uuid

import pytest

from api.schemas import ChatRequest
from core.config import settings
from core.llm_engine import LLMEngine
from orchestrator.tool_registry import ToolRegistry

NEXI_ONLY = re.compile(r"\b(negocios?|agendar|reserva\w*|restaurantes?|NexiService|Nexo)\b", re.I)
SCHOOL_ONLY = re.compile(r"\b(SchoolSena|fichas?|aprendic?e?s?|instructor\w*|calificaci\w+)\b", re.I)

SCHOOL_MSGS = [
    "¿Cómo me fue en el examen de ayer?",          # caso real que recibió el fallback de NexiService
    "asdkjhasd",                                   # nada que entender
    "quiero agendar una cita",                     # vocabulario de NexiService
    "recomiéndame un restaurante",
    "¿qué clases tengo hoy?",
    "cuánto cuesta el servicio",
]
NEXI_MSGS = [
    "busco restaurantes en popayan",
    "asdkjhasd",
    "¿qué clases tengo hoy?",                      # vocabulario de SchoolSena
    "mis notas",
    "hola",
]


def _token(roles):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return "Bearer " + b({"alg": "HS256"}) + "." + b({"roles": roles, "sub": 1}) + ".x"


@pytest.fixture()
def school_stubs():
    from tools import schoolsena as ss
    saved = {n: e["handler"] for n, e in ss.TOOLS_REGISTRY.items()}

    async def fake(params=None, context=None, **kw):
        return {"result": [], "error": None}

    for e in ss.TOOLS_REGISTRY.values():
        e["handler"] = fake
    yield
    for n, h in saved.items():
        ss.TOOLS_REGISTRY[n]["handler"] = h


def test_conversaciones_intercaladas_y_concurrentes_no_se_mezclan(school_stubs):
    from services.chat_service import ChatService
    from orchestrator import project_scope
    from orchestrator.interceptors.manager import SOURCE_KEY
    from orchestrator.memory_manager import get_or_create_conversation

    settings.LLM_EXTERNAL_ENABLED = False
    svc = ChatService(None, settings, LLMEngine(model_path=""),
                      {p: ToolRegistry.for_project(p) for p in ("nexiservice", "schoolsena")})
    before = project_scope.CONTAMINATION_EVENTS

    async def run(project, msgs, roles, personality):
        cid = str(uuid.uuid4())
        out = []
        for m in msgs:
            r = await svc.process_message(
                ChatRequest(project_id=project, user_id=f"iso-{project}", conversation_id=cid,
                            message=m, personality=personality),
                session_id=cid, auth_header=_token(roles) if roles else None)
            fd = get_or_create_conversation(r.conversation_id, 0, project)["final_data"]
            out.append((project, m, r.reply, fd.get(SOURCE_KEY)))
        return out

    async def main():
        jobs = []
        for _ in range(3):
            jobs.append(run("schoolsena", SCHOOL_MSGS, ["APRENDIZ"], "sena"))
            jobs.append(run("nexiservice", NEXI_MSGS, None, "nexo"))
        return [t for conv in await asyncio.gather(*jobs) for t in conv]

    turns = asyncio.run(main())
    assert len(turns) == 3 * (len(SCHOOL_MSGS) + len(NEXI_MSGS))
    for project, msg, reply, source in turns:
        assert source == project, (project, msg, source)
        if project == "schoolsena":
            assert not NEXI_ONLY.search(reply), (msg, reply)
        else:
            assert not SCHOOL_ONLY.search(reply), (msg, reply)
    assert project_scope.CONTAMINATION_EVENTS == before


def test_trip_wire_descarta_una_respuesta_de_otro_proyecto():
    from orchestrator import project_scope
    from orchestrator.interceptors.manager import SOURCE_KEY

    fd = {SOURCE_KEY: "nexiservice", "properties": [{"businesses": [1]}]}
    before = project_scope.CONTAMINATION_EVENTS
    reply = project_scope.enforce("schoolsena", fd, "¿Buscas un negocio?")
    assert "negocio" not in reply and fd[SOURCE_KEY] == "schoolsena" and "properties" not in fd
    assert project_scope.CONTAMINATION_EVENTS == before + 1


def test_school_no_pasa_por_la_comprension_de_nexiservice(monkeypatch):
    """Un turno de School no consulta el router/semántica de NexiService (ni vt_inventario)."""
    import services.chat_service as cs

    def boom(*a, **k):
        raise AssertionError("detect_intent de NexiService llamado en un turno de SchoolSena")

    monkeypatch.setattr(cs, "detect_intent", boom)
    from services.chat_service import ChatService
    svc = ChatService(None, settings, LLMEngine(model_path=""), {"schoolsena": ToolRegistry.for_project("schoolsena")})
    cid = str(uuid.uuid4())
    r = asyncio.run(svc.process_message(
        ChatRequest(project_id="schoolsena", user_id="iso-x", conversation_id=cid, message="hola", personality="sena"),
        session_id=cid))
    assert r.reply


def test_un_despacho_mal_registrado_se_detecta_y_no_llega_al_usuario(monkeypatch, school_stubs):
    """
    Si por error una petición de School llegara al interceptor de NexiService,
    la marca de origen lo delata y project_scope descarta la respuesta. Es la
    prueba de que el trip-wire funciona, no sólo de que hoy no hace falta.
    """
    from services.chat_service import ChatService
    from orchestrator import project_scope
    from types import SimpleNamespace
    from orchestrator.interceptors import manager

    # Un interceptor de NexiService que (por un error de registro) responde a
    # todo, sin comprobar el proyecto. El interceptor real de NexiService sí lo
    # comprueba (otra capa); aquí se prueba la última: la marca de origen.
    async def responde_todo(project_id, intent_name, args, context):
        return {"reply": "¿Buscas un negocio? Te agendo una cita.",
                "final_data": {"properties": [{"businesses": [{"id": 1, "name": "X"}]}]}}

    monkeypatch.setitem(manager._PRE_LLM, "schoolsena",
                        SimpleNamespace(PROJECT="nexiservice", pre_llm_interceptor=responde_todo))
    settings.LLM_EXTERNAL_ENABLED = False
    svc = ChatService(None, settings, LLMEngine(model_path=""),
                      {p: ToolRegistry.for_project(p) for p in ("nexiservice", "schoolsena")})
    before = project_scope.CONTAMINATION_EVENTS
    cid = str(uuid.uuid4())
    r = asyncio.run(svc.process_message(
        ChatRequest(project_id="schoolsena", user_id="iso-trip", conversation_id=cid,
                    message="busco restaurantes en popayan", personality="sena"),
        session_id=cid, auth_header=_token(["APRENDIZ"])))
    assert project_scope.CONTAMINATION_EVENTS == before + 1
    assert not NEXI_ONLY.search(r.reply), r.reply
    assert not r.properties
