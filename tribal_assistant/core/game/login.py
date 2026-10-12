"""Login + world select."""

from loguru import logger
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tribal_assistant.core.accounts.context import current_account
from tribal_assistant.core.errors import UpstreamError
from tribal_assistant.core.game.human import human_delay

LOGIN_URL = "https://www.divoke-kmene.sk/"
PLAY_URL = LOGIN_URL + "page/play/{server}"

LOGIN_FORM_SELECTOR = "#login_form #user"
VILLAGE_MENU_SELECTOR = "#menu_row2_village"

LOGIN_TIMEOUT_MS = 120_000
WORLD_TIMEOUT_MS = 30_000


async def visit(page: Page, url: str) -> None:
    """Open a page; the site redirecting an active session straight into the game is a success, not an error."""
    try:
        await page.goto(url, wait_until="domcontentloaded")
    except PlaywrightError as exc:
        if "interrupted by another navigation" not in str(exc):
            raise
        logger.info("Site redirecionou a sessão ativa para {}", page.url.split("?")[0])
        await page.wait_for_load_state("domcontentloaded")


async def login(page: Page) -> None:
    if page.url.startswith(current_account().base_url) and await page.locator(VILLAGE_MENU_SELECTOR).count():
        return

    await visit(page, LOGIN_URL)
    if page.url.startswith(current_account().base_url) and await page.locator(VILLAGE_MENU_SELECTOR).count():
        logger.info("Sessão já ativa: entrou direto no jogo")
        return
    await human_delay()

    if await page.locator(LOGIN_FORM_SELECTOR).count():
        await _submit_credentials(page)
    else:
        logger.info("Sessão já ativa, formulário de login ignorado")

    await human_delay()
    await _enter_world(page)


async def _submit_credentials(page: Page) -> None:
    logger.info("Preenchendo credenciais")
    await page.fill("#user", current_account().username)
    await human_delay(400, 900)
    await page.fill("#password", current_account().password)
    await human_delay(400, 900)
    await page.click("a.btn-login")

    logger.info("Aguardando captcha/login (até {}s)", LOGIN_TIMEOUT_MS // 1000)
    try:
        await page.wait_for_selector(LOGIN_FORM_SELECTOR, state="detached", timeout=LOGIN_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        raise UpstreamError(
            "Login não concluiu em 2 min: captcha não resolvido ou usuário/senha inválidos"
        ) from exc
    await page.wait_for_load_state("domcontentloaded")


async def _enter_world(page: Page) -> None:
    if await page.locator(VILLAGE_MENU_SELECTOR).count():
        return

    server = current_account().server
    logger.info("Entrando no mundo {}", server)
    await visit(page, PLAY_URL.format(server=server))
    try:
        await page.wait_for_selector(VILLAGE_MENU_SELECTOR, timeout=WORLD_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        raise UpstreamError(
            f"Não entrou no mundo {server} (url atual: {page.url}). "
            "Confira TW_SERVER e se a conta tem aldeia nesse mundo."
        ) from exc
