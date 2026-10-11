#!/usr/bin/env python3
"""SK106 Light Cavalry Rush advisory: GET-only, never submits game actions.

Turn a verified own-village snapshot into actionable, non-premium manual steps.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path("/home/worker/tribal-sk106-lab")
BASE = "http://127.0.0.1:8766"
OUT = ROOT / "world-intel" / "lc-rush-latest.json"
RES = ("wood", "clay", "iron")
PROD = {"wood": "wood_prod", "clay": "clay_prod", "iron": "iron_prod"}
ACCOUNT = ("chlpatucky", "sk106", "502|458")


def get(path: str) -> dict | list:
    # No POST/PATCH, no browser click, no game mutations.
    with urlopen(BASE + path, timeout=12) as response:
        return json.load(response)


def wait_minutes(v: dict, building: dict) -> int | None:
    costs = [building.get("next_" + r) for r in RES]
    if not all(type(c) is int and c >= 0 for c in costs):
        return None
    if not building.get("can_build"):
        return None
    waits = []
    for resource, cost in zip(RES, costs):
        missing = max(0, cost - float(v.get(resource, 0)))
        rate = float(v.get(PROD[resource], 0))
        if missing and rate <= 0:
            return None
        waits.append(missing / rate if missing else 0.0)
    return math.ceil(60 * max(waits))


def steps(v: dict) -> list[dict]:
    b = {x["name"]: x for x in v.get("buildings", [])}
    out = []
    # A tactical sequence, not an unverified claim about game's future costs.
    # Every completed or queued milestone is excluded from manual instructions.
    for name, lvl, why in (
        ("smith", 4, "Priamy krok k úlohe na odomknutie stajní"),
        ("main", 9, "Hlavná budova na cestu k úrovni 10"),
        ("smith", 5, "Požiadavka questu na sprístupnenie stajní"),
        ("main", 10, "Požiadavka questu na sprístupnenie stajní"),
        ("stable", 1, "Dokončiť quest odomknutia stajní"),
        ("stable", 2, "Priblížiť sa k ľahkej jazde"),
        ("stable", 3, "Podmienka dostupnosti ľahkej jazdy podľa herného prehľadu"),
    ):
        item = b.get(name)
        if not item:
            continue
        reached = max(int(item.get("level") or 0), int(item.get("queued_level") or 0))
        if reached >= lvl:
            continue
        # Only show verified next-level costs. No fabricated cost for later levels.
        next_level = item.get("next_level")
        if next_level == lvl and item.get("can_build"):
            status = "affordable" if wait_minutes(v, item) == 0 else "saving"
            price = {r: item.get("next_" + r) for r in RES}
            eta = wait_minutes(v, item)
        else:
            status = "locked_or_preceding_level"
            price, eta = None, None
        out.append({"building": name, "target_level": lvl, "status": status,
                    "resource_wait_min": eta, "cost": price, "why": why})
    return out


def advice(player: dict, village: dict, quests: dict, scavenge: list) -> dict:
    if (player.get("name"), player.get("world"), village.get("coords")) != ACCOUNT:
        raise RuntimeError("ACCOUNT_PIN_MISMATCH")
    ts = village.get("synced_at")
    if not ts:
        raise RuntimeError("MISSING_GAME_SYNC")
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
    if age > 900 or age < -60:
        raise RuntimeError("STALE_GAME_SNAPSHOT")
    levels = {x["name"]: int(x.get("level") or 0) for x in village["buildings"]}
    queue = [{"building": x["name"], "level": x["queued_level"],
              "until": x.get("queued_until")}
             for x in village["buildings"] if x.get("queued_level") is not None]
    units = {x["name"]: {"total": x.get("total", 0), "home": x.get("home", 0),
                         "away": x.get("away", 0)}
             for x in village.get("units", []) if x.get("total", 0)}
    todo = steps(village)
    primary = next((x for x in todo if x["status"] in ("affordable", "saving")), None)
    if not primary and todo:
        primary = todo[0]
    # Explicitly separate hints from confirmed world state.
    quest_titles = [q.get("title") for q in quests.get("quests", [])
                    if q.get("state") != "done"]
    sc = scavenge[0] if scavenge else {}
    return {
        "mode": "READ_ONLY_MANUAL_ADVISORY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "world": ACCOUNT[1], "account": ACCOUNT[0],
        "source_synced_at": ts, "points": player.get("points"),
        "rank": player.get("rank"),
        "resources": {r: village.get(r) for r in RES},
        "production_per_hour": {r: village.get(PROD[r]) for r in RES},
        "levels": levels, "queue": queue, "units": units,
        "main_goal": "Odomknúť stajne 3, preveriť výskum a spustiť ručné farmenie ľahkou jazdou",
        "primary_manual_step": primary,
        "milestones": todo, "incomplete_quest_titles": quest_titles,
        "scavenging": {
            "free_options": sc.get("free_options", []),
            "units_home": sc.get("units_home", {}),
            "note": sc.get("note", "")
        },
        "manual_followups": [
            "Skontrolovať herné odmeny z questov a získať tie, na ktoré už vznikol nárok",
            "Po návrate armády využívať zber surovín; nechať dostatočnú rezervu na obranu",
            "Po odomknutí stajní preveriť presné podmienky výskumu a verbovania ľahkej jazdy",
            "Na farmenie vyberať barbarské dediny podľa bezpečnosti a výnosu, nie hráčske dediny"
        ],
        "risk_note": "Iba odporúčania na manuálne hranie. Nepoužívať automatické kliknutia bez schválenia hernej podpory.",
        "notes": [
            "Zostávajúce náklady budúcich úrovní nie sú v snímke známe.",
            "Čas dostupnosti surovín neobsahuje budúce náklady, loot, odmeny ani prebiehajúce výdavky.",
            "Kritérium úspechu: dostupnosť ľahkej jazdy, získané suroviny/hodinu, rast bodov, vojenská produkcia."
        ]
    }


def main() -> None:
    overview = get("/api/v1/game/overview")
    report = advice(overview["player"], overview["villages"][0],
                    get("/api/v1/agents/quests"),
                    get("/api/v1/game/scavenge-plan"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(OUT)
    print(json.dumps({"points": report["points"],
                      "recommendation": report["primary_manual_step"],
                      "queued": report["queue"],
                      "mode": report["mode"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
