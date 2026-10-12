"""Reads the whole account state from the browser and persists it."""

import random
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from loguru import logger
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from sqlalchemy.exc import SQLAlchemyError

from tribal_assistant.core.accounts.context import current_account_id
from tribal_assistant.core.agents.knobs import Knobs, KnobStore
from tribal_assistant.core.agents.learning import LessonBook
from tribal_assistant.core.config import settings
from tribal_assistant.core.db.session import SessionFactory
from tribal_assistant.core.errors import UpstreamError
from tribal_assistant.core.events import event_bus
from tribal_assistant.core.game.actions import GameActions
from tribal_assistant.core.game.browser import (
    BUILD_QUEUE_JS,
    ensure_in_game,
    evaluate_page,
    open_screen,
)
from tribal_assistant.core.game.camera import VillageCamera
from tribal_assistant.core.game.scraper.game import (
    GameSnapshot,
    GameVillage,
    parse_player,
    parse_reports,
    parse_village,
)
from tribal_assistant.core.game.scraper.social import InboxParser
from tribal_assistant.core.game.scraper.spy_report import SpyReportParser
from tribal_assistant.core.game.locale_text import is_battle_report
from tribal_assistant.core.game.screens import ScreenCatalog
from tribal_assistant.core.game.session import game_session
from tribal_assistant.core.game.state import session_state
from tribal_assistant.core.repositories.agent_settings import AgentSettingsRepository
from tribal_assistant.core.repositories.game import GameRepository
from tribal_assistant.core.repositories.lessons import LessonRepository
from tribal_assistant.core.services.timelapse import TimelapseService

GAME_DATA_JS = "() => window.game_data || null"

UPGRADES_JS = """() => {
  const src = (window.BuildingMain && BuildingMain.buildings) || {};
  const out = {};
  for (const [id, b] of Object.entries(src)) {
    out[id] = {level_next: b.level_next, max_level: b.max_level, wood: b.wood, stone: b.stone,
      iron: b.iron, pop: b.pop, build_time: b.build_time, can_build: b.can_build, error: b.error};
  }
  return out;
}"""

TRAIN_JS = r"""() => {
  const units = ((window.game_data && game_data.units) || []).filter(u => u !== 'militia');
  const text = (u, key) => { const e = document.getElementById(`${u}_0_${key}`); return e ? e.innerText.trim() : null; };
  const locked = [...document.querySelectorAll('#content_value table.vis tr')]
    .filter(tr => !tr.querySelector('input.recruit_unit'));
  return units.map(u => {
    const input = document.getElementById(`${u}_0`);
    const row = input ? input.closest('tr')
      : locked.find(tr => tr.querySelector(`[data-unit="${u}"], img[src*="unit_${u}."], img[src*="unit_${u}_"]`));
    let home = null, total = null, requirements = null;
    if (row) {
      const counts = row.innerText.match(/(\d+)\s*\/\s*(\d+)/);
      if (counts) { home = counts[1]; total = counts[2]; }
      if (!input) requirements = [...row.cells].slice(1).map(c => c.innerText.trim()).join(' ').replace(/\s+/g, ' ');
    }
    const max = document.getElementById(`${u}_0_a`);
    return {name: u, available: !!input, home, total, requirements,
      max: max ? (max.innerText.match(/\d+/) || [null])[0] : null,
      wood: text(u, 'cost_wood'), stone: text(u, 'cost_stone'), iron: text(u, 'cost_iron'),
      pop: text(u, 'cost_pop'), time: text(u, 'cost_time')};
  });
}"""

RECRUIT_QUEUE_JS = r"""() => {
  const units = (window.game_data && game_data.units) || [];
  return [...document.querySelectorAll('[id^="trainqueue_"] tr, .trainqueue_wrap tr')]
    .filter(tr => tr.querySelector('[data-endtime]'))
    .map(tr => {
      const sprite = tr.querySelector('[class*="unit_sprite"]');
      const classes = sprite ? sprite.className.split(/\s+/) : [];
      const unit = units.find(u => classes.includes(u)) || units.find(u => tr.innerHTML.includes(`unit_${u}`)) || '';
      return {unit, text: (tr.cells[0] || tr).innerText, end: tr.querySelector('[data-endtime]').dataset.endtime};
    });
}"""

