"""Hosted desktop fixes must never become general process/window cleanup."""

import ctypes as c
import io
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import hosted_desktop as hosted

ROOT = r"C:\Windows"
ENV = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}
FACTS = {"product_type": 1, "build": 26200, "native_machine": 0xAA64}


def identity(name, path, family=None, **changes):
    return replace(hosted.Identity(10, 20, 3, 40, name, path, family), **changes)


def window(**changes):
    return {
        "hwnd": 100,
        "class": "Windows.UI.Core.CoreWindow",
        "title": "Start",
        "minimized": False,
        "cloaked": False,
        **changes,
    }


@pytest.mark.parametrize(
    "platform,environment,facts",
    [
        ("linux", ENV, FACTS),
        ("win32", {}, FACTS),
        ("win32", {**ENV, "GITHUB_ACTIONS": "false"}, FACTS),
        ("win32", {**ENV, "RUNNER_ENVIRONMENT": "self-hosted"}, FACTS),
        ("win32", ENV, {**FACTS, "product_type": 3}),
        ("win32", ENV, {**FACTS, "build": 19045}),
        ("win32", ENV, {**FACTS, "native_machine": 0x8664}),
    ],
)
def test_no_mutation_is_authorized_on_normal_self_hosted_or_non_arm_windows(
    platform, environment, facts
):
    with pytest.raises(hosted.PreparationError):
        hosted.require_hosted(platform, environment, facts)


def test_actual_native_arm_client_is_required_even_for_emulated_python():
    hosted.require_hosted("win32", ENV, FACTS)


@pytest.mark.parametrize(
    "process",
    [
        identity("useroobebroker.exe", ROOT + r"\System32\oobe\UserOOBEBroker.exe"),
        identity("wwahost.exe", ROOT + r"\System32\WWAHost.exe", hosted._CLOUD),
    ],
)
def test_only_exact_system_first_login_processes_can_be_terminated(process):
    assert (
        hosted.action_for(process, None, root=ROOT, session=3, foreground=100)
        == "terminate"
    )
    assert (
        hosted.action_for(
            replace(process, session=4), None, root=ROOT, session=3, foreground=100
        )
        is None
    )
    with pytest.raises(hosted.PreparationError, match="path"):
        hosted.action_for(
            replace(process, path="C:/owned/" + process.name),
            None,
            root=ROOT,
            session=3,
            foreground=100,
        )


@pytest.mark.parametrize(
    "family,error", [(None, 15700), ("Unrelated.Application_publisher", 0)]
)
def test_arbitrary_wwahost_is_never_treated_as_privacy_ui(family, error):
    process = identity(
        "wwahost.exe", ROOT + r"\System32\WWAHost.exe", family, package_error=error
    )
    with pytest.raises(hosted.PreparationError) as failed:
        hosted.action_for(process, window(), root=ROOT, session=3, foreground=100)
    assert failed.value.code == (error or 13)


def test_start_escape_requires_exact_package_path_class_and_current_foreground():
    process = identity(
        "startmenuexperiencehost.exe",
        ROOT + "\\SystemApps\\" + hosted._START + r"\StartMenuExperienceHost.exe",
        hosted._START,
    )
    assert (
        hosted.action_for(process, window(), root=ROOT, session=3, foreground=100)
        == "escape"
    )
    for changed, foreground in ((window(title="Other"), 100), (window(), 999)):
        with pytest.raises(hosted.PreparationError):
            hosted.action_for(
                process, changed, root=ROOT, session=3, foreground=foreground
            )
    assert (
        hosted.action_for(
            process, window(cloaked=True), root=ROOT, session=3, foreground=100
        )
        is None
    )


def test_performance_options_never_closes_an_unrelated_system_dialog():
    process = identity(
        "systempropertiesperformance.exe",
        ROOT + r"\System32\SystemPropertiesPerformance.exe",
    )
    assert (
        hosted.action_for(
            process,
            window(title="Performance Options", **{"class": "#32770"}),
            root=ROOT,
            session=3,
            foreground=100,
        )
        == "close"
    )
    with pytest.raises(hosted.PreparationError):
        hosted.action_for(
            process,
            window(title="Other dialog", **{"class": "#32770"}),
            root=ROOT,
            session=3,
            foreground=100,
        )


