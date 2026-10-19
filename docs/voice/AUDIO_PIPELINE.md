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
speaker_focus   marca voz de fondo por dominancia de nivel (TV, oficina, terceros)
  ▼
speaker_lock    identidad del hablante: aprende la voz de quien llama y atenúa las demás
  ▼
voice_gate      Silero VAD v6 + puerta retroactiva con pre-roll e histéresis
  ▼
denoise         DPDFNet 8 kHz nativo (ONNX/CPU); se salta con el canal cerrado
  ▼
voice_focus     post-filtro de voz objetivo: armónicos del tono dominante + modulación
  ▼
normalize       ganancia lenta a nivel objetivo + limitador suave
  │
  ▼
PCM16 8 kHz → OpenAI Realtime STT
```

**La puerta va antes del supresor**, y eso está medido, no supuesto: el detector
ve la señal natural (los artefactos de supresión lo hacían abrir con ruido de
fondo: fuga de −16 dB durante el silencio, frente a silencio absoluto con este
orden), y el supresor no gasta CPU mientras el canal está cerrado.

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

### Voces de fondo y música: dos mecanismos distintos

Ni el mejor VAD resuelve la televisión encendida: **eso es voz humana** y la
acepta con razón. Y un supresor de ruido no resuelve la música: la familia
DeepFilterNet —a la que pertenece DPDFNet— está entrenada con ruido como
adversario, y su propia documentación reconoce que no sirve para reducir música.
Se atacan por separado:

**1. `speaker_focus` — distancia al micrófono.** Quien habla al teléfono domina el
nivel; la televisión y los terceros llegan sistemáticamente más abajo. Se mantiene
una ventana deslizante de niveles **integrados por sílaba (~200 ms)**, se toma el
percentil 85 como nivel del hablante principal y se marca como fondo lo que quede
18 dB por debajo. Integrar por sílaba es lo que hace que funcione: el habla normal
tiene 15-20 dB de rango dinámico *dentro* de una misma palabra, así que comparar
tramas de 20 ms marcaría como "fondo" media conversación del propio usuario
(medido: 209 tramas falsas de 383 antes de integrar, 21 después).

**2. `voice_focus` — estructura de voz.** Post-filtro con dos criterios ortogonales
al supresor neuronal, ambos sin modelo y de coste marginal:

- *Armónicos del tono dominante.* Una voz tiene una fundamental con sus armónicos;
  la música tiene varias fundamentales a la vez (un acorde) y el ruido ninguna. Se
  estima el tono de la trama por autocorrelación (reutilizando la FFT ya hecha) y
  se atenúa la energía que no encaja en su rejilla de armónicos. Solo por debajo de
  2 kHz y solo en tramas sonorizadas: las consonantes sordas son inarmónicas a
  propósito y borrarlas costaría palabras.
- *Modulación silábica.* El habla varía de nivel 4-8 veces por segundo; una nota
  sostenida, un ventilador o un motor no. Por banda se compara la envolvente rápida
  con la lenta.

Medido: mejora en **los seis tipos de fondo probados**, con un coste de 0.5-0.7 dB
sobre el nivel de la voz y sin cambiar la fidelidad de forma de onda sobre voz
limpia (0.856 con y sin la etapa).

### Identidad del hablante: `speaker_lock`

Es la única etapa capaz de distinguir **una voz de otra voz**. Todas las demás
miden propiedades genéricas (¿hay energía?, ¿es voz?, ¿es armónico?, ¿está
modulado?) y un televisor encendido las satisface todas, con razón. Por eso el
rechazo de música era de 33 dB y el de voces humanas de fondo de 1.7 dB.

**Modelo.** `wespeaker-voxceleb-resnet34-LM` en ONNX (26 MB, 256 dimensiones,
Apache-2.0), con un banco de filtros mel al estilo Kaldi reimplementado en numpy
(`services/audio/embedding.py`). La elección está medida sobre las grabaciones de
referencia de tres hablantes que publica sherpa-onnx (misma persona / personas
distintas, coseno):

| modelo | misma persona | personas distintas | ¿separa? |
|---|---|---|---|
| **wespeaker ResNet34-LM** | **0.69** | **0.18** | **sí** |
| wespeaker CAM++ (sherpa) | 0.50 | 0.42 | no |
| NeMo TitaNet-small | 0.34 | 0.31 | no |

Los dos últimos son más baratos sobre el papel y aparecen recomendados en la
literatura; con las mismas características de entrada **no discriminan**, así que
se descartaron. Conviene no volver a elegirlos sin repetir esta medición.

**La banda telefónica casi no estorba.** El modelo se entrenó a 16 kHz y recibe
audio de 8 kHz remuestreado (polifásico, obligatorio: con interpolación simple la
banda alta se llena con una imagen especular de la voz, peor que dejarla vacía).
El margen entre "misma persona" y "otra persona" baja de 0.266 a 0.244: la
identidad vive por debajo de 3.4 kHz.

**Lo que decide si la etapa sirve o estorba es la geometría de las ventanas**, y
esto costó varias iteraciones medidas:

1. *Patrón largo, seguimiento corto.* Comparar dos fragmentos cortos entre sí no
   funciona (margen −0.34 con 1 s). Comparar un fragmento corto contra un patrón
   **ya estable** sí: 96.7 % de acierto con ventanas de 0.4 s, 91.7 % con 0.6 s,
   80 % con 1.5 s. De ahí la asimetría: el patrón se promedia sobre varias
   ventanas y el seguimiento usa ventanas de 0.4 s cada 0.15 s. La resolución
   corta es imprescindible: las voces ajenas se cuelan en los huecos de medio
   segundo que deja el usuario entre frases.
2. *La ventana no cruza turnos, y la frontera es **relativa**.* Si la ventana
   arrastra medio segundo de voz del usuario, su embedding se parece al usuario
   —el usuario suena más fuerte— y la etapa no atenúa nada: medido, dejaba pasar
   el 94 % de las tramas de fondo. Y la frontera **no** puede detectarse con un
   umbral absoluto de silencio, porque con fondo continuo el micrófono nunca
   queda en silencio: medido, cero fronteras detectadas en las escenas de
   televisor y de restaurante. Se detecta por caída de nivel relativa a lo que se
   venía oyendo (`AUDIO_SPEAKER_TURN_DROP_DB`).
3. *Un solo patrón, no dos.* Se probó mantener además un patrón del intruso y
   decidir por la diferencia entre las dos similitudes — la estructura de razón
   de verosimilitudes que recomienda la literatura y que sobre el papel está
   mejor calibrada. **Medido aquí, empeora**: el rechazo medio baja de 17.0 a
   15.6 dB y la conversación cercana se desploma de 13.8 a 9.5 dB, porque durante
   el habla simultánea las ventanas contienen a los dos y el patrón del intruso
   se contamina hasta parecerse al usuario.
4. *El normalizador deshacía el trabajo.* Perseguía el nivel de la voz ajena ya
   atenuada y la subía de vuelta: 17.9 → 13.6 dB. Ahora no adapta su ganancia
   sobre tramas marcadas como voz ajena, por el mismo argumento por el que nunca
   adaptaba en silencio.

**Sesgo deliberado a quedarse corto.** Los umbrales son bajísimos (aceptar 0.05,
rechazar −0.05, cuando el usuario puntúa 0.47 y las voces ajenas 0.14): se exige
evidencia muy fuerte de que la voz **no** es del usuario antes de tocar nada.
Sin patrón, con la ventana a medio llenar, con similitud intermedia o durante el
habla simultánea, el audio pasa intacto. Cortarle la frase al usuario cuesta la
llamada; dejar colar una frase ajena cuesta una corrección.

**Coste**: ~0.06 núcleos por llamada (0.196 → 0.254 medido sobre 20 s de habla
continua). Sin el modelo descargado la etapa es transparente y lo registra.

### Extracción de hablante objetivo (separar dos voces simultáneas): por qué no

`speaker_lock` decide **de quién es** un tramo de audio; no sabe separar dos
voces que suenan a la vez. Eso último exige un modelo de extracción de hablante
objetivo, y ninguno es desplegable hoy. Conviene dejar escrito por qué para no
repetir el análisis:

- **Ningún modelo abierto de TSE sirve**: los buenos (VoiceFilter, SpeakerBeam-SS,
  TEA-PSE, TargetVoice) no publicaron pesos; los que sí (USEF-TSE, SEF-PNet) no son
  causales; `AV_MossFormer2_TSE` necesita **vídeo de la cara**; el único TSE causal
  abierto del reto REAL-TSE 2026 quedó **último de 13**.
- **Todos exigen un audio de enrolamiento de 5-10 s del usuario**, que en una
  llamada entrante no existe. La literatura de 2026 que intenta obtenerlo del
  propio comienzo de la llamada concluye que **ninguno de los modelos probados
  mejora la tasa de error frente a no procesar nada**: mejoran las métricas
  perceptuales y **empeoran** el reconocimiento, porque la distorsión fonética
  que introduce la extracción cuesta más que la interferencia que quita. Es el
  motivo de fondo por el que `speaker_lock` **decide** sobre el audio en vez de
  **reconstruirlo**: una ganancia no puede inventar fonemas.
- **`weya-ai/hush`** (Apache-2.0) es el único modelo abierto de aislamiento de voz
  principal *sin* enrolamiento, y se descartó con una prueba propia: su ONNX no
  expone el estado recurrente, así que **no puede ejecutarse trama a trama**
  (procesado por tramas, el error contra el procesado por secuencia completa es del
  36 % de la escala de la señal). Solo serviría por bloques de cientos de ms, que es
  latencia inaceptable para conversación.
- Lo que la industria vende para esto (Krisp BVC, ai-coustics Voice Focus) es
  licencia comercial con clave en el camino de la llamada.

También se probó y descartó, con medición propia: **`dpdfnet8_8khz`** (el modelo
grande) da *peor* resultado sobre música que el mediano (−13.5 dB frente a
−14.8 dB) al doble de CPU; y **encadenar el supresor dos o tres veces** solo aporta
0.4 dB por cada pasada completa de coste.

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

Voz real en español (TTS es-CO decodificada a 8 kHz), bloques de 20 ms,
configuración por defecto, portátil Windows (Python 3.14, ONNX Runtime 1.28,
1 hilo por inferencia). Las cifras de fondo se obtienen descomponiendo la salida
en voz + fondo por mínimos cuadrados, con alineación por correlación.

### Supresión de fondo **mientras el usuario habla** (el fallo reportado)

| Fondo | Antes | Ahora | Voz |
|---|---|---|---|
| Música (SNR 7.8 dB) | −12.4 dB | **−20.7 dB** | −3.2 dB |
| Otra persona hablando | −4.5 dB | **−8.5 dB** | −2.7 dB |
| Cafetería / varias voces | −10.2 dB | **−19.6 dB** | −3.3 dB |
| Motor | −14.6 dB | **−25.2 dB** | −3.3 dB |

Relación voz/fondo en la salida: música 17.6 → **25.4 dB**, cafetería 9.6 →
**18.1 dB**, motor 12.7 → **22.7 dB**.

De dónde sale la mejora, por partes:

1. **Retirar el colchón global de atenuación (+5.4 dB).** Era el defecto de la
   versión anterior (`attn_limit_db=12`) y la causa directa de "la música vuelve
   cuando el usuario habla": mezclaba la señal original entera 12 dB por debajo, así
   que garantizaba un lecho musical perfectamente transcribible en cuanto la puerta
   abría. La protección de fonemas ahora es **selectiva por banda** (solo donde el
   modelo conservó señal, es decir donde hay voz), y por eso no reinyecta escena.
2. **Puerta antes del supresor.** Elimina la fuga durante el silencio que aparecía
   al quitar el colchón (los artefactos de supresión hacían abrir al detector):
   −16.3 dB → silencio absoluto.
3. **`voice_focus` (+2.0 a +10.3 dB según el fondo).** Mejora en los seis fondos
   probados: música −3.5, otra persona −2.2, cafetería −2.0, motor −2.0, ruido
   blanco −7.7, impulsos −10.3 dB.

### Voces humanas de fondo: efecto de `speaker_lock`

Banco reproducible: `python scripts/benchmark_speaker_isolation.py`. Genera con
`edge-tts` un corpus de seis hablantes en español, sitúa al usuario en campo
cercano y a los interferentes en campo lejano (reverberación de sala, absorción
del aire, nivel), y mide por separado **cuánta voz ajena sobrevive** y **cuánta
voz propia se pierde** — ninguno de los dos números significa nada solo, porque
silenciarlo todo da un rechazo perfecto y una transcripción vacía.

| Escena | Rechazo de fondo antes | Ahora | Voz del usuario |
|---|---|---|---|
| **Televisión encendida** | 10.2 dB | **21.5 dB** | −6.1 dB (sin cambio) |
| Restaurante / varias voces | 26.3 dB | 25.9 dB | −6.6 dB |
| Conversación cercana | 16.2 dB | 15.6 dB | −6.1 dB |
| **Habla simultánea cercana** | 3.1 dB | **3.0 dB** | −6.7 dB |
| Música | 38.0 dB | 38.0 dB | −6.0 dB |
| Ruido continuo + impulsivo | 31.0 dB | 31.0 dB | −6.4 dB |

Lectura honesta de esta tabla:

- **Resuelve el caso de la fuente sonora ajena y continua** (televisión, radio,
  altavoz, locutor): +11 dB, que es la diferencia entre un fondo transcribible y
  uno que no lo es. Era el peor de todos y pasa a estar en línea con los demás.
- **No cambia lo que ya funcionaba** (música, ruido, restaurante): esas las
  resolvían el supresor neuronal y `voice_focus`, y la identidad no tenía nada
  que añadir. Tampoco las estropea.
- **No resuelve el habla simultánea cercana**, y no va a resolverla: cuando dos
  personas hablan a la vez a distancia parecida, decidir "de quién es este tramo"
  no basta, hay que **separar** las dos señales, y eso es la extracción de
  hablante objetivo que la sección anterior documenta como no desplegable. Con
  las dos voces mezcladas en la misma ventana ninguna hipótesis gana, y el diseño
  deja pasar el audio a propósito.
- **El coste para el usuario es cero medible** (−6.0/−6.7 dB antes y después, la
  misma cifra), que era el requisito no negociable.

### Otros comportamientos

| Escenario | Resultado |
|---|---|
| Ruido solo (motor, cafetería, blanco) | **silencio digital**, 0 de 383 tramas clasificadas como voz |
| Voz limpia | 91 % de tramas abiertas, fidelidad de onda 0.85 |
| Eco de altavoz (retardos 50-625 ms) | ERLE medio **19-27 dB**, retardo estimado con **error 0 muestras** |
| Doble habla (eco + usuario) | voz del usuario preservada (correlación 0.74) |

**Latencia estructural: 184 ms** (`CaptureEnhancer.latency_ms` lo reporta):
echo_control 16 ms + dereverb 16 ms + voice_gate 96 ms (pre-roll retroactivo) +
denoise 40 ms (retardo propio del modelo, medido) + voice_focus 16 ms.

### Coste y concurrencia

Llamada realista (40 % habla, 60 % silencio), 8 núcleos:

| Configuración | CPU por bloque de 20 ms | RTF |
|---|---|---|
| Completo, con bypass en silencio | **5.0 ms** | 0.25 |
| Completo, sin bypass | 9.0 ms | 0.45 |
| `AUDIO_DENOISE_BACKEND=spectral` | 1.25 ms | 0.06 |

Escalado real por el camino asíncrono, a ritmo de 20 ms por bloque, sin ningún
bloque degradado hasta 24 llamadas:

| Llamadas simultáneas | p50 por bloque | p95 | Bloques degradados |
|---|---|---|---|
| 4 | 7.8 ms | 26.8 ms | 0 |
| 8 | 16.6 ms | 47.8 ms | 0 |
| 12 | 27.6 ms | 66.3 ms | 0 |
| 20 | 51.7 ms | 93.6 ms | 0 |
| 24 | 64.2 ms | 107.8 ms | 0 |

**Memoria: ~17 MB de modelos por proceso** (compartidos, no por llamada) y
**~2.5 MB por llamada**. Las sesiones ONNX son 2 con una llamada y 2 con cuarenta —
`stats()["onnx_sessions"]` lo expone precisamente para detectar una fuga.

Recomendación de dimensionado: **~1.5 llamadas por núcleo** para mantener p50 por
debajo de 30 ms. `AUDIO_MAX_CONCURRENT_CALLS` (o `AUDIO_CORES_PER_CALL`) fija el
tope que `max_concurrent_calls()` devuelve.

> **El no-habla se silencia, no se descarta.** El flujo conserva su línea de
> tiempo porque el detector de fin de enunciado de OpenAI mide *silencio* para
> cerrar el turno: descartar tramas haría que nunca viera silencio y el turno no
> cerraría nunca. Por la misma razón el ahorro de CPU en silencio emite exactamente
> tantas muestras como habría producido la inferencia.

---

## 4 bis. Aislamiento entre llamadas

Requisito: ninguna llamada puede contaminar a otra. Cómo se cumple, elemento por
elemento:

| Recurso | Alcance |
|---|---|
| Buffers, colas, solapes, líneas de retardo | **por llamada** (instancia de etapa) |
| Estado recurrente de los modelos (ONNX) | **por llamada** — viaja como tensor de entrada/salida, no vive en la sesión |
| Referencia de eco (`FarEndReference`) | **por llamada**, con `threading.Lock` (el playback publica desde el bucle, la captura consume desde un hilo) |
| Pesos de los modelos (`InferenceSession`) | **compartidos por proceso**, inmutables y seguros para `run()` concurrente |
| Ejecutor de hilos | **compartido por proceso**, acotado a los núcleos disponibles |
| Métricas | por llamada |

Las sesiones se comparten a propósito: una por llamada cargaría una copia completa
de los pesos (~14 MB) y tardaría ~175 ms en construirse, cada vez. Como el estado
recurrente es explícito, compartir la sesión no comparte **nada mutable**, que es
la única lectura de "modelos aislados" que además escala.

Tres garantías que el código hace explícitas porque son fáciles de romper:

1. **Un solo bloque en vuelo por llamada.** Si un bloque agota su presupuesto, el
   siguiente **no** se procesa en paralelo: se entrega sin procesar. Sin esto, dos
   hilos mutarían los mismos buffers y el resultado sería basura silenciosa.
2. **El orden lo garantiza el `await` de `runtime._on_audio`.** El transporte no lee
   el evento siguiente hasta que el actual termina. Convertirlo en `create_task()`
   reordenaría los bloques entre hilos y corrompería el estado recurrente.
3. **Nada de `reset()` desde el bucle mientras hay un bloque en vuelo.** El runtime
   no lo hace; cualquier código nuevo que lo hiciera introduciría una carrera.

Escalado horizontal: cada llamada es un WebSocket independiente y no comparte nada
con las demás, así que varios procesos (workers de uvicorn) o varias instancias
detrás de un balanceador funcionan sin estado compartido ni enrutado pegajoso. Al
repartir en N workers, fijar `AUDIO_WORKER_THREADS = núcleos / N` para no
sobre-suscribir.

---

## 5. Despliegue

```bash
pip install -r requirements.txt          # añade onnxruntime
python scripts/fetch_audio_models.py     # descarga los pesos (~39 MB)
```

Tres modelos: Silero VAD (2.3 MB, MIT), DPDFNet 8 kHz (10 MB, Apache-2.0) y
WeSpeaker ResNet34-LM (26.5 MB, Apache-2.0, identidad de hablante).

Los pesos **no están en el repositorio** (`.gitignore`: `models/*.onnx`): son
binarios de terceros con licencia y versión propias. El script es idempotente y
verifica tamaño y hash; `--force` re-descarga, `--only vad|denoise|speaker`
limita el alcance.

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
| `AUDIO_PIPELINE_STAGES` | 9 etapas | Composición y **orden**. Quitar un nombre desactiva esa etapa. |
| `AUDIO_DENOISE_BACKEND` | `onnx` | `onnx` \| `spectral` \| `none`. |
| `AUDIO_DENOISE_MODEL_PATH` | `models/dpdfnet2_8khz.onnx` | Cambiar de modelo = cambiar esta ruta. |
| `AUDIO_DENOISE_ATTN_LIMIT_DB` | `12.0` | Techo de atenuación (0 = sin supresión, negativo = sin límite). |
| `AUDIO_DENOISE_RATE` | `0` | `0` = tasa del pipeline; `16000` conecta un modelo de banda ancha. |
| `AUDIO_VAD_THRESHOLD` | `0.6` | Sensibilidad del detector de voz. |
| `AUDIO_GATE_PRE_ROLL_MS` | `96.0` | Apertura retroactiva = latencia del pipeline. |
| `AUDIO_GATE_ECHO_PENALTY` | `0.3` | Evidencia extra exigida con eco (≥ 1.0 = veto absoluto). |
| `AUDIO_FOCUS_MARGIN_DB` | `18.0` | dB por debajo del hablante dominante para considerar fondo. |
| `AUDIO_SPEAKER_MODEL_PATH` | `models/wespeaker_resnet34_lm.onnx` | Identidad de hablante. Vacío o ausente = etapa transparente. |
| `AUDIO_SPEAKER_REJECT` | `-0.05` | Similitud por debajo de la cual la voz se considera ajena. Subirlo rechaza más y **arriesga cortar al usuario**. |
| `AUDIO_SPEAKER_ACCEPT` | `0.05` | Similitud que confirma al usuario (histéresis con la anterior). |
| `AUDIO_SPEAKER_TRACK_WINDOW_SEC` | `0.4` | Ventana de seguimiento. Más corta = más resolución, menos fiable. |
| `AUDIO_SPEAKER_TURN_DROP_DB` | `10.0` | Caída de nivel que marca frontera de turno (**relativa**, no absoluta). |
| `AUDIO_SPEAKER_FLOOR_DB` | `-18.0` | Suelo de atenuación de voz ajena. **Nunca 0**: el silencio digital dispara alucinaciones del reconocedor. |
| `AUDIO_SPEAKER_MARK_BACKGROUND` | `True` | Deja que la identidad mande sobre el criterio de nivel de `speaker_focus`. |
| `AUDIO_ECHO_SEARCH_MS` | `700.0` | Rango de búsqueda del retardo de ida y vuelta. |
| `AUDIO_FOCUS_HARMONIC_STRENGTH` | `3.0` | Fuerza del criterio armónico de `voice_focus` (0 = desactivado). |
| `AUDIO_FOCUS_MODULATION_STRENGTH` | `1.0` | Fuerza del criterio de modulación silábica. |
| `AUDIO_DENOISE_PROTECT_FLOOR_DB` | `12.0` | Protección de fonemas selectiva por banda (sustituye al colchón global). |
| `AUDIO_DENOISE_BYPASS_ON_SILENCE` | `True` | Salta la inferencia con el canal cerrado (mitad de CPU). |
| `AUDIO_WORKER_THREADS` | `0` | Hilos del ejecutor de audio (0 = núcleos disponibles, con cuota de cgroup). |
| `AUDIO_MAX_CONCURRENT_CALLS` | `0` | Tope de llamadas simultáneas (0 = derivarlo de `AUDIO_CORES_PER_CALL`). |
| `AUDIO_BLOCK_TIMEOUT_MS` | `150.0` | Válvula contra la deriva: por encima, el bloque pasa sin procesar. |
| `AUDIO_DENOISE_THREADS` | `1` | **No cambiar**: con 4 hilos el modelo consume 3.4 núcleos para 0.5 de trabajo. |

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

1. **La música se atenúa 20 dB, no se elimina.** Es el techo de lo desplegable hoy
   sin licencia comercial ni entrenar un modelo propio: no existe extracción de
   hablante objetivo abierta y utilizable (§2). Si la música resulta crítica para el
   negocio, las dos únicas vías reales son licenciar ai-coustics Quail Voice Focus /
   Krisp VIVA-tel, o entrenar un modelo causal a 8 kHz con música y voces
   competidoras como interferencia a 0 dB de relación.
2. **Habla simultánea cercana: sigue sin resolverse, y es un límite de fondo.**
   Cuando otra persona habla *a la vez* que el usuario y a distancia parecida, el
   rechazo se queda en 3 dB — igual que antes de `speaker_lock`. La razón no es de
   ajuste: la identidad sirve para decidir **de quién es** un tramo, y ahí los dos
   están presentes de verdad, así que la única salida sería **separar** las dos
   señales (extracción de hablante objetivo, §2: no desplegable). El diseño
   detecta esa situación y deja pasar el audio a propósito, que es lo correcto
   —el usuario está hablando— pero significa que el acompañante también pasa.
   Vías reales si esto se vuelve crítico: licenciar Krisp VIVA-tel / ai-coustics,
   o entrenar un modelo causal a 8 kHz con voces competidoras a 0 dB.
   Lo que sí quedó resuelto es la fuente ajena **continua** (televisión, radio,
   altavoz): de 10.2 a 21.5 dB.
3. **El primer segundo de cada playback cancela peor.** Cualquier AEC necesita
   converger. Mitigado porque la escucha está cerrada durante el playback.
4. **`speaker_lock` se juega todo en el enrolamiento.** Si el patrón se aprende
   de la persona equivocada, la etapa silencia al usuario durante el resto de la
   llamada. Las defensas son tres (nivel dominante, rango dinámico de campo
   cercano, sin playback de Lyra) y hasta que el patrón existe no se atenúa nada,
   pero es el fallo que hay que vigilar en producción: el log dice cuándo se
   aprende y `stats()` publica `enrolled`, `similarity` y `foreign_ratio`. Un
   `foreign_ratio` alto de forma sostenida es la señal de alarma.
5. **La medición que falta es la de verdad: WER sobre llamadas reales.** Todos los
   números de §4 son de laboratorio con voz sintetizada y fondos sintéticos. La
   varianza publicada entre "mejora un 84 %" y "empeora 46 puntos" depende del
   audio, no del modelo. La grabadora ya guarda el audio crudo: pasar 100-200
   llamadas reales por `services.audio.enhance_pcm_once` con varias configuraciones
   y comparar WER separando inserciones de borrados.
6. **`mod_audio_stream` en su edición comunitaria limita a 10 canales simultáneos.**
   Verificar la licencia antes de dimensionar para 20-40 llamadas: puede ser el
   techo antes que la CPU.
6. **Precalentamiento opcional.** `services.audio.prewarm_async()` carga modelos y
   calienta los caminos perezosos (~2.4 s). Sin llamarlo, la primera llamada del
   proceso paga ese coste (repartido, sin bloquear el bucle, porque la construcción
   está diferida al hilo de trabajo). Una línea en el arranque de la app lo elimina;
   no se añadió para no tocar nada fuera de la capa de audio.
7. **Control de admisión.** `max_concurrent_calls()` devuelve el tope calculado, pero
   **rechazar la llamada N+1 exige tocar el router del WebSocket**, que queda fuera
   del alcance de este cambio. Mientras no se haga, la protección es la válvula de
   `AUDIO_BLOCK_TIMEOUT_MS` y la métrica `blocks_degraded`.