HOME_UNITS_JS = """() => [...document.querySelectorAll('input[id^="unit_input_"]')].map(i => ({
  name: i.name || i.id.slice('unit_input_'.length), count: i.dataset.allCount}))"""

OVERVIEW_JS = """() => {
  const read = (sel, direction) => [...document.querySelectorAll(sel + ' tr')]
    .filter(tr => tr.querySelector('[data-endtime]'))
    .map(tr => {
      const img = tr.querySelector('img[src*="command/"]');
      const idEl = tr.querySelector('[data-command-id], .quickedit[data-id], .quickedit-in[data-id], .quickedit-out[data-id]');
      const hintEl = tr.querySelector('[data-icon-hint]');
      const village = tr.querySelector('a[href*="info_village"]');
      const player = tr.querySelector('a[href*="info_player"]');
      return {direction, id: idEl ? (idEl.dataset.commandId || idEl.dataset.id) : null, icon: img ? img.getAttribute('src') : '',
        hint: hintEl ? hintEl.dataset.iconHint : '', type: hintEl ? (hintEl.dataset.commandType || '') : '',
        origin: village ? village.innerText : '', player: player ? player.innerText : '',
        watchtower: /watchtower/i.test(tr.innerHTML),
        text: (tr.cells[0] || tr).innerText, end: tr.querySelector('[data-endtime]').dataset.endtime};
    });
  const banner = document.querySelector('#show_newbie') || document.querySelector('#content_value');
  return {commands: [...read('#show_incoming_units', 'in'), ...read('#show_outgoing_units', 'out')],
    text: banner ? banner.innerText : ''};
}"""

INFO_COMMAND_JS = """() => {
  const root = document.querySelector('#content_value') || document.body;
  const village = root.querySelector('a[href*="info_village"]');
  const player = root.querySelector('a[href*="info_player"]');
  const hint = root.querySelector('[data-icon-hint]');
  return {origin: village ? village.innerText : '', player: player ? player.innerText : '',
    hint: hint ? hint.dataset.iconHint : '', watchtower: /watchtower/i.test(root.innerHTML)};
}"""

SCAVENGE_JS = """() => {
  const s = window.ScavengeScreen;
  if (!s || !s.village || !s.village.options) return [];
  const clean = v => v == null ? null : JSON.parse(JSON.stringify(v, (k, x) => k.startsWith('_') ? undefined : x));
  return Object.values(s.village.options).map(o => ({id: o.base.id, name: o.base.name,
    loot_factor: o.base.loot_factor, locked: o.is_locked, unlock_time: o.unlock_time,
    squad: clean(o.scavenging_squad)}));
}"""

VILLAGE_IDS_JS = """() => [...new Set([...document.querySelectorAll('#production_table .quickedit-vn')]
  .map(e => e.dataset.id).filter(Boolean))]"""

REPORTS_JS = r"""() => [...document.querySelectorAll('#report_list tr')]
  .filter(tr => tr.querySelector('.quickedit[data-id]'))
  .map(tr => {
    const q = tr.querySelector('.quickedit[data-id]');
    return {id: q.dataset.id, title: q.innerText, received: tr.cells[tr.cells.length - 1].innerText,
      icons: [...tr.querySelectorAll('img')].map(i => i.getAttribute('src')).join(' '),
      is_new: /\((?:nov[áaéey]|new|novo)\)/i.test(tr.innerText) || tr.classList.contains("unread")};
  })"""

