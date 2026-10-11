"""Game actions: the only code that changes game state.

Every action takes the shared browser lock, reaches the screen the way a player
would, acts through the game's own buttons and forms, and reports what the game
answered. Guardrails live one layer up (tribal_assistant.core.agents.guardrails).
"""

from datetime import UTC, datetime
from typing import Any
from time import monotonic

from loguru import logger
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from tribal_assistant.core.accounts.context import current_account
from tribal_assistant.core.game.browser import (
    BUILD_QUEUE_JS,
    ensure_in_game,
    evaluate_page,
    open_screen,
)
from tribal_assistant.core.game.conquest import Conquest
from tribal_assistant.core.game.diplomacy import Diplomacy
from tribal_assistant.core.game.farm_assistant import FarmAssistant
from tribal_assistant.core.game.forge import Forge
from tribal_assistant.core.game.human import (
    human_click,
    human_delay,
    human_fill,
    human_type,
    reading_pause,
)
from tribal_assistant.core.game.items import ItemCount
from tribal_assistant.core.game.market import Market
from tribal_assistant.core.game.profile import Profile
from tribal_assistant.core.game.result import ActionResult
from tribal_assistant.core.game.scraper.quests import (
    Quest,
    QuestReward,
    parse_quest_body,
    parse_quest_list,
    parse_rewards,
)
from tribal_assistant.core.game.screens import ScreenCatalog
from tribal_assistant.core.game.session import game_session
from tribal_assistant.core.game.social import Social
from tribal_assistant.core.game.tribe import TribeHall
from tribal_assistant.core.game.wander import Wanderer

UNIT_SCREEN = {
    "spear": "barracks",
    "sword": "barracks",
    "axe": "barracks",
    "archer": "barracks",
    "spy": "stable",
    "light": "stable",
    "marcher": "stable",
    "heavy": "stable",
    "ram": "garage",
    "catapult": "garage",
}

QUEST_POPUP = ".quest-popup-container"

SCAVENGE_HOME_JS = """() => Object.fromEntries([...document.querySelectorAll('.units-entry-all[data-unit]')]
  .map(a => [a.dataset.unit, Number((a.innerText.match(/\\d+/) || [0])[0])]))"""

CURRENT_FLAG_JS = """() => {
  const th = [...document.querySelectorAll('#content_value th')].find(t => /Atual bandeira/.test(t.innerText));
  const cell = th && th.closest('table').querySelector('td');
  return cell ? (cell.querySelector('strong')?.innerText.trim() + ' ' + (cell.querySelector('p')?.innerText.trim() || '')).trim() : '';
}"""

FLAGS_STATE_JS = """() => {
  const owned = [...document.querySelectorAll('.flag_box:not(.flag_box_empty)')]
    .map(n => n.id.replace('flag_box_', '').split('_').map(Number))
    .filter(p => p.length === 2 && p.every(Number.isFinite));
  const th = [...document.querySelectorAll('#content_value th')].find(t => /Atual bandeira/.test(t.innerText));
  const cell = th && th.closest('table').querySelector('td');
  const current = cell ? (cell.querySelector('strong')?.innerText || '').trim() : '';
  return {owned, current};
}"""

KNIGHT_STATE_JS = """() => {
  const visible = (sel) => { const n = document.querySelector(sel); return !!(n && n.offsetParent); };
  return {
    learnable: [...document.querySelectorAll('.skill_node.learnable[data-skill]')].map(n => Number(n.dataset.skill)),
    can_recruit: visible('.knight_recruit_launch'),
    can_train: visible('.knight_train_launch'),
  };
}"""

MARKET_OFFERS_JS = """() => {
  const names = {Madeira: 'wood', Argila: 'stone', Ferro: 'iron'};
  const res = (td) => {
    const icon = td && td.querySelector('.icon.header[data-title]');
    return {res: icon ? names[icon.dataset.title] || null : null, amount: Number((td ? td.innerText : '').replace(/\\D/g, '')) || 0};
  };
  const rows = [...document.querySelectorAll('#content_value table.vis tr')]
    .filter(tr => tr.cells.length >= 7 && tr.querySelector('a[href*="info_player"]'));
  return rows.map((tr, index) => {
    const receive = res(tr.cells[0]);
    const pay = res(tr.cells[1]);
    const time = (tr.cells[3].innerText.match(/(\\d+):(\\d+):(\\d+)/) || []).slice(1).map(Number);
    return {
      index, receive: receive.res, receive_amount: receive.amount, pay: pay.res, pay_amount: pay.amount,
      player: tr.cells[2].innerText.trim(), minutes: time.length ? time[0] * 60 + time[1] : null,
      can_accept: !!tr.cells[6].querySelector('form, input[type=submit], .btn'),
    };
  });
}"""

MERCHANTS_JS = """() => {
  const text = (document.querySelector('#content_value') || document.body).innerText;
  const m = text.match(/Comerciantes:\\s*(\\d+)\\s*\\/\\s*(\\d+)/);
  const c = text.match(/transporte:\\s*([\\d.]+)/);
  return {free: m ? Number(m[1]) : 0, total: m ? Number(m[2]) : 0, carry: c ? Number(c[1].replace(/\\D/g, '')) : 0};
}"""

SMITH_JS = """() => {
  const techs = (window.BuildingSmith && BuildingSmith.techs && BuildingSmith.techs.available) || {};
  const busy = document.querySelectorAll('#current_research tbody tr.lit, #techqueue_smithy tr').length > 0;
  return Object.values(techs).map(t => ({
    unit: t.id, level: Number(t.level || 0), busy,
    blocked: Object.keys(t).filter(k => k.startsWith('error_') && t[k]),
    cost: {wood: Number(t.wood || 0), clay: Number(t.stone || 0), iron: Number(t.iron || 0)},
  }));
}"""

FREE_FINISH = "#buildqueue .btn-instant-free"
FREE_WAIT_MAX = 75
QUEUE_CHECKS = 4
QUEUE_WAIT_MS = 800
FREE_WAIT_JS = "(n) => { const at = Number(n.dataset.availableFrom || 0); return at ? Math.max(0, at - Date.now() / 1000) : null; }"

