"""Game state parser — pure functions of raw page data -> dataclasses.

The raw inputs are what the browser hands back from `window.game_data`,
screen globals (`BuildingMain`, `ScavengeScreen`) and a few DOM reads
(see `tribal_assistant.core.game.modules.game_sync`).
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from tribal_assistant.core.game.incoming import IncomingLabel, size_of
from tribal_assistant.core.game.locale_text import (has_protection_context, fold, relative_day, report_category, report_month)

SECONDS_PER_HOUR = 3600
SERVER_TZ = ZoneInfo("Europe/Bratislava")

_COORDS = re.compile(r"(\d{1,3}\|\d{1,3})")
_NUMBER = re.compile(r"(\d+)")
_DURATION = re.compile(r"(\d+):(\d{2}):(\d{2})")
_PROTECTION = re.compile(r"acaba em (\d{1,2})\.(\d{1,2})\.\s*às\s*(\d{1,2}):(\d{2}):(\d{2})")
_REPORT_DATE = re.compile(r"(\w{3})\.?\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})")
UNIT_REQUIREMENTS = {
    "sword": "Ferreiro (Nível 1)",
    "axe": "Ferreiro (Nível 2)",
    "archer": "Quartel (Nível 5), Ferreiro (Nível 5)",
    "spy": "Estábulo (Nível 1)",
    "light": "Estábulo (Nível 3)",
    "marcher": "Estábulo (Nível 5)",
    "heavy": "Estábulo (Nível 10), Ferreiro (Nível 15)",
    "ram": "Oficina (Nível 1)",
    "catapult": "Oficina (Nível 2), Ferreiro (Nível 12)",
    "knight": "Estátua (Nível 1)",
    "snob": "Academia (Nível 1)",
}
_MONTHS = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


@dataclass(frozen=True)
class PlayerSnapshot:
    game_id: str
    name: str
    world: str
    points: int
    rank: int
    villages: int
    incomings: int
    ally_id: str | None = None
    premium_points: int = 0
    new_reports: int = 0
    new_mails: int = 0
    new_quests: bool = False
    daily_bonus: bool = False
    protection_until: datetime | None = None


@dataclass(frozen=True)
class BuildingSnapshot:
    name: str
    level: int
    max_level: int | None = None
    next_level: int | None = None
    next_wood: int | None = None
    next_clay: int | None = None
    next_iron: int | None = None
    next_pop: int | None = None
    build_time: int | None = None
    can_build: bool = False
    blocker: str | None = None
    queued_level: int | None = None
    queued_until: datetime | None = None


@dataclass(frozen=True)
class UnitSnapshot:
    name: str
    home: int = 0
    total: int = 0
    available: bool = False
    max_recruit: int = 0
    cost_wood: int | None = None
    cost_clay: int | None = None
    cost_iron: int | None = None
    cost_pop: int | None = None
    build_time: int | None = None
    blocker: str | None = None


@dataclass(frozen=True)
class RecruitSnapshot:
    unit: str
    count: int
    finishes_at: datetime | None


@dataclass(frozen=True)
class CommandSnapshot:
    game_id: str | None
    direction: str
    kind: str
    label: str
    coords: str | None
    arrival_at: datetime
    origin_coords: str | None = None
    origin_player: str | None = None
    size: str | None = None
    watchtower: bool = False


@dataclass(frozen=True)
class ScavengeSnapshot:
    option_id: int
    name: str
    loot_factor: float
    is_locked: bool
    unlock_at: datetime | None
    return_at: datetime | None
    squad_json: str | None


@dataclass(frozen=True)
class GameVillage:
    game_id: str
    name: str
    coords: str
    points: int
    wood: int
    clay: int
    iron: int
    storage: int
    pop_current: int
    pop_max: int
    wood_prod: int
    clay_prod: int
    iron_prod: int
    buildings: tuple[BuildingSnapshot, ...] = ()
    units: tuple[UnitSnapshot, ...] = ()
    recruit_queue: tuple[RecruitSnapshot, ...] = ()
    commands: tuple[CommandSnapshot, ...] = ()
    scavenge: tuple[ScavengeSnapshot, ...] = ()


@dataclass(frozen=True)
class ReportSnapshot:
    game_id: str
    title: str
    category: str
    result: str | None
    is_new: bool
    received_at: datetime | None
    origin_coords: str | None = None
    target_coords: str | None = None
    loot_wood: int = 0
    loot_clay: int = 0
    loot_iron: int = 0
    haul_total: int | None = None


@dataclass(frozen=True)
class GameSnapshot:
    player: PlayerSnapshot
    villages: tuple[GameVillage, ...]
    reports: tuple[ReportSnapshot, ...] = ()


def to_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return int(value)
    digits = "".join(c for c in str(value) if c.isdigit())
    return int(digits) if digits else 0


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else to_int(value)


def _per_hour(per_second: Any) -> int:
    try:
        return round(float(per_second) * SECONDS_PER_HOUR)
    except (TypeError, ValueError):
        return 0


def _from_epoch(value: Any) -> datetime | None:
    seconds = to_int(value)
    if seconds > 10**11:
        seconds //= 1000
    return datetime.fromtimestamp(seconds, UTC) if seconds else None


def parse_duration(text: Any) -> int | None:
    """"0:05:03" -> 303 seconds."""
    match = _DURATION.search(str(text or ""))
    if not match:
        return None
    h, m, s = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s


def _server_time(now: datetime, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    """Server-local date without a year -> UTC, picking the year closest to `now`."""
    local_now = now.astimezone(SERVER_TZ)
    candidates = [
        datetime(local_now.year + delta, month, day, hour, minute, second, tzinfo=SERVER_TZ)
        for delta in (-1, 0, 1)
    ]
    best = min(candidates, key=lambda c: abs(c - local_now))
    return best.astimezone(UTC)


def parse_protection(text: str, now: datetime | None = None) -> datetime | None:
    """Read a newcomer protection expiry only when it is near an explicit protection label."""
    text = text or ""
    # SK/CZ/PT/EN date forms: "13.10. o 20:38:21" / "03.10. às 20:38:21".
    pattern = re.compile(
        r"(?<!\d)(\d{1,2})\.(\d{1,2})\.?\s*(?:(\d{4})\s*)?(?:(?:o|as|às|at)\s*)?"
        r"(\d{1,2}):(\d{2})(?::(\d{2}))?", re.I)
    for match in pattern.finditer(text):
        context = fold(text[max(0,match.start()-160):match.start()])
        if not has_protection_context(context):
            continue
        if not any(k in context for k in ("konci", "skonci", "trva", "uplynie", "acaba", "ends", "until", "expires", "protecao de iniciante")):
            continue
        day,month,year,h,m,second=match.groups()
        try:
            if year:
                return datetime(int(year),int(month),int(day),int(h),int(m),
                                int(second or 0),tzinfo=SERVER_TZ).astimezone(UTC)
            return _server_time(now or datetime.now(UTC),int(month),int(day),
                                int(h),int(m),int(second or 0))
        except ValueError:
            return None
    return None


def parse_report_date(text: str, now: datetime | None = None) -> datetime | None:
    """Localized list labels: dnes/včera, hoje/ontem, numeric dd.mm., month names."""
    now = now or datetime.now(UTC)
    text = (text or "").strip()
    clock = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if not clock:
        return None
    h, minute, sec=(int(clock.group(1)),int(clock.group(2)),int(clock.group(3) or 0))
    day_offset=relative_day(text)
    if day_offset is not None:
        local=now.astimezone(SERVER_TZ)+timedelta(days=day_offset)
        return _server_time(now,local.month,local.day,h,minute,sec)
    date=re.search(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.?(\d{4})?",text)
    if date:
        day,month,year=date.groups()
        try:
            if year:
                return datetime(int(year),int(month),int(day),h,minute,sec,
                                tzinfo=SERVER_TZ).astimezone(UTC)
            return _server_time(now,int(month),int(day),h,minute,sec)
        except ValueError:
            return None
    date=re.search(r"([^\W\d_]{3,12})\.?\s+(\d{1,2}),?\s*(?:o|at|às|as)?\s*(\d{1,2}):(\d{2})",text,re.I)
    if date:
        month=report_month(date.group(1))
        if month:
            try:
                return _server_time(now,month,int(date.group(2)),int(date.group(3)),
                                    int(date.group(4)),0)
            except ValueError:
                return None
    return None

def parse_player(game_data: Mapping[str, Any], overview_text: str = "") -> PlayerSnapshot:
    player = game_data.get("player") or {}
    ally = str(player.get("ally") or "0")
    return PlayerSnapshot(
        game_id=str(player.get("id", "")),
        name=str(player.get("name", "")),
        world=str(game_data.get("world", "")),
        points=to_int(player.get("points")),
        rank=to_int(player.get("rank")),
        villages=to_int(player.get("villages")),
        incomings=to_int(player.get("incomings")),
        ally_id=None if ally == "0" else ally,
        premium_points=to_int(player.get("pp")),
        new_reports=to_int(player.get("new_report")),
        new_mails=to_int(player.get("new_igm")),
        new_quests=to_int(player.get("new_quest")) > 0,
        daily_bonus=to_int(player.get("new_daily_bonus")) > 0,
        protection_until=parse_protection(overview_text),
    )


def parse_queue(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int, datetime | None]]:
    """Build queue rows -> {building: (highest queued level, finish time)}."""
    queue: dict[str, tuple[int, datetime | None]] = {}
    for row in rows:
        building = str(row.get("building") or "")
        if not building:
            continue
        numbers = _NUMBER.findall(str(row.get("text") or ""))
        level = int(numbers[-1]) if numbers else 0
        until = _from_epoch(row.get("end")) or parse_report_date(str(row.get("done") or ""))
        previous = queue.get(building)
        if previous is None or level > previous[0]:
            queue[building] = (level, until)
    return queue


def parse_buildings(
    levels: Mapping[str, Any],
    upgrades: Mapping[str, Mapping[str, Any]],
    queue: Mapping[str, tuple[int, datetime | None]],
) -> tuple[BuildingSnapshot, ...]:
    """Merge current levels with the next-level info from the headquarters screen.

    `upgrades` only lists buildings whose requirements are met; the rest are
    marked as blocked.
    """
    result = []
    for name, raw_level in levels.items():
        info = upgrades.get(name)
        queued_level, queued_until = queue.get(name, (None, None))
        if info is None:
            result.append(
                BuildingSnapshot(
                    name=name,
                    level=to_int(raw_level),
                    blocker="requisitos não atendidos",
                    queued_level=queued_level,
                    queued_until=queued_until,
                )
            )
            continue
        error = info.get("error")
        max_level = to_int(info.get("max_level")) or None
        level = to_int(raw_level)
        maxed = max_level is not None and level >= max_level
        result.append(
            BuildingSnapshot(
                name=name,
                level=level,
                max_level=max_level,
                next_level=None if maxed else to_int(info.get("level_next")) or level + 1,
                next_wood=None if maxed else to_int(info.get("wood")),
                next_clay=None if maxed else to_int(info.get("stone")),
                next_iron=None if maxed else to_int(info.get("iron")),
                next_pop=None if maxed else to_int(info.get("pop")),
                build_time=None if maxed else to_int(info.get("build_time")),
                can_build=bool(info.get("can_build")) and not maxed,
                blocker="nível máximo" if maxed else (str(error) if error else None),
                queued_level=queued_level,
                queued_until=queued_until,
            )
        )
    return tuple(result)


def parse_units(
    rows: Sequence[Mapping[str, Any]], home_counts: Mapping[str, int] | None = None
) -> tuple[UnitSnapshot, ...]:
    """Rows from the recruitment screen (one per world unit).

    `home_counts` comes from the rally point, which also counts units whose
    recruitment is currently locked (the recruitment screen hides those).
    """
    home_counts = home_counts or {}
    result = []
    for row in rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        available = bool(row.get("available"))
        home = home_counts.get(name, to_int(row.get("home")))
        result.append(
            UnitSnapshot(
                name=name,
                home=home,
                total=max(to_int(row.get("total")), home),
                available=available,
                max_recruit=to_int(row.get("max")),
                cost_wood=_optional_int(row.get("wood")),
                cost_clay=_optional_int(row.get("stone")),
                cost_iron=_optional_int(row.get("iron")),
                cost_pop=_optional_int(row.get("pop")),
                build_time=parse_duration(row.get("time")),
                blocker=None
                if available
                else str(row.get("requirements") or "")
                or UNIT_REQUIREMENTS.get(name, "indisponível"),
            )
        )
    return tuple(result)


def parse_home_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Rally point inputs -> {unit: count at home}."""
    return {str(r["name"]): to_int(r.get("count")) for r in rows if r.get("name")}


