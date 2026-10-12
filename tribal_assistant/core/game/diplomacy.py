"""Social screens: tribe invites and applications, and the mentor offers for new players."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from tribal_assistant.core.game.human import human_click, human_delay, human_fill
from tribal_assistant.core.game.result import ActionResult
from tribal_assistant.core.game.scraper.social import TribeParser
from tribal_assistant.core.game.session import game_session

if TYPE_CHECKING:
    from tribal_assistant.core.game.actions import GameActions

MENTORS_JS = """() => [...document.querySelectorAll('a[href*="action=accept_mentor"]')].map(a => {
  const card = a.closest('table');
  const text = card ? card.textContent.replace(/\\s+/g, ' ') : '';
  const points = (text.match(/(?:Pontos|Body|Points):\\s*([\\d.]+)/i) || [])[1] || '0';
  const rank = (text.match(/(?:Classificação|Poradie|Umiestnenie|Rank):\\s*(\\d+)/i) || [])[1] || '0';
  return {
    id: new URL(a.href, location.href).searchParams.get('mentor_id'),
    name: (card?.querySelector('th')?.textContent || '').trim(),
    points: Number(points.replace(/\\D/g, '')),
    rank: Number(rank),
    recommended: /Recomendado|Odpor[uú][cč]an[yý]|Recommended/i.test(text),
  };
})"""


GONE = ("Tribo não encontrada", "Kmeň neexistuje", "Kmeň nebol nájdený", "Tribe not found")
GONE_TEXT = "a tribo não existe mais"

class Diplomacy:
    def __init__(self, actions: GameActions) -> None:
        self.actions = actions

    @staticmethod
    def best_tribe(nearby: list[dict[str, Any]], skip: set[str], min_members: int) -> dict[str, Any] | None:
        candidates = [t for t in nearby if t["id"] not in skip and t.get("members", 0) >= min_members]
        return max(candidates, key=lambda t: (t.get("score", 0), t.get("points", 0), t.get("members", 0)), default=None)

    @staticmethod
    def best_mentor(mentors: list[dict[str, Any]], skip: set[str]) -> dict[str, Any] | None:
        candidates = [m for m in mentors if m.get("id") and m.get("name") not in skip]
        return max(candidates, key=lambda m: (m.get("recommended", False), -(m.get("rank") or 10**6)), default=None)

    async def tribes(self, village_id: str) -> dict[str, Any]:
        async with game_session.lock:
            page = await self.actions._in_game(village_id, "ally")
            html = await page.content()
        state = TribeParser.state(html)
        if state["applications"]:
            self.actions._capture(html, "tribe_applications")
        return state

    async def mentors(self, village_id: str) -> list[dict[str, Any]]:
        async with game_session.lock:
            page = await self.actions._in_game(village_id, "mentor")
            return await page.evaluate(MENTORS_JS)

    async def apply(self, village_id: str, ally_id: str, text: str) -> ActionResult:
        async with game_session.lock:
            page = await self.actions._in_game(village_id, "info_ally", mode="apply", id=ally_id)
            html = await page.content()
            self.actions._capture(html, "tribe_apply")
            if any(label.casefold() in html.casefold() for label in GONE):
                return ActionResult(False, "apply_to_tribe", GONE_TEXT, {"gone": True})

            field = page.locator("form textarea:visible").first
            if not await field.count():
                return ActionResult(False, "apply_to_tribe", "campo da candidatura não encontrado")

            await human_fill(field, text)
            await human_delay(500, 1200)

            submit = page.locator('form input[type="submit"]:visible, form button[type="submit"]:visible, form .btn:visible').first
            if not await submit.count():
                return ActionResult(False, "apply_to_tribe", "formulário de candidatura não encontrado")

            await human_click(page, submit)
            await page.wait_for_timeout(1_500)
            messages = await self.actions.screen_messages(page)

        if messages["errors"]:
            return ActionResult(False, "apply_to_tribe", " | ".join(messages["errors"]))

        logger.info("Candidatura enviada à tribo {}", ally_id)
        return ActionResult(True, "apply_to_tribe", "candidatura enviada", {"ally_id": ally_id, "notices": messages["notices"]})

    async def accept_invite(self, village_id: str, invite_id: str) -> ActionResult:
        async with game_session.lock:
            page = await self.actions._in_game(village_id, "ally")
            link = page.locator(f'table.vis a[href*="accept"][href*="{invite_id}"]').first
            if not await link.count():
                return ActionResult(False, "accept_tribe_invite", "convite não encontrado")

            await human_click(page, link)
            await page.wait_for_timeout(1_200)
            confirm = page.locator(".evt-confirm-btn:visible, .btn-confirm-yes:visible").first
            if await confirm.count():
                await human_click(page, confirm)
                await page.wait_for_timeout(1_200)

            messages = await self.actions.screen_messages(page)
            state = TribeParser.state(await page.content()) if "screen=ally" in page.url else {"in_tribe": True}

        if messages["errors"] or not state.get("in_tribe"):
            return ActionResult(False, "accept_tribe_invite", " | ".join(messages["errors"]) or "o convite não foi aceito")

        logger.info("Entrou na tribo pelo convite {}", invite_id)
        return ActionResult(True, "accept_tribe_invite", "entrou na tribo", {"notices": messages["notices"]})

    async def accept_mentor(self, village_id: str, mentor_id: str) -> ActionResult:
        async with game_session.lock:
            page = await self.actions._in_game(village_id, "mentor")
            link = page.locator(f'a[href*="action=accept_mentor"][href*="mentor_id={mentor_id}"]').first
            if not await link.count():
                return ActionResult(False, "accept_mentor", "oferta de mentor não encontrada")

            await human_click(page, link)
            await page.wait_for_timeout(1_000)
            confirm = page.locator(".evt-confirm-btn:visible, .popup_box .btn-confirm-yes:visible").first
            if await confirm.count():
                await human_click(page, confirm)
                await page.wait_for_timeout(1_500)

            messages = await self.actions.screen_messages(page)
            await self.actions._in_game(village_id, "mentor")
            left = await page.evaluate(MENTORS_JS)

        if messages["errors"] or any(m["id"] == mentor_id for m in left):
            return ActionResult(False, "accept_mentor", " | ".join(messages["errors"]) or "a oferta continua aberta")

        logger.info("Mentor {} aceito", mentor_id)
        return ActionResult(True, "accept_mentor", "mentor aceito", {"mentor_id": mentor_id, "notices": messages["notices"]})