DAILY_CHEST = "#daily_bonus_content .reward:has(.actions a.btn)"
DAILY_STATE_JS = """() => [...document.querySelectorAll('#daily_bonus_content .reward')].map(r => ({
  day: (r.querySelector('.day')?.innerText || '').trim(),
  open: !!r.querySelector('.actions a.btn'),
  item: (r.querySelector('.db-chest')?.dataset.title || '').match(/class="name">([^<]+)/)?.[1] || null,
}))"""

UNLOCK_DIALOG_JS = """(id) => {
  const box = document.querySelector(`#popup_box_unlock-option-${id}`);
  if (!box) return null;
  const num = (sel) => Number((box.querySelector(sel)?.innerText || "0").replace(/\\D/g, ""));
  const res = (window.game_data && game_data.village) || {};
  return {
    wood: num(".wood-value"), stone: num(".stone-value"), iron: num(".iron-value"),
    duration: (box.querySelector(".duration")?.innerText || "").trim(),
    blocked: (box.querySelector(".disabled-explanation")?.innerText || "").trim(),
    have: {wood: Number(res.wood || 0), stone: Number(res.stone || 0), iron: Number(res.iron || 0)},
  };
}"""

SCREEN_MESSAGES_JS = """() => {
  const read = (sel) => [...document.querySelectorAll(sel)]
    .filter(n => n.offsetParent !== null || n.closest('#autoHideBox, .autoHideBox'))
    .map(n => n.innerText.trim()).filter(Boolean);
  const errors = read('.error_box, .autoHideBox.error, #error, .error, .autoHideBox.fail');
  const notices = read('.autoHideBox.success, .autoHideBox.info, .success_box, .info_box.notice, .autoHideBox:not(.error):not(.fail)');
  return {errors: [...new Set(errors)], notices: [...new Set(notices)].filter(t => !errors.includes(t))};
}"""

NEXT_LEVEL_JS = """(id) => {
  const b = window.BuildingMain && BuildingMain.buildings && BuildingMain.buildings[id];
  return b ? Number(b.level_next || 0) : null;
}"""

BUILDING_ERROR_JS = """(id) => {
  const b = window.BuildingMain && BuildingMain.buildings && BuildingMain.buildings[id];
  return b ? (b.error || null) : 'edifício desconhecido';
}"""


