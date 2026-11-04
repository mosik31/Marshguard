#!/usr/bin/env bash
# Start MarshGuard in a GitHub Codespace (or locally) and print the links you need.
set -e
cd "$(dirname "$0")/backend"
[ -f .env ] || cp .env.example .env
pip install -q -r requirements.txt

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
SERVER=$!
sleep 4

if [ -n "$CODESPACE_NAME" ]; then
  BASE="https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
  if gh codespace ports visibility 8000:public -c "$CODESPACE_NAME" >/dev/null 2>&1; then
    echo "Port 8000 is now Public."
  else
    echo "Could not make port 8000 public automatically."
    echo "Open the PORTS tab, right-click port 8000 → Port Visibility → Public."
  fi
else
  BASE="http://localhost:8000"
fi

cat <<MSG

==================================================================
 MarshGuard is running
   Dashboard          $BASE/dashboard
   WhatsApp simulator $BASE/whatsapp
   Meta callback URL  $BASE/webhook/whatsapp
   Meta verify token  $(grep '^WHATSAPP_VERIFY_TOKEN=' .env | cut -d= -f2-)
 Edit backend/.env, then press Ctrl+C and run ./start.sh again.
==================================================================

MSG
wait $SERVER
