"""Recent Files provider remains reachable at every real GTK stack edge."""

from unittest.mock import MagicMock, patch

import pytest
from gi.repository import GdkPixbuf, Gtk

from docking.applets.recentfiles.applet import RecentFilesApplet
from docking.applets.recentfiles.state import MAX_ENTRIES, RecentEntry
from docking.core.config import Config
from docking.core.position import Position
from docking.ui.stack import FOLDER_STACK_MAX_VISIBLE_ROWS
from tests.ui.preview_support import settle_gtk
from tests.ui.stack_support import StackHarness

pytestmark = pytest.mark.visual


@pytest.mark.parametrize("position", list(Position))
def test_recentfiles_visible_stack_keeps_last_item_reachable(position):
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display required")
    manager = MagicMock()
    manager.get_items.return_value = []
    loader = MagicMock()
    loader.load_icon.return_value = None
    icon = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 96, 96)
    icon.fill(0x4684DCFF)
    targets = MagicMock()
    targets.resolve_file.return_value = MagicMock(icon=icon)
    with patch(
        "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
        return_value=manager,
    ):
        applet = RecentFilesApplet(
            96, config=Config(), icon_loader=loader, target_service=targets
        )
    applet._entries = [
        RecentEntry(f"file-{index}.txt", f"file:///file-{index}.txt")
        for index in range(MAX_ENTRIES)
    ]
    stack = StackHarness()
    try:
        stack.show(position=position)
        stack.content = applet.stack_content(96)
        assert stack.stack.refresh()
        for _ in range(7):
            settle_gtk()

        stack.assert_bounded()
        assert targets.resolve_file.call_count == FOLDER_STACK_MAX_VISIBLE_ROWS
        assert len(stack.stack._folder_stack_cards) == FOLDER_STACK_MAX_VISIBLE_ROWS
        if stack.scroller is not None:
            stack.scroll_to(True)
        stack.click_label(-1)
        targets.open_target.assert_called_once_with(
            applet._entries[FOLDER_STACK_MAX_VISIBLE_ROWS - 1].uri
        )
        assert not stack.popup.get_visible()
        assert len(applet.get_menu_items()) == MAX_ENTRIES + 2
    finally:
        stack.close()
