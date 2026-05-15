# Guía de Contribución — Lyra AI

---

## 1. Estándares de Código

### Asincronía total

Lyra es un motor 100% asíncrono. Toda función que realice I/O debe ser `async def` y usar librerías compatibles.

```python
# ❌ Incorrecto
import requests
response = requests.get(url)

# ✅ Correcto
import httpx
async with httpx.AsyncClient() as client:
    response = await client.get(url)
```

### Logging centralizado

Nunca uses `print()`. Usa el logger estandarizado.

```python
from core.logger import get_logger
logger = get_logger(__name__)

logger.info("Mensaje informativo")
logger.warning("Algo inusual ocurrió")
logger.error("Error crítico", exc_info=True)
```

El nombre del logger hereda del módulo automáticamente (`lyra.services.chat`, `lyra.tools.rentus`, etc.).

### Type Hints obligatorios

Todas las firmas de funciones deben tener tipos declarados.

```python
# ❌ Incorrecto
async def process(data, user_id):
    ...

# ✅ Correcto
async def process(data: dict, user_id: str) -> ChatResponse:
    ...
```

### Thin Routers

Los routers solo reciben y devuelven HTTP. Sin lógica de negocio, sin acceso a DB.

```python
# ❌ Incorrecto — lógica de negocio en el router
@router.post("/chat")
async def chat(req: ChatRequest):
    user = db.query(...)
    history = get_history(...)
    response = call_llm(...)
    return response

# ✅ Correcto — router delega al servicio
@router.post("/chat")
async def chat(req: ChatRequest, svc: ChatService = Depends(get_chat_service)):
    return await svc.process_message(req)
```

---

## 2. Contrato de Tools

Toda nueva integración con una API externa debe cumplir este contrato exacto:

```python
# tools/mi_integracion.py

TOOL_NAME = "nombre_de_la_tool"  # snake_case, único en el proyecto

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "nombre_de_la_tool",
        "description": "Descripción clara de qué hace. El LLM usa esto para decidir cuándo llamarla.",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "Descripción del parámetro"
                },
                "param2": {
                    "type": "integer",
                    "description": "Descripción del parámetro"
                }
            },
            "required": ["param1"]  # Solo los obligatorios
        }
    }
}

async def execute(params: dict, context: dict) -> dict:
    """
    params: argumentos enviados por el LLM
    context: {user_data, project_config, auth_header, user_lat, user_lng, ...}
    returns: siempre {"result": ..., "error": None} o {"result": None, "error": "mensaje"}
    """
    try:
        param1 = params.get("param1")
        # lógica de integración...
        return {"result": data, "error": None}
    except Exception as e:
        logger.error(f"[{TOOL_NAME}] Error: {e}", exc_info=True)
        return {"result": None, "error": str(e)}
```

**Reglas de las tools:**
- Son **stateless** — no guardan estado entre llamadas
- El `context` contiene todo lo que necesitan: credenciales, datos del usuario, configuración del proyecto
- Siempre retornan el formato `{"result": ..., "error": ...}`
- Usan `httpx.AsyncClient()` para llamadas HTTP

---

## 3. Cómo Agregar una Nueva Tool

**Paso 1** — Crear `tools/mi_proyecto.py` con el contrato de arriba.

**Paso 2** — El `ToolRegistry` la detecta automáticamente al arrancar. No hay que registrarla manualmente.

**Paso 3** — Agregar el nombre de la tool al YAML del proyecto:

```yaml
# projects/mi_proyecto.yaml
tools:
  - nombre_de_la_tool
```

**Paso 4** — Verificar que carga correctamente:

```bash
python -c "
from orchestrator.tool_registry import ToolRegistry
reg = ToolRegistry.for_project('mi_proyecto')
print(reg.list_tools())
"
```

---

## 4. Cómo Agregar un Nuevo Proyecto

**Paso 1** — Crear `projects/mi_proyecto.yaml`:

```yaml
project_id: mi_proyecto
assistant_name: "Nombre del Asistente"
greeting: "¡Hola! ¿En qué puedo ayudarte?"
system_prompt: |
  Eres un asistente especializado en...
tools:
  - mi_tool_1
  - mi_tool_2
llm:
  model: openai/gpt-4o-mini
  temperature: 0.4
  max_tokens: 800
```

**Paso 2** — Crear `tools/mi_proyecto.py` con las tools del proyecto.

**Paso 3** — Registrar en `main.py`:

```python
app.state.registries["mi_proyecto"] = ToolRegistry.for_project("mi_proyecto")
```

