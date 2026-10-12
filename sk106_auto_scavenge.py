#!/usr/bin/env python3
"""SK106 single-purpose auto-scavenge runner.

Uses the existing game assistant's guarded action only for send_scavenge.
No farming attacks, no upgrades, no research, no paid actions, no stealth.
PINNED to chlpatucky/sk106/502|458, fails closed on stale or uncertain data.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path("/home/worker/tribal-sk106-lab")
STATE = ROOT / "auto-scavenge-state.json"
LOCK = ROOT / "auto-scavenge.lock"
API = "http://127.0.0.1:8766"
ACCOUNT = ("chlpatucky", "sk106", "502|458")
MAX_SNAPSHOT_AGE_SECONDS = 480
MIN_RESEND_SECONDS = 20 * 60
UNCERTAIN_RETRY_SECONDS = 30 * 60
MIN_SQUAD = 10


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(text):
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def atomic_save(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def get(path):
    with urlopen(API + path, timeout=12) as response:
        return json.load(response)


def post(path, payload):
    req = Request(API + path, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=75) as response:
        return json.load(response)


def decide(overview: dict, scavenge: list, state: dict, now: datetime) -> dict:
    player = overview.get("player") or {}
    villages = overview.get("villages") or []
    if len(villages) != 1:
        return {"skip": "UNEXPECTED_VILLAGE_COUNT"}
    village = villages[0]
    if (player.get("name"), player.get("world"), village.get("coords")) != ACCOUNT:
        return {"skip": "ACCOUNT_PIN_MISMATCH"}
    if village.get("id") != 1:
        return {"skip": "VILLAGE_ID_MISMATCH"}
    synctime = parse_dt(village.get("synced_at"))
    if synctime is None:
        return {"skip": "NO_SYNC_TIME"}
    age = (now - synctime).total_seconds()
    if age < -60 or age > MAX_SNAPSHOT_AGE_SECONDS:
        return {"skip": "GAME_STATE_STALE", "snapshot_age_sec": round(age)}
    if (player.get("incomings") or 0) != 0:
        return {"skip": "INCOMING_ACTIVITY"}
    slots = [s for s in scavenge if s.get("village_id") == village["id"]]
    if len(slots) != 1:
        return {"skip": "SCAVENGE_VILLAGE_MISMATCH"}
    slot = slots[0]

    units = {u["name"]: u for u in village.get("units", [])}
    spear = units.get("spear", {})
    sword = units.get("sword", {})
    for u in (spear, sword):
        if not isinstance(u.get("home"), int) or not isinstance(u.get("away"), int):
            return {"skip": "UNKNOWN_TROOP_STATE"}
    home = {"spear": spear["home"], "sword": sword["home"]}
    plan_home = slot.get("units_home") or {}
    if any(plan_home.get(k, 0) != v for k, v in home.items()):
        return {"skip": "SCAVENGE_SYNC_CONFLICT"}
    if spear["away"] or sword["away"]:
        return {"skip": "SCAVENGE_IN_FLIGHT"}

    pending = state.get("pending") or {}
    if pending:
        pending_at = parse_dt(pending.get("at"))
        if pending_at is None:
            return {"skip": "UNPARSABLE_PENDING"}
        elapsed = (now - pending_at).total_seconds()
        if elapsed < UNCERTAIN_RETRY_SECONDS:
            return {"skip": "PENDING_ACTION_NOT_RETRIED", "age_sec": round(elapsed)}
        # After an unknown response, only a fresh later snapshot may unlock.
        if synctime <= pending_at:
            return {"skip": "UNCONFIRMED_ACTION_NO_FRESH_SYNC"}
        # If there was no confirmation for 30+ min, fail closed permanently.
        return {"skip": "UNCONFIRMED_ACTION_NEEDS_REVIEW"}

    last = parse_dt(state.get("last_sent_at"))
    if last is not None:
        elapsed = (now - last).total_seconds()
        if elapsed < MIN_RESEND_SECONDS:
            return {"skip": "RECENT_SUCCESS_COOLDOWN", "age_sec": round(elapsed)}
        if synctime <= last:
            return {"skip": "SENT_BUT_NO_NEW_GAME_SYNC"}
    tier = 2 if 2 in slot.get("free_options", []) else 1 if 1 in slot.get("free_options", []) else None
    if tier is None:
        return {"skip": "NO_FREE_SCAVENGE_TIER"}
    # At least 2 of each remain home, plus the untouched paladin.
    squad = {"spear": max(0, home["spear"] - 2),
             "sword": max(0, home["sword"] - 2)}
    squad = {k: v for k, v in squad.items() if v > 0}
    if sum(squad.values()) < MIN_SQUAD:
        return {"skip": "INSUFFICIENT_TROOPS_WITH_DEFENSE_RESERVE"}
    return {"send": {"option_id": tier, "units": squad,
                      "reason": "SK106 bezpečný automatický zber, ponechaná obranná rezerva"},
            "source_synced_at": village["synced_at"]}


def run_once() -> dict:
    now = utcnow()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        overview = get("/api/v1/game/overview")
        scavenge = get("/api/v1/game/scavenge-plan")
        decision = decide(overview, scavenge, state, now)
    except Exception as exc:
        result = {"at": iso(now), "status": "ERROR", "error_type": type(exc).__name__,
                  "error": str(exc)[:180]}
        state["last_result"] = result
        atomic_save(STATE, state)
        raise

    if "send" not in decision:
        result = {"at": iso(now), "status": "SKIPPED", **decision}
        state["last_result"] = result
        atomic_save(STATE, state)
        return result

    payload = {"village_id": 1, "tool": "send_scavenge",
               "arguments": decision["send"], "dry_run": False, "source": "api"}
    state["pending"] = {"at": iso(now), "source_synced_at": decision["source_synced_at"],
                        "option_id": payload["arguments"]["option_id"],
                        "units": payload["arguments"]["units"]}
    atomic_save(STATE, state)  # crash-safe no duplicate send
    try:
        response = post("/api/v1/agents/act", payload)
    except Exception as exc:
        result = {"at": iso(utcnow()), "status": "UNKNOWN_RESPONSE_NO_RETRY",
                  "error_type": type(exc).__name__, "error": str(exc)[:180]}
        state["last_result"] = result
        atomic_save(STATE, state)
        raise
    if response.get("ok") is True and response.get("dry_run") is False:
        state["last_sent_at"] = iso(now)
        state.pop("pending", None)
        result = {"at": iso(utcnow()), "status": "SENT",
                  "option_id": payload["arguments"]["option_id"],
                  "units": payload["arguments"]["units"],
                  "detail": response.get("detail", "")}
    else:
        # Ambiguous/refused game-state result: leave pending, fail closed.
        result = {"at": iso(utcnow()), "status": "NOT_CONFIRMED_NO_RETRY",
                  "detail": str(response.get("detail", ""))[:200]}
    state["last_result"] = result
    atomic_save(STATE, state)
    return result


def main():
    LOCK.touch(exist_ok=True)
    with LOCK.open("r+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"SKIP_CONCURRENT"}')
            return
        print(json.dumps(run_once(), ensure_ascii=False))


if __name__ == "__main__":
    main()
