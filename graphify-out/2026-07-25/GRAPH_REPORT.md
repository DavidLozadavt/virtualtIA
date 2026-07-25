# Graph Report - virtualtIA  (2026-07-24)

## Corpus Check
- 124 files · ~169,098 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2356 nodes · 5657 edges · 96 communities (93 shown, 3 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 669 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ebabc9a4`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Geo Bbox/Type Primitives
- Location Match Ranking
- Orchestrator History Helpers
- IntelliTaxi Tools
- Telephony Call Handler / LLM Client
- Streaming STT Buffer & Barge-in
- Browser Voice STT/TTS
- STT Hint Vocabulary Builder
- SchoolSena Interceptors
- Local Catalog Match / Streaming Pipeline
- DB Connection & NexiService
- Schedule Datetime Resolution
- Conversation Memory
- Runtime Config API
- WhatsApp Service & Address Bbox
- FreeSWITCH Transcript Normalization
- WhatsApp Router
- Rate Limit Middleware
- FreeSWITCH Migration Endpoints
- Audio VAD (mulaw)
- Memory Manager (Trust/Personality)
- FreeSWITCH Inbound Call + TTS Store
- FastAPI Dependency Injection
- Main Chat Endpoint
- Tool Runner History Recovery
- Address Normalization & WP Session
- FreeSWITCH Test/Process Endpoints
- Interceptor Base / Generic Query
- Audio Quality Profile
- HTTP Timeout/Payload Builder
- Navigation Tool
- Response Template Engine
- VPS Deploy (No Twilio)
- README Overview
- Rentus Property Search
- Reverse Geocoding Proxy
- WS Audio Buffer Resolution
- Structured Logger & Intent Router
- Geocoding Phase-1 Plan Doc
- Context Builder (System Prompt)
- User Lookup/Create
- Audio Preprocessing (Resample)
- STT Prompt/Model Resolution
- LLM Datetime Extraction
- FreeSWITCH Migration Guide Doc
- Geocoding Files-Changed Doc
- Admin Session Update
- API Layer Architecture Doc
- ESL Client (FreeSWITCH)
- System Design Philosophy Doc
- Tool Registry
- Legacy Tool Adapter
- Admin Stats API
- STT Service (OpenAI/Groq)
- Interceptor Manager
- ffmpeg Binary Resolver
- Phone Number Utils
- Mulaw/WAV Conversion
- Max Utterance Duration Gate
- WS Buffer Mute/Flush State
- Geocoding Alias Learning Rules
- Personality Resolution
- TTS Synthesis
- ARCHITECTURE ADR List
- Lua Call Script Helpers
- FreeSWITCH Recording Upload
- Claude Settings Hooks
- Claude Local Permissions
- DB Migration Runner
- Low-Precision Cache Diagnostic Script
- SchoolSena YAML Config
- 4. Caller inventory (contracts a wrapper must preserve)
- Cronología de lo que se hizo
- Despliegue Lyra Voice V2 — streaming full-duplex (mod_audio_stream)
- FreeSWITCH ↔ Lyra — arquitectura de integración (Voice V2)
- _OIFakeBackend
- runtime.py
- test_audio_file_store.py
- address_utils.py
- test_runtime_hangup_race.py
- Handoff — Unificación del STT en OpenAI `gpt-4o-mini-transcribe` (2026-07-19)
- Handoff — Despliegue Lyra Voice V2 a producción (2026-07-19)
- orchestrator.py
- D. LegacyToolAdapter
- Colombian Urban Address Nomenclature — Verified Reference
- Path
- Lyra Voice V2 — Implementación (2026-07-19)

## God Nodes (most connected - your core abstractions)
1. `VoiceCallRuntime` - 76 edges
2. `CallSession` - 67 edges
3. `TurnOrchestrator` - 66 edges
4. `get_connection()` - 60 edges
5. `Decision` - 46 edges
6. `parse_co_address()` - 44 edges
7. `TranscriptEvent` - 44 edges
8. `ResolutionStatus` - 42 edges
9. `strip_accents()` - 42 edges
10. `SessionStore` - 42 edges

## Surprising Connections (you probably didn't know these)
- `Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute.` --rationale_for--> `D. LegacyToolAdapter`  [EXTRACTED]
  orchestrator/tool_adapter.py → ARCHITECTURE.md
- `get_db()` --calls--> `get_connection()`  [EXTRACTED]
  api/dependencies.py → core/database.py
- `TestCreateServiceRequest` --uses--> `TelephonyBackendClient`  [INFERRED]
  api/routers/freeswitch.py → services/telephony/backend_client.py
- `TestCreateServiceRequest` --uses--> `TurnNLU`  [INFERRED]
  api/routers/freeswitch.py → services/voice/nlu.py
- `TestCreateServiceRequest` --uses--> `VoiceCallRuntime`  [INFERRED]
  api/routers/freeswitch.py → services/voice/runtime.py

## Import Cycles
- 1-file cycle: `api/dependencies.py -> api/dependencies.py`
- 1-file cycle: `orchestrator/tool_runner.py -> orchestrator/tool_runner.py`
- 2-file cycle: `api/dependencies.py -> services/chat_service.py -> api/dependencies.py`
- 2-file cycle: `api/dependencies.py -> services/whatsapp_service.py -> api/dependencies.py`
- 2-file cycle: `orchestrator/tool_registry.py -> orchestrator/tool_runner.py -> orchestrator/tool_registry.py`

## Communities (96 total, 3 thin omitted)

### Community 0 - "Geo Bbox/Type Primitives"
Cohesion: 0.06
Nodes (95): GeoCandidate, GeoResolution, GeoSessionState, in_urban_bbox(), in_wide_bbox(), LocationType, bool, Enum (+87 more)

### Community 1 - "Location Match Ranking"
Cohesion: 0.14
Nodes (33): aggressive_place_recovery(), _best_for_entity(), _build_catalog(), catalog_terms(), _content_tokens(), _Entity, _has_content(), _is_all_filler() (+25 more)

### Community 2 - "Orchestrator History Helpers"
Cohesion: 0.11
Nodes (38): _assert_data_from_db(), BookingState, _call_confirm_appointment(), clear_booking_state(), _extract_confirmed_name_from_assistant(), _extract_name_from_messages(), _filter_services_by_subcategory(), get_booking_state() (+30 more)

### Community 3 - "IntelliTaxi Tools"
Cohesion: 0.13
Nodes (34): clean_map_location(), _create_wp_service(), _finalizar_taxi(), get_wp_session(), _has_address_signal(), is_conversational_query(), MessageCache, process_whatsapp_message() (+26 more)

### Community 4 - "Telephony Call Handler / LLM Client"
Cohesion: 0.12
Nodes (50): RuntimeError, CallSession, Fachada de sesiones — memoria o Redis según VOICE_SESSION_STORE., Fachada de sesiones — memoria o Redis según VOICE_SESSION_STORE., Fachada de sesiones — memoria o Redis según VOICE_SESSION_STORE., Estado de una llamada activa — identificada por call_uuid (FreeSWITCH)., SessionStore, Parcial estable: apto para NLU anticipado / geocoding especulativo. (+42 more)

### Community 5 - "Streaming STT Buffer & Barge-in"
Cohesion: 0.10
Nodes (28): CallSession, ConversationMemory, AddressState, NLUResult, NLUResult, _dedup_named(), _disambiguation_question(), _join_options() (+20 more)

### Community 6 - "Browser Voice STT/TTS"
Cohesion: 0.07
Nodes (28): _build_schedule_clarification(), _extract_session_today(), _extract_session_user_id(), _extract_tastes_from_history(), _find_anchored_id_in_messages(), _inject_ids_into_titles(), date, orchestrator/tool_runner.py — Agent loop con límite estricto de herramientas. (+20 more)

### Community 7 - "STT Hint Vocabulary Builder"
Cohesion: 0.06
Nodes (35): BaseModel, TestCreateServiceRequest, Procesa turnos de conversación telefónica (control-loop V2)., Procesa turnos de conversación telefónica (control-loop V2)., Procesa turnos de conversación telefónica (control-loop V2)., TurnOrchestrator, Sintetiza oraciones de forma incremental y cachea frases repetidas., StreamingTTS (+27 more)

### Community 8 - "SchoolSena Interceptors"
Cohesion: 0.09
Nodes (41): _denied(), _entity_type(), _get_roles(), post_execution_interceptor(), pre_llm_interceptor(), Any, bool, str (+33 more)

### Community 9 - "Local Catalog Match / Streaming Pipeline"
Cohesion: 0.11
Nodes (37): _aggressive_normalize(), _alias_covers_input(), _best_catalog_snap(), bigram_similarity(), _build_phonetic_repair_index(), _collapse_adjacent_duplicate_phrases(), combined_score(), correct_stt_errors() (+29 more)

### Community 10 - "DB Connection & NexiService"
Cohesion: 0.12
Nodes (38): _clean_search_query(), confirm_appointment(), fly_to_business(), _get_active_cities_data(), get_business_availability(), get_business_mission_vision(), get_business_reviews(), get_business_services() (+30 more)

### Community 11 - "Schedule Datetime Resolution"
Cohesion: 0.11
Nodes (36): Normaliza fecha/hora preferidas desde args del LLM o el historial reciente., _resolve_schedule_datetime(), extract_session_today(), extract_session_user_id(), find_anchored_id_in_messages(), format_time_24h(), get_recent_user_messages(), is_generic_query() (+28 more)

### Community 12 - "Conversation Memory"
Cohesion: 0.11
Nodes (23): ConversationMemory, ConversationRepair, _extract_partial_location(), get_progressive_retry_message(), get_repair_message(), infer_intent(), float, int (+15 more)

### Community 13 - "Runtime Config API"
Cohesion: 0.07
Nodes (38): clear_cache(), ConfigUpdate, create_version(), health_check(), health_incidents(), list_versions(), mark_alert_read(), pending_alerts() (+30 more)

### Community 14 - "WhatsApp Service & Address Bbox"
Cohesion: 0.13
Nodes (24): _build_local_match_index(), extract_destination_address(), extract_pickup_address(), _is_correction_request(), looks_like_place(), Elimina saludos y relleno del inicio/fin del texto., Búsqueda local en el catálogo de barrios/landmarks de Popayán     (popayan_geod, Valida que `text` parezca una ubicación real en Popayán, para descartar     ext (+16 more)

### Community 15 - "FreeSWITCH Transcript Normalization"
Cohesion: 0.11
Nodes (43): _all_barrios(), _cand_name(), _comuna_of(), _landmarks(), _log(), _nearest_with_gate(), _norm(), _others() (+35 more)

### Community 16 - "WhatsApp Router"
Cohesion: 0.06
Nodes (50): _get_voice_config(), get_voice_config_public(), BaseModel, float, str, gateway/browser_voice_router.py — REST endpoints for browser-based voice (STT +, Convert text to spoken audio using OpenAI TTS.      Returns: MP3 audio binary, Stream audio directly to an <audio src="..."> tag for near-zero latency TTS. (+42 more)

### Community 17 - "Rate Limit Middleware"
Cohesion: 0.12
Nodes (21): _find_anchored_id_in_messages(), _is_generic_query(), _normalize(), _normalize_time(), bool, int, str, orchestrator/interceptors/helpers.py ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Uti (+13 more)

### Community 19 - "Audio VAD (mulaw)"
Cohesion: 0.12
Nodes (17): navigate_to_company(), str, tools/navigation.py — Herramientas para la navegación programática en la UI., Activa la navegación automática hacia el perfil de una empresa específica., _format_logo(), get_businesses_comparison(), get_general_info(), bool (+9 more)

### Community 20 - "Memory Manager (Trust/Personality)"
Cohesion: 0.10
Nodes (38): ChatService, list_projects(), gateway/router.py — FastAPI endpoints: POST /chat, GET /health, GET /projects., List all active projects in the database., ChatRequest, ChatResponse, BaseModel, ChatResponse (+30 more)

### Community 21 - "FreeSWITCH Inbound Call + TTS Store"
Cohesion: 0.08
Nodes (23): 1. Closed candidate universe, 2. Immutable normalized address, 3. Role of the user response, 4. No query contamination, 5. Fail-safe behavior, 6. Landmark-based disambiguation, 7. Selection priority, 8. Precision over recall (+15 more)

### Community 22 - "FastAPI Dependency Injection"
Cohesion: 0.06
Nodes (76): audio_stream(), audio_turn(), _echo_tokens(), _ensure_tts_prewarm(), _flush_audio_turn(), freeswitch_health(), _get_ws_buffer(), head_audio_file() (+68 more)

### Community 23 - "Main Chat Endpoint"
Cohesion: 0.17
Nodes (16): _build_business_list(), clear_session_history(), _format_distance(), _get_variations(), _load_templates(), float, int, str (+8 more)

### Community 24 - "Tool Runner History Recovery"
Cohesion: 0.06
Nodes (35): 0. Hard scope boundary (what is and is not touched), 10. Pruebas (complete battery), 11. Riesgos y mitigaciones, 12. Estrategia de migración / plan de implementación (phased, verifiable, reversible), 13.1 Second-iteration review (mandatory decisions integrated), 13. Autorrevisión (review log), 14. Definition of done (this phase), 15. Implementation reconciliations (objective contradictions found) (+27 more)

### Community 25 - "Address Normalization & WP Session"
Cohesion: 0.24
Nodes (6): bool, str, Almacenamiento de sesiones en memoria.     Reemplazar por RedisSessionStore cua, SessionStore, WhatsappService, WpSession

### Community 26 - "FreeSWITCH Test/Process Endpoints"
Cohesion: 0.09
Nodes (30): int, Request, RateLimitMiddleware, gateway/middleware.py — Rate limiting en memoria (dict + timestamp).  Simple i, Per-IP rate limiter.     max_requests: maximum requests per window.     window, get_config(), _get_config_value(), _get_current_version() (+22 more)

### Community 27 - "Interceptor Base / Generic Query"
Cohesion: 0.10
Nodes (35): normalize_colombian_address(), Forma canónica abreviada colombiana (`Cra. 52 #3C-6`).      Wrapper de compati, Normaliza al formato colombiano estándar.     'carrera cuarta a el # 17 b 28' →, Garantiza que el número de casa y el landmark sobrevivan hasta la query del, reattach_address_details(), _log(), parse_co_address(), Acceptance battery for the Colombian address parser (spec §10).  Single author (+27 more)

### Community 28 - "Audio Quality Profile"
Cohesion: 0.08
Nodes (18): AudioQualityProfile, bool, float, int, True si el usuario usa frases muy cortas., Perfil de calidad de audio de una llamada.     Se actualiza turn a turn para ad, Recomienda el speechTimeout de Twilio basado en el perfil del usuario., Timeout total de <Gather> en segundos. (+10 more)

### Community 29 - "HTTP Timeout/Payload Builder"
Cohesion: 0.13
Nodes (14): Any, AsyncClient, bool, float, str, Geocodifica origen/destino y crea el servicio en Laravel.         Usado por tes, POST al backend Laravel.          Returns: (success, user_message, response_js, get_submission_guard() (+6 more)

### Community 30 - "Navigation Tool"
Cohesion: 0.18
Nodes (9): BargeInHandler, bool, Detecta y maneja interrupciones del usuario mientras Lyra habla.          Perm, Detecta si el texto parcial indica intención de interrumpir.         Se usa con, bool, bytes, str, Acumula duración de habla sostenida sobre el residual post-AEC. (+1 more)

### Community 31 - "Response Template Engine"
Cohesion: 0.12
Nodes (29): _build_alias_index(), _ensure_index(), _estimate_coords_from_street(), _find_similar_places(), fuzzy_search(), geocode_local(), get_stats(), _normalize_address_advanced() (+21 more)

### Community 32 - "VPS Deploy (No Twilio)"
Cohesion: 0.12
Nodes (21): 1. Lyra Python (.env), 2. Redis, 3. Dependencias Lyra, 4. Apagar FreeSWITCH viejo (Twilio), 4. FreeSWITCH, 5. FreeSWITCH nuevo, Arquitectura, Arquitectura objetivo (+13 more)

### Community 33 - "README Overview"
Cohesion: 0.11
Nodes (17): Arquitectura del Sistema, Convenciones de Desarrollo, Cómo Agregar un Nuevo Proyecto, Desarrollado por, Despliegue y Configuración, Estructura del Proyecto, Motor de IA Híbrido, Panel de Administración (+9 more)

### Community 34 - "Rentus Property Search"
Cohesion: 0.12
Nodes (20): get_property_detail(), GetPropertyDetailTool, _parse_properties_from_response(), float, int, str, tools/rentus.py — Tool functions for the Rentus project., Obtiene el detalle completo de una propiedad por ID. (+12 more)

### Community 35 - "Reverse Geocoding Proxy"
Cohesion: 0.17
Nodes (18): float, Proxy for reverse geocoding to avoid CORS and handle API keys securely., reverse_geocode_api(), _extract_city_from_google(), _format_google_results(), forward_geocode(), float, str (+10 more)

### Community 36 - "WS Audio Buffer Resolution"
Cohesion: 0.29
Nodes (5): Enum, str, RepairKind, TokenKind, _OIFakeGeocoder

### Community 37 - "Structured Logger & Intent Router"
Cohesion: 0.23
Nodes (14): detect_intent(), _extract_city(), _extract_date(), _extract_service_name(), _is_spam(), _normalize(), bool, str (+6 more)

### Community 38 - "Geocoding Phase-1 Plan Doc"
Cohesion: 0.29
Nodes (8): Recupera tanto la ciudad como la categoría de la última búsqueda desde los tool, Recupera la ciudad sugerida en el último mensaje de la herramienta o del asisten, _recover_last_search_args_from_history(), _recover_suggested_city_from_history(), Recupera la ciudad sugerida en el último mensaje de la herramienta o del asisten, Recupera tanto la ciudad como la categoría de la última búsqueda., recover_last_search_args_from_history(), recover_suggested_city_from_history()

### Community 39 - "Context Builder (System Prompt)"
Cohesion: 0.29
Nodes (16): build_system_prompt(), _extract_anchored_ids(), _is_followup_reference(), _is_new_request(), _is_trivial_input(), _normalize_text(), _project_system_content(), bool (+8 more)

### Community 40 - "User Lookup/Create"
Cohesion: 0.19
Nodes (4): _MemoryBackend, int, str, _RedisBackend

### Community 41 - "Audio Preprocessing (Resample)"
Cohesion: 0.13
Nodes (29): HybridEndpointer, ends_in_continuation(), HybridEndpointer, bool, float, object, str, Endpointing híbrido acústico + semántico (spec §3.2, §3.7).  Capa acústica: el S (+21 more)

### Community 42 - "STT Prompt/Model Resolution"
Cohesion: 0.16
Nodes (47): _ambiguous_session(), FakeBackend, FakeGeocoder, _geo(), _geo_coords(), _nlu(), _orch(), Turn Orchestrator: los estados de negocio de V1 se preservan exactamente. (+39 more)

### Community 43 - "LLM Datetime Extraction"
Cohesion: 0.10
Nodes (21): _clamp(), fallback_classify(), _is_greeting_only(), _nlu_api_key(), parse_nlu_payload(), bool, float, int (+13 more)

### Community 44 - "FreeSWITCH Migration Guide Doc"
Cohesion: 0.33
Nodes (6): _match_property_id_in_reply(), _normalize(), Thin wrapper — delegates to tools.shared.utils.normalize_text., Intenta identificar qué propiedad mencionó la IA en su respuesta de texto., Thin wrapper — delegates to tools.shared.utils.normalize_text with punctuation s, Intenta identificar qué propiedad mencionó la IA en su respuesta.

### Community 46 - "Admin Session Update"
Cohesion: 0.06
Nodes (35): 1. LANDMARK vs PROXIMITY Split, 2. Candidate Name Extraction, 3. Landmark Data Coverage, Alcance, Antes (Bug), Archivos Modificados, ✅ Backward Compat, Comportamiento Antes/Después (+27 more)

### Community 47 - "API Layer Architecture Doc"
Cohesion: 0.06
Nodes (43): API Layer (api/), Core Layer (core/) — Shared Resources, Dependency Injection via FastAPI Depends, Motor Híbrido — Cerebro Rápido + Cerebro Lento, Intent Router (Regex + Keywords Classifier), Interceptor Pattern (AOP-lite Pre/Post LLM), LegacyToolAdapter — Gradual Migration Pattern, LLM as Auxiliary Fallback — ADR-002 (+35 more)

### Community 48 - "ESL Client (FreeSWITCH)"
Cohesion: 0.25
Nodes (9): FreeSwitchESLClient, bool, float, int, str, Detiene la reproducción en curso en el canal (comando core, sin         depende, Conexión corta por comando (sin suscripción de eventos)., Reproduce audio en la pata indicada (URL HTTP o ruta absoluta WAV). (+1 more)

### Community 49 - "System Design Philosophy Doc"
Cohesion: 0.08
Nodes (24): 1. Filosofía de Diseño, 2. El Motor Híbrido — Cerebro Rápido + Cerebro Lento, 3. Capas del Sistema, 4. Patrones Implementados, 5. Flujos de Datos Detallados, 6. Gestión de Configuración, 7. Observabilidad, 8. Decisiones Arquitectónicas (ADRs) (+16 more)

### Community 52 - "Tool Registry"
Cohesion: 0.12
Nodes (24): Any, str, Registra una herramienta moderna que cumple con el contrato:         - TOOL_NAM, Registra una función antigua envolviéndola en un LegacyToolAdapter., Retorna todos los esquemas registrados para el LLM., Ejecuta una herramienta por nombre con manejo de errores estandarizado., Lista nombres de herramientas registradas., Factory: Crea un registry y auto-descubre las herramientas del proyecto. (+16 more)

### Community 54 - "Legacy Tool Adapter"
Cohesion: 0.08
Nodes (22): get_esl_client(), Span de recogida preferido: directo, o la referencia indirecta.          La refe, _is_street_text(), Conduce una llamada completa sobre el WS de mod_audio_stream., Generación anticipada: NLU + geocoding especulativo sobre el parcial., Generación anticipada: NLU + geocoding especulativo sobre el parcial., Habla el resultado y ejecuta la acción (create/hangup/listen)., Cierra la escucha: descarta audio/eventos entrantes, sin STT,         endpointi (+14 more)

### Community 57 - "Admin Stats API"
Cohesion: 0.09
Nodes (32): delete_session(), export_sessions(), list_sessions(), int, str, Export sessions (basic JSON export)., Get full session with messages., Delete a session and its messages. (+24 more)

### Community 58 - "STT Service (OpenAI/Groq)"
Cohesion: 0.15
Nodes (22): _base_decision(), decide(), Decision, Enum, Decisión por tipo+confianza, SIN considerar desambiguación. Una     coincidenci, Mapea un LocationMatch a la acción del flujo. Precisión sobre recall.      La, _echo_tokens(), is_stt_hallucination() (+14 more)

### Community 62 - "Interceptor Manager"
Cohesion: 0.33
Nodes (8): Any, str, Runs logic before tool execution (e.g. arg patching, guards).     Called from t, Runs logic after tool execution (e.g. updating UI state, map center)., Runs all registered pre-LLM interceptors. Returns a response dict if intercepted, run_post_execution_interceptors(), run_pre_execution_interceptors(), run_pre_llm_interceptors()

### Community 63 - "ffmpeg Binary Resolver"
Cohesion: 0.08
Nodes (32): ffmpeg_executable(), log_ffmpeg_diagnostics(), str, Ruta absoluta de ffmpeg para telefonía (subprocess sin depender de PATH)., Binario ffmpeg configurado (absoluto por defecto)., Log de diagnóstico antes de invocar ffmpeg., _expand_address_token(), normalize_for_speech() (+24 more)

### Community 64 - "Phone Number Utils"
Cohesion: 0.12
Nodes (16): 10. References, 1. What was delivered, 2. Contract, 3. Single-authority refactor (no second normalizer), 4. Bugs fixed (the reason this work existed), 5. Phases executed, 6. Tests, 7. Objective contradictions found & resolved (spec §15) (+8 more)

### Community 65 - "Mulaw/WAV Conversion"
Cohesion: 0.08
Nodes (34): OpenAIRealtimeSTT, build_keywords(), build_prompt(), _catalog_names(), _confidence_from_logprobs(), DeepgramLiveSTT, parse_deepgram_message(), pcm16_to_ulaw() (+26 more)

### Community 66 - "Max Utterance Duration Gate"
Cohesion: 0.10
Nodes (19): 0. Hallazgo más importante (root cause transversal), 10. Lista priorizada global (top 12, impacto × esfuerzo × riesgo), 1. Arquitectura actual del pipeline de llamada, 2. Speech-to-Text (por qué falla, por qué repite al usuario), 3. Text-to-Speech, 4. LLM / orquestación / prompts, 5. Geocoding / resolución de direcciones, 6. Dependencias, código muerto, infraestructura (+11 more)

### Community 68 - "WS Buffer Mute/Flush State"
Cohesion: 0.12
Nodes (20): _compound_num_replace(), _geocode_cache_get(), normalize_address(), Match, Render en palabras completas (`Carrera 5 #12-34`) para la ruta legacy de     Wh, Match, cancelar_servicio(), CancelarServicioTool (+12 more)

### Community 70 - "Geocoding Alias Learning Rules"
Cohesion: 0.19
Nodes (15): build_stream_audio_message(), Any, bytes, int, str, Mensaje de playback de mod_audio_stream (audio crudo base64)., call_uuid desde query string, headers o metadata JSON del protocolo., Número del llamante desde query string, headers o metadata JSON. (+7 more)

### Community 71 - "Personality Resolution"
Cohesion: 0.12
Nodes (16): Objetivo 1 — Modelo STT real, demostrado, Objetivo 2 — Todas las referencias a motores STT, Objetivo 3 — No existe ningún camino donde `whisper-1` se use como fallback dinámico en tiempo de ejecución dentro de una misma llamada, Objetivo 4 — Tabla de motores, Objetivo 5 — ¿Existe camino donde la llamada termine en Whisper por timeout/excepción/config/flag/retry?, Objetivo 6 — Variables de entorno STT: cuáles se usan realmente, Objetivo 7 — Clientes OpenAI, Objetivo 8 — ¿El script de diagnóstico usa exactamente el mismo código que producción? (+8 more)

### Community 72 - "TTS Synthesis"
Cohesion: 0.11
Nodes (32): AddressAST, _classify(), _classify_word(), _components(), _fold_number_words(), _merge_compound_via(), NumeroCore, _parse() (+24 more)

### Community 73 - "ARCHITECTURE ADR List"
Cohesion: 0.08
Nodes (23): 10. Parques, 11. IPS / puestos de salud / EPS (ampliación de §4), 1. Barrios por comuna, 2. Conjuntos / urbanizaciones residenciales, 3. Universidades e instituciones de educación superior, 4. IPS / hospitales / clínicas, 5. Centros comerciales, 6. Entidades públicas (+15 more)

### Community 74 - "Lua Call Script Helpers"
Cohesion: 0.13
Nodes (14): 0. Principio rector, extraído de PersonaPlex sin copiarlo, 1. Frontera de reutilización (del Agente 7, verificada, sin cambios), 2. Arquitectura V2 — componentes, 3.1 Transporte (reemplaza `lyra_call.lua` + `audio_turn`), 3.2 STT streaming (reemplaza `stt_service.py`), 3.3 NLU / extracción de entidades — responde directamente al ejemplo del usuario, 3.4 Turn Orchestrator (reemplaza el control-loop de `voice_call_engine.py`, preserva sus estados de negocio), 3.5 TTS streaming (reemplaza `tts_service.py`) (+6 more)

### Community 75 - "FreeSWITCH Recording Upload"
Cohesion: 0.10
Nodes (19): 1.1 Address string normalizer — `_to_google_address_format` (lines 261–283), 1.2 Geocoding API call — `_google_get_candidates` (lines 286–358), 1.3 Places Autocomplete call — `_google_autocomplete` (lines 403–432), 1.4 Places Text Search call — `_google_places_search` (lines 569–662), 1.5 Nominatim fallback — `_nominatim_get_candidates` (lines 667–696), 1.6 Where the suffix / enrichment comes from (double-suffix risk), 1. Current repo behavior (exact, with file:line), 2.1 Geocoding API `address` string (+11 more)

### Community 80 - "Claude Settings Hooks"
Cohesion: 0.14
Nodes (13): 1. Forzar PCMU en el perfil SIP / gateway, 2. Desactivar VAD del perfil sofia, 3. Dialplan: codec, comfort-noise y AGC por llamada (recomendado), 4. (Si aplica) Quitar AGC/CNG residual, a) Trazar negociación SDP / codec activo, Archivos típicos a tocar en el servidor, b) Inspeccionar un canal en curso, c) Estado del perfil (+5 more)

