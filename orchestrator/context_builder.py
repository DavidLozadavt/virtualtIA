"""
orchestrator/context_builder.py — Arma el system prompt completo para cada request.

CORRECCIÓN CRÍTICA (bug del historial roto):
  - Los mensajes assistant con content=None (tool_calls) se excluían del historial
    enviado al LLM, dejando huecos que rompían la coherencia del modelo.
  - Ahora se reconstruyen como un resumen legible para que el LLM entienda
    qué herramientas usó sin necesitar el formato raw de tool_calls.
"""
import logging
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("lyra.context")

_project_configs: dict[str, dict] = {}


def _project_system_content(project_config: dict, current_date: str, is_minimal: bool = False, user_data: dict = None, active_company_id: Optional[str] = None) -> str:
    slug = str(project_config.get("slug") or "").strip() or "default"
    name = str(project_config.get("name") or slug).strip() or "Asistente"
    personality = project_config.get("personality") or {}
    base_prompt = str(personality.get("base") or "").strip()

    parts = []
    if base_prompt:
        parts.append(base_prompt)
    else:
        parts.append(f"Eres {name}, asistente de {slug}.")

    instructions = project_config.get("instructions")
    if instructions and isinstance(instructions, list):
        parts.append("\n### INSTRUCCIONES GENERALES ###")
        for instr in instructions:
            parts.append(f"- {instr}")

    if active_company_id:
        parts.append(f"\n- **CONTEXTO DE NAVEGACIÓN ACTUAL:** El usuario está viendo el perfil de la empresa con ID: {active_company_id}. Si hace preguntas sobre qué servicios o productos se ofrecen, o cómo hacer una reserva, asume que habla de esta empresa específica, a menos que mencione otra.")

    if user_data and slug == "nexiservice":
        role = user_data.get("role", "client")
        parts.append("\n### ROL DEL USUARIO ACTUAL ###")
        if role == "admin":
            parts.append("- El canal de comunicacion es EL PANEL DE ADMINISTRACION.")
            parts.append("- Eres el asistente para los dueños de negocios o administradores. Tu objetivo es ayudarles a configurar la empresa y dominar el sistema administrativo.")
            admin_instr = project_config.get("admin_instructions")
            if admin_instr and isinstance(admin_instr, list):
                parts.append("\n### INSTRUCCIONES DEL ADMINISTRADOR ###")
                for instr in admin_instr:
                    parts.append(f"- {instr}")
        else:
            parts.append("- El canal de comunicacion es LA APP PUBLICA PARA CLIENTES.")
            parts.append("- Eres el asistente para clientes finales. Ayúdalos a encontrar negocios, agendar citas y comprar servicios de las empresas del directorio. NUNCA ofrezcas configurar la empresa.")
            client_instr = project_config.get("client_instructions")
            if client_instr and isinstance(client_instr, list):
                parts.append("\n### INSTRUCCIONES PARA CLIENTES ###")
                for instr in client_instr:
                    parts.append(f"- {instr}")
    elif user_data:
        # For other projects, we just mention the role without NexiService specifics
        role = user_data.get("role", "usuario")
        parts.append(f"\n### ROL DEL USUARIO ACTUAL ###\n- El usuario tiene el rol de: **{role}**.")

    if is_minimal:
        # En modo blindado (Nano), solo pasamos la identidad.
        # Quitamos la fecha para ahorrar los últimos 10-15 tokens.
        return "\n".join(parts)

    # ... resto de la lógica para otros proyectos que NO son minimal ...
    # (Este bloque se mantendrá para compatibilidad si otros proyectos lo necesitan)
    core_rules = project_config.get("core_rules") or {}
    if core_rules:
        parts.append("### REGLAS ###")
        parts.append(yaml.dump(core_rules, allow_unicode=True))

    return "\n".join(parts)

