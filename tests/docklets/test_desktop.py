"""Tests for the desktop (show desktop) docklet."""

from docking.docklets.desktop import DesktopDocklet


class TestDesktopDocklet:
    def test_creates_with_icon(self):
        docklet = DesktopDocklet(48)
        assert docklet.item.icon is not None
        assert docklet.item.name == "Desktop"

    def test_no_menu_items(self):
        docklet = DesktopDocklet(48)
        assert docklet.get_menu_items() == []

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            docklet = DesktopDocklet(size)
            pixbuf = docklet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
