# Diagnóstico: "no entiende / dice otro barrio u otra dirección"

**Fecha:** 2026-06-25
**Rama:** `fix/stt-origin-prefix-cleanup`
**Síntoma reportado:** usuarios se quejan de que el sistema de voz no los entiende y
confirma/usa un barrio o dirección distinta a la que dijeron.
**Método:** investigación de causa raíz (systematic-debugging, Iron Law: sin fix sin
causa confirmada). Este documento es solo diagnóstico — **no se modificó código**.

---

## 1. Mapa del pipeline — dónde nace un barrio equivocado

```
µ-law 8 kHz mono (FreeSWITCH mod_audio_stream)
  │
  ▼  CAPA A — STT
services/telephony/stt_service.py
  modelo: gpt-4o-mini-transcribe   (+ prompt = ~40 barrios del catálogo)
  │   transcript crudo
  ▼  CAPA B — post-proceso
api/routers/freeswitch.py:_normalize_transcript → api/routers/twilio.py:preprocess_stt
  └ core/stt_enhancer.py:repair_location_transcription  (snap fonético al catálogo)
  │   transcript "normalizado"
  ▼  CAPA C — resolución + decisión
services/telephony/voice_call_engine.py → core/location_match.py
  resolve_location_entity() → decide()  →  origen
  │
  ▼
TTS le repite el barrio al usuario  ("Perfecto, <barrio>…")
```

Las tres capas pueden, de forma independiente, sustituir el barrio real del usuario
por otro. Compuestas, se refuerzan: A/B empujan el texto hacia nombres del catálogo,
y C acepta ese nombre sin verificarlo.

---

## 2. Causa raíz #1 (humo claro) — `CONFIRM` tratado como `ACCEPT`

**Archivo:** `services/telephony/voice_call_engine.py:312`

```python
if d in (Decision.ACCEPT, Decision.CONFIRM) and m.canonical:
    origen = m.canonical
    trusted = True
```

`core/location_match.py` está diseñado **precision-first**. Clasifica el match por tipo
y confianza (`_base_decision`):

| Tipo / confianza                         | Decision   | Intención del diseño              |
|------------------------------------------|------------|-----------------------------------|
| EXACT / ALIAS_EXACT / SUBSTRING          | `ACCEPT`   | fijar ubicación (confiable)       |
| PHONETIC 0.85+ margin≥0.08               | `ACCEPT`   | fijar                             |
| **PHONETIC 0.70–0.85**                   | **CONFIRM**| **preguntar "¿Te refieres a X?"** |
| **FUZZY 0.62–0.85**                      | **CONFIRM**| **preguntar**                     |
| débil                                    | `REJECT`   | caer a LLM / pedir repetir        |

El motor **colapsa `CONFIRM` dentro de `ACCEPT`**: fija `origen`, marca `trusted=True`
y **nunca pregunta**. Es decir, todo match fonético/fuzzy de confianza media —
exactamente los que el matcher marcó como "dudoso, verificá" — se commitea en silencio
a un barrio que puede estar mal. **Esto es literalmente "le dice otro barrio".**

**Por qué está así:** se quitó deliberadamente el "¿me confirmas?" para reducir
fricción (ver memoria `project_freeswitch_voice_flow`). El trade-off salió mal: eliminó
la confirmación justo en los matches que más la necesitaban. No es un bug de tipeo —
es una decisión de producto que hay que revertir parcialmente.

**Riesgo de fix:** bajo. Separar las ramas: `ACCEPT` sigue silencioso; `CONFIRM` hace
una pregunta corta de sí/no antes de fijar `origen`.

---

## 3. Causa raíz #2 — el prompt del STT sesga al modelo a alucinar barrios

**Archivo:** `services/telephony/stt_service.py` → `_build_stt_prompt()` (usado en
`_transcribe_wav_bytes`, kwarg `prompt`).

Se inyectan hasta **40 nombres de barrio del catálogo** + términos prioritarios en el
`prompt` de `gpt-4o-mini-transcribe`.

Whisper y la familia gpt-4o-transcribe tratan `prompt` como **prior fuerte**, no como
una simple pista. Sobre audio telefónico degradado (8 kHz µ-law), el modelo tiende a
**sustituir** lo que "casi" oyó por uno de los nombres listados. Más nombres en el
prompt ⇒ más sustituciones a barrio equivocado. Es el modo de fallo clásico del
prompt-biasing.

Problemas asociados en la misma capa:
- `confidence = 1.0` **hardcodeado** para modelos no-whisper (gpt-4o-mini no da
  confianza por palabra). Ese 1.0 falso fluye por todo el sistema → ninguna capa
  posterior tiene señal para desconfiar de un transcript malo.
