"""Drainage network and hydraulic model for the Velachery–Pallikaranai–Okkiyam corridor.

All sections, slopes, roughness values and coordinates are illustrative placeholders.
Replace them with GCC / WRD drain survey data before any real use.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx

# x, y = position on the schematic map (SVG units); lat, lng = approximate location
NODES: dict[str, dict] = {
    "vel":   {"x": 80,  "y": 52,  "label": "Velachery",          "kind": "catch", "area": 60, "C": 0.80, "lat": 12.9815, "lng": 80.2180},
    "tar":   {"x": 250, "y": 52,  "label": "Taramani",           "kind": "catch", "area": 40, "C": 0.75, "lat": 12.9863, "lng": 80.2432},
    "med":   {"x": 420, "y": 52,  "label": "Medavakkam",         "kind": "catch", "area": 70, "C": 0.70, "lat": 12.9171, "lng": 80.1923},
    "per":   {"x": 575, "y": 52,  "label": "Perungudi",          "kind": "catch", "area": 50, "C": 0.80, "lat": 12.9654, "lng": 80.2461},
    "j1":    {"x": 165, "y": 172, "label": "Veerangal jn.",      "kind": "junc", "lat": 12.9690, "lng": 80.2210},
    "j2":    {"x": 420, "y": 172, "label": "Velachery lake",     "kind": "junc", "lat": 12.9560, "lng": 80.2050},
    "j3":    {"x": 575, "y": 172, "label": "Perungudi jn.",      "kind": "junc", "lat": 12.9560, "lng": 80.2400},
    "marsh": {"x": 390, "y": 272, "label": "Pallikaranai marsh", "kind": "marsh", "lat": 12.9450, "lng": 80.2180},
    "okk":   {"x": 505, "y": 362, "label": "Okkiyam Madavu",     "kind": "junc", "lat": 12.9300, "lng": 80.2330},
    "sea":   {"x": 600, "y": 400, "label": "Sea outfall",        "kind": "out",  "lat": 12.8000, "lng": 80.2500},
}

EDGES: list[dict] = [
    {"id": "e1", "from": "vel",   "to": "j1",    "name": "Velachery Main Rd drain",            "agency": "GCC",         "tier": "Street drain", "b": 3.0,  "d": 1.5, "n": 0.015, "S": 0.0010},
    {"id": "e2", "from": "tar",   "to": "j1",    "name": "Taramani Link Rd drain",             "agency": "GCC",         "tier": "Street drain", "b": 2.4,  "d": 1.4, "n": 0.015, "S": 0.0010},
    {"id": "e3", "from": "med",   "to": "j2",    "name": "Medavakkam Main Rd drain",           "agency": "GCC",         "tier": "Street drain", "b": 3.2,  "d": 1.6, "n": 0.015, "S": 0.0010},
    {"id": "e4", "from": "per",   "to": "j3",    "name": "Perungudi drain",                    "agency": "GCC",         "tier": "Street drain", "b": 2.8,  "d": 1.5, "n": 0.015, "S": 0.0010},
    {"id": "e5", "from": "j1",    "to": "marsh", "name": "Veerangal Odai",                     "agency": "WRD",         "tier": "Feeder canal", "b": 6.0,  "d": 2.0, "n": 0.025, "S": 0.0008},
    {"id": "e6", "from": "j2",    "to": "marsh", "name": "Velachery lake surplus course",      "agency": "WRD",         "tier": "Feeder canal", "b": 4.5,  "d": 2.0, "n": 0.025, "S": 0.0008},
    {"id": "e7", "from": "j3",    "to": "marsh", "name": "Perungudi feeder",                   "agency": "WRD",         "tier": "Feeder canal", "b": 4.0,  "d": 1.8, "n": 0.025, "S": 0.0008},
    {"id": "e8", "from": "marsh", "to": "okk",   "name": "Marsh outlet channel",               "agency": "Forest Dept", "tier": "Marsh outlet", "b": 10.0, "d": 2.0, "n": 0.035, "S": 0.0005},
    {"id": "e9", "from": "okk",   "to": "sea",   "name": "Okkiyam Madavu to Buckingham Canal", "agency": "WRD",         "tier": "Outfall",      "b": 10.0, "d": 2.5, "n": 0.030, "S": 0.0004},
]
EDGE_BY_ID = {e["id"]: e for e in EDGES}

# bf/df/nf = multipliers on width, depth and Manning's n
BLOCKAGES: dict[str, dict] = {
    "none":     {"label": "None"},
    "plastic":  {"label": "Plastic / solid waste", "machine": "Jetting-cum-suction truck",      "bf": 0.75, "df": 1.0, "nf": 1.3},
    "hyacinth": {"label": "Water hyacinth",        "machine": "Amphibious weed harvester",      "bf": 1.0,  "df": 1.0, "nf": 2.2},
    "silt":     {"label": "Silt deposition",       "machine": "Super-sucker desilting machine", "bf": 1.0,  "df": 0.6, "nf": 1.0},
    "rubble":   {"label": "Construction rubble",   "machine": "Long-reach excavator + tipper",  "bf": 0.5,  "df": 1.0, "nf": 1.1},
}

GRAPH = nx.DiGraph()
for _id, _n in NODES.items():
    GRAPH.add_node(_id, **_n)
for _e in EDGES:
    GRAPH.add_edge(_e["from"], _e["to"], id=_e["id"])
NODE_ORDER = list(nx.topological_sort(GRAPH))


@dataclass
class Scenario:
    rain: float = 55.0          # mm/h
    tide_high: bool = True
    marsh_healthy: bool = True


def manning(edge: dict, blockage: str = "none") -> dict:
    """Manning's equation for a rectangular channel: Q = (1/n) A R^(2/3) S^(1/2)."""
    k = BLOCKAGES.get(blockage, BLOCKAGES["none"])
    b = edge["b"] * k.get("bf", 1.0)
    d = edge["d"] * k.get("df", 1.0)
    n = edge["n"] * k.get("nf", 1.0)
    area = b * d
    radius = area / (b + 2 * d)
    q = (1 / n) * area * radius ** (2 / 3) * math.sqrt(edge["S"])
    return {"b": b, "d": d, "n": n, "A": area, "R": radius, "Q": q}


