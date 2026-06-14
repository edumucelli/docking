from docking.applets.tooltip import structured_tooltip


def test_structured_tooltip_orders_sections():
    text = structured_tooltip(
        title="Title",
        primary="Primary",
        details=("Detail",),
        freshness=("Updated: now",),
        error="failed",
        recovery="Retry from menu",
    )

    assert text.splitlines() == [
        "Title",
        "Primary",
        "Detail",
        "Updated: now",
        "Error: failed",
        "Retry from menu",
    ]


def test_structured_tooltip_skips_empty_lines():
    assert structured_tooltip(title="Title", details=("", None)) == "Title"
