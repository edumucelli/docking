"""Tests for System Tray presentation state."""

from docking.applets.systemtray.state import tooltip_text
from docking.platform.status_notifier.backend import (
    RegisteredItemAddress,
    tray_item_from_properties,
)
from docking.platform.tray.state import TrayState as StatusTrayState


class TestTooltipText:
    def test_no_error_no_items_no_legacy_unavailable(self):
        text = tooltip_text(
            StatusTrayState(
                available=False,
                mode="unavailable",
                items=(),
                error="",
                legacy_owner="",
            )
        )
        assert "System Tray unavailable" in text

    def test_watcher_mode_no_items_default_message(self):
        text = tooltip_text(
            StatusTrayState(
                available=True,
                mode="watcher",
                items=(),
                legacy_owner="",
            )
        )
        assert "no tray apps" in text

    def test_more_than_six_items_shows_truncation(self):
        items = tuple(
            tray_item_from_properties(
                address=RegisteredItemAddress(service=f"org.ex{i}.App", path="/Item"),
                properties={"Title": f"Item {i}", "Status": "Active"},
            )
            for i in range(8)
        )
        text = tooltip_text(
            StatusTrayState(
                available=True,
                mode="watcher",
                items=items,
            )
        )
        assert "8 item(s)" in text
        assert "2 more" in text
        assert "Item 0" in text
        assert "Item 5" in text
        assert "Item 6" not in text
