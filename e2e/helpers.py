from playwright.sync_api import expect


def pick(page, *names):
    """Clear the picker and add exactly these places by name (custom places, so no map click is needed)."""
    page.click("[data-preset=clear]")
    for name in names:
        page.fill("#add-name", name)
        page.click("#add-btn")


def start(page):
    """Press Start once the cost for the current picks is on screen (Start is refused before that)."""
    expect(page.locator("#start")).to_have_attribute("data-cost", "ready")
    page.click("#start")
