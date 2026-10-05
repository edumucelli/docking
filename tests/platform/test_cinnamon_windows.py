"""Taskbar state, actions and shell generation safety for Cinnamon Wayland."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.items import DockItem
from docking.platform.backends.base import ActionResult, DisplayServer, WindowId
from docking.platform.backends.cinnamon import muffin
from docking.platform.backends.cinnamon.shell import CinnamonShellClient
from docking.platform.backends.cinnamon.windows import CinnamonWindowService
from docking.platform.backends.diagnostics import WindowReason
from tests.platform.application_fakes import identity_services


def _row(sequence=1, **fields):
    return {
        "id": sequence,
        "generation": "shell-1",
        "wm-class": "firefox",
        "minimized": False,
        "maximized": False,
        "fullscreen": False,
        "can-minimize": True,
        "can-close": True,
        **fields,
    }


def _service(*rows):
    model = SimpleNamespace(
        visible_items=lambda: [DockItem(desktop_id="firefox.desktop")],
        update_running=MagicMock(),
    )
    client = SimpleNamespace(
        list_windows=MagicMock(return_value=rows),
        last_query_failed=False,
        window_action=MagicMock(return_value=ActionResult.OK),
    )
    service = CinnamonWindowService(model=model, client=client, **identity_services())
    return service, client, model


def test_snapshot_matching_excludes_own_dock_and_taskbar_ineligible_windows():
    service, _client, model = _service(
        _row(1, focused=True, **{"demands-attention": True, "pid": 42}),
        _row(2, pid=os.getpid()),
        _row(3, **{"desktop-or-dock": True}),
        _row(4, **{"skip-taskbar": True}),
        _row(5, minimized=True, **{"wm-class": "unknown"}),
        _row("bad"),
    )
    service.refresh()
    running = model.update_running.call_args.kwargs["running"]
    assert running["firefox.desktop"].count == 1
    assert running["firefox.desktop"].active
    assert running["firefox.desktop"].urgent
    records = service.diagnostic_snapshot().windows
    assert [record.reason for record in records] == [
        WindowReason.INCLUDED,
        WindowReason.OWN_PROCESS,
        WindowReason.DESKTOP_OR_DOCK,
        WindowReason.SKIP_TASKBAR,
        WindowReason.NO_MATCH,
        WindowReason.INVALID_WINDOW_ID,
    ]
    snapshot = service.list_windows("firefox.desktop")[0]
    assert snapshot.id == WindowId(DisplayServer.WAYLAND, "cinnamon:shell-1:1")
    assert snapshot.can_activate and snapshot.can_minimize and snapshot.can_close
    assert snapshot.minimized is False
    assert not snapshot.can_preview


def test_inactive_and_minimized_apps_activate_mru_without_launching():
    service, client, _ = _service(_row(2, minimized=True), _row(1))
    assert service.toggle_focus("firefox.desktop") is ActionResult.OK
    client.window_action.assert_called_once_with(
        WindowId(DisplayServer.WAYLAND, "cinnamon:shell-1:2"), "activate"
    )
    client.window_action.reset_mock()
    assert service.activate_most_recent("firefox.desktop") is ActionResult.OK
    assert client.window_action.call_args.args[0].value.endswith(":2")


def test_focused_app_toggle_minimizes_all_and_close_focused_is_selective():
    service, client, _ = _service(_row(2), _row(1, focused=True))
    assert service.toggle_focus("firefox.desktop") is ActionResult.OK
    assert [call.args[1] for call in client.window_action.call_args_list] == [
        "minimize",
        "minimize",
    ]
    client.window_action.reset_mock()
    assert service.close_focused("firefox.desktop") is ActionResult.OK
    client.window_action.assert_called_once_with(
        WindowId(DisplayServer.WAYLAND, "cinnamon:shell-1:1"), "close"
    )


def test_cycle_uses_stable_order_as_mru_order_changes():
    service, client, _ = _service(_row(1, focused=True), _row(2), _row(3))
    service.cycle("firefox.desktop")
    assert client.window_action.call_args.args[0].value.endswith(":2")
    client.list_windows.return_value = (_row(2, focused=True), _row(1), _row(3))
    service.cycle("firefox.desktop")
    assert client.window_action.call_args.args[0].value.endswith(":3")
    client.list_windows.return_value = (_row(3, focused=True), _row(2), _row(1))
    service.cycle("firefox.desktop")
    assert client.window_action.call_args.args[0].value.endswith(":1")


def test_closed_windows_and_restarted_shell_ids_cannot_target_another_window():
    service, client, model = _service(_row())
    service.refresh()
    old_id = service.list_windows("firefox.desktop")[0].id
    client.list_windows.return_value = (_row(generation="shell-2"),)
    assert service.activate(old_id) is ActionResult.NOT_FOUND
    assert service.close(old_id) is ActionResult.NOT_FOUND
    client.window_action.assert_not_called()
    new_id = service.list_windows("firefox.desktop")[0].id
    assert service.activate(new_id) is ActionResult.OK
    client.list_windows.return_value = ()
    assert service.close(new_id) is ActionResult.NOT_FOUND
    assert model.update_running.call_args.kwargs["running"] == {}


def test_query_failure_is_distinct_from_empty_snapshot_and_recovers():
    service, client, model = _service(_row())
    service.refresh()
    model.update_running.reset_mock()
    client.list_windows.return_value = ()
    client.last_query_failed = True
    assert service.toggle_focus("firefox.desktop") is ActionResult.FAILED
    assert service.diagnostic_snapshot().status == "failed"
    assert len(service.list_windows("firefox.desktop")) == 1
    model.update_running.assert_not_called()
    client.window_action.assert_not_called()
    client.last_query_failed = False
    assert service.toggle_focus("firefox.desktop") is ActionResult.NOT_FOUND
    assert service.list_all_windows() == ()
    assert model.update_running.call_args.kwargs["running"] == {}
    client.list_windows.return_value = (_row(),)
    assert service.toggle_focus("firefox.desktop") is ActionResult.OK
    assert service.diagnostic_snapshot().status == "available"


@pytest.mark.parametrize("result", [ActionResult.FAILED, ActionResult.UNSUPPORTED])
def test_bulk_actions_report_failure_or_unsupported(result):
    service, client, _ = _service(_row(), _row(2))
    client.window_action.return_value = result
    assert service.minimize_all("firefox.desktop") is result
    assert service.close_all("firefox.desktop") is result


def test_lifecycle_starts_once_and_cancels_polling(monkeypatch):
    schedule = MagicMock(return_value=42)
    remove = MagicMock()
    monkeypatch.setattr(muffin.GLib, "timeout_add", schedule)
    monkeypatch.setattr(muffin.GLib, "source_remove", remove)
    service, client, model = _service(_row())
    service.start()
    service.start()
    schedule.assert_called_once_with(250, service._poll)
    client.list_windows.assert_called_once()
    service.stop()
    service.stop()
    remove.assert_called_once_with(42)
    assert service.list_all_windows() == ()
    assert service.diagnostic_snapshot().status == "stopped"
    assert model.update_running.call_args.kwargs["running"] == {}


def _proxy(value, success=True):
    return SimpleNamespace(
        call_sync=MagicMock(
            return_value=SimpleNamespace(unpack=lambda: (success, json.dumps(value)))
        )
    )


def test_shell_client_distinguishes_failed_and_empty_snapshots():
    proxy = _proxy([], success=False)
    client = CinnamonShellClient(proxy=proxy)
    assert client.list_windows() == ()
    assert client.last_query_failed
    proxy.call_sync.return_value = SimpleNamespace(unpack=lambda: (True, "[]"))
    assert client.list_windows() == ()
    assert not client.last_query_failed
    proxy.call_sync.return_value = SimpleNamespace(unpack=lambda: (True, "[{}, 1]"))
    assert client.list_windows() == ({},)


@pytest.mark.parametrize("result", ["ok", "not_found", "unsupported", None])
def test_shell_action_validates_generation_and_uses_workspace_aware_activation(result):
    proxy = _proxy(result)
    client = CinnamonShellClient(proxy=proxy)
    expected = ActionResult(result) if result else ActionResult.FAILED
    assert (
        client.window_action(
            WindowId(DisplayServer.WAYLAND, 'cinnamon:shell-"quoted:1'), "activate"
        )
        is expected
    )
    script = proxy.call_sync.call_args.args[1].unpack()[0]
    assert 'global._dockingWindowGeneration !== "shell-\\"quoted"' in script
    assert "w.get_stable_sequence() === 1" in script
    assert "imports.ui.main.activateWindow" in script
    assert "w.get_workspace()?.index()" in script


@pytest.mark.parametrize(
    "window_id",
    [
        WindowId.x11(1),
        WindowId(DisplayServer.WAYLAND, "muffin:1"),
        WindowId(DisplayServer.WAYLAND, "cinnamon:shell:0"),
        WindowId(DisplayServer.WAYLAND, "cinnamon:shell:1);evil()"),
        WindowId(DisplayServer.WAYLAND, "cinnamon:shell:²"),
        WindowId(DisplayServer.WAYLAND, "cinnamon::1"),
    ],
)
def test_shell_rejects_foreign_and_malformed_ids_without_bus_call(window_id):
    proxy = _proxy("ok")
    assert (
        CinnamonShellClient(proxy=proxy).window_action(window_id, "activate")
        is ActionResult.NOT_FOUND
    )
    proxy.call_sync.assert_not_called()


@pytest.mark.parametrize("action", ["minimize", "close"])
def test_shell_actions_check_live_window_permissions(action):
    proxy = _proxy("unsupported")
    client = CinnamonShellClient(proxy=proxy)
    assert (
        client.window_action(
            WindowId(DisplayServer.WAYLAND, "cinnamon:shell:1"), action
        )
        is ActionResult.UNSUPPORTED
    )
    script = proxy.call_sync.call_args.args[1].unpack()[0]
    assert f"!w.can_{action}()" in script
