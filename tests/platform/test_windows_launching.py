"""Native shell launch lifetime, exit status, and exact launcher provenance."""

import ctypes
import gc
import sys
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.applications.backend import LaunchOutcome, ProcessBackend
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.types import MatchMethod
from docking.platform.native.windows import launching, shell
from tests.platform.application_fakes import ApplicationRegistryStub, application


@pytest.fixture
def native(monkeypatch):
    def code(_handle, output):
        output._obj.value = 259
        return 1

    def execute(pointer):
        info = pointer._obj
        info.process = 71
        return 1

    kernel = SimpleNamespace(
        CloseHandle=Mock(return_value=1),
        GetProcessId=Mock(return_value=42),
        WaitForSingleObject=Mock(return_value=258),
        GetExitCodeProcess=Mock(side_effect=code),
    )
    library = SimpleNamespace(ShellExecuteExW=Mock(side_effect=execute))
    monkeypatch.setattr(
        launching.ctypes,
        "WinDLL",
        lambda name, **_kw: kernel if name == "kernel32" else library,
        raising=False,
    )
    monkeypatch.setattr(launching, "apartment", nullcontext)
    return kernel, library


def test_execute_info_uses_the_sdk_pointer_layout():
    info = launching.ShellExecuteInfo
    assert ctypes.sizeof(info) == (112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 60)
    assert info.process.offset == (104 if ctypes.sizeof(ctypes.c_void_p) == 8 else 56)


def test_shell_retains_literal_shortcut_semantics_and_an_optional_process(native):
    kernel, library = native
    target = r"C:\Profiles\résumé & $x #100%.lnk"
    outcome = launching.launch_shortcut(target)
    assert outcome.started and outcome.process.pid == 42
    info = library.ShellExecuteExW.call_args.args[0]._obj
    assert info.file == target and info.verb == "open"
    assert info.parameters is None and info.directory is None
    assert info.mask == 0x440 and info.size == ctypes.sizeof(info)
    assert outcome.process.poll() is None
    kernel.WaitForSingleObject.assert_called_once_with(71, 0)
    kernel.CloseHandle.assert_not_called()


def test_selected_file_parameters_are_literal_and_do_not_replace_link_options(native):
    import subprocess

    _kernel, library = native
    target = r"C:\Pins\selected profile.lnk"
    paths = (r"C:\Documents\résumé & #100%.txt", r"\\fileserver\team\日本語.txt")
    assert launching.launch_shortcut(target, arguments=paths).started
    info = library.ShellExecuteExW.call_args.args[0]._obj
    assert info.file == target
    assert info.parameters == subprocess.list2cmdline(paths)
    assert info.directory is None and info.show == 1


@pytest.mark.parametrize(
    "target,expected",
    [
        ("C:/Pins/résumé & #100%.lnk", r"C:\Pins\résumé & #100%.lnk"),
        ("//server/share/selected profile.lnk", r"\\server\share\selected profile.lnk"),
        (r"C:\Pins\literal #100%.lnk", r"C:\Pins\literal #100%.lnk"),
        (
            "https://example.com/a/b?path=C:/literal",
            "https://example.com/a/b?path=C:/literal",
        ),
    ],
)
def test_shell_normalizes_only_absolute_native_targets(native, target, expected):
    _kernel, library = native
    assert launching.launch_shortcut(target).started
    info = library.ShellExecuteExW.call_args.args[0]._obj
    assert info.file == expected
    assert info.mask == 0x440
    assert info.parameters is None and info.directory is None


def test_failed_shell_launch_has_no_error_dialog_and_closes_its_returned_handle(native):
    kernel, library = native

    def denied(pointer):
        info = pointer._obj
        assert info.mask == 0x440 and info.file == r"\\server\share\missing.lnk"
        info.process = 71
        return 0

    library.ShellExecuteExW.side_effect = denied
    assert launching.launch_shortcut(
        "//server/share/missing.lnk", arguments=(r"C:\literal & #100%.txt",)
    ) == LaunchOutcome(False)
    kernel.CloseHandle.assert_called_once_with(71)


@pytest.mark.parametrize("arguments", [("bad\0.txt",), ("x" * 32767,), ("😀" * 16384,)])
def test_invalid_or_overlong_file_arguments_never_start_a_partial_launch(
    native, arguments
):
    _kernel, library = native
    assert launching.launch_shortcut(
        r"C:\Pins\Selected.lnk", arguments=arguments
    ) == LaunchOutcome(False)
    library.ShellExecuteExW.assert_not_called()


