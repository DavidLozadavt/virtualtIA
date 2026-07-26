"""
Cliente ESL mínimo para FreeSWITCH — uuid_broadcast / uuid_kill.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger("lyra.telephony.esl")


class FreeSwitchESLClient:
    """Conexión corta por comando (sin suscripción de eventos)."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
        timeout: float = 5.0,
    ):
        self.host = host or settings.FREESWITCH_ESL_HOST
        self.port = port or settings.FREESWITCH_ESL_PORT
        self.password = password or settings.FREESWITCH_ESL_PASSWORD
        self.timeout = timeout

    async def _read_frame(self, reader: asyncio.StreamReader) -> tuple[dict[str, str], str]:
        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                break
            if ":" in decoded:
                key, val = decoded.split(":", 1)
                headers[key.strip().lower()] = val.strip()

        body = b""
        length = int(headers.get("content-length", "0") or "0")
        if length > 0:
            body = await asyncio.wait_for(reader.readexactly(length), timeout=self.timeout)

        text = (body.decode("utf-8", errors="replace") if body else "").strip()
        return headers, text

    async def _run_api(self, command: str) -> tuple[bool, str]:
        if not self.password:
            return False, "ESL password not configured"

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
        except Exception as e:
            logger.warning(
                "[esl] connect failed host=%s port=%s err=%s",
                self.host,
                self.port,
                e,
            )
            return False, str(e)

        try:
            await self._read_frame(reader)  # auth/request
            writer.write(f"auth {self.password}\n\n".encode())
            await writer.drain()
            auth_headers, auth_body = await self._read_frame(reader)
            reply_text = auth_headers.get("reply-text", auth_body)
            if "-ERR" in reply_text or "invalid" in reply_text.lower():
                return False, f"auth failed: {reply_text[:200]}"

            writer.write(f"api {command}\n\n".encode())
            await writer.drain()
            resp_headers, resp_body = await self._read_frame(reader)
            combined = resp_body or resp_headers.get("reply-text", "")
            ok = "+OK" in combined or combined.startswith("+OK")
            return ok, combined
        except Exception as e:
            logger.error("[esl] command=%r err=%s", command, e)
            return False, str(e)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def uuid_broadcast(self, call_uuid: str, media_uri: str, leg: str = "aleg") -> bool:
        """Reproduce audio en la pata indicada (URL HTTP o ruta absoluta WAV)."""
        path = media_uri.replace("\\", "/")
        ok, reply = await self._run_api(f"uuid_broadcast {call_uuid} {path} {leg}")
        logger.info(
            "[esl] uuid_broadcast call_uuid=%s path=%s ok=%s reply=%s",
            call_uuid,
            path,
            ok,
            (reply or "")[:120],
        )
        return ok

    async def uuid_kill(self, call_uuid: str, cause: str = "NORMAL_CLEARING") -> bool:
        ok, reply = await self._run_api(f"uuid_kill {call_uuid} {cause}")
        logger.info("[esl] uuid_kill call_uuid=%s ok=%s", call_uuid, ok)
        return ok

    async def uuid_break(self, call_uuid: str) -> bool:
        """Detiene la reproducción en curso en el canal (comando core, sin
        depender de mod_audio_stream) — usado para cortar el playback en
        barge-in."""
        ok, reply = await self._run_api(f"uuid_break {call_uuid} all")
        logger.info("[esl] uuid_break call_uuid=%s ok=%s", call_uuid, ok)
        return ok


_esl: Optional[FreeSwitchESLClient] = None


def get_esl_client() -> FreeSwitchESLClient:
    global _esl
    if _esl is None:
        _esl = FreeSwitchESLClient()
    return _esl
