"""Match a WhatsApp location pin to the nearest channel and its owning agency.

Production version: PostGIS `ST_DWithin` / `ST_ClosestPoint` against surveyed drain
geometry with an agency column. This stand-in uses straight lines between nodes.
"""
from __future__ import annotations

import math

from .network import EDGES, NODES

MAX_DISTANCE_M = 2500


def _xy(lat: float, lng: float, lat0: float) -> tuple[float, float]:
    k = 111_320.0
    return lng * k * math.cos(math.radians(lat0)), lat * k


def _seg_dist(p, a, b) -> float:
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def nearest_channel(lat: float, lng: float) -> tuple[dict | None, float]:
    best, best_d = None, float("inf")
    for e in EDGES:
        a, b = NODES[e["from"]], NODES[e["to"]]
        d = _seg_dist(_xy(lat, lng, lat), _xy(a["lat"], a["lng"], lat), _xy(b["lat"], b["lng"], lat))
        if d < best_d:
            best, best_d = e, d
    return (best, best_d) if best_d <= MAX_DISTANCE_M else (None, best_d)


def channel_midpoint(edge_id: str) -> tuple[float, float]:
    e = next(x for x in EDGES if x["id"] == edge_id)
    a, b = NODES[e["from"]], NODES[e["to"]]
    return (a["lat"] + b["lat"]) / 2, (a["lng"] + b["lng"]) / 2