### Community 81 - "Claude Local Permissions"
Cohesion: 0.29
Nodes (14): InterruptionClassifier, InterruptionClassifier, Decide si la voz entrante durante playback es interrupción real., _feed_speech(), _loud_frame(), bytes, int, Clasificador de interrupción: energía sostenida + contenido + contexto. (+6 more)

### Community 83 - "DB Migration Runner"
Cohesion: 0.29
Nodes (8): _classify_nonstreet(), _is_landmark(), _preprocess(), bool, Fold case/accents/whitespace and strip leading courtesy preamble.      No addres, Fold case/accents/whitespace and strip leading courtesy preamble.      No addr, Return (AddressState, canonical|None) for a non-street candidate., _strip_accents()

### Community 84 - "Low-Precision Cache Diagnostic Script"
Cohesion: 0.08
Nodes (32): get_chat_service(), get_db(), get_llm(), get_settings(), get_tool_registries(), get_twilio_service(), get_whatsapp_service(), Request (+24 more)

### Community 98 - "4. Caller inventory (contracts a wrapper must preserve)"
Cohesion: 0.11
Nodes (18): 1. Flow diagram (raw transcript → Google), 2. Transformation inventory (in call order), 3. Degradation points (each cited; the two prod cases traced), 3a. Prod Case 1 — `raw "Carrera 52, calle número 3C6"` → `query "Cra. 52 calle # 3c6"`, 3b. Prod Case 2 — `"Calle 5 carrera 17 28"`, 3c. Full enumeration of degradation points, 4. Caller inventory (contracts a wrapper must preserve), 5. Side effects & state (+10 more)