class GameActions:
    """Every state-changing action a player can take, driven through the real game UI."""

    @property
    def diplomacy(self) -> Diplomacy:
        return Diplomacy(self)

    @property
    def farm(self) -> FarmAssistant:
        return FarmAssistant(self)

    @property
    def forge(self) -> Forge:
        return Forge(self)

    @property
    def conquest(self) -> Conquest:
        return Conquest(self)

    @property
    def market(self) -> Market:
        return Market(self)

    @property
    def wander(self) -> Wanderer:
        return Wanderer(self)

    @property
    def profile(self) -> Profile:
        return Profile(self)

    @property
    def social(self) -> Social:
        return Social(self)

    @property
    def tribe(self) -> TribeHall:
        return TribeHall(self)

    def _capture(self, page_html: str, name: str) -> None:
        """Keep the HTML of screens we act on, so scrapers can be written against real markup."""
        directory = current_account().capture_dir
        directory.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        (directory / f"{name}-{stamp}.html").write_text(page_html, encoding="utf-8")

    async def screen_messages(self, page: Page) -> dict[str, list[str]]:
        """Errors and notices the game shows on screen after an action (red boxes, green toasts)."""
        try:
            found = await evaluate_page(page, SCREEN_MESSAGES_JS) or {}
        except PlaywrightError:
            return {"errors": [], "notices": []}

        for text in found.get("errors", []):
            logger.warning("Jogo (erro): {}", text)

        for text in found.get("notices", []):
            logger.info("Jogo: {}", text)

        return {"errors": found.get("errors", []), "notices": found.get("notices", [])}

    async def _game_error(self, page: Page) -> str | None:
        messages = await self.screen_messages(page)
        return " | ".join(messages["errors"]) or None

    async def _click_and_settle(self, page: Page, locator: Any, timeout: int = 15_000) -> None:
        """Click a control that may either navigate or update the page over AJAX."""
        try:
            async with page.expect_navigation(wait_until="load", timeout=timeout):
                await human_click(page, locator)
        except PlaywrightError:
            await page.wait_for_load_state("load")

        await human_delay(600, 1500)

    async def _in_game(self, village_id: str, screen: str, **params: str) -> Page:
        page = await game_session.page()
        await ensure_in_game(page)
        await open_screen(page, screen, village_id, **params)

        return page

    async def click_free_finish(self, page: Page) -> int:
        """Click the free "finish now" button on short build orders; never the paid premium ones."""
        finished = 0

        for _ in range(5):
            button = page.locator(FREE_FINISH).first
            if not await button.count():
                break

            if not await button.is_visible():
                wait = await button.evaluate(FREE_WAIT_JS)
                if wait is None or wait > FREE_WAIT_MAX:
                    break

                # Never hold the shared browser lock while waiting for the
                # free-finish window. The scheduled watcher will check again.
                break

            classes = await button.get_attribute("class") or ""
            if "btn-instant-free" not in classes:
                break

            await self._click_and_settle(page, button)
            finished += 1

        if finished:
            logger.info("{} construção(ões) concluída(s) grátis", finished)
        elif await page.locator("#buildqueue").count():
            html = await page.locator("#buildqueue").first.evaluate("(n) => n.outerHTML")
            ScreenCatalog().save("buildqueue", html, quiet=True)

        return finished

    async def finish_free(self, village_id: str) -> ActionResult:
        """Open the headquarters and use every free "finish now" available."""
        async with game_session.lock:
            page = await self._in_game(village_id, "main")
            finished = await self.click_free_finish(page)

        if not finished:
            return ActionResult(
                False, "finish_free", "nenhuma construção com conclusão grátis agora"
            )

        return ActionResult(
            True,
            "finish_free",
            f"{finished} construção(ões) concluída(s) grátis",
            {"finished": finished},
        )

    async def unlock_scavenge(self, village_id: str, option_id: int) -> ActionResult:
        """Unlock a scavenging tier from the rally point, after checking its cost against the stock."""
        async with game_session.lock:
            page = await self._in_game(village_id, "place", mode="scavenge")

            option = page.locator(".options-container .scavenge-option").nth(option_id - 1)
            button = option.locator(".unlock-button")
            if not await button.count() or not await button.first.is_visible():
                return ActionResult(
                    False,
                    "unlock_scavenge",
                    f"coleta {option_id} não está disponível para desbloquear",
                )

            await button.first.scroll_into_view_if_needed()
            await human_delay(300, 900)
            await button.first.click()

            try:
                await page.locator(f"#popup_box_unlock-option-{option_id}").wait_for(
                    state="visible", timeout=8_000
                )
            except PlaywrightError:
                return ActionResult(False, "unlock_scavenge", "janela de desbloqueio não abriu")

            info = await page.evaluate(UNLOCK_DIALOG_JS, option_id)
            if info is None:
                return ActionResult(False, "unlock_scavenge", "janela de desbloqueio não abriu")

            cost = {"wood": info["wood"], "clay": info["stone"], "iron": info["iron"]}
            if info["blocked"]:
                await self._close_popup(page)
                return ActionResult(False, "unlock_scavenge", info["blocked"], {"cost": cost})

            short = [
                k
                for k, v in (
                    ("wood", info["wood"]),
                    ("stone", info["stone"]),
                    ("iron", info["iron"]),
                )
                if v > info["have"][k]
            ]
            if short:
                await self._close_popup(page)
                return ActionResult(
                    False,
                    "unlock_scavenge",
                    "recursos insuficientes: " + ", ".join(short),
                    {"cost": cost},
                )

            confirm = page.locator(
                f"#popup_box_unlock-option-{option_id} .scavenge-option-unlock-dialog a.btn"
            ).first
            await human_delay(600, 1500)
            await confirm.click()
            await page.wait_for_timeout(2_000)

            if error := await self._game_error(page):
                return ActionResult(False, "unlock_scavenge", error, {"cost": cost})

            logger.info(
                "Desbloqueando coleta {} na aldeia {} por {}", option_id, village_id, cost
            )
            return ActionResult(
                True,
                "unlock_scavenge",
                f"coleta {option_id} desbloqueando ({info['duration']})",
                {"option_id": option_id, "cost": cost, "duration": info["duration"]},
            )

    async def send_scavenge(
        self, village_id: str, option_id: int, units: dict[str, int]
    ) -> ActionResult:
        """Send troops scavenging on one tier with the free start button (never the premium +20%)."""
        units = {u: n for u, n in units.items() if n > 0}
        if not units:
            return ActionResult(False, "send_scavenge", "nenhuma tropa informada")

        async with game_session.lock:
            page = await self._in_game(village_id, "place", mode="scavenge")

            option = page.locator(".options-container .scavenge-option").nth(option_id - 1)
            start = option.locator(".free_send_button")
            if not await start.count() or not await start.first.is_visible():
                return ActionResult(
                    False, "send_scavenge", f"coleta {option_id} não está livre para enviar"
                )

            home = await page.evaluate(SCAVENGE_HOME_JS) or {}
            units = {
                u: min(n, int(home.get(u, 0))) for u, n in units.items() if int(home.get(u, 0)) > 0
            }
            if not units:
                return ActionResult(False, "send_scavenge", "nenhuma das tropas pedidas está em casa", {"home": home})

            for unit, count in units.items():
                field_ = page.locator(f"input.unitsInput[name='{unit}']").first
                if not await field_.count():
                    return ActionResult(False, "send_scavenge", f"{unit} não pode coletar")

                await field_.click()
                await field_.fill("")
                await human_type(field_, str(count), delay=80)

            await human_delay(500, 1200)
            await start.first.click()
            await page.wait_for_timeout(2_000)

            if error := await self._game_error(page):
                return ActionResult(
                    False, "send_scavenge", error, {"option_id": option_id, "units": units}
                )

            logger.info("Coleta {} na aldeia {} com {}", option_id, village_id, units)
            return ActionResult(
                True,
                "send_scavenge",
                f"{sum(units.values())} tropa(s) coletando no nível {option_id}",
                {"option_id": option_id, "units": units},
            )

    async def upgrade_building(
        self, village_id: str, building: str, finish_free: bool = True
    ) -> ActionResult:
        """Queue the next level of `building` from the headquarters screen."""
        started = monotonic()
        async with game_session.lock:
            acquired = monotonic()
            page = await self._in_game(village_id, "main")
            navigated = monotonic()
            before = await evaluate_page(page, BUILD_QUEUE_JS) or []
            next_before = await page.evaluate(NEXT_LEVEL_JS, building)

            button = page.locator(f'a.btn-build:not(.btn-bcr)[data-building="{building}"]')
            if not await button.count() or not await button.first.is_visible():
                reason = await page.evaluate(BUILDING_ERROR_JS, building)
                return ActionResult(
                    False,
                    "upgrade_building",
                    reason or "sem botão de construção",
                    {"building": building},
                )

            level = await button.first.get_attribute("data-level-next")
            click_started = monotonic()
            # Build buttons can complete via AJAX; do not wait 15s for a navigation
            # that will never occur. The existing queue verification remains mandatory.
            await self._click_and_settle(page, button.first, timeout=3_000)
            click_done = monotonic()

            if error := await self._game_error(page):
                return ActionResult(False, "upgrade_building", error, {"building": building})

            if not await page.locator("#buildqueue").count():
                await open_screen(page, "main", village_id)

            after, finished = before, False
            for attempt in range(QUEUE_CHECKS):
                if attempt == QUEUE_CHECKS - 1:
                    await open_screen(page, "main", village_id)
                after = await evaluate_page(page, BUILD_QUEUE_JS) or []
                next_after = await page.evaluate(NEXT_LEVEL_JS, building)
                finished = next_before is not None and next_after is not None and next_after > next_before
                if len(after) > len(before) or finished:
                    break
                await page.wait_for_timeout(QUEUE_WAIT_MS)

            if len(after) <= len(before) and not finished:
                self._capture(await page.content(), f"main-upgrade-{building}")
                reason = await page.evaluate(BUILDING_ERROR_JS, building)
                return ActionResult(
                    False,
                    "upgrade_building",
                    "fila de construção não mudou" + (f": {reason}" if reason else ""),
                    {"building": building},
                )

            verified = monotonic()
            finished = await self.click_free_finish(page) if finish_free else 0
            completed = monotonic()
            logger.info(
                "BUILD_LATENCY building={} lock_wait_s={:.3f} navigation_s={:.3f} click_s={:.3f} verify_s={:.3f} free_finish_s={:.3f} total_s={:.3f}",
                building, acquired-started, navigated-acquired, click_done-click_started,
                verified-click_done, completed-verified, completed-started,
            )
            logger.info("{} nível {} na fila da aldeia {}", building, level, village_id)
            return ActionResult(
                True,
                "upgrade_building",
                f"{building} → nível {level} na fila",
                {"building": building, "level": level, "queue": after, "finished_free": finished},
            )

    async def recruit(self, village_id: str, unit: str, count: int) -> ActionResult:
        """Recruit `count` units of `unit` in the building that trains it."""
        screen = UNIT_SCREEN.get(unit)
        if screen is None or count <= 0:
            return ActionResult(
                False, "recruit", f"unidade {unit!r} não recrutável aqui", {"unit": unit}
            )

        async with game_session.lock:
            page = await self._in_game(village_id, screen)

            field_ = page.locator(f"#train_form input[name='{unit}']")
            if not await field_.count() or not await field_.first.is_visible():
                return ActionResult(
                    False, "recruit", f"{unit} indisponível em {screen}", {"unit": unit}
                )

            available = page.locator(f"#{unit}_0_a")
            if await available.count():
                text = (await available.first.inner_text()).strip("() \n")
                if text.isdigit() and int(text) < count:
                    count = int(text)

            if count <= 0:
                return ActionResult(False, "recruit", "sem recursos ou população", {"unit": unit})

            await field_.first.click()
            await human_type(field_.first, str(count), delay=90)
            await self._click_and_settle(page, page.locator("#train_form .btn-recruit").first)

            if error := await self._game_error(page):
                return ActionResult(False, "recruit", error, {"unit": unit, "count": count})

            logger.info("Recrutando {} {} na aldeia {}", count, unit, village_id)
            return ActionResult(
                True, "recruit", f"{count} {unit} em recrutamento", {"unit": unit, "count": count}
            )

    async def send_attack(
        self, village_id: str, x: int, y: int, units: dict[str, int]
    ) -> ActionResult:
        """Send an attack from the rally point: fill troops and target, confirm."""
        units = {u: n for u, n in units.items() if n > 0}
        if not units:
            return ActionResult(False, "send_attack", "nenhuma tropa informada")

        target = f"{x}|{y}"

        async with game_session.lock:
            page = await self._in_game(village_id, "place")

            for unit, count in units.items():
                box = page.locator(f"#unit_input_{unit}")
                if not await box.count():
                    return ActionResult(
                        False, "send_attack", f"sem campo para {unit}", {"unit": unit}
                    )

                home = await box.first.get_attribute("data-all-count")
                if home is not None and int(home) < count:
                    return ActionResult(
                        False, "send_attack", f"só há {home} {unit} na aldeia", {"unit": unit}
                    )

                await box.first.click()
                await human_type(box.first, str(count), delay=80)

            coords = page.locator("input.target-input-field").first
            if await coords.count() and await coords.is_visible():
                await coords.click()
                await human_type(coords, target, delay=70)
            else:
                await human_fill(page.locator("#inputx"), str(x))
                await human_fill(page.locator("#inputy"), str(y))

            await human_delay(400, 1100)
            await self._click_and_settle(page, page.locator("#target_attack"))
            self._capture(await page.content(), "place-confirm")

            if error := await self._game_error(page):
                return ActionResult(False, "send_attack", error, {"target": target})

            confirm = page.locator(
                "#troop_confirm_submit, #troop_confirm_go, #command-data-form .btn-attack"
            ).first
            if not await confirm.count():
                return ActionResult(
                    False, "send_attack", "tela de confirmação não encontrada", {"target": target}
                )

            arrival = None
            for selector in ("#date_arrival", ".relative_time"):
                node = page.locator(selector)
                if await node.count():
                    arrival = (await node.first.inner_text()).strip()
                    break

            await reading_pause(page)
            await self._click_and_settle(page, confirm)

            if error := await self._game_error(page):
                return ActionResult(False, "send_attack", error, {"target": target})

            if await page.locator("#troop_confirm_submit:visible, #troop_confirm_go:visible").count():
                self._capture(await page.content(), "place-not-sent")
                return ActionResult(False, "send_attack", "o jogo não aceitou a confirmação: a tela de confirmar continua aberta", {"target": target})

            listed = await page.locator(f'.commands-container :text("{target}"), #commands_outgoings :text("{target}")').count()
            if not listed:
                self._capture(await page.content(), "place-sent-unlisted")
                logger.warning("Envio para {} confirmado mas o comando não apareceu na lista da praça", target)

            logger.info("Ataque enviado de {} para {} com {}", village_id, target, units)
            return ActionResult(
                True,
                "send_attack",
                f"ataque enviado para {target}",
                {"target": target, "units": units, "arrival": arrival},
            )

    async def _open_quests(self, page: Page) -> bool:
        popup = page.locator(QUEST_POPUP)
        if await popup.count() and await popup.first.is_visible():
            return True

        trigger = page.locator("#new_quest, .quest[data-id], #questlog_new .quest").first
        if not await trigger.count():
            return False

        await human_click(page, trigger)

        try:
            await popup.first.wait_for(state="visible", timeout=10_000)
        except PlaywrightError:
            return False

        await human_delay(500, 1200)
        return True

    async def _close_popup(self, page: Page) -> None:
        close = page.locator(".popup_box_close").first

        if await close.count() and await close.is_visible():
            await human_click(page, close)
        else:
            await page.keyboard.press("Escape")

    async def _popup_html(self, page: Page) -> str:
        return await page.locator(QUEST_POPUP).first.evaluate("n => n.outerHTML")

    async def _select_quest(self, page: Page, quest_id: str) -> bool:
        """Show a quest in the popup, expanding its quest line first (popup UI only, no game action)."""
        link = page.locator(f'{QUEST_POPUP} a.quest-link[data-quest-id="{quest_id}"]').first
        if not await link.count():
            return False

        if not await link.is_visible():
            line = await link.get_attribute("data-questline-id")
            header = page.locator(f"{QUEST_POPUP} #questline-header-{line}").first
            if await header.count():
                await header.evaluate("n => n.click()")
                await page.wait_for_timeout(400)

        await link.evaluate("n => n.click()")
        await page.wait_for_timeout(700)
        return True

    async def read_quests(self, village_id: str) -> tuple[list[Quest], list[QuestReward]]:
        """Open the quest popup and read every quest line plus claimable rewards."""
        async with game_session.lock:
            page = await self._in_game(village_id, "overview")
            if not await self._open_quests(page):
                return [], []

            quests = parse_quest_list(await self._popup_html(page))

            for quest in quests:
                if not await self._select_quest(page, quest.quest_id):
                    continue
                quest.description, quest.goals, quest.can_complete = parse_quest_body(
                    await self._popup_html(page)
                )

            rewards = parse_rewards(await self._popup_html(page))
            await self._close_popup(page)

            return quests, rewards

    async def complete_quest(self, village_id: str, quest_id: str) -> ActionResult:
        """Press "Missão completa" on a finished quest."""
        async with game_session.lock:
            page = await self._in_game(village_id, "overview")
            if not await self._open_quests(page):
                return ActionResult(False, "complete_quest", "missões indisponíveis")

            if not await self._select_quest(page, quest_id):
                await self._close_popup(page)
                return ActionResult(False, "complete_quest", f"missão {quest_id} não encontrada")

            button = page.locator(f"{QUEST_POPUP} #main-tab .status-btn:not(.hidden)").first
            if not await button.count():
                await self._close_popup(page)
                return ActionResult(
                    False, "complete_quest", "missão ainda não concluída", {"quest_id": quest_id}
                )

            await human_click(page, button)
            await human_delay(900, 1800)
            await self._close_popup(page)

            logger.info("Missão {} concluída", quest_id)
            return ActionResult(
                True, "complete_quest", f"missão {quest_id} concluída", {"quest_id": quest_id}
            )

    async def open_daily_bonus(self, village_id: str) -> ActionResult:
        """Open every unlocked daily bonus chest in the profile; items go to the inventory."""
        async with game_session.lock:
            page = await self._in_game(village_id, "info_player", mode="daily_bonus")
            chests = await page.evaluate(DAILY_STATE_JS) or []

            opened = 0
            for _ in range(10):
                reward = page.locator(DAILY_CHEST).first
                if not await reward.count():
                    break

                await reward.locator(".chest_container").first.hover()
                await human_delay(300, 700)

                button = reward.locator(".actions a.btn").first
                if await button.is_visible():
                    await human_click(page, button)
                else:
                    await button.evaluate("(n) => n.click()")

                await human_delay(900, 1800)

                if not opened:
                    self._capture(await page.content(), "daily-bonus-opened")

                opened += 1
                await self._close_popup(page)
                await human_delay(400, 900)

        if not opened:
            return ActionResult(
                False, "open_daily_bonus", "nenhum baú diário para abrir", {"chests": chests}
            )

        items = [c["item"] for c in chests if c.get("open") and c.get("item")][:opened]
        logger.info("{} baú(s) de bônus diário aberto(s)", opened)
        return ActionResult(
            True,
            "open_daily_bonus",
            f"{opened} baú(s) diário(s) aberto(s): {', '.join(items) or 'itens no inventário'}",
            {"opened": opened, "items": items},
        )

    async def recruit_knight(self, village_id: str) -> ActionResult:
        """Recruit a paladin at the statue with resources (never premium points)."""
        async with game_session.lock:
            page = await self._in_game(village_id, "statue")

            launch = page.locator(".knight_recruit_launch")
            if not await launch.count() or not await launch.first.is_visible():
                return ActionResult(
                    False, "recruit_knight", "sem opção de recrutar paladino nesta aldeia"
                )

            await human_click(page, launch.first)
            await human_delay(700, 1400)

            confirm = page.locator("#knight_recruit_confirm")
            if not await confirm.count():
                self._capture(await page.content(), "statue-recruit")
                return ActionResult(False, "recruit_knight", "janela de recrutamento não abriu")

            await human_click(page, confirm.first)
            await page.wait_for_timeout(2_000)

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "recruit_knight", " | ".join(messages["errors"]))

        logger.info("Recrutando paladino na aldeia {}", village_id)
        return ActionResult(
            True, "recruit_knight", "paladino em recrutamento", {"notices": messages["notices"]}
        )

    async def inventory(self, village_id: str) -> list[dict[str, Any]]:
        """Items in the inventory with their detail text and whether they can be used."""
        async with game_session.lock:
            page = await self._in_game(village_id, "inventory")
            try:
                await page.wait_for_selector(".inventory_items .item, .inventory_message_empty:visible", timeout=15_000)
            except PlaywrightError:
                logger.warning("Inventário não carregou na aldeia {}", village_id)
                return []

            items = []
            for item in await page.locator(".inventory_items .item").all():
                key = (await item.get_attribute("id") or "").removeprefix("item_")
                await item.click()
                await page.wait_for_timeout(700)
                detail = page.locator(".inventory_detail:visible").first
                items.append(
                    {
                        "key": key,
                        "name": await item.get_attribute("data-title"),
                        "detail": " ".join((await detail.inner_text()).split()),
                        "usable": await detail.locator(".detail_actions a.btn").count() > 0,
                    }
                )

        return items

    async def use_item(self, village_id: str, key: str) -> ActionResult:
        """Use one inventory item (resource pack, construction bonus...) through its Usar button."""
        async with game_session.lock:
            page = await self._in_game(village_id, "inventory")
            await page.wait_for_timeout(2_000)

            item = page.locator(f"#item_{key}")
            if not await item.count():
                return ActionResult(False, "use_item", f"item {key} não está no inventário")

            name = await item.first.get_attribute("data-title")
            await human_click(page, item.first)
            await human_delay(600, 1200)
            before = ItemCount.of(await page.locator(".inventory_detail:visible").first.inner_text())

            use = page.locator(".inventory_detail .detail_actions a.btn").first
            if not await use.count():
                return ActionResult(False, "use_item", f"{name} não pode ser usado agora")

            await human_click(page, use)
            await human_delay(900, 1600)

            self._capture(await page.content(), f"use-item-{key}")
            dialog = page.locator(
                ".evt-confirm-btn:visible, .btn-confirm-yes:visible, .popup_box_container a.btn:visible:not(.btn-confirm-no):not(.btn-pp), .popup_box_container input.btn:visible"
            )
            if await dialog.count():
                await human_click(page, dialog.first)
                await page.wait_for_timeout(1_500)

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(
                    False, "use_item", " | ".join(messages["errors"]), {"item": name}
                )

            page = await self._in_game(village_id, "inventory")
            await page.wait_for_timeout(2_000)
            after = 0
            again = page.locator(f"#item_{key}")
            if await again.count():
                await human_click(page, again.first)
                await human_delay(500, 900)
                after = ItemCount.of(await page.locator(".inventory_detail:visible").first.inner_text())

            if not ItemCount.consumed(before, after):
                self._capture(await page.content(), f"use-item-not-consumed-{key}")
                return ActionResult(False, "use_item", f"{name}: o jogo não consumiu o item (continua com {after})", {"item": name})

        logger.info("Item {} ({}) usado na aldeia {}", key, name, village_id)
        return ActionResult(
            True, "use_item", f"{name} usado", {"item": name, "notices": messages["notices"]}
        )

    async def choose_relic(self, village_id: str, index: int) -> ActionResult:
        """Pick one of the starter relics offered in the treasury (relic_system)."""
        async with game_session.lock:
            page = await self._in_game(village_id, "relic_system")

            link = page.locator(f'a.btn[href*="mode=choose_relic"][href*="index={index}"]')
            if not await link.count():
                self._capture(await page.content(), "relic-missing")
                return ActionResult(
                    False,
                    "choose_relic",
                    "nenhuma relíquia inicial para escolher",
                    {"url": page.url},
                )

            await self._click_and_settle(page, link.first)
            await human_delay(700, 1400)

            confirm = page.locator(
                ".popup_box_container .btn-confirm-yes, .popup_box_container a.btn:visible"
            )
            if await confirm.count():
                await human_click(page, confirm.first)
                await page.wait_for_timeout(1_500)

            self._capture(await page.content(), "relic-chosen")
            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "choose_relic", " | ".join(messages["errors"]))

        logger.info("Relíquia inicial {} escolhida na aldeia {}", index, village_id)
        return ActionResult(
            True, "choose_relic", f"relíquia {index} escolhida", {"notices": messages["notices"]}
        )

    async def _open_relics(self, page: Page, village_id: str) -> None:
        await open_screen(page, "relic_system", village_id)
        await page.wait_for_timeout(1_500)

    async def equip_relic(self, village_id: str) -> ActionResult:
        """Equip the first relic from the treasury into this village's free relic slot."""
        async with game_session.lock:
            page = await self._in_game(village_id, "relic_system")
            await page.wait_for_timeout(2_000)

            status = page.locator("#village_equip_status")
            if (
                await status.count()
                and "nenhuma relíquia equipada" not in await status.inner_text()
            ):
                return ActionResult(False, "equip_relic", "aldeia já tem relíquia equipada")

            relic = page.locator("#relics img[data-id]").first
            if not await relic.count():
                return ActionResult(
                    False, "equip_relic", "nenhuma relíquia no inventário da tesouraria"
                )

            relic_id = await relic.get_attribute("data-id")
            await human_click(page, relic)
            await human_delay(800, 1500)

            equip = page.locator("#equip_button:visible")
            if not await equip.count():
                self._capture(await page.content(), "relic-equip-missing")
                return ActionResult(
                    False, "equip_relic", "botão de equipar não encontrado", {"relic": relic_id}
                )

            await human_click(page, equip.first)
            await human_delay(900, 1600)

            confirm = page.locator(".evt-confirm-btn:visible, .btn-confirm-yes:visible")
            if await confirm.count():
                await human_click(page, confirm.first)
                await page.wait_for_timeout(1_500)

            messages = await self.screen_messages(page)
            self._capture(await page.content(), "relic-equip")
            if messages["errors"]:
                return ActionResult(
                    False, "equip_relic", " | ".join(messages["errors"]), {"relic": relic_id}
                )

            await self._open_relics(page, village_id)
            status = " ".join((await page.locator("#village_equip_status").inner_text()).split())
            if "nenhuma relíquia equipada" in status:
                return ActionResult(
                    False,
                    "equip_relic",
                    f"relíquia não ficou equipada: {status}",
                    {"relic": relic_id},
                )

        logger.info("Relíquia {} equipada na aldeia {}", relic_id, village_id)
        return ActionResult(
            True,
            "equip_relic",
            f"relíquia {relic_id} equipada",
            {"relic": relic_id, "notices": messages["notices"]},
        )

    async def rename_village(self, village_id: str, name: str) -> ActionResult:
        """Rename the village from the headquarters form."""
        async with game_session.lock:
            page = await self._in_game(village_id, "main")

            field_ = page.locator('form[action*="action=change_name"] input[name="name"]').first
            if not await field_.count():
                return ActionResult(False, "rename_village", "formulário de nome não encontrado")

            await field_.click()
            await field_.fill("")
            await human_type(field_, name[:32], delay=70)
            await human_delay(400, 900)
            await self._click_and_settle(
                page, page.locator('form[action*="action=change_name"] input[type="submit"]').first
            )

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "rename_village", " | ".join(messages["errors"]))

        logger.info("Aldeia {} renomeada para {}", village_id, name)
        return ActionResult(
            True,
            "rename_village",
            f"aldeia renomeada para {name}",
            {"notices": messages["notices"]},
        )

    async def assign_flag(self, village_id: str, flag_type: int, level: int) -> ActionResult:
        """Assign an owned flag (type and level from the flags screen) to this village."""
        async with game_session.lock:
            page = await self._in_game(village_id, "flags")

            box = page.locator(f"#flag_box_{flag_type}_{level}:not(.flag_box_empty)")
            if not await box.count():
                return ActionResult(
                    False, "assign_flag", f"bandeira {flag_type}_{level} não disponível"
                )

            title = await box.first.get_attribute("data-title")
            await human_click(page, box.first)
            await human_delay(800, 1500)

            confirm = page.locator("#selected_flag .btn-confirm-yes")
            if await confirm.count():
                await confirm.first.hover()
                await human_delay(300, 700)
                await confirm.first.evaluate("(n) => n.click()")
                await page.wait_for_timeout(2_000)

            second = page.locator(".evt-confirm-btn:visible")
            if await second.count():
                await human_click(page, second.first)
                await page.wait_for_timeout(1_500)

            self._capture(await page.content(), "flag-assign")

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "assign_flag", " | ".join(messages["errors"]))

            await open_screen(page, "flags", village_id)
            current = await page.evaluate(CURRENT_FLAG_JS)

        if not current:
            return ActionResult(False, "assign_flag", f"bandeira não ficou atribuída ({title})")

        logger.info("Bandeira {}_{} atribuída na aldeia {}", flag_type, level, village_id)
        return ActionResult(
            True, "assign_flag", f"bandeira atribuída: {current}", {"notices": messages["notices"]}
        )

    async def learn_knight_skill(self, village_id: str, skill_id: int) -> ActionResult:
        """Spend a paladin skill point on a learnable skill of the statue skill tree."""
        async with game_session.lock:
            page = await self._in_game(village_id, "statue")
            await page.wait_for_timeout(1_500)

            node = page.locator(f'.skill_node.learnable[data-skill="{skill_id}"]')
            if not await node.count():
                return ActionResult(
                    False,
                    "learn_knight_skill",
                    f"habilidade {skill_id} não pode ser aprendida agora",
                )

            await human_click(page, node.first)
            await human_delay(800, 1500)
            self._capture(await page.content(), f"knight-skill-{skill_id}")

            learn = page.locator(
                ".popup_box_container .btn:visible:not(.btn-pp):not(.btn-confirm-no), "
                "#knight_skill_tree .btn:visible:not(.btn-pp)"
            )
            if not await learn.count():
                return ActionResult(False, "learn_knight_skill", "botão de aprender não encontrado")

            await human_click(page, learn.first)
            await page.wait_for_timeout(1_200)

            second = page.locator(".evt-confirm-btn:visible")
            if await second.count():
                await human_click(page, second.first)
                await page.wait_for_timeout(1_200)

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "learn_knight_skill", " | ".join(messages["errors"]))

            await open_screen(page, "statue", village_id)
            await page.wait_for_timeout(1_200)
            learned = await page.locator(
                f'.skill_node[data-skill="{skill_id}"]:not(.learnable):not(.unknown)'
            ).count()

        if not learned:
            return ActionResult(
                False,
                "learn_knight_skill",
                f"habilidade {skill_id} não foi aprendida",
                {"notices": messages["notices"]},
            )

        logger.info("Habilidade de paladino {} aprendida na aldeia {}", skill_id, village_id)
        return ActionResult(
            True,
            "learn_knight_skill",
            f"habilidade {skill_id} aprendida",
            {"notices": messages["notices"]},
        )

    async def train_knight(self, village_id: str, regimen: int) -> ActionResult:
        """Start a paladin XP training with resources (never the premium -20% option)."""
        async with game_session.lock:
            page = await self._in_game(village_id, "statue")

            launch = page.locator(".knight_train_launch")
            if not await launch.count() or not await launch.first.is_visible():
                return ActionResult(False, "train_knight", "paladino indisponível para treino")

            await human_click(page, launch.first)
            await human_delay(800, 1500)

            start = page.locator(
                f'.knight_regimen_confirm[data-regimen="{regimen}"][data-cheap="0"]:not(.btn-pp):not(.btn-disabled)'
            )
            if not await start.count():
                await page.keyboard.press("Escape")
                return ActionResult(
                    False,
                    "train_knight",
                    f"treino {regimen} indisponível (recursos ou paladino ocupado)",
                )

            await human_click(page, start.first)
            await page.wait_for_timeout(1_500)

            second = page.locator(".evt-confirm-btn:visible")
            if await second.count():
                await human_click(page, second.first)
                await page.wait_for_timeout(1_200)

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "train_knight", " | ".join(messages["errors"]))

        logger.info("Treino de paladino {} iniciado na aldeia {}", regimen, village_id)
        return ActionResult(
            True, "train_knight", f"treino {regimen} iniciado", {"notices": messages["notices"]}
        )

    async def flags(self, village_id: str) -> dict[str, Any]:
        """Owned flags (type, level) and the flag assigned to this village."""
        async with game_session.lock:
            page = await self._in_game(village_id, "flags")
            return await page.evaluate(FLAGS_STATE_JS)

    async def knight_state(self, village_id: str) -> dict[str, Any]:
        """What the statue allows right now: learnable skills, recruit and XP training."""
        async with game_session.lock:
            page = await self._in_game(village_id, "statue")
            await page.wait_for_timeout(1_200)
            return await page.evaluate(KNIGHT_STATE_JS)

    async def market_offers(self, village_id: str) -> list[dict[str, Any]]:
        """Other players' offers in the market (never the premium merchant row)."""
        async with game_session.lock:
            page = await self._in_game(village_id, "market", mode="other_offer")
            return await page.evaluate(MARKET_OFFERS_JS) or []

    async def accept_offer(
        self, village_id: str, receive: str, pay: str, amount: int, player: str
    ) -> ActionResult:
        """Accept one unit of a player's offer, matched by resources, amount and player."""
        async with game_session.lock:
            page = await self._in_game(village_id, "market", mode="other_offer")
            offers = await page.evaluate(MARKET_OFFERS_JS) or []

            match = next(
                (
                    o
                    for o in offers
                    if o["receive"] == receive
                    and o["pay"] == pay
                    and o["pay_amount"] == amount
                    and o["player"] == player
                ),
                None,
            )
            if match is None:
                return ActionResult(False, "accept_offer", "oferta não está mais disponível")

            if not match["can_accept"]:
                return ActionResult(
                    False, "accept_offer", "sem recursos ou comerciantes para esta oferta"
                )

            rows = page.locator("#content_value table.vis tr").filter(
                has=page.locator('a[href*="info_player"]')
            )
            row = rows.nth(match["index"])
            count = row.locator('input[name="count"]')
            if await count.count():
                await human_fill(count.first, "1")

            button = row.locator('input[type="submit"]:not(.btn-pp), .btn:not(.btn-pp)').first
            await self._click_and_settle(page, button)
            self._capture(await page.content(), "market-accept")

            confirm = page.locator(".evt-confirm-btn:visible, .btn-confirm-yes:visible")
            if await confirm.count():
                await human_click(page, confirm.first)
                await page.wait_for_timeout(1_500)

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "accept_offer", " | ".join(messages["errors"]))

        logger.info("Oferta aceita: {} {} por {} de {}", amount, receive, pay, player)
        return ActionResult(
            True,
            "accept_offer",
            f"troca aceita: {amount} {pay} por {match['receive_amount']} {receive} ({player})",
            {"notices": messages["notices"], "minutes": match["minutes"]},
        )

    async def market_merchants(self, village_id: str) -> dict[str, int]:
        """Free and total merchants and how much one delivery can carry."""
        async with game_session.lock:
            page = await self._in_game(village_id, "market", mode="own_offer")
            return await page.evaluate(MERCHANTS_JS)

    async def create_offer(
        self, village_id: str, sell: str, amount: int, buy: str, max_hours: int = 5
    ) -> ActionResult:
        """Post an own market offer at the only allowed ratio (1:1): give `amount` of `sell` for the same of `buy`."""
        async with game_session.lock:
            page = await self._in_game(village_id, "market", mode="own_offer")
            merchants = await page.evaluate(MERCHANTS_JS)
            if merchants.get("free", 0) <= 0:
                return ActionResult(False, "create_offer", "nenhum comerciante livre")

            form = page.locator("#own_offer_form")
            if not await form.count():
                return ActionResult(False, "create_offer", "formulário de oferta não encontrado")

            await human_fill(form.locator("#res_sell_amount"), str(amount))
            await form.locator(f"#res_sell_{sell}").check()
            await human_fill(form.locator("#res_buy_amount"), str(amount))
            await form.locator(f"#res_buy_{buy}").check()
            await human_fill(form.locator('input[name="multi"]'), "1")
            await human_fill(form.locator('input[name="max_time"]'), str(max_hours))
            await human_delay(500, 1100)
            await self._click_and_settle(page, form.locator("#submit_offer"))
            self._capture(await page.content(), "market-offer")

            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "create_offer", " | ".join(messages["errors"]))

        logger.info("Oferta no mercado: {} {} por {} {}", amount, sell, amount, buy)
        return ActionResult(
            True,
            "create_offer",
            f"oferta criada: {amount} {sell} por {amount} {buy} (até {max_hours}h)",
            {"notices": messages["notices"]},
        )

    async def smith(self, village_id: str) -> list[dict[str, Any]]:
        """Technologies at the smithy with level, blocking reasons and cost."""
        async with game_session.lock:
            page = await self._in_game(village_id, "smith")
            await page.wait_for_timeout(1_000)
            return await page.evaluate(SMITH_JS) or []

    async def research(self, village_id: str, unit: str) -> ActionResult:
        """Research a unit at the smithy through its research button (never a premium option)."""
        async with game_session.lock:
            page = await self._in_game(village_id, "smith")
            await page.wait_for_timeout(1_000)

            row = page.locator(
                f'tr:has(a.unit_link[data-unit="{unit}"]), div:has(> a.unit_link[data-unit="{unit}"])'
            ).last
            button = row.locator(
                'a.btn-research:visible, a.btn:visible:not(.btn-pp):not(.btn-disabled):has-text("Pesquis"), '
                'button:visible:not(.btn-pp):has-text("Pesquis")'
            )
            if not await button.count():
                self._capture(await page.content(), f"smith-research-{unit}")
                return ActionResult(
                    False, "research", f"botão de pesquisa de {unit} não disponível"
                )

            await human_click(page, button.first)
            await page.wait_for_timeout(1_500)

            confirm = page.locator(".evt-confirm-btn:visible")
            if await confirm.count():
                await human_click(page, confirm.first)
                await page.wait_for_timeout(1_200)

            self._capture(await page.content(), f"smith-research-{unit}")
            messages = await self.screen_messages(page)
            if messages["errors"]:
                return ActionResult(
                    False, "research", " | ".join(messages["errors"]), {"unit": unit}
                )

        logger.info("Pesquisa {} iniciada na aldeia {}", unit, village_id)
        return ActionResult(
            True,
            "research",
            f"pesquisa de {unit} iniciada",
            {"unit": unit, "notices": messages["notices"]},
        )

    async def claim_rewards(self, village_id: str) -> ActionResult:
        """Claim every reward waiting in the "Recompensas" tab (resources land in this village)."""
        async with game_session.lock:
            page = await self._in_game(village_id, "overview")
            if not await self._open_quests(page):
                return ActionResult(False, "claim_rewards", "missões indisponíveis")

            tab = page.locator(f'{QUEST_POPUP} a.tab-link[data-tab="reward-tab"]').first
            if await tab.count():
                await human_click(page, tab)
                await page.wait_for_timeout(700)

            claimed = []
            for _ in range(20):
                button = page.locator(
                    f"{QUEST_POPUP} #reward-tab .reward-system-claim-button:visible"
                ).first
                if not await button.count():
                    break

                claimed.append(await button.get_attribute("data-reward-id"))
                await human_click(page, button)
                await human_delay(700, 1500)

            await self._close_popup(page)

            if not claimed:
                return ActionResult(False, "claim_rewards", "nenhuma recompensa disponível")

            logger.info("{} recompensa(s) resgatada(s)", len(claimed))
            return ActionResult(
                True,
                "claim_rewards",
                f"{len(claimed)} recompensa(s) coletada(s)",
                {"rewards": claimed},
            )
