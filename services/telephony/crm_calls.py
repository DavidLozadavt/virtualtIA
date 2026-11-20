"""
services/telephony/crm_calls.py — Llamadas grabadas del CRM del ERP por FreeSWITCH.

Flujo puente (sin IA en la llamada):
1. El ERP pide POST /crm/llamadas con el id del mensaje, el celular del asesor y el del cliente.
2. Se origina por ESL: suena el asesor; al contestar se hace bridge al cliente y, cuando el
   cliente contesta, record_session graba la conversación (las dos voces).
3. Se espera a que la llamada termine (uuid_exists), se convierte el WAV a MP3 si hay ffmpeg
   y se sube al ERP (callback_url, cabecera X-Lyra-Key) con estado y duración.

Las grabaciones se escriben donde las ve FreeSWITCH (CRM_RECORDINGS_FS_DIR) y se leen donde
las ve este proceso (CRM_RECORDINGS_DIR); en una instalación sin contenedores son la misma ruta.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
import uuid
import wave
from pathlib import Path
from typing import Optional

import httpx

from core.config import settings
from services.telephony.esl_client import FreeSwitchESLClient
from services.telephony.ffmpeg_bin import ffmpeg_executable

logger = logging.getLogger("lyra.telephony.crm_calls")

_PHONE = re.compile(r"^\+?\d{7,15}$")

# Causas de FreeSWITCH que significan "nadie contestó" (no es una falla técnica).
_NO_ANSWER = ("NO_ANSWER", "USER_BUSY", "NO_USER_RESPONSE", "CALL_REJECTED", "ORIGINATOR_CANCEL",
              "ALLOTTED_TIMEOUT", "RECOVERY_ON_TIMER_EXPIRE")

# Referencias a las tareas en curso (si no, el recolector puede cortarlas).
_tareas: set[asyncio.Task] = set()


def numero_valido(tel: str) -> bool:
    return bool(_PHONE.match(tel or ""))


def _destino(tel: str) -> str:
    """+573001234567 → sofia/gateway/<gw>/<prefijo>573001234567"""
    return f"sofia/gateway/{settings.CRM_SIP_GATEWAY}/{settings.CRM_SIP_PREFIX}{tel.lstrip('+')}"


def comando_originate(call_uuid: str, mensaje_id: int, asesor: str, cliente: str,
                      caller_id: str, ruta_fs: str) -> str:
    """Comando ESL: suena el asesor; al contestar, bridge al cliente grabado desde que contesta."""
    cid = (caller_id or "").lstrip("+") or "0000000"
    a = ",".join([
        f"origination_uuid={call_uuid}",
        f"origination_caller_id_number={cid}",
        f"originate_timeout={settings.CRM_CALL_ANSWER_TIMEOUT}",
        "ignore_early_media=true",
        f"crm_mensaje={mensaje_id}",
    ])
    b = ",".join([
        f"origination_caller_id_number={cid}",
        f"leg_timeout={settings.CRM_CALL_ANSWER_TIMEOUT + 10}",
        "RECORD_STEREO=true",
        f"execute_on_answer='record_session {ruta_fs}'",
    ])
    return f"originate {{{a}}}{_destino(asesor)} &bridge([{b}]{_destino(cliente)})"


def _duracion_wav(ruta: Path) -> Optional[int]:
    try:
        with wave.open(str(ruta), "rb") as w:
            return int(w.getnframes() / float(w.getframerate() or 8000))
    except Exception:
        return None


def _a_mp3(ruta: Path) -> Path:
    """WAV → MP3 mono 32 kbps (≈ 14 MB por hora, dentro del límite de la transcripción)."""
    destino = ruta.with_suffix(".mp3")
    try:
        subprocess.run(
            [ffmpeg_executable(), "-y", "-loglevel", "error", "-i", str(ruta), "-ac", "1", "-b:a", "32k", str(destino)],
            check=True, timeout=300,
        )
        return destino
    except Exception as exc:
        logger.warning("[crm_calls] no se convirtió a mp3 (%s); se envía el wav", exc)
        return ruta


async def _avisar_erp(callback_url: str, mensaje_id: int, estado: str, duracion: Optional[int] = None,
                      error: Optional[str] = None, archivo: Optional[Path] = None) -> None:
    datos = {"mensaje_id": str(mensaje_id), "estado": estado}
    if duracion is not None:
        datos["duracion"] = str(duracion)
    if error:
        datos["error"] = error[:500]
    headers = {"X-Lyra-Key": settings.CRM_API_KEY}
    for intento in range(3):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                if archivo and archivo.exists():
                    tipo = "audio/wav" if archivo.suffix == ".wav" else "audio/mpeg"
                    with open(archivo, "rb") as f:
                        r = await client.post(callback_url, data=datos, headers=headers,
                                              files={"archivo": (archivo.name, f, tipo)})
                else:
                    r = await client.post(callback_url, data=datos, headers=headers)
            if r.status_code < 500:
                if r.status_code >= 400:
                    logger.error("[crm_calls] ERP rechazó el resultado m=%s status=%s body=%s",
                                 mensaje_id, r.status_code, r.text[:200])
                return
        except Exception as exc:
            logger.warning("[crm_calls] aviso al ERP falló m=%s intento=%s err=%s", mensaje_id, intento + 1, exc)
        await asyncio.sleep(5 * (intento + 1))


async def _seguir_llamada(esl: FreeSwitchESLClient, call_uuid: str, mensaje_id: int, asesor: str,
                          cliente: str, caller_id: str, callback_url: str) -> None:
    nombre = f"crm_{mensaje_id}_{call_uuid}.wav"
    ruta_fs = f"{settings.CRM_RECORDINGS_FS_DIR.rstrip('/')}/{nombre}"
    ruta_local = Path(settings.CRM_RECORDINGS_DIR) / nombre
    Path(settings.CRM_RECORDINGS_DIR).mkdir(parents=True, exist_ok=True)

    ok, reply = await esl._run_api(comando_originate(call_uuid, mensaje_id, asesor, cliente, caller_id, ruta_fs))
    logger.info("[crm_calls] originate m=%s uuid=%s ok=%s reply=%s", mensaje_id, call_uuid, ok, (reply or "")[:120])
    if not ok:
        causa = (reply or "").replace("-ERR", "").strip()
        if any(c in causa for c in _NO_ANSWER):
            await _avisar_erp(callback_url, mensaje_id, "no_contesta", error="El asesor no contestó.")
        else:
            await _avisar_erp(callback_url, mensaje_id, "fallida", error=f"Central: {causa or 'sin respuesta'}")
        return

    await _avisar_erp(callback_url, mensaje_id, "en_curso")

    # Esperar a que cuelguen (tope: CRM_CALL_MAX_SECONDS).
    limite = time.monotonic() + settings.CRM_CALL_MAX_SECONDS
    while time.monotonic() < limite:
        await asyncio.sleep(3)
        existe, r = await esl._run_api(f"uuid_exists {call_uuid}")
        if existe and "true" not in (r or "").lower():
            break
    await asyncio.sleep(1)  # FreeSWITCH termina de escribir el archivo

    if not ruta_local.exists() or ruta_local.stat().st_size < 1024:
        await _avisar_erp(callback_url, mensaje_id, "no_contesta", error="El cliente no contestó.")
        return

    duracion = _duracion_wav(ruta_local)
    archivo = _a_mp3(ruta_local)
    await _avisar_erp(callback_url, mensaje_id, "completada", duracion=duracion, archivo=archivo)
    for p in {ruta_local, archivo}:
        try:
            os.remove(p)
        except OSError:
            pass


def iniciar_llamada(mensaje_id: int, asesor: str, cliente: str, caller_id: str, callback_url: str,
                    esl: Optional[FreeSwitchESLClient] = None) -> str:
    """Lanza la llamada en segundo plano y devuelve su uuid de inmediato."""
    call_uuid = str(uuid.uuid4())
    # originate bloquea hasta que el asesor contesta: timeout de ESL mayor que el de timbrado.
    esl = esl or FreeSwitchESLClient(timeout=settings.CRM_CALL_ANSWER_TIMEOUT + 15)
    tarea = asyncio.create_task(_seguir_llamada(esl, call_uuid, mensaje_id, asesor, cliente, caller_id, callback_url))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return call_uuid


def avisar_llamada_ia(call_uuid: str, telefono: str, duracion: int, dialogo: list[tuple[str, str]]) -> None:
    """Llamada atendida por Lyra → CRM del ERP (POST /api/integration/crm/llamadas/ia).

    El ERP la deja en la conversación del contacto si el número está registrado y descarga la
    grabación de GET /freeswitch/recording/{call_uuid}.wav. Sin CRM_ERP_URL no hace nada.
    """
    if not settings.CRM_ERP_URL or not settings.CRM_API_KEY or not call_uuid:
        return
    texto = "\n".join(f"{quien}: {t}" for quien, t in dialogo if t)

    async def _enviar() -> None:
        url = settings.CRM_ERP_URL.rstrip("/") + "/api/integration/crm/llamadas/ia"
        datos = {"call_uuid": call_uuid, "telefono": telefono, "duracion": duracion, "transcripcion": texto}
        for intento in range(3):
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.post(url, json=datos, headers={"X-Lyra-Key": settings.CRM_API_KEY})
                if r.status_code < 500:
                    logger.info("[crm_calls] llamada IA al CRM uuid=%s status=%s", call_uuid, r.status_code)
                    return
            except Exception as exc:
                logger.warning("[crm_calls] aviso IA al CRM falló uuid=%s intento=%s err=%s", call_uuid, intento + 1, exc)
            await asyncio.sleep(3 * (intento + 1))

    tarea = asyncio.get_running_loop().create_task(_enviar())
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