def load_project_config(project_id: str, user_personality_override: Optional[str] = None) -> Optional[dict]:
    yaml_path = Path(__file__).parent.parent / "projects" / f"{project_id}.yaml"
    
    # Prioritize project-specific personalities
    personalities_path = Path(__file__).parent.parent / "projects" / f"personalities_{project_id}.yaml"
    if not personalities_path.exists():
        personalities_path = Path(__file__).parent.parent / "projects" / "personalities.yaml"
    
    if not yaml_path.exists():
        logger.warning(f"Project config not found: {yaml_path}")
        return None

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # ── SISTEMA DE PERSONALIDADES DINÁMICAS ──
        active_persona = user_personality_override or config.get("active_personality")
        config["active_personality"] = active_persona  # Always persist override choice
        if active_persona and personalities_path.exists():
            with open(personalities_path, "r", encoding="utf-8") as pf:
                personas = yaml.safe_load(pf) or {}
                persona = personas.get(active_persona)

                if persona:
                    # Inyectamos los valores al vuelo para abstraerlos del YAML original
                    if "personality" not in config:
                        config["personality"] = {}
                    config["personality"]["base"] = persona.get("system_prompt", config["personality"].get("base", ""))

                    if "voice" not in config:
                        config["voice"] = {}
                    config["voice"]["tts_voice"] = persona.get("tts_voice", config["voice"].get("tts_voice"))

                    # Metadatos extra para el Frontend / Tool Runner
                    config["assistant_name"] = persona.get("assistant_name", active_persona.capitalize())
                    config["greeting"] = persona.get("greeting", "¡Hola! ¿En qué te puedo ayudar?")
                    logger.info(f"CONFIG LOADED | Project: {project_id} | Persona: {active_persona} | Name: {config['assistant_name']}")

        return config
    except Exception as e:
        logger.error(f"Error loading YAML {yaml_path}: {e}")
        return None


def _extract_anchored_ids(conversation_history: list[dict]) -> list[str]:
    """
    Extrae IDs anclados en formato [ID: X] del último mensaje relevante del asistente.
    Solo usa el formato canónico para evitar falsos positivos.
    """
    for msg in reversed(conversation_history):
        if msg["role"] != "assistant":
            continue
        content = msg.get("content") or ""
        found = re.findall(r'\[ID:\s*(\d+)\]', content, re.IGNORECASE)
        if found:
            return found
    return []


def _sanitize_history(conversation_history: list[dict], strip_anchors: bool = False, is_nexiservice: bool = False) -> list[dict]:
    """
    Limpia el historial para enviarlo al LLM de forma coherente.

    Reglas:
    - Mensajes 'tool' (raw JSON de resultado): se omiten, ya están resumidos
      en el mensaje del asistente que siguió.
    - Mensajes 'assistant' con content=None y tool_calls (guardados como string en BD):
      se convierten en un texto legible para no dejar huecos en el historial.
    - Mensajes 'assistant' con content=None sin info util: se omiten.
    - Todo lo demas pasa tal cual.

    IMPORTANTE: Sin esta funcion, cuando el historial tiene un turno donde el asistente
    llamo a una herramienta (content=None), el LLM lo ve como un hueco y pierde contexto,
    causando que vuelva a presentarse o ignore lo que ya discutio.
    """
    cleaned = []
    max_chars = 1000
    for msg in conversation_history:
        role = msg["role"]
        content = msg.get("content") or ""

        # Para NexiService, mantenemos mensajes de herramienta si tienen datos de empresas (es nuestra memoria)
        is_nexi_tool = "nexiservice" in str(msg.get("name", "")) or is_nexiservice
        if role == "tool" and is_nexi_tool:
            try:
                content_raw = msg.get("content", "{}")
                if isinstance(content_raw, str):
                    data = json.loads(content_raw)
                else:
                    data = content_raw
                
                # OPTIMIZACIÓN DE TOKENS: Dejamos solo lo vital para el historial
                compact_tool = {
                    "data": data.get("data", "Empresa encontrada"),
                    "_original_businesses": data.get("_original_businesses", [])
                }
                cleaned.append({"role": "tool", "content": json.dumps(compact_tool), "name": msg.get("name")})
                continue
            except:
                continue

        if role == "tool":
            continue

        # Si el historial guarda tool_calls como JSON string en content,
        # lo convertimos a un resumen legible para el LLM.
        if role == "assistant" and isinstance(content, str):
            content_str = content.strip()

            # Caso 1: tool_calls serializados como string en BD
            if content_str.startswith("[{") and '"type": "function"' in content_str:
                try:
                    parsed = json.loads(content_str)
                    if isinstance(parsed, list):
                        calls_text = ", ".join(
                            tc.get("function", {}).get("name", "herramienta")
                            for tc in parsed
                            if isinstance(tc, dict)
                        )
                        cleaned.append({"role": "assistant", "content": f"[Use herramientas: {calls_text}]"})
                        continue
                except Exception:
                    # Si falla el parseo, caemos al append normal.
                    pass

            # Caso 2: content vacío pero (posiblemente) hay tool_calls en runtime
            if not content_str:
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    calls_text = ", ".join(
                        tc.get("function", {}).get("name", "herramienta")
                        for tc in tool_calls
                        if isinstance(tc, dict)
                    )
                    cleaned.append({"role": "assistant", "content": f"[Use herramientas: {calls_text}]"})
                # Si no hay nada útil, omitimos.
                continue

        # Minimizacion de payload: normalizar espacios y truncar.
        content = re.sub(r"\s+", " ", content).strip()

        # Deduplicar: si ya metimos IDs en el system, quitamos anchors del historial.
        if strip_anchors and role == "assistant" and isinstance(content, str) and content:
            # Strip [ID: X], [BIZ: X], [TAG: X]
            content = re.sub(r"\[(?:ID|BIZ|TAG):\s*\d+\]\s*", "", content, flags=re.IGNORECASE).strip()

        if len(content) > max_chars:
            content = content[: max_chars - 1].rstrip() + "…"

        cleaned.append({"role": role, "content": content})

    return cleaned


