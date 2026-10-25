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
