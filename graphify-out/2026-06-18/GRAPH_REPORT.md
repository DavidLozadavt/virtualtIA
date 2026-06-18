# Graph Report - virtualtIA  (2026-06-08)

## Corpus Check
- 83 files · ~115,743 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1755 nodes · 3663 edges · 94 communities (88 shown, 6 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 243 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `5142e1b6`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Twilio Voice Router|Twilio Voice Router]]
- [[_COMMUNITY_Geocoding Types & Candidates|Geocoding Types & Candidates]]
- [[_COMMUNITY_STT Enhancement & Local Search|STT Enhancement & Local Search]]
- [[_COMMUNITY_Google Geocoding Service|Google Geocoding Service]]
- [[_COMMUNITY_Browser Voice & TTS|Browser Voice & TTS]]
- [[_COMMUNITY_App Config & Logging|App Config & Logging]]
- [[_COMMUNITY_Scheduling & Shared Utils|Scheduling & Shared Utils]]
- [[_COMMUNITY_Popayan Geodata Index|Popayan Geodata Index]]
- [[_COMMUNITY_Nexiservice Booking Interceptor|Nexiservice Booking Interceptor]]
- [[_COMMUNITY_Admin Session Management|Admin Session Management]]
- [[_COMMUNITY_Nexiservice Tools|Nexiservice Tools]]
- [[_COMMUNITY_Admin Config Management|Admin Config Management]]
- [[_COMMUNITY_Conversation Repair|Conversation Repair]]
- [[_COMMUNITY_Voice Session State|Voice Session State]]
- [[_COMMUNITY_Booking Flow Tests|Booking Flow Tests]]
- [[_COMMUNITY_SENA Learning Tools|SENA Learning Tools]]
- [[_COMMUNITY_Main Router & Database|Main Router & Database]]
- [[_COMMUNITY_Tool Adapter & Registry|Tool Adapter & Registry]]
- [[_COMMUNITY_WhatsApp Channel Router|WhatsApp Channel Router]]
- [[_COMMUNITY_Address Normalization Core|Address Normalization Core]]
- [[_COMMUNITY_Tool Runner Utilities|Tool Runner Utilities]]
- [[_COMMUNITY_LLM Engine & Middleware|LLM Engine & Middleware]]
- [[_COMMUNITY_Rentus Property Tools|Rentus Property Tools]]
- [[_COMMUNITY_Streaming STT Pipeline|Streaming STT Pipeline]]
- [[_COMMUNITY_IntelliTaxi Tools|IntelliTaxi Tools]]
- [[_COMMUNITY_Orchestrator Tool Runner|Orchestrator Tool Runner]]
- [[_COMMUNITY_API Dependency Injection|API Dependency Injection]]
- [[_COMMUNITY_Interceptor Helpers|Interceptor Helpers]]
- [[_COMMUNITY_Navigation & UI Tools|Navigation & UI Tools]]
- [[_COMMUNITY_Adaptive Endpoint Control|Adaptive Endpoint Control]]
- [[_COMMUNITY_Context Builder & Prompt|Context Builder & Prompt]]
- [[_COMMUNITY_Response Engine & Templates|Response Engine & Templates]]
- [[_COMMUNITY_Architecture Overview Docs|Architecture Overview Docs]]
- [[_COMMUNITY_Lyra Hybrid Engine Design|Lyra Hybrid Engine Design]]
- [[_COMMUNITY_LLM Utility Functions|LLM Utility Functions]]
- [[_COMMUNITY_Geocoding Architecture Docs|Geocoding Architecture Docs]]
- [[_COMMUNITY_Intent Router|Intent Router]]
- [[_COMMUNITY_SENA Interceptors|SENA Interceptors]]
- [[_COMMUNITY_Audio Quality Profiling|Audio Quality Profiling]]
- [[_COMMUNITY_Streaming Pipeline Entry|Streaming Pipeline Entry]]
- [[_COMMUNITY_Interceptor Manager|Interceptor Manager]]
- [[_COMMUNITY_Colombian Address Extraction|Colombian Address Extraction]]
- [[_COMMUNITY_Chat Service|Chat Service]]
- [[_COMMUNITY_Geocoding Overview & Rentus|Geocoding Overview & Rentus]]
- [[_COMMUNITY_Voice STT Tuning Docs|Voice STT Tuning Docs]]
- [[_COMMUNITY_Geocoding Phase 1 Plan|Geocoding Phase 1 Plan]]
- [[_COMMUNITY_Response Generation|Response Generation]]
- [[_COMMUNITY_Project Configurations|Project Configurations]]
- [[_COMMUNITY_Appointment Utilities|Appointment Utilities]]
- [[_COMMUNITY_TTS Audio Router|TTS Audio Router]]
- [[_COMMUNITY_Claude Dev Settings|Claude Dev Settings]]
- [[_COMMUNITY_Model Path Config|Model Path Config]]
- [[_COMMUNITY_Barge-In Interruption|Barge-In Interruption]]
- [[_COMMUNITY_Booking State|Booking State]]
- [[_COMMUNITY_SENA Project Config|SENA Project Config]]
- [[_COMMUNITY_City Data Cache|City Data Cache]]
- [[_COMMUNITY_Channel Guide Docs|Channel Guide Docs]]
- [[_COMMUNITY_PR Checklist Docs|PR Checklist Docs]]
- [[_COMMUNITY_Twilio Init|Twilio Init]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 100|Community 100]]

