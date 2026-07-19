# Lyra AI — Arquitectura Técnica

Este documento describe las decisiones de diseño, la estructura de capas y los flujos de datos del motor de orquestación **Lyra AI**.

---

## 1. Filosofía de Diseño

Lyra está construido sobre tres principios que guían cada decisión arquitectónica:

**Local primero, LLM como respaldo.** El LLM externo es caro y lento. Lyra resuelve todo lo que puede de forma determinista y local. Solo delega al LLM cuando la tarea lo requiere genuinamente.

**Proyectos como configuración, no como código.** Añadir un nuevo caso de uso no debe tocar el núcleo del motor. Todo lo que define la "personalidad" de un proyecto vive en un archivo YAML.

**Capas con responsabilidades estrictas.** Cada capa sabe qué hace y qué no hace. Los routers no tienen lógica de negocio. Los servicios no conocen HTTP. Las tools son stateless.

---

## 2. El Motor Híbrido — Cerebro Rápido + Cerebro Lento

El componente más importante de la arquitectura de Lyra es la distinción entre resolución local y resolución por LLM.

```
┌─────────────────────────────────────────────────────────────────┐
│                     MOTOR HÍBRIDO DE LYRA                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Consulta del usuario                                            │
│        │                                                         │
│        ▼                                                         │
│  ┌─────────────────┐                                            │
│  │  Intent Router  │  ← Regex + Keywords (determinista, local)  │
│  │ intent_router.py│    0 tokens · ~1ms                         │
│  └────────┬────────┘                                            │
│           │ intent + args                                        │
│           ▼                                                      │
│  ┌─────────────────┐                                            │
│  │   Interceptor   │  ← Lógica de negocio local por proyecto    │
│  │  (por proyecto) │    Respuestas hardcoded · Markdown · APIs  │
│  └────────┬────────┘                                            │
│           │                                                      │
│     ┌─────┴──────┐                                              │
│     │            │                                               │
│  BYPASS       SIN MATCH                                          │
│  (respuesta   (el intent es                                      │
│   directa)     ambiguo)                                          │
│                  │                                               │
│                  ▼                                               │
│         ┌────────────────┐                                       │
│         │  LLM Externo   │  ← OpenRouter / OpenAI               │
│         │  (auxiliar)    │    Solo cuando Lyra no puede solo     │
│         └────────────────┘                                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

Para NexiService, el **70–80% de las consultas transaccionales** (buscar negocios, ver servicios, agendar) se resuelven en la capa local sin consumir tokens.

---

## 3. Capas del Sistema

### Capa 1 — Transporte (`api/`)

**Responsabilidad única:** Recibir requests HTTP, validar el schema con Pydantic, delegar al servicio correspondiente y devolver la respuesta.

No contiene lógica de negocio. No accede a la base de datos directamente. No conoce qué hace el orquestador.

```
api/
├── routers/main.py          → POST /chat
├── routers/freeswitch.py    → WS /freeswitch/audio · /recording · /health
├── routers/whatsapp.py      → GET|POST /whatsapp
├── routers/admin/           → Panel de administración
├── schemas/chat.py          → ChatRequest, ChatResponse
├── schemas/common.py        → HealthResponse, ErrorResponse
├── dependencies.py          → Inyección de DB, Services, Registries
└── middleware.py            → Rate limiting
```

### Capa 2 — Servicios (`services/`)

**Responsabilidad:** Lógica de negocio por canal de comunicación. Gestiona el estado de la conversación, el historial y las particularidades de cada protocolo (HTTP, TwiML, WhatsApp).

```
services/
├── chat_service.py          → Historial · trust level · orquestación del agente
├── whatsapp_service.py      → Máquina de estados FSM · Meta Graph API
├── voice/                   → Lyra Voice V2: motor conversacional streaming
│   ├── runtime.py           → Composición full-duplex por llamada
│   ├── transport.py         → WS mod_audio_stream (frames + playback)
│   ├── stt_stream.py        → Deepgram streaming (parciales + keywords)
│   ├── endpointing.py       → Endpointing híbrido acústico + semántico
│   ├── nlu.py               → Extracción de spans (structured outputs)
│   ├── orchestrator.py      → FSM de negocio de la llamada
│   ├── tts_stream.py        → edge-tts incremental por oración
│   ├── aec.py / barge_in.py → Eco servidor + interrupciones reales
│   └── recorder.py          → Grabación mezclada server-side
└── telephony/               → Contratos de negocio: sesiones, backend, ESL
```

**Nota sobre sesiones de llamada:** se guardan en `session_store` (memoria en
desarrollo, Redis vía `VOICE_SESSION_STORE=redis` en producción multi-worker).

### Capa 3 — Orquestación (`orchestrator/`)

**Responsabilidad:** Coordinar el ciclo de vida del agente. Es agnóstico al proyecto — recibe un contexto y una configuración de herramientas, y decide si resolver localmente o invocar al LLM.

```
orchestrator/
├── tool_runner.py       → run_agent_loop · árbitro local vs LLM
├── tool_registry.py     → Registro dinámico de tools por proyecto
├── intent_router.py     → Clasificador determinista (Regex + Keywords)
├── context_builder.py   → Construcción del system prompt y contexto
├── memory_manager.py    → Persistencia de sesiones y perfiles en MySQL
├── response_engine.py   → Post-procesamiento de respuestas del LLM
└── interceptors/
    ├── manager.py           → Orquestador de interceptores pre/post LLM
    └── nexiservice.py       → Bypass local para NexiService