def test_wsl_minimization_requires_exact_installed_terminal_family_and_native_wsl_title():
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    known = window(title=r"c:/WINDOWS/System32/wsl.exe")
    assert (
        hosted.action_for(process, known, root=ROOT, session=3, foreground=100)
        == "minimize"
    )
    for changed in (
        replace(process, family=None, package_error=15700),
        replace(process, family="Other"),
    ):
        with pytest.raises(hosted.PreparationError):
            hosted.action_for(changed, known, root=ROOT, session=3, foreground=100)
    assert (
        hosted.action_for(
            process, window(title="User shell"), root=ROOT, session=3, foreground=100
        )
        is None
    )
    assert (
        hosted.action_for(
            process, window(title="User shell"), root=ROOT, session=3, foreground=999
        )
        is None
    )
    assert (
        hosted.action_for(
            process, {**known, "minimized": True}, root=ROOT, session=3, foreground=999
        )
        is None
    )


class Registry:
    HKEY_LOCAL_MACHINE, KEY_READ, KEY_SET_VALUE, KEY_WOW64_64KEY, REG_DWORD = (
        1,
        2,
        4,
        8,
        4,
    )

    def __init__(self, value=None, *, exists=True):
        self.value, self.exists = value, exists
        self.writes, self.closed = [], 0
        self.OpenKey, self.CreateKeyEx = self.open_key, self.create_key
        self.QueryValueEx, self.SetValueEx = self.query, self.set_value
        self.DeleteValue, self.QueryInfoKey = self.delete_value, self.info
        self.DeleteKeyEx = self.delete_key

    @contextmanager
    def opened(self):
        try:
            yield self
        finally:
            self.closed += 1

    def open_key(self, hive, path, reserved, access):
        assert (hive, path, reserved, access) == (1, hosted._POLICY, 0, 14)
        if not self.exists:
            raise FileNotFoundError(2, "missing")
        return self.opened()

    def create_key(self, *args):
        self.exists = True
        return self.open_key(*args)

    def query(self, key, name):
        assert key is self and name == hosted._VALUE
        if self.value is None:
            raise FileNotFoundError(2, "missing")
        return self.value

    def set_value(self, key, name, reserved, kind, value):
        assert (key, name, reserved) == (self, hosted._VALUE, 0)
        self.value = value, kind
        self.writes.append(self.value)

    def delete_value(self, key, name):
        assert (key, name) == (self, hosted._VALUE)
        self.value = None

    def info(self, key):
        assert key is self
        return 0, int(self.value is not None), 0

    def delete_key(self, hive, path, access, reserved):
        assert (hive, path, access, reserved) == (1, hosted._POLICY, 8, 0)
        self.exists = False


@pytest.mark.parametrize("previous", [None, (0, 4), (1, 4), ("previous", 1)])
def test_policy_restores_exact_value_and_type_even_when_collection_raises(previous):
    registry, journal = Registry(previous), {}
    with (
        pytest.raises(ValueError, match="test failure"),
        hosted.privacy_policy(registry, journal),
    ):
        assert registry.value == (1, 4)
        raise ValueError("test failure")
    assert registry.value == previous and journal["restored"]
    assert registry.closed == 1
    if previous == (1, 4):
        assert not registry.writes


def test_policy_cleanup_removes_only_new_empty_key_after_failure():
    registry, journal = Registry(exists=False), {}
    with pytest.raises(ValueError), hosted.privacy_policy(registry, journal):
        raise ValueError("owned failure")
    assert not registry.exists and registry.value is None and journal["restored"]


def test_policy_external_change_is_left_untouched_and_fails_cleanup():
    registry, journal = Registry((0, 4)), {}
    with (
        pytest.raises(hosted.PreparationError, match="externally"),
        hosted.privacy_policy(registry, journal),
    ):
        registry.value = (2, 4)
    assert registry.value == (2, 4) and journal["restored"] is False


