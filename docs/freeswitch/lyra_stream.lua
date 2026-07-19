-- lyra_stream.lua — Lyra Voice V2: streaming full-duplex vía mod_audio_stream
--
-- Flujo por llamada:
--   1. answer
--   2. uuid_audio_stream start → WS bidireccional con Lyra
--        · FreeSWITCH envía el audio del llamante (PCM16 8k mono) en tiempo real
--        · Lyra devuelve el TTS con mensajes streamAudio (playback full-duplex)
--   3. la llamada queda viva mientras conversa; Lyra cuelga vía ESL uuid_kill
--
-- Ya NO hay: record-loop, wget, base64, archivos WAV por turno, ni subida de
-- grabación al colgar (la grabación completa la mezcla y guarda el servidor).
--
-- Requiere: mod_audio_stream cargado (load mod_audio_stream).
-- WS_BASE = host:puerto por el que FreeSWITCH alcanza el app Lyra.

local WS_BASE = "ws://172.17.0.1:8098"

local function urlencode(s)
  return (tostring(s or ""):gsub("[^%w%-%_%.]", function(c)
    return string.format("%%%02X", string.byte(c))
  end))
end

local uuid   = session:get_uuid()
local caller = session:getVariable("caller_id_number") or ""

session:answer()

local ws_url = WS_BASE .. "/freeswitch/audio"
  .. "?call_uuid=" .. urlencode(uuid)
  .. "&caller_number=" .. urlencode(caller)

local api = freeswitch.API()
local res = api:executeString(
  "uuid_audio_stream " .. uuid .. " start " .. ws_url .. " mono 8k")
freeswitch.consoleLog("info",
  "[lyra_stream] uuid=" .. uuid .. " ws=" .. ws_url .. " res=" .. tostring(res) .. "\n")

-- Mantener la llamada viva mientras el WS conversa. Lyra termina la llamada
-- con uuid_kill (ESL); si el usuario cuelga, session:ready() se vuelve false.
while session:ready() do
  session:sleep(500)
end

api:executeString("uuid_audio_stream " .. uuid .. " stop")