```

### Capa 4 — Herramientas (`tools/`)

**Responsabilidad:** Integración stateless con APIs externas. Cada tool es independiente y no mantiene estado propio.

Cada archivo expone el mismo contrato:

```python
TOOL_NAME: str           # Identificador único
TOOL_SCHEMA: dict        # Schema OpenAI function calling
async def execute(params: dict, context: dict) -> dict  # Ejecutor
```

```
tools/
├── nexiservice.py   → search_businesses · fly_to_business · comparaciones · reseñas
├── rentus.py        → propiedades · agendamiento · geocodificación
├── intellitaxi.py   → órdenes de despacho · coordenadas · Laravel backend
└── shared/
    └── utils.py     → normalize_text · haversine · parse_date · WEEKDAY_MAP · ...
```

### Capa 5 — Núcleo (`core/`)

**Responsabilidad:** Recursos base compartidos por todas las capas. Sin lógica de negocio.

```
core/
├── config.py        → Variables de entorno tipadas (pydantic-settings)
├── database.py      → Pool de conexiones MySQL
├── llm_engine.py    → Cliente LLM async · OpenRouter / OpenAI · recuperación de errores
├── logger.py        → Logging centralizado con rotación de archivos (10MB × 5)
├── pusher.py        → Eventos en tiempo real hacia el frontend
└── voice_engine.py  → TTS con edge-tts
```

---

## 4. Patrones Implementados

### A. Inyección de Dependencias

Todas las dependencias (DB, LLM, Registries, Services) se inicializan en el `lifespan` de FastAPI y se inyectan via `Depends()`. Ningún servicio importa recursos globales directamente.

```python
# main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm_engine = init_llm()
    app.state.registries = {
        "nexiservice": ToolRegistry.for_project("nexiservice"),
        "rentus":      ToolRegistry.for_project("rentus"),
        "intellitaxi": ToolRegistry.for_project("intellitaxi"),
    }
    yield

# api/dependencies.py
async def get_chat_service(
    db=Depends(get_db),
    config=Depends(get_settings),
    llm=Depends(get_llm)
) -> ChatService:
    return ChatService(db, config, llm)
