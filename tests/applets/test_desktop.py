"""Tests for the desktop (show desktop) applet."""

from docking.applets.desktop import DesktopApplet


class TestDesktopApplet:
    def test_creates_with_icon(self):
        applet = DesktopApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Desktop"

    def test_no_menu_items(self):
        applet = DesktopApplet(48)
        assert applet.get_menu_items() == []

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = DesktopApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
