"""Recycle Bin user confirmation, failures and owned shell notification cleanup."""

import ctypes
import sys
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from gi.repository import Gio

from docking.applets.trash.implementations.windows import backend as windows
from docking.platform import environment
from docking.platform.composition.windows import WindowsAppletBackendFactories
from docking.platform.native.windows import shell_changes


def test_windows_selection_does_not_inspect_unix_trash(monkeypatch):
    monkeypatch.setattr(environment, "is_windows", lambda: True)
    native = Mock()
    monkeypatch.setattr(windows, "WindowsRecycleBin", lambda: native)
    assert isinstance(
        WindowsAppletBackendFactories().trash(), windows.WindowsTrashBackend
    )


def test_empty_requires_confirmation_and_cancel_preserves_contents():
    native = Mock()
    native.count.return_value = 3
    backend = windows.WindowsTrashBackend(native=native)
    confirm = Mock(return_value=False)
    result = backend.empty(confirm)
    assert not result.attempted and result.remaining_count == 3
    confirm.assert_called_once_with()
    native.empty.assert_not_called()


def test_empty_reports_the_actual_remaining_contents():
    native = Mock()
    native.count.side_effect = [4, 1]
    backend = windows.WindowsTrashBackend(native=native)
    result = backend.empty(lambda: True)
    assert result.attempted and result.before_count == 4 and result.remaining_count == 1
    native.empty.assert_called_once_with()


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("shell failed")])
def test_failed_empty_recounts_and_preserves_failure_for_ui(error):
    native = Mock()
    native.count.side_effect = [4, 2]
    native.empty.side_effect = error
    result = windows.WindowsTrashBackend(native=native).empty(lambda: True)
    assert result.attempted and result.remaining_count == 2 and result.failures == 1
    assert result.permission_denied == isinstance(error, PermissionError)


def test_failed_query_keeps_last_known_count_and_cannot_trigger_deletion():
    native = Mock()
    native.count.side_effect = [
        2,
        OSError("offline"),
        OSError("offline"),
        OSError("offline"),
    ]
    backend = windows.WindowsTrashBackend(native=native)
    assert backend.count_items() == 2
    assert backend.count_items() == 2
    result = backend.empty(lambda: True)
    assert result.failures and result.remaining_count == 2
    native.empty.assert_not_called()


def test_already_empty_does_not_prompt_or_call_empty():
    native = Mock()
    native.count.return_value = 0
    confirm = Mock()
    result = windows.WindowsTrashBackend(native=native).empty(confirm)
    assert not result.attempted and result.remaining_count == 0
    confirm.assert_not_called()
    native.empty.assert_not_called()


def test_open_uses_the_native_recycle_bin_shell_folder(monkeypatch):
    launch = Mock(return_value=True)
    monkeypatch.setattr(windows, "open_default_target", launch)
    windows.WindowsTrashBackend(native=Mock()).open()
    launch.assert_called_once_with("shell:RecycleBinFolder")


@pytest.mark.parametrize(
    "path",
    [
        r"\\server\share\keep.txt",
        "//server/share/keep.txt",
        r"\\server/share\keep.txt",
        r"relative.txt",
        r"C:relative.txt",
    ],
)
def test_unsupported_drop_does_not_start_a_file_operation(path):
    native = windows.WindowsRecycleBin.__new__(windows.WindowsRecycleBin)
    native._drive_type = Mock(return_value=3)
    assert not native.recycle(path)
    native._drive_type.assert_not_called()


def test_mapped_network_drive_is_not_permanently_deleted():
    native = windows.WindowsRecycleBin.__new__(windows.WindowsRecycleBin)
    native._drive_type = Mock(return_value=4)
    assert not native.recycle(r"Z:\keep.txt")
    native._drive_type.assert_called_once_with("Z:\\")


def test_native_drop_receives_the_decoded_filename():
    native = Mock()
    native.recycle.return_value = True
    backend = windows.WindowsTrashBackend(native=native)
    file = Gio.File.new_for_path("/local/resumé # 100%.txt")
    assert backend.trash_file(file)
    native.recycle.assert_called_once_with(file.get_path())


