import pytest

from mipt_auto.login import provider_locator


@pytest.mark.browser
def test_member_provider_button_is_found(page, demo_url):
    page.goto(demo_url)
    page.set_content('<button>Войти с помощью Университета 20.35</button>')
    assert provider_locator(page).count() == 1
    assert provider_locator(page).inner_text() == 'Войти с помощью Университета 20.35'
