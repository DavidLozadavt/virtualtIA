"""Descarga los modelos del pipeline de audio (paso de despliegue, una vez).

Los pesos no viven en el repositorio: son binarios de terceros con licencia
propia y versión independiente del código. Este script los deja donde la
configuración los espera:

  * **Silero VAD** (`silero_vad.onnx`, MIT, ~2.3 MB) → `AUDIO_VAD_MODEL_PATH`.
    Es el modelo con soporte nativo de 8 kHz; el archivo cuyo nombre contiene
    `16k` NO sirve para telefonía.
  * **DPDFNet 8 kHz** (`dpdfnet2_8khz.onnx`, Apache-2.0, ~10 MB) →
    `AUDIO_DENOISE_MODEL_PATH`. Es el supresor de ruido: modelo causal de
    streaming con tasa nativa de 8 kHz, lo que evita el ciclo
    8 kHz → 48 kHz → 8 kHz que exigen las alternativas.

Uso:

    python scripts/fetch_audio_models.py
    python scripts/fetch_audio_models.py --only vad
    python scripts/fetch_audio_models.py --force

Sin argumentos es idempotente: lo que ya está y es válido no se vuelve a bajar.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SILERO_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/"
    "src/silero_vad/data/silero_vad.onnx"
)
SILERO_MIN_BYTES = 1_000_000  # el .onnx real ronda 2.3 MB; menos = descarga truncada

DENOISE_URL_TEMPLATE = (
    "https://huggingface.co/Ceva-IP/DPDFNet/resolve/main/onnx/{name}?download=true"
)
DENOISE_MIN_BYTES = 4_000_000  # dpdfnet2_8khz.onnx ronda 10 MB


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "lyra-audio/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} al descargar {url}")
        with tempfile.NamedTemporaryFile(
            delete=False, dir=str(destination.parent), suffix=".part"
        ) as tmp:
            shutil.copyfileobj(response, tmp)
            temp_path = Path(tmp.name)
    # Movimiento atómico: nunca se deja un archivo a medias en la ruta final.
    temp_path.replace(destination)


def fetch_vad(force: bool) -> int:
    from core.config import settings
    from services.audio import resolve_model_path

    target = resolve_model_path(settings.AUDIO_VAD_MODEL_PATH)
    if target is None:
        print("AUDIO_VAD_MODEL_PATH está vacío: nada que descargar.")
        return 1
    if target.is_file() and target.stat().st_size >= SILERO_MIN_BYTES and not force:
        print(f"[vad] ya presente: {target} ({target.stat().st_size} bytes)")
        return 0
    print(f"[vad] descargando {SILERO_URL}")
    _download(SILERO_URL, target)
    size = target.stat().st_size
    if size < SILERO_MIN_BYTES:
        target.unlink(missing_ok=True)
        print(f"[vad] descarga inválida ({size} bytes)", file=sys.stderr)
        return 1
    digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
    print(f"[vad] listo: {target} ({size} bytes, sha256:{digest}…)")
    return 0


def fetch_denoise(force: bool) -> int:
    from core.config import settings
    from services.audio import resolve_model_path

    backend = (settings.AUDIO_DENOISE_BACKEND or "").strip().lower()
    if backend != "onnx":
        print(f"[denoise] backend={backend or 'none'}: no requiere descarga.")
        return 0
    target = resolve_model_path(settings.AUDIO_DENOISE_MODEL_PATH)
    if target is None:
        print("AUDIO_DENOISE_MODEL_PATH está vacío: nada que descargar.")
        return 1
    if target.is_file() and target.stat().st_size >= DENOISE_MIN_BYTES and not force:
        print(f"[denoise] ya presente: {target} ({target.stat().st_size} bytes)")
        return 0
    url = DENOISE_URL_TEMPLATE.format(name=target.name)
    print(f"[denoise] descargando {url}")
    try:
        _download(url, target)
    except Exception as e:  # noqa: BLE001 — sin red el pipeline degrada, no falla
        print(f"[denoise] no se pudo descargar {target.name}: {e}", file=sys.stderr)
        return 1
    size = target.stat().st_size
    if size < DENOISE_MIN_BYTES:
        target.unlink(missing_ok=True)
        print(f"[denoise] descarga inválida ({size} bytes)", file=sys.stderr)
        return 1
    digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
    print(f"[denoise] listo: {target} ({size} bytes, sha256:{digest}…)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=("vad", "denoise"),
        help="Descargar solo un componente.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Volver a descargar aunque ya exista."
    )
    args = parser.parse_args(argv)

    status = 0
    if args.only in (None, "vad"):
        status |= fetch_vad(args.force)
    if args.only in (None, "denoise"):
        status |= fetch_denoise(args.force)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
