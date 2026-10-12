"""Attack and spy report detail: scouted resources, buildings (the wall above all) and troops on both sides."""

import json
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

RESOURCES = {"wood": "wood", "stone": "clay", "clay": "clay", "iron": "iron"}
ORDER = ("wood", "clay", "iron")
BUILDING_LABELS = {
    "edifício principal": "main",
    "quartel": "barracks",
    "estábulo": "stable",
    "oficina": "garage",
    "ferreiro": "smith",
    "academia": "snob",
    "mercado": "market",
    "praça de reunião": "place",
    "estátua": "statue",
    "igreja": "church",
    "torre de vigia": "watchtower",
    "bosque": "wood",
    "poço de argila": "stone",
    "mina de ferro": "iron",
    "fazenda": "farm",
    "armazém": "storage",
    "esconderijo": "hide",
    "muralha": "wall",
    "hlavná budova": "main", "hlavní budova": "main",
    "kasárne": "barracks", "kasárna": "barracks",
    "stajne": "stable", "stáje": "stable",
    "dielňa": "garage", "dílna": "garage",
    "kováčska dielňa": "smith", "kovárna": "smith",
    "panský dvor": "snob", "panský dvůr": "snob",
    "trhovisko": "market", "tržiště": "market",
    "nádvorie": "place", "nádvoří": "place",
    "socha": "statue", "kostol": "church", "kostel": "church",
    "strážna veža": "watchtower", "strážní věž": "watchtower",
    "drevorubač": "wood", "dřevorubec": "wood",
    "hlinisko": "stone", "lom na hlinu": "stone",
    "železná baňa": "iron", "železný důl": "iron",
    "selský dvor": "farm", "selský dvůr": "farm",
    "skladisko": "storage", "skladiště": "storage",
    "úkryt": "hide", "skrýš": "hide",
    "hradby": "wall", "hradba": "wall",
}
BUILDING_IMAGE = re.compile(r"buildings/(?:mid/)?([a-z_]+?)\d*\.(?:png|webp|gif)")
UNIT_CLASS = re.compile(r"^unit-item-([a-z_]+)$")
NUMBER = re.compile(r"\d[\d.]*")


class SpyReportParser:
    """Turns the report sections the sync captured into plain numbers; missing parts stay out of the result."""

    @staticmethod
    def number(text: str) -> int:
        found = NUMBER.search(text or "")
        return int(found.group().replace(".", "")) if found else 0

    @classmethod
    def parse(cls, html: str) -> dict[str, Any]:
        if not html:
            return {}

        soup = BeautifulSoup(html, "lxml")
        result: dict[str, Any] = {}

        scouted = cls.resources(soup)
        if scouted is not None:
            result["scouted"] = scouted

        buildings = cls.buildings(soup)
        if buildings:
            result["buildings"] = buildings
            result["wall"] = buildings.get("wall", 0)

        for side, key in (("att", "attacker"), ("def", "defender")):
            table = soup.select_one(f"#attack_info_{side}_units")
            if table is not None:
                result[f"{key}_units"], result[f"{key}_losses"] = cls.units(table)

        return result

    @classmethod
    def resources(cls, soup: BeautifulSoup) -> dict[str, int] | None:
        box = soup.select_one("#attack_spy_resources")
        if box is None:
            return None

        found: dict[str, int] = {}
        for index, item in enumerate(box.select(".nowrap")):
            icon = item.select_one(".icon")
            names = [RESOURCES[c] for c in (icon.get("class") or []) if c in RESOURCES] if icon else []
            name = names[0] if names else ORDER[index] if index < len(ORDER) else None
            if name:
                found[name] = cls.number(item.get_text(" ", strip=True))

        if not found and re.search(r"nenhum|žiadne|žádné|none", box.get_text(" ", strip=True), re.IGNORECASE):
            return dict.fromkeys(ORDER, 0)

        return {name: found.get(name, 0) for name in ORDER} if found else None

    @classmethod
    def buildings(cls, soup: BeautifulSoup) -> dict[str, int]:
        data = soup.select_one("#attack_spy_building_data")
        if data is not None and data.get("value"):
            try:
                rows = json.loads(str(data["value"]))
                return {str(r["id"]): int(r.get("level") or 0) for r in rows if r.get("id")}
            except (ValueError, TypeError, KeyError):
                pass

        levels: dict[str, int] = {}
        for row in soup.select("[id^=attack_spy_buildings] tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            name = cls.building_name(cells[0])
            if name:
                levels[name] = cls.number(cells[-1].get_text(" ", strip=True))

        return levels

    @staticmethod
    def building_name(cell: Tag) -> str | None:
        image = cell.find("img")
        if image is not None:
            match = BUILDING_IMAGE.search(str(image.get("src") or ""))
            if match:
                return match.group(1)

        label = " ".join(cell.get_text(" ", strip=True).lower().split())
        return BUILDING_LABELS.get(label)

    @classmethod
    def units(cls, table: Tag) -> tuple[dict[str, int], dict[str, int]]:
        rows = []
        for row in table.find_all("tr"):
            counts = {}
            for cell in row.find_all("td"):
                names = [m.group(1) for c in (cell.get("class") or []) if (m := UNIT_CLASS.match(c))]
                text = cell.get_text(" ", strip=True)
                if names and NUMBER.fullmatch(text or "-"):
                    counts[names[0]] = cls.number(text)
            if counts:
                rows.append(counts)

        sent = {u: n for u, n in (rows[0] if rows else {}).items() if n}
        lost = {u: n for u, n in (rows[1] if len(rows) > 1 else {}).items() if n}
        return sent, lost