### Community 101 - "Cronología de lo que se hizo"
Cohesion: 0.22
Nodes (8): 1. Auditoría técnica completa del sistema de llamadas (18 agentes/subagentes en total a lo largo de la sesión), 2. Script de diagnóstico de audio independiente, 3. Traza forense: qué modelo STT usa realmente producción, 4. Diseño de Lyra Voice V2 (8 agentes: 7 investigación + 1 síntesis), Archivos de esta sesión, Cronología de lo que se hizo, Estado actual / qué falta, Resumen de sesión — Auditoría de voz Lyra + diseño V2 (2026-07-18)

### Community 103 - "Despliegue Lyra Voice V2 — streaming full-duplex (mod_audio_stream)"
Cohesion: 0.29
Nodes (6): Arquitectura, Despliegue Lyra Voice V2 — streaming full-duplex (mod_audio_stream), Requisitos en el contenedor FreeSWITCH, Rollback, Variables nuevas del app (.env), Verificación post-deploy

### Community 104 - "FreeSWITCH ↔ Lyra — arquitectura de integración (Voice V2)"
Cohesion: 0.33
Nodes (5): Backend Laravel — checklist (sin cambios), Flujo, FreeSWITCH ↔ Lyra — arquitectura de integración (Voice V2), Rutas del app (Python), Validación rápida