- Modelo `gpt-4o-mini-transcribe` = el tier más débil de transcripción 4o. Candidato a
  upgrade (`gpt-4o-transcribe`), pero es decisión aparte de los bugs de lógica.

**Riesgo de fix:** medio. Reducir el tamaño del prompt (o quitar barrios y dejar solo
ciudad/vías), o medir A/B con y sin lista de barrios. Requiere validar con audio real.

---

## 4. Causa raíz #3 — `repair_location_transcription` reescribe palabras al catálogo

**Archivo:** `core/stt_enhancer.py:841` (`preprocess_stt` paso 5b).

`_best_catalog_snap` toma spans de 3/2/1 palabras y los "snapea" fonéticamente a una
entidad del catálogo **antes** de que corra el matcher. Si la guarda
(`_REPAIR_MIN_LEN`, unicidad, stopwords) es laxa, una palabra real del usuario se
sobrescribe en silencio por un barrio del catálogo.

Corre en **cada turno**. Junto con el prompt del STT (#2), produce un **doble sesgo**
hacia nombres del catálogo: el modelo ya tendió a un barrio listado, y luego el repair
lo empuja aún más. Las commits recientes de la rama tocan repetidamente esta zona de
post-proceso (greetings/nombres, word-boundary, duplicados) — señal de que es
inestable y se viene parchando por síntomas.

**Riesgo de fix:** medio-alto (toca lógica compartida con el gateway Twilio). No tocar
sin tests de regresión sobre los casos curados.

---

## 5. Cómo los tres se componen

```
audio turbio  ──prompt 40 barrios──►  STT emite barrio plausible-pero-ajeno   (A)
              ──repair snap──────────►  refuerza/cambia a barrio del catálogo  (B)
              ──CONFIRM≡ACCEPT───────►  motor lo fija sin preguntar            (C)
                                        TTS: "Perfecto, <barrio equivocado>"
```

Cada capa baja la barrera para el error; C lo hace irreversible al no confirmar.

---

## 6. Evidencia para confirmar qué capa domina (ANTES de fixear)

El código ya loguea todo lo necesario. Por cada turno de una llamada real, leer 3
líneas y comparar:

| Log                                                            | Capa | Qué revela            |
|---------------------------------------------------------------|------|-----------------------|
| `[stt/openai] transcript_text="…"`                            | A    | salida cruda del STT  |
| `[freeswitch/ws] transcript raw="…" norm="…"`                 | B    | ¿el repair lo cambió? |
| `[engine] origin… / disambiguated -> …`                       | C    | barrio final fijado   |

Lectura del diff:
- `raw` ya viene mal → **Capa A** (STT/prompt).
- `raw` bien, `norm` mal → **Capa B** (repair).
- `norm` bien, barrio final mal → **Capa C** (CONFIRM-as-ACCEPT o colisión fonética).

**Acción recomendada:** juntar ~10–20 llamadas con queja, clasificar cada error por
capa con esta tabla, y atacar la capa dominante primero.

---

## 7. Fixes propuestos (ordenados por confianza / riesgo)

| # | Fix                                                                                   | Capa | Riesgo     | Confianza |
|---|---------------------------------------------------------------------------------------|------|------------|-----------|
| 1 | `Decision.CONFIRM` vuelve a preguntar "¿Te refieres a X?" (sí/no) antes de fijar       | C    | bajo       | alta      |
| 2 | Reducir/quitar barrios del `prompt` del STT; medir A/B con audio real                  | A    | medio      | media     |
| 3 | Endurecer guardas de `repair_location_transcription` (unicidad/margen mínimo)          | B    | medio-alto | media     |
| 4 | Dejar de hardcodear `confidence=1.0`; propagar señal real o `None`                     | A    | bajo       | media     |
| 5 | Evaluar upgrade `gpt-4o-mini-transcribe` → `gpt-4o-transcribe`                         | A    | bajo       | baja*     |

\* baja confianza = mejora probable pero no confirma causa raíz; validar con logs/A-B.

**Iron Law:** #1 es el de mayor confianza y menor riesgo, pero ningún fix debería
mergear sin (a) la evidencia de logs de §6 y (b) un test de regresión que reproduzca
el caso real antes de tocar el código.

---

## 8. Notas

- No se modificó código en esta sesión: solo análisis estático + lectura de logs
  esperados.
- Memorias relacionadas: `project_freeswitch_voice_flow`, `project_stt_root_cause`,
  `project_location_resolution_policy`, `project_twilio_voice_tuning`.
