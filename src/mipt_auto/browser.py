from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from .config import Config, origin
from .errors import AppError


@contextmanager
def browser_engine(config: Config, *, headless: bool = False):
    config.validate()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless, channel=config.browser_channel or None)
        try:
            yield browser
        finally:
            # Закрытие браузера уничтожает непостоянные cookies/storage всех контекстов.
            browser.close()


@contextmanager
def account_session(browser, config: Config):
    context = browser.new_context(locale="ru-RU", viewport={"width": 1360, "height": 960},
                                  accept_downloads=False)
    try:
        context.set_default_timeout(config.timeout_ms)
        context.set_default_navigation_timeout(config.navigation_timeout_ms)
        page = context.new_page()
        page.goto(config.base_url, wait_until="commit")
        yield context, page
    finally:
        context.close()


@contextmanager
def browser_session(config: Config, *, headless: bool = False):
    with browser_engine(config, headless=headless) as browser:
        with account_session(browser, config) as session:
            yield session


def portal_page(context, config: Config):
    candidates = []
    for page in context.pages:
        try:
            if not page.is_closed() and origin(page.url) == config.portal_origin:
                candidates.append(page)
        except AppError:
            continue
    if not candidates:
        raise AppError("В управляемом окне браузера нет вкладки edu.mipt.ru. Завершите вход именно в окне, открытом приложением, и дождитесь возврата на платформу.")
    if len(candidates) > 1:
        raise AppError("Оставьте одну вкладку платформы, чтобы не выбрать чужую задачу.")
    return candidates[0]


def return_to_portal(context, config: Config):
    """Возврат после Leader ID в том же изолированном контексте."""
    try:
        return portal_page(context, config)
    except AppError:
        pass
    pages = [page for page in context.pages if not page.is_closed()]
    if len(pages) != 1:
        raise AppError("Не удалось определить вкладку после входа. Оставьте одну управляемую вкладку.")
    page = pages[0]
    page.goto(config.base_url, wait_until="commit")
    if origin(page.url) != config.portal_origin:
        raise AppError("После входа платформа не открылась. Проверьте завершение авторизации в управляемом браузере.")
    return page

