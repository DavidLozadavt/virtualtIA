# Graph Report - virtualtIA  (2026-07-25)

## Corpus Check
- 142 files · ~194,813 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2798 nodes · 6774 edges · 115 communities (110 shown, 5 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 768 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e7bf3cae`
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
- FarEndReference
- FrameContext
- Tool Registry
- audio/__init__.py
- Legacy Tool Adapter
- runtime_pool.py
- browser_voice.py
- Admin Stats API
- STT Service (OpenAI/Groq)
- audio_stream
- Pipeline de mejora de audio de captura (`services/audio`)
- str
- Interceptor Manager
- ffmpeg Binary Resolver
- Phone Number Utils
- Mulaw/WAV Conversion
- Max Utterance Duration Gate
- llm_utils.py
- WS Buffer Mute/Flush State
- llm_engine.py
- Geocoding Alias Learning Rules
- Personality Resolution
- TTS Synthesis
- ARCHITECTURE ADR List
- Lua Call Script Helpers
- FreeSWITCH Recording Upload
- VoiceFocusStage
- StreamResampler
- backend_client.py
- resolve_model_path
- Claude Settings Hooks
- Claude Local Permissions
- EchoControlStage
- DB Migration Runner
- Low-Precision Cache Diagnostic Script
- limpiar_numero
- SchoolSena YAML Config
- int
- _read_audio_upload
- _recover_last_businesses_from_history
- _extract_session_today
- 4. Caller inventory (contracts a wrapper must preserve)
- _inject_ids_into_titles
- _recover_last_appointment_entities
- Cronología de lo que se hizo
- Despliegue Lyra Voice V2 — streaming full-duplex (mod_audio_stream)
- FreeSWITCH ↔ Lyra — arquitectura de integración (Voice V2)
- runtime.py
- address_utils.py
- test_runtime_hangup_race.py
- Handoff — Unificación del STT en OpenAI `gpt-4o-mini-transcribe` (2026-07-19)
- Handoff — Despliegue Lyra Voice V2 a producción (2026-07-19)
- orchestrator.py
- Colombian Urban Address Nomenclature — Verified Reference
- Path
- Lyra Voice V2 — Implementación (2026-07-19)

## God Nodes (most connected - your core abstractions)
1. `VoiceCallRuntime` - 75 edges
2. `CallSession` - 67 edges
3. `FrameContext` - 66 edges
4. `TurnOrchestrator` - 65 edges
5. `get_connection()` - 60 edges
6. `Decision` - 46 edges
7. `FarEndReference` - 46 edges
8. `parse_co_address()` - 44 edges
9. `TranscriptEvent` - 44 edges
10. `ResolutionStatus` - 42 edges

## Surprising Connections (you probably didn't know these)
- `float` --uses--> `Decision`  [INFERRED]
  services/voice/filters.py → core/location_match.py
- `Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute.` --rationale_for--> `D. LegacyToolAdapter`  [EXTRACTED]
  orchestrator/tool_adapter.py → ARCHITECTURE.md
- `get_db()` --calls--> `get_connection()`  [EXTRACTED]
  api/dependencies.py → core/database.py
- `TestCreateServiceRequest` --uses--> `TurnNLU`  [INFERRED]
  api/routers/freeswitch.py → services/voice/nlu.py
- `TestCreateServiceRequest` --uses--> `TurnOrchestrator`  [INFERRED]
  api/routers/freeswitch.py → services/voice/orchestrator.py

## Import Cycles
- 1-file cycle: `api/dependencies.py -> api/dependencies.py`
- 1-file cycle: `orchestrator/tool_runner.py -> orchestrator/tool_runner.py`
- 2-file cycle: `api/dependencies.py -> services/chat_service.py -> api/dependencies.py`
- 2-file cycle: `api/dependencies.py -> services/whatsapp_service.py -> api/dependencies.py`
- 2-file cycle: `orchestrator/tool_registry.py -> orchestrator/tool_runner.py -> orchestrator/tool_registry.py`

## Communities (115 total, 5 thin omitted)

### Community 0 - "Geo Bbox/Type Primitives"
Cohesion: 0.06
Nodes (94): GeoCandidate, GeoResolution, GeoSessionState, in_urban_bbox(), in_wide_bbox(), LocationType, bool, Enum (+86 more)

### Community 1 - "Location Match Ranking"
Cohesion: 0.13
Nodes (34): aggressive_place_recovery(), _best_for_entity(), _build_catalog(), catalog_terms(), _content_tokens(), _Entity, _has_content(), _is_all_filler() (+26 more)

### Community 2 - "Orchestrator History Helpers"
Cohesion: 0.12
Nodes (36): _normalize(), Delegación a la utilidad global de normalización de texto., _assert_data_from_db(), BookingState, _call_confirm_appointment(), clear_booking_state(), _extract_confirmed_name_from_assistant(), _extract_name_from_messages() (+28 more)

### Community 3 - "IntelliTaxi Tools"
Cohesion: 0.14
Nodes (32): clean_map_location(), _create_wp_service(), _finalizar_taxi(), get_wp_session(), _has_address_signal(), is_conversational_query(), MessageCache, process_whatsapp_message() (+24 more)

### Community 4 - "Telephony Call Handler / LLM Client"
Cohesion: 0.17
Nodes (43): RuntimeError, Parcial estable: apto para NLU anticipado / geocoding especulativo., Fin de turno decidido: el texto completo entra al pipeline de turno., StablePartial, TurnReady, Extractor por llamada con generación anticipada sobre parciales.      `preempt, TurnNLU, str (+35 more)

### Community 5 - "Streaming STT Buffer & Barge-in"
Cohesion: 0.13
Nodes (28): CallSession, ConversationMemory, AddressState, NLUResult, CallSession, Estado de una llamada activa — identificada por call_uuid (FreeSWITCH)., NLUResult, _dedup_named() (+20 more)

### Community 6 - "Browser Voice STT/TTS"
Cohesion: 0.10
Nodes (22): _handle_conversational(), Maneja intents conversacionales (greeting, farewell, identity, capabilities)., _build_schedule_clarification(), _extract_session_user_id(), _extract_tastes_from_history(), _is_generic_query(), _match_property_id_in_reply(), _normalize() (+14 more)

### Community 7 - "STT Hint Vocabulary Builder"
Cohesion: 0.07
Nodes (33): Fachada de sesiones — memoria o Redis según VOICE_SESSION_STORE., Fachada de sesiones — memoria o Redis según VOICE_SESSION_STORE., SessionStore, TTS streaming por oración — edge-tts incremental → PCM 8 kHz mono.  edge-tts (, Sintetiza oraciones de forma incremental y cachea frases repetidas., StreamingTTS, FakeBackend, FakeESLClient (+25 more)

### Community 8 - "SchoolSena Interceptors"
Cohesion: 0.09
Nodes (41): _denied(), _entity_type(), _get_roles(), post_execution_interceptor(), pre_llm_interceptor(), Any, bool, str (+33 more)

### Community 9 - "Local Catalog Match / Streaming Pipeline"
Cohesion: 0.12
Nodes (35): _aggressive_normalize(), _alias_covers_input(), _best_catalog_snap(), bigram_similarity(), _build_phonetic_repair_index(), _collapse_adjacent_duplicate_phrases(), combined_score(), correct_stt_errors() (+27 more)

### Community 10 - "DB Connection & NexiService"
Cohesion: 0.13
Nodes (36): _clean_search_query(), confirm_appointment(), fly_to_business(), _get_active_cities_data(), get_business_availability(), get_business_mission_vision(), get_business_reviews(), get_business_services() (+28 more)

### Community 11 - "Schedule Datetime Resolution"
Cohesion: 0.10
Nodes (43): orchestrator/tool_runner.py — Agent loop con límite estricto de herramientas., Normaliza fecha/hora preferidas desde args del LLM o el historial reciente., _resolve_schedule_datetime(), build_schedule_clarification(), extract_session_today(), extract_session_user_id(), extract_tastes_from_history(), find_anchored_id_in_messages() (+35 more)

### Community 12 - "Conversation Memory"
Cohesion: 0.10
Nodes (25): ConversationMemory, ConversationRepair, _extract_partial_location(), get_progressive_retry_message(), get_repair_message(), infer_intent(), bool, float (+17 more)

### Community 13 - "Runtime Config API"
Cohesion: 0.11
Nodes (24): health_check(), Detailed health check for all Lyra services., chat(), geocode_api(), health(), list_projects(), public_status(), ChatRequest (+16 more)

### Community 14 - "WhatsApp Service & Address Bbox"
Cohesion: 0.07
Nodes (70): dbfs(), RMS lineal de una trama float32 (0.0 si está vacía)., Nivel RMS en dBFS; `floor_db` para silencio absoluto., rms(), AudioPipeline, Ejecuta una secuencia de etapas sobre un flujo PCM16 continuo.      Contrato de, Retardo estructural total del pipeline., Reinicia el estado de todas las etapas (nuevo turno de escucha). (+62 more)

### Community 15 - "FreeSWITCH Transcript Normalization"
Cohesion: 0.11
Nodes (43): _all_barrios(), _cand_name(), _comuna_of(), _landmarks(), _log(), _nearest_with_gate(), _norm(), _others() (+35 more)

### Community 16 - "WhatsApp Router"
Cohesion: 0.10
Nodes (31): Transcribe browser audio to text using Whisper.      Accepts: WebM, WAV, MP3,, Transcribe browser audio to text using gpt-4o-mini-transcribe.      Accepts: W, transcribe_audio(), _clean_for_tts(), _edge_tts_sync_bytes(), _is_gibberish(), bool, bytes (+23 more)

### Community 17 - "Rate Limit Middleware"
Cohesion: 0.12
Nodes (19): _find_anchored_id_in_messages(), _is_generic_query(), _normalize_time(), bool, int, str, orchestrator/interceptors/helpers.py ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Uti, Recupera la lista de negocios del último resultado de herramienta en el historia (+11 more)

### Community 19 - "Audio VAD (mulaw)"
Cohesion: 0.11
Nodes (19): navigate_to_company(), str, tools/navigation.py — Herramientas para la navegación programática en la UI., Activa la navegación automática hacia el perfil de una empresa específica., _format_logo(), get_businesses_comparison(), get_general_info(), open_business_web() (+11 more)

### Community 20 - "Memory Manager (Trust/Personality)"
Cohesion: 0.11
Nodes (33): ChatService, ChatRequest, ChatResponse, BaseModel, ChatResponse, get_conversation_history(), get_conversation_message_count(), get_or_create_conversation() (+25 more)

### Community 21 - "FreeSWITCH Inbound Call + TTS Store"
Cohesion: 0.08
Nodes (23): 1. Closed candidate universe, 2. Immutable normalized address, 3. Role of the user response, 4. No query contamination, 5. Fail-safe behavior, 6. Landmark-based disambiguation, 7. Selection priority, 8. Precision over recall (+15 more)

### Community 22 - "FastAPI Dependency Injection"
Cohesion: 0.14
Nodes (29): audio_turn(), freeswitch_health(), inbound_call(), _inbound_use_file_mode(), InboundCallRequest, _normalize_transcript(), _parse_inbound_body(), _pcm_peak_dbfs() (+21 more)

### Community 23 - "Main Chat Endpoint"
Cohesion: 0.16
Nodes (18): _build_business_list(), clear_session_history(), _format_distance(), generate_response(), _get_variations(), _load_templates(), float, int (+10 more)

### Community 24 - "Tool Runner History Recovery"
Cohesion: 0.06
Nodes (35): 0. Hard scope boundary (what is and is not touched), 10. Pruebas (complete battery), 11. Riesgos y mitigaciones, 12. Estrategia de migración / plan de implementación (phased, verifiable, reversible), 13.1 Second-iteration review (mandatory decisions integrated), 13. Autorrevisión (review log), 14. Definition of done (this phase), 15. Implementation reconciliations (objective contradictions found) (+27 more)

### Community 25 - "Address Normalization & WP Session"
Cohesion: 0.24
Nodes (6): bool, str, Almacenamiento de sesiones en memoria.     Reemplazar por RedisSessionStore cua, SessionStore, WhatsappService, WpSession

### Community 26 - "FreeSWITCH Test/Process Endpoints"
Cohesion: 0.06
Nodes (44): int, Request, RateLimitMiddleware, gateway/middleware.py — Rate limiting en memoria (dict + timestamp).  Simple i, Per-IP rate limiter.     max_requests: maximum requests per window.     window, clear_cache(), ConfigUpdate, create_version() (+36 more)

### Community 27 - "Interceptor Base / Generic Query"
Cohesion: 0.09
Nodes (38): parse_co_address(), ParsedAddress, _oi_geo(), _oi_imports(), _oi_nlu(), _OIFakeBackend, _OIFakeGeocoder, Acceptance battery for the Colombian address parser (spec §10).  Single author (+30 more)

### Community 28 - "Audio Quality Profile"
Cohesion: 0.11
Nodes (12): AudioQualityProfile, bool, float, int, True si el usuario usa frases muy cortas., Recomienda el speechTimeout de Twilio basado en el perfil del usuario., Timeout total de <Gather> en segundos., Repara la grafía de nombres de lugar en `text` usando el catálogo, vía     simi (+4 more)

### Community 29 - "HTTP Timeout/Payload Builder"
Cohesion: 0.27
Nodes (8): Any, AsyncClient, bool, float, str, Geocodifica origen/destino y crea el servicio en Laravel.         Usado por tes, POST al backend Laravel.          Returns: (success, user_message, response_js, Timeout

### Community 30 - "Navigation Tool"
Cohesion: 0.06
Nodes (35): gcc_phat(), hann_sqrt_window(), ndarray, Núcleo DSP compartido — STFT con estado, alineación temporal y suavizados.  Vari, Retardo de `capture` respecto a `reference` por GCC-PHAT.      GCC-PHAT (General, Suavizado exponencial entre tramas (alpha = peso del pasado)., Ventana raíz de Hann periódica: análisis y síntesis idénticas en WOLA., Ventana Vorbis (Princen-Bradley): w²[n] + w²[n+N/2] = 1.      Es la ventana con (+27 more)

### Community 31 - "Response Template Engine"
Cohesion: 0.09
Nodes (40): Elige el barrio OFICIAL geográficamente más cercano a una dirección ya     geoc, _select_barrio_by_proximity(), _build_alias_index(), _ensure_index(), _estimate_coords_from_street(), _find_similar_places(), fuzzy_search(), geocode_local() (+32 more)

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
Cohesion: 0.06
Nodes (35): float_to_pcm16(), pcm16_to_float(), PCM16 little-endian → float32 en [-1, 1)., float32 en [-1, 1] → PCM16 little-endian, con recorte duro., CaptureEnhancer, enhance_pcm_once(), Fachada por llamada: PCM sucio entra, PCM apto para el STT sale.      Además r, Devuelve (PCM procesado, contexto). Sin pipeline, el PCM va intacto. (+27 more)

### Community 37 - "Structured Logger & Intent Router"
Cohesion: 0.07
Nodes (20): DenoiseStage, _measure_output_delay(), OnnxStreamEnhancer, _probe_output_delay(), ndarray, Protocol, Supresor causal en streaming sobre un ONNX de espectro + estado recurrente., Estado inicial declarado en los metadatos del ONNX. (+12 more)

### Community 38 - "Geocoding Phase-1 Plan Doc"
Cohesion: 0.29
Nodes (8): Recupera tanto la ciudad como la categoría de la última búsqueda desde los tool, Recupera la ciudad sugerida en el último mensaje de la herramienta o del asisten, _recover_last_search_args_from_history(), _recover_suggested_city_from_history(), Recupera la ciudad sugerida en el último mensaje de la herramienta o del asisten, Recupera tanto la ciudad como la categoría de la última búsqueda., recover_last_search_args_from_history(), recover_suggested_city_from_history()

### Community 39 - "Context Builder (System Prompt)"
Cohesion: 0.11
Nodes (34): str, Configura un logger estándar con salida a consola y archivo rotativo., setup_logger(), build_system_prompt(), _extract_anchored_ids(), _is_followup_reference(), _is_new_request(), _is_trivial_input() (+26 more)

### Community 40 - "User Lookup/Create"
Cohesion: 0.19
Nodes (4): _MemoryBackend, int, str, _RedisBackend

### Community 41 - "Audio Preprocessing (Resample)"
Cohesion: 0.14
Nodes (26): HybridEndpointer, ends_in_continuation(), HybridEndpointer, bool, float, object, str, Endpointing híbrido acústico + semántico (spec §3.2, §3.7).  Capa acústica: el (+18 more)

### Community 42 - "STT Prompt/Model Resolution"
Cohesion: 0.16
Nodes (47): _ambiguous_session(), FakeBackend, FakeGeocoder, _geo(), _geo_coords(), _nlu(), _orch(), Turn Orchestrator: los estados de negocio de V1 se preservan exactamente. (+39 more)

### Community 43 - "LLM Datetime Extraction"
Cohesion: 0.10
Nodes (23): Limpia ruido conversacional al inicio de un transcript de dirección.      1. Q, strip_conversational_prefix(), _clamp(), fallback_classify(), _is_greeting_only(), _nlu_api_key(), parse_nlu_payload(), bool (+15 more)

### Community 44 - "FreeSWITCH Migration Guide Doc"
Cohesion: 0.07
Nodes (20): FrameSlicer, ndarray, Reagrupa un flujo continuo en tramas de exactamente `frame_size`.      Las muest, Acumula y emite todas las tramas completas disponibles., Devuelve el resto sin completar y vacía el buffer., Remuestrea con filtro polifásico (scipy) preservando la fase lineal.      8 kHz, resample(), _build_voice_gate() (+12 more)

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

### Community 50 - "FarEndReference"
Cohesion: 0.06
Nodes (22): build_capture_pipeline(), Construye el pipeline de captura según configuración., Pipeline de esta llamada, construido en el primer uso.          La construcció, FarEndReference, ndarray, Referencia far-end — el audio que Lyra reprodujo, alineado en el tiempo.  Para c, `length` muestras de referencia alineadas con la trama capturada.          `lag`, Muestras que la línea de tiempo lleva de adelanto sobre lo publicado.          P (+14 more)

### Community 51 - "FrameContext"
Cohesion: 0.10
Nodes (20): _build_speaker_focus(), FrameContext, ndarray, Estado compartido por bloque, escrito por el pipeline y leído por etapas.      `, Procesa un bloque float32 y devuelve el resultado (puede ser vacío)., ndarray, Marca como fondo el habla muy por debajo del nivel del hablante dominante., SpeakerFocusStage (+12 more)

### Community 52 - "Tool Registry"
Cohesion: 0.14
Nodes (20): D. LegacyToolAdapter, LegacyToolAdapter, Any, str, Ejecuta la función legacy inyectando el contexto si es necesario          o sim, Envuelve herramientas antiguas que no siguen el contrato TOOL_SCHEMA/execute., Any, str (+12 more)

### Community 53 - "audio/__init__.py"
Cohesion: 0.09
Nodes (20): _build_denoise(), _build_echo_control(), _build_normalize(), _build_preprocess(), _build_spectral_enhancer(), _build_voice_focus(), Pipeline de mejora de audio de captura — ensamblaje y fachada pública.  Arquit, AudioStage (+12 more)

### Community 54 - "Legacy Tool Adapter"
Cohesion: 0.08
Nodes (20): get_esl_client(), Cliente ESL mínimo para FreeSWITCH — uuid_broadcast / uuid_kill., Lyra Voice V2 — motor conversacional de voz streaming full-duplex.  Arquitectu, Span de recogida preferido: directo, o la referencia indirecta.          La re, _is_street_text(), Conduce una llamada completa sobre el WS de mod_audio_stream., Generación anticipada: NLU + geocoding especulativo sobre el parcial., Reproduce `text` por oraciones; retorna al terminar o al ser interrumpido. (+12 more)

### Community 55 - "runtime_pool.py"
Cohesion: 0.11
Nodes (24): Igual que `process`, pero ejecutándose fuera del bucle de eventos.          El, available_cpus(), clear_sessions(), default_worker_threads(), get_audio_executor(), get_session(), max_concurrent_calls(), prewarm() (+16 more)

### Community 56 - "browser_voice.py"
Cohesion: 0.13
Nodes (22): BaseModel, SessionUpdate, _get_voice_config(), get_voice_config_public(), BaseModel, float, str, gateway/browser_voice_router.py — REST endpoints for browser-based voice (STT + (+14 more)

### Community 57 - "Admin Stats API"
Cohesion: 0.09
Nodes (32): list_versions(), List Lyra version history., delete_session(), export_sessions(), list_sessions(), int, str, Export sessions (basic JSON export). (+24 more)

### Community 58 - "STT Service (OpenAI/Groq)"
Cohesion: 0.15
Nodes (20): _base_decision(), decide(), Decisión por tipo+confianza, SIN considerar desambiguación. Una     coincidenci, Mapea un LocationMatch a la acción del flujo. Precisión sobre recall.      La, _echo_tokens(), is_stt_hallucination(), looks_like_bot_echo(), normalize_transcript() (+12 more)

### Community 59 - "audio_stream"
Cohesion: 0.16
Nodes (22): audio_stream(), _ensure_tts_prewarm(), _flush_audio_turn(), _get_ws_buffer(), Any, bytes, WebSocket, WebSocket mod_audio_stream ↔ Lyra Voice V2 (full-duplex).      Entrada: frames (+14 more)

### Community 60 - "Pipeline de mejora de audio de captura (`services/audio`)"
Cohesion: 0.10
Nodes (19): 1. Arquitectura, 2. Por qué estos componentes, 3. Sobre-atenuación: el error que había que evitar, 4 bis. Aislamiento entre llamadas, 4. Comportamiento medido, 5. Despliegue, 6. Configuración, 7. Límites conocidos y qué medir (+11 more)

### Community 61 - "str"
Cohesion: 0.17
Nodes (18): _echo_tokens(), head_audio_file(), _is_stt_hallucination(), _looks_like_bot_echo(), _playback_tts_on_call(), bool, str, True si el transcript resuelve a una entidad real del catálogo local     (barri (+10 more)

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
Nodes (35): OpenAIRealtimeSTT, build_keywords(), build_prompt(), _catalog_names(), _confidence_from_logprobs(), OpenAIRealtimeSTT, parse_deepgram_message(), pcm16_to_ulaw() (+27 more)

### Community 66 - "Max Utterance Duration Gate"
Cohesion: 0.10
Nodes (19): 0. Hallazgo más importante (root cause transversal), 10. Lista priorizada global (top 12, impacto × esfuerzo × riesgo), 1. Arquitectura actual del pipeline de llamada, 2. Speech-to-Text (por qué falla, por qué repite al usuario), 3. Text-to-Speech, 4. LLM / orquestación / prompts, 5. Geocoding / resolución de direcciones, 6. Dependencias, código muerto, infraestructura (+11 more)

### Community 67 - "llm_utils.py"
Cohesion: 0.17
Nodes (17): AsyncOpenAI, call_llm(), call_llm_async(), extract_json_object(), get_async_openai_client(), get_model(), get_openai_client(), Any (+9 more)

### Community 68 - "WS Buffer Mute/Flush State"
Cohesion: 0.12
Nodes (21): _compound_num_replace(), _geocode_cache_get(), normalize_address(), Match, Forma canónica abreviada colombiana (`Cra. 52 #3C-6`).      Wrapper de compati, Render en palabras completas (`Carrera 5 #12-34`) para la ruta legacy de     Wh, Match, cancelar_servicio() (+13 more)

### Community 69 - "llm_engine.py"
Cohesion: 0.25
Nodes (10): LLMEngine, float, int, str, core/llm_engine.py — Tool calling para LLMs (OpenRouter/OpenAI-compatible).  N, Parse a failed_generation string like <function=name>{"k":v}</function>, Fix type mismatches: cast int→str, null→default, unwrap 'properties' wrapper., Request (+2 more)

### Community 70 - "Geocoding Alias Learning Rules"
Cohesion: 0.19
Nodes (15): build_stream_audio_message(), Any, bytes, int, str, Mensaje de playback de mod_audio_stream (audio crudo base64)., call_uuid desde query string, headers o metadata JSON del protocolo., Número del llamante desde query string, headers o metadata JSON. (+7 more)

### Community 71 - "Personality Resolution"
Cohesion: 0.12
Nodes (16): Objetivo 1 — Modelo STT real, demostrado, Objetivo 2 — Todas las referencias a motores STT, Objetivo 3 — No existe ningún camino donde `whisper-1` se use como fallback dinámico en tiempo de ejecución dentro de una misma llamada, Objetivo 4 — Tabla de motores, Objetivo 5 — ¿Existe camino donde la llamada termine en Whisper por timeout/excepción/config/flag/retry?, Objetivo 6 — Variables de entorno STT: cuáles se usan realmente, Objetivo 7 — Clientes OpenAI, Objetivo 8 — ¿El script de diagnóstico usa exactamente el mismo código que producción? (+8 more)

### Community 72 - "TTS Synthesis"
Cohesion: 0.10
Nodes (34): AddressAST, _classify(), _classify_word(), _components(), _fold_number_words(), _log(), _merge_compound_via(), NumeroCore (+26 more)

### Community 73 - "ARCHITECTURE ADR List"
Cohesion: 0.08
Nodes (23): 10. Parques, 11. IPS / puestos de salud / EPS (ampliación de §4), 1. Barrios por comuna, 2. Conjuntos / urbanizaciones residenciales, 3. Universidades e instituciones de educación superior, 4. IPS / hospitales / clínicas, 5. Centros comerciales, 6. Entidades públicas (+15 more)

### Community 74 - "Lua Call Script Helpers"
Cohesion: 0.13
Nodes (14): 0. Principio rector, extraído de PersonaPlex sin copiarlo, 1. Frontera de reutilización (del Agente 7, verificada, sin cambios), 2. Arquitectura V2 — componentes, 3.1 Transporte (reemplaza `lyra_call.lua` + `audio_turn`), 3.2 STT streaming (reemplaza `stt_service.py`), 3.3 NLU / extracción de entidades — responde directamente al ejemplo del usuario, 3.4 Turn Orchestrator (reemplaza el control-loop de `voice_call_engine.py`, preserva sus estados de negocio), 3.5 TTS streaming (reemplaza `tts_service.py`) (+6 more)

### Community 75 - "FreeSWITCH Recording Upload"
Cohesion: 0.10
Nodes (19): 1.1 Address string normalizer — `_to_google_address_format` (lines 261–283), 1.2 Geocoding API call — `_google_get_candidates` (lines 286–358), 1.3 Places Autocomplete call — `_google_autocomplete` (lines 403–432), 1.4 Places Text Search call — `_google_places_search` (lines 569–662), 1.5 Nominatim fallback — `_nominatim_get_candidates` (lines 667–696), 1.6 Where the suffix / enrichment comes from (double-suffix risk), 1. Current repo behavior (exact, with file:line), 2.1 Geocoding API `address` string (+11 more)

### Community 76 - "VoiceFocusStage"
Cohesion: 0.21
Nodes (6): ndarray, Tono dominante y confianza de sonoridad, por autocorrelación espectral., Peso por banda según su cercanía a un armónico de `f0`.          La distancia se, Peso por banda según cuánto varía su envolvente (ritmo silábico)., Atenúa lo que no encaja con una voz: acordes, tonos sostenidos, zumbidos., VoiceFocusStage

### Community 77 - "StreamResampler"
Cohesion: 0.20
Nodes (4): Remuestreador polifásico con estado — apto para flujo continuo.      `resample_p, StreamResampler, StageStats, test_stream_resampler_matches_continuous_resampling()

### Community 78 - "backend_client.py"
Cohesion: 0.20
Nodes (7): Cliente HTTP para crear solicitudes de taxi en el backend Laravel.  Mantiene e, get_submission_guard(), bool, str, Idempotencia de creación de servicio por call_uuid (evita duplicados en reintent, Marca call_uuid como ya enviado al backend., SubmissionGuard

### Community 79 - "resolve_model_path"
Cohesion: 0.29
Nodes (10): _download(), fetch_denoise(), fetch_vad(), main(), Path, Descarga los modelos del pipeline de audio (paso de despliegue, una vez).  Los p, Path, Ruta de un modelo: absoluta tal cual, relativa contra la raíz del proyecto. (+2 more)

### Community 80 - "Claude Settings Hooks"
Cohesion: 0.14
Nodes (13): 1. Forzar PCMU en el perfil SIP / gateway, 2. Desactivar VAD del perfil sofia, 3. Dialplan: codec, comfort-noise y AGC por llamada (recomendado), 4. (Si aplica) Quitar AGC/CNG residual, a) Trazar negociación SDP / codec activo, Archivos típicos a tocar en el servidor, b) Inspeccionar un canal en curso, c) Estado del perfil (+5 more)

### Community 81 - "Claude Local Permissions"
Cohesion: 0.11
Nodes (26): BargeInHandler, Detecta y maneja interrupciones del usuario mientras Lyra habla.          Perm, InterruptionClassifier, Telephony services — contratos de negocio compartidos del canal de voz.  El mo, GeoSessionSnapshot, Almacén de sesiones de llamada por call_uuid.  Soporta memoria (desarrollo) y, Snapshot serializable del estado geo de una sesión., InterruptionClassifier (+18 more)

### Community 82 - "EchoControlStage"
Cohesion: 0.27
Nodes (3): EchoControlStage, ndarray, Cancelación lineal + supresión de eco residual con referencia del TTS.

### Community 83 - "DB Migration Runner"
Cohesion: 0.12
Nodes (18): _classify_nonstreet(), _is_landmark(), _preprocess(), bool, Fold case/accents/whitespace and strip leading courtesy preamble.      No addres, Fold case/accents/whitespace and strip leading courtesy preamble.      No addr, Split preprocessed text into raw (kind-hint, value) pairs., Return (AddressState, canonical|None) for a non-street candidate. (+10 more)

### Community 84 - "Low-Precision Cache Diagnostic Script"
Cohesion: 0.14
Nodes (17): get_chat_service(), get_db(), get_llm(), get_settings(), get_tool_registries(), get_twilio_service(), get_whatsapp_service(), Request (+9 more)

### Community 85 - "limpiar_numero"
Cohesion: 0.33
Nodes (8): es_numero_troncal_o_empresa(), limpiar_numero(), Any, bool, str, Utilidades de normalización de teléfono — agnósticas al canal., Resuelve el teléfono real del cliente desde número directo o headers SIP., resolve_caller_phone()

### Community 87 - "int"
Cohesion: 0.29
Nodes (7): _find_anchored_id_in_messages(), _get_recent_user_messages(), int, Busca el ultimo ID anclado [ID: X] o [BIZ: X] en el historial visible., Recupera los detalles de una reserva pendiente de confirmación., Toma los ultimos mensajes del usuario para completar datos omitidos por el LLM., _recover_appointment_details_from_history()

### Community 90 - "_read_audio_upload"
Cohesion: 0.40
Nodes (5): Lee el WAV subido: cuerpo crudo (busybox wget --post-file) o multipart., Lee el WAV subido y devuelve bytes WAV crudos.      FreeSWITCH sube el audio e, Recibe la grabación de llamada completa (record_session) al colgar.      FreeS, _read_audio_upload(), upload_recording()

### Community 91 - "_recover_last_businesses_from_history"
Cohesion: 0.50
Nodes (4): Recupera la lista de negocios mencionados en el historial.     Busca tanto en r, _recover_last_businesses_from_history(), Recupera la lista de negocios mencionados en el historial., recover_last_businesses_from_history()

### Community 97 - "_extract_session_today"
Cohesion: 0.67
Nodes (3): _extract_session_today(), date, Extrae la fecha base de la sesión para resolver expresiones relativas.

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

### Community 106 - "runtime.py"
Cohesion: 0.16
Nodes (14): AudioFileStore, get_audio_file_store(), bytes, float, str, Almacén de audio de respuesta para reproducción vía ESL uuid_broadcast.  Pivot, Escribe WAV 8kHz mono en el directorio compartido con FreeSWITCH., Escribe `pcm` (16-bit mono 8kHz) como WAV.          Returns: (audio_id, ruta_d (+6 more)

### Community 109 - "address_utils.py"
Cohesion: 0.11
Nodes (43): _build_local_match_index(), _clean_stt_text(), _correct_speech(), extract_datetime_local(), extract_datetime_with_llm(), extract_destination_address(), extract_pickup_address(), _geocode_cache_set() (+35 more)

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
Cohesion: 0.46
Nodes (7): _client_with_stubbed_post(), _install_geocode_spy(), Backend client: un origen YA resuelto no se vuelve a geocodificar.  Cubre el b, Reemplaza core.geocoder_service.geocode (import perezoso dentro del método)., run(), test_missing_coords_falls_back_to_geocoding(), test_resolved_coords_skip_regeocoding()

### Community 118 - "Colombian Urban Address Nomenclature — Verified Reference"
Cohesion: 0.17
Nodes (11): Canonical grammar, Colombian Urban Address Nomenclature — Verified Reference, EBNF-style grammar (for tokenizer/parser), Google Maps preferred format, Modifiers table, Open questions / UNVERIFIED, Popayán notes, Road types table (+3 more)

### Community 119 - "Path"
Cohesion: 0.21
Nodes (8): Path, str, Grabación de llamada completa del lado servidor.  V1 dependía de `record_sessi, recording_path(), sanitize_recording_id(), Grabadora server-side: mezcla near/far y escritura WAV., test_empty_recorder_writes_nothing(), test_mix_and_write()

### Community 121 - "Lyra Voice V2 — Implementación (2026-07-19)"
Cohesion: 0.25
Nodes (7): Decisiones sobre ítems no validados del spec (§5), Eliminado (V1 completo), Go-live pendiente (infra, no código), Lo que NO cambió (checklist §4 del spec — verificado con git diff vacío), Lyra Voice V2 — Implementación (2026-07-19), Mapa spec → código, Verificación

## Knowledge Gaps
- **369 isolated node(s):** `Propiedades de la composición`, `Supresión de ruido: DPDFNet 8 kHz (Apache-2.0, ONNX, CPU)`, `Detección de voz: Silero VAD v6 (MIT, ONNX, CPU)`, `Cancelación de eco: implementación propia`, `Voces de fondo y música: dos mecanismos distintos` (+364 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `CaptureEnhancer` connect `WS Audio Buffer Resolution` to `Telephony Call Handler / LLM Client`, `STT Hint Vocabulary Builder`, `WhatsApp Service & Address Bbox`, `FarEndReference`, `FrameContext`, `audio/__init__.py`, `runtime_pool.py`?**
  _High betweenness centrality (0.117) - this node is a cross-community bridge._
- **Why does `FarEndReference` connect `FarEndReference` to `WS Audio Buffer Resolution`, `Structured Logger & Intent Router`, `FreeSWITCH Migration Guide Doc`, `WhatsApp Service & Address Bbox`, `EchoControlStage`, `FrameContext`, `audio/__init__.py`, `Navigation Tool`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Why does `get_connection()` connect `Admin Stats API` to `Geo Bbox/Type Primitives`, `Orchestrator History Helpers`, `DB Connection & NexiService`, `Runtime Config API`, `Audio VAD (mulaw)`, `Low-Precision Cache Diagnostic Script`, `Memory Manager (Trust/Personality)`, `FreeSWITCH Test/Process Endpoints`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Are the 37 inferred relationships involving `VoiceCallRuntime` (e.g. with `Request` and `str`) actually correct?**
  _`VoiceCallRuntime` has 37 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `CallSession` (e.g. with `CallSession` and `ConversationMemory`) actually correct?**
  _`CallSession` has 30 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `FrameContext` (e.g. with `CaptureEnhancer` and `StreamResampler`) actually correct?**
  _`FrameContext` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 37 inferred relationships involving `TurnOrchestrator` (e.g. with `Request` and `str`) actually correct?**
  _`TurnOrchestrator` has 37 INFERRED edges - model-reasoned connections that need verification._