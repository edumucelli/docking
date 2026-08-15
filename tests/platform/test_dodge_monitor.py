"""Tests for window dodge monitor evaluation logic."""

import sys
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.config import Config
from docking.platform.backends.x11.impl.dodge import ScreenRect, WindowDodgeMonitor

# Dock sits at bottom of 1920x1080 screen
DOCK_RECT = ScreenRect(x=600, y=1030, width=720, height=50)


def _make_window(
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    class_group: str = "App",
    pid: int | None = None,
    minimized: bool = False,
    maximized: bool = False,
    maximized_vertically: bool = False,
    maximized_horizontally: bool = False,
    win_type=None,
    visible_on_workspace: bool = True,
):
    """Create a mock Wnck.Window."""
    win = MagicMock()
    win.get_geometry.return_value = (x, y, w, h)
    win.get_class_group_name.return_value = class_group
    win.get_pid.return_value = pid if pid is not None else id(win)
    win.is_minimized.return_value = minimized
    win.is_maximized.return_value = maximized
    win.is_maximized_vertically.return_value = maximized_vertically
    win.is_maximized_horizontally.return_value = maximized_horizontally
    win.get_xid.return_value = id(win)
    win.is_visible_on_workspace.return_value = visible_on_workspace
    # Default to NORMAL type
    if win_type is None:
        win_type = MagicMock()
        win_type.__eq__ = lambda self, other: False
        win_type.__hash__ = lambda self: hash("NORMAL")
    win.get_window_type.return_value = win_type
    return win


def _make_monitor(*, config: Config, windows=None, active_window=None):
    """Create a WindowDodgeMonitor with mocked internals."""
    monitor = WindowDodgeMonitor(
        config=config,
        get_dock_rect=lambda: DOCK_RECT,
        on_change=MagicMock(),
    )
    screen = MagicMock()
    screen.get_windows.return_value = windows or []
    screen.get_active_window.return_value = active_window
    screen.get_active_workspace.return_value = MagicMock()
    monitor._screen = screen
    return monitor


class TestEvaluateNoneMode:
    def test_never_hides(self):
        # Given hide mode NONE
        config = Config(hide_mode="none")
        mon = _make_monitor(config=config)
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible
        assert result is False


class TestEvaluateAutohideMode:
    def test_never_hides_via_dodge(self):
        # Given hide mode AUTOHIDE (handled by separate autohide system)
        config = Config(hide_mode="autohide")
        mon = _make_monitor(config=config)
        # When evaluated
        result = mon._evaluate()
        # Then dodge logic returns False
        assert result is False


class TestEvaluateDodgeActive:
    def test_active_window_overlapping_dock(self):
        # Given an active window covering the dock area
        config = Config(hide_mode="dodge-active")
        active = _make_window(x=500, y=0, w=1000, h=1080)
        mon = _make_monitor(config=config, active_window=active)
        # When evaluated
        result = mon._evaluate()
        # Then dock should hide
        assert result is True

    def test_active_window_not_overlapping(self):
        # Given an active window above the dock
        config = Config(hide_mode="dodge-active")
        active = _make_window(x=0, y=0, w=500, h=500)
        mon = _make_monitor(config=config, active_window=active)
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible
        assert result is False

    def test_no_active_window(self):
        # Given no active window (e.g. desktop focused)
        config = Config(hide_mode="dodge-active")
        mon = _make_monitor(config=config, active_window=None)
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible
        assert result is False

    def test_active_window_not_visible_on_workspace_is_ignored(self):
        config = Config(hide_mode="dodge-active")
        active = _make_window(
            x=500,
            y=0,
            w=1000,
            h=1080,
            visible_on_workspace=False,
        )
        mon = _make_monitor(config=config, active_window=active)

        assert mon._evaluate() is False


