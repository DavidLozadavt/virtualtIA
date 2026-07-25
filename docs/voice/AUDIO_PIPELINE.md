# Pipeline de mejora de audio de captura (`services/audio`)

Aislamiento de voz, supresión de ruido y cancelación de eco entre el audio que
llega de FreeSWITCH y el reconocedor. Sustituye al envío directo del PCM crudo al
STT. **No modifica ninguna otra capa**: STT, TTS, NLU, FSM, geocodificación,
WhatsApp y orquestación quedan exactamente igual; el único punto de contacto es
`services/voice/runtime.py`.

---

## 1. Arquitectura

```
PCM16 8 kHz (mod_audio_stream)
  │
  ├─→ grabadora de la llamada  (audio CRUDO: es la evidencia, no la señal de trabajo)
  │
  ▼
preprocess      paso-alto 90 Hz + bloqueo DC + limitador suave de picos
  ▼
echo_control    alineación GCC-PHAT + filtro adaptativo MDF + supresión residual
  ▼
dereverb        supresión estadística de cola tardía de sala (Lebart)
  ▼
denoise         DPDFNet 8 kHz nativo (ONNX/CPU) con atenuación acotada
  ▼
speaker_focus   marca voz de fondo por dominancia de nivel (TV, oficina, terceros)
  ▼
voice_gate      Silero VAD v6 + puerta retroactiva con pre-roll e histéresis
  ▼
normalize       ganancia lenta a nivel objetivo + limitador suave
  │
  ▼
PCM16 8 kHz → OpenAI Realtime STT
```

Referencia de eco: el PCM del TTS que Lyra reproduce se publica en
`FarEndReference` (`runtime._play_text`), anclado al instante del
`uuid_broadcast`. Un barge-in lo trunca en el punto real de corte.

### Propiedades de la composición

- **Etapas independientes.** Cada una implementa el mismo contrato
  (`process(bloque, ctx) → bloque`, `reset()`, `latency_ms`). El orden y la
  composición se leen de `AUDIO_PIPELINE_STAGES`; no hay orden cableado en código.
- **Zonas de tasa de muestreo automáticas.** Una etapa declara su `rate` y el
  pipeline inserta remuestreadores polifásicos **con estado** a su alrededor. Por
  eso el cancelador de eco puede trabajar a 8 kHz (donde vive la referencia del
  TTS) y un modelo de 16 kHz puede entrar sin tocar el resto (`AUDIO_DENOISE_RATE`).
- **Ninguna etapa puede tumbar una llamada.** Si una falla, se desactiva para el
  resto de la llamada, se registra con traza y el audio sigue por las demás.
  `AUDIO_PIPELINE_STRICT=true` propaga el error (solo para pruebas).
- **Degradación explícita.** Si falta un modelo, se usa el respaldo (supresor
  espectral / detector por energía) y se registra una advertencia con el comando
  exacto para arreglarlo. El sistema nunca arranca en silencio con menos calidad
  de la esperada.

---

## 2. Por qué estos componentes

### Supresión de ruido: DPDFNet 8 kHz (Apache-2.0, ONNX, CPU)

| Alternativa | Por qué no |
|---|---|
| DeepFilterNet 2/3 | Abandonado (último commit 2024-09), **solo 48 kHz**, sin API de streaming oficial en Python. Fuerza 8k→48k→8k. |
| GTCRN / FastEnhancer | Excelentes, pero **16 kHz únicamente**: mismo problema de remuestreo. |
| RNNoise | 48 kHz, calidad claramente inferior a los modelos actuales. |
| MetricGAN+, SGMSE+, Resemble Enhance, VoiceFixer | Optimizados para que el audio *suene* bien. Documentadamente **destruyen** el reconocimiento (ver §3). |
| NVIDIA Maxine | SDK C++ Windows + GPU, o NIM bajo licencia empresarial. |
| Krisp / ai-coustics | Buenos y bien medidos, pero licencia comercial con clave en el camino de la llamada. |

DPDFNet es el sucesor mantenido de DeepFilterNet2 (bloques *dual-path* en el
encoder) y el único de la familia moderna con **modelo nativo de 8 kHz**, que es
la tasa real de la telefonía. Se ejecuta directamente sobre ONNX Runtime en lugar
de instalar su librería de referencia: esa arrastra `librosa`, `numba`,
`llvmlite` y `scikit-learn` (~200 MB de dependencias compiladas) para
reimplementar treinta líneas de STFT.

### Detección de voz: Silero VAD v6 (MIT, ONNX, CPU)

