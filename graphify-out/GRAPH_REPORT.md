# Graph Report - virtualtIA  (2026-06-18)

## Corpus Check
- 100 files · ~125,618 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2114 nodes · 4570 edges · 101 communities (93 shown, 8 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 411 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9e675a1f`
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
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 96|Community 96]]
- [[_COMMUNITY_Community 98|Community 98]]
- [[_COMMUNITY_Community 99|Community 99]]
- [[_COMMUNITY_Community 101|Community 101]]
- [[_COMMUNITY_Community 102|Community 102]]
- [[_COMMUNITY_Community 105|Community 105]]
- [[_COMMUNITY_Community 108|Community 108]]

## God Nodes (most connected - your core abstractions)
1. `get_connection()` - 64 edges
2. `str` - 54 edges
3. `ConversationMemory` - 45 edges
4. `process_speech()` - 43 edges
5. `resolve_location_entity()` - 43 edges
6. `AudioQualityProfile` - 42 edges
7. `Decision` - 40 edges
8. `VoiceCallEngine` - 38 edges
9. `ResolutionStatus` - 34 edges
10. `decide()` - 33 edges

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

## Communities (101 total, 8 thin omitted)

### Community 0 - "Twilio Voice Router"
Cohesion: 0.15
Nodes (11): bool, str, Procesamiento de voz para el flujo de taxi en Popayán.     Responsabilidades: l, Construye un índice (alias_normalizado, canónico) ordenado de mayor a menor long, Elimina artefactos comunes del STT de Twilio., Elimina saludos y preámbulos conversacionales., Normalización fonética simple para español colombiano., Aplica correcciones STT para topónimos de Popayán.         Estrategias en orden (+3 more)

### Community 1 - "Geocoding Types & Candidates"
Cohesion: 0.08
Nodes (66): Elimina saludos y relleno del inicio/fin del texto., _strip_preamble(), GeoCandidate, GeoResolution, in_urban_bbox(), in_wide_bbox(), LocationType, bool (+58 more)

### Community 2 - "STT Enhancement & Local Search"
Cohesion: 0.07
Nodes (36): Any, bytes, WebSocket, AudioEncoding, audio_stream(), _get_ws_buffer(), Resuelve call_uuid desde query string o metadata JSON., Resuelve call_uuid desde query string o metadata JSON. (+28 more)

### Community 3 - "Google Geocoding Service"
Cohesion: 0.12
Nodes (18): bool, str, int, str, CallSession, CallState, Procesa el input de voz y retorna el siguiente paso TwiML., Crea el servicio, limpia la sesión y retorna TwiML de cierre. (+10 more)

### Community 4 - "Browser Voice & TTS"
Cohesion: 0.13
Nodes (20): AsyncOpenAI, _clean_for_tts(), _edge_tts_sync_bytes(), _is_gibberish(), bool, bytes, float, str (+12 more)

### Community 5 - "App Config & Logging"
Cohesion: 0.09
Nodes (42): delete_session(), export_sessions(), list_sessions(), Export sessions (basic JSON export)., Get full session with messages., Delete a session and its messages., List conversations (sessions) with pagination., Session aggregate stats. (+34 more)

### Community 6 - "Scheduling & Shared Utils"
Cohesion: 0.12
Nodes (33): Normaliza fecha/hora preferidas desde args del LLM o el historial reciente., _resolve_schedule_datetime(), build_schedule_clarification(), extract_session_today(), extract_session_user_id(), extract_tastes_from_history(), format_time_24h(), get_recent_user_messages() (+25 more)

### Community 7 - "Popayan Geodata Index"
Cohesion: 0.06
Nodes (53): haversine(), Distancia en km entre dos coordenadas GPS (fórmula de Haversine).      Consoli, _build_alias_index(), _ensure_index(), _estimate_coords_from_street(), _find_similar_places(), fuzzy_search(), geocode_local() (+45 more)

### Community 8 - "Nexiservice Booking Interceptor"
Cohesion: 0.13
Nodes (11): int, str, CallSession, GeoSessionSnapshot, _MemoryBackend, Almacén de sesiones de llamada por call_uuid.  Soporta memoria (desarrollo) y, Fachada de sesiones — memoria o Redis según VOICE_SESSION_STORE., Snapshot serializable del estado geo de una sesión. (+3 more)

### Community 9 - "Admin Session Management"
Cohesion: 0.14
Nodes (22): float, str, get_pusher_client(), str, trigger_pusher_event(), get_voice_engine(), Retorna el singleton VoiceEngine, creándolo si aún no existe., load_project_config() (+14 more)

### Community 10 - "Nexiservice Tools"
Cohesion: 0.14
Nodes (33): _clean_search_query(), confirm_appointment(), fly_to_business(), get_business_availability(), get_business_mission_vision(), get_business_reviews(), get_business_services(), get_professional_info() (+25 more)

### Community 11 - "Admin Config Management"
Cohesion: 0.08
Nodes (33): clear_cache(), ConfigUpdate, create_version(), get_config(), _get_config_value(), _get_current_version(), get_status(), health_check() (+25 more)

### Community 12 - "Conversation Repair"
Cohesion: 0.07
Nodes (38): ConversationMemory, ConversationRepair, _extract_partial_location(), get_repair_message(), infer_intent(), bool, float, str (+30 more)

### Community 13 - "Voice Session State"
Cohesion: 0.12
Nodes (43): AsyncClient, AudioQualityProfile, bool, bytes, float, int, Request, WebSocket (+35 more)

### Community 14 - "Booking Flow Tests"
Cohesion: 0.13
Nodes (28): fail(), get_all_businesses(), get_first_tercero(), get_professionals_for_service(), get_services_for_business(), info(), _normalize(), ok() (+20 more)

### Community 15 - "SENA Learning Tools"
Cohesion: 0.09
Nodes (41): _denied(), _entity_type(), _get_roles(), post_execution_interceptor(), pre_llm_interceptor(), Obtiene el horario del instructor y deduplica por código de ficha,     mostrand, _run(), _run_fichas_asignadas() (+33 more)

### Community 16 - "Main Router & Database"
Cohesion: 0.15
Nodes (20): get_chat_service(), get_db(), get_llm(), get_settings(), get_tool_registries(), get_twilio_service(), get_whatsapp_service(), ChatService (+12 more)

### Community 17 - "Tool Adapter & Registry"
Cohesion: 0.20
Nodes (10): Auto-Accept Rules (ROOFTOP+RANGE_INTERPOLATED in POPAYAN_URBAN_BBOX), core/geo_types.py — LocationType, ResolutionStatus, GeoCandidate, GeoResolution, core/address_utils.py — NLP/STT utilities (cleaned of popayan_geodata), core/geocoder_service.py — run_pipeline, _google_get_candidates, _nominatim_get_candidates, _decide, migrations/003_location_cache.sql — confidence + location_type columns, Pending Router Migrations to run_pipeline() (twilio, whatsapp, speech_processor, intellitaxi tool), Geocodificación — Plan Fase 1, Phase 1 Success Criteria (30d resolution rate, <10% FAILED, 40% cache repeat) (+2 more)

### Community 18 - "WhatsApp Channel Router"
Cohesion: 0.16
Nodes (25): bool, int, Request, str, BackgroundTasks, clean_map_location(), _create_wp_service(), get_wp_session() (+17 more)

### Community 19 - "Address Normalization Core"
Cohesion: 0.38
Nodes (7): _key(), MatchType, int, _rank(), Orden = prioridad. Un tipo superior siempre vence a uno inferior aunque     el, Rango de prioridad para comparar candidatos. Los tipos textuales     (SUBSTRING, IntEnum

### Community 20 - "Tool Runner Utilities"
Cohesion: 0.12
Nodes (19): _build_schedule_clarification(), _extract_session_user_id(), _extract_tastes_from_history(), _is_generic_query(), _match_property_id_in_reply(), _normalize(), bool, str (+11 more)

### Community 21 - "LLM Engine & Middleware"
Cohesion: 0.11
Nodes (36): decide(), Mapea un LocationMatch a la acción del flujo. Precisión sobre recall.      La, Resuelve `text` a una entidad del catálogo con tipo y confianza.      scope: lis, Resuelve `text` a una entidad del catálogo con tipo y confianza.      scope: lis, Resuelve `text` a una entidad del catálogo con tipo y confianza.      scope: l, resolve_location_entity(), tests/test_location_match.py — Cobertura de la resolución de ubicaciones precis, villa del norte' es un barrio; no debe convertirse en 'SENA Norte' por     la pa (+28 more)

### Community 22 - "Rentus Property Tools"
Cohesion: 0.11
Nodes (20): get_property_detail(), GetPropertyDetailTool, _parse_properties_from_response(), float, int, str, tools/rentus.py — Tool functions for the Rentus project., Obtiene el detalle completo de una propiedad por ID. (+12 more)

### Community 23 - "Streaming STT Pipeline"
Cohesion: 0.09
Nodes (24): _extract_session_today(), _inject_ids_into_titles(), date, orchestrator/tool_runner.py — Agent loop con límite estricto de herramientas., Recupera tanto la ciudad como la categoría de la última búsqueda desde los tool, Recupera la lista de negocios mencionados en el historial.     Busca tanto en r, Busca en el historial menciones de servicios, profesionales o negocios en contex, Extrae la fecha base de la sesión para resolver expresiones relativas. (+16 more)

### Community 24 - "IntelliTaxi Tools"
Cohesion: 0.08
Nodes (40): _clean_stt_text(), _compound_num_replace(), _correct_speech(), extract_datetime_local(), extract_datetime_with_llm(), _geocode_cache_get(), _geocode_cache_set(), _in_popayan_bbox() (+32 more)

### Community 25 - "Orchestrator Tool Runner"
Cohesion: 0.13
Nodes (23): bool, bytes, int, str, _extract_transcription_text(), _guess_encoding(), _is_whisper_model(), _mulaw_to_wav() (+15 more)

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
Cohesion: 0.12
Nodes (21): bool, float, int, str, float, GeographicMatcher, _haversine(), _is_in_popayan() (+13 more)

### Community 30 - "Context Builder & Prompt"
Cohesion: 0.21
Nodes (26): object, Any, AsyncClient, bool, CallSession, float, str, SessionStore (+18 more)

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
Cohesion: 0.12
Nodes (19): float, str, check_connection(), bool, Quick ping to verify MySQL is reachable., Path, geocode_api(), health() (+11 more)

### Community 35 - "Geocoding Architecture Docs"
Cohesion: 0.22
Nodes (10): enriched_query Construction (query_base + alias_text by type), geo_human_aliases — Human Truth Table (Phase 2, deferred), location_cache — Technical Truth Table (canonical_query key), POPAYAN_URBAN_BBOX and POPAYAN_BBOX_WIDE Thresholds, Post-Resolution Verification (formatted_address must contain query numbers), Alias Learning Rules (create/increment/degrade by success_rate and recency), Alias Ranking Formula (0.50×success_rate + 0.35×recency + 0.15×type_weight), alias_type: GOOGLE_INFERRED, NEIGHBORHOOD, LANDMARK (SECTOR/FREE_TEXT eliminated) (+2 more)

### Community 36 - "Intent Router"
Cohesion: 0.17
Nodes (18): _build_hint_vocab(), Construye la cadena de hints model-aware desde los catálogos (cacheada)., Repara la grafía de nombres de lugar en `text` usando el catálogo, vía     simi, repair_location_transcription(), tests/test_stt_repair.py — Cobertura de la capa STT anterior al resolver: repar, Hola' (u otro saludo) jamás debe convertirse en 'Popayán' aunque el LLM     alu, _stub_openai(), test_deepgram_hints_are_single_tokens() (+10 more)

### Community 37 - "SENA Interceptors"
Cohesion: 0.21
Nodes (14): call_llm(), call_llm_async(), extract_json_object(), get_model(), get_openai_client(), Any, float, str (+6 more)

### Community 38 - "Audio Quality Profiling"
Cohesion: 0.12
Nodes (13): bool, True si la llamada tiene calidad consistentemente baja., True si el usuario habla en frases largas (muchas palabras por turno)., True si el usuario usa frases muy cortas., True si la llamada tiene calidad consistentemente baja., True si el usuario habla en frases largas (muchas palabras por turno)., True si el usuario usa frases muy cortas., True si la llamada tiene calidad consistentemente baja. (+5 more)

### Community 39 - "Streaming Pipeline Entry"
Cohesion: 0.04
Nodes (71): _build_dtmf_gather(), _cache_audio(), _generate_play_twiml(), _generate_say_twiml(), _generate_tts_audio(), _get_base_url_for_twilio(), Almacena audio en cache y retorna un ID único., Almacena audio en cache y retorna un ID único. (+63 more)

### Community 40 - "Interceptor Manager"
Cohesion: 0.33
Nodes (8): Runs logic before tool execution (e.g. arg patching, guards).     Called from t, Runs logic after tool execution (e.g. updating UI state, map center)., Runs all registered pre-LLM interceptors. Returns a response dict if intercepted, run_post_execution_interceptors(), run_pre_execution_interceptors(), run_pre_llm_interceptors(), Any, str

### Community 41 - "Colombian Address Extraction"
Cohesion: 0.13
Nodes (15): LegacyToolAdapter, Any, str, Ejecuta la función legacy inyectando el contexto si es necesario          o sim, Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute., Any, str, Registra una herramienta moderna que cumple con el contrato:         - TOOL_NAM (+7 more)

### Community 42 - "Chat Service"
Cohesion: 0.11
Nodes (16): int, Request, RateLimitMiddleware, gateway/middleware.py — Rate limiting en memoria (dict + timestamp).  Simple i, Per-IP rate limiter.     max_requests: maximum requests per window.     window, BaseHTTPMiddleware, str, Configura un logger estándar con salida a consola y archivo rotativo. (+8 more)

### Community 43 - "Geocoding Overview & Rentus"
Cohesion: 0.18
Nodes (10): Hypothesis: Colombian address nomenclature mathematically unique within city, Decisión sobre popayan_geodata.py, Estructura de archivos del sistema nuevo, Geocodificación — Overview del Refactor, Hipótesis central (validada en revisión arquitectónica), Por qué se hizo este refactor, Qué se construyó en cambio, Geocoding Pipeline: Cache→Google→Nominatim→CONTEXT_GATHERING (+2 more)

### Community 44 - "Voice STT Tuning Docs"
Cohesion: 0.21
Nodes (12): intellitaxi.yaml — TaxBelalcazar Telephony Project Config, nexiservice.yaml — NexiService Colombia Project Config, personalities.yaml — Global Personality Catalog (lyra, nexo), response_templates.yaml — Response Template Bank (lyra, nexo, sena personalities), Projects in Production (NexiService, Rentus, IntelliTaxi), _build_speech_attrs() — Centralized Twilio speechModel/language/enhanced config, Future: Twilio Media Streams + Deepgram Nova-2 real-time STT (replaces turn-based Gather), Decision: googlev2 as default STT model (es-CO native, premium, phone_call enhanced doesn't support es-CO) (+4 more)

### Community 45 - "Geocoding Phase 1 Plan"
Cohesion: 0.11
Nodes (18): Archivos ACTUALIZADOS, Archivos NO MODIFICADOS (pero relacionados), Archivos NUEVOS, Archivos REESCRITOS, `core/address_utils.py`, `core/address_utils.py`, `core/geo_types.py`, `core/geocoder_service.py` (+10 more)

### Community 46 - "Response Generation"
Cohesion: 0.25
Nodes (6): BaseSettings, str, core/config.py — Configuración centralizada desde .env con pydantic-settings., Resolve model path relative to project root., Resolve model path relative to project root., Settings

### Community 47 - "Project Configurations"
Cohesion: 0.15
Nodes (14): _empty_stats(), get_stats(), hourly_stats(), intent_stats(), _parse_period(), Intent breakdown (placeholder — Lyra uses LLM, not fixed intents)., Get usage statistics for a given period., Get hourly message distribution. (+6 more)

### Community 48 - "Appointment Utilities"
Cohesion: 0.11
Nodes (17): Arquitectura del Sistema, Convenciones de Desarrollo, Cómo Agregar un Nuevo Proyecto, Desarrollado por, Despliegue y Configuración, Estructura del Proyecto, Motor de IA Híbrido, Panel de Administración (+9 more)

### Community 49 - "TTS Audio Router"
Cohesion: 0.11
Nodes (30): _base_decision(), _best_for_entity(), _build_catalog(), _content_tokens(), _Entity, _has_content(), _is_all_filler(), is_filler() (+22 more)

### Community 52 - "Barge-In Interruption"
Cohesion: 0.21
Nodes (17): bool, bytes, float, int, str, detect_end_of_utterance(), detect_end_of_utterance_pcm16(), is_speech_frame() (+9 more)

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
Cohesion: 0.05
Nodes (77): str, _build_speech_attrs(), _cfg(), _cfg_int(), _create_service(), _deepgram_language(), _disambiguation_question(), es_numero_troncal_o_empresa() (+69 more)

### Community 71 - "Community 71"
Cohesion: 0.20
Nodes (10): Construcción de enriched_query, Geocodificación — Arquitectura Técnica, Máquina de Estados, Reglas de Auto-Aceptación, Separación de Responsabilidades, Tipos de Datos Core (`core/geo_types.py`), Umbrales Popayán, Verdad Humana: `geo_human_aliases` *(Fase 2 — diferida)* (+2 more)

### Community 73 - "Community 73"
Cohesion: 0.21
Nodes (6): bool, bytes, float, int, Silencia la captura durante un playback y descarta lo ya acumulado., WsAudioBuffer

### Community 74 - "Community 74"
Cohesion: 0.11
Nodes (17): 1. Lyra Python (.env), 2. Redis, 3. Dependencias Lyra, 4. Apagar FreeSWITCH viejo (Twilio), 5. FreeSWITCH nuevo, Arquitectura objetivo, Checklist de despliegue, Checklist llamada real (+9 more)

### Community 75 - "Community 75"
Cohesion: 0.05
Nodes (35): generate_contextual_response(), _get_contextual_hints(), bool, float, int, str, Mejor texto disponible (final si existe, si no el último parcial)., Segundos desde la última actividad de voz. (+27 more)

### Community 76 - "Community 76"
Cohesion: 0.24
Nodes (7): LLMEngine, float, int, str, core/llm_engine.py — Tool calling para LLMs (OpenRouter/OpenAI-compatible).  N, Parse a failed_generation string like <function=name>{"k":v}</function>, Fix type mismatches: cast int→str, null→default, unwrap 'properties' wrapper.

### Community 77 - "Community 77"
Cohesion: 0.18
Nodes (11): _get_cached_audio(), Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Sirve los audios de TTS cacheados en memoria para Twilio., Recupera audio del cache., Recupera audio del cache. (+3 more)

### Community 78 - "Community 78"
Cohesion: 0.14
Nodes (17): extract_destination_address(), extract_pickup_address(), _is_correction_request(), looks_like_place(), normalize_colombian_address(), _parse_si_no(), bool, Normaliza al formato colombiano estándar.     'carrera cuarta a el # 17 b 28' → (+9 more)

### Community 79 - "Community 79"
Cohesion: 0.18
Nodes (11): _find_anchored_id_in_messages(), _get_recent_user_messages(), float, int, Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible., Recupera los detalles de una reserva pendiente de confirmación., Toma los ultimos mensajes del usuario para completar datos omitidos por el LLM., Main agent loop. Orchestrates interceptors, LLM calls, and tool execution. (+3 more)

### Community 80 - "Community 80"
Cohesion: 0.29
Nodes (16): build_system_prompt(), _extract_anchored_ids(), _is_followup_reference(), _is_new_request(), _is_trivial_input(), _normalize_text(), _project_system_content(), bool (+8 more)

### Community 84 - "Community 84"
Cohesion: 0.06
Nodes (59): SessionUpdate, bool, float, Request, str, BaseModel, _echo_tokens(), _flush_audio_turn() (+51 more)

### Community 85 - "Community 85"
Cohesion: 0.12
Nodes (15): Archivos creados, Backend Laravel — checklist, Concurrencia (40–60 llamadas), Dialplan mínimo (ejemplo), Fase 1 — Validar backend sin Twilio, Fase 2 — Apagar FreeSWITCH viejo (Twilio), Fase 3 — FreeSWITCH nuevo (sin Twilio), Fase 4 — Prueba de llamada real (+7 more)

### Community 86 - "Community 86"
Cohesion: 0.23
Nodes (9): Any, AsyncClient, bool, float, str, Geocodifica origen/destino y crea el servicio en Laravel.         Usado por tes, Geocodifica origen/destino y crea el servicio en Laravel.         Usado por tes, POST al backend Laravel.          Returns: (success, user_message, response_js (+1 more)

### Community 87 - "Community 87"
Cohesion: 0.20
Nodes (9): int, Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Timeout total de <Gather> en segundos., Timeout total de <Gather> en segundos., Repara direcciones callejeras mangled por STT.     'carrera 4 a eb 1728' → 'car, Timeout total de <Gather> en segundos. (+1 more)

### Community 88 - "Community 88"
Cohesion: 0.23
Nodes (14): detect_intent(), _extract_city(), _extract_date(), _extract_service_name(), _is_spam(), _normalize(), bool, str (+6 more)

### Community 90 - "Community 90"
Cohesion: 0.22
Nodes (9): bool, float, int, str, StreamReader, FreeSwitchESLClient, Cliente ESL mínimo para FreeSWITCH — uuid_broadcast / uuid_kill., Conexión corta por comando (sin suscripción de eventos). (+1 more)

### Community 91 - "Community 91"
Cohesion: 0.13
Nodes (15): correct_stt_errors(), Aplica correcciones STT en orden de especificidad:     1. Match exacto     2., Aplica correcciones STT en orden de especificidad:     1. Match exacto     2., _aggressive_normalize(), preprocess_stt(), Normalización agresiva previa, para audio telefónico muy degradado.      - Eli, Normalización agresiva previa, para audio telefónico muy degradado.      - Eli, Normalización agresiva previa, para audio telefónico muy degradado.      - Eli (+7 more)

### Community 92 - "Community 92"
Cohesion: 0.11
Nodes (21): _best_catalog_snap(), bigram_similarity(), _build_phonetic_repair_index(), combined_score(), _normalize_street_abbreviations(), phonetic_key(), str, core/stt_enhancer.py — Motor de mejora de STT para audio telefónico colombiano. (+13 more)

### Community 93 - "Community 93"
Cohesion: 0.17
Nodes (11): Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Parámetros de endpointing adaptativos para el próximo Gather., Wrapper que usa los parámetros adaptativos de la sesión., Wrapper que usa los parámetros adaptativos de la sesión., Wrapper que usa los parámetros adaptativos de la sesión. (+3 more)

### Community 94 - "Community 94"
Cohesion: 0.17
Nodes (14): get_async_openai_client(), Get or create the async OpenAI/OpenRouter client.     Uses AsyncOpenAI so that, AsyncClient, CallSession, float, str, Cliente HTTP para crear solicitudes de taxi en el backend Laravel.  Mantiene e, Envía solicitudes telefónicas al backend IntelliTaxi. (+6 more)

### Community 95 - "Community 95"
Cohesion: 0.25
Nodes (5): bool, str, Idempotencia de creación de servicio por call_uuid (evita duplicados en reintent, Marca call_uuid como ya enviado al backend., SubmissionGuard

### Community 96 - "Community 96"
Cohesion: 0.33
Nodes (8): Any, bool, str, es_numero_troncal_o_empresa(), limpiar_numero(), Utilidades de normalización de teléfono — agnósticas al canal., Resuelve el teléfono real del cliente desde número directo o headers SIP., resolve_caller_phone()

### Community 98 - "Community 98"
Cohesion: 0.13
Nodes (15): _build_local_match_index(), Búsqueda local en el catálogo de barrios/landmarks de Popayán     (popayan_geod, Búsqueda local en el catálogo de barrios/landmarks de Popayán     (popayan_geod, _try_local_match(), _alias_covers_input(), True si el alias cubre el input sin dejar palabras de contenido sueltas., True si el alias cubre el input sin dejar palabras de contenido sueltas., True si el alias cubre el input sin dejar palabras de contenido sueltas. (+7 more)

### Community 99 - "Community 99"
Cohesion: 0.10
Nodes (21): str, bytes, int, str, bytes, str, ffmpeg_executable(), log_ffmpeg_diagnostics() (+13 more)

### Community 101 - "Community 101"
Cohesion: 0.40
Nodes (5): find_anchored_id_in_messages(), Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible., Recupera los detalles de una reserva pendiente de confirmación., recover_appointment_details_from_history(), int

### Community 102 - "Community 102"
Cohesion: 0.29
Nodes (6): Any, Settings, str, Returns the conversation history for a given session., Removes technical markers like [BIZ:123], [ANALIZANDO DATOS], etc. from the fina, _strip_debug_markers()

### Community 105 - "Community 105"
Cohesion: 0.33
Nodes (6): classify_speech_quality(), Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h, Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h, Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h, Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h, Clasifica la calidad del turno de voz.      Filosofía (corregida): Twilio YA h

### Community 108 - "Community 108"
Cohesion: 0.40
Nodes (5): _format_logo(), get_businesses_comparison(), Formatea la ruta del logo para que sea accesible desde el frontend., Obtiene datos comparativos para una lista de negocios., Format business logo to absolute URL.

## Knowledge Gaps
- **201 isolated node(s):** `PreToolUse`, `allow`, `int`, `Request`, `int` (+196 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `FastAPI` connect `Project Configurations` to `LLM Utility Functions`, `App Config & Logging`, `Community 70`, `Colombian Address Extraction`, `Chat Service`, `Admin Config Management`, `Community 76`, `Admin Session Management`, `Main Router & Database`, `WhatsApp Channel Router`, `Community 84`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `get_connection()` connect `App Config & Logging` to `Geocoding Types & Candidates`, `LLM Utility Functions`, `Community 72`, `Nexiservice Tools`, `Admin Config Management`, `Community 108`, `Booking Flow Tests`, `Project Configurations`, `Main Router & Database`, `Navigation & UI Tools`, `Response Engine & Templates`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `ToolRegistry` connect `Colombian Address Extraction` to `Chat Service`, `Community 79`, `Project Configurations`, `Tool Runner Utilities`, `Streaming STT Pipeline`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `str` (e.g. with `BargeInHandler` and `ConversationMemory`) actually correct?**
  _`str` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `ConversationMemory` (e.g. with `AsyncClient` and `AudioQualityProfile`) actually correct?**
  _`ConversationMemory` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PreToolUse`, `allow`, `int` to the rest of the system?**
  _938 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Twilio Voice Router` be split into smaller, more focused modules?**
  _Cohesion score 0.14814814814814814 - nodes in this community are weakly interconnected._