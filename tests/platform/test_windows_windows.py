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
    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = object.__new__(Win32WindowAPI)
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


def test_windows_covered_by_desktop_keep_running_entries_without_reporting_overlap():
    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = Win32WindowAPI.__new__(Win32WindowAPI)
    native._desktop = MagicMock()
    native._desktop.covered_windows.return_value = frozenset({10})
    native._foreground = lambda: 0
    native._callback_type = lambda value: value
    native._enumerate = lambda callback, _: (callback(10, 0), callback(20, 0))
    native._snapshot = lambda hwnd, *_args: WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, hwnd),
        desktop_id="",
        app_id="Example.App",
        pid=101,
        visible=True,
        minimized=False,
    )
    rows = native.windows()
    assert not rows[0].visible and not rows[0].minimized
    assert rows[1].visible
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    service = WindowsWindowService(
        model=model, **identity_services(application("Example.App"))
    )
    service.replace_windows(rows)
    assert len(service.list_windows("Example.App")) == 2
    assert model.update_running.call_args.kwargs["running"]["Example.App"].count == 2


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

    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = object.__new__(Win32WindowAPI)
    native._visible = lambda _hwnd: True
    native._pid = lambda _hwnd, output: setattr(output._obj, "value", 101)
    native._style = lambda *_args: 0
    native._owner = lambda *_args: 900
    native._class = lambda _hwnd, output, _size: setattr(output, "value", "#32770")
    native._dwm_attribute = lambda *_args: 0
    native._title = lambda _hwnd, output, _size: setattr(output, "value", "Open file")
    native._process_identity = lambda _pid: ("Example.App", "example")
    native._properties = MagicMock()
    native._properties.app_id.return_value = None
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
    native._visible = lambda _hwnd: False
    assert native._snapshot(100, 100, {}) is None
    hidden = native._snapshot(100, 100, {}, include_hidden=True)
    assert hidden is not None and not hidden.visible


def test_explicit_per_window_id_separates_applications_in_one_host_process():
    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = Win32WindowAPI.__new__(Win32WindowAPI)
    native._properties = MagicMock()
    native._properties.app_id.side_effect = lambda hwnd: {
        10: "Calc.App",
        20: "Clock.App",
    }[hwnd]
    native._process_identity = MagicMock(return_value=(None, "applicationframehost"))
    identities = {}
    rows = [
        WindowSnapshot(
            id=WindowId(DisplayServer.WINDOWS, hwnd),
            desktop_id="",
            app_id=native._application_id(
                hwnd, 101, "ApplicationFrameWindow", identities
            ),
            pid=101,
            visible=True,
        )
        for hwnd in (10, 20)
    ]
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    service = WindowsWindowService(
        model=model,
        **identity_services(application("Calc.App"), application("Clock.App")),
    )
    service.replace_windows(rows)
    assert [w.id.value for w in service.list_windows("Calc.App")] == [10]
    assert [w.id.value for w in service.list_windows("Clock.App")] == [20]
    assert set(model.update_running.call_args.kwargs["running"]) == {
        "Calc.App",
        "Clock.App",
    }
    native._process_identity.assert_not_called()


def test_missing_or_unreadable_window_id_uses_process_identity_without_hiding_window():
    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = Win32WindowAPI.__new__(Win32WindowAPI)
    native._properties = MagicMock()
    native._properties.app_id.side_effect = [None, OSError("Unavailable")]
    native._process_identity = MagicMock(return_value=("Process.App", "example"))
    identities = {}
    assert native._application_id(10, 101, "Example", identities) == "Process.App"
    assert native._application_id(20, 101, "Example", identities) == "Process.App"
    native._process_identity.assert_called_once_with(101)


def test_same_host_hwnd_and_pid_with_changed_app_id_cannot_receive_a_stale_action():
    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = Win32WindowAPI.__new__(Win32WindowAPI)
    native._exists = lambda _hwnd: True
    native._pid = lambda hwnd, output: setattr(output._obj, "value", 101)
    native._properties = MagicMock()
    native._properties.app_id.return_value = "Old.App"
    native._post = MagicMock(return_value=True)
    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="",
        pid=101,
        app_id="Old.App",
    )
    assert native.perform(row, "close") is ActionResult.OK
    native._post.reset_mock()
    native._properties.app_id.return_value = "New.App"
    assert native.perform(row, "close") is ActionResult.NOT_FOUND
    native._post.assert_not_called()
