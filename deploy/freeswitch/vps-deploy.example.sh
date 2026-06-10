#!/bin/bash
# Ejecutar EN EL VPS — reemplazar variables antes de correr.
# NO commitear este archivo con secretos reales.

set -euo pipefail

REPO_DIR=/opt/intellitaxi/virtualtIA
ENV_FILE=/opt/intellitaxi/secrets/lyra-freeswitch.env
FS_CONF=/etc/freeswitch

# --- Lyra IA (Docker) ---
cd "$REPO_DIR"
git fetch origin
git checkout feature/freeswitch-direct-sip-integration
git pull

export LYRA_ENV_FILE="$ENV_FILE"
docker compose -f docker-compose.freeswitch.yml up -d --build
curl -sf http://127.0.0.1:8000/freeswitch/health && echo " Lyra OK"

# --- FreeSWITCH gateway Twilio (archivo privado, fuera de git) ---
# Crear $FS_CONF/sip_profiles/external/twilio_ia.xml manualmente con tus credenciales.

# --- Dialplan ---
cp deploy/freeswitch/dialplan/public/intellitaxi.xml.example \
   "$FS_CONF/dialplan/public/intellitaxi.xml"
sed -i 's|WS_URL_PLACEHOLDER|ws://127.0.0.1:8000/freeswitch/audio|g' \
   "$FS_CONF/dialplan/public/intellitaxi.xml"

fs_cli -x "reloadxml"
fs_cli -x "sofia profile external rescan"
fs_cli -x "sofia status gateway twilio_ia"
