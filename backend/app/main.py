"""MarshGuard API server.

Run:  uvicorn app.main:app --reload --port 8000   (from the backend/ folder)
Dashboard:  http://localhost:8000/dashboard
WhatsApp simulator:  http://localhost:8000/whatsapp
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import httpx  # noqa: E402
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import classifier, geo, network, pipeline, store, whatsapp  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("marshguard")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
app = FastAPI(title="MarshGuard API", version="0.2.0")
store.init()
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
if whatsapp.configured() and not whatsapp.APP_SECRET:
    log.warning("WHATSAPP_APP_SECRET is not set: webhook signatures are NOT being checked. "
                "Set it before sharing the number publicly.")


# ---------------------------------------------------------------- live updates
class Hub:
    def __init__(self) -> None:
        self.clients: set[asyncio.Queue] = set()

    async def publish(self, event: dict) -> None:
        for q in list(self.clients):
            await q.put(event)


hub = Hub()


def state_payload() -> dict:
    sc = store.get_scenario()
    blk = store.blockages()
    sim = network.simulate(sc, blk)
    tickets = store.open_tickets()
    channels = []
    for e in network.EDGES:
        r = sim["res"][e["id"]]
        channels.append({**e, "blockage": blk.get(e["id"], "none"),
                         "citizen_tickets": [t["id"] for t in tickets if t["channel"] == e["id"]],
                         **{k: r[k] for k in ("demand", "cap", "cap_free", "util", "local_util", "status",
                                              "root", "symptom", "backwater", "n_eff")}})
    actions = network.build_actions(sc, blk, sim)
    for a in actions:
        a["citizen_tickets"] = [t["id"] for t in tickets if t["channel"] == a["channel"]]
    return {
        "type": "state",
        "scenario": {"rain": sc.rain, "tideHigh": sc.tide_high, "marshHealthy": sc.marsh_healthy,
                     "rainSource": store.get_setting("rain_source", "manual"),
                     "rainNote": store.get_setting("rain_note", "")},
        "nodes": network.NODES,
        "channels": channels,
        "inflow": sim["inflow"],
        "actions": actions,
        "alert": network.cap_alert(sim),
        "reports": [public_report(r) for r in store.recent_reports()],
        "blockTypes": {k: v["label"] for k, v in network.BLOCKAGES.items()},
        "integrations": {"whatsapp": whatsapp.configured(), "yolo": classifier.model_available(),
                         "whatsappStatus": whatsapp.status()},
        "ts": time.time(),
    }


async def broadcast_state() -> dict:
    s = state_payload()
    await hub.publish(s)
    return s


@app.get("/api/stream")
async def stream(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    hub.clients.add(q)

    async def gen():
        try:
            yield f"data: {json.dumps(state_payload())}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            hub.clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- pages
@app.get("/")
def root():
    return RedirectResponse("/dashboard")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(FRONTEND / "dashboard.html")


@app.get("/whatsapp")
def whatsapp_page():
    return FileResponse(FRONTEND / "whatsapp.html")


# ---------------------------------------------------------------- dashboard API
@app.get("/api/state")
def get_state():
    return state_payload()


class ScenarioIn(BaseModel):
    rain: float | None = None
    tideHigh: bool | None = None
    marshHealthy: bool | None = None


@app.post("/api/scenario")
async def set_scenario(body: ScenarioIn):
    if body.rain is not None:
        store.set_setting("rain", max(0.0, min(200.0, body.rain)))
        store.set_setting("rain_source", "manual")
        store.set_setting("rain_note", "")
    if body.tideHigh is not None:
        store.set_setting("tide_high", int(body.tideHigh))
    if body.marshHealthy is not None:
        store.set_setting("marsh_healthy", int(body.marshHealthy))
    return await broadcast_state()


@app.post("/api/rain/live")
async def live_rain():
    """Pull this hour's rainfall for Velachery from Open-Meteo (mm in the hour = mm/h)."""
    n = network.NODES["vel"]
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={n['lat']}&longitude={n['lng']}&hourly=precipitation&forecast_hours=6&timezone=Asia%2FKolkata")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            data = (await client.get(url)).raise_for_status().json()
    except Exception as exc:
        raise HTTPException(502, f"Could not reach Open-Meteo: {exc}") from exc
    hours = data["hourly"]["precipitation"]
    now, peak = float(hours[0] or 0), float(max(h or 0 for h in hours))
    store.set_setting("rain", now)
    store.set_setting("rain_source", "open-meteo")
    store.set_setting("rain_note", f"Open-Meteo, Velachery: {now:.1f} mm this hour, peak {peak:.1f} mm/h in the next 6 h")
    return await broadcast_state()


