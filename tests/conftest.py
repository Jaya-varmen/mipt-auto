import os

import pytest
from playwright.sync_api import sync_playwright

from mipt_auto.demo import demo_config, demo_server


@pytest.fixture(scope="session")
def demo_url():
    with demo_server() as url:
        yield url


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=os.environ.get("MIPT_BROWSER_CHANNEL") or None)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(locale="ru-RU")
    context.set_default_timeout(2000)
    yield context.new_page()
    context.close()


@pytest.fixture
def config(demo_url):
    config = demo_config(demo_url)
    config.timeout_ms = 2000
    config.result_timeout_ms = 1500
    return config

