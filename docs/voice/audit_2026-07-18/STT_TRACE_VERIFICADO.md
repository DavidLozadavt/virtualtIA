# Traza verificada: STT real en llamada telefónica de producción

Investigación desde cero. Cero conclusiones reutilizadas del informe anterior — cada afirmación tiene cita `archivo:línea` verificada en esta sesión.

---

## TRAZA COMPLETA DE EJECUCIÓN

**Salto 1 — FreeSWITCH → Lua**
- Archivo: `docs/freeswitch/lyra_call.lua`
- El dialplan (`docs/freeswitch/99_lyra_ai.xml:12`) invoca `lua lyra_call.lua`.
- `session:recordFile(utt, 15, 200, 3)` graba el turno del usuario a WAV local.
- Recibe: audio crudo del canal. Devuelve: archivo WAV en disco (`/tmp/utt_<uuid>.wav`).
- No modifica transcripción (no hay transcripción todavía en este salto).

**Salto 2 — Lua → HTTP**
- `lyra_call.lua` codifica el WAV en base64 y hace `busybox wget --post-file` a `POST /freeswitch/audio-turn?call_uuid=<uuid>`.
- Recibe: ruta del archivo WAV local. Devuelve: respuesta HTTP JSON (procesada en Salto 9).

**Salto 3 — Router HTTP**
- Archivo: `api/routers/freeswitch.py`, función `audio_turn`, línea **520-656**.
- Recibe: `Request` con body (base64 o WAV crudo, según `_read_audio_upload`, línea 484-517).
- `_wav_to_pcm16(wav_bytes)` (línea 448) extrae PCM16 mono + sample rate del WAV.
- Devuelve de este sub-paso: `(pcm_bytes, rate)`.

**Salto 4 — Gate de silencio (no es STT, pero decide si se llama o no)**
- Línea 554-572: calcula `peak_dbfs` con `_pcm_peak_dbfs` (línea 426-438, usa `audioop.max`).
- Si `peak_dbfs < -45.0` (`_SILENCE_DBFS_GATE`, línea 412): **corta el flujo aquí, nunca llama a STT.** Devuelve `{"no_speech": True}`.
- Si pasa el gate, continúa al salto 5.

**Salto 5 — Router → Service (instancia)**
- Línea 41: `_stt = TelephonySTTService()` — instancia global creada al importar el módulo `api/routers/freeswitch.py` (línea 25: `from services.telephony.stt_service import TelephonySTTService`).
- Línea 575: `stt = await _stt.transcribe_telephony_chunk(pcm, encoding="pcm16", call_uuid=call_uuid)`.
- Recibe: PCM16 + encoding. Devuelve: `dict` con `text`, `confidence`, `success`, `error`.

**Salto 6 — Service: resolución de proveedor/modelo (constructor, se ejecuta 1 vez al arrancar el proceso)**
- Archivo: `services/telephony/stt_service.py`, clase `TelephonySTTService`, `__init__` (línea 131-137) → `_init_client()` (línea 139-177).
- `self.provider = _resolve_stt_provider()` (línea 132, función en línea 77-93):
  - Lee `settings.TELEPHONY_STT_PROVIDER` o `settings.STT_PROVIDER` (línea 79).
  - Si es `"openai"`, `"groq"` o `"deepgram"` explícito → se usa ese valor (línea 81-87), salvo la excepción groq-sin-key (línea 82-86).
  - Si vacío → auto: `openai` si hay API key OpenAI (línea 89-90), si no `groq` si hay key Groq (línea 91-92), si no `openai` por defecto (línea 93).
- Si `provider == "openai"` (línea 152-161): `self.model = _resolve_openai_stt_model()` (línea 96-101):
  ```python
  return (
      (settings.OPENAI_STT_MODEL or "").strip()
      or (settings.TELEPHONY_STT_MODEL or "").strip()
      or _OPENAI_TRANSCRIBE_DEFAULT   # = "gpt-4o-mini-transcribe" (línea 19)
  )
  ```
- Cliente: `AsyncOpenAI(api_key=api_key)` — SDK oficial `openai`, **no** cliente Responses API, **no** SDK legado separado. Un solo tipo de cliente en todo este archivo (`from openai import AsyncOpenAI`, línea 141).