class TestEvaluateWindowDodge:
    def test_any_window_overlapping_hides(self):
        # Given a non-active window overlapping dock
        config = Config(hide_mode="window-dodge")
        win = _make_window(x=600, y=900, w=720, h=200)
        mon = _make_monitor(config=config, windows=[win])
        # When evaluated
        result = mon._evaluate()
        # Then dock hides
        assert result is True

    def test_no_windows_overlapping(self):
        # Given all windows above the dock
        config = Config(hide_mode="window-dodge")
        win = _make_window(x=0, y=0, w=400, h=400)
        mon = _make_monitor(config=config, windows=[win])
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible
        assert result is False

    def test_minimized_window_ignored(self):
        # Given a minimized window that would overlap
        config = Config(hide_mode="window-dodge")
        win = _make_window(x=600, y=900, w=720, h=200, minimized=True)
        mon = _make_monitor(config=config, windows=[win])
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible (minimized windows don't count)
        assert result is False


class TestEvaluateIntelligent:
    def test_same_app_window_overlapping(self):
        # Given active window class "Firefox" and another Firefox window overlapping dock
        config = Config(hide_mode="intelligent")
        active = _make_window(x=0, y=0, w=800, h=600, class_group="Firefox")
        sibling = _make_window(x=600, y=900, w=720, h=200, class_group="Firefox")
        mon = _make_monitor(
            config=config, windows=[active, sibling], active_window=active
        )
        # When evaluated
        result = mon._evaluate()
        # Then dock hides
        assert result is True

    def test_different_app_window_overlapping(self):
        # Given active "Firefox" but overlapping window is "Terminal"
        config = Config(hide_mode="intelligent")
        active = _make_window(x=0, y=0, w=800, h=600, class_group="Firefox")
        other = _make_window(x=600, y=900, w=720, h=200, class_group="Terminal")
        mon = _make_monitor(
            config=config, windows=[active, other], active_window=active
        )
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible (different app)
        assert result is False

    def test_active_window_overlapping_hides_without_class_group(self):
        config = Config(hide_mode="intelligent")
        active = _make_window(x=500, y=0, w=1000, h=1080, class_group="")
        mon = _make_monitor(config=config, windows=[], active_window=active)

        assert mon._evaluate() is True

    def test_same_process_window_overlapping_hides_without_class_group(self):
        config = Config(hide_mode="intelligent")
        active = _make_window(x=0, y=0, w=800, h=600, class_group="", pid=123)
        sibling = _make_window(x=600, y=900, w=720, h=200, class_group="", pid=123)
        mon = _make_monitor(
            config=config, windows=[active, sibling], active_window=active
        )

        assert mon._evaluate() is True

    def test_no_active_window(self):
        config = Config(hide_mode="intelligent")
        win = _make_window(x=600, y=900, w=720, h=200)
        mon = _make_monitor(config=config, windows=[win], active_window=None)
        assert mon._evaluate() is False


class TestEvaluateDodgeMaximized:
    def test_maximized_active_overlapping(self):
        # Given an active maximized window overlapping dock
        config = Config(hide_mode="dodge-maximized")
        active = _make_window(x=0, y=0, w=1920, h=1080, maximized=True)
        mon = _make_monitor(config=config, windows=[active], active_window=active)
        # When evaluated
        result = mon._evaluate()
        # Then dock hides
        assert result is True

    def test_non_maximized_active_not_hiding(self):
        # Given an active non-maximized window overlapping dock
        config = Config(hide_mode="dodge-maximized")
        active = _make_window(x=0, y=0, w=1920, h=1080, maximized=False)
        mon = _make_monitor(config=config, windows=[active], active_window=active)
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible
        assert result is False

    def test_dialog_overlapping_hides(self):
        # Given an active-app dialog window overlapping dock
        config = Config(hide_mode="dodge-maximized")
        # Make the dialog type compare equal to Wnck.WindowType.DIALOG
        from gi.repository import Wnck

        active = _make_window(x=0, y=0, w=800, h=600, class_group="App", pid=123)
        dialog = _make_window(
            x=600,
            y=900,
            w=400,
            h=300,
            class_group="App",
            pid=123,
            win_type=Wnck.WindowType.DIALOG,
        )
        mon = _make_monitor(
            config=config, windows=[active, dialog], active_window=active
        )
        # When evaluated
        result = mon._evaluate()
        # Then dock hides
        assert result is True

    def test_dialog_from_inactive_app_does_not_hide(self):
        config = Config(hide_mode="dodge-maximized")
        from gi.repository import Wnck

        active = _make_window(x=0, y=0, w=800, h=600, class_group="Active", pid=123)
        dialog = _make_window(
            x=600,
            y=900,
            w=400,
            h=300,
            class_group="Other",
            pid=456,
            win_type=Wnck.WindowType.DIALOG,
        )
        mon = _make_monitor(
            config=config, windows=[active, dialog], active_window=active
        )

        assert mon._evaluate() is False

    def test_vertically_maximized_active_overlapping_hides(self):
        config = Config(hide_mode="dodge-maximized")
        active = _make_window(
            x=0,
            y=0,
            w=1920,
            h=1080,
            maximized=False,
            maximized_vertically=True,
        )
        mon = _make_monitor(config=config, windows=[active], active_window=active)

        assert mon._evaluate() is True

    def test_no_active_and_no_dialog(self):
        # Given no active window and no dialog
        config = Config(hide_mode="dodge-maximized")
        win = _make_window(x=600, y=900, w=720, h=200)
        mon = _make_monitor(config=config, windows=[win], active_window=None)
        # When evaluated
        result = mon._evaluate()
        # Then dock stays visible
        assert result is False


class TestDoEvaluateCallsOnChange:
    def test_fires_on_change_when_state_changes(self):
        # Given dodge-active with overlapping active window
        config = Config(hide_mode="dodge-active")
        active = _make_window(x=500, y=0, w=1000, h=1080)
        mon = _make_monitor(config=config, active_window=active)
        # When _do_evaluate runs
        mon._do_evaluate()
        # Then on_change fired with True
        mon._on_change.assert_called_once_with(True)

    def test_no_change_no_callback(self):
        # Given dodge-active with no overlap (default _should_hide=False)
        config = Config(hide_mode="dodge-active")
        active = _make_window(x=0, y=0, w=100, h=100)
        mon = _make_monitor(config=config, active_window=active)
        # When _do_evaluate runs
        mon._do_evaluate()
        # Then on_change not called (still False)
        mon._on_change.assert_not_called()


class TestNullDockRect:
    def test_none_dock_rect_returns_false(self):
        # Given get_dock_rect returns None
        config = Config(hide_mode="dodge-active")
        active = _make_window(x=0, y=0, w=1920, h=1080)
        monitor = WindowDodgeMonitor(
            config=config,
            get_dock_rect=lambda: None,
            on_change=MagicMock(),
        )
        monitor._screen = MagicMock()
        monitor._screen.get_active_window.return_value = active
        # When evaluated
        result = monitor._evaluate()
        # Then returns False
        assert result is False


class TestLifecycle:
    def test_start_forces_initial_screen_update_before_connecting_windows(
        self, monkeypatch
    ):
        config = Config(hide_mode="intelligent")
        monitor = WindowDodgeMonitor(
            config=config,
            get_dock_rect=lambda: DOCK_RECT,
            on_change=MagicMock(),
        )
        screen = MagicMock()
        screen.get_windows.return_value = []
        monkeypatch.setattr(
            "docking.platform.backends.x11.impl.dodge.Wnck.Screen.get_default",
            MagicMock(return_value=screen),
        )
        monkeypatch.setattr(
            "docking.platform.backends.x11.impl.dodge.GLib.timeout_add",
            MagicMock(return_value=99),
        )

        monitor.start()

        screen.force_update.assert_called_once_with()

    def test_do_evaluate_forces_screen_update_before_reading_state(self):
        config = Config(hide_mode="dodge-active")
        active = _make_window(x=500, y=0, w=1000, h=1080)
        monitor = _make_monitor(config=config, active_window=active)
        calls: list[str] = []
        monitor._screen.force_update.side_effect = lambda: calls.append("force_update")
        monitor._screen.get_active_window.side_effect = lambda: (
            calls.append("get_active_window") or active
        )

        monitor._do_evaluate()

        assert calls[:2] == ["force_update", "get_active_window"]
        monitor._on_change.assert_called_once_with(True)

    def test_schedule_evaluate_replaces_debounce_timer(self, monkeypatch):
        config = Config(hide_mode="intelligent")
        monitor = WindowDodgeMonitor(
            config=config,
            get_dock_rect=lambda: DOCK_RECT,
            on_change=MagicMock(),
        )
        added: list[tuple[int, object]] = []
        removed: list[int] = []
        next_id = iter([10, 11])

        monkeypatch.setattr(
            "docking.platform.backends.x11.impl.dodge.GLib.timeout_add",
            lambda delay, callback: added.append((delay, callback)) or next(next_id),
        )
        monkeypatch.setattr(
            "docking.platform.backends.x11.impl.dodge.GLib.source_remove",
            lambda source_id: removed.append(source_id),
        )

        monitor._schedule_evaluate()
        monitor._schedule_evaluate()

        assert [delay for delay, _callback in added] == [200, 200]
        assert removed == [10]
        assert monitor._debounce_id == 11

    def test_evaluate_now_cancels_pending_debounce_and_runs_immediately(
        self, monkeypatch
    ):
        config = Config(hide_mode="dodge-active")
        monitor = WindowDodgeMonitor(
            config=config,
            get_dock_rect=lambda: DOCK_RECT,
            on_change=MagicMock(),
        )
        monitor._debounce_id = 42
        removed: list[int] = []
        monkeypatch.setattr(
            "docking.platform.backends.x11.impl.dodge.GLib.source_remove",
            lambda source_id: removed.append(source_id),
        )
        monitor._do_evaluate = MagicMock()

        monitor.evaluate_now()

        assert removed == [42]
        assert monitor._debounce_id == 0
        monitor._do_evaluate.assert_called_once_with()

    def test_stop_disconnects_window_signal_handlers(self, monkeypatch):
        config = Config(hide_mode="window-dodge")
        monitor = WindowDodgeMonitor(
            config=config,
            get_dock_rect=lambda: DOCK_RECT,
            on_change=MagicMock(),
        )

        screen = MagicMock()
        monitor._signal_ids = [(screen, 10)]

        first = _make_window(x=0, y=0, w=100, h=100)
        first.connect_after.side_effect = [101, 102]
        second = _make_window(x=0, y=0, w=100, h=100)
        second.connect_after.side_effect = [201, 202]

        monitor._connect_window(first)
        monitor._connect_window(second)
        monitor._debounce_id = 301
        removed: list[int] = []
        monkeypatch.setattr(
            "docking.platform.backends.x11.impl.dodge.GLib.source_remove",
            lambda source_id: removed.append(source_id),
        )

        monitor.stop()

        assert removed == [301]
        screen.disconnect.assert_called_once_with(10)
        assert monitor._debounce_id == 0
        first.disconnect.assert_any_call(101)
        first.disconnect.assert_any_call(102)
        second.disconnect.assert_any_call(201)
        second.disconnect.assert_any_call(202)
        assert monitor._window_signal_ids == {}
