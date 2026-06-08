# Graph Report - virtualtIA  (2026-06-08)

## Corpus Check
- 83 files · ~115,247 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1711 nodes · 3608 edges · 101 communities (92 shown, 9 thin omitted)
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
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 96|Community 96]]
- [[_COMMUNITY_Community 97|Community 97]]
- [[_COMMUNITY_Community 98|Community 98]]
- [[_COMMUNITY_Community 99|Community 99]]
- [[_COMMUNITY_Community 100|Community 100]]

## God Nodes (most connected - your core abstractions)
1. `get_connection()` - 64 edges
2. `str` - 54 edges
3. `process_speech()` - 41 edges
4. `resolve_location_entity()` - 40 edges
5. `AudioQualityProfile` - 40 edges
6. `ConversationMemory` - 34 edges
7. `decide()` - 31 edges
8. `str` - 29 edges
9. `Decision` - 29 edges
10. `normalize_text()` - 29 edges

## Surprising Connections (you probably didn't know these)
- `float` --uses--> `ChatService`  [INFERRED]
  api/routers/main.py → services/chat_service.py
- `str` --uses--> `ChatService`  [INFERRED]
  api/routers/main.py → services/chat_service.py
- `Geocoding State Machine (NORMALIZING→CACHE_LOOKUP→RESOLVING→CONTEXT_GATHERING→CONFIRMING→RESOLVED)` --semantically_similar_to--> `Twilio Sessions in Memory with asyncio.Lock — ADR-004`  [INFERRED] [semantically similar]
  docs/geocoding/01-architecture.md → ARCHITECTURE.md
- `Tool Contract Definition (CONTRIBUTING guide)` --semantically_similar_to--> `Tool Contract (TOOL_NAME, TOOL_SCHEMA, execute)`  [INFERRED] [semantically similar]
  CONTRIBUTING.md → ARCHITECTURE.md
- `get_db()` --calls--> `get_connection()`  [EXTRACTED]
  api/dependencies.py → core/database.py

## Import Cycles
- 1-file cycle: `main.py -> main.py`
- 1-file cycle: `core/llm_utils.py -> core/llm_utils.py`
- 2-file cycle: `api/middleware.py -> main.py -> api/middleware.py`

## Hyperedges (group relationships)
- **Geocoding Pipeline Core Components (Phase 1)** — geocoding_02_geocoder_service, geocoding_02_address_utils, geocoding_01_geo_types, geocoding_01_location_cache, geocoding_02_migration_003 [EXTRACTED 1.00]
- **All Lyra Project YAML Configurations** — projects_intellitaxi_yaml, projects_nexiservice_yaml, projects_rentus_yaml, projects_schoolsena_yaml [EXTRACTED 1.00]
- **Lyra Hybrid Engine Processing Flow** — architecture_intent_router, architecture_interceptor_pattern, architecture_tool_registry, architecture_orchestrator_layer, architecture_llm_as_fallback [EXTRACTED 1.00]

## Communities (101 total, 9 thin omitted)

### Community 0 - "Twilio Voice Router"
Cohesion: 0.16
Nodes (16): _build_dtmf_gather(), _build_speech_attrs(), _cfg(), _deepgram_language(), Idioma para los modelos Deepgram. nova-2/nova-3 NO soportan es-CO; solo     "es, Idioma para los modelos Deepgram. nova-2/nova-3 NO soportan es-CO; solo     "es, Idioma para los modelos Deepgram. nova-2/nova-3 NO soportan es-CO; solo     "es, Construye los atributos de reconocimiento de voz del <Gather>.      Centraliza (+8 more)

### Community 1 - "Geocoding Types & Candidates"
Cohesion: 0.07
Nodes (69): normalize_colombian_address(), Normaliza al formato colombiano estándar.     'carrera cuarta a el # 17 b 28' →, Normaliza al formato colombiano estándar.     'carrera cuarta a el # 17 b 28' →, GeoCandidate, GeoResolution, in_urban_bbox(), in_wide_bbox(), LocationType (+61 more)

