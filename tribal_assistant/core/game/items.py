"""Inventory item counts read from the detail panel ("Da propriedade de: N")."""

import re

OWNED = re.compile(r"(?:Da propriedade de|Vlastn[ií]ctvo|Vlastn[ií]te|Po[cč]et|Owned|Mno[zž]stvo):\s*(\d+)", re.I)


class ItemCount:
    @staticmethod
    def of(text: str) -> int:
        found = OWNED.search(text or "")
        return int(found.group(1)) if found else 1

    @staticmethod
    def consumed(before: int, after: int) -> bool:
        return after < before
