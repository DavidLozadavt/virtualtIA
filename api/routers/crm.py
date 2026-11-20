"""
api/routers/crm.py — Asistencia comercial para el CRM del ERP.

El ERP no conversa con Lyra por /chat: pide tareas puntuales (resumen de un
referido, borrador de mensaje, próxima acción desde una nota, respuesta a un
mensaje entrante) y recibe JSON. Lyra no guarda nada de esto.

Si el modelo externo está apagado o falla, responde 503 y el ERP usa sus reglas
deterministas; nunca devuelve un error disfrazado de contenido.
"""

import hmac
import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger("lyra.crm")

crm_router = APIRouter(prefix="/crm", tags=["crm"])

Task = Literal["resumen", "borrador", "proxima_accion", "entrante"]


class CrmAssistRequest(BaseModel):
    task: Task
    # Datos ya filtrados por el ERP (sin correos ni teléfonos).
    context: dict = Field(default_factory=dict)


@lru_cache(maxsize=1)
def _profile() -> dict:
    path = Path(__file__).resolve().parent.parent.parent / "projects" / "crm.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _check_key(key: Optional[str]) -> None:
    expected = settings.CRM_API_KEY
    if not expected or not key or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Clave inválida")


def _parse_json(text: str) -> dict:
    """El modelo a veces envuelve el JSON en ```; se toma el primer objeto."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("sin objeto JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("no es objeto")
    return data


_REQUIRED = {
    "resumen": ("resumen",),
    "borrador": ("cuerpo",),
    "proxima_accion": ("titulo", "tipo"),
    "entrante": ("accion", "respuesta"),
}


@crm_router.post("/assist")
async def crm_assist(body: CrmAssistRequest, request: Request,
                     x_lyra_key: Optional[str] = Header(None)):
    _check_key(x_lyra_key)

    if not settings.LLM_EXTERNAL_ENABLED:
        raise HTTPException(status_code=503, detail="Modelo externo deshabilitado")
    engine = getattr(request.app.state, "llm_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Motor no disponible")

    prof = _profile()
    system = prof["system_prompt"].strip() + "\n\nTarea:\n" + prof["tasks"][body.task].strip()
    if body.task == "entrante":
        system += "\n\nContenido aprobado:\n- " + "\n- ".join(prof.get("contenido_aprobado", []))

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Datos:\n" + json.dumps(body.context, ensure_ascii=False)},
    ]
    llm_cfg = prof.get("llm", {})

    from core import llm_usage
    start = time.time()
    llm_usage.begin_turn("crm", body.task)
    path = "llm"
    try:
        text = await run_in_threadpool(
            engine.generate, messages,
            max_tokens=llm_cfg.get("max_tokens", 400),
            # Regenerar pide variedad: más temperatura que la primera versión.
            temperature=0.8 if body.context.get("evitar_repetir") else llm_cfg.get("temperature", 0.3),
            purpose=f"crm_{body.task}",
        )
        data = _parse_json(text)
        missing = [k for k in _REQUIRED[body.task] if not data.get(k)]
        if missing:
            raise ValueError(f"faltan campos {missing}")
    except Exception as exc:  # LLMUnavailable, JSON roto, campos faltantes
        path = "error"
        logger.warning("crm/%s sin respuesta útil: %s", body.task, exc)
        raise HTTPException(status_code=503, detail="Sin respuesta del modelo")
    finally:
        try:
            llm_usage.end_turn(int((time.time() - start) * 1000), path)
        except Exception:
            pass

    return {"ok": True, "task": body.task, "data": data}


class CrmLlamadaRequest(BaseModel):
    mensaje_id: int
    asesor: str
    cliente: str
    caller_id: Optional[str] = None
    callback_url: str


@crm_router.post("/llamadas", status_code=202)
async def crm_llamada(body: CrmLlamadaRequest, x_lyra_key: Optional[str] = Header(None)):
    """Llamada grabada asesor → cliente por FreeSWITCH. El resultado llega al ERP por callback_url."""
    _check_key(x_lyra_key)
    from services.telephony import crm_calls

    if not settings.CRM_SIP_GATEWAY or not settings.FREESWITCH_ESL_ENABLED:
        raise HTTPException(status_code=503, detail="La central de llamadas no está configurada")
    if not crm_calls.numero_valido(body.asesor) or not crm_calls.numero_valido(body.cliente):
        raise HTTPException(status_code=422, detail="Teléfono inválido")
    if not body.callback_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="callback_url inválida")

    call_uuid = crm_calls.iniciar_llamada(
        body.mensaje_id, body.asesor, body.cliente,
        body.caller_id or "", body.callback_url,
    )
    logger.info("crm/llamadas m=%s uuid=%s", body.mensaje_id, call_uuid)
    return {"ok": True, "uuid": call_uuid}
