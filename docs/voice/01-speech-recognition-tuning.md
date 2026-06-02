# Reconocimiento de voz Twilio — Tuning para usuarios reales (Colombia)

**Fecha:** 2026-06-01
**Archivos tocados:** `api/routers/twilio.py`, `core/streaming_pipeline.py`, `core/conversation_repair.py`, `.env.example`
**Objetivo:** ≥90% de reconocimiento correcto con habla natural en condiciones telefónicas reales, sin exigir que el usuario hable fuerte ni vocalice exageradamente.

---

## 1. Problema identificado

En producción el sistema era poco tolerante: los usuarios debían hablar **fuerte y extremadamente claro** o el bot no entendía. Alta tasa de error con habla natural, acento colombiano, ruido de fondo o volumen normal.

El sistema obligaba al **usuario a adaptarse al sistema**, en vez de lo contrario.

## 2. Causa raíz (diagnóstico)

Cuatro puntos, todos en el flujo de voz:

| # | Causa raíz | Ubicación | Efecto |
|---|---|---|---|
| 1 | **Modelo STT no telefónico**: `speechModel="experimental_conversations"`, sin `enhanced`. No optimizado para audio de teléfono (8 kHz μ-law, ruido, volumen bajo). | `_twiml_gather_message` | El usuario debe compensar el modelo hablando fuerte/claro. |
| 2 | **Gating por `Confidence`**: `classify_speech_quality` marcaba como "low" texto perfectamente usable cuando Twilio reportaba confianza baja. La confianza de Twilio es **poco fiable** (a menudo `0.00` en transcripciones correctas). | `classify_speech_quality` | Le pedían repetir aunque habló bien → subía la voz. |
| 3 | **Mensaje que entrena el grito**: template literal *"¿Puedes hablar un poco más fuerte?"*. | `conversation_repair.py` (`noisy_audio`) | Le enseñaba a gritar. |
| 4 | **`speechTimeout` fijo y corto** (2.0 s) para dictado de direcciones con pausas naturales. | `AdaptiveEndpointController.get_parameters` | Cortes a mitad de frase → fragmentos. |

## 3. Cambios realizados

### Quick wins (config Twilio) — `api/routers/twilio.py`

- **Modelo configurable y telefónico.** Nuevo `_build_speech_attrs()` centraliza `speechModel` / `language` / `enhanced` para todos los `<Gather>`. Default **`googlev2`** (premium, soporta `es-CO`, robusto en ruido/volumen bajo). `enhanced="true"` se emite **solo** para `phone_call`.
- Todo por **variables de entorno** → se cambia el modelo en producción sin tocar código (solo `.env` + reinicio).

### Medium (flujo) — `api/routers/twilio.py`

- **`classify_speech_quality` reescrito a "text-first".** Si Twilio devolvió texto, oyó algo procesable. La confianza pasa a ser desempate, **nunca** barrera que fuerce repetir:
  - Texto con señal de dirección/lugar (número, calle, carrera, barrio, sector, norte/sur…) → `high` sin importar la confianza.
  - Frase de ≥3 palabras → `high`. 2 palabras → `medium`. 1 palabra ≥4 letras → `medium`.
  - `low` solo para ruido real (1 token corto: "eh", "mmm").

### Medium (end-of-speech) — `core/streaming_pipeline.py`

- **`speechTimeout="auto"`** por defecto para captura de direcciones (detección adaptativa de fin-de-habla de Twilio; no corta pausas naturales). Sí/no mantiene valor numérico (`1.5 s`) para respuesta ágil. Ambos por env.
- **Hints ampliados** con nomenclatura vial: `diagonal, transversal, avenida, bis, conjunto, manzana, oriente, occidente`.

### Quick — `core/conversation_repair.py`

- Eliminado *"¿Puedes hablar un poco más fuerte?"*. Reemplazado por preguntas que piden un dato más simple (el barrio), sin culpar al usuario.

## 4. Configuración final recomendada (`.env`)

```env
TWILIO_SPEECH_MODEL=googlev2        # premium, es-CO nativo
TWILIO_SPEECH_LANGUAGE=es-CO
TWILIO_SPEECH_ENHANCED=true         # solo afecta a phone_call
TWILIO_SPEECH_TIMEOUT_LONG=auto     # direcciones: fin-de-habla adaptativo
TWILIO_SPEECH_TIMEOUT_SHORT=1.5     # sí/no: ágil
```

**Si `googlev2` no convence**, probar el modelo telefónico puro:
```env
TWILIO_SPEECH_MODEL=phone_call
TWILIO_SPEECH_LANGUAGE=es-US        # phone_call enhanced NO soporta es-CO
TWILIO_SPEECH_ENHANCED=true
```

## 5. Antes vs Después

| Aspecto | Antes | Después |
|---|---|---|
| Modelo STT | `experimental_conversations`, sin enhanced | `googlev2` (premium, es-CO) — configurable |
| Texto correcto con `Confidence` baja | Marcado "low" → pide repetir | Procesado (text-first) |
| Fin de habla (direcciones) | Fijo 2.0 s → corta pausas | `auto` (adaptativo) |
| Mensaje ante duda | "habla más fuerte" | "¿cuál es el barrio?" |
| Hints viales | calle/carrera | + diagonal/transversal/avenida/bis/manzana |

## 6. Riesgos y limitaciones

- **Costo:** los modelos premium (`googlev2`, `phone_call` enhanced, `deepgram_*`) tienen tarifa por reconocimiento mayor que los estándar. Justificado para alcanzar ≥90% de acierto.
- **`es-CO` vs `phone_call`:** el modelo `phone_call` enhanced **no** soporta `es-CO` (solo `es-US` para español). Por eso el default es `googlev2`, que sí tiene `es-CO` nativo.
- **`speechTimeout="auto"`:** Twilio espera un silencio final algo mayor → la respuesta puede sentirse ~0.5–1 s más lenta, a cambio de no cortar al usuario. Aceptable para despacho de taxi.
- **Validación de modelo:** verificar en una llamada real que el `speechModel` elegido está habilitado en la cuenta Twilio y soporta el `language` configurado; si no, Twilio puede degradar silenciosamente.

## 7. Mejora estructural futura (no implementada)

Para latencia mínima y barge-in real: **Twilio Media Streams + STT en tiempo real** (Deepgram Nova-2 / AssemblyAI) vía el WebSocket `/voice/stream` (hoy es stub). Reemplazaría el ciclo `<Gather>` turn-based por streaming bidireccional. Lift grande y dependencia externa; documentado como siguiente paso.
