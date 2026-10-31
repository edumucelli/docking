"""Native catalog subscriptions own their windows, allocation and lifecycle."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.trash import windows_monitor
from docking.platform.backends.windows import catalog_monitor, shell_changes


@pytest.fixture
def catalog_runtime(monkeypatch):
    shell = [(101, 11)]
    idle, timers, removed = {}, {}, []
    serial = 0

    def attach(collection, callback):
        nonlocal serial
        serial += 1
        collection[serial] = callback
        return serial

    def timeout(milliseconds, callback):
        assert milliseconds == 500
        return attach(timers, callback)

    def remove(source):
        removed.append(source)
        return idle.pop(source, timers.pop(source, None)) is not None

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )
    monkeypatch.setattr(
        catalog_monitor, "explorer_tray", lambda _user, *, exclude_pid: shell[0]
    )
    monkeypatch.setattr(catalog_monitor.GLib, "timeout_add", timeout)
    monkeypatch.setattr(
        catalog_monitor.GLib, "idle_add", lambda callback: attach(idle, callback)
    )
    monkeypatch.setattr(catalog_monitor.GLib, "source_remove", remove)
    return SimpleNamespace(shell=shell, idle=idle, timers=timers, removed=removed)


@pytest.mark.parametrize("failure", [None, 1, 2])
def test_catalog_owns_all_folder_subscriptions_and_unwinds_partial_start(
    monkeypatch, failure, catalog_runtime
):
    opened, closed = [], []
    changed = Mock()

    def subscription(callback, *, folder_id, title):
        assert callable(callback) and title == "Docking application catalog monitor"
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
    assert not catalog_runtime.idle and not catalog_runtime.timers
    changed.assert_not_called()


def test_shell_identity_changes_reconnect_once_without_catalog_polling(
    monkeypatch, catalog_runtime
):
    changed = Mock()
    subscriptions = []

    def subscribe(callback, *, folder_id, title):
        monitor = SimpleNamespace(close=Mock(), reconnect=Mock(side_effect=callback))
        subscriptions.append(monitor)
        return monitor

    monkeypatch.setattr(catalog_monitor, "ShellChangeMonitor", subscribe)
    monitor = catalog_monitor.CatalogChangeMonitor(changed)
    tick = next(iter(catalog_runtime.timers.values()))
    assert tick() is True
    assert not catalog_runtime.idle
    for subscription in subscriptions:
        subscription.reconnect.assert_not_called()

    for identity in (None, (202, 22)):
        catalog_runtime.shell[0] = identity
        assert tick() is True
        assert tick() is True
        assert len(catalog_runtime.idle) == 1
        callback = catalog_runtime.idle.popitem()[1]
        assert callback() is False
    assert changed.call_count == 2
    for subscription in subscriptions:
        assert subscription.reconnect.call_count == 2
    monitor.close()


def test_one_native_callback_reconnects_all_folders_before_the_identity_timer(
    monkeypatch, catalog_runtime
):
    changed = Mock()
    callbacks, subscriptions = [], []

    def subscribe(callback, *, folder_id, title):
        callbacks.append(callback)
        monitor = SimpleNamespace(close=Mock(), reconnect=Mock(side_effect=callback))
        subscriptions.append(monitor)
        return monitor

    monkeypatch.setattr(catalog_monitor, "ShellChangeMonitor", subscribe)
    monitor = catalog_monitor.CatalogChangeMonitor(changed)
    tick = next(iter(catalog_runtime.timers.values()))
    catalog_runtime.shell[0] = (202, 22)
    # Only one of the native windows receives the replacement broadcast.
    callbacks[0]()
    assert tick() is True
    assert tick() is True
    for subscription in subscriptions:
        subscription.reconnect.assert_called_once_with()
    assert len(catalog_runtime.idle) == 1
    pending = catalog_runtime.idle.popitem()[1]
    assert pending() is False
    changed.assert_called_once_with()

    callbacks[0]()
    stale = catalog_runtime.idle.popitem()[1]
    monitor.close()
    assert tick() is False and stale() is False
    callbacks[1]()
    assert not catalog_runtime.idle and not catalog_runtime.timers
    changed.assert_called_once_with()
    for subscription in subscriptions:
        subscription.close.assert_called_once_with()


def test_failed_timer_start_unwinds_pending_native_notification_and_all_folders(
    monkeypatch, catalog_runtime
):
    closed, pending = [], []
    changed = Mock()

    def subscribe(callback, *, folder_id, title):
        callback()
        pending.extend(catalog_runtime.idle.values())
        return SimpleNamespace(close=lambda: closed.append(folder_id))

    def fail_timer(_milliseconds, _callback):
        raise OSError(5, "Owned timer setup failed")

    monkeypatch.setattr(catalog_monitor, "ShellChangeMonitor", subscribe)
    monkeypatch.setattr(catalog_monitor.GLib, "timeout_add", fail_timer)
    with pytest.raises(OSError, match="Owned timer setup failed"):
        catalog_monitor.CatalogChangeMonitor(changed)
    assert len(closed) == 3
    assert not catalog_runtime.idle and not catalog_runtime.timers
    assert all(callback() is False for callback in pending)
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
