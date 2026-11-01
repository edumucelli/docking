"""Native Windows snapshots use shared matching and reject stale actions."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


@pytest.mark.parametrize("order", [(100, 900), (900, 100)])
def test_ghost_keeps_one_original_identity_with_presentation_state_in_either_z_order(
    order,
):
    from docking.platform.backends.base import Rect
    from docking.platform.backends.windows.windows import Win32WindowAPI

    original = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="",
        app_id="Fixture.App",
        wm_class="Fixture.Window",
        pid=101,
        title="Document",
        workspace_id="original-desktop",
        on_current_workspace=False,
        sticky=True,
        visible=True,
        geometry=Rect(0, 0, 300, 200),
    )
    ghost = replace(
        original,
        id=WindowId(DisplayServer.WINDOWS, 900),
        pid=500,
        wm_class="Ghost",
        app_id="dwm",
        title="Document (Not Responding)",
        workspace_id="dwm-desktop",
        on_current_workspace=True,
        sticky=False,
        active=True,
        minimized=True,
        geometry=Rect(100, 200, 400, 300),
    )
    native = Win32WindowAPI.__new__(Win32WindowAPI)
    native._foreground = lambda: 900
    native._desktop = MagicMock()
    native._desktop.covered_windows.return_value = frozenset()
    native._callback_type = lambda value: value
    native._enumerate = lambda callback, data: tuple(callback(h, data) for h in order)
    native._snapshot = lambda hwnd, *_args, **_kw: {100: original, 900: ghost}[hwnd]
    native._ghosts = MagicMock()
    native._ghosts.original.return_value = 100
    rows = native.windows()
    assert len(rows) == 1
    row = rows[0]
    assert (row.id, row.pid, row.app_id, row.wm_class) == (
        original.id,
        original.pid,
        original.app_id,
        original.wm_class,
    )
    assert (row.title, row.active, row.minimized, row.geometry) == (
        ghost.title,
        ghost.active,
        ghost.minimized,
        ghost.geometry,
    )
    assert (row.workspace_id, row.on_current_workspace, row.sticky) == (
        original.workspace_id,
        original.on_current_workspace,
        original.sticky,
    )
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    service = WindowsWindowService(
        model=model, **identity_services(application("Fixture.App"))
    )
    service.replace_windows(rows)
    assert len(service.list_windows("Fixture.App")) == 1
    assert model.update_running.call_args.kwargs["running"]["Fixture.App"].count == 1


def test_unassociated_ghost_cannot_create_a_dwm_running_entry():
    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = Win32WindowAPI.__new__(Win32WindowAPI)
    native._foreground = lambda: 900
    native._desktop = MagicMock()
    native._desktop.covered_windows.return_value = frozenset()
    native._callback_type = lambda value: value
    native._enumerate = lambda callback, data: callback(900, data)
    native._snapshot = lambda *_args: WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 900),
        desktop_id="",
        pid=500,
        wm_class="Ghost",
    )
    native._ghosts = MagicMock()
    native._ghosts.original.return_value = None
    assert native.windows() == ()


def test_actions_and_capture_select_the_current_associated_surface_and_recover():
    from docking.platform.backends.windows.windows import (
        Win32WindowAPI,
        WindowsWindowTarget,
    )

    native = Win32WindowAPI.__new__(Win32WindowAPI)
    native.matches = MagicMock(return_value=True)
    native._ghosts = MagicMock()
    native._ghosts.presentation.return_value = 900
    native._pid = lambda hwnd, output: setattr(
        output._obj, "value", 500 if hwnd == 900 else 101
    )
    native._iconic = MagicMock(return_value=False)
    native._show = MagicMock(return_value=True)
    native._activate = MagicMock(return_value=True)
    native._post = MagicMock(return_value=True)
    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100), desktop_id="", pid=101
    )
    assert native.target(row) == WindowsWindowTarget(900, 500)
    assert native.perform(row, "minimize") is ActionResult.OK
    native._post.assert_called_once_with(900, 0x112, 0xF020, 0)
    native._post.reset_mock()
    native._iconic.return_value = True
    native._show.reset_mock()
    assert native.perform(row, "minimize") is ActionResult.OK
    native._show.assert_not_called()
    assert native.perform(row, "activate") is ActionResult.OK
    native._post.assert_called_once_with(900, 0x112, 0xF120, 0)
    native._show.assert_not_called()
    native._activate.assert_called_once_with(900)
    native._post.reset_mock()
    assert native.perform(row, "close") is ActionResult.OK
    native._post.assert_called_once_with(900, 0x112, 0xF060, 0)
    native._post.return_value = False
    native._activate.reset_mock()
    assert native.perform(row, "activate") is ActionResult.FAILED
    native._activate.assert_not_called()
    native._ghosts.presentation.return_value = None
    assert native.target(row) == WindowsWindowTarget(100, 101)
    native.matches.return_value = False
    assert native.target(row) is None
    assert native.perform(row, "close") is ActionResult.NOT_FOUND


def test_preview_target_requires_both_current_application_and_native_surface():
    from docking.platform.backends.windows.windows import WindowsWindowTarget

    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="",
        pid=101,
        app_id="Fixture.App",
    )
    native = MagicMock()
    ghost = WindowsWindowTarget(900, 500)
    native.target.return_value = ghost
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    service = WindowsWindowService(
        model=model, native=native, **identity_services(application("Fixture.App"))
    )
    service.replace_windows((row,))
    assert service.capture_target(row.id, 101, "Fixture.App") == ghost
    assert service.capture_is_current(row.id, 101, "Fixture.App", ghost)
    assert service.capture_target(row.id, 202, "Fixture.App") is None
    assert service.capture_target(row.id, 101, "Another.App") is None
    native.target.return_value = WindowsWindowTarget(100, 101)
    assert not service.capture_is_current(row.id, 101, "Fixture.App", ghost)
    service.stop()
    assert service.capture_target(row.id, 101, "Fixture.App") is None


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


def snapshot_api():
    from ctypes import wintypes

    from docking.platform.backends.windows.windows import Win32WindowAPI

    native = object.__new__(Win32WindowAPI)
    native._visible = lambda _hwnd: True
    native._membership = None
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
    return native


def test_native_owned_dialog_is_available_to_overlap_but_not_grouped_on_taskbar():
    native = snapshot_api()
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


@pytest.mark.parametrize(
    "current,cloaked,keep",
    [
        (False, 2, True),
        (False, 1, False),
        (False, 3, False),
        (True, 2, False),
        (None, 2, False),
        (True, 0, True),
    ],
)
def test_shell_cloaked_other_desktop_windows_keep_membership_without_overlap(
    current, cloaked, keep
):
    from docking.platform.backends.windows.virtual_desktops import WindowWorkspace

    native = snapshot_api()
    membership = WindowWorkspace(101, "desktop", current, sticky=True)
    native._membership = lambda hwnd, pid: (
        membership if (hwnd, pid) == (100, 101) else None
    )
    native._dwm_attribute = lambda hwnd, key, output, size: (
        setattr(output._obj, "value", cloaked) or 0
    )
    row = native._snapshot(100, 100, {})
    assert (row is not None) is keep
    if keep:
        assert (row.workspace_id, row.on_current_workspace, row.sticky) == (
            "desktop",
            current,
            True,
        )
        assert row.visible is (cloaked == 0)


@pytest.mark.parametrize(
    "current_only,current,keep",
    [
        (False, False, True),
        (True, False, False),
        (True, True, True),
        (True, None, True),
    ],
)
def test_current_workspace_filter_excludes_only_established_other_desktop(
    current_only, current, keep
):
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    service = WindowsWindowService(
        model=model,
        config=Config(current_workspace_only=current_only),
        **identity_services(application("Fixture.App")),
    )
    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="",
        app_id="Fixture.App",
        on_current_workspace=current,
    )
    service.replace_windows((row,))
    running = model.update_running.call_args.kwargs["running"]
    assert ("Fixture.App" in running) is keep
    # Filtering changes the projection; the complete snapshot remains usable.
    assert service.list_all_windows() == (replace(row, desktop_id="Fixture.App"),)


def test_offdesktop_activation_switches_before_native_action_but_close_does_not():
    native, workspaces = MagicMock(), MagicMock()
    native.perform.return_value = workspaces.activate_then.return_value = (
        ActionResult.OK
    )
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    service = WindowsWindowService(
        model=model,
        native=native,
        workspaces=workspaces,
        **identity_services(application("Fixture.App")),
    )
    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="",
        app_id="Fixture.App",
        workspace_id="other",
        on_current_workspace=False,
        can_activate=True,
        can_close=True,
    )
    service.replace_windows((row,))
    row = service.list_all_windows()[0]
    assert service.activate(row.id) is ActionResult.OK
    native.perform.assert_not_called()
    identifier, after = workspaces.activate_then.call_args.args
    assert identifier == "other"
    assert after() is ActionResult.OK
    native.perform.assert_called_once_with(row, "activate")
    assert service.close(row.id) is ActionResult.OK
    assert native.perform.call_args.args == (row, "close")
    assert workspaces.activate_then.call_count == 1


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
    native._ghosts = MagicMock()
    native._ghosts.presentation.return_value = None
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


def test_capture_identity_includes_own_surfaces_without_changing_taskbar_policy():
    import os

    native = snapshot_api()
    native._pid = lambda hwnd, output: setattr(output._obj, "value", os.getpid())
    native._exists = lambda hwnd: True
    native._ghosts = MagicMock()
    native._ghosts.original.return_value = None
    assert native._snapshot(100, 100, {}) is None
    row = native.capture_snapshot(100, expected_pid=os.getpid())
    assert row is not None and row.pid == os.getpid()
    assert native.capture_snapshot(100, expected_pid=os.getpid() + 1) is None


def test_uncataloged_native_program_projects_one_item_for_two_windows(tmp_path):
    from docking.platform.applications.identity import ProcessIdentity
    from docking.platform.applications.types import ApplicationOrigin
    from tests.platform.application_fakes import ApplicationRegistryStub

    executable = tmp_path / "Portable.exe"
    header = bytearray(64)
    header[:2] = b"MZ"
    header[0x3C:0x40] = (64).to_bytes(4, "little")
    executable.write_bytes(header + b"PE\0\0")
    identity = ProcessIdentity(pid=42, executable_path=executable)
    model = SimpleNamespace(visible_items=list, update_running=MagicMock())
    from docking.platform.applications.implementations.windows.registration import (
        WindowsExecutableRegistration,
    )

    registry = ApplicationRegistryStub(())
    registry.registrations = WindowsExecutableRegistration(tmp_path / "launchers")
    service = WindowsWindowService(
        model=model,
        application_registry=registry,
        process_identity_service=SimpleNamespace(
            identity_for_pid=lambda _pid: identity
        ),
    )
    service.replace_windows(
        tuple(
            WindowSnapshot(
                id=WindowId(DisplayServer.WINDOWS, handle),
                desktop_id="",
                app_id="portable",
                pid=42,
            )
            for handle in (100, 200)
        )
    )
    running = model.update_running.call_args.kwargs["running"]
    assert len(running) == 1
    entry = next(iter(running.values()))
    assert entry.count == 2
    assert entry.runtime_app.origin is ApplicationOrigin.RUNTIME
    assert entry.runtime_app.executable_path == executable
    assert len({row.desktop_id for row in service.list_all_windows()}) == 1
