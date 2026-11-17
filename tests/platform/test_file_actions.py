"""File actions keep paths out of command text and own chooser resources."""

import errno
import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from gi.repository import Gio, GLib

from docking.platform import commands
from docking.platform.desktop.base import OpenWithMode, OpenWithResult
from docking.platform.desktop.implementations.unix import file_actions as unix
from docking.platform.desktop.implementations.windows import file_actions as windows
from docking.platform.desktop.implementations.windows.backend import (
    WindowsDesktopServices,
)
from docking.platform.native.windows import shell
from tests.platform_factories import make_target_service


@pytest.mark.parametrize("mode", list(commands.TerminalMode))
def test_interactive_terminal_uses_shell_without_a_command(mode, monkeypatch):
    monkeypatch.setattr(
        commands,
        "find_terminal",
        lambda **kw: commands.ResolvedTerminal("terminal", ("-e",), mode),
    )
    assert commands.build_terminal_argv(shell="/bin/sh") == [
        "terminal",
        "-e",
        "/bin/sh",
    ]


@pytest.mark.parametrize("sandboxed", [False, True])
def test_unix_terminal_passes_directory_separately(tmp_path, monkeypatch, sandboxed):
    folder = tmp_path / "a ' $(touch NO) ; résumé # %"
    folder.mkdir()
    argv = ["terminal", "-e", "/bin/sh"]
    monkeypatch.setattr(unix.commands, "build_terminal_argv", lambda: argv)
    monkeypatch.setattr(unix, "is_flatpak", lambda: sandboxed)
    host = Mock(return_value=["flatpak-spawn", "--host", "terminal"])
    monkeypatch.setattr(unix.flatpak, "host_command", host)
    spawn = Mock()
    monkeypatch.setattr(unix.subprocess, "Popen", spawn)
    assert unix.open_in_terminal(folder)
    assert spawn.call_args.kwargs["shell"] is False
    if sandboxed:
        host.assert_called_once_with(argv, working_directory=str(folder))
        assert spawn.call_args.kwargs["cwd"] is None
    else:
        assert spawn.call_args.args[0] == argv
        assert spawn.call_args.kwargs["cwd"] == str(folder)
        host.assert_not_called()


def test_unix_missing_terminal_and_host_transport_never_launch(monkeypatch, tmp_path):
    spawn = Mock()
    monkeypatch.setattr(unix.subprocess, "Popen", spawn)
    monkeypatch.setattr(unix.commands, "build_terminal_argv", lambda: None)
    assert not unix.open_in_terminal(tmp_path)
    monkeypatch.setattr(unix.commands, "build_terminal_argv", lambda: ["terminal"])
    monkeypatch.setattr(unix, "is_flatpak", lambda: True)
    monkeypatch.setattr(unix.flatpak, "host_command", lambda *a, **kw: None)
    assert not unix.open_in_terminal(tmp_path)
    spawn.assert_not_called()


def test_windows_terminal_uses_new_console_and_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    spawn = Mock()
    monkeypatch.setattr(windows.subprocess, "Popen", spawn)
    assert windows.open_in_terminal(tmp_path)
    argv = spawn.call_args.args[0]
    assert argv[0].endswith("powershell.exe") and argv[1:] == ["-NoLogo"]
    assert spawn.call_args.kwargs == {
        "cwd": str(tmp_path),
        "shell": False,
        "creationflags": 0x10,
    }
    spawn.side_effect = OSError("unavailable")
    assert not windows.open_in_terminal(tmp_path)
    assert WindowsDesktopServices().open_with_mode(is_dir=True) is None
    assert WindowsDesktopServices().open_with_mode(is_dir=False) is OpenWithMode.NATIVE


