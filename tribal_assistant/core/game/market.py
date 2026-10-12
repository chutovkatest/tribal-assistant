"""Own market offers: read them, park a surplus behind an offer and take it back. Never touches the premium exchange."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup, Tag
from loguru import logger

from tribal_assistant.core.game.human import human_delay, human_fill, human_type
from tribal_assistant.core.game.result import ActionResult
from tribal_assistant.core.game.session import game_session

if TYPE_CHECKING:
    from tribal_assistant.core.game.actions import GameActions

RESOURCES = ("wood", "stone", "iron")
DEFAULT_RATIO = (0.9, 1.1)
LOAD = 1000
RATIO_RE = re.compile(r"ratio_(min|max)\s*=\s*([^;]+);")
TERM_RE = re.compile(r"[+-]?[\d.]+")


class OwnOfferParser:
    """HTML of the market's own-offer screens in, plain dicts out."""

    @staticmethod
    def _number(text: str) -> int:
        digits = re.sub(r"\D", "", text)
        return int(digits) if digits else 0

    @classmethod
    def _goods(cls, cell: Tag) -> tuple[str, int] | None:
        icon = cell.select_one(".icon.header")
        if icon is None:
            return None

        resource = next((c for c in icon.get("class", []) if c in RESOURCES), None)
        if resource is None:
            return None

        return resource, cls._number(cell.get_text())

    @classmethod
    def offers(cls, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        items = []
        for row in soup.select("tr.offer_container[data-id]"):
            goods = [g for g in (cls._goods(td) for td in row.find_all("td")) if g]
            if len(goods) < 2:
                continue

            offer_id = str(row["data-id"])
            (sell, sell_amount), (buy, buy_amount) = goods[:2]
            hours = soup.select_one(f"#offerText_{offer_id}")
            items.append(
                {
                    "id": offer_id,
                    "sell": sell,
                    "sell_amount": sell_amount,
                    "buy": buy,
                    "buy_amount": buy_amount,
                    "count": int(row.get("data-count") or 1),
                    "max_hours": cls._number(hours.get_text()) if hours else None,
                    "village": row.get("data-village") or None,
                }
            )

        return items

    @staticmethod
    def ratio(html: str) -> tuple[float, float]:
        """Lowest and highest buy/sell ratio the world allows for own offers."""
        found = {name: sum(float(t) for t in TERM_RE.findall(expr.replace(" ", ""))) for name, expr in RATIO_RE.findall(html)}
        low, high = DEFAULT_RATIO
        return round(found.get("min", low), 4), round(found.get("max", high), 4)

    @classmethod
    def merchants(cls, html: str) -> dict[str, int]:
        soup = BeautifulSoup(html, "lxml")
        free = soup.select_one("#market_merchant_available_count")
        total = soup.select_one("#market_merchant_total_count")
        carry = re.search(r"carry\s*:\s*(\d+)", html)
        return {
            "free": cls._number(free.get_text()) if free else 0,
            "total": cls._number(total.get_text()) if total else 0,
            "carry": int(carry.group(1)) if carry else LOAD,
        }


class SendParser:
    """The market's send screen and its confirmation: merchants, stock the form offers and who receives."""

    INSERT_RE = re.compile(r"\((\d[\d.]*)\)")
    COORDS_RE = re.compile(r"\((\d{1,3}\|\d{1,3})\)")

    @classmethod
    def form(cls, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "lxml")
        form = soup.select_one("#market-send-form")
        merchants = OwnOfferParser.merchants(html)
        limit = soup.select_one("#market_merchant_max_transport") or soup.select_one("#carry_max")
        stock = {}
        for link in soup.select("a.insert[data-res]"):
            found = cls.INSERT_RE.search(link.get_text())
            stock[str(link["data-res"])] = OwnOfferParser._number(found.group(1)) if found else 0

        return {
            "ready": form is not None and all(form.select_one(f'input[name="{r}"]') for r in RESOURCES),
            "free": merchants["free"],
            "total": merchants["total"],
            "carry": merchants["carry"],
            "max_transport": OwnOfferParser._number(limit.get("value") or limit.get_text()) if limit else merchants["free"] * merchants["carry"],
            "stock": stock,
        }

    @classmethod
    def confirmation(cls, html: str) -> dict[str, Any]:
        """Target coordinates and owner shown before the final click."""
        soup = BeautifulSoup(html, "lxml")
        content = soup.select_one("#content_value") or soup
        text = content.get_text(" ", strip=True)
        coords = cls.COORDS_RE.findall(text)
        player = None
        for row in content.select("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2 and cells[0].get_text(strip=True).rstrip(":").lower() in ("jogador", "proprietário", "dono", "hráč", "hrac", "vlastník", "vlastnik", "player", "owner"):
                player = cells[1].get_text(" ", strip=True)

        return {"coords": coords, "player": player}


class Market:
    """Own offers of one village, through the market's own-offer screen."""

    def __init__(self, actions: GameActions) -> None:
        self.actions = actions

    async def _screen(self, village_id: str, mode: str = "own_offer") -> tuple[Any, str]:
        page = await self.actions._in_game(village_id, "market", mode=mode)
        return page, await page.content()

    async def list_own_offers(self, village_id: str) -> list[dict[str, Any]]:
        async with game_session.lock:
            _, html = await self._screen(village_id)
            return OwnOfferParser.offers(html)

    async def cancel_offer(self, village_id: str, offer_id: str) -> ActionResult:
        async with game_session.lock:
            page, html = await self._screen(village_id)
            offer = next((o for o in OwnOfferParser.offers(html) if o["id"] == str(offer_id)), None)
            if offer is None:
                return ActionResult(False, "cancel_market_offer", f"oferta {offer_id} não está no mercado desta aldeia")

            await page.locator(f'#own_offers_table input[name="id_{offer_id}"]').first.check()
            await human_delay(400, 900)
            await self.actions._click_and_settle(page, page.locator('input[name="delete"]').first)
            self.actions._capture(await page.content(), "market-cancel")

            messages = await self.actions.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "cancel_market_offer", " | ".join(messages["errors"]))

            _, html = await self._screen(village_id)
            if any(o["id"] == str(offer_id) for o in OwnOfferParser.offers(html)):
                return ActionResult(False, "cancel_market_offer", "a oferta continua no mercado")

        amount = offer["sell_amount"] * offer["count"]
        logger.info("Oferta {} cancelada: {} {} devolvidos", offer_id, amount, offer["sell"])
        return ActionResult(
            True,
            "cancel_market_offer",
            f"oferta {offer_id} cancelada: {amount} {offer['sell']} de volta ao armazém",
            {"offer": offer, "notices": messages["notices"]},
        )

    async def park(self, village_id: str, sell: str, amount: int, buy: str, lots: int, max_hours: int = 1) -> ActionResult:
        """Own offers asking the highest ratio the world allows, so the resource waits in the merchants, safe from looting."""

        async with game_session.lock:
            page, html = await self._screen(village_id)
            merchants = OwnOfferParser.merchants(html)
            needed = -(-amount // (merchants["carry"] or LOAD)) * lots
            if merchants["free"] < needed:
                return ActionResult(False, "park_market_offer", f"comerciantes livres {merchants['free']}, precisa de {needed}")

            form = page.locator("#own_offer_form")
            if not await form.count():
                return ActionResult(False, "park_market_offer", "formulário de oferta não encontrado")

            _, high = OwnOfferParser.ratio(html)
            wanted = int(amount * high + 1e-6)
            await human_fill(form.locator("#res_sell_amount"), str(amount))
            await form.locator(f"#res_sell_{sell}").check()
            await human_fill(form.locator("#res_buy_amount"), str(wanted))
            await form.locator(f"#res_buy_{buy}").check()
            await human_fill(form.locator('input[name="multi"]'), str(lots))
            await human_fill(form.locator('input[name="max_time"]'), str(max_hours))
            await human_delay(500, 1100)
            await self.actions._click_and_settle(page, form.locator("#submit_offer"))
            after = await page.content()
            self.actions._capture(after, "market-park")

            messages = await self.actions.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "park_market_offer", " | ".join(messages["errors"]))

        parked = [o for o in OwnOfferParser.offers(after) if o["sell"] == sell and o["buy"] == buy and o["buy_amount"] == wanted]
        logger.info("Estacionadas {}x {} {} pedindo {} {}", lots, amount, sell, wanted, buy)
        return ActionResult(
            True,
            "park_market_offer",
            f"{lots}x {amount} {sell} estacionado(s) pedindo {wanted} {buy} (até {max_hours}h)",
            {"offers": [o["id"] for o in parked], "buy_amount": wanted, "notices": messages["notices"]},
        )

    async def send_resources(self, village_id: str, x: int, y: int, amounts: dict[str, int], owner: str | None = None) -> ActionResult:
        """Send resources to one of our own villages: fill the send form, check the confirmation shows our village, confirm."""

        target = f"{x}|{y}"
        fields = {"wood": amounts.get("wood", 0), "stone": amounts.get("clay", 0), "iron": amounts.get("iron", 0)}
        if sum(fields.values()) <= 0:
            return ActionResult(False, "send_resources", "nenhum recurso informado")

        async with game_session.lock:
            page, html = await self._screen(village_id, "send")
            form = SendParser.form(html)
            if not form["ready"]:
                return ActionResult(False, "send_resources", "formulário de envio não encontrado")

            needed = -(-sum(fields.values()) // (form["carry"] or LOAD))
            if form["free"] < needed:
                return ActionResult(False, "send_resources", f"comerciantes livres {form['free']}, precisa de {needed}")

            short = [r for r, v in fields.items() if v > form["stock"].get(r, v)]
            if short:
                return ActionResult(False, "send_resources", "estoque mudou: falta " + ", ".join(short))

            sender = page.locator("#market-send-form")
            for resource, amount in fields.items():
                if amount > 0:
                    await human_fill(sender.locator(f'input[name="{resource}"]'), str(amount))
                    await human_delay(200, 500)

            await sender.locator('input[name="target_type"][value="coord"]').check()
            coords = sender.locator("input.target-input-field").first
            await coords.click()
            await human_type(coords, target, delay=70)
            await page.evaluate("([x, y]) => { const ix = document.querySelector('#inputx'); const iy = document.querySelector('#inputy'); if (ix) ix.value = x; if (iy) iy.value = y; }", [str(x), str(y)])
            await human_delay(400, 900)
            await self.actions._click_and_settle(page, sender.locator('input[type="submit"]').first)

            confirm_html = await page.content()
            self.actions._capture(confirm_html, "market-send-confirm")
            messages = await self.actions.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "send_resources", " | ".join(messages["errors"]))

            shown = SendParser.confirmation(confirm_html)
            if target not in shown["coords"]:
                return ActionResult(False, "send_resources", f"confirmação não mostra o destino {target}")

            if owner and shown["player"] and owner.lower() not in shown["player"].lower():
                return ActionResult(False, "send_resources", f"destino pertence a {shown['player']}, não a {owner}")

            button = page.locator('#content_value form[action*="action=send"] input[type="submit"], #content_value form[action*="action=send"] .btn').first
            if not await button.count():
                return ActionResult(False, "send_resources", "botão de confirmação não encontrado")

            await human_delay(500, 1100)
            await self.actions._click_and_settle(page, button)
            messages = await self.actions.screen_messages(page)
            if messages["errors"]:
                return ActionResult(False, "send_resources", " | ".join(messages["errors"]))

        logger.info("Enviados {} de {} para {}", fields, village_id, target)
        return ActionResult(
            True,
            "send_resources",
            f"enviado {fields['wood']} madeira, {fields['stone']} argila, {fields['iron']} ferro para {target}",
            {"target": target, "amounts": amounts, "merchants": needed, "notices": messages["notices"]},
        )
