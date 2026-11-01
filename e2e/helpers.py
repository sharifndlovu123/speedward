def pick(page, *names):
    """Clear the picker and add exactly these places by name (custom places, so no map click is needed)."""
    page.click("[data-preset=clear]")
    for name in names:
        page.fill("#add-name", name)
        page.click("#add-btn")
