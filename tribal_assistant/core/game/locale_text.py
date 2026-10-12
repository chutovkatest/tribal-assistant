"""Language-independent interpretation of Tribal Wars human UI text.

Persistent API identifiers remain English. Human-facing UI on SK106 is Slovak.
We also accept historic pt-BR and common cs/en texts in read-only parsers.
"""
from __future__ import annotations

import re
import unicodedata


def fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").casefold())
    return "".join(c for c in value if not unicodedata.combining(c))


_REPORT_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scout", ("speh", "spion", "prieskum", "vyzved", "espi", "explorador", "scout", "spy")),
    ("attack", ("utok", "utoc", "napad", "atac", "ataqu", "attack", "angriff", "raid")),
    ("support", ("podpor", "posil", "apoi", "support", "unterstutz")),
    ("achievement", ("uspech", "ocenen", "vyznamen", "realiza", "achievement")),
    ("trade", ("obchod", "trhov", "vymen", "poslan", "comerc", "envia", "mercado", "trade")),
)


def report_category(title: str) -> str:
    value = fold(title)
    for category, words in _REPORT_TOKENS:
        if any(word in value for word in words):
            return category
    return "other"


def is_battle_report(title: str) -> bool:
    return report_category(title) in ("attack", "scout")


def is_loot_label(text: str) -> bool:
    value = fold(text)
    return any(word in value for word in ("korist", "ukoristen", "lup", "saque", "loot", "plunder", "rabov"))


def has_protection_context(text: str) -> bool:
    value = fold(text)
    return any(word in value for word in ("ochran", "zaciatocnik", "novacik", "protec", "newbie", "beginner"))


def report_month(token: str) -> int | None:
    """All 12 calendar months in sk/cs/pt/en without locale switches."""
    names = {
        "jan": 1, "led": 1, "feb": 2, "fev": 2, "uno": 2,
        "mar": 3, "bre": 3, "apr": 4, "abr": 4, "dub": 4,
        "maj": 5, "kve": 5, "may": 5, "jun": 6, "cve": 6, "cvn": 6,
        "jul": 7, "cvc": 7, "aug": 8, "ago": 8, "srp": 8,
        "sep": 9, "set": 9, "zar": 9, "oct": 10, "okt": 10, "out": 10, "rij": 10,
        "nov": 11, "lis": 11, "dec": 12, "dez": 12, "pro": 12,
    }
    return names.get(fold(token)[:3])


def relative_day(text: str) -> int | None:
    value = fold(text)
    for label, delta in (
        ("vcera", -1), ("yesterday", -1), ("ontem", -1),
        ("zajtra", 1), ("zitra", 1), ("tomorrow", 1), ("amanha", 1),
        ("dnes", 0), ("today", 0), ("hoje", 0),
    ):
        if re.search(r"\b" + label + r"\b", value):
            return delta
    return None