def parse_recruit_queue(rows: Sequence[Mapping[str, Any]]) -> tuple[RecruitSnapshot, ...]:
    result = []
    for row in rows:
        unit = str(row.get("unit") or "")
        if not unit:
            continue
        numbers = _NUMBER.findall(str(row.get("text") or ""))
        result.append(
            RecruitSnapshot(
                unit=unit,
                count=int(numbers[0]) if numbers else 0,
                finishes_at=_from_epoch(row.get("end")),
            )
        )
    return tuple(result)


def _command_kind(icon: str) -> str:
    icon = icon.lower()
    for key, kind in (
        ("return", "return"),
        ("back", "return"),
        ("cancel", "cancel"),
        ("snob", "noble"),
        ("support", "support"),
        ("attack", "attack"),
    ):
        if key in icon:
            return kind
    return "other"


def parse_commands(rows: Sequence[Mapping[str, Any]]) -> tuple[CommandSnapshot, ...]:
    result = []
    for row in rows:
        arrival = _from_epoch(row.get("end"))
        if arrival is None:
            continue
        text = " ".join(str(row.get("text") or "").split())
        coords = _COORDS.findall(text)
        incoming = row.get("direction") == "in"
        origin_found = _COORDS.findall(str(row.get("origin") or ""))
        origin = origin_found[-1] if origin_found else None
        player = " ".join(str(row.get("player") or "").split()) or None
        size = size_of(str(row.get("icon") or ""), str(row.get("hint") or ""))
        watchtower = bool(row.get("watchtower"))
        label = IncomingLabel.compose(text, size, player, origin, watchtower) if incoming and (size or origin or watchtower) else text
        result.append(
            CommandSnapshot(
                game_id=str(row["id"]) if row.get("id") else None,
                direction="in" if incoming else "out",
                kind=_command_kind(str(row.get("icon") or "") + " " + str(row.get("type") or "")),
                label=label[:255],
                coords=origin if incoming and origin else (coords[-1] if coords else None),
                arrival_at=arrival,
                origin_coords=origin if incoming else None,
                origin_player=player if incoming else None,
                size=size if incoming else None,
                watchtower=watchtower,
            )
        )
    return tuple(result)