## God Nodes (most connected - your core abstractions)
1. `get_connection()` - 64 edges
2. `str` - 54 edges
3. `process_speech()` - 43 edges
4. `resolve_location_entity()` - 41 edges
5. `AudioQualityProfile` - 40 edges
6. `ConversationMemory` - 34 edges
7. `decide()` - 31 edges
8. `str` - 29 edges
9. `Decision` - 29 edges
10. `strip_accents()` - 29 edges

## Surprising Connections (you probably didn't know these)
- `Geocoding State Machine (NORMALIZING→CACHE_LOOKUP→RESOLVING→CONTEXT_GATHERING→CONFIRMING→RESOLVED)` --semantically_similar_to--> `Twilio Sessions in Memory with asyncio.Lock — ADR-004`  [INFERRED] [semantically similar]
  docs/geocoding/01-architecture.md → ARCHITECTURE.md
- `Tool Contract Definition (CONTRIBUTING guide)` --semantically_similar_to--> `Tool Contract (TOOL_NAME, TOOL_SCHEMA, execute)`  [INFERRED] [semantically similar]
  CONTRIBUTING.md → ARCHITECTURE.md
- `Settings` --uses--> `WhatsappService`  [INFERRED]
  api/dependencies.py → services/whatsapp_service.py
- `Request` --uses--> `WhatsappService`  [INFERRED]
  api/dependencies.py → services/whatsapp_service.py
- `ChatService` --uses--> `WhatsappService`  [INFERRED]
  api/dependencies.py → services/whatsapp_service.py

## Import Cycles
- 1-file cycle: `main.py -> main.py`
- 1-file cycle: `core/llm_utils.py -> core/llm_utils.py`
- 2-file cycle: `api/middleware.py -> main.py -> api/middleware.py`

## Hyperedges (group relationships)
- **Geocoding Pipeline Core Components (Phase 1)** — geocoding_02_geocoder_service, geocoding_02_address_utils, geocoding_01_geo_types, geocoding_01_location_cache, geocoding_02_migration_003 [EXTRACTED 1.00]
- **All Lyra Project YAML Configurations** — projects_intellitaxi_yaml, projects_nexiservice_yaml, projects_rentus_yaml, projects_schoolsena_yaml [EXTRACTED 1.00]
- **Lyra Hybrid Engine Processing Flow** — architecture_intent_router, architecture_interceptor_pattern, architecture_tool_registry, architecture_orchestrator_layer, architecture_llm_as_fallback [EXTRACTED 1.00]

## Communities (94 total, 6 thin omitted)

### Community 0 - "Twilio Voice Router"
Cohesion: 0.20
Nodes (11): _build_alias_index(), _ensure_index(), get_stats(), tools/popayan_geodata.py — Base de conocimiento geográfico HIPERDETALLADA de Pop, Construye el índice maestro de aliases para búsqueda local eficiente., Construye el índice maestro de aliases para búsqueda local eficiente., Resuelve texto libre del usuario a un nombre canónico (sin coordenadas)., Resuelve texto libre del usuario a un nombre canónico (sin coordenadas). (+3 more)

### Community 1 - "Geocoding Types & Candidates"
Cohesion: 0.07
Nodes (69): normalize_colombian_address(), Normaliza al formato colombiano estándar.     'carrera cuarta a el # 17 b 28' →, Normaliza al formato colombiano estándar.     'carrera cuarta a el # 17 b 28' →, GeoCandidate, GeoResolution, in_urban_bbox(), in_wide_bbox(), LocationType (+61 more)

### Community 2 - "STT Enhancement & Local Search"
Cohesion: 0.14
Nodes (16): bool, Detecta si el texto parcial indica intención de interrumpir.         Se usa con, _build_phonetic_repair_index(), combined_score(), fuzzy_match_location(), phonetic_key(), core/stt_enhancer.py — Motor de mejora de STT para audio telefónico colombiano., Quita tildes para comparaciones fonéticas. (+8 more)

### Community 3 - "Google Geocoding Service"
Cohesion: 0.06
Nodes (34): bool, float, str, bool, int, str, int, str (+26 more)

### Community 4 - "Browser Voice & TTS"
Cohesion: 0.14
Nodes (19): _clean_for_tts(), _edge_tts_sync_bytes(), _is_gibberish(), bool, bytes, float, str, core/voice_engine.py — Centralized Speech-to-Text and Text-to-Speech engine for (+11 more)