**Salto 7 — Service: llamada real al modelo (por turno)**
- `transcribe_telephony_chunk` (línea 183-211) → `_pcm16_to_wav` (línea 376-378, aplica `preprocess_pcm16` de `services/telephony/audio_preprocess.py`: resample a 16kHz + HPF + normalize) → `_transcribe_wav_bytes` (línea 256-340).
- Línea 297: `response = await self._client.audio.transcriptions.create(**create_kwargs)`.
- `create_kwargs` (línea 273-296):
  - `model`: `self.model` (resuelto en Salto 6).
  - `file`: WAV en memoria (`BytesIO`).
  - `prompt`: `_build_stt_prompt()` (línea 32-60) — string de vocabulario Popayán.
  - Si `_is_whisper_model(self.model)` (línea 108-109, `"whisper" in model.lower()`): `language=self.language`, `response_format="verbose_json"`, `temperature=0.0`.
  - Si NO es modelo whisper (rama `gpt-4o-*-transcribe`): `response_format="json"`, `language=self.language` si está seteado. **Sin `temperature` explícita** en esta rama (línea 289-295).
- Devuelve: objeto de respuesta OpenAI → `_extract_transcription_text()` (línea 112-125) extrae `.text`.
- `confidence = 1.0` fijo (línea 313), salvo si `verbose=True` (solo rama whisper) y hay `segments` con `no_speech_prob` (línea 314-323).

**Salto 8 — Postprocesamiento de la transcripción (SÍ modifica el texto)**
- De vuelta en `api/routers/freeswitch.py`:
  - Línea 595: `_is_stt_hallucination(transcript)` (línea 441-445) — descarta (no modifica, elimina el turno entero) si coincide con lista fija de frases de alucinación conocidas.
  - Línea 604: `transcript = _normalize_transcript(transcript, stt.get("confidence", 0.0))` — **sí transforma el texto** (normalización, definida en otro punto de `freeswitch.py`, aplica limpieza antes de pasar al FSM).
  - Línea 606: `_looks_like_bot_echo(transcript, session.last_message)` — descarta si el texto se parece al último mensaje del bot (eco).

**Salto 9 — Texto → FSM**
- Línea 623: `response = await process_text_turn(store, call_uuid, user_text=transcript, confidence=stt.get("confidence", 0.0), ...)`.
- Archivo: `services/telephony/call_handler.py`, función `process_text_turn`, línea **104-153**.
- Línea 132: `turn = await run_conversation_turn(store, session, user_text=user_text, confidence=confidence, ...)` — **aquí el texto ya transcrito y normalizado entra al motor de decisión (FSM)**, fin del alcance de esta investigación.

---

## Objetivo 1 — Modelo STT real, demostrado

**No es una sola respuesta fija — depende de qué valores de `.env` estaban activos cuando el proceso Python arrancó**, porque `_resolve_openai_stt_model()` lee `settings` (pydantic-settings, cargado una vez al iniciar el proceso — ver Objetivo 6).

Dos fuentes de evidencia, en conflicto entre sí, y la razón exacta del conflicto:

**A) Evidencia de logs reales de producción** (`logs/lyra.log`, línea del propio log en cada arranque del servicio, mensaje emitido por `stt_service.py:160`):
```
2026-06-20 14:07:55 [stt/openai] provider enabled model=gpt-4o-mini-transcribe
2026-07-14 15:00:41 [stt/openai] provider enabled model=gpt-4o-mini-transcribe
2026-07-18 00:53:19 [stt/openai] provider enabled model=gpt-4o-mini-transcribe
2026-07-18 01:05:11 [stt/openai] provider enabled model=gpt-4o-mini-transcribe   ← última entrada del log completo
```
Cada una de estas líneas es la propia aplicación confirmando en tiempo de ejecución qué modelo cargó. Esto demuestra que **todas las llamadas reales registradas hasta el 2026-07-18 01:05:11 usaron `gpt-4o-mini-transcribe`** (el default de código, línea 19 de `stt_service.py`), lo cual solo ocurre si en ese momento `OPENAI_STT_MODEL` y `TELEPHONY_STT_MODEL` estaban vacíos en el `.env` que ese proceso cargó.