Es el único VAD neuronal de uso extendido con **modelo nativo de 8 kHz** (tramas
de 256 muestras). En el benchmark público de rechazo de ruido no-voz (ESC-50:
ladridos, llanto, motores, viento, lluvia) marca 0.87 frente a 0.42 del siguiente
competidor neuronal y **0.00 del VAD de WebRTC** — que por diseño clasifica casi
cualquier ruido ambiental como voz, porque su objetivo original era no perder
audio en un códec. Coste: <1 ms por trama de 32 ms en un hilo.

Se descartó TEN VAD: solo 16 kHz, peor rechazo de ruido, y su licencia prohíbe
despliegues que compitan con la oferta de su autor.

Se ejecuta el ONNX directamente en lugar de instalar el paquete `silero-vad`,
que declara `torch` y `torchaudio` como dependencias obligatorias.

### Cancelación de eco: implementación propia

No existe en 2026 un AEC neuronal open-source apto para producción: los ganadores
del AEC Challenge (DeepVQE y siguientes) no publicaron pesos, y las plataformas
comerciales (LiveKit, Pipecat) directamente **no ofrecen AEC de servidor** para
telefonía — delegan al dispositivo o venden aislamiento de voz. Además AEC3 de
WebRTC no funciona a 8 kHz (`ValidFullBandRate` admite 16/32/48 kHz) y su binding
de Python tampoco.

Lo que sí es correcto y está implementado aquí, en el orden en que hace falta:

1. **Alineación por GCC-PHAT.** El retardo de ida y vuelta de una llamada SIP con
   altavoz va de decenas de ms a más de medio segundo y no es constante. Sin
   alinear primero, ningún filtro adaptativo converge. GCC-PHAT correla solo la
   fase, así que encuentra el retardo aunque el eco vuelva muy atenuado y
   filtrado. Se re-estima cada 500 ms y se acepta solo con confianza suficiente.
2. **Filtro MDF** (*multidelay block frequency domain adaptive filter*): un
   coeficiente complejo por banda y por partición temporal, adaptado por NLMS.
   Es la misma familia que el filtro lineal de AEC3, y cuesta unos cientos de
   multiplicaciones complejas por trama de 16 ms.
3. **Supresión de eco residual** por acoplamiento espectral aprendido por banda
   con seguimiento de mínimos. Necesaria porque el altavoz de un teléfono es no
   lineal (satura) y eso no es modelable con un filtro lineal.
4. **Detección de doble habla** por coherencia captura/eco estimado: si el usuario
   habla mientras suena el eco, el filtro **se congela pero sigue filtrando**.

### Voces de fondo: dominancia de nivel

Ni el mejor VAD resuelve la televisión encendida: **eso es voz humana** y la
acepta con razón. Lo que distingue al interlocutor es la distancia al micrófono.
`speaker_focus` mantiene una ventana deslizante de niveles **integrados por
sílaba (~200 ms)**, toma el percentil 85 como nivel del hablante principal y
marca como fondo lo que quede 18 dB por debajo. Integrar por sílaba es lo que
hace que funcione: el habla normal tiene 15-20 dB de rango dinámico *dentro* de
una misma palabra, así que comparar tramas de 20 ms marcaría como "fondo" media
conversación del propio usuario (medido: 209 tramas falsas de 383 antes de
integrar, 21 después).

Se descartó enrolar la voz del usuario (Personal VAD, extracción de hablante
objetivo): en una llamada entrante nunca se oyó antes a esa persona, y los
*embeddings* de hablante se degradan mucho en banda telefónica de 8 kHz.

---

## 3. Sobre-atenuación: el error que había que evitar

La evidencia publicada en 2025-2026 es consistente en un punto incómodo: un
supresor de ruido optimizado para que el audio suene limpio suele **empeorar** la
transcripción. Un estudio con MetricGAN+ sobre 500 grabaciones × 9 condiciones de
ruido × 4 reconocedores empeoró el resultado en **las 40 configuraciones**, hasta
46 puntos de WER; y sobre audio ya limpio aún costaba 1-3 puntos. La causa medida
es el **artefacto de supresión**, no el ruido residual: al borrar lo que considera
ruido, el modelo se lleva fricativas y consonantes sordas (la "s" de los plurales,
la diferencia entre "quince" y "trece").

Por eso, en este pipeline:

