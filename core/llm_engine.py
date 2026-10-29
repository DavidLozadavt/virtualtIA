"""
core/llm_engine.py — Tool calling para LLMs (OpenRouter/OpenAI-compatible).

Nota: el proyecto usa tool-calling nativo vía la API; el proveedor local (GGUF/llama-cpp)
no está implementado aquí.
"""

import json
import re
import logging
import time
from typing import Optional, List, Dict
import httpx
from core.config import settings

logger = logging.getLogger("lyra.llm")


class LLMUnavailable(RuntimeError):
    """
    El modelo no pudo responder.

    Existe para que la causa deje de confundirse con una respuesta. Antes, un
    fallo devolvía la cadena "Error de conexión.", que el orquestador trataba
    como texto del asistente: se le mostraba al usuario en el chat y se enviaba
    al sintetizador de voz. Con una excepción tipada, quien llama decide qué
    decirle a la persona, y el motivo real queda en el registro.
    """

    def __init__(self, reason: str, status_code: Optional[int] = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code

    @property
    def is_quota(self) -> bool:
        """True si el proveedor rechazó por saldo o cupo, no por un fallo técnico."""
        return self.status_code in (402, 429)


_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-*]{4,}|Bearer\s+[A-Za-z0-9._\-]+)")


def _redact(text: str) -> str:
    """Quita cualquier credencial que el proveedor repita en su mensaje de error."""
    return _SECRET_RE.sub("<redactado>", text or "")


def _describe_api_error(response) -> str:
    """Mensaje de error del proveedor, listo para el registro (sin credenciales)."""
    return _redact(_raw_api_error(response))


def _raw_api_error(response) -> str:
    try:
        body = response.json()
        err = body.get("error") or {}
        detail = err.get("message") or str(body)[:200]
    except Exception:
        detail = (response.text or "")[:200]
    return f"HTTP {response.status_code}: {detail}"


#: Por debajo de esto una respuesta se corta a media frase; mejor no responder.
_MIN_USEFUL_TOKENS = 32

_AFFORDABLE_RE = re.compile(r"can only afford (\d+)", re.IGNORECASE)


def _affordable_tokens(detail: str) -> Optional[int]:
    """
    Presupuesto que el proveedor dice poder costear, si lo menciona.

    Cuando el saldo se agota, OpenRouter no se limita a rechazar: indica cuántos
    tokens sí alcanzan ("you requested up to 512 tokens, but can only afford
    50"). Aprovecharlo convierte una caída total en un asistente que aún
    responde, aunque sea con menos palabras.
    """
    match = _AFFORDABLE_RE.search(detail or "")
    if not match:
        return None
    try:
        budget = int(match.group(1))
    except ValueError:
        return None
    return budget if budget >= _MIN_USEFUL_TOKENS else None


