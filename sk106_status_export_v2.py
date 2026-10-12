#!/usr/bin/env python3
"""Atomic public-safe status export for SK106. No game actions."""
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from sk106_growth_scoreboard import compute as compute_growth

ROOT = Path("/home/worker/tribal-sk106-lab")
OUT = Path("/srv/chutovka/sk106/status.json")

def main():
    watch = json.loads((ROOT / "agy-watch-state.json").read_text())
    rush = json.loads((ROOT / "world-intel/lc-rush-latest.json").read_text())
    if rush.get("world") != "sk106" or rush.get("account") != "chlpatucky":
        raise ValueError("ACCOUNT_MISMATCH")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "world": "sk106",
        "account": "chlpatucky",
        "points": watch.get("points"),
        "watchdog_last_check": watch.get("last_check"),
        "last_build_result": watch.get("last_build_result"),
        "build_pending": watch.get("build_pending"),
        "last_error": watch.get("last_error"),
        "queue": watch.get("queue"),
        "light_cavalry_rush": {
            "mode": rush.get("mode"),
            "generated_at": rush.get("generated_at"),
            "source_synced_at": rush.get("source_synced_at"),
            "primary_manual_step": rush.get("primary_manual_step"),
            "levels": {k: rush.get("levels",{}).get(k)
                       for k in ("main","smith","stable","stone","iron")},
            "units": rush.get("units"),
            "resources": rush.get("resources"),
            "milestones": rush.get("milestones"),
        },
        "game_action_mode": (
            "AUTONOMOUS_BUILD_WATCHDOG_ACTIVE__OTHER_AGENTS_DRY_RUN"
            if subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                               "sk106-agy-watchdog.timer"], check=False).returncode == 0
            else "BUILD_WATCHDOG_INACTIVE"
        ),
        "lc_rush_target": watch.get("lc_rush_target"),
    }
    try:
        world = json.loads((ROOT / "world-intel/latest_summary.json").read_text())
        metric = compute_growth(rush, world)
        report["growth_scoreboard"] = {
            k: metric.get(k) for k in (
                "generated_at", "own_snapshot_at", "own_status",
                "world_observed_at", "world_status", "our_points",
                "veronika_points", "point_gap", "growth_observation_hours",
                "ours_points_per_hour_observed",
                "veronika_points_per_hour_observed", "relative_points_per_hour",
                "gap_trend", "hypothetical_catchup_hours_at_observed_rates",
                "next_priority", "next_build_resource_wait_min_estimate",
                "earliest_queue_completion", "construction_queue_empty",
                "ordinary_troops_home", "scavenging_opportunity")
        }
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        report["growth_scoreboard"] = {
            "status": "UNAVAILABLE", "error_type": type(exc).__name__}
    harvest = ROOT / "auto-scavenge-state.json"
    if harvest.exists():
        row = json.loads(harvest.read_text())
        report["auto_scavenge"] = {
            "last_sent_at": row.get("last_sent_at"),
            "last_result": row.get("last_result"),
            "pending": row.get("pending"),
            "module": "sk106_auto_scavenge.py"
        }
    else:
        report["auto_scavenge"] = {"status": "NO_STATE_YET"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(OUT)
    print("SK106_STATUS_EXPORTED", report["points"])

if __name__ == "__main__":
    main()
