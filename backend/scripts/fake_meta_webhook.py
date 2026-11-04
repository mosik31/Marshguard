"""Send a signed, Meta-style WhatsApp message to your own server, without Meta.

Use it to check the webhook URL, verify token and app secret before connecting the real number.

    python scripts/fake_meta_webhook.py http://localhost:8000 text "hi"
    python scripts/fake_meta_webhook.py https://<your-host> location 12.9752 80.2195

Reads WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN and WHATSAPP_PHONE_NUMBER_ID from backend/.env.
Replies are sent to the fake number, so with a real token they fail harmlessly; watch the server log.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
base, kind = sys.argv[1].rstrip("/"), sys.argv[2]
secret = os.getenv("WHATSAPP_APP_SECRET", "")
verify = os.getenv("WHATSAPP_VERIFY_TOKEN", "marshguard-verify")

# 1. the same handshake Meta does when you click "Verify and save"
q = urllib.parse.urlencode({"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": "ok123"})
with urllib.request.urlopen(f"{base}/webhook/whatsapp?{q}") as r:
    print("Verify handshake:", "PASS" if r.read() == b"ok123" else "FAIL")

# 2. a signed incoming message
if kind == "text":
    msg = {"type": "text", "text": {"body": " ".join(sys.argv[3:]) or "hi"}}
elif kind == "location":
    msg = {"type": "location", "location": {"latitude": float(sys.argv[3]), "longitude": float(sys.argv[4])}}
else:
    sys.exit("kind must be text or location")
msg.update({"from": "910000000000", "id": f"wamid.test{int(time.time() * 1000)}", "timestamp": str(int(time.time()))})
body = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
    "messaging_product": "whatsapp",
    "metadata": {"phone_number_id": os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")},
    "contacts": [{"wa_id": "910000000000", "profile": {"name": "Test"}}], "messages": [msg]}}]}]}).encode()
headers = {"Content-Type": "application/json"}
if secret:
    headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
req = urllib.request.Request(f"{base}/webhook/whatsapp", data=body, headers=headers, method="POST")
with urllib.request.urlopen(req) as r:
    print("Signed message:", r.status, r.read().decode())
