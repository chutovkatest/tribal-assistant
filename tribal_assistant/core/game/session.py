"""Long-lived Playwright session.

One browser stays open between syncs so the session logs in once and then only
navigates, instead of relaunching Chromium (and risking a captcha) every run.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from tribal_assistant.core.accounts.context import AccountContext, current_account
from tribal_assistant.core.config import settings
from tribal_assistant.core.errors import ConflictError
from tribal_assistant.core.game.lease import SessionLease
from tribal_assistant.core.game.narrator import Narrator

VIEWPORTS = ({"width": 1366, "height": 768}, {"width": 1440, "height": 900}, {"width": 1536, "height": 864})


class GameSession:
    def __init__(self, account: AccountContext) -> None:
        self.account = account
        self.lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self.lease = SessionLease()

    @property
    def state_file(self) -> Path:
        return self.account.state_path

    async def page(self) -> Page:
        await self.lease.hold()
        alive = (
            self._browser is not None
            and self._browser.is_connected()
            and self._page is not None
            and not self._page.is_closed()
        )
        if not alive:
            await self.close()
            await self._start()
        assert self._page is not None
        return self._page

    async def _start(self) -> None:
        if not settings.play:
            raise ConflictError("Este servidor não joga (PLAY=false): use o servidor que está jogando")

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.account.headless or settings.headless,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        self._context = await self._browser.new_context(
            storage_state=str(self.state_file) if self.state_file.exists() else None,
            viewport=random.choice(VIEWPORTS),
            locale="sk-SK",
            timezone_id="Europe/Bratislava",
        )
        self._page = await self._context.new_page()
        Narrator.watch(self._page)
        logger.info("Sessão do navegador iniciada para a conta {}", self.account.name)

    async def save_state(self) -> None:
        if self._context is not None:
            await self._context.storage_state(path=str(self.state_file))

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self.save_state()
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            logger.debug("Navegador já fechado")
        finally:
            self._playwright = self._browser = self._context = self._page = None
            await self.lease.release()


class SessionPool:
    """One browser per account; `game_session` resolves to the session of the current account."""

    def __init__(self) -> None:
        self.sessions: dict[int, GameSession] = {}

    def get(self, account: AccountContext | None = None) -> GameSession:
        account = account or current_account()
        session = self.sessions.get(account.id)
        if session is None or session.account != account:
            session = GameSession(account)
            self.sessions[account.id] = session

        return session

    @property
    def lock(self) -> asyncio.Lock:
        return self.get().lock

    async def page(self) -> Page:
        return await self.get().page()

    async def save_state(self) -> None:
        await self.get().save_state()

    async def close(self) -> None:
        for session in list(self.sessions.values()):
            await session.close()


game_session = SessionPool()
