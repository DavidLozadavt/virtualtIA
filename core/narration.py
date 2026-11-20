"""
core/narration.py — Lo que Lyra dice, en segmentos que la interfaz puede seguir.

Antes la voz y el mapa iban por separado: el front leía la respuesta completa y,
en paralelo, recorría los negocios con temporizadores fijos (2,2 s de espera y
3 s por parada). Con cuatro restaurantes el mapa terminaba cuando la voz apenas
nombraba el primero. El otro mecanismo —marcas [TAG:id] por frase— nunca llegó a
funcionar: ChatService las borraba como marcadores técnicos.

Aquí la respuesta se parte en SEGMENTOS hablados. Cada segmento lleva:

    text         lo que se dice (ya normalizado para voz, `speech_text.to_speech`)
    business_id  el negocio que ese segmento nombra, o None

A lo sumo un negocio por segmento. La asociación sale de la lista ESTRUCTURADA
de negocios del turno (ids y nombres que el propio handler presentó), no de
extraer nombres del texto con expresiones regulares: un segmento nombra un
negocio si contiene su nombre registrado.

El front reproduce los segmentos en orden y enfoca el mapa cuando EMPIEZA a
sonar el audio de cada uno; pausa, cancelación y cambio de velocidad siguen al
audio real. La granularidad es el segmento (una línea o frase), no la palabra:
el motor (edge-tts por /voice/synthesize_stream) no expone marcas de tiempo por
palabra al navegador.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

from core.speech_text import to_speech

#: Cuántos negocios se nombran en voz alta. El resto se resume; en pantalla
#: siguen todos.
SPOKEN_BUSINESS_LIMIT = 5


def _businesses(properties: Iterable) -> List[dict]:
    out = []
    for block in properties or []:
        if isinstance(block, dict):
            for b in block.get("businesses") or []:
                if isinstance(b, dict) and b.get("id") is not None and (b.get("name") or b.get("razonSocial")):
                    out.append(b)
    return out


def _norm(s: str) -> str:
    from core.semantic.morphology import normalize
    return normalize(s or "")


def build_narration(reply: str, properties: Optional[list] = None) -> List[dict]:
    """Segmentos hablados de `reply`, cada uno con el negocio que nombra (si alguno)."""
    if not reply or not reply.strip():
        return []
    businesses = _businesses(properties)
    names = [(b["id"], _norm(b.get("name") or b.get("razonSocial"))) for b in businesses]
    # Los nombres más largos primero: "Fogón Criollo Norte Popayán" antes que
    # un "Fogón Criollo" que fuera su prefijo.
    names.sort(key=lambda x: -len(x[1]))

    lines = [ln for ln in re.split(r"\n+", reply) if ln.strip()]
    segments: List[dict] = []
    spoken_biz = 0
    skipped = 0
    pending: List[str] = []

    def flush():
        if pending:
            text = to_speech("\n".join(pending))
            if text:
                segments.append({"text": text, "business_id": None})
            pending.clear()

    for line in lines:
        folded = _norm(line)
        hit = next((bid for bid, nm in names if nm and nm in folded), None)
        if hit is None:
            pending.append(line)
            continue
        if spoken_biz >= SPOKEN_BUSINESS_LIMIT:
            skipped += 1
            continue
        flush()
        text = to_speech(line)
        if text:
            segments.append({"text": text, "business_id": hit})
            spoken_biz += 1
    if skipped:
        pending.append(f"Y {skipped} más en la lista.")
    flush()
    return segments
