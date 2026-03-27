"""Tests for the base applet lifecycle contract."""

from unittest.mock import MagicMock

from docking.applets.base import Applet


class _DeferredInitApplet(Applet):
    id = "session"
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


class _BasicApplet(Applet):
    id = "session"
    name = "Basic"
    icon_name = "system-log-out"

    def __init__(self, config=None) -> None:
        self.render_count = 0
        super().__init__(icon_size=32, config=config)

    def create_icon(self, size: int):
        assert size == 32
        self.render_count += 1
        return object()

    def refresh_tooltip(self) -> None:
        self.item.name = f"Rendered {self.render_count}"


class TestAppletBaseHelpers:
    def test_load_prefs_reads_config_for_applet_id(self):
        config = MagicMock()
        config.applet_prefs = {"session": {"enabled": True}}
        applet = _BasicApplet(config=config)

        assert applet.load_prefs() == {"enabled": True}

    def test_load_prefs_without_config_returns_empty(self):
        applet = _BasicApplet()

        assert applet.load_prefs() == {}

    def test_save_prefs_updates_config_and_calls_save(self):
        config = MagicMock()
        config.applet_prefs = {}
        applet = _BasicApplet(config=config)

        applet.save_prefs({"foo": "bar"})

        assert config.applet_prefs["session"] == {"foo": "bar"}
        config.save.assert_called_once_with()

    def test_default_hooks_are_safe_and_present_notifies(self):
        applet = _BasicApplet()
        notify = MagicMock()

        applet.start(notify)
        applet.present()
        applet.on_clicked()
        applet.on_scroll(True)
        applet.apply_prefs()
        applet.stop()

        assert applet.get_menu_items() == []
        assert applet.item.name == "Rendered 1"
        assert applet.item.icon is not None
        notify.assert_called_once_with()
