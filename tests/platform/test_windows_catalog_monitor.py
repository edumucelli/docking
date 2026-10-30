"""Native catalog subscriptions own their windows, allocation and lifecycle."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.trash import windows_monitor
from docking.platform.backends.windows import catalog_monitor, shell_changes


@pytest.mark.parametrize("failure", [None, 1, 2])
def test_catalog_owns_all_folder_subscriptions_and_unwinds_partial_start(
    monkeypatch, failure
):
    opened, closed = [], []
    changed = Mock()

    def subscription(callback, *, folder_id, title):
        assert callback is changed and title == "Docking application catalog monitor"
        if len(opened) == failure:
            raise OSError(5, "Denied")
        opened.append(folder_id)
        return SimpleNamespace(close=lambda: closed.append(folder_id))

    monkeypatch.setattr(catalog_monitor, "ShellChangeMonitor", subscription)
    if failure is not None:
        with pytest.raises(OSError):
            catalog_monitor.CatalogChangeMonitor(changed)
    else:
        monitor = catalog_monitor.CatalogChangeMonitor(changed)
        assert len(set(opened)) == 3
        monitor.close()
        monitor.close()
    assert closed == list(reversed(opened))
    changed.assert_not_called()


def test_recycle_bin_wrapper_keeps_its_exact_folder_and_native_title(monkeypatch):
    initialize = Mock(return_value=None)
    monkeypatch.setattr(shell_changes.ShellChangeMonitor, "__init__", initialize)
    changed = Mock()
    windows_monitor.RecycleBinMonitor(changed)
    initialize.assert_called_once_with(
        changed,
        folder_id="b7534046-3ecb-4c18-be4e-64cd4cb7d6ac",
        title="Docking Recycle Bin monitor",
    )


def test_registry_closes_native_source_and_rejects_old_lifecycle_callbacks(
    tmp_path, monkeypatch
):
    from docking.platform.applications.registry import ApplicationRegistry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    registry = ApplicationRegistry(
        application_source=lambda: (), desktop_directories_source=lambda: ()
    )
    registry._app_monitor_factory = Mock(
        return_value=SimpleNamespace(connect=Mock(return_value=1), disconnect=Mock())
    )
    sources, callbacks, pending = [], [], []

    def source(callback):
        callbacks.append(callback)
        result = SimpleNamespace(close=Mock())
        sources.append(result)
        return result

    registry._change_monitor_factory = source
    registry._schedule_timeout = lambda _milliseconds, callback: (
        pending.append(callback) or len(pending)
    )
    registry._cancel_timeout = Mock(return_value=True)
    registry.start()
    registry.start()
    assert len(sources) == 1
    callbacks[0]()
    callbacks[0]()
    assert len(pending) == 1
    stale = pending[0]
    registry.stop()
    sources[0].close.assert_called_once()
    registry.start()
    generation = registry.generation
    callbacks[0]()
    assert stale() is False
    assert registry.generation == generation and len(pending) == 1
    callbacks[1]()
    assert len(pending) == 2
    registry.stop()
    registry.stop()
    sources[1].close.assert_called_once()


@pytest.mark.parametrize("failure", [None, "subclass", "folder", "register"])
def test_shared_shell_registration_uses_owned_pidl_and_cleans_partial_failure(
    monkeypatch, failure
):
    functions = {}
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "Native failure"), raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)

    def folder(_identifier, flags, token, output):
        assert flags == 0 and token is None
        output._obj.value = 202
        return -2147024891 if failure == "folder" else 0

    def register(hwnd, source_flags, event_flags, message, count, entry):
        assert hwnd == 101 and source_flags == 0x8003
        assert event_flags == 0x7FFFFFFF and count == 1
        assert message == shell_changes.ShellChangeMonitor._MESSAGE
        assert entry._obj.pidl == 202 and entry._obj.recursive == 1
        return 0 if failure == "register" else 303

    values = {
        "CreateWindowExW": 101,
        "RegisterWindowMessageW": 404,
        "SetWindowSubclass": 0 if failure == "subclass" else 1,
    }

    def bind(_library, name, *_signature):
        function = Mock(
            side_effect=folder
            if name == "SHGetKnownFolderIDList"
            else register
            if name == "SHChangeNotifyRegister"
            else None,
            return_value=values.get(name, 1),
        )
        functions[name] = function
        return function

    monkeypatch.setattr(shell_changes, "_bind", bind)
    if failure is not None:
        with pytest.raises(OSError):
            shell_changes.ShellChangeMonitor(
                Mock(),
                folder_id="a77f5d77-2e2b-44c3-a6a2-aba601054a51",
                title="Owned catalog observer",
            )
    else:
        monitor = shell_changes.ShellChangeMonitor(
            Mock(),
            folder_id="a77f5d77-2e2b-44c3-a6a2-aba601054a51",
            title="Owned catalog observer",
        )
        monitor.close()
        monitor.close()
        functions["SHChangeNotifyDeregister"].assert_called_once_with(303)
    functions["DestroyWindow"].assert_called_once_with(101)
    functions["RemoveWindowSubclass"].assert_called_once()
    if failure != "subclass":
        assert functions["CoTaskMemFree"].call_args.args[0].value == 202
    else:
        functions["CoTaskMemFree"].assert_not_called()