REPORT_DETAIL_JS = r"""() => {
  const text = s => { const e = document.querySelector(s); return e ? e.innerText : ''; };
  const html = s => [...document.querySelectorAll(s)].map(e => e.outerHTML).join('');
  const results = document.querySelector('#attack_results');
  let loot = [], haul = '';
  if (results) {
    const row = [...results.querySelectorAll('tr')].find(r => /saque|koris[tť]|ukorist|l[uú]p|loot|plunder/i.test(r.innerText));
    if (row) {
      loot = [...row.querySelectorAll('.nowrap')].map(e => e.innerText);
      haul = (row.innerText.match(/\d+\s*\/\s*\d+/) || [''])[0];
    }
  }
  return {attacker: text('#attack_info_att'), defender: text('#attack_info_def'), loot, haul,
    sections: html('#attack_spy_resources, #attack_spy_building_data, [id^="attack_spy_buildings"], #attack_info_att_units, #attack_info_def_units')};
}"""

REPORT_DETAILS_PER_SYNC = 5
TEXTS_PER_SYNC = 5

CONTENT_TEXT_JS = """() => {
  const root = document.querySelector('#content_value') || document.body;
  const clone = root.cloneNode(true);
  clone.querySelectorAll('script, style, .vis_item, #report_list').forEach(n => n.remove());
  return clone.innerText.replace(/\\s+/g, ' ').trim().slice(0, 4000);
}"""



async def _game_data(page: Page) -> dict[str, Any]:
    data = await evaluate_page(page, GAME_DATA_JS)
    if not data:
        raise UpstreamError(f"game_data ausente em {page.url}")
    return data


async def _own_village_ids(page: Page, game_data: dict[str, Any], count: int) -> list[str]:
    current = str(game_data["village"]["id"])
    if count <= 1:
        return [current]
    await open_screen(page, "overview_villages", current, mode="prod")
    ids = await evaluate_page(page, VILLAGE_IDS_JS)
    return ids or [current]


async def _read_village(
    page: Page, village_id: str, finish_free: bool = False, camera: VillageCamera | None = None
) -> tuple[GameVillage, str]:
    async def overview() -> dict[str, Any]:
        await open_screen(page, "overview", village_id)
        data = await evaluate_page(page, OVERVIEW_JS)
        if camera is not None:
            await camera.capture(page, village_id)
        await IncomingDetails.enrich(page, village_id, data.get("commands") or [])
        return {"overview": data}

    async def main() -> dict[str, Any]:
        await open_screen(page, "main", village_id)

        if finish_free:
            await GameActions().click_free_finish(page)

        return {
            "game_data": await _game_data(page),
            "upgrades": await evaluate_page(page, UPGRADES_JS),
            "build_queue": await evaluate_page(page, BUILD_QUEUE_JS),
        }

    async def train() -> dict[str, Any]:
        await open_screen(page, "train", village_id)
        return {
            "units": await evaluate_page(page, TRAIN_JS),
            "recruit": await evaluate_page(page, RECRUIT_QUEUE_JS),
        }

    async def place() -> dict[str, Any]:
        await open_screen(page, "place", village_id)
        return {"home": await evaluate_page(page, HOME_UNITS_JS)}

    async def scavenge() -> dict[str, Any]:
        await open_screen(page, "place", village_id, mode="scavenge")
        return {"scavenge": await evaluate_page(page, SCAVENGE_JS)}

    readers = [overview, main, train, place, scavenge]
    random.shuffle(readers)
    data: dict[str, Any] = {}
    for reader in readers:
        data.update(await reader())

    village = parse_village(
        data["game_data"],
        data["upgrades"],
        data["build_queue"],
        data["units"],
        home_rows=data["home"],
        recruit_rows=data["recruit"],
        command_rows=data["overview"]["commands"],
        scavenge_rows=data["scavenge"],
    )

    catalog = ScreenCatalog()
    missing = catalog.missing({b.name: b.level for b in village.buildings})
    for name in missing[:3]:
        try:
            await catalog.capture(page, village_id, name)
            Findings.screens().append(name)
        except PlaywrightError as exc:
            logger.warning("Não foi possível capturar a tela {}: {}", name, exc)

    return village, data["overview"]["text"]


