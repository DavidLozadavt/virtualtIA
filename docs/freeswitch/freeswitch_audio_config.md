# FreeSWITCH — Configuración de audio crudo para STT (PCMU, sin VAD/CNG/AGC)

> **Estado:** la configuración viva de FreeSWITCH **NO está en este repositorio**.
> El repo solo contiene plantillas (`docs/freeswitch/*.template`) y la guía de
> despliegue (`docs/freeswitch/VPS_DEPLOY.md`). Los cambios de abajo deben
> aplicarse **manualmente en el servidor FreeSWITCH**.
>
> **Objetivo:** entregar a Lyra audio G.711 PCMU 8 kHz lo más "crudo" posible,
> sin que FreeSWITCH haga su propio VAD, comfort-noise (CNG) ni AGC. Todo ese
> procesamiento ya lo hace la aplicación (`services/telephony/audio_vad.py` y el
> remuestreo/preproceso en `services/telephony/stt_service.py`). Si FreeSWITCH
> también lo hace, introduce silencios sintéticos, recortes y ganancia variable
> que son la causa raíz de transcripciones inconsistentes (la misma palabra
> devuelve transcripciones distintas en intentos repetidos).

---

## Por qué cada cambio

| Cambio | Razón |
|--------|-------|
| Forzar **PCMU** (G.711 µ-law), sin transcodificación | Evita que FreeSWITCH transcodifique a otro codec y vuelva a PCMU, paso que altera el audio. PCMU es lo que espera el pipeline (`TELEPHONY_AUDIO_CODEC=PCMU`, `TELEPHONY_SAMPLE_RATE=8000`). |
| **VAD de FreeSWITCH = none** | El VAD propio del perfil sofia compite con el VAD por energía de la app y puede cortar frames o insertar silencios → rompe la detección de fin de habla. |
| **Comfort noise (CNG) off** | El CNG sustituye silencios por ruido sintético; ese ruido contamina el cálculo del piso de ruido del VAD adaptativo y confunde a Whisper. |
| **AGC off** | El control automático de ganancia normaliza el volumen de forma no determinista → la misma palabra llega con amplitud distinta cada vez. |
| **PLC off (opcional)** | El Packet Loss Concealment rellena paquetes perdidos con audio sintético; mejor entregar el hueco real que un parche inventado. |

---

## Archivos típicos a tocar en el servidor

Rutas estándar de una instalación FreeSWITCH (Debian/Ubuntu, paquete oficial):

- Perfil SIP entrante (externo): `/etc/freeswitch/sip_profiles/external.xml`
  (o `external/entel.xml` si el gateway vive en un include).
- Variables globales: `/etc/freeswitch/vars.xml`
- Dialplan entrante: `/etc/freeswitch/dialplan/public/99_lyra_ai.xml`
  (el que se generó desde `docs/freeswitch/ai_dialplan.xml.template`).

> Aplica los cambios en el **orden** de las secciones 1→4. El más importante y
> el más seguro (no requiere reinicio) es el de **dialplan** (sección 3): fija
> codec y desactiva CNG/AGC **por llamada**, sin tocar el perfil global.

---

## 1. Forzar PCMU en el perfil SIP / gateway

En `/etc/freeswitch/sip_profiles/external.xml` (dentro de `<settings>` del
`<profile name="external">`) asegúrate de que las preferencias de codec sean
**solo PCMU**:

```xml
<param name="inbound-codec-prefs"  value="PCMU"/>
<param name="outbound-codec-prefs" value="PCMU"/>
<!-- No re-negociar codec a mitad de llamada -->
<param name="inbound-late-negotiation" value="false"/>
<param name="disable-transcoding" value="true"/>
```

Y en el `<gateway>` Entel (ver `docs/freeswitch/entel_gateway.xml.template`):

```xml
<!-- Antes: value="PCMU,PCMA" -->
<param name="codec-prefs" value="PCMU"/>
```

Globalmente, en `/etc/freeswitch/vars.xml`:

```xml
<X-PRE-PROCESS cmd="set" data="global_codec_prefs=PCMU"/>
<X-PRE-PROCESS cmd="set" data="outbound_codec_prefs=PCMU"/>
```

## 2. Desactivar VAD del perfil sofia

En el mismo `<profile name="external">` → `<settings>`:

```xml
<!-- Valores posibles: in | out | both | none -->
<param name="vad" value="none"/>
```

> En muchos builds el default ya es `none`, pero **fíjalo explícito** para que un
> cambio futuro de defaults no reactive el VAD interno.

## 3. Dialplan: codec, comfort-noise y AGC por llamada (recomendado)