### Community 5 - "App Config & Logging"
Cohesion: 0.10
Nodes (30): ChatResponse, get_conversation_history(), get_conversation_message_count(), get_or_create_conversation(), get_or_create_user(), int, str, orchestrator/memory_manager.py — CRUD historial en MySQL, compresión de contexto (+22 more)

### Community 6 - "Scheduling & Shared Utils"
Cohesion: 0.14
Nodes (26): build_schedule_clarification(), extract_session_user_id(), extract_tastes_from_history(), format_time_24h(), is_generic_query(), looks_like_iso_date(), looks_like_time_24h(), match_property_id_in_reply() (+18 more)

### Community 7 - "Popayan Geodata Index"
Cohesion: 0.17
Nodes (13): haversine(), Distancia en km entre dos coordenadas GPS (fórmula de Haversine).      Consoli, get_nearby_landmarks(), _haversine(), infer_barrio_from_coords(), float, Distancia haversine en kilómetros., Distancia haversine en kilómetros. (+5 more)

### Community 8 - "Nexiservice Booking Interceptor"
Cohesion: 0.20
Nodes (10): _build_local_match_index(), Búsqueda local en el catálogo de barrios/landmarks de Popayán     (popayan_geod, Búsqueda local en el catálogo de barrios/landmarks de Popayán     (popayan_geod, _try_local_match(), extract_address(), Pipeline unificado de extracción. role = "origen" | "destino"     Retorna (cano, Pipeline unificado de extracción. role = "origen" | "destino"     Retorna (cano, Pipeline unificado de extracción. role = "origen" | "destino"     Retorna (cano (+2 more)

### Community 9 - "Admin Session Management"
Cohesion: 0.12
Nodes (21): delete_session(), export_sessions(), list_sessions(), Export sessions (basic JSON export)., Get full session with messages., Delete a session and its messages., List conversations (sessions) with pagination., Session aggregate stats. (+13 more)

### Community 10 - "Nexiservice Tools"
Cohesion: 0.14
Nodes (33): _clean_search_query(), confirm_appointment(), fly_to_business(), get_business_availability(), get_business_mission_vision(), get_business_reviews(), get_business_services(), get_professional_info() (+25 more)

### Community 11 - "Admin Config Management"
Cohesion: 0.09
Nodes (30): clear_cache(), ConfigUpdate, create_version(), get_config(), _get_current_version(), get_status(), health_incidents(), list_versions() (+22 more)

### Community 12 - "Conversation Repair"
Cohesion: 0.09
Nodes (19): ConversationRepair, _extract_partial_location(), get_repair_message(), infer_intent(), float, str, core/conversation_repair.py — Motor de reparación conversacional inteligente par, Infiere la intención del usuario con scores de probabilidad.          Retorna: (+11 more)

### Community 13 - "Voice Session State"
Cohesion: 0.15
Nodes (37): AudioQualityProfile, bool, bytes, float, int, Request, AsyncClient, ConversationMemory (+29 more)

### Community 14 - "Booking Flow Tests"
Cohesion: 0.14
Nodes (26): fail(), get_all_businesses(), get_first_tercero(), get_professionals_for_service(), get_services_for_business(), info(), _normalize(), ok() (+18 more)

### Community 15 - "SENA Learning Tools"
Cohesion: 0.09
Nodes (41): _denied(), _entity_type(), _get_roles(), post_execution_interceptor(), pre_llm_interceptor(), Obtiene el horario del instructor y deduplica por código de ficha,     mostrand, _run(), _run_fichas_asignadas() (+33 more)

### Community 16 - "Main Router & Database"
Cohesion: 0.19
Nodes (14): BaseModel, str, Configura un logger estándar con salida a consola y archivo rotativo., setup_logger(), load_project_config(), health(), list_projects(), gateway/router.py — FastAPI endpoints: POST /chat, GET /health, GET /projects. (+6 more)

### Community 17 - "Tool Adapter & Registry"
Cohesion: 0.22
Nodes (10): _find_similar_places(), fuzzy_search(), _normalize_text(), int, Normalización robusta: minúsculas, sin tildes, sin especiales, espacios comprimi, Normalización robusta: minúsculas, sin tildes, sin especiales, espacios comprimi, Encuentra lugares con nombres similares usando bigrams. Umbral Jaccard = 0.28., Encuentra lugares con nombres similares usando bigrams. Umbral Jaccard = 0.28. (+2 more)

### Community 18 - "WhatsApp Channel Router"
Cohesion: 0.19
Nodes (22): bool, int, str, normalize_address(), Estandariza nomenclatura (Calle → Cl, etc.), Estandariza nomenclatura (Calle → Cl, etc.), clean_map_location(), _create_wp_service() (+14 more)

### Community 19 - "Address Normalization Core"
Cohesion: 0.20
Nodes (22): _clean_stt_text(), _correct_speech(), extract_datetime_local(), extract_datetime_with_llm(), _geocode_cache_get(), _geocode_cache_set(), _in_popayan_bbox(), _is_correction_request() (+14 more)

### Community 20 - "Tool Runner Utilities"
Cohesion: 0.12
Nodes (19): _build_schedule_clarification(), _extract_session_user_id(), _extract_tastes_from_history(), _is_generic_query(), _match_property_id_in_reply(), _normalize(), bool, str (+11 more)

### Community 21 - "LLM Engine & Middleware"
Cohesion: 0.05
Nodes (79): _base_decision(), _best_for_entity(), _build_catalog(), _content_tokens(), decide(), _Entity, _has_content(), _is_all_filler() (+71 more)

### Community 22 - "Rentus Property Tools"
Cohesion: 0.11
Nodes (20): get_property_detail(), GetPropertyDetailTool, _parse_properties_from_response(), float, int, str, tools/rentus.py — Tool functions for the Rentus project., Obtiene el detalle completo de una propiedad por ID. (+12 more)

### Community 23 - "Streaming STT Pipeline"
Cohesion: 0.32
Nodes (6): Request, BackgroundTasks, MessageCache, receive_message(), receive_universal_message(), verify_webhook()

### Community 24 - "IntelliTaxi Tools"
Cohesion: 0.15
Nodes (14): consultar_conductores_disponibles(), ConsultarConductoresTool, geocodificar_direccion(), GeocodificarDireccionTool, str, tools/intellitaxi.py — Tool functions for the IntelliTaxi project.  Integrates, Consulta conductores disponibles en una zona., Geocodifica una dirección en Popayán y devuelve coordenadas.          Pipeline (+6 more)

### Community 25 - "Orchestrator Tool Runner"
Cohesion: 0.10
Nodes (21): _inject_ids_into_titles(), orchestrator/tool_runner.py — Agent loop con límite estricto de herramientas., Recupera tanto la ciudad como la categoría de la última búsqueda desde los tool, Recupera la lista de negocios mencionados en el historial.     Busca tanto en r, Busca en el historial menciones de servicios, profesionales o negocios en contex, Inyecta el ancla [ID: X] en el título para el siguiente turno., Recupera la ciudad sugerida en el último mensaje de la herramienta o del asisten, _recover_last_appointment_entities() (+13 more)

### Community 26 - "API Dependency Injection"
Cohesion: 0.23
Nodes (6): bool, str, Almacenamiento de sesiones en memoria.     Reemplazar por RedisSessionStore cua, SessionStore, WhatsappService, WpSession

### Community 27 - "Interceptor Helpers"
Cohesion: 0.08
Nodes (24): 1. Filosofía de Diseño, 2. El Motor Híbrido — Cerebro Rápido + Cerebro Lento, 3. Capas del Sistema, 4. Patrones Implementados, 5. Flujos de Datos Detallados, 6. Gestión de Configuración, 7. Observabilidad, 8. Decisiones Arquitectónicas (ADRs) (+16 more)

### Community 28 - "Navigation & UI Tools"
Cohesion: 0.15
Nodes (14): navigate_to_company(), str, tools/navigation.py — Herramientas para la navegación programática en la UI., Activa la navegación automática hacia el perfil de una empresa específica., get_general_info(), open_business_web(), bool, float (+6 more)

### Community 29 - "Adaptive Endpoint Control"
Cohesion: 0.14
Nodes (18): bool, float, int, str, GeographicMatcher, _haversine(), _is_in_popayan(), services/twilio/navigation.py — Parser de navegación y matching geográfico para (+10 more)

### Community 30 - "Context Builder & Prompt"
Cohesion: 0.25
Nodes (8): _extract_session_today(), date, Extrae la fecha base de la sesión para resolver expresiones relativas., Normaliza fecha/hora preferidas desde args del LLM o el historial reciente., _resolve_schedule_datetime(), extract_session_today(), Extrae la fecha base de la sesión., date

### Community 31 - "Response Engine & Templates"
Cohesion: 0.05
Nodes (75): _find_anchored_id_in_messages(), _is_generic_query(), _normalize(), _normalize_time(), orchestrator/interceptors/helpers.py ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Uti, Recupera la lista de negocios del último resultado de herramienta en el historia, Recupera la categoría y ciudad de la última búsqueda de negocios., Reconstruye el contexto completo de una reserva en curso desde el historial. (+67 more)

### Community 32 - "Architecture Overview Docs"
Cohesion: 0.14
Nodes (16): API Layer (api/), Core Layer (core/) — Shared Resources, Dependency Injection via FastAPI Depends, Lyra AI — Generative AI Orchestration Engine, Services Layer (services/), Thin Routers — ADR-001, Twilio Sessions in Memory with asyncio.Lock — ADR-004, Geocoding State Machine (NORMALIZING→CACHE_LOOKUP→RESOLVING→CONTEXT_GATHERING→CONFIRMING→RESOLVED) (+8 more)

### Community 33 - "Lyra Hybrid Engine Design"
Cohesion: 0.15
Nodes (15): Motor Híbrido — Cerebro Rápido + Cerebro Lento, Intent Router (Regex + Keywords Classifier), Interceptor Pattern (AOP-lite Pre/Post LLM), LegacyToolAdapter — Gradual Migration Pattern, LLM as Auxiliary Fallback — ADR-002, Orchestrator Layer (orchestrator/), Tool Contract (TOOL_NAME, TOOL_SCHEMA, execute), Tool Registry with Auto-Discovery (+7 more)

### Community 34 - "LLM Utility Functions"
Cohesion: 0.29
Nodes (7): geocode_local(), parse_trip_locations(), str, Geocodifica una ubicación en la base de datos local de Popayán.      Orden de, Geocodifica una ubicación en la base de datos local de Popayán.      Orden de, Extrae posibles origen y destino desde texto libre de un usuario de taxi., Extrae posibles origen y destino desde texto libre de un usuario de taxi.

### Community 35 - "Geocoding Architecture Docs"
Cohesion: 0.14
Nodes (17): Auto-Accept Rules (ROOFTOP+RANGE_INTERPOLATED in POPAYAN_URBAN_BBOX), enriched_query Construction (query_base + alias_text by type), geo_human_aliases — Human Truth Table (Phase 2, deferred), core/geo_types.py — LocationType, ResolutionStatus, GeoCandidate, GeoResolution, location_cache — Technical Truth Table (canonical_query key), POPAYAN_URBAN_BBOX and POPAYAN_BBOX_WIDE Thresholds, Post-Resolution Verification (formatted_address must contain query numbers), core/geocoder_service.py — run_pipeline, _google_get_candidates, _nominatim_get_candidates, _decide (+9 more)

### Community 36 - "Intent Router"
Cohesion: 0.12
Nodes (25): _build_hint_vocab(), _get_contextual_hints(), Genera hints de vocabulario FOCALIZADOS según el estado (máx ~15 términos)., Genera hints de vocabulario FOCALIZADOS según el estado (máx ~15 términos)., Construye la cadena de hints model-aware desde los catálogos (cacheada)., Genera hints de vocabulario según el estado y el modelo STT.      - Captura (w, Repara la grafía de nombres de lugar en `text` usando el catálogo, vía     simi, repair_location_transcription() (+17 more)

### Community 37 - "SENA Interceptors"
Cohesion: 0.16
Nodes (17): AsyncOpenAI, call_llm(), call_llm_async(), extract_json_object(), get_async_openai_client(), get_model(), get_openai_client(), Any (+9 more)

### Community 38 - "Audio Quality Profiling"
Cohesion: 0.12
Nodes (13): bool, True si la llamada tiene calidad consistentemente baja., True si el usuario habla en frases largas (muchas palabras por turno)., True si el usuario usa frases muy cortas., True si la llamada tiene calidad consistentemente baja., True si el usuario habla en frases largas (muchas palabras por turno)., True si el usuario usa frases muy cortas., True si la llamada tiene calidad consistentemente baja. (+5 more)

### Community 39 - "Streaming Pipeline Entry"
Cohesion: 0.04
Nodes (62): _build_dtmf_gather(), _cache_audio(), _generate_play_twiml(), _generate_say_twiml(), _generate_tts_audio(), Almacena audio en cache y retorna un ID único., Almacena audio en cache y retorna un ID único., Almacena audio en cache y retorna un ID único. (+54 more)

### Community 40 - "Interceptor Manager"
Cohesion: 0.33
Nodes (8): Runs logic before tool execution (e.g. arg patching, guards).     Called from t, Runs logic after tool execution (e.g. updating UI state, map center)., Runs all registered pre-LLM interceptors. Returns a response dict if intercepted, run_post_execution_interceptors(), run_pre_execution_interceptors(), run_pre_llm_interceptors(), Any, str

### Community 41 - "Colombian Address Extraction"
Cohesion: 0.12
Nodes (14): LegacyToolAdapter, Any, str, Ejecuta la función legacy inyectando el contexto si es necesario          o sim, Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute., Any, str, Registra una herramienta moderna que cumple con el contrato:         - TOOL_NAM (+6 more)

### Community 42 - "Chat Service"
Cohesion: 0.09
Nodes (25): _get_config_value(), int, Request, RateLimitMiddleware, gateway/middleware.py — Rate limiting en memoria (dict + timestamp).  Simple i, Per-IP rate limiter.     max_requests: maximum requests per window.     window, BaseHTTPMiddleware, LLMEngine (+17 more)

### Community 43 - "Geocoding Overview & Rentus"
Cohesion: 0.18
Nodes (9): Hypothesis: Colombian address nomenclature mathematically unique within city, Decisión sobre popayan_geodata.py, Estructura de archivos del sistema nuevo, Geocodificación — Overview del Refactor, Hipótesis central (validada en revisión arquitectónica), Por qué se hizo este refactor, Qué se construyó en cambio, Decision: popayan_geodata.py removed from geocoding flow (+1 more)

### Community 44 - "Voice STT Tuning Docs"
Cohesion: 0.31
Nodes (9): core/address_utils.py — NLP/STT utilities (cleaned of popayan_geodata), _build_speech_attrs() — Centralized Twilio speechModel/language/enhanced config, classify_speech_quality — Rewritten to text-first logic, Future: Twilio Media Streams + Deepgram Nova-2 real-time STT (replaces turn-based Gather), Decision: googlev2 as default STT model (es-CO native, premium, phone_call enhanced doesn't support es-CO), STT Root Causes: experimental_conversations model, Confidence gating, loud-speak message, fixed speechTimeout, Decision: speechTimeout=auto for address capture (adaptive end-of-speech), Reconocimiento de Voz Twilio — Tuning para usuarios reales (Colombia) (+1 more)

### Community 45 - "Geocoding Phase 1 Plan"
Cohesion: 0.11
Nodes (18): Archivos ACTUALIZADOS, Archivos NO MODIFICADOS (pero relacionados), Archivos NUEVOS, Archivos REESCRITOS, `core/address_utils.py`, `core/address_utils.py`, `core/geo_types.py`, `core/geocoder_service.py` (+10 more)

### Community 46 - "Response Generation"
Cohesion: 0.12
Nodes (12): health_check(), Detailed health check for all Lyra services., BaseSettings, str, core/config.py — Configuración centralizada desde .env con pydantic-settings., Resolve model path relative to project root., Settings, check_connection() (+4 more)

### Community 47 - "Project Configurations"
Cohesion: 0.25
Nodes (10): _empty_stats(), get_stats(), hourly_stats(), intent_stats(), _parse_period(), Intent breakdown (placeholder — Lyra uses LLM, not fixed intents)., Get usage statistics for a given period., Get hourly message distribution. (+2 more)

### Community 48 - "Appointment Utilities"
Cohesion: 0.11
Nodes (17): Arquitectura del Sistema, Convenciones de Desarrollo, Cómo Agregar un Nuevo Proyecto, Desarrollado por, Despliegue y Configuración, Estructura del Proyecto, Motor de IA Híbrido, Panel de Administración (+9 more)

### Community 49 - "TTS Audio Router"
Cohesion: 0.29
Nodes (7): _find_anchored_id_in_messages(), _get_recent_user_messages(), int, Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible., Recupera los detalles de una reserva pendiente de confirmación., Toma los ultimos mensajes del usuario para completar datos omitidos por el LLM., _recover_appointment_details_from_history()

### Community 51 - "Model Path Config"
Cohesion: 0.18
Nodes (12): _disambiguation_question(), _extract_address_span(), Recorta el ruido conversacional dejando solo la dirección.      El geocoder NO, Recorta el ruido conversacional dejando solo la dirección.      El geocoder NO, Nombre corto para leer en voz alta (parte antes de la primera coma)., Construye la pregunta de desambiguación a partir de las sedes reales.     Gener, Nombre corto para leer en voz alta (parte antes de la primera coma)., Nombre corto para leer en voz alta (parte antes de la primera coma). (+4 more)

### Community 52 - "Barge-In Interruption"
Cohesion: 0.20
Nodes (9): int, Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Timeout total de <Gather> en segundos., Timeout total de <Gather> en segundos., Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Timeout total de <Gather> en segundos. (+1 more)

### Community 53 - "Booking State"
Cohesion: 0.19
Nodes (15): _extract_city_from_google(), _format_google_results(), forward_geocode(), float, str, services/geo.py — Servicio de geocodificación centralizado para Lyra AI.  Cons, Geocodifica una dirección o lugar a coordenadas.     Intenta Google Maps primer, Resuelve nombre de ciudad a coordenadas, usando:     1. Registro local de ciuda (+7 more)

### Community 55 - "City Data Cache"
Cohesion: 0.13
Nodes (14): 1. Estándares de Código, 2. Contrato de Tools, 3. Cómo Agregar una Nueva Tool, 4. Cómo Agregar un Nuevo Proyecto, 5. Cómo Agregar un Interceptor, 6. Cómo Agregar un Nuevo Canal de Mensajería, 7. Checklist de Pull Request, 8. Convenciones de Commits (+6 more)

### Community 66 - "Twilio Init"
Cohesion: 0.18
Nodes (11): partial_speech(), Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, partialResultCallback (/partial_speech) — DESACTIVADO por defecto.      Razón:, partialResultCallback (/partial_speech) — DESACTIVADO por defecto.      Razón: (+3 more)

### Community 67 - "Community 67"
Cohesion: 0.14
Nodes (13): Cache key = query normalizada exacta, Componentes, Comportamiento esperado en Fase 1, Criterios de Éxito para pasar a Fase 2, Estados diferidos a Fase 2, Estados implementados en Fase 1, Geocodificación — Plan Fase 1, Google API — Tomar TODOS los resultados, no solo results[0] (+5 more)

### Community 68 - "Community 68"
Cohesion: 0.15
Nodes (12): alias_type: 3 tipos operacionales, Crear alias (primera vez), Degradar alias (is_active = 0), Diseño de la tabla, Geocodificación — Fase 2: geo_human_aliases (DIFERIDA), Incrementar failure_count, Incrementar success_count, No crear alias (+4 more)

### Community 69 - "Community 69"
Cohesion: 0.15
Nodes (12): 1. Problema identificado, 2. Causa raíz (diagnóstico), 3. Cambios realizados, 4. Configuración final recomendada (`.env`), 5. Antes vs Después, 6. Riesgos y limitaciones, 7. Mejora estructural futura (no implementada), Medium (end-of-speech) — `core/streaming_pipeline.py` (+4 more)

### Community 70 - "Community 70"
Cohesion: 0.06
Nodes (61): str, _build_speech_attrs(), _cfg(), _cfg_int(), _deepgram_language(), es_numero_troncal_o_empresa(), extract_destination_address(), extract_pickup_address() (+53 more)

### Community 71 - "Community 71"
Cohesion: 0.20
Nodes (10): Construcción de enriched_query, Geocodificación — Arquitectura Técnica, Máquina de Estados, Reglas de Auto-Aceptación, Separación de Responsabilidades, Tipos de Datos Core (`core/geo_types.py`), Umbrales Popayán, Verdad Humana: `geo_human_aliases` *(Fase 2 — diferida)* (+2 more)

### Community 72 - "Community 72"
Cohesion: 0.29
Nodes (7): Geocoding Pipeline: Cache→Google→Nominatim→CONTEXT_GATHERING, intellitaxi.yaml — TaxBelalcazar Telephony Project Config, nexiservice.yaml — NexiService Colombia Project Config, personalities.yaml — Global Personality Catalog (lyra, nexo), rentus.yaml — Rentus Real Estate Assistant Project Config, response_templates.yaml — Response Template Bank (lyra, nexo, sena personalities), Projects in Production (NexiService, Rentus, IntelliTaxi)

### Community 73 - "Community 73"
Cohesion: 0.04
Nodes (40): _prioritized_canonical_names(), float, int, str, Segundos desde la última actividad de voz., Estima palabras por segundo basado en el texto acumulado y el tiempo.         Ú, Retorna los parámetros óptimos para el próximo <Gather>.                  stat, Nombres canónicos del catálogo, con los más relevantes al frente. (+32 more)

### Community 74 - "Community 74"
Cohesion: 0.29
Nodes (7): find_anchored_id_in_messages(), get_recent_user_messages(), Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible., Recupera los detalles de una reserva pendiente de confirmación., Toma los ultimos mensajes del usuario., recover_appointment_details_from_history(), int

### Community 75 - "Community 75"
Cohesion: 0.15
Nodes (21): float, str, get_pusher_client(), str, trigger_pusher_event(), get_voice_engine(), Retorna el singleton VoiceEngine, creándolo si aún no existe., _get_voice_config() (+13 more)

### Community 76 - "Community 76"
Cohesion: 0.33
Nodes (5): generate_contextual_response(), Mejor texto disponible (final si existe, si no el último parcial)., Genera una respuesta contextual del LLM para casos ambiguos.     Usa el histori, Genera una respuesta contextual del LLM para casos ambiguos.     Usa el histori, Genera una respuesta contextual del LLM para casos ambiguos.     Usa el histori

### Community 77 - "Community 77"
Cohesion: 0.18
Nodes (11): _get_cached_audio(), Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Recupera audio del cache., Recupera audio del cache. (+3 more)

### Community 78 - "Community 78"
Cohesion: 0.11
Nodes (25): get_chat_service(), get_llm(), get_settings(), get_tool_registries(), get_twilio_service(), get_whatsapp_service(), ChatService, Request (+17 more)

### Community 79 - "Community 79"
Cohesion: 0.33
Nodes (6): Webhook inicial de Twilio para modo mantenimiento simple (dice el mensaje y cuel, WebSocket para Twilio Media Streams.      Permite streaming de audio bidirecci, WebSocket para Twilio Media Streams.      Permite streaming de audio bidirecci, WebSocket para Twilio Media Streams.      Permite streaming de audio bidirecci, WebSocket para Twilio Media Streams.      Permite streaming de audio bidirecci, voice_stream()

### Community 80 - "Community 80"
Cohesion: 0.13
Nodes (30): build_system_prompt(), _extract_anchored_ids(), _is_followup_reference(), _is_new_request(), _is_trivial_input(), _normalize_text(), _project_system_content(), bool (+22 more)

### Community 83 - "Community 83"
Cohesion: 0.22
Nodes (9): _alias_covers_input(), Convierte una referencia humana informal a datos estructurados.     Retorna dic, True si el alias cubre el input sin dejar palabras de contenido sueltas., True si el alias cubre el input sin dejar palabras de contenido sueltas., True si el alias cubre el input sin dejar palabras de contenido sueltas., Convierte una referencia humana informal a datos estructurados.     Retorna dic, Convierte una referencia humana informal a datos estructurados.     Retorna dic, Convierte una referencia humana informal a datos estructurados.     Retorna dic (+1 more)

### Community 84 - "Community 84"
Cohesion: 0.33
Nodes (6): _estimate_coords_from_street(), _normalize_address_advanced(), Normalización avanzada de direcciones colombianas para Popayán., Normalización avanzada de direcciones colombianas para Popayán., Estima coordenadas a partir de nomenclatura urbana de Popayán., Estima coordenadas a partir de nomenclatura urbana de Popayán.

### Community 85 - "Community 85"
Cohesion: 0.50
Nodes (3): Request, str, serve_audio()

### Community 86 - "Community 86"
Cohesion: 0.18
Nodes (13): extract_destination_address(), extract_pickup_address(), looks_like_place(), Elimina saludos y relleno del inicio/fin del texto., Valida que `text` parezca una ubicación real en Popayán, para descartar     ext, Valida que `text` parezca una ubicación real en Popayán, para descartar     ext, Valida que `text` parezca una ubicación real en Popayán, para descartar     ext, Extrae dirección de recogida del texto libre.     Retorna (dirección, None) o ( (+5 more)

### Community 87 - "Community 87"
Cohesion: 0.15
Nodes (12): classify_speech_quality(), _create_service(), Geocodifica y crea el servicio de taxi en el backend Laravel., Geocodifica y crea el servicio de taxi en el backend Laravel., Geocodifica y crea el servicio de taxi en el backend Laravel., Geocodifica y crea el servicio de taxi en el backend Laravel., Geocodifica y crea el servicio de taxi en el backend Laravel., Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h (+4 more)

### Community 88 - "Community 88"
Cohesion: 0.50
Nodes (3): cancelar_servicio(), CancelarServicioTool, Cancela una solicitud de taxi activa.

### Community 91 - "Community 91"
Cohesion: 0.29
Nodes (7): _format_logo(), get_businesses_comparison(), Recomienda negocios basados en calificación promedio (satisfacción) y categoría, Formatea la ruta del logo para que sea accesible desde el frontend., Obtiene datos comparativos para una lista de negocios., Format business logo to absolute URL., recommend_businesses()

### Community 92 - "Community 92"
Cohesion: 0.12
Nodes (14): _best_catalog_snap(), bigram_similarity(), _normalize_street_abbreviations(), float, str, Normaliza abreviaturas de calle a forma canónica.     cl, cll, c/ → calle | cra, Normaliza abreviaturas de calle a forma canónica.     cl, cll, c/ → calle | cra, Devuelve la grafía CORRECTA del alias (mismo número de palabras) si     `span_n (+6 more)

### Community 93 - "Community 93"
Cohesion: 0.17
Nodes (11): Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Wrapper que usa los parámetros adaptativos de la sesión., Wrapper que usa los parámetros adaptativos de la sesión., Wrapper que usa los parámetros adaptativos de la sesión. (+3 more)

### Community 94 - "Community 94"
Cohesion: 0.50
Nodes (4): _compound_num_replace(), Convierte 'cuarenta y uno' → '41', etc. en el contexto de una dirección., Convierte 'cuarenta y uno' → '41', etc. en el contexto de una dirección., Match

### Community 100 - "Community 100"
Cohesion: 0.67
Nodes (3): get_nearby_barrios(), Barrios dentro de radius_km de las coordenadas dadas, ordenados por distancia., Barrios dentro de radius_km de las coordenadas dadas, ordenados por distancia.

## Knowledge Gaps
- **164 isolated node(s):** `PreToolUse`, `allow`, `int`, `Request`, `int` (+159 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_connection()` connect `Admin Session Management` to `Geocoding Types & Candidates`, `App Config & Logging`, `Chat Service`, `Admin Config Management`, `Nexiservice Tools`, `Response Generation`, `Community 78`, `Project Configurations`, `Main Router & Database`, `Booking Flow Tests`, `Community 91`, `Navigation & UI Tools`, `Response Engine & Templates`?**
  _High betweenness centrality (0.112) - this node is a cross-community bridge._
- **Why does `FastAPI` connect `Chat Service` to `Community 70`, `Admin Session Management`, `Community 75`, `Admin Config Management`, `Community 78`, `Project Configurations`, `Main Router & Database`, `WhatsApp Channel Router`, `Community 85`?**
  _High betweenness centrality (0.100) - this node is a cross-community bridge._
- **Why does `normalize_text()` connect `Scheduling & Shared Utils` to `Twilio Voice Router`, `Google Geocoding Service`, `Nexiservice Tools`, `Community 80`, `Tool Adapter & Registry`, `Tool Runner Utilities`, `Booking State`, `Orchestrator Tool Runner`, `Response Engine & Templates`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `str` (e.g. with `BargeInHandler` and `ConversationMemory`) actually correct?**
  _`str` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `AudioQualityProfile` (e.g. with `AudioQualityProfile` and `bool`) actually correct?**
  _`AudioQualityProfile` has 23 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PreToolUse`, `allow`, `int` to the rest of the system?**
  _797 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Geocoding Types & Candidates` be split into smaller, more focused modules?**
  _Cohesion score 0.07473684210526316 - nodes in this community are weakly interconnected._