class BlockageIn(BaseModel):
    blockage: str


@app.post("/api/channels/{cid}/blockage")
async def set_channel_blockage(cid: str, body: BlockageIn):
    if cid not in network.EDGE_BY_ID or body.blockage not in network.BLOCKAGES:
        raise HTTPException(404, "Unknown channel or blockage type")
    store.set_blockage(cid, body.blockage)
    return await broadcast_state()


@app.post("/api/channels/{cid}/clear")
async def clear_channel(cid: str):
    if cid not in network.EDGE_BY_ID:
        raise HTTPException(404, "Unknown channel")
    store.set_blockage(cid, "none")
    for t in store.close_channel_tickets(cid):
        await notify(t["phone"], t["id"], f"Ticket {t['id']} at {network.EDGE_BY_ID[cid]['name']} has been cleared by the "
                                 f"field team. Thank you for reporting. ✅")
    return await broadcast_state()


class ReviewIn(BaseModel):
    cls: str | None = None   # None = reject


@app.post("/api/reports/{rid}/review")
async def review_report(rid: int, body: ReviewIn):
    rep = store.get_report(rid)
    if not rep or rep["status"] != "review":
        raise HTTPException(404, "No report awaiting review with that id")
    edge = network.EDGE_BY_ID[rep["channel"]]
    if body.cls is None:
        store.update_report(rid, status="rejected")
        await notify(rep["phone"], f"R-{rid}", f"Report R-{rid}: an engineer checked your photo and found no blockage needing "
                                   f"action. Thank you for reporting.")
    else:
        if body.cls not in network.BLOCKAGES or body.cls == "none":
            raise HTTPException(400, "Unknown blockage type")
        store.update_report(rid, status="verified", cls=body.cls, confidence=None)
        tid = store.add_ticket(edge["id"], body.cls, edge["agency"], rid, rep["phone"])
        store.set_blockage(edge["id"], body.cls)
        await notify(rep["phone"], tid, f"Report R-{rid} verified: {network.BLOCKAGES[body.cls]['label']} at "
                                   f"{edge['name']}. Ticket {tid} raised with {edge['agency']}. "
                                   f"Reply STATUS {tid} for updates.")
    return await broadcast_state()


@app.post("/api/reset")
async def reset():
    store.reset_demo()
    await hub.publish({"type": "reset"})
    return await broadcast_state()


async def notify(phone: str | None, ref: str, text: str) -> None:
    """Message a citizen: real WhatsApp for real numbers, the simulator's chat for sim users."""
    if not phone:
        return
    if phone.startswith("sim-"):
        await hub.publish({"type": "bot", "phone": phone, "text": text})
        return
    result = await whatsapp.notify(phone, ref, text)
    if result in ("skipped", "failed"):
        await hub.publish({"type": "notice", "text": f"Could not message the reporter of {ref} on WhatsApp. "
                                                     f"See the WhatsApp status panel for the reason."})


def mask_phone(phone: str | None) -> str:
    if not phone or phone.startswith("sim-"):
        return "Simulator"
    return "+" + "•" * max(0, len(phone) - 4) + phone[-4:]