def test_target_preparation_rejects_missing_and_non_files(tmp_path):
    service = make_target_service(
        icon_loader=MagicMock(), application_registry=MagicMock()
    )
    assert service.prepare_open_with(tmp_path.as_uri()) == (
        tmp_path.as_uri(),
        "inode/directory",
    )
    with pytest.raises(FileNotFoundError):
        service.prepare_open_with((tmp_path / "absent").as_uri())
    with pytest.raises(ValueError):
        service.prepare_open_with("https://example.com/")


def test_target_terminal_rejects_files_before_platform_launch(tmp_path, monkeypatch):
    from docking.platform import targets

    file = tmp_path / "document.txt"
    file.write_text("owned fixture")
    desktop = Mock()
    monkeypatch.setattr(targets, "desktop_services", lambda: desktop)
    service = make_target_service(
        icon_loader=MagicMock(), application_registry=MagicMock()
    )
    assert not service.open_in_terminal(file.as_uri())
    assert not service.open_in_terminal("sftp://example.com/folder")
    desktop.open_in_terminal.assert_not_called()
    assert service.open_in_terminal(tmp_path.as_uri())
    desktop.open_in_terminal.assert_called_once_with(tmp_path)


@pytest.mark.parametrize(
    "status,expected",
    [
        (0, OpenWithResult.ACCEPTED),
        (-2147023673, OpenWithResult.CANCELLED),
        (-1, OpenWithResult.FAILED),
    ],
)
def test_windows_native_chooser_uses_owned_single_file_execution(
    monkeypatch, status, expected
):
    seen = []

    def show(owner, info):
        seen.append((owner, info._obj.file, info._obj.flags))
        return status

    monkeypatch.setattr(shell, "apartment", nullcontext)
    monkeypatch.setattr(shell, "_bind", lambda *a: show)
    from docking.platform.native.windows import ffi

    monkeypatch.setattr(
        ffi.windows_ctypes, "WinDLL", lambda *a: object(), raising=False
    )
    assert shell.open_with_dialog("C:\\owned\\résumé.txt", 42) is expected
    assert seen == [(42, "C:\\owned\\résumé.txt", 4)]


@pytest.fixture
def portal(monkeypatch, tmp_path):
    if os.name == "nt":
        pytest.skip("Unix file descriptors are unavailable on Windows")
    monkeypatch.setattr(unix, "_parent_identifier", lambda parent: "")
    watch = Mock(return_value=8)
    unwatch = Mock()
    monkeypatch.setattr(unix.Gio, "bus_watch_name_on_connection", watch)
    monkeypatch.setattr(unix.Gio, "bus_unwatch_name", unwatch)
    results = []
    chooser = unix._PortalChooser(tmp_path.as_uri(), object(), results.append)
    bus = MagicMock()
    bus.get_unique_name.return_value = ":1.42"
    bus.signal_subscribe.return_value = 7
    descriptors = Gio.UnixFDList.new()
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        descriptors.append(fd)
    finally:
        os.close(fd)
    chooser._ready((bus, descriptors, False))
    bus.call_with_unix_fd_list_finish.return_value = (
        GLib.Variant("(o)", (chooser._handle,)),
        None,
    )
    yield SimpleNamespace(
        chooser=chooser,
        bus=bus,
        descriptors=descriptors,
        results=results,
        watch=watch,
        unwatch=unwatch,
    )
    bus.call_with_unix_fd_list.call_args.args[-1](bus, object())
    chooser.cancel()


@pytest.mark.parametrize(
    "response,expected",
    [
        (0, OpenWithResult.ACCEPTED),
        (1, OpenWithResult.CANCELLED),
        (2, OpenWithResult.FAILED),
    ],
)
def test_portal_asks_before_opening_and_handles_response_once(
    portal, response, expected
):
    args = portal.bus.call_with_unix_fd_list.call_args.args
    assert args[3] == "OpenFile"
    parent, fd, options = args[4].unpack()
    assert parent == "" and fd == 0 and options["ask"] is True
    portal.chooser._response(
        None, None, "", "", "", GLib.Variant("(ua{sv})", (response, {}))
    )
    portal.chooser._response(
        None, None, "", "", "", GLib.Variant("(ua{sv})", (response, {}))
    )
    assert portal.results == [expected]
    portal.bus.signal_unsubscribe.assert_called_once_with(7)
    portal.unwatch.assert_called_once_with(8)