**B) Evidencia del `.env` actual en disco, ahora mismo:**
```
STT_PROVIDER=openai
STT_MODEL=whisper-1
TELEPHONY_STT_PROVIDER=openai
TELEPHONY_STT_MODEL=whisper-1
OPENAI_STT_MODEL=whisper-1
```
`OPENAI_STT_MODEL=whisper-1` tiene prioridad máxima en `_resolve_openai_stt_model()` (línea 97-98) → si un proceso arrancara **ahora** con este `.env`, `self.model` sería `"whisper-1"`, no `gpt-4o-mini-transcribe`.

**Verificación de la discrepancia (timestamps, comando ejecutado en esta sesión):**
```
Última línea de logs/lyra.log: 2026-07-18 01:05:11
Fecha de última modificación de .env: 2026-07-18 21:40:38
```
El archivo `.env` fue modificado **20 horas después** de la última entrada registrada en el log. No existe ninguna entrada de log posterior a esa modificación — es decir, **no hay evidencia en el repositorio de que un proceso de producción haya arrancado ya con `OPENAI_STT_MODEL=whisper-1` activo.**

**Conclusión del Objetivo 1, con la precisión que exige la pregunta:**
- El modelo que **efectivamente atendió llamadas reales** hasta la última entrada de log disponible (2026-07-18 01:05:11) fue **`gpt-4o-mini-transcribe`** — demostrado por log de la propia aplicación, no por lectura de código.
- El `.env` en disco **ahora mismo** especifica `whisper-1` explícitamente para ambas variables que controlan el modelo. Si el proceso de producción se reinicia con este `.env` tal como está, el próximo modelo cargado será `whisper-1`, por precedencia de código demostrada en el Salto 6.
- No puedo demostrar con evidencia de este repositorio cuál de los dos está sirviendo llamadas en este instante exacto, porque eso depende de si el proceso vivo ya fue reiniciado después de las 21:40:38 de hoy — dato que no está en el código ni en los logs disponibles.

---

## Objetivo 2 — Todas las referencias a motores STT