def public_report(r: dict) -> dict:
    """What the dashboard may see: no full phone numbers or file paths."""
    out = {k: v for k, v in r.items() if k not in ("phone", "photo")}
    out["reporter"] = mask_phone(r.get("phone"))
    out["has_photo"] = bool(r.get("photo")) and Path(r["photo"]).is_file()
    return out


@app.get("/api/reports/{rid}/photo")
def report_photo(rid: int):
    rep = store.get_report(rid)
    if not rep or not rep.get("photo") or not Path(rep["photo"]).is_file():
        raise HTTPException(404, "No photo for this report")
    path = Path(rep["photo"]).resolve()
    if store.DATA_DIR.resolve() / "photos" not in path.parents:
        raise HTTPException(404, "No photo for this report")
    return FileResponse(path)


@app.get("/api/whatsapp/status")
def whatsapp_status():
    return whatsapp.status()


# ---------------------------------------------------------------- simulator API
class SimMessage(BaseModel):
    phone: str = "sim-demo"
    type: str                      # text | image | location
    text: str | None = None
    sample: str | None = None      # plastic | hyacinth | silt | rubble | blurry
    channel: str | None = None     # location pin dropped on this channel's midpoint
    lat: float | None = None
    lng: float | None = None


@app.post("/api/sim/message")
async def sim_message(m: SimMessage):
    if not m.phone.startswith("sim-"):
        raise HTTPException(400, "Simulator phone ids must start with 'sim-'")
    if m.type == "image":
        msg = {"type": "image", "sample": m.sample or "plastic"}
    elif m.type == "location":
        lat, lng = (m.lat, m.lng) if m.lat is not None else geo.channel_midpoint(m.channel or "e1")
        msg = {"type": "location", "lat": lat, "lng": lng}
    else:
        msg = {"type": "text", "text": m.text or ""}
    out = await pipeline.handle(m.phone, msg, source="simulator")
    await broadcast_state()
    return out


@app.get("/api/channels")
def channel_list():
    return [{"id": e["id"], "name": e["name"], "agency": e["agency"],
             "lat": geo.channel_midpoint(e["id"])[0], "lng": geo.channel_midpoint(e["id"])[1]}
            for e in network.EDGES]


# ---------------------------------------------------------------- WhatsApp Cloud API webhook
@app.get("/webhook/whatsapp")
def verify_webhook(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == whatsapp.VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")


@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()
    if not whatsapp.valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
        log.warning("Rejected webhook with a bad signature. Check WHATSAPP_APP_SECRET.")
        raise HTTPException(401, "Bad signature")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Body is not JSON")
    store.set_setting("wa_last_webhook", time.time())
    # Reply 200 at once; Meta retries slow webhooks. Do the work in the background.
    background.add_task(process_webhook, payload)
    return {"ok": True}


async def process_webhook(payload: dict) -> None:
    for item in whatsapp.parse_messages(payload):
        phone, msg = item["phone"], item["msg"]
        if not store.first_time_seen(item["id"]):
            continue                                   # Meta re-delivered a message we already handled
        store.touch_contact(phone, item["name"])       # opens the 24-hour reply window
        if item["id"]:
            await whatsapp.mark_read(item["id"])       # blue ticks for the citizen
        try:
            if msg["type"] == "image":
                try:
                    data, path = await whatsapp.download_media(msg["media_id"])
                except Exception as exc:
                    log.warning("Photo download failed for …%s: %s", phone[-4:], exc)
                    await whatsapp.send_text(phone, "We could not open that photo. Please send it again as a "
                                                    "normal photo (JPEG or PNG, under 5 MB).")
                    continue
                msg = {"type": "image", "bytes": data, "photo_path": path}
            out = await pipeline.handle(phone, msg, source="whatsapp")
            for reply in out["replies"]:
                await whatsapp.send_reply(phone, reply)
            await broadcast_state()
        except Exception:
            log.exception("Failed to process WhatsApp message from …%s", phone[-4:])
            await whatsapp.send_text(phone, "Sorry, something went wrong on our side. Please try again in a minute.")
