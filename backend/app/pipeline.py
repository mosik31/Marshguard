"""Citizen reporting conversation, shared by the real WhatsApp webhook and the simulator.

WhatsApp delivers a photo and a location pin as two separate messages, so the bot keeps a
small per-phone session and files the report once it has both.
"""
from __future__ import annotations

import re

from . import classifier, geo, store
from .network import BLOCKAGES, EDGE_BY_ID, manning


def T(text: str) -> dict:
    return {"type": "text", "text": text}


def ASK_LOCATION(text: str) -> dict:
    """Shown on WhatsApp as a message with a 'Send location' button."""
    return {"type": "location_request", "text": text}

GREETING = (
    "Vanakkam! Send a photo of a blocked drain or canal, then share your location. "
    "We verify it and route it to the right department.\n\n"
    "வணக்கம்! அடைப்பு உள்ள வடிகாலின் புகைப்படத்தையும் இருப்பிடத்தையும் அனுப்பவும்."
)


def _finalize(phone: str, source: str, sess: dict) -> tuple[list[dict], dict | None]:
    photo, loc = sess["photo"], sess["loc"]
    edge = EDGE_BY_ID[loc["channel"]]
    store.save_session(phone, {})

    if photo.get("cls") is None:  # no model configured: human review
        rid = store.add_report(phone=phone, source=source, channel=edge["id"], cls=None, confidence=None,
                               status="review", photo=photo.get("path"), lat=loc["lat"], lng=loc["lng"])
        return ([T(f"Thanks. Your photo near {edge['name']} is with an engineer for verification. "
                   f"Reference R-{rid}. We will message you when a ticket is raised.")],
                {"kind": "review", "report": rid, "channel": edge["id"]})

    cls, conf = photo["cls"], photo["conf"]
    before = manning(edge, store.blockages().get(edge["id"], "none"))["Q"]
    rid = store.add_report(phone=phone, source=source, channel=edge["id"], cls=cls, confidence=conf,
                           status="verified", photo=photo.get("path"), lat=loc["lat"], lng=loc["lng"])
    tid = store.add_ticket(edge["id"], cls, edge["agency"], rid, phone)
    store.set_blockage(edge["id"], cls)
    after = manning(edge, cls)["Q"]
    reply = (f"Verified: {BLOCKAGES[cls]['label']} (confidence {conf:.2f}).\n"
             f"Location matched to {edge['name']}, maintained by {edge['agency']}.\n\n"
             f"Ticket {tid} raised. Reply STATUS {tid} for updates.")
    return [T(reply)], {"kind": "ticket", "ticket": tid, "channel": edge["id"], "name": edge["name"],
                     "agency": edge["agency"], "cls": cls, "label": BLOCKAGES[cls]["label"],
                     "machine": BLOCKAGES[cls]["machine"], "cap_before": before, "cap_after": after}


async def handle(phone: str, msg: dict, source: str = "whatsapp") -> dict:
    """msg: {type: text|image|location, ...}. Returns {replies: [...], event: {...}|None}."""
    sess = store.get_session(phone)
    kind = msg.get("type")

    if kind == "text":
        text = (msg.get("text") or "").strip()
        m = re.match(r"(?i)status\s+(mg-\d+)", text)
        if m:
            t = store.get_ticket(m.group(1))
            if not t:
                return {"replies": [T(f"No ticket {m.group(1).upper()} found.")], "event": None}
            state = "cleared by the field team ✅" if t["status"] == "cleared" else f"open with {t['agency']}"
            return {"replies": [T(f"Ticket {t['id']} ({BLOCKAGES[t['cls']]['label']}) is {state}.")], "event": None}
        return {"replies": [T(GREETING)], "event": None}

    if kind == "image":
        if "sample" in msg:
            cls, conf = classifier.classify_sample(msg["sample"])
            result = (cls, conf)
        else:
            result = classifier.classify_image(msg["bytes"])
        photo = {"path": msg.get("photo_path")}
        if result is None:
            photo.update(cls=None, conf=None)
        else:
            cls, conf = result
            if conf < classifier.THRESHOLD or cls not in BLOCKAGES or cls == "none":
                store.add_report(phone=phone, source=source, channel=(sess.get("loc") or {}).get("channel"),
                                 cls=cls, confidence=conf, status="low_confidence", photo=msg.get("photo_path"))
                return {"replies": [T(f"Photo confidence {conf:.2f} is below {classifier.THRESHOLD:.2f}, so we could "
                                      "not confirm a blockage.\nPlease send a clearer photo in daylight, from closer "
                                      "to the drain.")], "event": None}
            photo.update(cls=cls, conf=conf)
        sess["photo"] = photo
        if sess.get("loc"):
            replies, event = _finalize(phone, source, sess)
            return {"replies": replies, "event": event}
        store.save_session(phone, sess)
        hint = f" It looks like {BLOCKAGES[photo['cls']]['label'].lower()}." if photo.get("cls") else ""
        return {"replies": [ASK_LOCATION(f"Photo received.{hint} Now tap Send location so we can find the drain.")],
                "event": None}

    if kind == "location":
        edge, dist = geo.nearest_channel(msg["lat"], msg["lng"])
        if edge is None:
            return {"replies": [T("That location is outside the MarshGuard pilot corridor "
                                  "(Velachery – Pallikaranai – Okkiyam). Please report it to GCC on 1913.")],
                    "event": None}
        sess["loc"] = {"lat": msg["lat"], "lng": msg["lng"], "channel": edge["id"]}
        if sess.get("photo"):
            replies, event = _finalize(phone, source, sess)
            return {"replies": replies, "event": event}
        store.save_session(phone, sess)
        return {"replies": [T(f"Location received, near {edge['name']} ({int(dist)} m). Now send a photo of the blockage.")],
                "event": None}

    return {"replies": [T("Please send a photo, a location pin, or STATUS followed by your ticket number.")], "event": None}
