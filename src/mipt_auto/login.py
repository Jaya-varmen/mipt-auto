import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from .control import Cancelled
from .errors import AppError


def authenticated_page(context):
    ready = []
    for candidate in context.pages:
        if candidate.is_closed():
            continue
        parsed = urlsplit(candidate.url)
        if parsed.scheme == "https" and parsed.hostname == "edu.mipt.ru":
            # Ссылка наблюдалась в авторизованном кабинете; данных профиля не читаем.
            if candidate.locator("a[href*='/member/profile/meroprijatija/active']").count():
                ready.append(candidate)
    return ready[0] if len(ready) == 1 else None


def wait_for_automatic_login(context, page, control, timeout_seconds=10):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        control.check()
        try:
            ready = authenticated_page(context)
            if ready is not None:
                return ready
            open_page = next((p for p in context.pages if not p.is_closed()), None)
            if open_page is None:
                return None
            open_page.wait_for_timeout(200)
        except PlaywrightError:
            return None
    return None


def provider_locator(page):
    name = re.compile(r"(?:войти\s+с\s+помощью\s+)?университет(?:а)?\s*20[.]35", re.I)
    for candidate in (page.get_by_role("button", name=name),
                      page.get_by_role("link", name=name), page.get_by_text(name)):
        visible = candidate.filter(visible=True)
        if visible.count() == 1:
            return visible
    return page.get_by_role("button", name=name).filter(visible=True)


def prepare_login(context, page, account, vault, control):
    """Обычный вход. Любая неоднозначность оставляет поля для ручного ввода."""
    stage = "кнопка Университета 20.35"
    try:
        provider = provider_locator(page)
        provider.first.wait_for(state="visible", timeout=15000)
        if provider.count() != 1:
            return "Кнопка Университета 20.35 неоднозначна. Нажмите её вручную."
        provider.click()
        stage = "переход к Leader ID"
        deadline = time.monotonic() + 20
        login_page = None
        while time.monotonic() < deadline:
            control.check()
            candidates = [p for p in context.pages if urlsplit(p.url).scheme == "https"
                          and urlsplit(p.url).hostname in {"leader-id.ru", "www.leader-id.ru"}]
            if len(candidates) == 1:
                login_page = candidates[0]
                break
            page.wait_for_timeout(100)
        if login_page is None or not account.get("has_password"):
            return "Выполните вход и CAPTCHA вручную."
        stage = "поля логина и пароля"
        login = login_page.locator('input[type="text"]:visible')
        password_field = login_page.locator('input[type="password"]:visible')
        login.first.wait_for(state="visible", timeout=10000)
        if login.count() != 1 or password_field.count() != 1:
            return "Поля входа требуют ручной проверки. Введите данные в браузере."
        secret = vault.get(account["email"])
        if not secret:
            return "В accounts.csv нет пароля. Введите его в браузере."
        try:
            control.check()
            stage = "поле логина Leader ID"
            login.fill(account["email"], timeout=5000)
            stage = "поле пароля Leader ID"
            password_field.fill(secret, timeout=5000)
        finally:
            secret = None
        button = login_page.get_by_role("button", name=re.compile(r"^Войти$", re.I)).filter(visible=True)
        if button.count() != 1:
            return "Данные заполнены. Кнопка «Войти» не распознана; нажмите её вручную."
        stage = "кнопка входа Leader ID"
        button.click()
        return "Данные входа заполнены. Пройдите CAPTCHA, если она появилась."
    except PlaywrightError:
        return f"Автоматический вход остановился на этапе «{stage}». Продолжите вручную в браузере."
    except Cancelled:
        raise
    except AppError:
        return "Не удалось прочитать accounts.csv. Введите пароль вручную в браузере."
