"""
core/llm_engine.py — Tool calling para LLMs (OpenRouter/OpenAI-compatible).

Nota: el proyecto usa tool-calling nativo vía la API; el proveedor local (GGUF/llama-cpp)
no está implementado aquí.
"""

import json
import re
import logging
from typing import Optional, List, Dict
import httpx
from core.config import settings

logger = logging.getLogger("lyra.llm")


class LLMEngine:
    def __init__(self, model_path: str, n_ctx: int = 4096, n_gpu_layers: int = 0, n_threads: int = 4):
        self.provider = settings.LLM_PROVIDER
        self.api_endpoint = settings.llm_base_url().rstrip("/") + "/chat/completions"
        self.model_name = settings.OPENAI_MODEL or "openai/gpt-4o-mini"
        self._tools_type_map: dict = {}  # tool_name -> {arg_name: json_type}

    def generate_with_tools(self, messages: List[Dict], tools_schema: List[Dict],
                            max_tokens: int = 512, temperature: float = 0.1) -> Dict:
        try:
            if self.provider not in {"openrouter", "openai"}:
                return {"type": "text", "content": f"Proveedor LLM no soportado: {self.provider}"}

            llm_tools = self._build_llm_tools(tools_schema)

            headers = {
                "Authorization": f"Bearer {settings.llm_api_key()}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model_name,
                "messages": messages,
                "tools": llm_tools,
                "tool_choice": "auto",
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            with httpx.Client(timeout=30.0) as client:
                response = client.post(self.api_endpoint, headers=headers, json=payload)

                if response.status_code == 200:
                    return self._handle_success(response.json())

                # --- Recuperación de 400 tool_use_failed ---
                if response.status_code == 400:
                    body = response.json()
                    err = body.get("error", {})
                    if err.get("code") == "tool_use_failed" and err.get("failed_generation"):
                        recovered = self._recover_failed_tool_call(err["failed_generation"])
                        if recovered:
                            logger.info(f"Recovered failed tool call: {recovered['tool']}({recovered['args']})")
                            return recovered

                logger.error(f"LLM API {response.status_code}: {response.text[:300]}")
                return {"type": "text", "content": "Error de comunicación con la API."}

        except Exception as e:
            logger.error(f"LLM Exception: {e}")
            return {"type": "text", "content": f"Error interno: {e}"}

    def generate(self, messages: List[Dict], max_tokens: int = 512, temperature: float = 0.7) -> str:
        try:
            if self.provider not in {"openrouter", "openai"}:
                return "Error: proveedor LLM no soportado para modo sin herramientas."

            headers = {"Authorization": f"Bearer {settings.llm_api_key()}"}
            payload = {"model": self.model_name, "messages": messages,
                       "max_tokens": max_tokens, "temperature": temperature}
            r = httpx.post(self.api_endpoint, headers=headers, json=payload, timeout=30.0)
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return "Error de conexión."

    # ── helpers ──────────────────────────────────────────────

    def _build_llm_tools(self, tools_schema: List[Dict]) -> List[Dict]:
        llm_tools = []
        for t in tools_schema:
            tool_name = t["name"]
            props: Dict[str, Dict] = {}
            arg_types: Dict[str, str] = {}

            # Soportar ambos formatos:
            # - "args": {"k": "type — description"} (legacy)
            # - "parameters": {"k": {"type": "...", "description": "..."} } (rentus.yaml actual)
            if t.get("args"):
                for k, v in t.get("args", {}).items():
                    raw = str(v).split(" — ")[0].lower()
                    if "int" in raw:
                        js_type = "integer"
                    elif "float" in raw or "num" in raw:
                        js_type = "number"
                    elif "bool" in raw:
                        js_type = "boolean"
                    else:
                        js_type = "string"
                    arg_types[k] = js_type
                    desc = str(v).split(" — ")[1] if " — " in str(v) else str(v)
                    props[k] = {"type": js_type, "description": desc}
            else:
                for k, v in (t.get("parameters") or {}).items():
                    raw_type = str((v or {}).get("type", "string")).lower()
                    if raw_type in {"int", "integer"}:
                        js_type = "integer"
                    elif raw_type in {"float", "number", "num"}:
                        js_type = "number"
                    elif raw_type in {"bool", "boolean"}:
                        js_type = "boolean"
                    else:
                        js_type = "string"
                    arg_types[k] = js_type
                    props[k] = {
                        "type": js_type,
                        "description": (v or {}).get("description") or "",
                    }

            self._tools_type_map[tool_name] = arg_types

            # Por defecto hacemos todos los args opcionales (required = []).
            # Las herramientas (ej. tools/rentus.py) ya tienen defaults, y el orquestador
            # maneja clarificaciones cuando falta info.
            required = t.get("required") or []

            llm_tools.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": t.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            })
        return llm_tools

    def _handle_success(self, data: dict) -> Dict:
        msg = data["choices"][0]["message"]
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            tool_name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            args = self._sanitize_args(tool_name, args)
            return {"type": "tool_call", "id": tc["id"], "tool": tool_name, "args": args}
        return {"type": "text", "content": (msg.get("content") or "").strip()}

    def _recover_failed_tool_call(self, failed_gen: str) -> Optional[Dict]:
        """Parse a failed_generation string like <function=name>{"k":v}</function>
        and return a cleaned tool_call dict."""
        m = re.search(r'<function=(\w+)>\s*(\{.*?\})\s*</function>', failed_gen)
        if not m:
            return None
        tool_name = m.group(1)
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            return None
        args = self._sanitize_args(tool_name, args)
        return {"type": "tool_call", "id": f"recovered_{tool_name}", "tool": tool_name, "args": args}

    def _sanitize_args(self, tool_name: str, args: dict) -> dict:
        """Fix type mismatches: cast int→str, null→default, unwrap 'properties' wrapper."""
        if "properties" in args and len(args) == 1 and isinstance(args["properties"], dict):
            args = args["properties"]

        expected = self._tools_type_map.get(tool_name, {})
        clean = {}
        for k, v in args.items():
            t = expected.get(k, "string")
            if v is None:
                clean[k] = "" if t == "string" else 0
            elif t == "string":
                clean[k] = str(v)
            elif t == "integer":
                try:
                    clean[k] = int(v) if v != "" else 0
                except (ValueError, TypeError):
                    clean[k] = 0
            elif t == "number":
                try:
                    clean[k] = float(v) if v != "" else 0.0
                except (ValueError, TypeError):
                    clean[k] = 0.0
            else:
                clean[k] = v
        return clean