### Community 2 - "STT Enhancement & Local Search"
Cohesion: 0.11
Nodes (24): _best_catalog_snap(), bigram_similarity(), _build_phonetic_repair_index(), combined_score(), fuzzy_match_location(), _normalize_street_abbreviations(), phonetic_key(), float (+16 more)

### Community 3 - "Google Geocoding Service"
Cohesion: 0.12
Nodes (18): bool, str, int, str, CallSession, CallState, Procesa el input de voz y retorna el siguiente paso TwiML., Crea el servicio, limpia la sesión y retorna TwiML de cierre. (+10 more)

### Community 4 - "Browser Voice & TTS"
Cohesion: 0.12
Nodes (22): AsyncOpenAI, get_async_openai_client(), Get or create the async OpenAI/OpenRouter client.     Uses AsyncOpenAI so that, _clean_for_tts(), _edge_tts_sync_bytes(), _is_gibberish(), bool, bytes (+14 more)

### Community 5 - "App Config & Logging"
Cohesion: 0.13
Nodes (27): BaseSettings, ChatResponse, core/config.py — Configuración centralizada desde .env con pydantic-settings., Settings, get_conversation_history(), get_conversation_message_count(), get_or_create_conversation(), get_or_create_user() (+19 more)

### Community 6 - "Scheduling & Shared Utils"
Cohesion: 0.06
Nodes (58): str, Configura un logger estándar con salida a consola y archivo rotativo., setup_logger(), main.py — Lyra Microservice Entry Point  ... [TRUNCATED FOR BREVITY - ANALISIS, _extract_session_today(), _inject_ids_into_titles(), date, orchestrator/tool_runner.py — Agent loop con límite estricto de herramientas. (+50 more)

### Community 7 - "Popayan Geodata Index"
Cohesion: 0.06
Nodes (50): haversine(), Distancia en km entre dos coordenadas GPS (fórmula de Haversine).      Consoli, _build_alias_index(), _ensure_index(), _estimate_coords_from_street(), _find_similar_places(), fuzzy_search(), geocode_local() (+42 more)

### Community 8 - "Nexiservice Booking Interceptor"
Cohesion: 0.15
Nodes (16): _is_generic_query(), _normalize(), _normalize_time(), orchestrator/interceptors/helpers.py ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Uti, Recupera la lista de negocios del último resultado de herramienta en el historia, Recupera la categoría y ciudad de la última búsqueda de negocios., Reconstruye el contexto completo de una reserva en curso desde el historial., Normaliza una cadena de hora a formato HH:MM. (+8 more)

### Community 9 - "Admin Session Management"
Cohesion: 0.15
Nodes (17): delete_session(), export_sessions(), list_sessions(), Export sessions (basic JSON export)., Get full session with messages., Delete a session and its messages., List conversations (sessions) with pagination., session_detail() (+9 more)

### Community 10 - "Nexiservice Tools"
Cohesion: 0.14
Nodes (33): _clean_search_query(), confirm_appointment(), fly_to_business(), get_business_availability(), get_business_mission_vision(), get_business_reviews(), get_business_services(), get_general_info() (+25 more)

### Community 11 - "Admin Config Management"
Cohesion: 0.09
Nodes (31): clear_cache(), ConfigUpdate, create_version(), get_config(), _get_config_value(), _get_current_version(), get_status(), health_incidents() (+23 more)

### Community 12 - "Conversation Repair"
Cohesion: 0.11
Nodes (20): ConversationMemory, ConversationRepair, _extract_partial_location(), get_repair_message(), infer_intent(), float, str, core/conversation_repair.py — Motor de reparación conversacional inteligente par (+12 more)

### Community 13 - "Voice Session State"
Cohesion: 0.15
Nodes (32): AudioQualityProfile, bool, bytes, float, int, Request, AsyncClient, ConversationMemory (+24 more)

### Community 14 - "Booking Flow Tests"
Cohesion: 0.13
Nodes (28): fail(), get_all_businesses(), get_first_tercero(), get_professionals_for_service(), get_services_for_business(), info(), _normalize(), ok() (+20 more)

### Community 15 - "SENA Learning Tools"
Cohesion: 0.09
Nodes (41): _denied(), _entity_type(), _get_roles(), post_execution_interceptor(), pre_llm_interceptor(), Obtiene el horario del instructor y deduplica por código de ficha,     mostrand, _run(), _run_fichas_asignadas() (+33 more)

### Community 16 - "Main Router & Database"
Cohesion: 0.15
Nodes (16): float, str, BaseModel, geocode_api(), health(), list_projects(), gateway/router.py — FastAPI endpoints: POST /chat, GET /health, GET /projects., Health check — reports model and DB status. (+8 more)

### Community 17 - "Tool Adapter & Registry"
Cohesion: 0.22
Nodes (6): int, Request, RateLimitMiddleware, gateway/middleware.py — Rate limiting en memoria (dict + timestamp).  Simple i, Per-IP rate limiter.     max_requests: maximum requests per window.     window, BaseHTTPMiddleware

### Community 18 - "WhatsApp Channel Router"
Cohesion: 0.16
Nodes (25): bool, int, Request, str, BackgroundTasks, clean_map_location(), _create_wp_service(), get_wp_session() (+17 more)

### Community 19 - "Address Normalization Core"
Cohesion: 0.16
Nodes (25): _build_local_match_index(), _clean_stt_text(), _correct_speech(), extract_datetime_local(), extract_datetime_with_llm(), _geocode_cache_set(), _in_popayan_bbox(), _is_correction_request() (+17 more)

### Community 20 - "Tool Runner Utilities"
Cohesion: 0.09
Nodes (26): _build_schedule_clarification(), _extract_session_user_id(), _extract_tastes_from_history(), _find_anchored_id_in_messages(), _get_recent_user_messages(), _is_generic_query(), _match_property_id_in_reply(), _normalize() (+18 more)

### Community 21 - "LLM Engine & Middleware"
Cohesion: 0.13
Nodes (31): _best_for_entity(), _build_catalog(), _content_tokens(), _Entity, _has_content(), _is_all_filler(), _key(), LocationMatch (+23 more)

### Community 22 - "Rentus Property Tools"
Cohesion: 0.11
Nodes (20): get_property_detail(), GetPropertyDetailTool, _parse_properties_from_response(), float, int, str, tools/rentus.py — Tool functions for the Rentus project., Obtiene el detalle completo de una propiedad por ID. (+12 more)

### Community 23 - "Streaming STT Pipeline"
Cohesion: 0.14
Nodes (6): float, Segundos desde la última actividad de voz., Estima palabras por segundo basado en el texto acumulado y el tiempo.         Ú, Procesa el texto final de STT de un turno.                  Retorna:, Procesa el texto final de STT de un turno.                  Retorna:, Procesa el texto final de STT de un turno.                  Retorna:

### Community 24 - "IntelliTaxi Tools"
Cohesion: 0.12
Nodes (18): _geocode_cache_get(), normalize_address(), Estandariza nomenclatura (Calle → Cl, etc.), Estandariza nomenclatura (Calle → Cl, etc.), cancelar_servicio(), CancelarServicioTool, consultar_conductores_disponibles(), ConsultarConductoresTool (+10 more)

### Community 25 - "Orchestrator Tool Runner"
Cohesion: 0.25
Nodes (8): Recupera tanto la ciudad como la categoría de la última búsqueda desde los tool, Recupera la ciudad sugerida en el último mensaje de la herramienta o del asisten, _recover_last_search_args_from_history(), _recover_suggested_city_from_history(), Recupera la ciudad sugerida en el último mensaje de la herramienta o del asisten, Recupera tanto la ciudad como la categoría de la última búsqueda., recover_last_search_args_from_history(), recover_suggested_city_from_history()

### Community 26 - "API Dependency Injection"
Cohesion: 0.23
Nodes (6): bool, str, Almacenamiento de sesiones en memoria.     Reemplazar por RedisSessionStore cua, SessionStore, WhatsappService, WpSession

### Community 27 - "Interceptor Helpers"
Cohesion: 0.08
Nodes (24): 1. Filosofía de Diseño, 2. El Motor Híbrido — Cerebro Rápido + Cerebro Lento, 3. Capas del Sistema, 4. Patrones Implementados, 5. Flujos de Datos Detallados, 6. Gestión de Configuración, 7. Observabilidad, 8. Decisiones Arquitectónicas (ADRs) (+16 more)

### Community 28 - "Navigation & UI Tools"
Cohesion: 0.18
Nodes (12): navigate_to_company(), str, tools/navigation.py — Herramientas para la navegación programática en la UI., Activa la navegación automática hacia el perfil de una empresa específica., open_business_web(), bool, float, Obtiene la URL de la página web o red social de un negocio para abrirla. (+4 more)

### Community 29 - "Adaptive Endpoint Control"
Cohesion: 0.14
Nodes (18): bool, float, int, str, GeographicMatcher, _haversine(), _is_in_popayan(), services/twilio/navigation.py — Parser de navegación y matching geográfico para (+10 more)

### Community 30 - "Context Builder & Prompt"
Cohesion: 0.23
Nodes (14): detect_intent(), _extract_city(), _extract_date(), _extract_service_name(), _is_spam(), _normalize(), bool, str (+6 more)

### Community 31 - "Response Engine & Templates"
Cohesion: 0.14
Nodes (20): _build_business_list(), clear_session_history(), _format_distance(), generate_response(), _get_variations(), _load_templates(), float, int (+12 more)

### Community 32 - "Architecture Overview Docs"
Cohesion: 0.14
Nodes (16): API Layer (api/), Core Layer (core/) — Shared Resources, Dependency Injection via FastAPI Depends, Lyra AI — Generative AI Orchestration Engine, Services Layer (services/), Thin Routers — ADR-001, Twilio Sessions in Memory with asyncio.Lock — ADR-004, Geocoding State Machine (NORMALIZING→CACHE_LOOKUP→RESOLVING→CONTEXT_GATHERING→CONFIRMING→RESOLVED) (+8 more)

### Community 33 - "Lyra Hybrid Engine Design"
Cohesion: 0.15
Nodes (15): Motor Híbrido — Cerebro Rápido + Cerebro Lento, Intent Router (Regex + Keywords Classifier), Interceptor Pattern (AOP-lite Pre/Post LLM), LegacyToolAdapter — Gradual Migration Pattern, LLM as Auxiliary Fallback — ADR-002, Orchestrator Layer (orchestrator/), Tool Contract (TOOL_NAME, TOOL_SCHEMA, execute), Tool Registry with Auto-Discovery (+7 more)

### Community 34 - "LLM Utility Functions"
Cohesion: 0.11
Nodes (28): _base_decision(), decide(), Decisión por tipo+confianza, SIN considerar desambiguación. Una     coincidencia, Mapea un LocationMatch a la acción del flujo. Precisión sobre recall.      La de, tests/test_location_match.py — Cobertura de la resolución de ubicaciones precisi, Regresión: 'buenas' (fuzzy débil contra alias 'sena') NO debe escalar a     AMBI, Una coincidencia fuzzy débil a una entidad ambigua nunca da AMBIGUOUS., El caso reportado: 'Villa del Viento' nunca debe mapear a OTRA entidad     (SENA (+20 more)

### Community 35 - "Geocoding Architecture Docs"
Cohesion: 0.14
Nodes (17): Auto-Accept Rules (ROOFTOP+RANGE_INTERPOLATED in POPAYAN_URBAN_BBOX), enriched_query Construction (query_base + alias_text by type), geo_human_aliases — Human Truth Table (Phase 2, deferred), core/geo_types.py — LocationType, ResolutionStatus, GeoCandidate, GeoResolution, location_cache — Technical Truth Table (canonical_query key), POPAYAN_URBAN_BBOX and POPAYAN_BBOX_WIDE Thresholds, Post-Resolution Verification (formatted_address must contain query numbers), core/geocoder_service.py — run_pipeline, _google_get_candidates, _nominatim_get_candidates, _decide (+9 more)

### Community 36 - "Intent Router"
Cohesion: 0.15
Nodes (21): _build_hint_vocab(), _get_contextual_hints(), Genera hints de vocabulario FOCALIZADOS según el estado (máx ~15 términos)., Genera hints de vocabulario FOCALIZADOS según el estado (máx ~15 términos)., Construye la cadena de hints model-aware desde los catálogos (cacheada)., Genera hints de vocabulario según el estado y el modelo STT.      - Captura (w, Repara la grafía de nombres de lugar en `text` usando el catálogo, vía     simi, repair_location_transcription() (+13 more)

### Community 37 - "SENA Interceptors"
Cohesion: 0.21
Nodes (14): call_llm(), call_llm_async(), extract_json_object(), get_model(), get_openai_client(), Any, float, str (+6 more)

### Community 38 - "Audio Quality Profiling"
Cohesion: 0.12
Nodes (13): bool, True si la llamada tiene calidad consistentemente baja., True si el usuario habla en frases largas (muchas palabras por turno)., True si el usuario usa frases muy cortas., True si la llamada tiene calidad consistentemente baja., True si el usuario habla en frases largas (muchas palabras por turno)., True si el usuario usa frases muy cortas., True si la llamada tiene calidad consistentemente baja. (+5 more)

### Community 39 - "Streaming Pipeline Entry"
Cohesion: 0.08
Nodes (28): _cache_audio(), _generate_play_twiml(), _get_base_url_for_twilio(), Almacena audio en cache y retorna un ID único., Almacena audio en cache y retorna un ID único., Almacena audio en cache y retorna un ID único., Webhook inicial de Twilio para modo mantenimiento., Webhook para procesar la respuesta en modo mantenimiento. (+20 more)

### Community 40 - "Interceptor Manager"
Cohesion: 0.33
Nodes (8): Runs logic before tool execution (e.g. arg patching, guards).     Called from t, Runs logic after tool execution (e.g. updating UI state, map center)., Runs all registered pre-LLM interceptors. Returns a response dict if intercepted, run_post_execution_interceptors(), run_pre_execution_interceptors(), run_pre_llm_interceptors(), Any, str

### Community 41 - "Colombian Address Extraction"
Cohesion: 0.11
Nodes (18): Request, validation_exception_handler(), LegacyToolAdapter, Any, str, Ejecuta la función legacy inyectando el contexto si es necesario          o sim, Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute., Any (+10 more)

### Community 42 - "Chat Service"
Cohesion: 0.20
Nodes (9): LLMEngine, float, int, str, core/llm_engine.py — Tool calling para LLMs (OpenRouter/OpenAI-compatible).  N, Parse a failed_generation string like <function=name>{"k":v}</function>, Fix type mismatches: cast int→str, null→default, unwrap 'properties' wrapper., lifespan() (+1 more)

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
Cohesion: 0.29
Nodes (6): health_check(), Detailed health check for all Lyra services., check_connection(), bool, core/database.py — Pool de conexiones MySQL con PyMySQL.  Provee get_connectio, Quick ping to verify MySQL is reachable.

### Community 47 - "Project Configurations"
Cohesion: 0.12
Nodes (17): Session aggregate stats., session_stats(), SessionUpdate, _empty_stats(), get_stats(), hourly_stats(), intent_stats(), _parse_period() (+9 more)

### Community 48 - "Appointment Utilities"
Cohesion: 0.11
Nodes (17): Arquitectura del Sistema, Convenciones de Desarrollo, Cómo Agregar un Nuevo Proyecto, Desarrollado por, Despliegue y Configuración, Estructura del Proyecto, Motor de IA Híbrido, Panel de Administración (+9 more)

### Community 49 - "TTS Audio Router"
Cohesion: 0.13
Nodes (16): bool, float, str, normalize_text(), Normaliza texto eliminando tildes/diacríticos, convirtiendo a minúsculas,     y, NavigationParser, Parser de descripciones de navegación relativa comunes en Colombia/Popayán., Procesamiento de voz para el flujo de taxi en Popayán.     Responsabilidades: l (+8 more)

### Community 51 - "Model Path Config"
Cohesion: 0.16
Nodes (17): str, _disambiguation_question(), extract_address(), _extract_address_span(), extract_destination_address(), extract_pickup_address(), Recorta el ruido conversacional dejando solo la dirección.      El geocoder NO, Pipeline unificado de extracción. role = "origen" | "destino"     Retorna (cano (+9 more)

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
Cohesion: 0.11
Nodes (19): partial_speech(), Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, Procesa resultados parciales de STT de Twilio (partialResultCallback).      Pe, partialResultCallback (/partial_speech) — DESACTIVADO por defecto.      Razón:, partialResultCallback (/partial_speech) — DESACTIVADO por defecto.      Razón:, partialResultCallback (/partial_speech) — DESACTIVADO por defecto.      Razón: (+11 more)

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
Cohesion: 0.14
Nodes (25): _cfg_int(), es_numero_troncal_o_empresa(), _gather_action_url(), _get_process_speech_url(), get_session(), limpiar_numero(), _max_silence(), obtener_telefono_cliente() (+17 more)

### Community 71 - "Community 71"
Cohesion: 0.20
Nodes (10): Construcción de enriched_query, Geocodificación — Arquitectura Técnica, Máquina de Estados, Reglas de Auto-Aceptación, Separación de Responsabilidades, Tipos de Datos Core (`core/geo_types.py`), Umbrales Popayán, Verdad Humana: `geo_human_aliases` *(Fase 2 — diferida)* (+2 more)

### Community 72 - "Community 72"
Cohesion: 0.29
Nodes (7): Geocoding Pipeline: Cache→Google→Nominatim→CONTEXT_GATHERING, intellitaxi.yaml — TaxBelalcazar Telephony Project Config, nexiservice.yaml — NexiService Colombia Project Config, personalities.yaml — Global Personality Catalog (lyra, nexo), rentus.yaml — Rentus Real Estate Assistant Project Config, response_templates.yaml — Response Template Bank (lyra, nexo, sena personalities), Projects in Production (NexiService, Rentus, IntelliTaxi)

### Community 73 - "Community 73"
Cohesion: 0.08
Nodes (26): generate_contextual_response(), PartialTranscript, _prioritized_canonical_names(), bool, str, core/streaming_pipeline.py — Pipeline de streaming incremental para Lyra.  Imp, Mejor texto disponible (final si existe, si no el último parcial)., Retorna los parámetros óptimos para el próximo <Gather>.                  stat (+18 more)

### Community 74 - "Community 74"
Cohesion: 0.18
Nodes (17): _find_anchored_id_in_messages(), Busca el último ID de negocio anclado en el historial de mensajes., BookingState, get_booking_state(), _handle_conversational(), _handle_get_business_availability(), _handle_get_business_reviews(), _handle_get_business_services() (+9 more)

### Community 75 - "Community 75"
Cohesion: 0.14
Nodes (22): float, str, get_pusher_client(), str, trigger_pusher_event(), get_voice_engine(), Retorna el singleton VoiceEngine, creándolo si aún no existe., load_project_config() (+14 more)

### Community 76 - "Community 76"
Cohesion: 0.20
Nodes (10): _aggressive_normalize(), preprocess_stt(), Normalización agresiva previa, para audio telefónico muy degradado.      - Eli, Normalización agresiva previa, para audio telefónico muy degradado.      - Eli, Normalización agresiva previa, para audio telefónico muy degradado.      - Eli, Normalización agresiva previa, para audio telefónico muy degradado.      - Eli, Pipeline completo de pre-procesamiento STT.      Pasos (en orden):     0. Nor, Pipeline completo de pre-procesamiento STT.      Pasos (en orden):     0. Nor (+2 more)

### Community 77 - "Community 77"
Cohesion: 0.22
Nodes (9): _get_cached_audio(), Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Recupera audio del cache., Recupera audio del cache., Recupera audio del cache. (+1 more)

### Community 78 - "Community 78"
Cohesion: 0.15
Nodes (20): get_chat_service(), get_db(), get_llm(), get_settings(), get_tool_registries(), get_twilio_service(), get_whatsapp_service(), ChatService (+12 more)

### Community 79 - "Community 79"
Cohesion: 0.11
Nodes (19): _generate_tts_audio(), _lyra_tts_voice(), Voz principal de Lyra via edge_tts (Azure Neural)., Voz principal de Lyra via edge_tts (Azure Neural)., Voz principal de Lyra via edge_tts (Azure Neural)., Expands street abbreviations so TTS reads full words instead of letters., Expands street abbreviations so TTS reads full words instead of letters., Expands street abbreviations so TTS reads full words instead of letters. (+11 more)

### Community 80 - "Community 80"
Cohesion: 0.29
Nodes (16): build_system_prompt(), _extract_anchored_ids(), _is_followup_reference(), _is_new_request(), _is_trivial_input(), _normalize_text(), _project_system_content(), bool (+8 more)

### Community 83 - "Community 83"
Cohesion: 0.13
Nodes (15): _alias_covers_input(), Convierte una referencia humana informal a datos estructurados.     Retorna dic, True si el alias cubre el input sin dejar palabras de contenido sueltas., True si el alias cubre el input sin dejar palabras de contenido sueltas., True si el alias cubre el input sin dejar palabras de contenido sueltas., Convierte una referencia humana informal a datos estructurados.     Retorna dic, Convierte una referencia humana informal a datos estructurados.     Retorna dic, Convierte una referencia humana informal a datos estructurados.     Retorna dic (+7 more)

### Community 84 - "Community 84"
Cohesion: 0.21
Nodes (15): _call_confirm_appointment(), clear_booking_state(), _extract_name_from_messages(), _handle_confirm_appointment(), _handle_request_appointment(), _handle_waiting_name_state(), _is_valid_name(), Verifica que el candidato sea un nombre humano real.     Retorna False si es No (+7 more)

### Community 85 - "Community 85"
Cohesion: 0.14
Nodes (15): _generate_say_twiml(), Fallback: genera <Say> con voz Polly (si edge_tts no está disponible)., Fallback: genera <Say> con voz Polly (si edge_tts no está disponible)., Fallback: genera <Say> con voz Polly (si edge_tts no está disponible)., Fallback: genera <Say> con voz Polly (si edge_tts no está disponible)., Redirect con audio opcional (para transiciones de estado)., Redirect con audio opcional (para transiciones de estado)., Redirect con audio opcional (para transiciones de estado). (+7 more)

### Community 86 - "Community 86"
Cohesion: 0.18
Nodes (13): extract_destination_address(), extract_pickup_address(), looks_like_place(), Elimina saludos y relleno del inicio/fin del texto., Valida que `text` parezca una ubicación real en Popayán, para descartar     ext, Valida que `text` parezca una ubicación real en Popayán, para descartar     ext, Valida que `text` parezca una ubicación real en Popayán, para descartar     ext, Extrae dirección de recogida del texto libre.     Retorna (dirección, None) o ( (+5 more)

### Community 87 - "Community 87"
Cohesion: 0.18
Nodes (10): classify_speech_quality(), _create_service(), Geocodifica y crea el servicio de taxi en el backend Laravel., Geocodifica y crea el servicio de taxi en el backend Laravel., Geocodifica y crea el servicio de taxi en el backend Laravel., Geocodifica y crea el servicio de taxi en el backend Laravel., Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h, Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h (+2 more)

### Community 88 - "Community 88"
Cohesion: 0.25
Nodes (9): _assert_data_from_db(), _extract_confirmed_name_from_assistant(), post_execution_interceptor(), Recupera el nombre del servicio que el usuario seleccionó ANTES de que se     l, Si el asistente preguntó '¿La reserva irá a tu nombre (Juan)?', extrae 'Juan'., _recover_pending_service_from_history(), _resolve_logged_user_name(), Any (+1 more)

### Community 89 - "Community 89"
Cohesion: 0.25
Nodes (5): int, Procesa texto parcial y retorna estado de intención actualizado., Procesa un fragmento de speech parcial (de partialResultCallback).         Reto, Procesa un fragmento de speech parcial (de partialResultCallback).         Reto, Procesa un fragmento de speech parcial (de partialResultCallback).         Reto

### Community 90 - "Community 90"
Cohesion: 0.29
Nodes (6): Any, Settings, str, Returns the conversation history for a given session., Removes technical markers like [BIZ:123], [ANALIZANDO DATOS], etc. from the fina, _strip_debug_markers()

### Community 91 - "Community 91"
Cohesion: 0.29
Nodes (7): _format_logo(), get_businesses_comparison(), Recomienda negocios basados en calificación promedio (satisfacción) y categoría, Formatea la ruta del logo para que sea accesible desde el frontend., Obtiene datos comparativos para una lista de negocios., Format business logo to absolute URL., recommend_businesses()

### Community 92 - "Community 92"
Cohesion: 0.40
Nodes (4): Recomienda el speechTimeout de Twilio basado en el perfil del usuario., Recomienda el speechTimeout de Twilio basado en el perfil del usuario., Recomienda el speechTimeout de Twilio basado en el perfil del usuario., Recomienda el speechTimeout de Twilio basado en el perfil del usuario.

### Community 93 - "Community 93"
Cohesion: 0.40
Nodes (4): Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather.

### Community 94 - "Community 94"
Cohesion: 0.50
Nodes (4): _compound_num_replace(), Convierte 'cuarenta y uno' → '41', etc. en el contexto de una dirección., Convierte 'cuarenta y uno' → '41', etc. en el contexto de una dirección., Match

### Community 98 - "Community 98"
Cohesion: 0.67
Nodes (3): villa del norte' es un barrio; no debe convertirse en 'SENA Norte' por     la pa, villa del norte' es un barrio; no debe convertirse en 'SENA Norte' por     la pa, test_villa_del_norte_not_routed_to_sena()

### Community 99 - "Community 99"
Cohesion: 0.67
Nodes (3): Un substring cubierto (textual) debe ganar a cualquier casi-acierto     fonético, Un substring cubierto (textual) debe ganar a cualquier casi-acierto     fonético, test_phonetic_does_not_override_substring()

### Community 100 - "Community 100"
Cohesion: 0.67
Nodes (3): get_nearby_barrios(), Barrios dentro de radius_km de las coordenadas dadas, ordenados por distancia., Barrios dentro de radius_km de las coordenadas dadas, ordenados por distancia.

## Knowledge Gaps
- **164 isolated node(s):** `PreToolUse`, `allow`, `int`, `Request`, `int` (+159 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_connection()` connect `Admin Session Management` to `Geocoding Types & Candidates`, `App Config & Logging`, `Community 74`, `Admin Config Management`, `Nexiservice Tools`, `Response Generation`, `Community 78`, `Project Configurations`, `Main Router & Database`, `Booking Flow Tests`, `Community 88`, `Community 91`, `Navigation & UI Tools`?**
  _High betweenness centrality (0.114) - this node is a cross-community bridge._
- **Why does `FastAPI` connect `Project Configurations` to `App Config & Logging`, `Scheduling & Shared Utils`, `Community 70`, `Colombian Address Extraction`, `Chat Service`, `Admin Config Management`, `Community 75`, `Community 78`, `Main Router & Database`, `Tool Adapter & Registry`, `WhatsApp Channel Router`?**
  _High betweenness centrality (0.098) - this node is a cross-community bridge._
- **Why does `normalize_text()` connect `TTS Audio Router` to `Scheduling & Shared Utils`, `Popayan Geodata Index`, `Nexiservice Booking Interceptor`, `Nexiservice Tools`, `Tool Runner Utilities`, `Booking State`, `Context Builder & Prompt`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `str` (e.g. with `BargeInHandler` and `ConversationMemory`) actually correct?**
  _`str` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `AudioQualityProfile` (e.g. with `AudioQualityProfile` and `bool`) actually correct?**
  _`AudioQualityProfile` has 23 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PreToolUse`, `allow`, `int` to the rest of the system?**
  _758 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Geocoding Types & Candidates` be split into smaller, more focused modules?**
  _Cohesion score 0.07473684210526316 - nodes in this community are weakly interconnected._