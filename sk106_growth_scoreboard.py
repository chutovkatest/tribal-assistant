#!/usr/bin/env python3
"""Read-only SK106 growth scoreboard with evidence timestamps.

Inputs are previously captured private game data and public world exports.
Produces no game commands and makes no HTTP requests.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path("/home/worker/tribal-sk106-lab")
OUT=ROOT/"world-intel"/"growth-scoreboard-latest.json"
PUBLIC_PLAYER_ID=1054384
OWN_PLAYER_ID=1086181
RES=("wood","clay","iron")
PROD=("wood_prod","clay_prod","iron_prod")

def dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc)
    except (ValueError,AttributeError):
        return None

def age_minutes(now, stamp):
    moment=dt(stamp)
    return round((now-moment).total_seconds()/60, 1) if moment else None

def wait_minutes(resources, production, cost):
    mins=[]
    for r,p in zip(RES,PROD):
        required=cost.get(r)
        if not isinstance(required,int) or required<0:
            return None
        lacking=max(0,required-resources.get(r,0))
        rate=production.get(p,0)
        if not isinstance(rate,(int,float)) or rate<0 or (lacking and rate<=0):
            return None
        mins.append(lacking/rate*60 if lacking else 0)
    return int(math.ceil(max(mins)))

def compute(advisor, world, now=None):
    now=now or datetime.now(timezone.utc)
    if advisor.get("world")!="sk106" or advisor.get("account")!="chlpatucky":
        raise RuntimeError("PIN_MISMATCH")
    assert isinstance(advisor.get("points"),int)
    focus={r.get("player_id"):r for r in world.get("players_of_interest",[])
           if isinstance(r,dict)}
    self_public=focus.get(OWN_PLAYER_ID)
    rival=focus.get(PUBLIC_PLAYER_ID)
    world_age=age_minutes(now,world.get("observed_at"))
    private_age=age_minutes(now,advisor.get("source_synced_at"))
    if private_age is None or private_age< -2 or private_age>20:
        private_status="STALE"
    else:
        private_status="CURRENT"
    if world_age is None or world_age< -2 or world_age>90:
        world_status="STALE"
    else:
        world_status="CURRENT"
    competitor_points=rival.get("points") if rival and world_status=="CURRENT" else None
    ours=advisor["points"] if private_status=="CURRENT" else None
    gap=competitor_points-ours if competitor_points is not None and ours is not None else None
    interval=0
    at=dt(world.get("observed_at"))
    before=dt(world.get("previous_at"))
    if at and before and at>before:
        interval=(at-before).total_seconds()/3600
    def growth(entry):
        d=entry.get("delta_points") if entry else None
        return round(d/interval,2) if isinstance(d,(int,float)) and 0.1<=interval<=24 and world_status=="CURRENT" else None
    own_growth=growth(self_public)
    rival_growth=growth(rival)
    relative=round(own_growth-rival_growth,2) if own_growth is not None and rival_growth is not None else None
    if gap is None or relative is None:
        trend="INSUFFICIENT_EVIDENCE"
        catchup_hours=None
    elif relative<=0:
        trend="LOSING_GROUND_OR_FLAT"
        catchup_hours=None
    else:
        trend="CLOSING_GAP"
        catchup_hours=round(max(0,gap)/relative,1)
    milestones=advisor.get("milestones",[])
    primary=next((x for x in milestones if x.get("status") in ("saving","affordable")),None)
    # Existing milestone order is strategy-specific; don't silently re-rank.
    if primary is None:
        primary=next((x for x in milestones if x.get("status")=="locked_or_preceding_level"),None)
    res=advisor.get("resources",{})
    production={p:advisor.get("production_per_hour",{}).get(r) for p,r in zip(PROD,RES)}
    eta=wait_minutes(res,production,primary["cost"]) if primary and primary.get("cost") else None
    queue=advisor.get("queue",[])
    queue_due=min((x.get("until") for x in queue if x.get("until")),default=None)
    home={k:info.get("home",0) for k,info in advisor.get("units",{}).items()}
    ordinary_home=sum(v for k,v in home.items() if k!="knight")
    building_idle=not queue
    return {
        "mode":"READ_ONLY_SCOREBOARD",
        "generated_at":now.isoformat(),
        "own_snapshot_at":advisor.get("source_synced_at"),
        "own_snapshot_age_minutes":private_age,
        "own_status":private_status,
        "world_observed_at":world.get("observed_at"),
        "world_age_minutes":world_age,
        "world_status":world_status,
        "our_points":ours,
        "our_rank":advisor.get("rank") if ours is not None else None,
        "veronika_points":competitor_points,
        "point_gap":gap,
        "growth_observation_hours":round(interval,2) if interval else None,
        "ours_points_per_hour_observed":own_growth,
        "veronika_points_per_hour_observed":rival_growth,
        "relative_points_per_hour":relative,
        "gap_trend":trend,
        "hypothetical_catchup_hours_at_observed_rates":catchup_hours,
        "next_priority":primary,
        "next_build_resource_wait_min_estimate":eta,
        "queue":queue,
        "earliest_queue_completion":queue_due,
        "construction_queue_empty":building_idle,
        "ordinary_troops_home":ordinary_home,
        "scavenging_opportunity":"TROOPS_AT_HOME" if ordinary_home else "NONE_AT_HOME",
        "caveats":[
            "Bodové prírastky verejného exportu zahŕňajú iba dve pozorovania, nie dlhodobý trend.",
            "Príjem z farmenia, questov a zberu nie je zahrnutý v ETA surovín.",
            "Chýbajúce či zastarané údaje konkurenta sa nezobrazia ako aktuálne.",
            "Tento modul nevykonáva žiadne herné akcie."
        ]
    }

def main():
    advisor=json.loads((ROOT/"world-intel"/"lc-rush-latest.json").read_text())
    world=json.loads((ROOT/"world-intel"/"latest_summary.json").read_text())
    result=compute(advisor,world)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    tmp.replace(OUT)
    print(json.dumps({k:result[k] for k in ("our_points","veronika_points","point_gap","gap_trend","next_build_resource_wait_min_estimate","scavenging_opportunity")},ensure_ascii=False))

if __name__=="__main__":
    main()
