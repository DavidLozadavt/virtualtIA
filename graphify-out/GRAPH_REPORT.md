# Graph Report - .  (2026-06-04)

## Corpus Check
- 83 files · ~107,002 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1253 nodes · 2849 edges · 67 communities (59 shown, 8 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 212 edges (avg confidence: 0.54)
- Token cost: 0 input · 0 output

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

## God Nodes (most connected - your core abstractions)
1. `get_connection()` - 64 edges
2. `str` - 51 edges
3. `AudioQualityProfile` - 36 edges
4. `process_speech()` - 35 edges
5. `ConversationMemory` - 34 edges
6. `str` - 29 edges
7. `normalize_text()` - 29 edges
8. `BargeInHandler` - 28 edges
9. `run_pipeline()` - 26 edges
10. `StreamingSTTBuffer` - 26 edges

## Surprising Connections (you probably didn't know these)
- `Geocoding State Machine (NORMALIZING→CACHE_LOOKUP→RESOLVING→CONTEXT_GATHERING→CONFIRMING→RESOLVED)` --semantically_similar_to--> `Twilio Sessions in Memory with asyncio.Lock — ADR-004`  [INFERRED] [semantically similar]
  docs/geocoding/01-architecture.md → ARCHITECTURE.md
- `ChatRequest` --uses--> `ChatService`  [INFERRED]
  api/routers/main.py → services/chat_service.py
- `ChatService` --uses--> `ChatService`  [INFERRED]
  api/routers/main.py → services/chat_service.py
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

## Communities (67 total, 8 thin omitted)

### Community 0 - "Twilio Voice Router"
Cohesion: 0.06
Nodes (87): Request, str, get_async_openai_client(), Get or create the async OpenAI/OpenRouter client.     Uses AsyncOpenAI so that, _get_contextual_hints(), Genera hints de vocabulario FOCALIZADOS según el estado (máx ~15 términos)., _aggressive_normalize(), _build_dtmf_gather() (+79 more)

### Community 1 - "Geocoding Types & Candidates"
Cohesion: 0.08
Nodes (68): Elimina saludos y relleno del inicio/fin del texto., _strip_preamble(), GeoCandidate, GeoResolution, in_urban_bbox(), in_wide_bbox(), LocationType, bool (+60 more)

### Community 2 - "STT Enhancement & Local Search"
Cohesion: 0.07
Nodes (43): _build_local_match_index(), Búsqueda local en el catálogo de barrios/landmarks de Popayán     (popayan_geod, _try_local_match(), bigram_similarity(), combined_score(), fuzzy_match_location(), _normalize_street_abbreviations(), phonetic_key() (+35 more)

### Community 3 - "Google Geocoding Service"
Cohesion: 0.08
Nodes (31): _extract_city_from_google(), _format_google_results(), forward_geocode(), float, str, services/geo.py — Servicio de geocodificación centralizado para Lyra AI.  Cons, Geocodifica una dirección o lugar a coordenadas.     Intenta Google Maps primer, Resuelve nombre de ciudad a coordenadas, usando:     1. Registro local de ciuda (+23 more)

### Community 4 - "Browser Voice & TTS"
Cohesion: 0.07
Nodes (40): float, str, AsyncOpenAI, str, trigger_pusher_event(), _clean_for_tts(), _edge_tts_sync_bytes(), get_voice_engine() (+32 more)

### Community 5 - "App Config & Logging"
Cohesion: 0.10
Nodes (32): BaseSettings, ChatResponse, core/config.py — Configuración centralizada desde .env con pydantic-settings., Settings, str, Configura un logger estándar con salida a consola y archivo rotativo., setup_logger(), main.py — Lyra Microservice Entry Point  ... [TRUNCATED FOR BREVITY - ANALISIS (+24 more)

### Community 6 - "Scheduling & Shared Utils"
Cohesion: 0.09
Nodes (37): Normaliza fecha/hora preferidas desde args del LLM o el historial reciente., _resolve_schedule_datetime(), build_schedule_clarification(), extract_session_today(), extract_session_user_id(), extract_tastes_from_history(), format_time_24h(), get_recent_user_messages() (+29 more)

### Community 7 - "Popayan Geodata Index"
Cohesion: 0.10
Nodes (36): haversine(), Distancia en km entre dos coordenadas GPS (fórmula de Haversine).      Consoli, _build_alias_index(), _ensure_index(), _estimate_coords_from_street(), _find_similar_places(), fuzzy_search(), geocode_local() (+28 more)

### Community 8 - "Nexiservice Booking Interceptor"
Cohesion: 0.12
Nodes (36): _normalize(), Reconstruye el contexto completo de una reserva en curso desde el historial., Delegación a la utilidad global de normalización de texto., _recover_last_reservation_context_from_history(), _assert_data_from_db(), _call_confirm_appointment(), clear_booking_state(), _extract_confirmed_name_from_assistant() (+28 more)

### Community 9 - "Admin Session Management"
Cohesion: 0.09
Nodes (31): delete_session(), export_sessions(), list_sessions(), Export sessions (basic JSON export)., Get full session with messages., Delete a session and its messages., List conversations (sessions) with pagination., Session aggregate stats. (+23 more)

### Community 10 - "Nexiservice Tools"
Cohesion: 0.13
Nodes (35): _clean_search_query(), confirm_appointment(), fly_to_business(), get_business_availability(), get_business_mission_vision(), get_business_services(), get_general_info(), get_professional_info() (+27 more)

### Community 11 - "Admin Config Management"
Cohesion: 0.08
Nodes (34): clear_cache(), ConfigUpdate, create_version(), get_config(), _get_config_value(), _get_current_version(), get_status(), health_incidents() (+26 more)

### Community 12 - "Conversation Repair"
Cohesion: 0.11
Nodes (20): ConversationMemory, ConversationRepair, _extract_partial_location(), get_repair_message(), infer_intent(), float, str, core/conversation_repair.py — Motor de reparación conversacional inteligente par (+12 more)

### Community 13 - "Voice Session State"
Cohesion: 0.18
Nodes (22): AudioQualityProfile, bool, bytes, float, int, AsyncClient, BargeInHandler, Detecta y maneja interrupciones del usuario mientras Lyra habla.          Perm (+14 more)

### Community 14 - "Booking Flow Tests"
Cohesion: 0.14
Nodes (26): fail(), get_all_businesses(), get_first_tercero(), get_professionals_for_service(), get_services_for_business(), info(), _normalize(), ok() (+18 more)

### Community 15 - "SENA Learning Tools"
Cohesion: 0.12
Nodes (28): _api_get(), get_actividades_pendientes(), get_clases_hoy(), get_contratos_vencimiento(), get_entregas(), get_estudiantes_ficha(), get_fichas_activas(), get_horario() (+20 more)

### Community 16 - "Main Router & Database"
Cohesion: 0.12
Nodes (22): health_check(), Detailed health check for all Lyra services., ChatRequest, ChatService, Request, BaseModel, check_connection(), bool (+14 more)

### Community 17 - "Tool Adapter & Registry"
Cohesion: 0.13
Nodes (15): LegacyToolAdapter, Any, str, Ejecuta la función legacy inyectando el contexto si es necesario          o sim, Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute., Any, str, Registra una herramienta moderna que cumple con el contrato:         - TOOL_NAM (+7 more)

### Community 18 - "WhatsApp Channel Router"
Cohesion: 0.17
Nodes (22): bool, int, Request, str, BackgroundTasks, clean_map_location(), _create_wp_service(), get_wp_session() (+14 more)

### Community 19 - "Address Normalization Core"
Cohesion: 0.17
Nodes (25): _clean_stt_text(), _compound_num_replace(), _correct_speech(), extract_datetime_local(), extract_datetime_with_llm(), _geocode_cache_get(), _geocode_cache_set(), _in_popayan_bbox() (+17 more)

### Community 20 - "Tool Runner Utilities"
Cohesion: 0.09
Nodes (26): _build_schedule_clarification(), _extract_session_user_id(), _extract_tastes_from_history(), _find_anchored_id_in_messages(), _get_recent_user_messages(), _is_generic_query(), _match_property_id_in_reply(), _normalize() (+18 more)

### Community 21 - "LLM Engine & Middleware"
Cohesion: 0.12
Nodes (15): int, Request, RateLimitMiddleware, Per-IP rate limiter.     max_requests: maximum requests per window.     window, BaseHTTPMiddleware, LLMEngine, float, int (+7 more)

### Community 22 - "Rentus Property Tools"
Cohesion: 0.11
Nodes (20): get_property_detail(), GetPropertyDetailTool, _parse_properties_from_response(), float, int, str, tools/rentus.py — Tool functions for the Rentus project., Obtiene el detalle completo de una propiedad por ID. (+12 more)

### Community 23 - "Streaming STT Pipeline"
Cohesion: 0.11
Nodes (13): ConversationMemory, generate_contextual_response(), AudioQualityProfile, bool, int, str, Mejor texto disponible (final si existe, si no el último parcial)., Procesa texto parcial y retorna estado de intención actualizado. (+5 more)

### Community 24 - "IntelliTaxi Tools"
Cohesion: 0.13
Nodes (16): normalize_address(), Estandariza nomenclatura (Calle → Cl, etc.), cancelar_servicio(), CancelarServicioTool, consultar_conductores_disponibles(), ConsultarConductoresTool, geocodificar_direccion(), GeocodificarDireccionTool (+8 more)

### Community 25 - "Orchestrator Tool Runner"
Cohesion: 0.10
Nodes (20): _extract_session_today(), _inject_ids_into_titles(), date, orchestrator/tool_runner.py — Agent loop con límite estricto de herramientas., Recupera tanto la ciudad como la categoría de la última búsqueda desde los tool, Recupera la lista de negocios mencionados en el historial.     Busca tanto en r, Busca en el historial menciones de servicios, profesionales o negocios en contex, Extrae la fecha base de la sesión para resolver expresiones relativas. (+12 more)

### Community 26 - "API Dependency Injection"
Cohesion: 0.15
Nodes (19): get_chat_service(), get_db(), get_llm(), get_settings(), get_tool_registries(), get_twilio_service(), get_whatsapp_service(), ChatService (+11 more)

### Community 27 - "Interceptor Helpers"
Cohesion: 0.11
Nodes (19): _find_anchored_id_in_messages(), _is_generic_query(), _normalize_time(), orchestrator/interceptors/helpers.py ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Uti, Recupera la lista de negocios del último resultado de herramienta en el historia, Recupera la categoría y ciudad de la última búsqueda de negocios., Normaliza una cadena de hora a formato HH:MM., Retorna True si el texto parece referirse a una entidad genérica     (pronombre (+11 more)

### Community 28 - "Navigation & UI Tools"
Cohesion: 0.12
Nodes (17): navigate_to_company(), str, tools/navigation.py — Herramientas para la navegación programática en la UI., Activa la navegación automática hacia el perfil de una empresa específica., _format_logo(), get_businesses_comparison(), open_business_web(), bool (+9 more)

### Community 29 - "Adaptive Endpoint Control"
Cohesion: 0.16
Nodes (6): float, Segundos desde la última actividad de voz., Estima palabras por segundo basado en el texto acumulado y el tiempo.         Ú, Procesa el texto final de STT de un turno.                  Retorna:, Acumula transcripciones parciales de Twilio y construye hipótesis incrementales., StreamingSTTBuffer

### Community 30 - "Context Builder & Prompt"
Cohesion: 0.29
Nodes (16): build_system_prompt(), _extract_anchored_ids(), _is_followup_reference(), _is_new_request(), _is_trivial_input(), _normalize_text(), _project_system_content(), bool (+8 more)

### Community 31 - "Response Engine & Templates"
Cohesion: 0.17
Nodes (16): _build_business_list(), clear_session_history(), _format_distance(), _get_variations(), _load_templates(), float, int, str (+8 more)

### Community 32 - "Architecture Overview Docs"
Cohesion: 0.15
Nodes (15): API Layer (api/), Core Layer (core/) — Shared Resources, Dependency Injection via FastAPI Depends, Lyra AI — Generative AI Orchestration Engine, Services Layer (services/), Thin Routers — ADR-001, Twilio Sessions in Memory with asyncio.Lock — ADR-004, Lyra AI — README Overview (+7 more)

### Community 33 - "Lyra Hybrid Engine Design"
Cohesion: 0.15
Nodes (15): Motor Híbrido — Cerebro Rápido + Cerebro Lento, Intent Router (Regex + Keywords Classifier), Interceptor Pattern (AOP-lite Pre/Post LLM), LegacyToolAdapter — Gradual Migration Pattern, LLM as Auxiliary Fallback — ADR-002, Orchestrator Layer (orchestrator/), Tool Contract (TOOL_NAME, TOOL_SCHEMA, execute), Tool Registry with Auto-Discovery (+7 more)

### Community 34 - "LLM Utility Functions"
Cohesion: 0.21
Nodes (14): call_llm(), call_llm_async(), extract_json_object(), get_model(), get_openai_client(), Any, float, str (+6 more)

### Community 35 - "Geocoding Architecture Docs"
Cohesion: 0.17
Nodes (15): Geocodificación — Arquitectura Técnica, Auto-Accept Rules (ROOFTOP+RANGE_INTERPOLATED in POPAYAN_URBAN_BBOX), enriched_query Construction (query_base + alias_text by type), geo_human_aliases — Human Truth Table (Phase 2, deferred), core/geo_types.py — LocationType, ResolutionStatus, GeoCandidate, GeoResolution, location_cache — Technical Truth Table (canonical_query key), POPAYAN_URBAN_BBOX and POPAYAN_BBOX_WIDE Thresholds, Post-Resolution Verification (formatted_address must contain query numbers) (+7 more)

### Community 36 - "Intent Router"
Cohesion: 0.23
Nodes (14): detect_intent(), _extract_city(), _extract_date(), _extract_service_name(), _is_spam(), _normalize(), bool, str (+6 more)

### Community 37 - "SENA Interceptors"
Cohesion: 0.35
Nodes (13): _denied(), _entity_type(), _get_roles(), post_execution_interceptor(), pre_llm_interceptor(), Obtiene el horario del instructor y deduplica por código de ficha,     mostrand, _run(), _run_fichas_asignadas() (+5 more)

### Community 38 - "Audio Quality Profiling"
Cohesion: 0.18
Nodes (8): AudioQualityProfile, bool, int, Perfil de calidad de audio de una llamada.     Se actualiza turn a turn para ad, True si la llamada tiene calidad consistentemente baja., True si el usuario habla en frases largas (muchas palabras por turno)., True si el usuario usa frases muy cortas., Timeout total de <Gather> en segundos.

### Community 39 - "Streaming Pipeline Entry"
Cohesion: 0.25
Nodes (8): PartialTranscript, core/streaming_pipeline.py — Pipeline de streaming incremental para Lyra.  Imp, Agrega un fragmento parcial y retorna el texto acumulado mejorado.         Apli, Marca la transcripción como final y aplica todas las correcciones.         Reto, correct_stt_errors(), expand_number_words_in_streets(), Aplica correcciones STT en orden de especificidad:     1. Match exacto     2., Convierte palabras-número a dígitos solo cuando están en contexto de calle/carre

### Community 40 - "Interceptor Manager"
Cohesion: 0.33
Nodes (8): Runs logic before tool execution (e.g. arg patching, guards).     Called from t, Runs logic after tool execution (e.g. updating UI state, map center)., Runs all registered pre-LLM interceptors. Returns a response dict if intercepted, run_post_execution_interceptors(), run_pre_execution_interceptors(), run_pre_llm_interceptors(), Any, str

### Community 41 - "Colombian Address Extraction"
Cohesion: 0.25
Nodes (8): extract_destination_address(), extract_pickup_address(), looks_like_place(), normalize_colombian_address(), Normaliza al formato colombiano estándar.     'carrera cuarta a el # 17 b 28' →, Valida que `text` parezca una ubicación real en Popayán, para descartar     ext, Extrae dirección de recogida del texto libre.     Retorna (dirección, None) o (, Extrae dirección de destino del texto libre.

### Community 42 - "Chat Service"
Cohesion: 0.29
Nodes (6): Any, Settings, str, Returns the conversation history for a given session., Removes technical markers like [BIZ:123], [ANALIZANDO DATOS], etc. from the fina, _strip_debug_markers()

### Community 43 - "Geocoding Overview & Rentus"
Cohesion: 0.29
Nodes (7): Hypothesis: Colombian address nomenclature mathematically unique within city, Geocodificación — Overview del Refactor, Geocoding Pipeline: Cache→Google→Nominatim→CONTEXT_GATHERING, Decision: popayan_geodata.py removed from geocoding flow, Geocodificación — Archivos Modificados, tools/popayan_geodata.py — Preserved for STT corrections only, NOT geocoding, rentus.yaml — Rentus Real Estate Assistant Project Config

### Community 44 - "Voice STT Tuning Docs"
Cohesion: 0.43
Nodes (7): _build_speech_attrs() — Centralized Twilio speechModel/language/enhanced config, Future: Twilio Media Streams + Deepgram Nova-2 real-time STT (replaces turn-based Gather), Decision: googlev2 as default STT model (es-CO native, premium, phone_call enhanced doesn't support es-CO), STT Root Causes: experimental_conversations model, Confidence gating, loud-speak message, fixed speechTimeout, Decision: speechTimeout=auto for address capture (adaptive end-of-speech), Reconocimiento de Voz Twilio — Tuning para usuarios reales (Colombia), Decision: text-first confidence — Twilio Confidence is unreliable, text presence is the gate

### Community 45 - "Geocoding Phase 1 Plan"
Cohesion: 0.33
Nodes (6): core/address_utils.py — NLP/STT utilities (cleaned of popayan_geodata), migrations/003_location_cache.sql — confidence + location_type columns, Pending Router Migrations to run_pipeline() (twilio, whatsapp, speech_processor, intellitaxi tool), Geocodificación — Plan Fase 1, Phase 1 Success Criteria (30d resolution rate, <10% FAILED, 40% cache repeat), classify_speech_quality — Rewritten to text-first logic

### Community 46 - "Response Generation"
Cohesion: 0.40
Nodes (6): _handle_conversational(), Maneja intents conversacionales (greeting, farewell, identity, capabilities)., generate_response(), Genera una respuesta a partir de las plantillas YAML.      Args:         conv, Atajo para generate_response con resolución automática de personalidad., _resp()

### Community 47 - "Project Configurations"
Cohesion: 0.40
Nodes (5): intellitaxi.yaml — TaxBelalcazar Telephony Project Config, nexiservice.yaml — NexiService Colombia Project Config, personalities.yaml — Global Personality Catalog (lyra, nexo), response_templates.yaml — Response Template Bank (lyra, nexo, sena personalities), Projects in Production (NexiService, Rentus, IntelliTaxi)

### Community 48 - "Appointment Utilities"
Cohesion: 0.40
Nodes (5): find_anchored_id_in_messages(), Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible., Recupera los detalles de una reserva pendiente de confirmación., recover_appointment_details_from_history(), int

### Community 49 - "TTS Audio Router"
Cohesion: 0.50
Nodes (3): Request, str, serve_audio()

## Knowledge Gaps
- **61 isolated node(s):** `allow`, `int`, `Request`, `int`, `int` (+56 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_connection()` connect `Admin Session Management` to `Geocoding Types & Candidates`, `App Config & Logging`, `Nexiservice Booking Interceptor`, `Nexiservice Tools`, `Admin Config Management`, `Booking Flow Tests`, `Main Router & Database`, `City Data Cache`, `API Dependency Injection`, `Interceptor Helpers`, `Navigation & UI Tools`?**
  _High betweenness centrality (0.164) - this node is a cross-community bridge._
- **Why does `FastAPI` connect `Admin Session Management` to `Twilio Voice Router`, `Browser Voice & TTS`, `App Config & Logging`, `Admin Config Management`, `Main Router & Database`, `Tool Adapter & Registry`, `TTS Audio Router`, `WhatsApp Channel Router`, `LLM Engine & Middleware`, `API Dependency Injection`?**
  _High betweenness centrality (0.123) - this node is a cross-community bridge._
- **Why does `normalize_text()` connect `Google Geocoding Service` to `Intent Router`, `Scheduling & Shared Utils`, `Popayan Geodata Index`, `Nexiservice Booking Interceptor`, `Nexiservice Tools`, `Tool Runner Utilities`, `Orchestrator Tool Runner`, `Interceptor Helpers`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `str` (e.g. with `BargeInHandler` and `ConversationMemory`) actually correct?**
  _`str` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 22 inferred relationships involving `AudioQualityProfile` (e.g. with `AudioQualityProfile` and `bool`) actually correct?**
  _`AudioQualityProfile` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 22 inferred relationships involving `ConversationMemory` (e.g. with `AudioQualityProfile` and `bool`) actually correct?**
  _`ConversationMemory` has 22 INFERRED edges - model-reasoned connections that need verification._
- **What connects `allow`, `int`, `Request` to the rest of the system?**
  _458 weakly-connected nodes found - possible documentation gaps or missing edges._