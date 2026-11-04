"""SQLite persistence. Swap for PostgreSQL + PostGIS in production; the queries are plain SQL."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .network import EDGES, Scenario

DATA_DIR = Path(os.getenv("MARSHGUARD_DATA", Path(__file__).resolve().parents[1] / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "photos").mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "marshguard.db"

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS channels (id TEXT PRIMARY KEY, blockage TEXT NOT NULL DEFAULT 'none');
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT, source TEXT, channel TEXT, cls TEXT, confidence REAL,
  status TEXT,            -- verified | review | rejected | low_confidence
  photo TEXT, lat REAL, lng REAL, created REAL);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, channel TEXT, cls TEXT, agency TEXT, report_id INTEGER, phone TEXT,
  status TEXT,            -- open | cleared
  created REAL, closed REAL);
CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS contacts (phone TEXT PRIMARY KEY, name TEXT, last_inbound REAL);
CREATE TABLE IF NOT EXISTS seen_messages (id TEXT PRIMARY KEY, ts REAL);
"""


def init(seed_demo: bool = True) -> None:
    with _lock:
        _conn.executescript(SCHEMA)
        for e in EDGES:
            _conn.execute("INSERT OR IGNORE INTO channels(id) VALUES (?)", (e["id"],))
        if seed_demo and _conn.execute("SELECT v FROM settings WHERE k='seeded'").fetchone() is None:
            _conn.execute("UPDATE channels SET blockage='plastic' WHERE id='e1'")
            _conn.execute("UPDATE channels SET blockage='hyacinth' WHERE id='e5'")
            _conn.execute("INSERT INTO settings VALUES ('seeded','1')")
        _conn.commit()


def _q(sql: str, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur


# ---- scenario -------------------------------------------------------------
def get_scenario() -> Scenario:
    rows = {r["k"]: r["v"] for r in _q("SELECT k, v FROM settings")}
    return Scenario(rain=float(rows.get("rain", 55)), tide_high=rows.get("tide_high", "1") == "1",
                    marsh_healthy=rows.get("marsh_healthy", "1") == "1")


def get_setting(k: str, default: str | None = None) -> str | None:
    r = _q("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_setting(k: str, v) -> None:
    _q("INSERT INTO settings(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


# ---- channels -------------------------------------------------------------
def blockages() -> dict[str, str]:
    return {r["id"]: r["blockage"] for r in _q("SELECT id, blockage FROM channels")}


def set_blockage(channel: str, blockage: str) -> None:
    _q("UPDATE channels SET blockage=? WHERE id=?", (blockage, channel))


# ---- reports & tickets ----------------------------------------------------
def add_report(**kw) -> int:
    kw.setdefault("created", time.time())
    cols = ",".join(kw)
    return _q(f"INSERT INTO reports({cols}) VALUES ({','.join('?' * len(kw))})", tuple(kw.values())).lastrowid


def update_report(rid: int, **kw) -> None:
    sets = ",".join(f"{k}=?" for k in kw)
    _q(f"UPDATE reports SET {sets} WHERE id=?", (*kw.values(), rid))


def get_report(rid: int):
    r = _q("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return dict(r) if r else None


def recent_reports(limit: int = 15) -> list[dict]:
    return [dict(r) for r in _q("SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,))]


def next_ticket_id() -> str:
    n = _q("SELECT COUNT(*) AS c FROM tickets").fetchone()["c"]
    return f"MG-{3100 + n}"


def add_ticket(channel: str, cls: str, agency: str, report_id: int, phone: str) -> str:
    tid = next_ticket_id()
    _q("INSERT INTO tickets VALUES (?,?,?,?,?,?, 'open', ?, NULL)",
       (tid, channel, cls, agency, report_id, phone, time.time()))
    return tid


def open_tickets() -> list[dict]:
    return [dict(r) for r in _q("SELECT * FROM tickets WHERE status='open' ORDER BY created")]


def get_ticket(tid: str):
    r = _q("SELECT * FROM tickets WHERE id=?", (tid.upper(),)).fetchone()
    return dict(r) if r else None


def close_channel_tickets(channel: str) -> list[dict]:
    rows = [dict(r) for r in _q("SELECT * FROM tickets WHERE channel=? AND status='open'", (channel,))]
    _q("UPDATE tickets SET status='cleared', closed=? WHERE channel=? AND status='open'", (time.time(), channel))
    return rows


# ---- WhatsApp conversation sessions --------------------------------------
def get_session(phone: str) -> dict:
    r = _q("SELECT data FROM sessions WHERE phone=?", (phone,)).fetchone()
    return json.loads(r["data"]) if r else {}


def save_session(phone: str, data: dict) -> None:
    _q("INSERT INTO sessions VALUES (?, ?) ON CONFLICT(phone) DO UPDATE SET data=excluded.data",
       (phone, json.dumps(data)))


# ---- WhatsApp contacts and duplicate protection -----------------------------
def touch_contact(phone: str, name: str | None) -> None:
    _q("INSERT INTO contacts VALUES (?, ?, ?) ON CONFLICT(phone) DO UPDATE SET "
       "name=COALESCE(excluded.name, contacts.name), last_inbound=excluded.last_inbound",
       (phone, name, time.time()))


def last_inbound(phone: str) -> float | None:
    r = _q("SELECT last_inbound FROM contacts WHERE phone=?", (phone,)).fetchone()
    return r["last_inbound"] if r else None


def first_time_seen(message_id: str | None) -> bool:
    """Meta can deliver the same webhook more than once. True only the first time."""
    if not message_id:
        return True
    cur = _q("INSERT OR IGNORE INTO seen_messages VALUES (?, ?)", (message_id, time.time()))
    _q("DELETE FROM seen_messages WHERE ts < ?", (time.time() - 7 * 86400,))
    return cur.rowcount == 1


def reset_demo() -> None:
    with _lock:
        _conn.executescript("DELETE FROM reports; DELETE FROM tickets; DELETE FROM sessions; DELETE FROM settings; DELETE FROM seen_messages;"
                            "UPDATE channels SET blockage='none';")
        _conn.commit()
    init(seed_demo=True)
