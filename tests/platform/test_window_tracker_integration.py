"""Integration-style tests for WindowTracker control flow."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - fallback for non-GI environments
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.platform.window_tracker as window_tracker_mod  # noqa: E402
from docking.platform.model import DockItem  # noqa: E402


class FakeWindow:
    def __init__(
        self,
        xid: int,
        class_group: str = "App",
        class_instance: str | None = None,
        window_type: int = 0,
        skip_tasklist: bool = False,
        urgent: bool = False,
        minimized: bool = False,
    ) -> None:
        self._xid = xid
        self._class_group = class_group
        self._class_instance = class_instance
        self._window_type = window_type
        self._skip_tasklist = skip_tasklist
        self._urgent = urgent
        self._minimized = minimized
        self.activated_with: list[int] = []
        self.closed_with: list[int] = []
        self.minimize_count = 0
        self.unminimize_count = 0

    def get_window_type(self) -> int:
        return self._window_type

    def is_skip_tasklist(self) -> bool:
        return self._skip_tasklist

    def get_class_group_name(self) -> str:
        return self._class_group

    def get_class_instance_name(self) -> str | None:
        return self._class_instance

    def get_xid(self) -> int:
        return self._xid

    def needs_attention(self) -> bool:
        return self._urgent

    def is_minimized(self) -> bool:
        return self._minimized

    def activate(self, timestamp: int) -> None:
        self.activated_with.append(timestamp)

    def close(self, timestamp: int) -> None:
        self.closed_with.append(timestamp)

    def minimize(self) -> None:
        self.minimize_count += 1

    def unminimize(self, _timestamp: int) -> None:
        self.unminimize_count += 1
        self._minimized = False


class FakeScreen:
    def __init__(self, windows: list[FakeWindow], active_window: FakeWindow | None):
        self._windows = windows
        self._active_window = active_window
        self.force_update_called = 0
        self.connections: list[str] = []

    def force_update(self) -> None:
        self.force_update_called += 1

    def connect(self, signal: str, _callback) -> None:
        self.connections.append(signal)

    def get_windows(self) -> list[FakeWindow]:
        return list(self._windows)

    def get_active_window(self) -> FakeWindow | None:
        return self._active_window


@pytest.fixture
def tracker_env(monkeypatch):
    monkeypatch.setattr(
        window_tracker_mod.Wnck,
        "WindowType",
        SimpleNamespace(DESKTOP=1, DOCK=2),
        raising=False,
    )
    monkeypatch.setattr(window_tracker_mod.GLib, "idle_add", lambda _fn: 1)
    monkeypatch.setattr(window_tracker_mod.Gtk, "get_current_event_time", lambda: 123)

    model = MagicMock()
    model.visible_items.return_value = [
        DockItem(desktop_id="firefox.desktop", wm_class="Firefox"),
        DockItem(desktop_id="code.desktop", wm_class="Code"),
        DockItem(desktop_id="no-class.desktop", wm_class=""),
    ]
    launcher = MagicMock()
    tracker = window_tracker_mod.WindowTracker(model=model, launcher=launcher)
    return tracker, model, launcher


class TestWindowTrackerInit:
    def test_builds_wm_class_map_on_init(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        # When
        # Then
        assert tracker._wm_class_to_desktop == {
            "firefox": "firefox.desktop",
            "code": "code.desktop",
        }

    def test_init_screen_returns_false_when_screen_missing(
        self, tracker_env, monkeypatch
    ):
        # Given
        tracker, _model, _launcher = tracker_env
        monkeypatch.setattr(
            window_tracker_mod.Wnck.Screen,
            "get_default",
            lambda: None,
            raising=False,
        )
        # When
        # Then
        assert tracker._init_screen() is False

    def test_init_screen_connects_signals_and_scans(self, tracker_env, monkeypatch):
        # Given
        tracker, _model, _launcher = tracker_env
        screen = FakeScreen(windows=[], active_window=None)
        monkeypatch.setattr(
            window_tracker_mod.Wnck.Screen,
            "get_default",
            lambda: screen,
            raising=False,
        )
        tracker._update_running = MagicMock()

        # When
        result = tracker._init_screen()
        # Then
        assert result is False
        assert screen.force_update_called == 1
        assert screen.connections == [
            "window-opened",
            "window-closed",
            "active-window-changed",
        ]
        tracker._update_running.assert_called_once()


class TestWindowTrackerRunningAggregation:
    def test_update_running_aggregates_windows(self, tracker_env):
        # Given
        tracker, model, _launcher = tracker_env
        w_desktop = FakeWindow(
            100, window_type=window_tracker_mod.Wnck.WindowType.DESKTOP
        )
        w_skip = FakeWindow(200, skip_tasklist=True)
        w1 = FakeWindow(1, class_group="Firefox")
        w2 = FakeWindow(2, class_group="Firefox", urgent=True)
        w3 = FakeWindow(3, class_group="Code")
        tracker._screen = FakeScreen(
            windows=[w_desktop, w_skip, w1, w2, w3],
            active_window=w2,
        )

        mapping = {w1: "firefox.desktop", w2: "firefox.desktop", w3: "code.desktop"}
        tracker._match_window = MagicMock(
            side_effect=lambda window: mapping.get(window)
        )
        # When
        tracker._update_running()

        # Then
        model.update_running.assert_called_once()
        running = model.update_running.call_args.args[0]
        assert running["firefox.desktop"]["count"] == 2
        assert running["firefox.desktop"]["active"] is True
        assert running["firefox.desktop"]["urgent"] is True
        assert running["firefox.desktop"]["xids"] == [1, 2]
        assert running["code.desktop"]["count"] == 1
        assert tracker._running_xids_by_desktop == {
            "firefox.desktop": [1, 2],
            "code.desktop": [3],
        }

    def test_get_windows_for_uses_cached_xids_and_filters(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        tracker._running_xids_by_desktop = {"firefox.desktop": [1, 2, 3]}
        w1 = FakeWindow(1)
        w2 = FakeWindow(2, window_type=window_tracker_mod.Wnck.WindowType.DOCK)
        w3 = FakeWindow(3, skip_tasklist=True)
        tracker._screen = FakeScreen(windows=[w1, w2, w3], active_window=None)

        # When
        windows = tracker.get_windows_for("firefox.desktop")
        # Then
        assert windows == [w1]


class TestWindowMatching:
    def test_match_uses_direct_class_map(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        win = FakeWindow(10, class_group="Firefox")
        # When
        # Then
        assert tracker._match_window(win) == "firefox.desktop"

    def test_match_uses_class_instance_map(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        tracker._wm_class_to_desktop = {"firefox-bin": "firefox.desktop"}
        win = FakeWindow(11, class_group="Unknown", class_instance="Firefox-Bin")
        # When
        # Then
        assert tracker._match_window(win) == "firefox.desktop"

    def test_match_uses_launcher_candidates_and_caches_result(self, tracker_env):
        # Given
        tracker, _model, launcher = tracker_env
        info = SimpleNamespace(desktop_id="mongodb-compass.desktop")
        launcher.resolve.side_effect = lambda desktop_id: (
            info if desktop_id == "mongodb-compass.desktop" else None
        )
        win = FakeWindow(12, class_group="MongoDB Compass")

        # When
        # Then
        assert tracker._match_window(win) == "mongodb-compass.desktop"
        assert (
            tracker._wm_class_to_desktop["mongodb compass"] == "mongodb-compass.desktop"
        )

    def test_match_uses_gnome_prefix_fallback(self, tracker_env):
        # Given
        tracker, _model, launcher = tracker_env
        info = SimpleNamespace(desktop_id="org.gnome.Terminal.desktop")
        launcher.resolve.side_effect = lambda desktop_id: (
            info if desktop_id == "org.gnome.Terminal.desktop" else None
        )
        win = FakeWindow(13, class_group="Terminal")

        # When
        # Then
        assert tracker._match_window(win) == "org.gnome.Terminal.desktop"

    def test_match_returns_none_for_empty_class_group(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        win = FakeWindow(14, class_group="")
        # When
        # Then
        assert tracker._match_window(win) is None


class TestWindowActions:
    def test_toggle_focus_minimizes_when_one_window_is_active(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        w1 = FakeWindow(1)
        w2 = FakeWindow(2)
        tracker._running_xids_by_desktop = {"firefox.desktop": [1, 2]}
        tracker._screen = FakeScreen(windows=[w1, w2], active_window=w2)

        # When
        tracker.toggle_focus("firefox.desktop")
        # Then
        assert w1.minimize_count == 1
        assert w2.minimize_count == 1

    def test_toggle_focus_activates_first_window_when_not_active(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        w1 = FakeWindow(1)
        w2 = FakeWindow(2)
        other = FakeWindow(99)
        tracker._running_xids_by_desktop = {"firefox.desktop": [1, 2]}
        tracker._screen = FakeScreen(windows=[w1, w2, other], active_window=other)

        # When
        tracker.toggle_focus("firefox.desktop")
        # Then
        assert w1.activated_with == [123]
        assert w2.activated_with == []

    def test_close_all_closes_all_matching_windows(self, tracker_env):
        # Given
        tracker, _model, _launcher = tracker_env
        w1 = FakeWindow(1)
        w2 = FakeWindow(2)
        tracker._running_xids_by_desktop = {"firefox.desktop": [1, 2]}
        tracker._screen = FakeScreen(windows=[w1, w2], active_window=None)

        # When
        tracker.close_all("firefox.desktop")
        # Then
        assert w1.closed_with == [123]
        assert w2.closed_with == [123]