- **La supresión está acotada.** `AUDIO_DENOISE_ATTN_LIMIT_DB` (12 dB por defecto)
  mezcla la señal original con la limpia: `α = 10^(-límite/20)`, salida
  `α·original + (1-α)·limpia`. Es la misma semántica que el modelo aplica en su
  modo offline y que su API de streaming no expone. Con 12 dB el modelo puede
  atenuar ruido de fondo pero **no puede borrar un fonema completo**.
- **La mezcla está alineada.** El modelo introduce ~40 ms de retardo propio (no
  los 10 ms de su ventana: aplica un filtro profundo sobre varias tramas). Se
  **mide automáticamente** con un barrido de frecuencia al cargar el modelo
  (`AUDIO_DENOISE_DELAY_SAMPLES=-1`) y la señal original se retiene otro tanto.
  Sin esto la mezcla sumaría una copia adelantada 40 ms: un pre-eco.
- **La dereverberación es deliberadamente suave** (0.5 de intensidad) y es la
  primera etapa a desactivar si una medición de WER real no mejora.
- **La normalización es lenta y acotada** (±12 dB, adaptación solo sobre habla
  confirmada). La red del operador ya aplica su propio control de ganancia; un
  segundo control rápido encima produce bombeo, y el bombeo sí degrada el
  reconocimiento.

---

## 4. Comportamiento medido

Medido con voz real en español (TTS es-CO decodificado a 8 kHz, 12.3 s), bloques
de 20 ms, configuración por defecto, en un portátil Windows (Python 3.14,
ONNX Runtime 1.28, 1 hilo).

| Escenario | Energía que llega al STT | Tramas de voz abiertas | Notas |
|---|---|---|---|
| Voz limpia | 81 % | 91 % | fidelidad de forma de onda 0.85 |
| Voz + ruido de motor (SNR ≈ 6 dB) | 50 % | 90 % | el ruido se va, la voz se mantiene |
| **Ruido de motor solo** | **0.000 %** | **0 %** | silencio digital absoluto |
| **Ruido de cafetería / blanco solo** | prob. VAD máx. **0.00** | — | 0 de 383 tramas clasificadas como voz |
| Eco de altavoz solo (retardos 50-625 ms) | 0-33 % | 15-22 % | ERLE medio **19-27 dB** |
| Doble habla (eco + usuario) | — | 58 % | voz del usuario preservada (corr. 0.74) |

Estimación de retardo de eco: **exacta** (error 0 muestras) para retardos de 400,
1400, 2400, 3200 y 5000 muestras (50 a 625 ms).

**Latencia estructural: 168 ms** con la configuración por defecto
(`CaptureEnhancer.latency_ms` lo reporta y `AudioPipeline.stats()` lo desglosa):

| Etapa | Latencia |
|---|---|
| `echo_control` (STFT 256/128) | 16 ms |
| `dereverb` (STFT 256/128) | 16 ms |
| `denoise` (retardo del modelo, medido) | 40 ms |
| `voice_gate` (pre-roll retroactivo) | 96 ms |

El pre-roll es la única latencia deliberada y es lo que evita decapitar la
consonante inicial de cada palabra: el detector confirma la voz unas tramas
después de que empezó, así que la puerta retiene esas tramas y **abre hacia
atrás**. Se ajusta con `AUDIO_GATE_PRE_ROLL_MS`.

**Coste de CPU: RTF ≈ 0.47 por llamada** (≈ 2 llamadas concurrentes por núcleo),
de las cuales 0.39 son del supresor neuronal. Para reducirlo:
`AUDIO_DENOISE_BACKEND=spectral` (respaldo DSP, coste marginal) o `none`.

> **El no-habla se silencia, no se descarta.** El flujo conserva su línea de
> tiempo porque el detector de fin de enunciado de OpenAI mide *silencio* para
> cerrar el turno: descartar tramas haría que nunca viera silencio y el turno no
> cerraría nunca.

---

## 5. Despliegue

```bash
pip install -r requirements.txt          # añade onnxruntime
python scripts/fetch_audio_models.py     # descarga los pesos (~12.5 MB)
```

Los pesos **no están en el repositorio** (`.gitignore`: `models/*.onnx`): son
binarios de terceros con licencia y versión propias. El script es idempotente y
verifica tamaño y hash; `--force` re-descarga, `--only vad|denoise` limita el
alcance.

Sin ejecutarlo el sistema arranca igual, con el supresor espectral y el detector
por energía, y registra:

```
[audio] supresor ONNX no disponible (...); se usa el respaldo espectral...
[audio] Silero VAD no disponible (...); se usa el detector por energía, que NO
        distingue voz de ruido. Descarga el modelo con: python scripts/fetch_audio_models.py
```