class LLMEngine:
    def __init__(self, model_path: str = "", n_ctx: int = 4096, n_gpu_layers: int = 0, n_threads: int = 4):
        self.provider = settings.LLM_PROVIDER
        self.api_endpoint = settings.llm_base_url().rstrip("/") + "/chat/completions"
        self.model_name = self._model_id(settings.OPENAI_MODEL or "gpt-4o-mini")
        self._tools_type_map: dict = {}  # tool_name -> {arg_name: json_type}

    def _model_id(self, model: str) -> str:
        """OpenAI directo no acepta el prefijo de proveedor que usa OpenRouter."""
        if self.provider == "openai" and model.startswith("openai/"):
            return model.split("/", 1)[1]
        return model

    def _post(self, payload: dict, purpose: str) -> dict:
        """
        Única salida HTTP hacia el modelo.

        Antes de llamar comprueba el tope por turno y el presupuesto diario; con
        cualquiera agotado no hay red. Reintenta como mucho LLM_MAX_RETRIES veces
        y sólo ante fallos transitorios (429 por ritmo, 5xx, red). Cada intento
        queda medido en core/llm_usage con los tokens que reporta el proveedor.
        """
        from core import llm_usage

        if self.provider not in {"openrouter", "openai"}:
            raise LLMUnavailable(f"Proveedor LLM no soportado: {self.provider}")
        try:
            llm_usage.check_budget()
        except llm_usage.LLMBudgetExceeded as exc:
            logger.warning("Llamada al modelo omitida (%s): %s", purpose, exc)
            raise LLMUnavailable(str(exc), status_code=429) from exc

        api_key = settings.llm_api_key()
        if not api_key:
            raise LLMUnavailable("Credencial del modelo no configurada", status_code=401)

        payload = {**payload, "model": self._model_id(payload.get("model") or self.model_name)}
        # Los modelos actuales de OpenAI usan max_completion_tokens (los de
        # razonamiento rechazan max_tokens); OpenRouter mantiene el nombre antiguo.
        if self.provider == "openai" and "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        timeout = httpx.Timeout(settings.LLM_TIMEOUT_SECONDS, connect=5.0)
        retries = max(0, int(settings.LLM_MAX_RETRIES))
        attempt = 0
        while True:
            t0 = time.time()
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(self.api_endpoint, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                llm_usage.record_call(purpose, payload["model"], "network_error",
                                      int((time.time() - t0) * 1000), retries=attempt)
                if attempt < retries:
                    attempt += 1
                    time.sleep(0.5)
                    continue
                raise LLMUnavailable(f"Sin conexión con el modelo: {type(exc).__name__}") from exc

            latency = int((time.time() - t0) * 1000)
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as exc:
                    llm_usage.record_call(purpose, payload["model"], "bad_json", latency, retries=attempt)
                    raise LLMUnavailable("Respuesta ilegible del modelo") from exc
                llm_usage.record_call(purpose, data.get("model") or payload["model"], "ok",
                                      latency, data.get("usage"), retries=attempt)
                return data

            detail = _describe_api_error(response)
            status = response.status_code
            llm_usage.record_call(purpose, payload["model"], f"http_{status}", latency, retries=attempt)
            insufficient_quota = "insufficient_quota" in detail or status == 402
            transient = (status == 429 and not insufficient_quota) or status >= 500
            if transient and attempt < retries:
                attempt += 1
                try:
                    wait = min(2.0, float(response.headers.get("retry-after", "0.8")))
                except ValueError:
                    wait = 0.8
                time.sleep(wait)
                continue
            error = LLMUnavailable(detail, status_code=status)
            error.response = response
            raise error

    def generate_with_tools(self, messages: List[Dict], tools_schema: List[Dict],
                            max_tokens: int = 512, temperature: float = 0.1,
                            purpose: str = "agent_tools") -> Dict:
        llm_tools = self._build_llm_tools(tools_schema)
        payload = {
            "messages": messages,
            "tools": llm_tools,
            "tool_choice": "auto",
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.provider == "openai":
            # Una herramienta por paso: el orquestador ejecuta y valida de a una.
            payload["parallel_tool_calls"] = False
        try:
            data = self._post(payload, purpose)
        except LLMUnavailable as exc:
            response = getattr(exc, "response", None)
            # Recuperación de 400 tool_use_failed (proveedores tipo Groq).
            if response is not None and response.status_code == 400:
                try:
                    err = response.json().get("error", {})
                except ValueError:
                    err = {}
                if err.get("code") == "tool_use_failed" and err.get("failed_generation"):
                    recovered = self._recover_failed_tool_call(err["failed_generation"])
                    if recovered:
                        logger.info(f"Recovered failed tool call: {recovered['tool']}({recovered['args']})")
                        return recovered
            logger.error("LLM API falló (con herramientas) — %s", exc.reason)
            raise
        try:
            return self._handle_success(data)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.error("Respuesta del LLM ilegible: %s", e)
            raise LLMUnavailable(f"Respuesta ilegible: {e}") from e

    def generate(self, messages: List[Dict], max_tokens: int = 512, temperature: float = 0.7,
                 purpose: str = "reply", model: Optional[str] = None) -> str:
        """
        Respuesta en texto libre. Lanza `LLMUnavailable` si el modelo no responde.

        Nunca devuelve un mensaje de error como si fuera contenido: quien llama
        necesita poder distinguir "el asistente dijo esto" de "el asistente no
        pudo responder", porque lo primero se le muestra y se le lee al usuario.
        """
        payload = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if model:
            payload["model"] = model
        try:
            data = self._post(payload, purpose)
        except LLMUnavailable as exc:
            budget = _affordable_tokens(exc.reason) if exc.status_code == 402 else None
            if budget and budget < max_tokens:
                logger.warning(
                    "Saldo insuficiente para %d tokens; reintentando con %d. "
                    "Las respuestas serán más cortas hasta recargar crédito.",
                    max_tokens, budget,
                )
                payload["max_tokens"] = budget
                data = self._post(payload, purpose)
            else:
                logger.error("LLM API falló — %s", exc.reason)
                raise

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.error("Respuesta del LLM ilegible: %s", e)
            raise LLMUnavailable(f"Respuesta ilegible: {e}") from e

        if not content:
            raise LLMUnavailable("El modelo devolvió una respuesta vacía")
        return content.strip()

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