class Findings:
    """Screens captured and texts read during a sync, kept apart per account until they become lessons."""

    captured: ClassVar[dict[int | None, list[str]]] = {}
    learned: ClassVar[dict[int | None, list[tuple[Any, ...]]]] = {}

    @classmethod
    def screens(cls) -> list[str]:
        return cls.captured.setdefault(current_account_id(), [])

    @classmethod
    def texts(cls) -> list[tuple[Any, ...]]:
        return cls.learned.setdefault(current_account_id(), [])

    @classmethod
    def clear(cls) -> None:
        cls.captured.pop(current_account_id(), None)
        cls.learned.pop(current_account_id(), None)


class ReportIntel:
    """Scouting and troop details read from report pages, per account, until they reach the target lessons."""

    found: ClassVar[dict[int | None, dict[str, dict[str, Any]]]] = {}

    @classmethod
    def keep(cls, report_id: str, detail: dict[str, Any]) -> None:
        intel = SpyReportParser.parse(str(detail.pop("sections", "") or ""))
        if intel:
            cls.found.setdefault(current_account_id(), {})[report_id] = intel

    @classmethod
    def take(cls) -> dict[str, dict[str, Any]]:
        return cls.found.pop(current_account_id(), {})


class IncomingDetails:
    """Origin and attacker of incoming attacks, read once per command from its info page."""

    PER_SYNC = 3
    FIELDS = ("origin", "player", "hint", "watchtower")
    known: ClassVar[dict[str, dict[str, Any]]] = {}

    @classmethod
    def merge(cls, row: dict[str, Any], detail: dict[str, Any]) -> None:
        for key in cls.FIELDS:
            if detail.get(key) and not row.get(key):
                row[key] = detail[key]

    @classmethod
    async def enrich(cls, page: Page, village_id: str, rows: list[dict[str, Any]]) -> None:
        budget = cls.PER_SYNC
        for row in rows:
            if row.get("direction") != "in" or not row.get("id") or row.get("origin"):
                continue

            key = f"{current_account_id()}:{row['id']}"
            if key not in cls.known and budget > 0:
                budget -= 1
                try:
                    await open_screen(page, "info_command", village_id, id=str(row["id"]), type="other")
                    cls.known[key] = await evaluate_page(page, INFO_COMMAND_JS) or {}
                except PlaywrightError as exc:
                    logger.debug("Não foi possível ler o comando recebido {}: {}", row["id"], exc)
                    continue

            cls.merge(row, cls.known.get(key, {}))


class ReportClock:
    """Reads the report list at least every few minutes, even when the game shows no new ones."""

    EVERY = timedelta(minutes=10)
    last: ClassVar[dict[int | None, datetime]] = {}

    @classmethod
    def due(cls) -> bool:
        last = cls.last.get(current_account_id())
        return last is None or datetime.now(UTC) - last >= cls.EVERY

    @classmethod
    def mark(cls) -> None:
        cls.last[current_account_id()] = datetime.now(UTC)


async def _read_reports(page: Page, village_id: str, known: set[str]) -> list[dict[str, Any]]:
    await open_screen(page, "report", village_id, mode="all")
    rows: list[dict[str, Any]] = await evaluate_page(page, REPORTS_JS)
    if not settings.sync_report_details:
        return rows

    pending = [
        r for r in rows if r["id"] not in known and is_battle_report(str(r.get("title", "")))
    ][:REPORT_DETAILS_PER_SYNC]
    for row in pending:
        await open_screen(page, "report", village_id, mode="all", view=row["id"])
        row["detail"] = await evaluate_page(page, REPORT_DETAIL_JS)
        if isinstance(row["detail"], dict):
            ReportIntel.keep(row["id"], row["detail"])
    return rows


