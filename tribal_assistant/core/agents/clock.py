"""The game clock the agents read: what time it is on the server and how far each moment is."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from tribal_assistant.core.game.scraper.game import SERVER_TZ

GAME_CLOCK = re.compile(r"(dnes|zajtra|zítra|hoje|amanhã|today|tomorrow)\s*(?:o|às|at)?\s*(\d{1,2}):(\d{2})", re.I)


class Clock:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime.now(UTC)

    @staticmethod
    def aware(value: Any) -> datetime | None:
        if isinstance(value, str) and value:
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return None

        if not isinstance(value, datetime):
            return None

        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def span(seconds: float) -> str:
        minutes = round(abs(seconds) / 60)
        if minutes < 60:
            return f"{minutes} min"

        hours, rest = divmod(minutes, 60)
        if hours < 48:
            return f"{hours}h{rest:02d}"

        return f"{hours // 24}d{hours % 24}h"

    def relative(self, value: Any) -> str:
        moment = self.aware(value)
        if moment is None:
            return ""

        seconds = (moment - self.now).total_seconds()
        if abs(seconds) < 60:
            return "agora"

        return f"em {self.span(seconds)}" if seconds > 0 else f"há {self.span(seconds)}"

    def local(self, value: Any) -> str:
        moment = self.aware(value)
        if moment is None:
            return ""

        local = moment.astimezone(SERVER_TZ)
        days = (local.date() - self.now.astimezone(SERVER_TZ).date()).days
        prefix = {0: "", 1: "amanhã ", -1: "ontem "}.get(days, f"{local:%d/%m} ")
        return f"{prefix}{local:%H:%M}"

    def when(self, value: Any) -> str:
        moment = self.aware(value)
        if moment is None:
            return ""

        return f"{self.local(moment)} ({self.relative(moment)})"

    def header(self, synced_at: Any = None, protection_until: Any = None) -> str:
        parts = [f"Agora: {self.now.astimezone(SERVER_TZ):%d/%m %H:%M} (hora do servidor)"]

        if self.aware(synced_at):
            parts.append(f"estado lido do jogo {self.relative(synced_at)}")

        protection = self.aware(protection_until)
        if protection and protection > self.now:
            parts.append(f"proteção de iniciante até {self.when(protection)}")

        return " · ".join(parts)

    def annotate(self, text: str) -> str:
        """"Recursos disponíveis hoje às 16:36" gains how far that is: "(em 40 min)"."""
        match = GAME_CLOCK.search(text or "")
        if not match:
            return text

        day = self.now.astimezone(SERVER_TZ).date() + timedelta(days=1 if match[1].casefold() in ("amanhã", "zajtra", "zítra", "tomorrow") else 0)
        moment = datetime(day.year, day.month, day.day, int(match[2]), int(match[3]), tzinfo=SERVER_TZ)
        return f"{text} ({self.relative(moment)})"