def simulate(sc: Scenario, blockages: dict[str, str]) -> dict:
    attenuation = 0.4 if sc.marsh_healthy else 0.15
    outflow = {nid: 0.0 for nid in NODES}
    for nid, n in NODES.items():
        if n["kind"] == "catch":
            outflow[nid] = n["C"] * sc.rain * n["area"] / 360.0      # rational method, m³/s

    res: dict[str, dict] = {}
    for nid in NODE_ORDER:                                            # route downstream
        for _, dst, data in GRAPH.out_edges(nid, data=True):
            q = outflow[nid]
            res[data["id"]] = {"demand": q}
            outflow[dst] += q * (1 - attenuation) if dst == "marsh" else q

    for e in EDGES:
        m = manning(e, blockages.get(e["id"], "none"))
        cap = m["Q"] * (0.55 if e["id"] == "e9" and sc.tide_high else 1.0)
        r = res[e["id"]]
        r.update(n_eff=m["n"], cap_free=m["Q"], cap=cap, local_util=r["demand"] / cap, backwater=False)

    # backwater: an overloaded channel throttles the channels feeding it
    for nid in reversed(NODE_ORDER):
        for src, _, data in GRAPH.in_edges(nid, data=True):
            r = res[data["id"]]
            downs = [d["id"] for _, _, d in GRAPH.out_edges(nid, data=True)]
            r["down"] = downs[0] if downs else None
            if r["down"] and res[r["down"]]["util"] > 1:
                du = res[r["down"]]["util"]
                f = max(0.5, 1 - 0.5 * min(du - 1, 1))
                if nid == "marsh" and sc.marsh_healthy:
                    f = 1 - (1 - f) * 0.5                             # marsh storage damps backwater
                r["cap"] *= f
                r["backwater"] = True
            r["util"] = r["demand"] / r["cap"]
    # outfall edge(s) have no downstream
    for e in EDGES:
        res[e["id"]].setdefault("util", res[e["id"]]["demand"] / res[e["id"]]["cap"])
        res[e["id"]].setdefault("down", None)

    for e in EDGES:
        r = res[e["id"]]
        r["over"] = r["util"] > 1
        r["status"] = "over" if r["util"] > 1 else "watch" if r["util"] > 0.8 else "ok"
        r["root"] = r["over"] and r["local_util"] > 1               # would overflow even with free flow below
        r["symptom"] = r["over"] and not r["root"]

    inflow = sum(outflow[k] for k, n in NODES.items() if n["kind"] == "catch")
    return {"res": res, "inflow": inflow}


def upstream_catchments(node_id: str) -> set[str]:
    if NODES[node_id]["kind"] == "catch":
        return {NODES[node_id]["label"]}
    out: set[str] = set()
    for src in GRAPH.predecessors(node_id):
        out |= upstream_catchments(src)
    return out


def build_actions(sc: Scenario, blockages: dict[str, str], sim: dict) -> list[dict]:
    actions = []
    for e in EDGES:
        r = sim["res"][e["id"]]
        blk = blockages.get(e["id"], "none")
        has_blk = blk != "none"
        pr = cause = action = None
        if r["root"]:
            pr = "P1" if r["util"] > 1.3 else "P2"
            if has_blk:
                cause = f"{BLOCKAGES[blk]['label']} cutting capacity to {r['cap']:.1f} m³/s"
                action = f"{BLOCKAGES[blk]['machine']}. No manual entry into the channel."
            elif e["id"] == "e9" and sc.tide_high:
                cause = "High tide limiting outfall discharge"
                action = "Stage 2 high-capacity dewatering pumps at Okkiyam; inspect sand bar at the mouth."
            elif e["id"] == "e8":
                cause = "Marsh outlet over design capacity"
                action = "Forest Dept field check of outlet weir; clear debris."
            else:
                cause = "Design capacity exceeded"
                action = "Mobile dewatering pumps at downstream junction; flag for capacity upgrade."
        elif has_blk:
            pr = "P2" if r["over"] else "P3"
            cause = BLOCKAGES[blk]["label"] + (" (also under backwater)" if r["over"] else " reported")
            action = BLOCKAGES[blk]["machine"] + (" once downstream clears." if r["over"] else ". Clear before next rain band.")
        if pr:
            actions.append({"channel": e["id"], "name": e["name"], "agency": e["agency"], "priority": pr,
                            "cause": cause, "action": action, "util": r["util"], "clearable": has_blk})
    actions.sort(key=lambda a: (a["priority"], -a["util"]))
    return actions


def cap_alert(sim: dict) -> dict | None:
    severe = [e for e in EDGES if sim["res"][e["id"]]["util"] > 1.2]
    if not severe:
        return None
    places: set[str] = set()
    for e in severe:
        places |= upstream_catchments(e["from"])
    return {"places": sorted(places), "peak": max(sim["res"][e["id"]]["util"] for e in severe)}