En `/etc/freeswitch/dialplan/public/99_lyra_ai.xml`, **antes** del `answer`
(añadir junto a los otros `<action application="set" .../>`):

```xml
<!-- Fijar codec sin transcodificación, por llamada -->
<action application="set" data="absolute_codec_string=PCMU"/>

<!-- Comfort noise (CNG) OFF: no rellenar silencios con ruido sintético -->
<action application="set" data="suppress_cng=true"/>
<action application="set" data="rtp_silence_factor=0"/>

<!-- AGC OFF: no normalizar ganancia (no aplicar spandsp_start_agc en ningún punto) -->
<!-- (no se setea ninguna variable de AGC; basta con NO invocar start_agc) -->

<!-- Opcional: PLC OFF, entregar huecos reales en vez de audio inventado -->
<action application="set" data="rtp_jitter_buffer_plc=false"/>
```

> **AGC:** FreeSWITCH no aplica AGC por defecto en una llamada simple; solo se
> activa si alguien llama `spandsp_start_agc`/`start_agc` o lo configura en
> conferencia. Verifica que **ningún** dialplan/script lo invoque para estas
> llamadas (`grep -ri "agc" /etc/freeswitch/`).

## 4. (Si aplica) Quitar AGC/CNG residual

```bash
grep -rin -e "agc" -e "cng" -e "comfort" /etc/freeswitch/
```

Si aparece `spandsp_start_agc`, `start_agc`, `suppress_cng=false` o un
`<param name="vad" value="both"/>`, revísalo y déjalo en el estado de arriba.

---

## Recargar sin caer el servicio

```bash
# 1. Releer todo el XML (dialplan + perfiles). No corta llamadas activas.
fs_cli -x "reloadxml"

# 2. Aplicar cambios del perfil sofia external (rescan = no corta registros)
fs_cli -x "sofia profile external rescan"
#   Si rescan no toma los codec-prefs, usar restart (corta llamadas del perfil):
# fs_cli -x "sofia profile external restart"
```

> **Importante:** los cambios en `vars.xml` (sección 1, `X-PRE-PROCESS`) se
> procesan **solo al arrancar**. `reloadxml` NO los re-ejecuta. Para que tomen
> efecto sin reiniciar todo, usa la vía de **dialplan** (`absolute_codec_string`,
> sección 3), que sí aplica por llamada tras `reloadxml`. Si necesitas el
> `vars.xml`, programa un reinicio en ventana de mantenimiento:
> `systemctl restart freeswitch`.

---

## Verificación en logs

### a) Trazar negociación SDP / codec activo

```bash
fs_cli
# dentro de la consola:
console loglevel debug
sofia global siptrace on        # ver INVITE/200 OK con el SDP
```

En una llamada de prueba, busca en consola:

- En el SDP del INVITE/200 OK: **solo** `m=audio ... 0` (payload 0 = PCMU) y
  `a=rtpmap:0 PCMU/8000`. Si aparecen otros codecs negociados, los `codec-prefs`
  no se aplicaron.
- Línea de activación de codec:
  `switch_core_codec.c ... Activating Codec PCMU (8000)` para read y write.
- **No** debe aparecer `Transcoding` ni un segundo codec activado.

### b) Inspeccionar un canal en curso

```bash
fs_cli -x "show channels"                 # obtener el UUID
fs_cli -x "uuid_dump <UUID>" | grep -iE "codec|cng|agc|vad|plc"
```

Confirmar:

- `read_codec` = `PCMU`, `write_codec` = `PCMU`
- `suppress_cng` = `true`
- `absolute_codec_string` = `PCMU`
- ninguna variable `*agc*` activa

### c) Estado del perfil

```bash
fs_cli -x "sofia status profile external" | grep -iE "codec|vad"
```

Debe listar PCMU como única preferencia.

---

## Resumen de aplicación manual (checklist)

- [ ] `external.xml`: `inbound/outbound-codec-prefs=PCMU`, `disable-transcoding=true`, `vad=none`
- [ ] `entel.xml` (gateway): `codec-prefs="PCMU"`
- [ ] `vars.xml`: `global_codec_prefs=PCMU` (requiere restart)
- [ ] dialplan `99_lyra_ai.xml`: `absolute_codec_string=PCMU`, `suppress_cng=true`, `rtp_jitter_buffer_plc=false`
- [ ] `grep -ri agc /etc/freeswitch/` → sin AGC activo
- [ ] `fs_cli -x "reloadxml"` + `sofia profile external rescan`
- [ ] Llamada de prueba: SDP solo PCMU, sin `Transcoding`, `uuid_dump` confirma codec/CNG/AGC
