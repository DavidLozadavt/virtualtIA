"""
Ruta absoluta de ffmpeg para telefonía (subprocess sin depender de PATH).
"""

from __future__ import annotations

import logging
import os
import shutil

from core.config import settings

logger = logging.getLogger("lyra.telephony.ffmpeg")

FFMPEG_BIN = "/usr/bin/ffmpeg"


def ffmpeg_executable() -> str:
    """Binario ffmpeg configurado (absoluto por defecto)."""
    return (settings.FFMPEG_BIN or FFMPEG_BIN).strip() or FFMPEG_BIN


def log_ffmpeg_diagnostics(context: str = "") -> None:
    """Log de diagnóstico antes de invocar ffmpeg."""
    logger.info(
        "[ffmpeg] diagnostics context=%s shutil.which=%r PATH=%r bin=%r",
        context,
        shutil.which("ffmpeg"),
        os.environ.get("PATH", ""),
        ffmpeg_executable(),
    )
