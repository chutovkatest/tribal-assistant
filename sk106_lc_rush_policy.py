"""Deterministic SK106 Light Cavalry Rush milestone selector.

Never sends game actions itself. Called by the existing pinned single-writer
watchdog after account/world/freshness validation. An unaffordable milestone
blocks wasteful fallback builds.
"""
MILESTONES = (("smith", 4), ("main", 9), ("smith", 5),
              ("main", 10), ("stable", 1), ("stable", 2), ("stable", 3))


def lc_rush_candidates(village, affordable):
    """Return (active, candidates, target). Active means do not fall back."""
    buildings = {b["name"]: b for b in village.get("buildings", [])}
    if not all(k in buildings for k in ("smith", "main", "stable")):
        # Legacy unit fixtures represent only a subset; leave the old
        # policy in charge. Production snapshots are checked in safe_view.
        return False, [], "CORE_BUILDINGS_MISSING"
    for name, level in MILESTONES:
        b = buildings[name]
        current = max(int(b.get("level") or 0), int(b.get("queued_level") or 0))
        if current >= level:
            continue
        if b.get("next_level") == level and name in affordable and not b.get("queued_level"):
            return True, [name], f"{name}:{level}"
        return True, [], f"WAIT_FOR_{name}:{level}"
    return False, [], "LC_MILESTONES_READY_CHECK_RESEARCH"