_RETURN_KEYS = ("return_time", "finish_time", "end_time", "returns_at")


def parse_scavenge(rows: Sequence[Mapping[str, Any]]) -> tuple[ScavengeSnapshot, ...]:
    result = []
    for row in rows:
        squad = row.get("squad") or None
        return_at = None
        if isinstance(squad, Mapping):
            for key in _RETURN_KEYS:
                if squad.get(key):
                    return_at = _from_epoch(squad[key])
                    break
        result.append(
            ScavengeSnapshot(
                option_id=to_int(row.get("id")),
                name=str(row.get("name") or ""),
                loot_factor=float(row.get("loot_factor") or 0),
                is_locked=bool(row.get("locked")),
                unlock_at=_from_epoch(row.get("unlock_time")),
                return_at=return_at,
                squad_json=json.dumps(squad, ensure_ascii=False) if squad else None,
            )
        )
    return tuple(result)


def _report_category(title: str) -> str:
    return report_category(title)

def _report_result(icons: str) -> str | None:
    for color in ("green", "yellow", "red", "blue"):
        if f"/{color}." in icons or f"dots/{color}" in icons:
            return color
    return None


def parse_reports(
    rows: Sequence[Mapping[str, Any]],
    details: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> tuple[ReportSnapshot, ...]:
    details = details or {}
    result = []
    for row in rows:
        game_id = str(row.get("id") or "")
        if not game_id:
            continue
        title = " ".join(str(row.get("title") or "").split())
        detail = details.get(game_id) or {}
        attacker = _COORDS.findall(str(detail.get("attacker") or ""))
        defender = _COORDS.findall(str(detail.get("defender") or ""))
        named = _COORDS.findall(title)
        if not attacker and not defender and len(named) >= 2:
            attacker, defender = named[:1], named[-1:]
        loot = [to_int(n) for n in detail.get("loot") or []]
        haul = _NUMBER.findall(str(detail.get("haul") or ""))
        result.append(
            ReportSnapshot(
                game_id=game_id,
                title=title[:255],
                category=_report_category(title),
                result=_report_result(str(row.get("icons") or "")),
                is_new=bool(row.get("is_new")),
                received_at=parse_report_date(str(row.get("received") or ""), now),
                origin_coords=attacker[-1] if attacker else None,
                target_coords=defender[-1] if defender else None,
                loot_wood=loot[0] if len(loot) > 0 else 0,
                loot_clay=loot[1] if len(loot) > 1 else 0,
                loot_iron=loot[2] if len(loot) > 2 else 0,
                haul_total=int(haul[-1]) if haul else None,
            )
        )
    return tuple(result)


def parse_village(
    game_data: Mapping[str, Any],
    upgrades: Mapping[str, Mapping[str, Any]],
    queue_rows: Sequence[Mapping[str, Any]],
    unit_rows: Sequence[Mapping[str, Any]],
    home_rows: Sequence[Mapping[str, Any]] = (),
    recruit_rows: Sequence[Mapping[str, Any]] = (),
    command_rows: Sequence[Mapping[str, Any]] = (),
    scavenge_rows: Sequence[Mapping[str, Any]] = (),
) -> GameVillage:
    village = game_data.get("village") or {}
    return GameVillage(
        game_id=str(village.get("id", "")),
        name=str(village.get("name", "")),
        coords=str(village.get("coord") or f"{village.get('x', 0)}|{village.get('y', 0)}"),
        points=to_int(village.get("points")),
        wood=to_int(village.get("wood")),
        clay=to_int(village.get("stone")),
        iron=to_int(village.get("iron")),
        storage=to_int(village.get("storage_max")),
        pop_current=to_int(village.get("pop")),
        pop_max=to_int(village.get("pop_max")),
        wood_prod=_per_hour(village.get("wood_prod")),
        clay_prod=_per_hour(village.get("stone_prod")),
        iron_prod=_per_hour(village.get("iron_prod")),
        buildings=parse_buildings(
            village.get("buildings") or {}, upgrades, parse_queue(queue_rows)
        ),
        units=parse_units(unit_rows, parse_home_counts(home_rows)),
        recruit_queue=parse_recruit_queue(recruit_rows),
        commands=parse_commands(command_rows),
        scavenge=parse_scavenge(scavenge_rows),
    )
