"""
orchestrator/interceptors/helpers.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Utilidades de recuperación de contexto para los interceptores de Lyra.

Responsabilidades:
- Normalización de texto
- Detección de consultas genéricas
- Recuperación de IDs anclados en el historial ([BIZ:X], [ID:X])
- Recuperación de listas de negocios desde mensajes anteriores
- Recuperación del contexto de reserva activo (booking flow)

Convención de tags usados en el historial:
  [BIZ:<id>]              → ID de negocio activo
  [SERVICIO:<nombre>]     → Nombre del servicio en flujo de reserva
  [CONFIRMACIÓN NECESARIA] → Marca que indica que se espera confirmación del usuario
  Reserva: **<nombre>**   → Nombre del servicio dentro de un mensaje de confirmación
  Hora solicitada: <HH:MM> → Hora dentro de un mensaje de confirmación
"""

from __future__ import annotations

import json
import re
import logging
from typing import Dict, List, Optional, Any

from tools.shared.utils import normalize_text

logger = logging.getLogger("lyra.interceptors.helpers")


# ─── Normalización ────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    """Delegación a la utilidad global de normalización de texto."""
    return normalize_text(s)


# ─── Detección de consultas genéricas ─────────────────────────────────────────

# Palabras que indican que el usuario NO está nombrando un negocio específico
_GENERIC_ENTITY_WORDS = frozenset({
    "este", "ese", "aqui", "alli", "negocio", "local", "tienda",
    "ella", "ello", "esta", "compania", "empresa", "lugar", "sitio",
    "dia", "sol", "mar", "paz",
})


def _is_generic_query(text: str | None) -> bool:
    """
    Retorna True si el texto parece referirse a una entidad genérica
    (pronombres demostrativos, sustantivos genéricos) en lugar de nombrar
    un negocio específico.
    """
    if not text:
        return True

    text_norm = _normalize(text)
    words = text_norm.split()

    # Solo aplicar el filtro en textos cortos (1-3 palabras)
    if len(words) <= 3:
        for kw in _GENERIC_ENTITY_WORDS:
            if re.search(rf"\b{kw}\b", text_norm):
                return True

    return False


# ─── Búsqueda de IDs anclados ────────────────────────────────────────────────

# Patrones de anclas de ID reconocidos en el historial
_BIZ_ID_PATTERNS = [
    re.compile(r"\[BIZ:(\d+)\]", re.IGNORECASE),
    re.compile(r"\[ID:(\d+)\]", re.IGNORECASE),
    re.compile(r"\[TAG:(\d+)\]", re.IGNORECASE),
    re.compile(r"/empresa/(\d+)"),
]


def _find_anchored_id_in_messages(messages: List[Dict]) -> Optional[int]:
    """
    Busca el último ID de negocio anclado en el historial de mensajes.
    """
    for msg in reversed(messages):
        content = msg.get("content") or ""

        # Normalizar contenido si viene en formato de bloques (API format)
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "")
                for block in content
                if block.get("type") == "text"
            )

        if not isinstance(content, str) or not content:
            continue

        for pattern in _BIZ_ID_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                try:
                    found_id = int(matches[-1])
                    return found_id
                except ValueError:
                    continue

    return None


# ─── Recuperación de lista de negocios ────────────────────────────────────────

def _recover_last_businesses_from_history(messages: List[Dict]) -> List[Dict]:
    """
    Recupera la lista de negocios del último resultado de herramienta en el historial.
    """
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue

        try:
            content = msg.get("content") or "{}"
            data = json.loads(content) if isinstance(content, str) else content

            biz_list = (
                data.get("businesses")
                or data.get("results")
                or data.get("suggested_businesses")
            )

            if biz_list and isinstance(biz_list, list):
                result = [
                    {
                        "id": str(b.get("id")),
                        "name": b.get("name") or b.get("razonSocial"),
                    }
                    for b in biz_list
                ]
                return result

        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

    return []


def _recover_last_search_args_from_history(messages: List[Dict]) -> Dict:
    """
    Recupera la categoría y ciudad de la última búsqueda de negocios.
    """
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue

        try:
            content = msg.get("content") or "{}"
            data = json.loads(content) if isinstance(content, str) else content

            if "category" in data or "city" in data:
                return {
                    "category": data.get("category"),
                    "city": data.get("suggested_next_city") or data.get("city"),
                    "biz_count": len(data.get("businesses") or []),
                }
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

    return {}