Al inicio de cada llamada se registra la composición real y la latencia; al
final, las métricas por etapa (ERLE, retardo estimado, tramas de voz, tramas
penalizadas por eco, coste de CPU).

**FreeSWITCH no cambia.** La guía existente (`docs/freeswitch/freeswitch_audio_config.md`)
sigue siendo correcta y ahora más importante: PCMU sin transcodificar, sin VAD,
sin CNG y sin AGC. El pipeline necesita audio crudo; si FreeSWITCH procesa
primero, se procesa dos veces.

---

## 6. Configuración

Todo en `core/config.py`, prefijo `AUDIO_`, ajustable por `.env`. Sin valores
mágicos en el código. Los más relevantes:

| Variable | Defecto | Qué controla |
|---|---|---|
| `AUDIO_PIPELINE_ENABLED` | `True` | Apagarlo devuelve el PCM intacto (paso directo). |
| `AUDIO_PIPELINE_STAGES` | 7 etapas | Composición y **orden**. Quitar un nombre desactiva esa etapa. |
| `AUDIO_DENOISE_BACKEND` | `onnx` | `onnx` \| `spectral` \| `none`. |
| `AUDIO_DENOISE_MODEL_PATH` | `models/dpdfnet2_8khz.onnx` | Cambiar de modelo = cambiar esta ruta. |
| `AUDIO_DENOISE_ATTN_LIMIT_DB` | `12.0` | Techo de atenuación (0 = sin supresión, negativo = sin límite). |
| `AUDIO_DENOISE_RATE` | `0` | `0` = tasa del pipeline; `16000` conecta un modelo de banda ancha. |
| `AUDIO_VAD_THRESHOLD` | `0.6` | Sensibilidad del detector de voz. |
| `AUDIO_GATE_PRE_ROLL_MS` | `96.0` | Apertura retroactiva = latencia del pipeline. |
| `AUDIO_GATE_ECHO_PENALTY` | `0.3` | Evidencia extra exigida con eco (≥ 1.0 = veto absoluto). |
| `AUDIO_FOCUS_MARGIN_DB` | `18.0` | dB por debajo del hablante dominante para considerar fondo. |
| `AUDIO_ECHO_SEARCH_MS` | `700.0` | Rango de búsqueda del retardo de ida y vuelta. |

### Sustituir un modelo

- **Supresor:** cualquier ONNX de streaming causal con firma
  `(spec[1,1,bins,2], state) → (spec, state)` y metadatos de estado inicial.
  Basta apuntar `AUDIO_DENOISE_MODEL_PATH` (y `AUDIO_DENOISE_RATE` si su tasa
  nativa es otra). La ventana, el salto y el retardo se deducen del propio modelo.
- **Detector de voz:** implementar `SpeechDetector` (`frame_size`,
  `probability(trama) → [0,1]`, `reset()`) y construirlo en `_build_voice_gate`.
- **Etapa nueva:** heredar de `BaseStage`, registrarla en `STAGE_BUILDERS` y
  nombrarla en `AUDIO_PIPELINE_STAGES`. Nada más se toca.

---

## 7. Límites conocidos y qué medir

1. **El primer segundo de cada playback cancela peor.** Cualquier AEC necesita
   converger; aquí la alineación se intenta en cuanto hay ~350 ms de audio con
   energía real, pero antes de eso solo actúa la supresión espectral. Mitigado en
   la práctica porque la escucha está cerrada durante el playback.
2. **La medición que falta es la de verdad: WER sobre llamadas reales.** Todos los
   números de §4 son de laboratorio. La varianza publicada entre "mejora un 84 %"
   y "empeora 46 puntos" depende del audio, no del modelo. Antes de dar por bueno
   un ajuste agresivo: grabar 100-200 llamadas reales (la grabadora ya guarda el
   audio crudo), pasarlas por `services.audio.enhance_pcm_once` con distintas
   configuraciones, y comparar WER separando inserciones de borrados — las mejoras
   suelen venir de eliminar inserciones.
3. **RTF 0.47 limita la concurrencia** a ~2 llamadas por núcleo. Medir en el VPS
   real (AVX2 cambia el resultado) antes de dimensionar.
4. **La voz de fondo se rechaza por nivel, no por identidad.** Un tercero que
   hable *pegado* al micrófono con el mismo volumen que el usuario no se
   distingue. La solución real (aislamiento de hablante sin enrolamiento) hoy solo
   existe con licencia comercial.