```

**Por qué:** Permite testear cada servicio de forma aislada con mocks sin levantar infraestructura real.

### B. Tool Registry con Auto-Discovery

En lugar de un bloque `if/elif` gigante para despachar herramientas, el `ToolRegistry` escanea automáticamente el módulo `tools/{project_id}.py` al arrancar y registra cada función que cumpla el contrato.

```python
# orchestrator/tool_registry.py
class ToolRegistry:
    @classmethod
    def for_project(cls, project_id: str) -> "ToolRegistry":
        registry = cls()
        module = importlib.import_module(f"tools.{project_id}")
        for name, obj in inspect.getmembers(module):
            if hasattr(obj, "TOOL_NAME") and hasattr(obj, "execute"):
                registry.register(obj)
        return registry
```

**Por qué:** Añadir una nueva herramienta no requiere tocar el orquestador. Solo crear la función con el contrato correcto.

### C. Patrón Interceptor (AOP-lite)

Los interceptores permiten inyectar lógica específica de proyecto sin contaminar el orquestador general.

**Pre-LLM:** Si el interceptor detecta una intención conocida, devuelve la respuesta directamente. El LLM nunca se invoca. Esto es el núcleo del ahorro de tokens.

**Post-execution:** Después de que una tool se ejecuta, el interceptor puede enriquecer la respuesta con metadatos de UI (coordenadas de mapa, acciones de frontend, estados visuales).

```python
# Flujo en tool_runner.py
intercepted = await interceptor_manager.run_pre_llm_interceptors(
    project_id, intent_name, intent_args, context
)
if intercepted:
    return {**intercepted, "final_data": final_data}  # BYPASS TOTAL

# ... si no hubo interceptación, llama al LLM
```

### D. LegacyToolAdapter

Durante la migración al contrato nativo de tools, las integraciones antiguas se envuelven en un adaptador sin modificar su código original.

```python
class LegacyToolAdapter:
    def __init__(self, name, func, schema):
        self.TOOL_NAME = name
        self._func = func
        self.TOOL_SCHEMA = schema

    async def execute(self, params, context):
        try:
            return {"result": await self._func(**params), "error": None}
        except Exception as e:
            return {"result": None, "error": str(e)}
```

**Por qué:** Permite migración gradual sin riesgo de romper herramientas en producción.

---

## 5. Flujos de Datos Detallados

### Flujo de Chat (NexiService / Rentus)

```
1. Usuario envía mensaje
   POST /chat  →  api/routers/main.py

2. Validación y delegación
   ChatRequest (Pydantic)  →  ChatService.process_message()

3. Contexto y memoria
   ChatService recupera historial de MySQL (memory_manager)
   Calcula trust_level del usuario
   Guarda el mensaje del usuario

4. Clasificación local (0 tokens)
   intent_router.detect_intent(mensaje)
   → intent: "search_businesses", args: {city: "Popayán", category: "restaurantes"}

5. Interceptación local
   interceptor_manager.run_pre_llm_interceptors()
   → NexiServiceInterceptor.pre_llm()
   → Construye respuesta Markdown localmente
   → BYPASS: retorna sin llamar al LLM (~70-80% de casos)

6. [Si no hubo bypass] LLM Externo
   run_agent_loop(engine, messages, registry, context)
   → LLMEngine.complete(messages, tool_schemas)
   → LLM devuelve tool_call: {name: "search_businesses", args: {...}}
   → registry.execute("search_businesses", args, context)
   → Tool llama a la API externa, retorna resultados
   → LLM genera respuesta final con los resultados

7. Post-processing
   interceptor_manager.run_post_interceptors()
   → Enriquece con map_center, properties, voice_action

8. Persistencia
   ChatService guarda respuesta en MySQL

9. Respuesta
   ChatResponse(reply, conversation_id, trust_level, latency_ms, properties, map_center)
```

### Flujo de Voz (IntelliTaxi — Lyra Voice V2)

```
1. FreeSWITCH abre el WebSocket full-duplex
   lyra_stream.lua → uuid_audio_stream → WS /freeswitch/audio