# ─── Recuperación del contexto de reserva activo ─────────────────────────────

def _recover_last_reservation_context_from_history(messages: List[Dict]) -> Dict:
    """
    Reconstruye el contexto completo de una reserva en curso desde el historial.
    """
    ctx: Dict[str, Optional[str]] = {
        "business_id": None,
        "service_name": None,
        "time": None,
        "date": None,
        "reservation_name": None,
    }

    _CRITICAL_FIELDS = {"business_id", "service_name", "time", "reservation_name"}

    for msg in reversed(messages):
        content = msg.get("content") or ""

        if isinstance(content, list):
            content = " ".join(
                block.get("text", "")
                for block in content
                if block.get("type") == "text"
            )

        if not isinstance(content, str) or not content:
            continue

        # ── 1. Mensaje con tag [CONFIRMACIÓN NECESARIA] ──────────────────────
        if "[CONFIRMACIÓN NECESARIA]" in content or "[CONFIRMACION NECESARIA]" in content:
            # business_id
            if not ctx["business_id"]:
                m = re.search(r"\[BIZ:(\d+)\]", content)
                if m: ctx["business_id"] = m.group(1)

            # service_name
            if not ctx["service_name"]:
                m = re.search(r"Reserva:\s*\*\*([^*\n\]]+)\*\*", content)
                if m: ctx["service_name"] = m.group(1).strip()
            if not ctx["service_name"]:
                m = re.search(r"\[SERVICIO:([^\]]+)\]", content, re.IGNORECASE)
                if m: ctx["service_name"] = m.group(1).strip()

            # time
            if not ctx["time"]:
                m = re.search(r"Hora\s+solicitada[:\s]+\*?\*?\s*(\d{1,2}(?::\d{2})?(?:\s*(?:am|pm|tarde|noche))?)", content, re.IGNORECASE)
                if m: ctx["time"] = _normalize_time(m.group(1).strip())
            if not ctx["time"]:
                m = re.search(r"a\s+las\s+\*?\*?(\d{1,2}(?::\d{2})?(?:\s*(?:am|pm|tarde|noche))?)\*?\*?", content, re.IGNORECASE)
                if m: ctx["time"] = _normalize_time(m.group(1).strip())

            # date
            if not ctx["date"]:
                content_lower = content.lower()
                if "hoy" in content_lower: ctx["date"] = "today"
                elif "mañana" in content_lower or "manana" in content_lower: ctx["date"] = "tomorrow"

        # ── 2. Ancla [BIZ:ID] en cualquier mensaje ───────────────────────────
        if not ctx["business_id"]:
            m = re.search(r"\[BIZ:(\d+)\]", content)
            if m: ctx["business_id"] = m.group(1)

        # ── 3. Nombre de reserva ─────────────────────────────────────────────
        if not ctx["reservation_name"] and msg.get("role") == "assistant":
            m = re.search(r"(?:reserva\s+para|a\s+nombre\s+de|agendar\s+para|listo[,\s]+)\s*\*?\*?([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑA-Za-záéíóúñ]+){0,3})\*?\*?", content, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                cand_norm = _normalize(candidate)
                # Evitar palabras de pregunta y términos comunes de negocio
                _exclude = {"fogon", "criollo", "norte", "barberia", "restaurante", "quien", "quienes", "cual", "cuales", "como"}
                if not any(exc in cand_norm for exc in _exclude):
                    ctx["reservation_name"] = candidate

        if all(ctx.get(k) for k in _CRITICAL_FIELDS):
            break

    return ctx


def _normalize_time(raw: str) -> str:
    """Normaliza una cadena de hora a formato HH:MM."""
    raw = raw.strip().lower()
    is_pm = any(kw in raw for kw in ["pm", "tarde", "noche"])
    is_am = any(kw in raw for kw in ["am", "madrugada"]) and not is_pm
    raw_digits = re.sub(r"[^0-9:]", "", raw)

    try:
        if ":" in raw_digits:
            h_str, m_str = raw_digits.split(":", 1)
            h, m = int(h_str), int(m_str[:2]) if m_str else 0
        else:
            h, m = int(raw_digits), 0
        if is_pm and h < 12: h += 12
        elif is_am and h == 12: h = 0
        return f"{h:02d}:{m:02d}"
    except:
        return raw