### Community 105 - "_OIFakeBackend"
Cohesion: 0.43
Nodes (7): _oi_geo(), _oi_imports(), _oi_nlu(), _OIFakeBackend, test_orch_barrio_name_flow_unchanged(), test_orch_invalid_structure_reasks_no_geocode(), test_orch_street_geocoded_with_canonical()

### Community 106 - "runtime.py"
Cohesion: 0.10
Nodes (18): core/config.py — Configuración centralizada desde .env con pydantic-settings., Cliente ESL mínimo para FreeSWITCH — uuid_broadcast / uuid_kill., GeoSessionSnapshot, Almacén de sesiones de llamada por call_uuid.  Soporta memoria (desarrollo) y, Snapshot serializable del estado geo de una sesión., AudioFileStore, get_audio_file_store(), bytes (+10 more)

### Community 108 - "test_audio_file_store.py"
Cohesion: 0.53
Nodes (5): _make_store(), Almacén de audio compartido para playback vía ESL uuid_broadcast., test_prune_removes_oldest_beyond_max(), test_save_pcm_unique_id_per_call(), test_save_pcm_writes_valid_wav()

### Community 109 - "address_utils.py"
Cohesion: 0.11
Nodes (37): AsyncOpenAI, _clean_stt_text(), _correct_speech(), extract_datetime_local(), extract_datetime_with_llm(), _geocode_cache_set(), _in_popayan_bbox(), _is_repeat_request() (+29 more)

