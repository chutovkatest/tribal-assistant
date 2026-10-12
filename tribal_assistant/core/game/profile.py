"""Player profile text (own profile → Editar perfil), written once so the quest "A aparência importa" completes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from tribal_assistant.core.game.human import human_click, human_delay, human_fill
from tribal_assistant.core.game.result import ActionResult
from tribal_assistant.core.game.session import game_session

if TYPE_CHECKING:
    from playwright.async_api import Page

    from tribal_assistant.core.game.actions import GameActions

SCREEN = "info_player"
EDIT = '#content_value a:has-text("Editar perfil"), #content_value a:has-text("Upraviť profil"), #content_value a:has-text("Upraviť"), #content_value a:has-text("Editar"), #content_value a[href*="edit_profile"], #content_value a[href*="mode=edit"]'
TEXT_FIELD = 'form textarea[name="personal_text"], form textarea[name="text"], form textarea'
SUBMIT = 'form input[type="submit"]:visible, form button[type="submit"]:visible, form .btn-confirm-yes:visible, form input.btn:visible'
CURRENT_JS = "() => (document.querySelector('form textarea')?.value || '').trim()"


class Profile:
    def __init__(self, actions: GameActions) -> None:
        self.actions = actions

    async def _form(self, village_id: str) -> Page:
        page = await self.actions._in_game(village_id, SCREEN, edit="1")
        await page.wait_for_timeout(800)
        if not await page.locator(TEXT_FIELD).count():
            edit = page.locator(EDIT).first
            if await edit.count():
                await human_click(page, edit)
                await page.wait_for_timeout(1_200)
        self.actions._capture(await page.content(), "profile_edit")
        return page

    async def set_text(self, village_id: str, text: str) -> ActionResult:
        async with game_session.lock:
            page = await self._form(village_id)

            field = page.locator(TEXT_FIELD).first
            if not await field.count():
                return ActionResult(False, "set_profile_text", "campo do texto do perfil não encontrado")

            if (await page.evaluate(CURRENT_JS)) == text.strip():
                return ActionResult(True, "set_profile_text", "texto do perfil já está atualizado")

            await human_fill(field, text)
            await human_delay(500, 1200)

            form = field.locator("xpath=ancestor::form[1]")
            submit = form.locator('input[type="submit"], button[type="submit"], .btn').first
            if not await submit.count():
                submit = page.locator(SUBMIT).first
            if not await submit.count():
                return ActionResult(False, "set_profile_text", "botão de salvar do perfil não encontrado")

            await human_click(page, submit)
            await page.wait_for_timeout(1_500)
            messages = await self.actions.screen_messages(page)
            page = await self._form(village_id)
            saved = (await page.evaluate(CURRENT_JS)) == text.strip()

        if messages["errors"] or not saved:
            return ActionResult(False, "set_profile_text", " | ".join(messages["errors"]) or "o jogo não salvou o texto")

        logger.info("Texto do perfil atualizado")
        return ActionResult(True, "set_profile_text", "texto do perfil salvo", {"notices": messages["notices"]})
