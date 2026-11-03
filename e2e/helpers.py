from playwright.sync_api import expect

SHEETS = ["peek", "half", "full"]


def on_phone(page):
    """True when the phone layout is in use (the bottom sheet's own buttons are on screen)."""
    return page.locator("#sheet-more").is_visible() or page.locator("#sheet-less").is_visible()


def raise_sheet(page, height="half"):
    """On phones the bottom sheet starts at peek (address, cost and Start); the places and More checks
    show from half, the result details at full. Does nothing on wider screens."""
    while on_phone(page):
        now = page.evaluate("document.body.dataset.sheet")
        if SHEETS.index(now) >= SHEETS.index(height):
            return
        page.click("#sheet-more")
        expect(page.locator("body")).to_have_attribute("data-sheet", SHEETS[SHEETS.index(now) + 1])


def pick(page, *names):
    """Clear the picker and add exactly these places by name (custom places, so no map click is needed)."""
    raise_sheet(page, "half")
    page.click("[data-preset=clear]")
    for name in names:
        page.fill("#add-name", name)
        page.click("#add-btn")


def start(page):
    """Press Start once the cost for the current picks is on screen (Start is refused before that)."""
    expect(page.locator("#start")).to_have_attribute("data-cost", "ready")
    page.click("#start")


def open_details(page):
    """The result details: the Table button on wider screens, the sheet at full height on phones."""
    if on_phone(page):
        raise_sheet(page, "full")
    else:
        page.click("#drawer-toggle")
    expect(page.locator("#drawer")).to_be_visible()
