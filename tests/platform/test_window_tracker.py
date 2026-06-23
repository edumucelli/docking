"""Tests for window tracker WM_CLASS matching."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

_CREATED_GI_FALLBACK = False
try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - fallback for non-GI environments
    _CREATED_GI_FALLBACK = True
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.platform.backends.x11.impl.window_tracker as tracker_mod
from docking.platform.launcher import DESKTOP_SUFFIX, GNOME_APP_PREFIX

if _CREATED_GI_FALLBACK:
    sys.modules.pop("gi", None)
    sys.modules.pop("gi.repository", None)


class TestDesktopConstants:
    def test_desktop_suffix(self):
        assert DESKTOP_SUFFIX == ".desktop"

    def test_gnome_app_prefix(self):
        # Used to strip GNOME app ID prefixes from desktop filenames
        assert isinstance(GNOME_APP_PREFIX, str)


class _FakeWindow:
    def __init__(self, xid: int, *, minimized: bool = False) -> None:
        self.xid = xid
        self.minimized = minimized
        self.minimize_calls = 0
        self.close_calls: list[int] = []

    def get_xid(self) -> int:
        return self.xid

    def is_minimized(self) -> bool:
        return self.minimized

    def minimize(self) -> None:
        self.minimize_calls += 1
        self.minimized = True

    def close(self, timestamp: int) -> None:
        self.close_calls.append(timestamp)


class TestWindowActions:
    def _make_tracker(
        self, windows: list[_FakeWindow], *, active: _FakeWindow | None = None
    ):
        tracker = tracker_mod.WindowTracker.__new__(tracker_mod.WindowTracker)
        tracker._screen = SimpleNamespace(get_active_window=lambda: active)
        tracker._running_xids_by_desktop = {"firefox.desktop": [w.xid for w in windows]}
        tracker._cycle_index = {}
        tracker._cycle_order_by_desktop = {}
        tracker._get_windows_for = lambda desktop_id: list(windows)
        tracker._model = MagicMock()
        tracker._config = None
        tracker._launcher = MagicMock()
        return tracker

    def test_internal_minimize_windows_minimizes_each_non_minimized_window(self):
        first = _FakeWindow(1)
        second = _FakeWindow(2, minimized=True)
        tracker = self._make_tracker([first, second])

        tracker_mod.WindowTracker._minimize_windows(tracker, "firefox.desktop")

        assert first.minimize_calls == 1
        assert second.minimize_calls == 0

    def test_close_focused_only_closes_active_matching_window(self, monkeypatch):
        active = _FakeWindow(2)
        other = _FakeWindow(3)
        tracker = self._make_tracker([active, other], active=active)
        monkeypatch.setattr(tracker_mod.Gtk, "get_current_event_time", lambda: 77)

        tracker_mod.WindowTracker.close_focused(tracker, "firefox.desktop")

        assert active.close_calls == [77]
        assert other.close_calls == []

    def test_close_focused_noops_for_other_active_window(self, monkeypatch):
        active = _FakeWindow(9)
        own = _FakeWindow(2)
        tracker = self._make_tracker([own], active=active)
        monkeypatch.setattr(tracker_mod.Gtk, "get_current_event_time", lambda: 88)

        tracker_mod.WindowTracker.close_focused(tracker, "firefox.desktop")

        assert own.close_calls == []
        assert active.close_calls == []

    def test_internal_cycle_windows_activates_first_when_app_is_not_active(
        self, monkeypatch
    ):
        first = _FakeWindow(1)
        second = _FakeWindow(2)
        third = _FakeWindow(3)
        tracker = self._make_tracker([first, second, third], active=None)
        activated: list[int] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: activated.append(window.xid)),
        )

        tracker_mod.WindowTracker._cycle_windows(tracker, "firefox.desktop")

        assert activated == [1]
        assert tracker._cycle_index["firefox.desktop"] == 0

    def test_internal_cycle_windows_uses_active_window_position(self, monkeypatch):
        first = _FakeWindow(1)
        second = _FakeWindow(2)
        third = _FakeWindow(3)
        tracker = self._make_tracker([first, second, third], active=second)
        activated: list[int] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: activated.append(window.xid)),
        )

        tracker_mod.WindowTracker._cycle_windows(tracker, "firefox.desktop")

        assert activated == [3]
        assert tracker._cycle_index["firefox.desktop"] == 2

    def test_internal_cycle_windows_minimizes_on_last_window(self, monkeypatch):
        first = _FakeWindow(1)
        second = _FakeWindow(2)
        tracker = self._make_tracker([first, second], active=second)
        minimize_calls: list[str] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: (_ for _ in ()).throw(AssertionError())),
        )
        tracker._minimize_windows = lambda desktop_id: minimize_calls.append(desktop_id)

        tracker_mod.WindowTracker._cycle_windows(tracker, "firefox.desktop")

        assert minimize_calls == ["firefox.desktop"]
        assert tracker._cycle_index["firefox.desktop"] == 0

    def test_internal_cycle_windows_resets_index_when_membership_changes(
        self, monkeypatch
    ):
        first = _FakeWindow(1)
        second = _FakeWindow(2)
        tracker = self._make_tracker([first, second], active=None)
        tracker._cycle_order_by_desktop["firefox.desktop"] = [1, 2, 3]
        tracker._cycle_index["firefox.desktop"] = 5
        activated: list[int] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: activated.append(window.xid)),
        )

        tracker_mod.WindowTracker._cycle_windows(tracker, "firefox.desktop")

        assert activated == [1]
        assert tracker._cycle_index["firefox.desktop"] == 0

    def _make_mru_tracker(
        self,
        windows: list[_FakeWindow],
        stacked: list[_FakeWindow],
        *,
        active: _FakeWindow | None = None,
    ):
        tracker = tracker_mod.WindowTracker.__new__(tracker_mod.WindowTracker)
        tracker._screen = SimpleNamespace(
            get_active_window=lambda: active,
            get_windows_stacked=lambda: list(stacked),
        )
        tracker._running_xids_by_desktop = {"firefox.desktop": [w.xid for w in windows]}
        tracker._cycle_index = {}
        tracker._cycle_order_by_desktop = {}
        tracker._get_windows_for = lambda desktop_id: list(windows)
        tracker._model = MagicMock()
        tracker._config = None
        tracker._launcher = MagicMock()
        return tracker

    def test_activate_most_recent_picks_top_of_stack(self, monkeypatch):
        first = _FakeWindow(1)
        second = _FakeWindow(2)
        third = _FakeWindow(3)
        unrelated = _FakeWindow(99)
        tracker = self._make_mru_tracker(
            windows=[first, second, third],
            stacked=[first, unrelated, second, third],
            active=unrelated,
        )
        activated: list[int] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: activated.append(window.xid)),
        )

        tracker_mod.WindowTracker.activate_most_recent(tracker, "firefox.desktop")

        assert activated == [3]

    def test_activate_most_recent_minimizes_when_app_active(self, monkeypatch):
        first = _FakeWindow(1)
        second = _FakeWindow(2)
        tracker = self._make_mru_tracker(
            windows=[first, second],
            stacked=[first, second],
            active=second,
        )
        minimize_calls: list[str] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: (_ for _ in ()).throw(AssertionError())),
        )
        tracker._minimize_windows = lambda desktop_id: minimize_calls.append(desktop_id)

        tracker_mod.WindowTracker.activate_most_recent(tracker, "firefox.desktop")

        assert minimize_calls == ["firefox.desktop"]

    def test_activate_most_recent_noop_when_no_windows(self, monkeypatch):
        tracker = self._make_mru_tracker(windows=[], stacked=[], active=None)
        activated: list[int] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: activated.append(window.xid)),
        )

        tracker_mod.WindowTracker.activate_most_recent(tracker, "firefox.desktop")

        assert activated == []

    def test_activate_most_recent_falls_back_when_stacking_unavailable(
        self, monkeypatch
    ):
        first = _FakeWindow(1)
        second = _FakeWindow(2)
        tracker = self._make_mru_tracker(
            windows=[first, second], stacked=[], active=None
        )
        activated: list[int] = []
        monkeypatch.setattr(
            tracker_mod.WindowTracker,
            "activate_window",
            staticmethod(lambda window: activated.append(window.xid)),
        )

        tracker_mod.WindowTracker.activate_most_recent(tracker, "firefox.desktop")

        assert activated == [1]