@pytest.mark.parametrize("failed", [None, "parse", 5, 18, 21, 22])
def test_file_operation_preserves_recycle_warning_and_releases_on_failure(
    monkeypatch, failed
):
    events = []

    @contextmanager
    def apartment():
        events.append("enter")
        try:
            yield object()
        finally:
            events.append("exit")

    def bind(_library, name, *_signature):
        def invoke(*args):
            events.append(name)
            if name == "SHCreateItemFromParsingName" and failed == "parse":
                return -2147024891
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = (
                11 if name == "CoCreateInstance" else 22
            )
            return 0

        return invoke

    def method(_pointer, index, *_signature):
        def invoke(_this, *args):
            events.append(index)
            if index == 5:
                assert args[0] & 0x80000  # FOFX_RECYCLEONDELETE
                assert args[0] & 0x4000  # FOF_WANTNUKEWARNING
                assert not args[0] & 0x10  # Do not confirm permanent deletion.
            if index == failed:
                return -2147024891
            if index == 22:
                ctypes.cast(args[0], ctypes.POINTER(ctypes.c_int32))[0] = 0
            return 0

        return invoke

    monkeypatch.setattr(windows, "apartment", apartment)
    monkeypatch.setattr(windows, "_bind", bind)
    monkeypatch.setattr(windows, "method", method)
    monkeypatch.setattr(
        windows, "release", lambda p: events.append(("release", p.value))
    )
    native = windows.WindowsRecycleBin.__new__(windows.WindowsRecycleBin)
    native._drive_type = Mock(return_value=3)
    native._shell = object()
    if failed is None:
        assert native.recycle(r"C:\résumé # 100%.txt")
    else:
        with pytest.raises(PermissionError):
            native.recycle(r"C:\résumé # 100%.txt")
    assert events[-1] == "exit"
    assert ("release", 11) in events
    if failed not in (5, "parse"):
        assert ("release", 22) in events
    if failed in (5, "parse", 18):
        assert 21 not in events  # No operation runs after an earlier failure.


@pytest.fixture
def observer(monkeypatch):
    monitor = shell_changes.ShellChangeMonitor.__new__(shell_changes.ShellChangeMonitor)
    monitor._hwnd = 101
    monitor._pidl = ctypes.c_void_p(202)
    monitor._registration = 303
    monitor._source = 0
    monitor._reconnect = False
    monitor._closed = False
    monitor._subclass_id = 404
    monitor._callback = Mock()
    monitor._changed = Mock()
    monitor._lock = Mock(return_value=505)
    monitor._unlock = Mock()
    monitor._next = Mock(return_value=0)
    monitor._taskbar_created = 606
    monitor._subscribe = Mock()
    timeline = Mock()
    for name in ("_deregister", "_remove", "_destroy", "_free"):
        setattr(monitor, name, getattr(timeline, name))
    pending = {}

    def enqueue(callback):
        pending[1] = callback
        return 1

    monkeypatch.setattr(shell_changes.GLib, "idle_add", enqueue)
    monkeypatch.setattr(
        shell_changes.GLib, "source_remove", lambda key: pending.pop(key)
    )
    return monitor, pending, timeline


def test_native_notifications_unlock_packets_and_coalesce_before_gtk(observer):
    monitor, pending, _timeline = observer
    for _ in range(3):
        assert monitor._message(101, monitor._MESSAGE, 77, 88, 0, 0) == 0
    assert monitor._lock.call_count == monitor._unlock.call_count == 3
    monitor._changed.assert_not_called()
    assert len(pending) == 1
    assert not pending.pop(1)()
    monitor._changed.assert_called_once_with()


def test_close_removes_pending_refresh_then_unsubscribes_and_releases_native_resources(
    observer,
):
    monitor, pending, timeline = observer
    monitor._queue()
    monitor.close()
    monitor.close()
    assert not pending
    assert [call[0] for call in timeline.mock_calls] == [
        "_deregister",
        "_remove",
        "_destroy",
        "_free",
    ]
    assert (
        monitor._registration == 0 and monitor._hwnd is None and not monitor._pidl.value
    )
    monitor._changed.assert_not_called()


def test_shell_restart_reconnects_from_idle_before_refreshing(observer):
    monitor, pending, _timeline = observer
    monitor._message(101, monitor._taskbar_created, 0, 0, 0, 0)
    monitor._subscribe.assert_not_called()
    pending.pop(1)()
    monitor._subscribe.assert_called_once_with()
    monitor._changed.assert_called_once_with()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows file URI decoding")
@pytest.mark.parametrize(
    "path", [r"C:\résumé # 100%.txt", r"\\server\share\résumé # 100%.txt"]
)
def test_native_drop_uri_keeps_drive_and_unc_authority(path):
    from pathlib import Path

    native = Mock(recycle=Mock(return_value=True))
    backend = windows.WindowsTrashBackend(native=native)
    for value in (path, Path(path).as_uri()):
        assert backend.trash_uris([value])
        native.recycle.assert_called_once_with(str(Path(path)))
        native.recycle.reset_mock()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows shell ABI")
def test_native_query_has_the_sdk_layout_and_only_reads_state():
    info = windows.RecycleBinInfo
    assert ctypes.sizeof(info) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)
    assert windows.WindowsRecycleBin().count() >= 0