| # | Archivo:línea | Símbolo | Ejecutable hoy desde llamada telefónica | Quién llama | Cuándo | Código muerto | Fallback | Producción | Prueba |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `services/telephony/stt_service.py:19` | `_OPENAI_TRANSCRIBE_DEFAULT = "gpt-4o-mini-transcribe"` | Sí, si `OPENAI_STT_MODEL` y `TELEPHONY_STT_MODEL` están vacíos | `_resolve_openai_stt_model()` | Al construir `TelephonySTTService()` (import de `freeswitch.py`, línea 41) | No | Es el fallback final de la cadena de resolución | **Sí, demostrado por logs hasta 2026-07-18 01:05** | No |
| 2 | `services/telephony/stt_service.py:20` | `_GROQ_WHISPER_DEFAULT = "whisper-large-v3"` | Solo si `provider="groq"` | `_resolve_groq_stt_model()` | Igual que arriba | No, pero inalcanzable hoy | Sí | No — `GROQ_API_KEY` vacío en `.env` (verificado, sin ninguna línea `GROQ_API_KEY=` en `.env`), y `_resolve_stt_provider()` nunca elige `groq` sin esa key salvo que `TELEPHONY_STT_PROVIDER=groq` explícito, que **no** es el caso (`.env` dice `openai`) | No |
| 3 | `services/telephony/stt_service.py:297` | `self._client.audio.transcriptions.create(**create_kwargs)` | Sí — es la única llamada real a un motor STT en todo el path de llamada telefónica | `_transcribe_wav_bytes` | Cada turno de conversación con audio | No | No, es la llamada primaria | Sí | No |
| 4 | `services/telephony/stt_service.py:147-150,223-229` | rama `provider == "deepgram"` | **No puede ejecutarse hoy** — `self._client = None` se asigna y nunca se reemplaza (línea 148); `transcribe_mulaw_chunk` devuelve error inmediato si `provider=="deepgram"` (línea 223-229); no hay cliente Deepgram importado en ningún punto del archivo | Nadie en producción — requeriría `TELEPHONY_STT_PROVIDER=deepgram` explícito en `.env`, que **no está seteado** (`.env` dice `openai`) | Nunca en el estado actual | **Sí, es un stub sin implementar**, no un fallback real | Nominalmente diseñado como fallback futuro ("streaming pending", comentario propio) | No | No |
| 5 | `core/voice_engine.py:4,177,182,192,240` | Comentario "STT: OpenAI Whisper API (whisper-1)" + `self.stt_model="whisper-1"` + `audio.transcriptions.create` | **No alcanzable desde una llamada telefónica** — verificado por grep: ni `api/routers/freeswitch.py`, ni `services/telephony/voice_call_engine.py`, ni `services/telephony/call_handler.py`, ni `services/telephony/stt_service.py` importan `core.voice_engine` (comando `grep -rn "voice_engine" <esos 4 archivos>` → 0 resultados) | `api/routers/browser_voice.py` únicamente (voz de navegador, no telefonía) | Solo si se usa el endpoint de voz de navegador | No es código muerto en sí — está vivo, pero **para otro canal**, no para la llamada telefónica | No aplica a este canal | No, es producción de un canal distinto | No |
| 6 | `api/routers/browser_voice.py:16,109` | `stt_model=voice_cfg.get("stt_model", "whisper-1")` | No alcanzable desde llamada telefónica (mismo motivo que #5, mismo router) | El propio router de voz de navegador | Solo en ese canal | No | No | Producción de otro canal | No |
| 7 | `core/streaming_pipeline.py` (múltiples líneas: 346-357, 424, 472) | Referencias a `Deepgram`/`deepgram_nova-2` como comentarios/lógica de construcción de hints | **No alcanzable** — verificado por grep: `streaming_pipeline` no es importado por ningún archivo del path de llamada telefónica (`api/routers/freeswitch.py`, `services/telephony/*`) | Nadie en el path de producción telefónica | Nunca en el estado actual | Sí, módulo completo sin ningún llamador externo en el path telefónico | Diseñado como si fuera parte de un pipeline Twilio, nunca migrado | No | No |
| 8 | `scratch/audio_diagnostic.py:249,253,256,271-272` | Llama explícitamente `gpt-4o-mini-transcribe`, `whisper-1` (OpenAI), `whisper-large-v3` (Groq) | Sí, pero **es un script standalone de diagnóstico que yo mismo escribí en esta sesión**, no forma parte del sistema de producción; usa su propio cliente `openai.OpenAI` (síncrono) instanciado directamente en el script, no `TelephonySTTService` | Solo se ejecuta manualmente vía CLI | Solo cuando el usuario lo invoca a mano | No aplica (herramienta de prueba, no de producción) | No | No | **Sí, es la herramienta de prueba** |

---

## Objetivo 3 — No existe ningún camino donde `whisper-1` se use como fallback dinámico en tiempo de ejecución dentro de una misma llamada

Búsqueda específica de caminos por timeout/excepción/retry/feature-flag que deriven a Whisper **dentro del mismo `TelephonySTTService`**:

- `_transcribe_wav_bytes` (`stt_service.py:256-340`): el único `try/except` (línea 269-340) captura **cualquier** excepción y devuelve `{"success": False, "error": str(e)}` (línea 338-340) — **no** reintenta con otro modelo, **no** cambia de proveedor, **no** hay lógica de fallback a Whisper tras un fallo de `gpt-4o-mini-transcribe`. Un error simplemente propaga como "no speech" al llamador (`freeswitch.py:576-582`), que a su vez responde `{"error": "stt_error"}` sin reintento automático de otro motor.
- No existe ningún `if model_failed: try whisper-1` en el archivo.
- El único mecanismo que determina si el modelo usado **es** un modelo Whisper es `_is_whisper_model()` (línea 108-109), usado exclusivamente para decidir **parámetros de la petición** (`response_format`, `temperature`) al modelo **ya elegido** — no para decidir cambio de modelo.

**El único camino real hacia Whisper es estático, no dinámico**: si `OPENAI_STT_MODEL` o `TELEPHONY_STT_MODEL` contienen la palabra `whisper` en el `.env` que el proceso cargó al arrancar (Salto 6), *todas* las llamadas de esa instancia de proceso usan Whisper — no es un fallback condicional intra-llamada, es una configuración fija para toda la vida del proceso.

---

## Objetivo 4 — Tabla de motores

| Motor | Ruta de ejecución | Quién lo llama | Condición de uso | Producción (llamada telefónica) | Fallback | Código muerto |
|---|---|---|---|---|---|---|
| `gpt-4o-mini-transcribe` (OpenAI) | `stt_service.py:297` vía `_transcribe_wav_bytes` | `TelephonySTTService._transcribe_wav_bytes`, invocado por `freeswitch.py:575` | Default de código si `OPENAI_STT_MODEL`/`TELEPHONY_STT_MODEL` vacíos al arrancar el proceso | **Sí — confirmado por logs reales hasta 2026-07-18 01:05:11** | No | No |
| `whisper-1` (OpenAI, vía mismo cliente/endpoint) | Idéntica ruta que arriba, mismo código, distinto valor de `self.model` | Igual | Si `.env` fija `OPENAI_STT_MODEL=whisper-1` o `TELEPHONY_STT_MODEL=whisper-1` al arrancar el proceso | **`.env` actual en disco lo especifica; sin log posterior que confirme que ya está sirviendo llamadas reales con este valor** | No | No |
| `whisper-large-v3` (Groq) | `stt_service.py:297`, cliente apuntando a `api.groq.com` | Igual, solo si `provider="groq"` | Requiere `GROQ_API_KEY` seteada (no está en `.env`) o `TELEPHONY_STT_PROVIDER=groq` explícito (no es el caso) | No | Diseñado como alternativa de proveedor, no como fallback automático | No, pero inalcanzable en config actual |
| Deepgram | `stt_service.py:147-150,223-229` | N/A — stub | Requeriría `TELEPHONY_STT_PROVIDER=deepgram`, y aun así solo devuelve error | No | Nominal, no funcional | **Sí**, placeholder sin implementación |
| `whisper-1` vía `core/voice_engine.py` | `voice_engine.py:240` | `api/routers/browser_voice.py` | Canal de voz de navegador, no telefonía | No (canal distinto) | No | No — vivo, pero fuera de alcance de la llamada telefónica |

---

## Objetivo 5 — ¿Existe camino donde la llamada termine en Whisper por timeout/excepción/config/flag/retry?

- Por **timeout/excepción/retry**: No. Demostrado en Objetivo 3 — el único `except` no cambia de modelo.
- Por **configuración/variable de entorno**: **Sí**, es el único camino real, y es estático por proceso, no dinámico por llamada — cualquier llamada telefónica que ocurra mientras el proceso tenga `OPENAI_STT_MODEL=whisper-1` (o `TELEPHONY_STT_MODEL=whisper-1`) cargado en memoria usará Whisper para **todas** sus transcripciones, sin excepción ni condición adicional.
- Por **feature flag** dedicado: No existe ningún flag booleano tipo `USE_WHISPER_FALLBACK`; el único control es el nombre del modelo en `OPENAI_STT_MODEL`/`TELEPHONY_STT_MODEL`.

---

## Objetivo 6 — Variables de entorno STT: cuáles se usan realmente

| Variable | ¿Se lee en el path telefónico? | Dónde | Efecto real | Valor actual en `.env` |
|---|---|---|---|---|
| `STT_PROVIDER` | Sí, como fallback si `TELEPHONY_STT_PROVIDER` vacío | `stt_service.py:79` | Determina proveedor si `TELEPHONY_STT_PROVIDER` no está seteado | `openai` (pero `TELEPHONY_STT_PROVIDER` también está seteado a `openai`, así que `STT_PROVIDER` es redundante hoy, no decisivo) |
| `TELEPHONY_STT_PROVIDER` | Sí, prioridad sobre `STT_PROVIDER` | `stt_service.py:79` | Decide proveedor: aquí `openai` | `openai` |
| `OPENAI_STT_MODEL` | Sí, **máxima prioridad** para el modelo | `stt_service.py:97-98` | Si está seteada, gana sobre todo lo demás | `whisper-1` |
| `TELEPHONY_STT_MODEL` | Sí, segunda prioridad | `stt_service.py:98-99` | Solo se usa si `OPENAI_STT_MODEL` está vacía — **hoy no decide nada porque `OPENAI_STT_MODEL` ya está seteada** | `whisper-1` (mismo valor, pero irrelevante mientras `OPENAI_STT_MODEL` exista) |
| `STT_MODEL` | **No se lee en ningún punto de `stt_service.py`** — no existe como atributo de `Settings` en `core/config.py` (verificado: solo existen `OPENAI_STT_MODEL` y `TELEPHONY_STT_MODEL` como campos) | N/A | **Sin efecto — variable obsoleta/no consumida**, pese a estar en `.env` (`STT_MODEL=whisper-1`) | Presente en `.env` pero muerta |
| `OPENAI_STT_API_KEY` | Sí | `stt_service.py:65-67`, vía `_openai_stt_api_key()` | Prioridad máxima para la key de STT | No está seteada en `.env` (no aparece en el grep de esta sesión) → cae al siguiente nivel |
| `OPENAI_API_KEY` | Sí, segundo nivel | `stt_service.py:68-70` | Se usa si no empieza con `sk-or` (OpenRouter) | Seteada, usada |
| `OPENAI_WHISPER_KEY` | Sí, tercer nivel (legacy) | `stt_service.py:71-73` | Solo si los dos anteriores están vacíos | No aparece en `.env` |
| `GROQ_API_KEY` | Sí | `stt_service.py:82,91,164` | Decide si el proveedor puede ser groq | **No está en `.env`** — confirmado, cero coincidencias en el grep de esta sesión |
| `TELEPHONY_STT_LANGUAGE` | Sí | `stt_service.py:133` → usado en línea 286,294 | Fija idioma `es` en la petición | Default `"es"` en `core/config.py:70` (no vi override explícito en `.env` en esta sesión) |
| `TELEPHONY_SAMPLE_RATE` | Sí | `stt_service.py:134` | Sample rate para reconstrucción de WAV | Default `8000` |

**Conclusión Objetivo 6:** `STT_MODEL` (sin prefijo) es la única variable de entorno relacionada con STT presente en `.env` que **no tiene ningún efecto** — no existe como campo de `Settings`. Todas las demás sí se leen. `OPENAI_STT_MODEL` es la que manda hoy sobre el nombre del modelo.

---

## Objetivo 7 — Clientes OpenAI

Búsqueda exhaustiva en el path de llamada telefónica (`api/routers/freeswitch.py`, `services/telephony/*.py`, `core/geocoder_service.py` para LLM, `core/llm_utils.py`, `core/llm_engine.py`):

- **`services/telephony/stt_service.py:141`**: `from openai import AsyncOpenAI` — **el único cliente usado para STT en el path telefónico**. Un solo tipo, SDK oficial (`openai` en `requirements.txt:10`), Audio API clásica (`.audio.transcriptions.create`), no Responses API, no SDK separado.
- **`core/llm_utils.py`**: usa `AsyncOpenAI`/`openai` para el LLM (no STT) — cliente distinto, propósito distinto, no interfiere con la resolución de modelo STT.
- **`core/voice_engine.py:240`**: también instancia `self.openai_client` y llama `.audio.transcriptions.create` — pero, como se demostró en Objetivo 2 fila 5, este archivo **no es importado por ningún componente del path de llamada telefónica**. Es un cliente real, pero de otro canal (voz de navegador).
- **Conclusión:** no hay "cliente antiguo vs nuevo" compitiendo en el path telefónico — hay un único cliente (`AsyncOpenAI`) en `stt_service.py`, y un cliente separado y no relacionado en `voice_engine.py` que sirve a un router distinto.

---

## Objetivo 8 — ¿El script de diagnóstico usa exactamente el mismo código que producción?

**No.** Diferencias verificadas línea por línea:

| Aspecto | Producción (`stt_service.py`) | Script (`scratch/audio_diagnostic.py`) |
|---|---|---|
| Cliente | `openai.AsyncOpenAI` (async, línea 141) | `openai.OpenAI` (síncrono, línea ~250) |
| Modelo | Resuelto dinámicamente vía `.env` (Salto 6) — hoy sería `whisper-1` si el proceso reinicia, `gpt-4o-mini-transcribe` en la última sesión real registrada | Hardcodea explícitamente `["gpt-4o-mini-transcribe", "whisper-1"]`, prueba ambos siempre, sin leer `TELEPHONY_STT_MODEL`/`OPENAI_STT_MODEL` |
| Endpoint | `self._client.audio.transcriptions.create(**create_kwargs)` | `client.audio.transcriptions.create(**kwargs)` — mismo método del SDK, pero kwargs distintos |
| `prompt` (sesgo de vocabulario Popayán) | Sí, siempre incluido vía `_build_stt_prompt()` (línea 281-283) | **No se envía ningún `prompt`** en el script — confirmado, no hay parámetro `prompt` en las llamadas del script |
| `temperature` | `0.0` solo si es modelo whisper (línea 288); **sin fijar** para `gpt-4o-mini-transcribe` | `0.0` solo para `whisper-1` (si `model == "whisper-1"`); igual comportamiento en ese punto específico, pero por código completamente distinto y no sincronizado |
| `language` | `"es"` siempre que `self.language` esté seteado (línea 286,294) | `language="es"` hardcodeado, sí coincide en valor |
| `response_format` | `"verbose_json"` solo si whisper y `verbose=True`; si no, `"json"` | `"verbose_json"` solo para `whisper-1`; `gpt-4o-mini-transcribe` no fija `response_format` en el script (usa default del SDK) |
| Preprocesamiento de audio previo al envío | Sí — `preprocess_pcm16()` (resample a 16kHz + HPF + normalize, `audio_preprocess.py`) se aplica siempre antes de construir el WAV (Salto 7) | **No** — el script lee el WAV del disco tal cual (`wav_path.read_bytes()`) y lo sube sin ningún preprocesamiento DSP |
| Postprocesamiento del texto | Sí — filtro de alucinación, normalización, filtro de eco (Salto 8, todo en `freeswitch.py`) | **No** — el script devuelve el `.text` crudo de la API, sin ningún filtro |
| Formato de entrada | PCM16 extraído de un WAV grabado por FreeSWITCH vía `recordFile` (8kHz telefónico, procesado) | WAV arbitrario proporcionado por el usuario, leído directo del disco |

**Conclusión Objetivo 8:** el script reproduce **el nombre de los modelos y algunos parámetros puntuales** (idioma, `temperature=0` para whisper), pero **no** es el mismo pipeline: falta el prompt de sesgo de vocabulario, falta el preprocesamiento DSP (resample/HPF/normalize), falta todo el postprocesamiento (alucinación/normalización/eco), y usa un cliente síncrono distinto. Las transcripciones comparadas en la sesión anterior son indicativas de comportamiento del modelo crudo, **no** una réplica fiel del pipeline de producción.

---

## Objetivo 9 — Transformaciones de la transcripción después del STT

Todas ocurren en `api/routers/freeswitch.py`, después de recibir `stt["text"]`, antes de llegar al FSM:

1. **Filtro de alucinación** (línea 595, función `_is_stt_hallucination`, línea 441-445): compara contra lista fija `_STT_HALLUCINATIONS` (línea 415-423) tras `strip_accents()` (importado de `core/stt_enhancer.py`). Si coincide, descarta el turno entero (no llega texto al FSM).
2. **Normalización** (línea 604, `_normalize_transcript(transcript, confidence)`): transforma el texto antes de pasarlo al FSM — función definida en el propio `freeswitch.py` (no confirmado en esta traza el detalle interno línea por línea, pero se invoca siempre entre STT y FSM).
3. **Filtro de eco del bot** (línea 606, `_looks_like_bot_echo(transcript, session.last_message)`): compara el texto normalizado contra el último mensaje que dijo el bot; si se parece, descarta el turno completo.
4. **Preprocesamiento previo al STT** (no es transcripción, es audio, Salto 7): `preprocess_pcm16` en `services/telephony/audio_preprocess.py`, aplicado **antes** de que el audio llegue al modelo, no después — no transforma texto pero sí condiciona lo que el modelo recibe.

No se detectó, dentro de este path exacto, ningún paso adicional de corrección fonética/fuzzy/alias entre el STT y el FSM — esos mecanismos (confirmados en investigación previa de esta sesión, `core/stt_enhancer.py`, `core/address_utils.py`) actúan **después**, dentro del FSM/geocoding, fuera del alcance de esta traza (que termina en `run_conversation_turn`, Salto 9).

---

## RESPUESTAS FINALES

### Pregunta 1 — ¿Qué modelo STT usa realmente una llamada telefónica en producción?

Con la evidencia disponible en este repositorio: **`gpt-4o-mini-transcribe`, confirmado por logs reales de ejecución (`logs/lyra.log`) hasta la última entrada disponible, 2026-07-18 01:05:11.** Sin embargo, el `.env` en disco fue modificado 20 horas después de esa última entrada de log y ahora especifica `whisper-1` explícitamente en `OPENAI_STT_MODEL` y `TELEPHONY_STT_MODEL` — variables que, por el código de `_resolve_openai_stt_model()` (`stt_service.py:96-101`), tienen prioridad absoluta sobre el default `gpt-4o-mini-transcribe`. No existe en el repositorio ninguna entrada de log posterior a esa modificación del `.env` que confirme si el proceso vivo ya recogió ese cambio (`TelephonySTTService` resuelve el modelo una sola vez, en `__init__`, al arrancar el proceso — ver Objetivo 6/Salto 6). **La respuesta correcta y completa es: fue `gpt-4o-mini-transcribe` hasta la última llamada registrada; será `whisper-1` en el próximo arranque del proceso si el `.env` actual no cambia antes.**

### Pregunta 2 — ¿Existe algún camino donde producción pueda usar Whisper?

**Sí.** No por timeout, excepción, retry ni feature flag (ninguno de esos existe, Objetivo 3/5) — sino por **configuración estática de proceso**: si `OPENAI_STT_MODEL` o `TELEPHONY_STT_MODEL` en el `.env` que el proceso carga al iniciar contienen un nombre de modelo Whisper (`stt_service.py:96-101`), **todas** las llamadas de ese proceso usan Whisper, sin condición adicional. Esto es exactamente lo que el `.env` actual en disco especifica ahora mismo (`OPENAI_STT_MODEL=whisper-1`, `TELEPHONY_STT_MODEL=whisper-1`).

### Pregunta 3 — ¿El informe anterior era correcto al afirmar que producción usa `gpt-4o-mini-transcribe`?

**Parcialmente correcto.** Era correcto en el sentido de que citaba con precisión el código del default (`_OPENAI_TRANSCRIBE_DEFAULT`, `stt_service.py:19`) y esa afirmación coincide exactamente con lo que muestran los logs reales hasta la última entrada disponible (2026-07-18 01:05:11) — no fue una suposición, tenía respaldo de log real, que yo mismo verifiqué de forma independiente en esta sesión. Es incompleto porque **no verificó el `.env` actual**, que hoy contradice ese resultado al fijar explícitamente `whisper-1` con prioridad de código superior al default que citó, y porque no señaló que la resolución del modelo depende del momento exacto de arranque del proceso, no es una constante del código.

### Pregunta 4 — ¿El script de diagnóstico analiza exactamente el mismo pipeline que producción?

**No.** Demostrado en Objetivo 8: cliente distinto (síncrono vs. asíncrono), sin `prompt` de sesgo de vocabulario, sin preprocesamiento DSP (resample/HPF/normalize) antes de enviar el audio, sin ningún postprocesamiento (filtro de alucinación, normalización, filtro de eco) después de recibir el texto, y sin lectura de las variables de entorno que realmente deciden el modelo en producción (usa una lista hardcodeada de dos modelos en vez de `_resolve_openai_stt_model()`). Las transcripciones que produjo son útiles para comparar el comportamiento crudo de los modelos sobre un WAV dado, pero no reproducen el pipeline real turno-a-turno de la llamada telefónica.