def test_signaled_process_can_really_exit_with_still_active_numeric_status(native):
    kernel, _library = native
    process = launching.WindowsLaunchedProcess(71)
    kernel.WaitForSingleObject.return_value = 0
    assert process.poll() == 259
    assert process.poll() == 259
    kernel.WaitForSingleObject.assert_called_once_with(71, 0)
    kernel.CloseHandle.assert_called_once_with(71)
    del process
    gc.collect()
    kernel.CloseHandle.assert_called_once_with(71)


@pytest.mark.parametrize("error", ["wait", "exit"])
def test_failed_poll_does_not_invent_a_process_exit_and_retains_handle_ownership(
    native, error
):
    kernel, _library = native
    process = launching.WindowsLaunchedProcess(71)
    kernel.WaitForSingleObject.return_value = 0xFFFFFFFF if error == "wait" else 0
    if error == "exit":
        kernel.GetExitCodeProcess.side_effect = None
        kernel.GetExitCodeProcess.return_value = 0
    with pytest.raises(OSError):
        process.poll()
    kernel.CloseHandle.assert_not_called()
    del process
    gc.collect()
    kernel.CloseHandle.assert_called_once_with(71)


def test_shell_activation_without_a_new_process_is_still_successful(native):
    kernel, library = native
    library.ShellExecuteExW.side_effect = lambda _pointer: 1
    outcome = launching.launch_shortcut(r"C:\fixture.lnk")
    assert outcome == LaunchOutcome(True)
    kernel.GetProcessId.assert_not_called()
    kernel.CloseHandle.assert_not_called()


def test_failed_shell_call_releases_any_returned_process_handle(native):
    kernel, library = native

    def fail(pointer):
        pointer._obj.process = 71
        return 0

    library.ShellExecuteExW.side_effect = fail
    assert launching.launch_shortcut(r"C:\fixture.lnk") == LaunchOutcome(False)
    kernel.CloseHandle.assert_called_once_with(71)
    kernel.GetProcessId.assert_not_called()


def test_missing_process_id_keeps_launch_success_without_leaking_handle(native):
    kernel, _library = native
    kernel.GetProcessId.return_value = 0
    assert launching.launch_shortcut(r"C:\fixture.lnk") == LaunchOutcome(True)
    gc.collect()
    kernel.CloseHandle.assert_called_once_with(71)


def test_provenance_eviction_releases_the_live_native_handle(native):
    kernel, _library = native
    store = LaunchProvenanceStore(max_records=1)
    first = launching.WindowsLaunchedProcess(71)
    store.record_launch(process=first, desktop_id="one", executable_path=None)
    second = SimpleNamespace(pid=43, poll=lambda: None)
    store.record_launch(process=second, desktop_id="two", executable_path=None)
    kernel.CloseHandle.assert_not_called()
    del first
    gc.collect()
    kernel.CloseHandle.assert_called_once_with(71)
    assert store.provenance_for_pid(42) is None
    assert store.provenance_for_pid(43).desktop_id == "two"


def test_same_executable_launchers_match_the_process_they_actually_started(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(sys.modules, "psutil", None)
    executable = tmp_path / "shared.exe"
    apps = tuple(
        replace(
            application(name, wm_class="shared"),
            executable_path=executable,
            exec_line="",
            launch_target=f"C:/{name}.lnk",
        )
        for name in ("one", "two")
    )
    registry = ApplicationRegistryStub(apps)
    store = LaunchProvenanceStore()
    processes = [SimpleNamespace(pid=pid, poll=lambda: None) for pid in (42, 43)]
    activate = Mock(side_effect=[LaunchOutcome(True, process) for process in processes])
    monkeypatch.setattr(shell, "launch_application_target", activate)
    from docking.platform.applications.implementations.windows.launch import (
        WindowsApplicationLaunchBackend,
    )

    launcher = ApplicationLauncher(
        registry, store, backend=WindowsApplicationLaunchBackend()
    )
    identity = ProcessIdentityService(
        store,
        backend=Mock(
            spec=ProcessBackend,
            executable=Mock(return_value=executable),
            script=Mock(return_value=None),
            sandbox_app_id=Mock(return_value=None),
            steam_app_id=Mock(return_value=None),
        ),
    )
    matcher = AppIdMatcher(registry, identity)
    for app in apps:
        assert launcher.launch(app.desktop_id)
    for app, process in zip(apps, processes, strict=True):
        match = matcher.match_result("shared", process_id=process.pid)
        assert match.desktop_id == app.desktop_id
        assert match.evidence.method is MatchMethod.LAUNCH_PROVENANCE