### Community 110 - "test_runtime_hangup_race.py"
Cohesion: 0.18
Nodes (9): DummyTransport, Regresión: uuid_kill debe completarse aunque _hangup() se cancele a mitad de ca, uuid_kill lento — deja tiempo de cancelar la tarea que lo espera., asyncio.shield debe dejar correr uuid_kill aunque la tarea que lo     invoca se, Red de seguridad: _shutdown() cuelga el canal aunque _hangup() nunca     se hay, _runtime(), SlowFakeESL, test_kill_channel_survives_task_cancellation() (+1 more)

### Community 111 - "Handoff — Unificación del STT en OpenAI `gpt-4o-mini-transcribe` (2026-07-19)"
Cohesion: 0.13
Nodes (14): 1. Punto de partida y motivación, 2. Paso 1 — Canal navegador: `whisper-1` → `gpt-4o-mini-transcribe`, 3. Paso 2 — Línea telefónica: Deepgram → OpenAI Realtime transcription, 4. Paso 3 — Optimización del reconecto fantasma, 5. Verificación, 6. Qué se logró, 7. Pendiente / notas, 8. Referencias (+6 more)

### Community 113 - "Handoff — Despliegue Lyra Voice V2 a producción (2026-07-19)"
Cohesion: 0.14
Nodes (13): 10. Referencias, 1. Punto de partida, 2. Compilar `mod_audio_stream` desde cero, 3. Binario oficial v1.0.3 (gratis, <10 canales), 4. Pivote de arquitectura: playback vía ESL `uuid_broadcast`, 5. Cadena de bugs de infraestructura (cada uno bloqueaba al siguiente), 6. Bug de carrera en el colgado (post-pivote, ya con todo lo anterior funcionando), 7. Qué se logró (funcional en producción, verificado con llamadas reales) (+5 more)

