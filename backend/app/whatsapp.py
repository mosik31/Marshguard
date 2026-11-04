"""Meta WhatsApp Cloud API client.

Covers what a real deployment needs:
- webhook verification and X-Hub-Signature-256 checking
- parsing incoming text, image, location and button messages (status callbacks are ignored)
- downloading photos, with a size and type check
- sending text, a "Send location" button, read receipts, and approved templates
- the 24-hour customer service window: free-form messages only within 24 h of the
  citizen's last message; after that only an approved template can be sent
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

import httpx

from . import store
from .store import DATA_DIR

log = logging.getLogger("marshguard.whatsapp")

API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v23.0")
GRAPH = f"https://graph.facebook.com/{API_VERSION}"
TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "marshguard-verify").strip()
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "").strip()
# Optional approved Utility template with two body variables: {{1}} ticket id, {{2}} update text
TEMPLATE_NAME = os.getenv("WHATSAPP_TEMPLATE_NAME", "").strip()
TEMPLATE_LANG = os.getenv("WHATSAPP_TEMPLATE_LANG", "en").strip()

WINDOW_SECONDS = 24 * 3600 - 300          # keep a 5-minute safety margin
MAX_PHOTO_BYTES = 5 * 1024 * 1024         # WhatsApp's own limit for images is 5 MB
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

# A shared client so the Graph API can be mocked in tests
_transport: httpx.AsyncBaseTransport | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, transport=_transport)


def configured() -> bool:
    return bool(TOKEN and PHONE_NUMBER_ID)


def status() -> dict:
    return {
        "configured": configured(),
        "phone_number_id": PHONE_NUMBER_ID[-4:].rjust(len(PHONE_NUMBER_ID), "•") if PHONE_NUMBER_ID else "",
        "signature_check": bool(APP_SECRET),
        "custom_verify_token": VERIFY_TOKEN != "marshguard-verify",
        "template": TEMPLATE_NAME or None,
        "api_version": API_VERSION,
        "last_webhook": float(store.get_setting("wa_last_webhook", "0") or 0) or None,
        "last_error": store.get_setting("wa_last_error", "") or None,
    }


def _record_error(msg: str) -> None:
    log.error(msg)
    store.set_setting("wa_last_error", f"{time.strftime('%d %b %H:%M')} · {msg}"[:300])


# ---------------------------------------------------------------- incoming
def valid_signature(raw_body: bytes, header: str | None) -> bool:
    """Check X-Hub-Signature-256. Skipped only when no app secret is configured."""
    if not APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def parse_messages(payload: dict) -> list[dict]:
    """Flatten a webhook payload into messages. Each has id, phone, name, msg."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if PHONE_NUMBER_ID and value.get("metadata", {}).get("phone_number_id") not in (None, PHONE_NUMBER_ID):
                continue                                  # another number on the same app
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", []) or []}
            for m in value.get("messages", []) or []:     # value["statuses"] (sent/delivered/read) is ignored
                phone, t = m.get("from"), m.get("type")
                if t == "text":
                    msg = {"type": "text", "text": m["text"]["body"]}
                elif t == "image":
                    msg = {"type": "image", "media_id": m["image"]["id"], "mime": m["image"].get("mime_type", "")}
                elif t == "location":
                    loc = m["location"]
                    msg = {"type": "location", "lat": loc["latitude"], "lng": loc["longitude"]}
                elif t == "interactive" and m.get("interactive", {}).get("type") == "button_reply":
                    msg = {"type": "text", "text": m["interactive"]["button_reply"].get("title", "")}
                elif t == "button":
                    msg = {"type": "text", "text": m.get("button", {}).get("text", "")}
                else:
                    msg = {"type": "other", "raw_type": t}
                out.append({"id": m.get("id"), "phone": phone, "name": names.get(phone), "msg": msg})
    return out


async def download_media(media_id: str) -> tuple[bytes, str]:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with _client() as client:
        meta = (await client.get(f"{GRAPH}/{media_id}", headers=headers)).raise_for_status().json()
        mime = meta.get("mime_type", "image/jpeg")
        if mime not in PHOTO_TYPES:
            raise ValueError(f"Unsupported photo type {mime}")
        if int(meta.get("file_size", 0) or 0) > MAX_PHOTO_BYTES:
            raise ValueError("Photo larger than 5 MB")
        data = (await client.get(meta["url"], headers=headers)).raise_for_status().content
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Photo larger than 5 MB")
    path = DATA_DIR / "photos" / f"{int(time.time())}_{media_id}{PHOTO_TYPES[mime]}"
    path.write_bytes(data)
    return data, str(path)


# ---------------------------------------------------------------- outgoing
async def _post(payload: dict) -> bool:
    if not configured():
        log.info("WhatsApp not configured; would send: %s", payload)
        return False
    try:
        async with _client() as client:
            r = await client.post(f"{GRAPH}/{PHONE_NUMBER_ID}/messages",
                                  headers={"Authorization": f"Bearer {TOKEN}"},
                                  json={"messaging_product": "whatsapp", **payload})
    except httpx.HTTPError as exc:
        _record_error(f"Could not reach WhatsApp: {exc}")
        return False
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            detail = f"{err.get('code')} {err.get('message')}"
        except ValueError:
            detail = r.text[:200]
        if r.status_code == 401:
            detail += " (access token expired or wrong: generate a new one)"
        _record_error(f"Send failed ({r.status_code}): {detail}")
        return False
    return True


async def send_text(to: str, body: str) -> bool:
    return await _post({"recipient_type": "individual", "to": to, "type": "text",
                        "text": {"body": body[:4096], "preview_url": False}})


async def send_location_request(to: str, body: str) -> bool:
    """Message with a 'Send location' button. Falls back to plain text if it fails."""
    ok = await _post({"recipient_type": "individual", "to": to, "type": "interactive",
                      "interactive": {"type": "location_request_message", "body": {"text": body[:1024]},
                                      "action": {"name": "send_location"}}})
    return ok or await send_text(to, body)


async def mark_read(message_id: str) -> None:
    await _post({"status": "read", "message_id": message_id})


async def send_template(to: str, params: list[str]) -> bool:
    if not TEMPLATE_NAME:
        return False
    return await _post({"to": to, "type": "template", "template": {
        "name": TEMPLATE_NAME, "language": {"code": TEMPLATE_LANG},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": p[:1000]} for p in params]}]}})


def in_service_window(phone: str) -> bool:
    last = store.last_inbound(phone)
    return last is not None and time.time() - last < WINDOW_SECONDS


async def send_reply(to: str, reply: dict) -> bool:
    """Reply produced by the pipeline: {'type': 'text'|'location_request', 'text': ...}."""
    if reply.get("type") == "location_request":
        return await send_location_request(to, reply["text"])
    return await send_text(to, reply["text"])


async def notify(to: str, ticket: str, text: str) -> str:
    """Later update to a citizen (ticket cleared, review result).

    Returns 'sent', 'template', or 'skipped' (outside the 24 h window and no template set up).
    """
    if in_service_window(to):
        return "sent" if await send_text(to, text) else "failed"
    if await send_template(to, [ticket, text]):
        return "template"
    _record_error(f"Could not notify …{to[-4:]}: more than 24 h since their last message and no "
                  f"WHATSAPP_TEMPLATE_NAME is set")
    return "skipped"
