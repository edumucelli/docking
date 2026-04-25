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
from docking.platform.dodge import ScreenRect, WindowDodgeMonitor

# Dock sits at bottom of 1920x1080 screen
DOCK_RECT = ScreenRect(x=600, y=1030, width=720, height=50)


def _make_window(
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    class_group: str = "App",
    minimized: bool = False,
    maximized: bool = False,
    win_type=None,
    visible_on_workspace: bool = True,
):
    """Create a mock Wnck.Window."""
    win = MagicMock()
    win.get_geometry.return_value = (x, y, w, h)
    win.get_class_group_name.return_value = class_group
    win.is_minimized.return_value = minimized
    win.is_maximized.return_value = maximized
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
        # Given a dialog window overlapping dock
        config = Config(hide_mode="dodge-maximized")
        # Make the dialog type compare equal to Wnck.WindowType.DIALOG
        from gi.repository import Wnck

        dialog = _make_window(
            x=600, y=900, w=400, h=300, win_type=Wnck.WindowType.DIALOG
        )
        mon = _make_monitor(config=config, windows=[dialog], active_window=None)
        # When evaluated
        result = mon._evaluate()
        # Then dock hides
        assert result is True

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
            "docking.platform.dodge.GLib.source_remove",
            lambda source_id: removed.append(source_id),
        )
        monitor._do_evaluate = MagicMock()

        monitor.evaluate_now()

        assert removed == [42]
        assert monitor._debounce_id == 0
        monitor._do_evaluate.assert_called_once_with()

    def test_stop_disconnects_window_signal_handlers(self):
        config = Config(hide_mode="window-dodge")
        monitor = WindowDodgeMonitor(
            config=config,
            get_dock_rect=lambda: DOCK_RECT,
            on_change=MagicMock(),
        )

        screen = MagicMock()
        monitor._signal_ids = [(screen, 10)]

        first = _make_window(x=0, y=0, w=100, h=100)
        first.connect.side_effect = [101, 102]
        second = _make_window(x=0, y=0, w=100, h=100)
        second.connect.side_effect = [201, 202]

        monitor._connect_window(first)
        monitor._connect_window(second)

        monitor.stop()

        screen.disconnect.assert_called_once_with(10)
        first.disconnect.assert_any_call(101)
        first.disconnect.assert_any_call(102)
        second.disconnect.assert_any_call(201)
        second.disconnect.assert_any_call(202)
        assert monitor._window_signal_ids == {}
