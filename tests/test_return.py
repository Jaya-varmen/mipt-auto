from mipt_auto.browser import return_to_portal


def test_return_to_portal_uses_existing_tab(page, config):
    page.goto(config.base_url)
    assert return_to_portal(page.context, config) == page