def _normalize_text(s: str) -> str:
    return (s or "").strip().lower()


def _is_trivial_input(user_message: str) -> bool:
    # Respuestas cortas donde el historial aporta poco.
    t = _normalize_text(user_message)
    trivial = {
        "sí", "si", "ok", "vale", "claro", "listo", "dale", "gracias", "gracias lyra",
        "perfecto", "bien", "entiendo", "de acuerdo", "ya", "vamos",
    }
    return (len(t) <= 3 and t) or t in trivial


def _is_followup_reference(user_message: str) -> bool:
    # Pistas de que el usuario está respondiendo a una propiedad previa.
    t = _normalize_text(user_message)
    followup_markers = [
        "[id:", "[biz:", "[tag:", # si el usuario reusa un ancla
        "esa", "ese", "esa propiedad", "esa misma", "la primera", "la segunda",
        "ver mas", "ver más", "fotos", "foto", "mostrame", "muéstrame",
        "agendar", "agenda", "visitar", "visita", "contactar", "contacto", "visitarla", "visitarlo",
        "sí", "si", "dale", "vale", "bueno", "acepto", "llévame", "llevame", "ir alla", "ir allá",
        "reseñas", "reseña", "opiniones", "comentarios", "valoracion", "calificacion"
    ]
    return any(m in t for m in followup_markers)


def _is_new_request(user_message: str) -> bool:
    # Si el usuario formula una búsqueda nueva, ignoramos el historial para ahorrar tokens.
    # NOTA: no ignoramos si detectamos intención de follow-up/agendar/ver más.
    if _is_followup_reference(user_message):
        return False

    t = _normalize_text(user_message)
    search_markers = [
        "busco", "quiero", "necesito", "recom", "recomiendo", "filtra", "filtrar",
        "apartamento", "casa", "estudio", "oficina", "local", "bodega", "lote", "finca",
        "arriendo", "alquiler", "arrendar", "alquilar", "precio", "presupuesto", "máximo", "maximo",
        "menos de", "menor a", "habitacion", "habitaciones", "cuarto", "cuartos",
    ]
    return any(m in t for m in search_markers)