def test_recycled_pid_cannot_authorize_termination_on_a_different_process_handle():
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258),
        GetProcessId=Mock(return_value=999),
        TerminateProcess=Mock(),
    )
    process = identity("wwahost.exe", ROOT + r"\System32\WWAHost.exe", hosted._CLOUD)
    with pytest.raises(hosted.PreparationError, match="identity changed"):
        api.perform(process, "terminate", None)
    api.kernel.TerminateProcess.assert_not_called()


def test_sweep_failure_records_identity_but_never_mutates_unproven_wwahost():
    process = identity(
        "wwahost.exe", ROOT + r"\System32\WWAHost.exe", package_error=15700
    )

    @contextmanager
    def processes():
        yield [process]

    api = SimpleNamespace(
        exited=[],
        root=ROOT,
        session=3,
        processes=processes,
        windows=lambda _pid: [window()],
        foreground=lambda: 100,
        perform=Mock(),
    )
    journal = {"actions": []}
    with pytest.raises(hosted.PreparationError):
        hosted.sweep(api, journal)
    api.perform.assert_not_called()
    assert journal["observed"]["20:40"]["package_error"] == 15700


@pytest.mark.parametrize("status", (0, 1))
def test_wrapper_collects_both_children_via_collector_and_restores_lifetime(
    tmp_path, status
):
    @contextmanager
    def processes():
        yield []

    captures, journal = [], {}
    api = SimpleNamespace(
        exited=[], processes=processes, capture=lambda path: captures.append(path.name)
    )
    registry = Registry((0, 4))

    @contextmanager
    def owned_collector():
        try:
            yield SimpleNamespace(poll=lambda: status, returncode=status)
        finally:
            journal["collector_stopped"] = True

    collector = Mock(side_effect=owned_collector)
    assert hosted.run_prepared(api, registry, tmp_path, collector, journal) == status
    collector.assert_called_once_with()
    assert registry.value == (0, 4) and journal["policy"]["restored"]
    assert journal["collector_stopped"] and journal["ok"] is (status == 0)
    assert captures == [
        "desktop-preparation-before.png",
        "desktop-preparation-ready.png",
        "desktop-preparation-after.png",
    ]
    assert (tmp_path / "desktop-preparation.json").is_file()


def test_guard_rejection_does_not_construct_native_or_registry_helpers(monkeypatch):
    monkeypatch.setattr(
        hosted.sys,
        "argv",
        ["hosted_desktop.py", "--installed-root", "owned", "--output", "owned"],
    )
    monkeypatch.setattr(
        hosted,
        "NativeDesktop",
        Mock(side_effect=AssertionError("untrusted host inspected")),
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    with pytest.raises(hosted.PreparationError):
        hosted.main()
    hosted.NativeDesktop.assert_not_called()


def test_native_process_handle_is_released_after_identity_query_failure(monkeypatch):
    process = SimpleNamespace(pid=20, info={"name": "UserOOBEBroker.exe"})
    monkeypatch.setitem(
        hosted.sys.modules,
        "psutil",
        SimpleNamespace(process_iter=lambda _fields: [process]),
    )
    monkeypatch.setattr(c, "get_last_error", lambda: 5, raising=False)
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.session, api.session_of = 3, lambda _pid: 3
    api.kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=123),
        CloseHandle=Mock(),
        QueryFullProcessImageNameW=Mock(return_value=0),
    )
    with pytest.raises(hosted.PreparationError) as failed, api.processes():
        raise AssertionError("Identity query should fail first")
    assert failed.value.code == 5
    api.kernel.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("stage", ("other-session", "exited", "denied"))
def test_other_session_and_exited_candidates_never_receive_mutation_rights(
    monkeypatch, stage
):
    process = SimpleNamespace(pid=20, info={"name": "UserOOBEBroker.exe"})
    monkeypatch.setitem(
        hosted.sys.modules,
        "psutil",
        SimpleNamespace(process_iter=lambda _fields: [process]),
    )
    monkeypatch.setattr(
        c, "get_last_error", lambda: 87 if stage == "exited" else 5, raising=False
    )
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.session, api.exited = 3, []
    api.session_of = lambda _pid: 4 if stage == "other-session" else 3
    api.kernel = SimpleNamespace(OpenProcess=Mock(return_value=0), CloseHandle=Mock())
    if stage == "denied":
        with pytest.raises(hosted.PreparationError) as failed, api.processes():
            raise AssertionError("Access denied must fail before yielding")
        assert failed.value.code == 5
    else:
        with api.processes() as candidates:
            assert not candidates
    if stage == "other-session":
        api.kernel.OpenProcess.assert_not_called()
    elif stage == "exited":
        assert api.exited == [{"pid": 20, "name": "useroobebroker.exe", "code": 87}]
    api.kernel.CloseHandle.assert_not_called()


