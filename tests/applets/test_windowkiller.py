"""Tests for the Window Killer applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.windowkiller.applet as windowkiller_applet_mod
from docking.applets.windowkiller import WindowKillerApplet
from docking.applets.windowkiller.state import kill_pid


class TestKillPid:
    def test_returns_false_for_zero(self):
        assert kill_pid(pid=0) is False

    def test_returns_false_for_negative(self):
        assert kill_pid(pid=-1) is False

    def test_returns_false_on_os_error(self, monkeypatch):
        monkeypatch.setattr("os.kill", MagicMock(side_effect=OSError("no process")))
        assert kill_pid(pid=99999) is False

    def test_returns_true_on_success(self, monkeypatch):
        killed = MagicMock()
        monkeypatch.setattr("os.kill", killed)
        assert kill_pid(pid=1234) is True
        killed.assert_called_once()


class TestAppletCreation:
    def test_creates_with_icon(self):
        applet = WindowKillerApplet(48)
        assert applet.item.icon is not None

    def test_tooltip(self):
        applet = WindowKillerApplet(48)
        applet.refresh_tooltip()
        assert "kill" in applet.item.name.lower()

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = WindowKillerApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None


class TestAppletOverlay:
    def test_on_clicked_starts_pick(self, monkeypatch):
        applet = WindowKillerApplet(48)
        pick = MagicMock()
        monkeypatch.setattr(applet, "_start_pick", pick)
        applet.on_clicked()
        pick.assert_called_once()

    def test_stop_dismisses_overlay(self):
        applet = WindowKillerApplet(48)
        overlay = MagicMock()
        applet._overlay = overlay
        applet.stop()
        overlay.destroy.assert_called_once()
        assert applet._overlay is None

    def test_dismiss_without_overlay_is_safe(self):
        applet = WindowKillerApplet(48)
        applet._dismiss_overlay()  # should not raise

    def test_start_pick_noop_when_overlay_exists(self):
        applet = WindowKillerApplet(48)
        marker = object()
        applet._overlay = marker

        applet._start_pick()

        assert applet._overlay is marker

    def test_start_pick_creates_overlay_and_grabs_input(self, monkeypatch):
        applet = WindowKillerApplet(48)
        calls: list[object] = []

        class _Seat:
            def grab(self, *args):
                calls.append("grab")

        class _Display:
            def get_default_seat(self):
                return _Seat()

        class _Screen:
            def get_rgba_visual(self):
                return "rgba"

            def get_width(self):
                return 1920

            def get_height(self):
                return 1080

        class _OverlayWindow:
            def set_cursor(self, cursor):
                calls.append(("cursor", cursor))

        class _Overlay:
            def __init__(self, **_kwargs):
                self.screen = _Screen()
                self.window = _OverlayWindow()

            def set_decorated(self, _value):
                return

            def set_app_paintable(self, _value):
                return

            def get_screen(self):
                return self.screen

            def set_visual(self, visual):
                calls.append(("visual", visual))

            def set_default_size(self, *_args):
                return

            def move(self, *_args):
                return

            def connect(self, *_args):
                return

            def set_events(self, _events):
                return

            def show_all(self):
                return

            def get_window(self):
                return self.window

        monkeypatch.setattr(windowkiller_applet_mod.Gtk, "Window", _Overlay)
        monkeypatch.setattr(
            windowkiller_applet_mod.Gdk.Display, "get_default", lambda: _Display()
        )
        monkeypatch.setattr(
            windowkiller_applet_mod.Gdk.Cursor,
            "new_for_display",
            lambda *_args: "crosshair",
        )

        applet._start_pick()

        assert applet._overlay is not None
        assert ("visual", "rgba") in calls
        assert ("cursor", "crosshair") in calls
        assert "grab" in calls

    def test_overlay_draw_paints_transparent_mask(self):
        applet = WindowKillerApplet(48)
        cr = MagicMock()

        assert applet._on_overlay_draw(MagicMock(), cr) is True
        cr.set_source_rgba.assert_called_once_with(0, 0, 0, 0.01)
        cr.paint.assert_called_once_with()

    def test_overlay_click_kills_selected_window(self, monkeypatch):
        applet = WindowKillerApplet(48)
        dismiss = []
        monkeypatch.setattr(applet, "_dismiss_overlay", lambda: dismiss.append(True))
        screen = SimpleNamespace(force_update=MagicMock())
        target = SimpleNamespace(get_pid=lambda: 123, get_name=lambda: "Firefox")
        logger = SimpleNamespace(info=MagicMock(), warning=MagicMock())
        monkeypatch.setattr(
            windowkiller_applet_mod.Wnck.Screen, "get_default", lambda: screen
        )
        monkeypatch.setattr(applet, "_window_at", lambda **_kwargs: target)
        killed = MagicMock(return_value=True)
        monkeypatch.setattr(windowkiller_applet_mod, "kill_pid", killed)
        monkeypatch.setattr(
            windowkiller_applet_mod.log, "bind", lambda **_kwargs: logger
        )

        event = SimpleNamespace(x_root=10, y_root=20)
        assert applet._on_overlay_click(MagicMock(), event) is True
        assert dismiss == [True]
        screen.force_update.assert_called_once_with()
        killed.assert_called_once_with(pid=123)
        logger.info.assert_called_once()

    def test_overlay_click_warns_when_window_has_no_pid(self, monkeypatch):
        applet = WindowKillerApplet(48)
        monkeypatch.setattr(applet, "_dismiss_overlay", lambda: None)
        screen = SimpleNamespace(force_update=MagicMock())
        target = SimpleNamespace(get_pid=lambda: 0, get_name=lambda: "Nameless")
        logger = SimpleNamespace(info=MagicMock(), warning=MagicMock())
        monkeypatch.setattr(
            windowkiller_applet_mod.Wnck.Screen, "get_default", lambda: screen
        )
        monkeypatch.setattr(applet, "_window_at", lambda **_kwargs: target)
        monkeypatch.setattr(
            windowkiller_applet_mod.log, "bind", lambda **_kwargs: logger
        )

        assert (
            applet._on_overlay_click(MagicMock(), SimpleNamespace(x_root=0, y_root=0))
            is True
        )
        logger.warning.assert_called_once()

    def test_overlay_key_other_key_is_noop(self):
        applet = WindowKillerApplet(48)
        dismiss = []
        applet._dismiss_overlay = lambda: dismiss.append(True)  # type: ignore[method-assign]

        class _Event:
            keyval = 0

        assert applet._on_overlay_key(MagicMock(), _Event()) is True
        assert dismiss == []

    def test_window_at_returns_topmost_normal_window_containing_point(self):
        applet = WindowKillerApplet(48)
        skipped_type = SimpleNamespace(
            get_window_type=lambda: object(),
            is_minimized=lambda: False,
            get_geometry=lambda: (0, 0, 100, 100),
        )
        skipped_minimized = SimpleNamespace(
            get_window_type=lambda: windowkiller_applet_mod.Wnck.WindowType.NORMAL,
            is_minimized=lambda: True,
            get_geometry=lambda: (0, 0, 100, 100),
        )
        target = SimpleNamespace(
            get_window_type=lambda: windowkiller_applet_mod.Wnck.WindowType.NORMAL,
            is_minimized=lambda: False,
            get_geometry=lambda: (10, 10, 60, 60),
        )
        screen = SimpleNamespace(
            get_windows_stacked=lambda: [target, skipped_minimized, skipped_type]
        )

        assert applet._window_at(screen, 20, 20) is target
