-- lyra_call.lua — conversación por record-loop (FreeSWITCH stock, sin mod_audio_stream)
--
-- Flujo por llamada:
--   1. answer + record_session (graba la llamada COMPLETA para el frontend)
--   2. POST /freeswitch/inbound-call  -> baja y reproduce el saludo
--   3. LOOP: graba locución del usuario (silencio = fin) -> POST /freeswitch/audio-turn
--            -> baja y reproduce la respuesta -> si hangup, corta
--   4. Al colgar: sube la grabación completa a /freeswitch/recording
--
-- Requiere en el contenedor: mod_lua, busybox wget (--post-file, --post-data).
-- APP = host:puerto por el que FreeSWITCH alcanza el app (el MISMO del saludo).

local APP = "http://172.17.0.1:8098"

local uuid   = session:get_uuid()
local caller = session:getVariable("caller_id_number") or ""
local dest   = session:getVariable("destination_number") or ""

-- ── Parámetros de grabación de la locución (calidad de transcripción) ──
-- max_len:   tope de segundos por locución (generoso: no cortar a adultos mayores)
-- sil_thr:   umbral de energía para considerar "silencio" (más bajo = más sensible)
-- sil_secs:  segundos de silencio que marcan fin de la locución
local UTT_MAX_LEN  = 15
local UTT_SIL_THR  = 200
local UTT_SIL_SECS = 3
local MAX_TURNS    = 20

session:answer()
session:sleep(400)

-- Grabación de llamada completa (ambas patas) → un solo WAV
local rec_path = "/tmp/rec_" .. uuid .. ".wav"
session:execute("record_session", rec_path)

-- ── Helpers HTTP (busybox wget; cuerpo crudo) ──
local function post_file(url, file)
  local out = "/tmp/resp_" .. uuid .. ".txt"
  os.execute(string.format(
    "busybox wget -q -O %s --header=Content-Type:audio/wav --post-file=%s '%s'",
    out, file, url))
  local fh = io.open(out, "r"); if not fh then return "" end
  local body = fh:read("*a"); fh:close(); return body or ""
end

local function download(url, dest_file)
  os.execute(string.format("busybox wget -q -O %s '%s'", dest_file, url))
end

-- Extrae "key":"valor" | true | false desde JSON simple (sin lib json)
local function jfield(body, key)
  return body:match('"' .. key .. '"%s*:%s*"(.-)"')
      or body:match('"' .. key .. '"%s*:%s*(true)')
      or body:match('"' .. key .. '"%s*:%s*(false)')
end

local function play_url(url)
  if not url or url == "" or url == "null" then return end
  local f = "/tmp/play_" .. uuid .. ".wav"
  download(url, f)
  session:streamFile(f)
end

-- ── Saludo: POST inbound-call (crea sesión + genera saludo) ──
do
  local out = "/tmp/inb_" .. uuid .. ".json"
  os.execute(string.format(
    "busybox wget -q -O %s "
    .. "--post-data='call_uuid=%s&caller_number=%s&destination_number=%s&source=entel' "
    .. "--header=Content-Type:application/x-www-form-urlencoded "
    .. "'%s/freeswitch/inbound-call'",
    out, uuid, caller, dest, APP))
  local fh = io.open(out, "r"); local body = fh and fh:read("*a") or ""
  if fh then fh:close() end
  play_url(jfield(body, "audio_url"))
end

-- ── Bucle de conversación ──
for i = 1, MAX_TURNS do
  if not session:ready() then break end

  local utt = "/tmp/utt_" .. uuid .. ".wav"
  session:recordFile(utt, UTT_MAX_LEN, UTT_SIL_THR, UTT_SIL_SECS)
  if not session:ready() then break end

  local body      = post_file(APP .. "/freeswitch/audio-turn?call_uuid=" .. uuid, utt)
  local audio_url = jfield(body, "audio_url")
  local hangup    = jfield(body, "hangup")

  play_url(audio_url)

  if hangup == "true" then break end
end

-- ── Fin: detener y subir la grabación completa ──
session:execute("stop_record_session", rec_path)
os.execute(string.format(
  "busybox wget -q -O /dev/null --header=Content-Type:audio/wav --post-file=%s "
  .. "'%s/freeswitch/recording?call_uuid=%s'",
  rec_path, APP, uuid))

session:hangup()
