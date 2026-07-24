"""Tests for the desktop (show desktop) applet."""

from unittest.mock import MagicMock

from docking.applets.desktop.applet import DesktopApplet
from docking.applets.services import AppletServices
from docking.core.config import Config


class TestDesktopApplet:
    def test_creates_with_icon(self):
        applet = DesktopApplet(48, config=Config())
        assert applet.item.icon is not None
        assert applet.item.name == "Desktop"

    def test_no_menu_items(self):
        applet = DesktopApplet(48, config=Config())
        assert applet.get_menu_items() == []

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = DesktopApplet(size, config=Config())
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_click_uses_desktop_action_service(self):
        service = MagicMock()
        applet = DesktopApplet(48, config=Config())
        applet.set_services(AppletServices(desktop_actions=service))

        applet.on_clicked()

        service.show_desktop.assert_called_once_with()

    def test_click_without_desktop_action_service_is_safe(self):
        applet = DesktopApplet(48, config=Config())

        applet.on_clicked()
