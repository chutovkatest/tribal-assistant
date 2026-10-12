"""Incoming attacks: label format, travel times per unit and the slowest unit an arrival time allows."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tribal_assistant.core.agents.knowledge import UNITS

SEPARATOR = " · "
WATCHTOWER_TAG = "torre"
SIZE_WORDS = {"small": "pequeno", "medium": "médio", "large": "grande"}
SIZE_TROOPS = {"small": 1000, "medium": 5000, "large": 12000}
SIZE_HINTS = (
    ("small", ("small", "pequeno", "malý", "maly", "mal[iý]")),
    ("medium", ("medium", "médio", "medio", "stredný", "stredny", "střední")),
    ("large", ("large", "grande", "veľký", "velky", "velký")),
)
PROBES = (
    ("spy", "espião"),
    ("light", "CL"),
    ("heavy", "CP"),
    ("axe", "infantaria"),
    ("sword", "espadachim"),
    ("ram", "aríete"),
    ("snob", "nobre"),
)
_ORIGIN = re.compile(r"^(.*?)\s*\((\d{1,3}\|\d{1,3})\)$")
_COORDS = re.compile(r"(\d{1,3})\|(\d{1,3})")


def size_of(*texts: str) -> str | None:
    joined = " ".join(t.lower() for t in texts if t)
    for size, words in SIZE_HINTS:
        if any(word in joined for word in words):
            return size

    return None


def distance(a: str | None, b: str | None) -> float | None:
    first = _COORDS.search(a or "")
    second = _COORDS.search(b or "")
    if not first or not second:
        return None

    return math.hypot(int(first[1]) - int(second[1]), int(first[2]) - int(second[2]))


class IncomingLabel:
    """Label kept in the commands table: name · size · player (origin) · torre."""

    @staticmethod
    def compose(name: str, size: str | None = None, player: str | None = None, origin: str | None = None, watchtower: bool = False) -> str:
        parts = [" ".join(name.split()) or "Ataque"]
        if size in SIZE_WORDS:
            parts.append(SIZE_WORDS[size])

        if origin:
            parts.append(f"{player or '?'} ({origin})")

        if watchtower:
            parts.append(WATCHTOWER_TAG)

        return SEPARATOR.join(parts)[:255]

    @staticmethod
    def parse(label: str) -> dict[str, Any]:
        parts = [p.strip() for p in (label or "").split(SEPARATOR.strip()) if p.strip()]
        found: dict[str, Any] = {"name": parts[0] if parts else "", "size": None, "player": None, "origin": None, "watchtower": False}
        for part in parts[1:]:
            match = _ORIGIN.match(part)
            if match:
                found["player"] = None if match[1] in ("", "?") else match[1]
                found["origin"] = match[2]
            elif part == WATCHTOWER_TAG:
                found["watchtower"] = True
            else:
                found["size"] = next((k for k, v in SIZE_WORDS.items() if v == part), found["size"])

        return found


class TravelClock:
    """Minutes per field for each unit in this world; nobles take 7/6 of a ram."""

    def __init__(self, minutes_per_field: Mapping[str, float] | None = None) -> None:
        self.table = {u: float(info.speed) for u, info in UNITS.items()}
        self.table.update({u: float(v) for u, v in (minutes_per_field or {}).items() if v})
        if "ram" in self.table and not (minutes_per_field or {}).get("snob"):
            self.table["snob"] = self.table["ram"] * 7 / 6

    @classmethod
    def for_world(cls, units: Mapping[str, Any] | None = None, config: Mapping[str, Any] | None = None) -> "TravelClock":
        speeds = {u: float(v["speed"]) for u, v in (units or {}).items() if isinstance(v, Mapping) and v.get("speed")}
        if speeds:
            return cls(speeds)

        factor = float((config or {}).get("speed") or 1) * float((config or {}).get("unit_speed") or 1)
        return cls({u: info.speed / factor for u, info in UNITS.items()})

    def minutes(self, unit: str, fields: float) -> float:
        return fields * self.table.get(unit, UNITS["snob"].speed)

    def slowest(self, fields: float | None, remaining_minutes: float, tolerance: float = 1.0) -> str | None:
        """The fastest unit that still needs at least the time left when the attack was first seen."""
        if fields is None or fields <= 0:
            return None

        for unit, _ in sorted(PROBES, key=lambda p: self.table.get(p[0], 99)):
            if self.minutes(unit, fields) + tolerance >= remaining_minutes:
                return unit

        return None


def tag_for(unit: str | None, size: str | None = None, noble: bool = False) -> str:
    if noble or unit == "snob":
        return "nobre"

    if unit is None:
        return "desconhecido"

    tag = dict(PROBES)[unit]
    if size == "small" and unit == "ram":
        return "fake"

    return tag


@dataclass(frozen=True)
class ArmyGuess:
    units: dict[str, int]
    pop: int


def guess_army(unit: str | None, size: str | None, cap_pop: int | None) -> ArmyGuess | None:
    """Rough attacking army from the size icon and what the attacker can feed."""
    if size is None and not cap_pop:
        return None

    if unit == "spy":
        return ArmyGuess({"spy": 5}, 10)

    pop = min(v for v in (SIZE_TROOPS.get(size or "", 0) or None, cap_pop) if v)
    if unit in ("light", "heavy"):
        mix = {unit: 1.0}
    elif unit in ("ram", "snob"):
        mix = {"axe": 0.6, "light": 0.3, "ram": 0.1}
    else:
        mix = {"axe": 0.65, "light": 0.35}

    units = {u: int(pop * share / UNITS[u].pop) for u, share in mix.items()}
    return ArmyGuess({u: n for u, n in units.items() if n > 0}, pop)
