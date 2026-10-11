#!/usr/bin/env python3
"""SK106 economic decision engine v1. Read-only by design.

No game actions. Provides a single deterministic ranking for the existing
watchdog and an auditable 24h resource-opportunity plan.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path("/home/worker/tribal-sk106-lab")
API = "http://127.0.0.1:8766/api/v1/game/overview"
OUTPUT = ROOT / "world-intel" / "growth-engine-latest.json"
BASE_POINTS = {
    "main": 10, "barracks": 16, "smith": 19, "market": 10,
    "wood": 6, "stone": 6, "iron": 6, "farm": 5,
    "storage": 6, "hide": 5, "wall": 8,
}
MINE_PROD = {"wood": "wood_prod", "stone": "clay_prod", "iron": "iron_prod"}
PROD_KEYS = {"wood": "wood_prod", "clay": "clay_prod", "iron": "iron_prod"}
RESOURCE = ("wood", "clay", "iron")


def _value(v, b):
    name = b["name"]
    level = b["level"]
    if name not in BASE_POINTS or b.get("next_level") is None:
        return None
    costs = [b.get("next_wood"), b.get("next_clay"), b.get("next_iron")]
    if not all(type(x) is int and x >= 0 for x in costs):
        return None
    if not b.get("can_build"):
        return None
    if b.get("queued_level") is not None:
        return None
    if b.get("blocker") and not str(b["blocker"]).startswith("Suroviny"):
        return None
    prod = [max(0.1, float(v.get(x, 0))) for x in ("wood_prod", "clay_prod", "iron_prod")]
    stock = [float(v.get(x, 0)) for x in RESOURCE]
    wait_h = max(max(0, costs[i] - stock[i]) / prod[i] for i in range(3))
    build_h = max(0.02, float(b.get("build_time") or 1800) / 3600)
    p = BASE_POINTS[name]
    next_points = float(p) if level == 0 else max(1.0, p * (1.2 ** level - 1.2 ** (level - 1)))
    # Weighted cost is a scarcity-aware resource opportunity cost, not just raw total.
    weighted = sum(costs[i] / prod[i] for i in range(3))
    resource_h = weighted / 3
    horizon_h = 24
    multiplier = 1.0
    reasons = []
    if name in MINE_PROD:
        # Approximate 16% output gain/level; 24h incremental resources have long-term value.
        incremental = float(v.get(MINE_PROD[name], 0)) * 0.16 * max(0, horizon_h - wait_h - build_h)
        multiplier += min(1.6, incremental / max(1, sum(costs)) * 2.2)
        reasons.append("24h production return")
        if name == "iron" and v.get("iron_prod", 0) < min(v.get("wood_prod", 0), v.get("clay_prod", 0)):
            multiplier += 0.4
            reasons.append("iron bottleneck")
    if name == "farm":
        occupancy = v.get("pop_current", 0) / max(1, v.get("pop_max", 1))
        if occupancy < 0.72:
            multiplier *= 0.10
            reasons.append("unused farm capacity")
    if name == "storage":
        fullness = max(stock) / max(1, v.get("storage", 1))
        if fullness < 0.78:
            multiplier *= 0.12
            reasons.append("unused warehouse capacity")
    if name == "hide":
        multiplier *= 0.23
        reasons.append("low early economic return")
    if name == "wall":
        multiplier *= 0.5 if v.get("incomings", 0) == 0 else 1.5
        reasons.append("defensive reserve")
    if name == "barracks":
        multiplier *= 0.45
    if name == "smith" and level == 0:
        multiplier *= 1.65
        reasons.append("quest/tech unlock candidate; verify reward")
    if name == "market" and level == 0:
        multiplier *= 1.2
    if name == "main" and level < 10:
        multiplier *= 1.2
    score = round(multiplier * next_points / (0.35 + resource_h * 0.4 + wait_h * 0.25 + build_h * 0.25), 4)
    return {"name": name, "level": level, "next_level": b["next_level"],
            "cost": dict(zip(RESOURCE, costs)), "wait_hours": round(wait_h, 2),
            "build_hours": round(build_h, 2), "estimated_points_increment": round(next_points, 2),
            "score": score, "reason": ", ".join(reasons) or "points and resource opportunity"}


def evaluate(v):
    entries = [x for b in v["buildings"] if (x := _value(v, b)) is not None]
    entries.sort(key=lambda x: (-x["score"], x["wait_hours"], x["name"]))
    return entries


def rank(v, candidates):
    allowed = set(candidates)
    scores = evaluate(v)
    return [x["name"] for x in scores if x["name"] in allowed and x["score"] >= 1.5]


def main():
    with urlopen(API, timeout=10) as r:
        overview = json.load(r)
    p = overview["player"]
    v = overview["villages"][0]
    if (p.get("name"), p.get("world"), v.get("coords")) != ("chlpatucky", "sk106", "502|458"):
        raise RuntimeError("PIN_MISMATCH")
    plan = evaluate(v)
    result = {
        "mode": "READ_ONLY_ADVISORY", "world": "sk106",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_synced_at": v.get("synced_at"),
        "points": p["points"],
        "resources": {x: v[x] for x in RESOURCE},
        "production_per_hour": {x: v[PROD_KEYS[x]] for x in RESOURCE},
        "population_used": v.get("pop_current"), "population_max": v.get("pop_max"),
        "warehouse_capacity": v.get("storage"),
        "queue": [{"name": b["name"], "level": b["queued_level"], "until": b.get("queued_until")}
                  for b in v["buildings"] if b.get("queued_level") is not None],
        "candidates": plan, "recommendation": plan[0]["name"] if plan else None,
        "notes": ["Not a guaranteed 24h maximum", "Quest rewards and scavenging yields are not modeled",
                  "No game actions are issued by this engine"],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"points": p["points"], "recommendation": result["recommendation"],
                      "top5": plan[:5], "queue": result["queue"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