def test_pinned_process_natural_exit_is_benign_and_never_terminates_another_pid():
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=0), TerminateProcess=Mock()
    )
    process = identity("wwahost.exe", ROOT + r"\System32\WWAHost.exe", hosted._CLOUD)
    assert api.perform(process, "terminate", None) == "already-exited"
    api.kernel.TerminateProcess.assert_not_called()


@pytest.mark.parametrize("failure", (None, "assign", "body", "cancel"))
def test_owned_collector_is_contained_before_permission_and_killed_before_wait(
    tmp_path, monkeypatch, failure
):
    order, journal = [], {}
    pipe = SimpleNamespace(closed=False)
    pipe.write = lambda value: order.append(("permission", value))

    def close_pipe():
        pipe.closed = True
        order.append("pipe-close")

    pipe.close = close_pipe
    process = SimpleNamespace(_handle=123, stdin=pipe, poll=lambda: 0)
    process.wait = lambda **_kw: order.append("process-wait")
    job = SimpleNamespace(close=lambda: order.append("job-close"))

    def assign(handle):
        assert handle == 123
        order.append("job-assign")
        if failure == "assign":
            raise hosted.PreparationError("AssignProcessToJobObject", 5)

    job.assign = assign
    monkeypatch.setitem(
        sys.modules,
        "docking.platform.backends.windows.process_job",
        SimpleNamespace(ProcessJob=lambda: job),
    )
    spawn = Mock(return_value=process)
    monkeypatch.setattr(hosted.subprocess, "Popen", spawn)

    def exercise():
        with hosted.collector(tmp_path, tmp_path / "installed", journal):
            order.append("collecting")
            if failure == "body":
                raise ValueError("smoke failed")
            if failure == "cancel":
                raise KeyboardInterrupt

    if failure:
        error = {
            "assign": hosted.PreparationError,
            "body": ValueError,
            "cancel": KeyboardInterrupt,
        }[failure]
        with pytest.raises(error):
            exercise()
    else:
        exercise()
    command = spawn.call_args.args[0]
    assert "--collect" in command and spawn.call_args.kwargs["stdin"] == subprocess.PIPE
    assert order.index("job-close") < order.index("process-wait")
    if failure == "assign":
        assert not any(isinstance(entry, tuple) for entry in order)
    else:
        assert (
            order.index("job-assign")
            < order.index(("permission", b"1"))
            < order.index("collecting")
        )
    assert pipe.closed and journal["collector_stopped"]


@pytest.mark.parametrize(
    "error",
    (
        hosted.PreparationError("unproven desktop", 5),
        ValueError("unexpected internal error"),
    ),
)
def test_main_thread_sweep_failure_quiesces_collector_before_policy_restoration(
    tmp_path, error
):
    order, journal = [], {}
    registry = Registry((0, 4))
    original_set = registry.SetValueEx

    def set_value(*args):
        original_set(*args)
        if registry.value == (0, 4):
            order.append("policy-restored")

    registry.SetValueEx = set_value
    calls = 0

    @contextmanager
    def processes():
        nonlocal calls
        calls += 1
        if calls == 2:
            order.append("main-thread-sweep")
            raise error
        yield []

    @contextmanager
    def owned_collector():
        try:
            yield SimpleNamespace(poll=lambda: None)
        finally:
            order.append("collector-stopped")
            journal["collector_stopped"] = True

    api = SimpleNamespace(exited=[], processes=processes, capture=lambda _path: None)
    if isinstance(error, ValueError):
        with pytest.raises(ValueError):
            hosted.run_prepared(api, registry, tmp_path, owned_collector, journal)
    else:
        assert (
            hosted.run_prepared(api, registry, tmp_path, owned_collector, journal) == 1
        )
    assert order == ["main-thread-sweep", "collector-stopped", "policy-restored"]
    assert journal["ok"] is False and journal["collector_stopped"]