### Community 114 - "orchestrator.py"
Cohesion: 0.14
Nodes (18): Cliente HTTP para crear solicitudes de taxi en el backend Laravel.  Mantiene e, Envía solicitudes telefónicas al backend IntelliTaxi., TelephonyBackendClient, Telephony services — contratos de negocio compartidos del canal de voz.  El moto, _barrio_ref_from_text(), _extract_placa_correction(), _looks_like_street(), Enum (+10 more)

### Community 115 - "D. LegacyToolAdapter"
Cohesion: 0.30
Nodes (7): D. LegacyToolAdapter, LegacyToolAdapter, Any, str, Ejecuta la función legacy inyectando el contexto si es necesario          o sim, Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute., # TODO: En el futuro, buscar clases que hereden de BaseTool.

### Community 118 - "Colombian Urban Address Nomenclature — Verified Reference"
Cohesion: 0.17
Nodes (11): Canonical grammar, Colombian Urban Address Nomenclature — Verified Reference, EBNF-style grammar (for tokenizer/parser), Google Maps preferred format, Modifiers table, Open questions / UNVERIFIED, Popayán notes, Road types table (+3 more)

### Community 119 - "Path"
Cohesion: 0.31
Nodes (5): Path, str, Grabación de llamada completa del lado servidor.  V1 dependía de `record_session, recording_path(), sanitize_recording_id()

### Community 121 - "Lyra Voice V2 — Implementación (2026-07-19)"
Cohesion: 0.25
Nodes (7): Decisiones sobre ítems no validados del spec (§5), Eliminado (V1 completo), Go-live pendiente (infra, no código), Lo que NO cambió (checklist §4 del spec — verificado con git diff vacío), Lyra Voice V2 — Implementación (2026-07-19), Mapa spec → código, Verificación

## Knowledge Gaps
- **355 isolated node(s):** `int`, `int`, `int`, `float`, `str` (+350 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_connection()` connect `Admin Stats API` to `Geo Bbox/Type Primitives`, `Orchestrator History Helpers`, `DB Connection & NexiService`, `Runtime Config API`, `Audio VAD (mulaw)`, `Memory Manager (Trust/Personality)`, `Low-Precision Cache Diagnostic Script`, `FreeSWITCH Test/Process Endpoints`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Why does `_collect()` connect `STT Hint Vocabulary Builder` to `SchoolSena Interceptors`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Why does `_run()` connect `SchoolSena Interceptors` to `STT Hint Vocabulary Builder`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Are the 37 inferred relationships involving `VoiceCallRuntime` (e.g. with `Request` and `str`) actually correct?**
  _`VoiceCallRuntime` has 37 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `CallSession` (e.g. with `CallSession` and `ConversationMemory`) actually correct?**
  _`CallSession` has 30 INFERRED edges - model-reasoned connections that need verification._
- **Are the 37 inferred relationships involving `TurnOrchestrator` (e.g. with `Request` and `str`) actually correct?**
  _`TurnOrchestrator` has 37 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `Decision` (e.g. with `InboundCallRequest` and `ProcessTextRequest`) actually correct?**
  _`Decision` has 36 INFERRED edges - model-reasoned connections that need verification._