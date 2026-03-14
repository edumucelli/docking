"""Tests for the base applet lifecycle contract."""

from docking.applets.base import Applet
from docking.applets.identity import AppletId


class _DeferredInitApplet(Applet):
    id = AppletId.SESSION
    name = "Deferred Init"
    icon_name = "system-log-out"

    def __init__(self) -> None:
        self._label = ""
        self.render_calls = 0
        super().__init__(icon_size=48)
        self._label = "Ready"
        self.present()

    def create_icon(self, size: int):
        assert size == 48
        assert self._label == "Ready"
        self.render_calls += 1

    def refresh_tooltip(self) -> None:
        self.item.name = self._label


class TestAppletBaseLifecycle:
    def test_initial_presentation_waits_for_subclass_init(self):
        applet = _DeferredInitApplet()

        assert applet.render_calls == 1
        assert applet.item.name == "Ready"
        assert applet.item.icon is None
