"""Tests for the X11 WindowService facade."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - fallback for non-GI environments
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.platform.window_tracker as tracker_mod
from docking.platform.backends.base import ActionResult, WindowId
from docking.platform.backends.x11.windows import X11WindowService
from docking.platform.running import RunningAppInfo


class FakeWorkspace:
    def __init__(self, number: int) -> None:
        self._number = number

    def get_number(self) -> int:
        return self._number


class FakeWindow:
    def __init__(
        self,
        xid: int,
        *,
        title: str = "Window",
        urgent: bool = False,
        minimized: bool = False,
        maximized: bool = False,
        fullscreen: bool = False,
        workspace: FakeWorkspace | None = None,
    ) -> None:
        self._xid = xid
        self._title = title
        self._urgent = urgent
        self._minimized = minimized
        self._maximized = maximized
        self._fullscreen = fullscreen
        self._workspace = workspace
        self.activated_with: list[int] = []
        self.closed_with: list[int] = []
        self.minimize_calls = 0

    def get_window_type(self) -> int:
        return 0

    def is_skip_tasklist(self) -> bool:
        return False

    def get_xid(self) -> int:
        return self._xid

    def get_name(self) -> str:
        return self._title

    def get_class_group_name(self) -> str:
        return "Firefox"

    def get_class_instance_name(self) -> str:
        return "firefox"

    def needs_attention(self) -> bool:
        return self._urgent

    def is_minimized(self) -> bool:
        return self._minimized

    def is_maximized(self) -> bool:
        return self._maximized

    def is_fullscreen(self) -> bool:
        return self._fullscreen

    def get_geometry(self) -> tuple[int, int, int, int]:
        return (10, 20, 800, 600)

    def get_workspace(self) -> FakeWorkspace | None:
        return self._workspace

    def activate(self, timestamp: int) -> None:
        self.activated_with.append(timestamp)

    def close(self, timestamp: int) -> None:
        self.closed_with.append(timestamp)

    def minimize(self) -> None:
        self.minimize_calls += 1
        self._minimized = True


class FakeScreen:
    def __init__(self, windows: list[FakeWindow], active_window: FakeWindow | None):
        self._windows = windows
        self._active_window = active_window

    def get_windows(self) -> list[FakeWindow]:
        return list(self._windows)

    def get_active_window(self) -> FakeWindow | None:
        return self._active_window


def make_service(
    windows: list[FakeWindow], *, active: FakeWindow | None = None
) -> X11WindowService:
    service = X11WindowService.__new__(X11WindowService)
    service._screen = FakeScreen(windows=windows, active_window=active)
    service._config = None
    service._model = MagicMock()
    service._launcher = MagicMock()
    service._running_xids_by_desktop = {
        "firefox.desktop": [window.get_xid() for window in windows]
    }
    service._last_running = {
        "firefox.desktop": RunningAppInfo(
            count=len(windows),
            xids=tuple(window.get_xid() for window in windows),
            window_ids=tuple(WindowId.x11(window.get_xid()) for window in windows),
        )
    }
    service._cycle_index = {}
    service._cycle_order_by_desktop = {}
    return service


def test_list_windows_returns_backend_snapshots(monkeypatch):
    monkeypatch.setattr(
        tracker_mod.Wnck,
        "WindowType",
        SimpleNamespace(DESKTOP=1, DOCK=2),
        raising=False,
    )

    first = FakeWindow(10, title="Firefox")
    second = FakeWindow(
        20,
        title="Private Window",
        urgent=True,
        minimized=True,
        maximized=True,
        workspace=FakeWorkspace(2),
    )
    service = make_service([first, second], active=second)

    snapshots = service.list_windows("firefox.desktop")

    assert len(snapshots) == 2
    assert snapshots[0].id == WindowId.x11(10)
    assert snapshots[0].title == "Firefox"
    assert snapshots[0].active is False
    assert snapshots[0].urgent is False
    assert snapshots[0].geometry is not None
    assert snapshots[0].geometry.width == 800
    assert snapshots[1].id == WindowId.x11(20)
    assert snapshots[1].title == "Private Window"
    assert snapshots[1].active is True
    assert snapshots[1].urgent is True
    assert snapshots[1].minimized is True
    assert snapshots[1].maximized is True
    assert snapshots[1].fullscreen is False
    assert snapshots[1].workspace_id == "2"
    assert snapshots[1].can_activate is True
    assert snapshots[1].can_close is True


def test_snapshot_running_returns_copy():
    service = make_service([FakeWindow(10)])

    running = service.snapshot_running()
    running.clear()

    assert service.snapshot_running()["firefox.desktop"].count == 1
    assert service.snapshot_running()["firefox.desktop"].xids == (10,)
    assert service.snapshot_running()["firefox.desktop"].window_ids == (
        WindowId.x11(10),
    )


def test_activate_uses_x11_window_id(monkeypatch):
    monkeypatch.setattr(tracker_mod.Gtk, "get_current_event_time", lambda: 123)
    window = FakeWindow(10)
    service = make_service([window])

    result = service.activate(WindowId.x11(10))

    assert result is ActionResult.OK
    assert window.activated_with == [123]


def test_activate_reports_not_found_for_stale_xid():
    service = make_service([FakeWindow(10)])

    assert service.activate(WindowId.x11(99)) is ActionResult.NOT_FOUND


def test_activate_rejects_non_x11_window_id():
    service = make_service([FakeWindow(10)])

    assert (
        service.activate(WindowId(backend="wayland", value="window-1"))
        is ActionResult.UNSUPPORTED
    )


def test_close_uses_x11_window_id(monkeypatch):
    monkeypatch.setattr(tracker_mod.Gtk, "get_current_event_time", lambda: 456)
    window = FakeWindow(10)
    service = make_service([window])

    result = service.close(WindowId.x11(10))

    assert result is ActionResult.OK
    assert window.closed_with == [456]