**Paso 4** — (Opcional) Crear interceptor para manejo local sin LLM. Ver sección 5.

---

## 5. Cómo Agregar un Interceptor

Los interceptores permiten que Lyra resuelva intenciones comunes **sin invocar al LLM**, ahorrando tokens y latencia.

**Cuándo crear un interceptor:** Cuando hay respuestas predecibles para intenciones específicas (búsquedas estándar, saludos, comandos de navegación, flujos transaccionales simples).

```python
# orchestrator/interceptors/mi_proyecto.py

from orchestrator.interceptors.manager import BaseInterceptor
from core.logger import get_logger

logger = get_logger(__name__)

class MiProyectoInterceptor(BaseInterceptor):

    async def pre_llm(
        self,
        intent: str,
        args: dict,
        context: dict
    ) -> dict | None:
        """
        Retorna un dict con la respuesta si puede resolverse localmente.
        Retorna None si el LLM debe manejarlo.
        """
        if intent == "buscar_producto":
            resultado = self._buscar_localmente(args)
            if resultado:
                return {
                    "reply": self._formatear_respuesta(resultado),
                    "bypass": True,
                    "properties": resultado
                }

        return None  # Sin match → el LLM toma el control

    async def post_execution(
        self,
        tool_name: str,
        tool_result: dict,
        context: dict
    ) -> dict:
        """
        Enriquece la respuesta después de que una tool se ejecutó.
        Útil para inyectar metadatos de UI (coordenadas, acciones de mapa, etc.)
        """
        return tool_result  # Sin modificaciones por defecto
```

**Registrar el interceptor en `manager.py`:**

```python
from orchestrator.interceptors.mi_proyecto import MiProyectoInterceptor

INTERCEPTORS = {
    "nexiservice": NexiServiceInterceptor(),
    "mi_proyecto": MiProyectoInterceptor(),  # ← agregar aquí
}
```

---

## 6. Cómo Agregar un Nuevo Canal de Mensajería

Si necesitas soportar un nuevo canal (SMS, Telegram, etc.):

**Paso 1** — Crear `services/nuevo_canal_service.py`:

```python
class NuevoCanalService:
    def __init__(self, db, config):
        self.db = db
        self.config = config

    async def process_message(self, sender_id: str, content: str) -> str:
        # lógica específica del canal...
```

**Paso 2** — Crear `api/routers/nuevo_canal.py` (thin router):

```python
@router.post("/nuevo-canal/webhook")
async def webhook(
    request: Request,
    svc: NuevoCanalService = Depends(get_nuevo_canal_service)
):
    # extraer datos del request
    return await svc.process_message(sender_id, content)
```

**Paso 3** — Agregar la dependencia en `api/dependencies.py` y montar el router en `main.py`.

---

## 7. Checklist de Pull Request

Antes de hacer merge, verifica:

- [ ] Todas las funciones tienen **Type Hints**
- [ ] No hay ningún `print()` — usar `get_logger(__name__)`
- [ ] Las funciones con I/O son `async def` y usan `httpx`
- [ ] Si es una nueva tool: tiene `TOOL_NAME`, `TOOL_SCHEMA` y `execute(params, context)`
- [ ] Si es un nuevo router: delega completamente al servicio, sin lógica interna
- [ ] Si es un nuevo servicio: recibe sus dependencias en `__init__` (no importa globals)
- [ ] Sin imports circulares: `python -c "import main" && echo OK`
- [ ] Si se agregaron variables de entorno: documentadas en `.env.example` y en el README

---

## 8. Convenciones de Commits

Usamos el formato de Commits Convencionales:

| Prefijo | Uso |
|---|---|
| `feat:` | Nueva funcionalidad |
| `fix:` | Corrección de bug |
| `refactor:` | Cambio de código sin nueva funcionalidad ni fix |
| `docs:` | Cambios en documentación |
| `chore:` | Mantenimiento, dependencias, configuración |
| `perf:` | Mejora de rendimiento |

**Ejemplos:**
```
feat: add rentus appointment scheduling tool
fix: correct speech normalization for Popayán addresses
refactor: extract twilio constants to dedicated module
docs: update ARCHITECTURE.md with interceptor pattern
```

---

## 9. Reporte de Errores

Al reportar un bug, incluye:

1. El `project_id` y canal donde ocurrió (Chat / WhatsApp / Voz)
2. El mensaje del usuario que lo desencadenó
3. El fragmento de log relevante de `/logs/`
4. Si es reproducible, los pasos exactos para reproducirlo