async def _read_texts(page: Page, village_id: str, report_rows: list[dict[str, Any]], known: set[str]) -> None:
    """Open unread reports of any kind and messages, and keep their full text for learning."""
    budget = TEXTS_PER_SYNC

    for row in report_rows:
        if budget <= 0:
            break

        key = f"report:{row['id']}"
        if key in known:
            continue

        try:
            await open_screen(page, "report", village_id, mode="all", view=row["id"])
            Findings.texts().append((key, "report", str(row.get("title", ""))[:255], await evaluate_page(page, CONTENT_TEXT_JS)))
            budget -= 1
        except PlaywrightError as exc:
            logger.debug("Não foi possível ler o relatório {}: {}", row["id"], exc)

    try:
        await open_screen(page, "mail", village_id)
        mails = InboxParser.inbox(await page.content())
    except PlaywrightError:
        mails = []

    for mail in mails:
        if budget <= 0:
            break

        key = f"mail:{mail['id']}"
        if key in known:
            continue

        try:
            await open_screen(page, "mail", village_id, mode="view", view=mail["id"])
            thread = InboxParser.thread(await page.content())
            transcript = "\n".join(f"{m['author'] or '?'} ({m['date'] or '-'}): {m['text']}" for m in thread["messages"])
            Findings.texts().append((key, "mail", mail["subject"][:255], transcript, {**mail, "messages": thread["messages"]}))
            budget -= 1
        except PlaywrightError as exc:
            logger.debug("Não foi possível ler a mensagem {}: {}", mail["id"], exc)


async def _read_game(
    known_reports: set[str],
    finish_free: bool = False,
    learned: set[str] | None = None,
    camera: VillageCamera | None = None,
) -> GameSnapshot:
    page = await game_session.page()
    await ensure_in_game(page)

    game_data = await _game_data(page)
    first = parse_player(game_data)
    village_ids = await _own_village_ids(page, game_data, first.villages)

    villages = []
    overview_text = ""
    for village_id in village_ids:
        village, text = await _read_village(page, village_id, finish_free, camera)
        villages.append(village)
        overview_text = overview_text or text

    report_rows: list[dict[str, Any]] = []
    if first.new_reports or not known_reports or ReportClock.due():
        report_rows = await _read_reports(page, village_ids[0], known_reports)
        ReportClock.mark()
        await _read_texts(page, village_ids[0], report_rows, learned or set())
    player = parse_player(await _game_data(page), overview_text)

    await game_session.save_state()
    return GameSnapshot(
        player=player,
        villages=tuple(villages),
        reports=parse_reports(
            report_rows, {r["id"]: r["detail"] for r in report_rows if r.get("detail")}
        ),
    )


async def _keep_frames(snapshot: GameSnapshot, camera: VillageCamera, knobs: Knobs) -> None:
    if not camera.shots:
        return

    try:
        async with SessionFactory() as session:
            kept = await TimelapseService(session).record(snapshot.villages, camera.shots, knobs)
    except SQLAlchemyError as exc:
        logger.warning("Não foi possível guardar as fotos do timelapse: {}", exc)
        return

    if kept:
        logger.info("{} foto(s) da aldeia guardadas no timelapse", kept)


async def sync_game() -> GameSnapshot:
    async with game_session.lock:
        try:
            async with SessionFactory() as session:
                known = await GameRepository(session).report_ids()
                config = await AgentSettingsRepository(session).get()

                learned = await LessonRepository(session).keys(("report", "mail"))
                knobs = await KnobStore(session).load()
            camera = TimelapseService.camera(knobs)
            snapshot = await _read_game(known, config.auto_finish_free, learned, camera)
            async with SessionFactory() as session:
                await GameRepository(session).persist(snapshot)

                book = LessonBook(session)
                await book.reports(snapshot.reports, known, ReportIntel.take())
                await book.screens(ScreenCatalog(), Findings.screens())
                await book.texts(Findings.texts())
                Findings.clear()
            await _keep_frames(snapshot, camera, knobs)
        except Exception as exc:
            session_state.logged_in = False
            session_state.last_error = str(exc)
            raise
    session_state.logged_in = True
    session_state.last_error = None
    session_state.last_sync_at = datetime.now(UTC)
    event_bus.publish("sync", {"villages": len(snapshot.villages), "reports": len(snapshot.reports)})
    logger.info(
        "{} aldeia(s) e {} relatório(s) sincronizados", len(snapshot.villages), len(snapshot.reports)
    )
    return snapshot