def _select_history_for_request(
    conversation_history: list[dict],
    user_message: str,
    is_nexiservice: bool = False,
) -> list[dict]:
    """
    Estrategia dinámica para reducir tokens:
    - trivial: 0 mensajes
    - new request: 0 mensajes
    - follow-up: 1-2 mensajes relevantes
    """
    if not conversation_history:
        return []
    
    # Si es trivial, podemos limpiar historial para ahorrar tokens,
    # PERO solo si no estamos en NexiService donde el contexto de negocio es crucial.
    if _is_trivial_input(user_message):
        if is_nexiservice:
            return conversation_history
        return []
    
    if _is_new_request(user_message):
        if is_nexiservice:
            return conversation_history
        return conversation_history[-2:] if len(conversation_history) >= 2 else conversation_history

    # Caso 1: follow-up que depende de una propiedad anclada.
    if _is_followup_reference(user_message):
        anchored_indices: list[int] = []
        for i in range(len(conversation_history) - 1, -1, -1):
            m = conversation_history[i]
            if m.get("role") != "assistant":
                continue
            content = (m.get("content") or "").strip()
            if re.search(r"\[(?:ID|BIZ|TAG):\s*(\d+)\]", content, flags=re.IGNORECASE):
                anchored_indices.append(i)
                break
        if anchored_indices:
            idx = anchored_indices[0]
            selected_raw = [conversation_history[idx]]
            # Agregamos el último mensaje de usuario posterior al ancla (si existe) para contexto.
            for j in range(len(conversation_history) - 1, idx, -1):
                if conversation_history[j].get("role") == "user":
                    selected_raw.append(conversation_history[j])
                    break
            selected_raw_sorted = selected_raw
            # Normalizar orden temporal (assistant primero si aplica).
            selected_raw_sorted = sorted(selected_raw_sorted, key=lambda x: conversation_history.index(x))
            # Limitar a 2 mensajes para tokens (sin slicing).
            result: list[dict] = []
            for x in selected_raw_sorted:
                result.append(x)
                if len(result) > 2:
                    result.pop(0)
            return result

    # Caso 2: general follow-up sin ancla explícita.
    selected: list[dict] = []
    for m in reversed(conversation_history):
        # En NexiService, los mensajes 'tool' son VITALES para el contexto (contienen la lista de negocios)
        if m.get("role") not in {"user", "assistant", "tool"}:
            continue
        # Preservar metadatos ocultos para NexiService (importante para tool_runner)
        new_msg = m.copy()
        if is_nexiservice and m.get("role") == "tool" and "_original_businesses" in str(m.get("content", "")):
            new_msg["content"] = m["content"] # Mantener intacto si tiene la lista de negocios
            
        selected.append(new_msg)
        if len(selected) >= 10: # Aumentamos de 4 a 10 para mayor profundidad de contexto
            break
    return list(reversed(selected))


def build_system_prompt(
    project_config: dict,
    user_trust_level: int,
    user_data: dict,
    conversation_history: list[dict],
    user_message: str,
    active_company_id: Optional[str] = None,
    local_intent: Optional[dict] = None,
) -> list[dict]:
    current_date = datetime.now().strftime("%Y-%m-%d")
    session_user_id = user_data.get("external_user_id", "DESCONOCIDO")
    u_normalized = _normalize_text(user_message)

    # 1. MODO Y RUTEADO PREVENTIVO
    project_id = project_config.get("slug", "default")
    is_minimal_project = (project_id == "nexiservice")

    if local_intent is None:
        from orchestrator.intent_router import detect_intent
        local_intent = detect_intent(user_message, project_id)
        
    intent_name = local_intent.get("intent")

    # Detección de respuesta trivial (Lite)
    # IMPORTANTE: Si es una intención de NexiService (reseñas, navegación, etc.), NO es trivial.
    protected_intents = {
        "confirm_navigation", "confirm_general", "get_business_reviews", "navigate_to_company",
        "get_business_services", "get_business_availability", "get_business_web",
        "get_business_mission_vision", "request_appointment",
        "get_business_professionals", "search_businesses", "semantic_clarify",
        # Conversación: también necesita el hilo. Un "gracias" o un "¿y eso cómo
        # funciona?" sólo se responden bien sabiendo de qué se venía hablando;
        # sin historial, el asistente vuelve a presentarse en cada turno.
        "greeting", "conversation", "capabilities", "identity",
    }
    is_trivial = _is_trivial_input(user_message)

    if intent_name in protected_intents:
        is_trivial = False # Forzamos historial para mantener contexto de negocio
    
    system_content = _project_system_content(
        project_config=project_config,
        current_date=current_date,
        is_minimal=is_minimal_project,
        user_data=user_data,
        active_company_id=active_company_id
    )

    messages = [{"role": "system", "content": system_content}]

    # 3. HISTORIAL DINÁMICO
    # Si es trivial, 0 historial (Ahorro Total)
    if is_trivial:
        messages.append({"role": "user", "content": user_message})
        return messages

    selected_raw = _select_history_for_request(
        conversation_history=conversation_history,
        user_message=user_message,
        is_nexiservice=is_minimal_project
    )

    for msg in _sanitize_history(selected_raw, strip_anchors=False, is_nexiservice=is_minimal_project):
        messages.append(msg)

    messages.append({"role": "user", "content": user_message})
    return messages