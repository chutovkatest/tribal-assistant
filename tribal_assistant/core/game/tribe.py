"""Inside the tribe: overview, members and forum read for lessons, and one reply in a forum thread."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from tribal_assistant.core.game.human import human_click, human_delay, human_fill
from tribal_assistant.core.game.result import ActionResult
from tribal_assistant.core.game.scraper.social import TribeParser
from tribal_assistant.core.game.session import game_session

if TYPE_CHECKING:
    from tribal_assistant.core.game.actions import GameActions

MODES = ("overview", "members", "forum")
FORUM_FIELD = 'textarea[name="message"]:visible, form textarea:visible'
FORUM_OPEN = '#content_value a:has-text("Responder"), #content_value a:has-text("Odpovedať"), #content_value a:has-text("Odpovědět"), #content_value a:has-text("Reply"), #content_value a[href*="answer=true"]'
SUBMIT = 'input[type="submit"], button[type="submit"]'


class TribeHall:
    def __init__(self, actions: GameActions) -> None:
        self.actions = actions

    async def read(self, village_id: str, forums: int) -> dict[str, Any]:
        found: dict[str, Any] = {"overview": "", "members": [], "threads": []}
        async with game_session.lock:
            for mode in MODES:
                page = await self.actions._in_game(village_id, "ally", mode=mode)
                html = await page.content()
                self.actions._capture(html, f"tribe_{mode}")
                if mode == "overview":
                    found["overview"] = TribeParser.content(html)
                elif mode == "members":
                    found["members"] = TribeParser.members(html)
                else:
                    found["threads"] = TribeParser.threads(html)
                    for forum_id in TribeParser.forums(html)[:forums]:
                        page = await self.actions._in_game(village_id, "forum", screenmode="view_forum", forum_id=forum_id)
                        inner = await page.content()
                        self.actions._capture(inner, "tribe_subforum")
                        found["threads"] += [t for t in TribeParser.threads(inner) if t["id"] not in {x["id"] for x in found["threads"]}]
        return found

    async def reply(self, village_id: str, thread_id: str, forum_id: str | None, text: str) -> ActionResult:
        params = {"screenmode": "view_thread", "thread_id": thread_id, **({"forum_id": forum_id} if forum_id else {})}
        async with game_session.lock:
            page = await self.actions._in_game(village_id, "forum", **params, answer="true")
            self.actions._capture(await page.content(), "forum_thread")

            field = page.locator(FORUM_FIELD).first
            if not await field.count():
                opener = page.locator(FORUM_OPEN).first
                if await opener.count():
                    await human_click(page, opener)
                    await page.wait_for_timeout(1_000)
                field = page.locator(FORUM_FIELD).first

            if not await field.count():
                return ActionResult(False, "reply_forum", "campo de resposta do fórum não encontrado")

            await human_fill(field, text)
            await human_delay(600, 1500)
            submit = field.locator("xpath=ancestor::form[1]").locator(SUBMIT).first
            if not await submit.count():
                return ActionResult(False, "reply_forum", "botão de responder no fórum não encontrado")

            await self.actions._click_and_settle(page, submit)
            messages = await self.actions.screen_messages(page)
            page = await self.actions._in_game(village_id, "forum", **params)
            posted = text[:40] in TribeParser.content(await page.content(), 100_000)

        if messages["errors"] or not posted:
            return ActionResult(False, "reply_forum", " | ".join(messages["errors"]) or "a resposta não apareceu no tópico")

        logger.info("Resposta publicada no tópico {}", thread_id)
        return ActionResult(True, "reply_forum", "resposta publicada no fórum", {"thread_id": thread_id, "notices": messages["notices"]})
