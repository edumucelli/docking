"""Native Windows snapshots use shared matching and reject stale actions."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.config import Config
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowSnapshot,
)
from docking.platform.backends.windows.windows import WindowsWindowService
from tests.platform.application_fakes import application, identity_services


def test_reused_native_handle_cannot_act_on_another_process():
    from docking.platform.backends.windows.native import WindowsNativeAPI

    native = object.__new__(WindowsNativeAPI)
    native._exists = lambda _hwnd: True
    native._post = MagicMock()

    def new_owner(_hwnd, output):
        output._obj.value = 202

    native._pid = new_owner
    old = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 123), desktop_id="", pid=101
    )
    assert native.perform(old, "close") is ActionResult.NOT_FOUND
    native._post.assert_not_called()


def test_windows_grouping_actions_and_disappearing_windows(monkeypatch):
    from docking.platform.backends.windows import windows as module

    monkeypatch.setattr(module.GLib, "timeout_add", lambda *_args: 91)
    removed = []
    monkeypatch.setattr(module.GLib, "source_remove", removed.append)
    first = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 1),
        desktop_id="",
        app_id="Example.App",
        pid=101,
        title="First",
        active=True,
        visible=True,
        can_activate=True,
        can_minimize=True,
        can_close=True,
    )
    second = replace(first, id=WindowId(DisplayServer.WINDOWS, 2), active=False)
    native = MagicMock()
    native.windows.return_value = (first, second)
    native.perform.return_value = ActionResult.OK
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    service = WindowsWindowService(
        model=model,
        native=native,
        config=Config(current_workspace_only=True),
        **identity_services(application("Example.App")),
    )
    service.start()
    try:
        rows = service.list_windows("Example.App")
        assert len(rows) == 2
        running = model.update_running.call_args.kwargs["running"]["Example.App"]
        assert running.count == 2 and running.active
        assert service.toggle_focus("Example.App") is ActionResult.OK
        assert [c.args[1] for c in native.perform.call_args_list] == [
            "minimize",
            "minimize",
        ]
        native.perform.reset_mock()
        native.windows.return_value = ()
        service.refresh()
        assert service.close(first.id) is ActionResult.NOT_FOUND
        native.perform.assert_not_called()
        assert model.update_running.call_args.kwargs["running"] == {}
    finally:
        service.stop()
    assert removed == [91]


def test_native_owned_dialog_is_available_to_overlap_but_not_grouped_on_taskbar():
    from ctypes import wintypes

    from docking.platform.backends.windows.native import WindowsNativeAPI

    native = object.__new__(WindowsNativeAPI)
    native._visible = lambda _hwnd: True
    native._pid = lambda _hwnd, output: setattr(output._obj, "value", 101)
    native._style = lambda *_args: 0
    native._owner = lambda *_args: 900
    native._class = lambda _hwnd, output, _size: setattr(output, "value", "#32770")
    native._dwm_attribute = lambda *_args: 0
    native._title = lambda _hwnd, output, _size: setattr(output, "value", "Open file")
    native._process_identity = lambda _pid: ("Example.App", "example")
    native._rect = lambda _hwnd, output: (
        setattr(output._obj, "left", -1920),
        setattr(output._obj, "right", 0),
        setattr(output._obj, "bottom", 1080),
    )
    native._monitor_from_window = lambda *_args: 1

    def monitor_info(_monitor, output):
        output._obj.monitor = wintypes.RECT(-1920, 0, 0, 1080)
        return True

    native._monitor_info = monitor_info
    native._iconic = lambda _hwnd: False
    native._zoomed = lambda _hwnd: False
    row = native._snapshot(100, 100, {})
    assert row is not None
    assert row.dialog and row.skip_taskbar and row.fullscreen
    assert row.active and row.app_id == "Example.App"
    native._iconic = lambda _hwnd: True
    minimized = native._snapshot(100, 100, {})
    assert minimized is not None and not minimized.fullscreen
    native._style = lambda *_args: 0x00000080  # Passive tool palette.
    assert native._snapshot(100, 100, {}) is None
    native._style = lambda *_args: 0x00040000  # Explicit taskbar entry.
    independent = native._snapshot(100, 100, {})
    assert independent is not None and not independent.skip_taskbar
    assert not independent.dialog