def test_verified_wsl_terminal_is_only_minimized_without_sending_input_or_activation():
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.root, api.session = ROOT, 3
    current = window(title=ROOT + r"\System32\wsl.exe")
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258), GetProcessId=Mock(return_value=20)
    )

    def minimize(hwnd, mode):
        assert (hwnd, mode) == (100, 7)
        current["minimized"] = True
        return 0  # ShowWindow returns previous visibility, not success.

    api.user = SimpleNamespace(ShowWindow=Mock(side_effect=minimize))
    api.windows, api.foreground = lambda _pid: [dict(current)], lambda: 100
    api.desktop = SimpleNamespace(
        key=Mock(), wait_for=lambda predicate, _message, _seconds: predicate()
    )
    assert api.perform(process, "minimize", current) == "dismissed"
    api.user.ShowWindow.assert_called_once_with(100, 7)
    api.desktop.key.assert_not_called()


def test_changed_hwnd_owner_cannot_be_minimized_even_if_its_old_title_was_wsl():
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.root, api.session = ROOT, 3
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258), GetProcessId=Mock(return_value=20)
    )
    api.user = SimpleNamespace(ShowWindow=Mock())
    api.windows, api.foreground = lambda _pid: [], lambda: 100
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    with pytest.raises(hosted.PreparationError, match="window identity changed"):
        api.perform(process, "minimize", window(title=ROOT + r"\System32\wsl.exe"))
    api.user.ShowWindow.assert_not_called()


def test_broken_permission_pipe_still_closes_owned_job_and_waits_before_return(
    tmp_path, monkeypatch
):
    order, journal = [], {}
    pipe = SimpleNamespace(
        closed=False,
        write=Mock(side_effect=BrokenPipeError(32, "owned pipe")),
        close=Mock(side_effect=BrokenPipeError(32, "owned pipe")),
    )
    process = SimpleNamespace(
        _handle=123,
        stdin=pipe,
        wait=lambda **_kwargs: order.append("wait"),
        poll=lambda: 0,
    )
    job = SimpleNamespace(
        assign=lambda _handle: None, close=lambda: order.append("job-close")
    )
    monkeypatch.setitem(
        sys.modules,
        "docking.platform.backends.windows.process_job",
        SimpleNamespace(ProcessJob=lambda: job),
    )
    monkeypatch.setattr(hosted.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(BrokenPipeError), hosted.collector(tmp_path, tmp_path, journal):
        raise AssertionError("No permission was delivered")
    assert order == ["job-close", "wait"] and journal["collector_stopped"]


@pytest.mark.parametrize("permission", (b"", b"0", b"1"))
def test_collector_branch_cannot_run_smokes_before_exact_parent_permission(
    tmp_path, monkeypatch, permission
):
    from tools.windows import ci

    monkeypatch.setattr(hosted.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(
        hosted.sys,
        "argv",
        [
            "hosted_desktop.py",
            "--collect",
            "--installed-root",
            str(tmp_path),
            "--output",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(
        hosted.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(permission))
    )
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace())
    monkeypatch.setattr(
        hosted, "NativeDesktop", lambda: SimpleNamespace(facts=lambda: FACTS)
    )
    collect = Mock(return_value=0)
    monkeypatch.setattr(ci, "input_domain", collect)
    if permission == b"1":
        assert hosted.main() == 0
        assert collect.call_args.kwargs == {"owned_amd64_editor": True}
    else:
        with pytest.raises(hosted.PreparationError, match="not authorized"):
            hosted.main()
        collect.assert_not_called()


@pytest.mark.parametrize("native_code", ("absent", None, 5))
def test_windows_crt_errors_keep_numeric_errno_without_losing_native_winerror(
    monkeypatch, native_code
):
    monkeypatch.setattr(hosted.sys, "platform", "win32")
    error = BrokenPipeError(32, "private pipe path")
    if native_code != "absent":
        error.winerror = native_code
    assert hosted._error(error) == {
        "operation": "BrokenPipeError",
        "code": 5 if native_code == 5 else 32,
    }