2. Audio entrante continuo (PCM16 8k)
   frame → AEC (eco del TTS cancelado) → Deepgram streaming
   (nunca se descarta audio del usuario: full-duplex real)

3. Comprensión del turno
   parciales STT → endpointing híbrido (acústico + semántico)
   → NLU structured-output: {intent, pickup_span, landmark_reference, ...}
   → el span crudo va a core/location_match + core/geocoder_service
     (el LLM extrae, el resolver resuelve — nunca al revés)

4. Máquina de estados de negocio (idéntica a V1)
   waiting_origin → confirming_origin / waiting_geo_context
   → creating_service → finished

5. Respuesta hablada
   texto → normalización (números/direcciones) → edge-tts por oración
   → playback streamAudio con pacing (~200 ms/chunk)
   → si el usuario interrumpe con contenido real: se cancela el playback
     y el historial se trunca a lo realmente escuchado

6. Cierre
   backend Laravel crea el servicio (idempotente por call_uuid)
   → WhatsApp de confirmación → ESL uuid_kill
   → grabación mezclada en /freeswitch/recording/{uuid}.wav
```

---

## 6. Gestión de Configuración

Lyra usa un modelo de dos niveles:

**Nivel 1 — Global (`.env`):** Credenciales, hosts, puertos. Gestionado por `core/config.py` con `pydantic-settings`. Tipado estricto, sin strings mágicos.

**Nivel 2 — Por proyecto (`projects/*.yaml`):** Define la "personalidad" del agente. System prompt, herramientas habilitadas, parámetros del LLM (temperatura, max_tokens), nombre del asistente, greeting inicial. Este archivo es la única cosa que cambia entre proyectos.

---

## 7. Observabilidad

El logging está centralizado en `core/logger.py` con rotación automática (10MB por archivo, hasta 5 archivos en `/logs`).

Cada componente usa su propio sub-logger con jerarquía estandarizada:

```python
from core.logger import get_logger
logger = get_logger(__name__)
# Produce: lyra.services.chat, lyra.orchestrator.tool_runner, lyra.tools.nexiservice
```

Los logs críticos incluyen `exc_info=True` para stack traces completos en producción.

---

## 8. Decisiones Arquitectónicas (ADRs)

### ADR-001: Lógica de negocio en `services/`, no en routers

**Contexto:** Los routers originales contenían cientos de líneas de lógica de negocio mezclada con el manejo HTTP.

**Decisión:** Thin Routers. Toda lógica va a `services/`. El router solo valida el schema y delega.

**Consecuencia:** Los servicios son testeables de forma aislada con mocks. Los routers son predecibles y uniformes.

### ADR-002: LLM externo como auxiliar, no como motor principal

**Contexto:** Usar el LLM para cada consulta introduce latencia de red y costo por token.

**Decisión:** Motor híbrido. `intent_router` + interceptores resuelven localmente todo lo que pueden. El LLM solo se activa para intenciones ambiguas o conversaciones complejas.

**Consecuencia:** Para NexiService, ~70-80% de las consultas no consumen tokens. Latencia promedio significativamente menor.

### ADR-003: LegacyToolAdapter para migración gradual

**Contexto:** Migrar todas las tools al nuevo contrato simultáneamente es de alto riesgo en producción.

**Decisión:** `LegacyToolAdapter` envuelve tools antiguas sin modificarlas. Permite migración herramienta por herramienta.

**Consecuencia:** Cero downtime durante la migración. Las tools antiguas funcionan igual, las nuevas usan el contrato nativo.

### ADR-004: Sesiones Twilio en memoria

**Contexto:** Las sesiones de llamada de IntelliTaxi necesitan estado compartido entre webhooks.

**Decisión:** `dict` en memoria con `asyncio.Lock` por sesión.

**Consecuencia:** Funciona correctamente con un solo worker de Uvicorn. **En producción con múltiples workers (Gunicorn), esto debe migrarse a Redis.** Pendiente como mejora futura.