def test_portal_cancel_closes_request_and_ignores_late_response(portal):
    portal.chooser.cancel()
    assert portal.chooser._cancellable.is_cancelled()
    assert portal.bus.call.call_args.args[3] == "Close"
    portal.chooser._response(None, None, "", "", "", GLib.Variant("(ua{sv})", (0, {})))
    assert portal.results == [OpenWithResult.CANCELLED]


def test_portal_method_failure_closes_descriptors_and_reports_failure(portal):
    portal.bus.call_with_unix_fd_list_finish.side_effect = GLib.Error("owned failure")
    callback = portal.bus.call_with_unix_fd_list.call_args.args[-1]
    callback(portal.bus, object())
    assert portal.descriptors.get_length() == 0
    assert portal.results == [OpenWithResult.FAILED]
    portal.bus.call_with_unix_fd_list_finish.side_effect = None


def test_portal_owner_loss_finishes_and_cleans_up_once(portal):
    vanished = portal.watch.call_args.args[-1]
    vanished(portal.bus, unix.PORTAL_NAME)
    vanished(portal.bus, unix.PORTAL_NAME)
    assert portal.results == [OpenWithResult.FAILED]
    portal.unwatch.assert_called_once_with(8)
    portal.bus.signal_unsubscribe.assert_called_once_with(7)


@pytest.mark.skipif(os.name == "nt", reason="Unix descriptor access modes")
@pytest.mark.parametrize("kind", ["file", "directory", "readonly"])
def test_portal_prepares_descriptor_with_matching_write_access(
    monkeypatch, tmp_path, kind
):
    import fcntl

    path = tmp_path if kind == "directory" else tmp_path / "owned.txt"
    if kind != "directory":
        path.write_text("owned fixture")
    if kind == "readonly":
        opening = os.open

        def read_only(path, flags):
            if flags & os.O_ACCMODE == os.O_RDWR:
                raise PermissionError(errno.EACCES, "owned read-only fixture")
            return opening(path, flags)

        monkeypatch.setattr(unix.os, "open", read_only)
    bus = MagicMock()
    bus.call_sync.return_value = GLib.Variant("(v)", (GLib.Variant("u", 3),))
    monkeypatch.setattr(unix.Gio, "bus_get_sync", lambda *_: bus)
    monkeypatch.setattr(unix, "_parent_identifier", lambda _: "")
    chooser = unix._PortalChooser(path.as_uri(), object(), Mock())
    prepared = chooser._prepare()
    assert prepared is not None
    _bus, descriptors, writable = prepared
    fd = descriptors.get(0)
    try:
        assert writable is (kind == "file")
        assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == (
            os.O_RDWR if writable else os.O_RDONLY
        )
    finally:
        os.close(fd)
        chooser._close_descriptors(descriptors)


def test_windows_cancellation_during_target_validation_does_not_open_chooser(
    monkeypatch,
):
    worker = Mock()
    monkeypatch.setattr(windows, "BackgroundWorker", lambda: worker)
    monkeypatch.setattr(windows, "window_handle", lambda window: 42)
    path = Mock()
    monkeypatch.setattr(windows, "local_path_from_uri_or_path", lambda uri: path)
    opened = Mock()
    monkeypatch.setattr(shell, "open_with_dialog", opened)
    cancel = windows.request_open_with(
        "file:///owned/file.txt", parent=MagicMock(), on_result=Mock()
    )
    path.is_file.side_effect = lambda: cancel() or True
    assert worker.run.call_args.kwargs["fn"]() is OpenWithResult.CANCELLED
    opened.assert_not_called()
