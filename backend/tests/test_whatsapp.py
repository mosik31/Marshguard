"""End-to-end WhatsApp tests against a fake Meta Graph API. No real number or internet needed.

Run from backend/:   pip install pytest   then   pytest -q
"""
import hashlib
import hmac
import json
import os
import tempfile
import time

os.environ["MARSHGUARD_DATA"] = tempfile.mkdtemp()
os.environ.update(WHATSAPP_TOKEN="test-token", WHATSAPP_PHONE_NUMBER_ID="1111", WHATSAPP_APP_SECRET="shh",
                  WHATSAPP_VERIFY_TOKEN="my-verify", WHATSAPP_TEMPLATE_NAME="ticket_update")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, store, whatsapp  # noqa: E402

SENT: list[dict] = []
PHONE = "919876543210"


def fake_graph(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and request.url.path.endswith("/1111/messages"):
        SENT.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
    if request.url.path.endswith("/media-1"):
        return httpx.Response(200, json={"url": "https://lookaside.example/media-1", "mime_type": "image/jpeg",
                                         "file_size": 1000})
    if request.url.host == "lookaside.example":
        return httpx.Response(200, content=b"\xff\xd8fakejpeg")
    return httpx.Response(404)


whatsapp._transport = httpx.MockTransport(fake_graph)
client = TestClient(main.app)


def post(messages, sign=True):
    body = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "1111"}, "contacts": [{"wa_id": PHONE, "profile": {"name": "Priya"}}],
        "messages": messages}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"shh", body, hashlib.sha256).hexdigest() if sign else "sha256=bad"
    return client.post("/webhook/whatsapp", content=body,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})


def texts():
    return [m.get("text", {}).get("body") or m.get("interactive", {}).get("body", {}).get("text") for m in SENT
            if m.get("type") in ("text", "interactive")]


@pytest.fixture(autouse=True)
def clean():
    store.reset_demo()
    SENT.clear()


def test_verify_handshake():
    r = client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "my-verify",
                                                "hub.challenge": "123"})
    assert r.status_code == 200 and r.text == "123"
    assert client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                                   "hub.challenge": "1"}).status_code == 403


def test_bad_signature_rejected():
    assert post([{"id": "w1", "from": PHONE, "type": "text", "text": {"body": "hi"}}], sign=False).status_code == 401
    assert SENT == []


def test_full_report_flow_and_notifications():
    post([{"id": "w1", "from": PHONE, "type": "text", "text": {"body": "hi"}}])
    assert "Vanakkam" in texts()[-1]
    assert any(m.get("status") == "read" and m["message_id"] == "w1" for m in SENT)

    # photo first: no YOLO weights, so the bot asks for location with a button
    post([{"id": "w2", "from": PHONE, "type": "image", "image": {"id": "media-1", "mime_type": "image/jpeg"}}])
    assert SENT[-1]["type"] == "interactive"
    assert SENT[-1]["interactive"]["type"] == "location_request_message"

    # location near Velachery Main Rd drain -> goes to manual review
    post([{"id": "w3", "from": PHONE, "type": "location", "location": {"latitude": 12.9752, "longitude": 80.2195}}])
    assert "with an engineer" in texts()[-1]
    rep = client.get("/api/state").json()["reports"][0]
    assert rep["status"] == "review" and rep["has_photo"] and rep["reporter"].endswith("3210")
    assert PHONE not in json.dumps(client.get("/api/state").json())          # full number never reaches the UI
    assert client.get(f"/api/reports/{rep['id']}/photo").status_code == 200

    # engineer verifies -> citizen is told the ticket id
    client.post(f"/api/reports/{rep['id']}/review", json={"cls": "plastic"})
    assert "Ticket MG-3100 raised" in texts()[-1]

    # status query
    post([{"id": "w4", "from": PHONE, "type": "text", "text": {"body": "STATUS MG-3100"}}])
    assert "open with GCC" in texts()[-1]

    # cleared within 24 h -> free-form text
    client.post(f"/api/channels/{rep['channel']}/clear")
    assert "has been cleared" in texts()[-1]


def test_duplicate_delivery_handled_once():
    m = [{"id": "dup", "from": PHONE, "type": "text", "text": {"body": "hi"}}]
    post(m); post(m)
    assert sum(1 for t in texts() if t and "Vanakkam" in t) == 1


def test_outside_24h_uses_template():
    post([{"id": "a", "from": PHONE, "type": "text", "text": {"body": "hi"}}])
    post([{"id": "b", "from": PHONE, "type": "image", "image": {"id": "media-1"}}])
    post([{"id": "c", "from": PHONE, "type": "location", "location": {"latitude": 12.9752, "longitude": 80.2195}}])
    rid = client.get("/api/state").json()["reports"][0]["id"]
    client.post(f"/api/reports/{rid}/review", json={"cls": "silt"})
    store._q("UPDATE contacts SET last_inbound=?", (time.time() - 25 * 3600,))   # 25 h later
    SENT.clear()
    client.post("/api/channels/e1/clear")
    assert SENT and SENT[-1]["type"] == "template"
    assert SENT[-1]["template"]["name"] == "ticket_update"
    assert SENT[-1]["template"]["components"][0]["parameters"][0]["text"] == "MG-3100"


def test_status_callbacks_ignored():
    body = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "1111"},
                                              "statuses": [{"id": "wamid.out", "status": "delivered"}]}}]}]}
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"shh", raw, hashlib.sha256).hexdigest()
    r = client.post("/webhook/whatsapp", content=raw, headers={"X-Hub-Signature-256": sig})
    assert r.status_code == 200 and SENT == []
