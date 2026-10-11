#!/usr/bin/env python3
"""Atomic public-safe status export for SK106. No game actions."""
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(OUT)
    print("SK106_STATUS_EXPORTED", report["points"])

if __name__ == "__main__":
    main()
