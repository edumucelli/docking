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


@pytest.fixture
def fast_minimize_clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(hosted.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        hosted.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    return now


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


@pytest.mark.parametrize("caption", ("Start", "Démarrer", "", "开始"))
def test_start_escape_requires_exact_package_path_class_and_current_foreground(caption):
    process = identity(
        "startmenuexperiencehost.exe",
        ROOT + "\\SystemApps\\" + hosted._START + r"\StartMenuExperienceHost.exe",
        hosted._START,
    )
    assert (
        hosted.action_for(
            process, window(title=caption), root=ROOT, session=3, foreground=100
        )
        == "escape"
    )
    with pytest.raises(hosted.PreparationError):
        hosted.action_for(
            process,
            window(**{"class": "Unrelated"}),
            root=ROOT,
            session=3,
            foreground=100,
        )
    assert (
        hosted.action_for(
            replace(process, family=None),
            window(title="Unknown", **{"class": "Unrelated"}),
            root=ROOT,
            session=3,
            foreground=999,
        )
        is None
    )
    assert (
        hosted.action_for(
            process, window(cloaked=True), root=ROOT, session=3, foreground=100
        )
        is None
    )


@pytest.mark.parametrize(
    "caption",
    ("Performance Options", "System Properties", "Options de performances", ""),
)
def test_exact_performance_utility_dialog_is_identified_without_caption(caption):
    process = identity(
        "systempropertiesperformance.exe",
        ROOT + r"\System32\SystemPropertiesPerformance.exe",
    )
    assert (
        hosted.action_for(
            process,
            window(title=caption, **{"class": "#32770"}),
            root=ROOT,
            session=3,
            foreground=100,
        )
        == "close"
    )
    with pytest.raises(hosted.PreparationError):
        hosted.action_for(
            process,
            window(title=caption, **{"class": "Unrelated"}),
            root=ROOT,
            session=3,
            foreground=100,
        )


def test_performance_dialog_class_never_authorizes_another_executable_or_path():
    performance = identity(
        "systempropertiesperformance.exe",
        ROOT + r"\System32\SystemPropertiesPerformance.exe",
    )
    dialog = window(title="Performance Options", **{"class": "#32770"})
    with pytest.raises(hosted.PreparationError, match="path"):
        hosted.action_for(
            replace(performance, path=r"C:\owned\SystemPropertiesPerformance.exe"),
            dialog,
            root=ROOT,
            session=3,
            foreground=100,
        )
    unrelated = identity("unrelated.exe", ROOT + r"\System32\unrelated.exe")
    assert (
        hosted.action_for(unrelated, dialog, root=ROOT, session=3, foreground=100)
        is None
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


def test_first_classification_failure_retains_all_known_identity_and_window_evidence():
    candidates = [
        identity(
            "systempropertiesperformance.exe",
            ROOT + r"\System32\SystemPropertiesPerformance.exe",
        ),
        identity("wwahost.exe", ROOT + r"\System32\WWAHost.exe", hosted._CLOUD, pid=21),
        identity(
            "startmenuexperiencehost.exe",
            ROOT + "\\SystemApps\\" + hosted._START + r"\StartMenuExperienceHost.exe",
            hosted._START,
            pid=22,
        ),
        identity(
            "windowsterminal.exe",
            r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
            hosted._TERMINAL,
            pid=23,
        ),
    ]
    queried = []

    @contextmanager
    def processes():
        yield candidates

    def windows(pid):
        queried.append(pid)
        return [
            window(
                hwnd=pid,
                **{"class": "Unproven" if pid == 20 else "Windows.UI.Core.CoreWindow"},
            )
        ]

    api = SimpleNamespace(
        exited=[],
        root=ROOT,
        session=3,
        processes=processes,
        windows=windows,
        foreground=lambda: 20,
        perform=Mock(),
    )
    journal = {"actions": []}
    with pytest.raises(hosted.PreparationError, match="Performance Options"):
        hosted.sweep(api, journal)
    assert queried == [20, 21, 22, 23]
    assert set(journal["observed"]) == {f"{pid}:40" for pid in queried}
    assert journal["observed"]["21:40"]["family"] == hosted._CLOUD
    assert journal["observed"]["22:40"]["family"] == hosted._START
    assert journal["observed"]["23:40"]["family"] == hosted._TERMINAL
    assert journal["observed"]["20:40"]["windows"][0]["class"] == "Unproven"
    api.perform.assert_not_called()


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
        PermissionError(13, "owned native query"),
        AssertionError("unexpected readback assertion"),
        KeyboardInterrupt(),
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
        if calls == 3:
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
        assert hosted.run_prepared(
            api, registry, tmp_path, owned_collector, journal
        ) == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert order == ["main-thread-sweep", "collector-stopped", "policy-restored"]
    assert journal["ok"] is False and journal["collector_stopped"]
    if isinstance(error, KeyboardInterrupt):
        assert journal["cancelled"]


@pytest.mark.parametrize("foreground", (False, True))
def test_verified_wsl_terminal_uses_os_z_order_only_when_foreground(foreground):
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.root, api.session = ROOT, 3
    current = window(title=ROOT + r"\System32\wsl.exe")
    active = [100 if foreground else 999]
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258), GetProcessId=Mock(return_value=20)
    )

    def minimize(hwnd, mode):
        assert (hwnd, mode) == (100, 6 if foreground else 7)
        current["minimized"] = True
        if mode == 6:
            active[0] = 999  # Windows chooses the next top-level in Z order.
        return 0

    def wait_for(predicate, message, seconds):
        assert seconds == 2
        assert predicate(), message

    api.user = SimpleNamespace(ShowWindow=Mock(side_effect=minimize))
    api.windows, api.foreground = lambda _pid: [dict(current)], lambda: active[0]
    api.desktop = SimpleNamespace(key=Mock(), wait_for=wait_for)
    record = {}
    assert api.perform(process, "minimize", current, record=record) == "dismissed"
    api.user.ShowWindow.assert_called_once_with(100, 6 if foreground else 7)
    assert record == {
        "show_command": 6 if foreground else 7,
        "show_commands": [6 if foreground else 7],
        "foreground_before": 100 if foreground else 999,
        "foreground_after": 999,
        "window_state": "minimized",
    }
    api.desktop.key.assert_not_called()


@pytest.fixture
def iconic_wsl(fast_minimize_clock):
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.root, api.session = ROOT, 3
    state = SimpleNamespace(
        active=100, rows=[window(title=ROOT + r"\System32\wsl.exe", minimized=True)]
    )
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258), GetProcessId=Mock(return_value=20)
    )
    api.windows, api.foreground = (
        lambda _pid: [dict(row) for row in state.rows],
        lambda: state.active,
    )

    def wait_for(predicate, message, _seconds):
        assert predicate(), message

    api.desktop = SimpleNamespace(wait_for=wait_for)

    def owner(_hwnd, value):
        value._obj.value = 20
        return 21

    api.user = SimpleNamespace(
        ShowWindow=Mock(), GetWindowThreadProcessId=Mock(side_effect=owner)
    )
    api.package = Mock(return_value=(hosted._TERMINAL, 0))
    return api, process, state


@pytest.mark.parametrize("after_restore", (100, 999))
def test_iconic_foreground_wsl_restores_without_activation_then_uses_fresh_foreground(
    iconic_wsl, after_restore
):
    api, process, state = iconic_wsl

    def show(_hwnd, command):
        if command == 4:
            state.rows[0]["minimized"] = False
            state.active = after_restore
        else:
            assert command == (6 if after_restore == 100 else 7)
            state.rows[0]["minimized"] = True
            state.active = 999
        return 1

    api.user.ShowWindow.side_effect = show
    record = {}
    assert api.perform(process, "minimize", state.rows[0], record=record) == "dismissed"
    assert record["show_commands"] == [4, 6 if after_restore == 100 else 7]
    assert record["restore"] == {
        "foreground_before": 100,
        "foreground_after": after_restore,
        "window_state": "visible",
    }
    assert record["foreground_after"] == 999 and record["window_state"] == "minimized"


def test_iconic_wsl_failed_restore_never_attempts_an_unproven_minimize(iconic_wsl):
    api, process, state = iconic_wsl
    record = {}
    with pytest.raises(AssertionError, match="did not restore"):
        api.perform(process, "minimize", state.rows[0], record=record)
    api.user.ShowWindow.assert_called_once_with(100, 4)
    assert (
        record["show_commands"] == [4]
        and record["restore"]["window_state"] == "minimized"
    )


def test_iconic_wsl_replaced_target_after_restore_gets_no_second_command(iconic_wsl):
    api, process, state = iconic_wsl

    def replaced(_hwnd, command):
        assert command == 4
        state.rows[0].update(title="An unrelated terminal", minimized=False)
        return 1

    api.user.ShowWindow.side_effect = replaced
    with pytest.raises(hosted.PreparationError, match="identity changed"):
        api.perform(process, "minimize", state.rows[0], record={})
    api.user.ShowWindow.assert_called_once_with(100, 4)


def test_iconic_wsl_natural_close_during_restore_needs_no_minimize(iconic_wsl):
    api, process, state = iconic_wsl
    captured = state.rows[0]

    def closed(_hwnd, command):
        assert command == 4
        state.rows.clear()
        state.active = 999
        return 1

    api.user.ShowWindow.side_effect = closed
    record = {}
    assert api.perform(process, "minimize", captured, record=record) == "dismissed"
    assert record["show_commands"] == [4] and record["window_state"] == "disappeared"
    api.user.ShowWindow.assert_called_once_with(100, 4)


def test_iconic_wsl_recovery_cannot_report_success_if_final_minimize_retains_foreground(
    iconic_wsl,
):
    api, process, state = iconic_wsl

    def show(_hwnd, command):
        state.rows[0]["minimized"] = command != 4
        return 1

    api.user.ShowWindow.side_effect = show
    record = {}
    with pytest.raises(hosted.WslDismissalPendingError):
        api.perform(process, "minimize", state.rows[0], record=record)
    assert record["show_commands"] == [4, 6] and record["foreground_after"] == 100


@pytest.mark.parametrize("relinquished", (True, False))
def test_wsl_closing_during_minimize_is_dismissed_only_after_foreground_release(
    relinquished,
    fast_minimize_clock,
):
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.root, api.session = ROOT, 3
    rows = [window(title=ROOT + r"\System32\wsl.exe")]
    active = [100]
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258), GetProcessId=Mock(return_value=20)
    )
    api.windows, api.foreground = lambda _pid: list(rows), lambda: active[0]

    def minimize(_hwnd, command):
        assert command == 6
        rows.clear()
        active[0] = 999 if relinquished else 100
        return 1

    def wait_for(predicate, message, _seconds):
        assert predicate(), message

    api.user = SimpleNamespace(ShowWindow=Mock(side_effect=minimize))
    api.desktop = SimpleNamespace(wait_for=wait_for)
    record = {}
    if relinquished:
        assert api.perform(process, "minimize", rows[0], record=record) == "dismissed"
    else:
        with pytest.raises(hosted.PreparationError, match="retained foreground"):
            api.perform(process, "minimize", rows[0], record=record)
    assert record["window_state"] == "disappeared"
    assert record["foreground_after"] == (999 if relinquished else 100)


def test_wsl_minimize_uses_final_foreground_read_instead_of_earlier_snapshot():
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
    api.windows = lambda _pid: [dict(current)]
    api.foreground = Mock(side_effect=[100, 999, 999])

    def minimize(_hwnd, command):
        assert command == 7
        current["minimized"] = True
        return 1

    def wait_for(predicate, message, _seconds):
        assert predicate(), message

    api.user = SimpleNamespace(ShowWindow=Mock(side_effect=minimize))
    api.desktop = SimpleNamespace(wait_for=wait_for)
    record = {}
    api.perform(process, "minimize", current, record=record)
    api.user.ShowWindow.assert_called_once_with(100, 7)
    assert record["foreground_before"] == 999


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


def test_startup_readiness_requires_two_quiet_nonblocking_sweeps_100ms_apart(
    monkeypatch,
):
    now, calls, pauses, journal = [0.0], [], [], {}
    outcomes = iter(
        ((0, False), (1, True), (0, False), (0, True), (0, False), (0, False))
    )

    def sweep(_api, report):
        actions, blocking = next(outcomes)
        calls.append(now[0])
        report["known_visible_blockers"] = ["known"] if blocking else []
        return actions

    def pause(seconds):
        assert seconds == 0.1
        pauses.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(hosted, "sweep", sweep)
    hosted.settle_desktop(None, journal, clock=lambda: now[0], pause=pause)
    assert len(calls) == 6 and len(pauses) == 5 and journal["quiet_sweeps"] == 2


def test_startup_readiness_has_a_deadline_even_without_mutation_permission(monkeypatch):
    now, journal = [0.0], {}

    def blocked(_api, report):
        report["known_visible_blockers"] = ["verified but not foreground Start"]
        return 0

    def pause(seconds):
        assert seconds == 0.1
        now[0] += seconds

    monkeypatch.setattr(hosted, "sweep", blocked)
    with pytest.raises(hosted.PreparationError) as failed:
        hosted.settle_desktop(None, journal, clock=lambda: now[0], pause=pause)
    assert failed.value.code == 1460 and 10 <= now[0] < 10.2
    assert journal["quiet_sweeps"] == 0


def test_verified_visible_nonforeground_start_is_observed_as_blocking_but_untouched():
    process = identity(
        "startmenuexperiencehost.exe",
        ROOT + "\\SystemApps\\" + hosted._START + r"\StartMenuExperienceHost.exe",
        hosted._START,
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
        foreground=lambda: 999,
        perform=Mock(),
    )
    journal = {"actions": []}
    assert hosted.sweep(api, journal) == 0
    api.perform.assert_not_called()
    assert journal["known_visible_blockers"] == [
        {"pid": 20, "hwnd": 100, "name": process.name}
    ]


def test_collector_poll_interval_is_100ms_and_cleanup_still_precedes_policy_restore(
    tmp_path,
):
    journal, waits = {}, []

    @contextmanager
    def processes():
        yield []

    process = SimpleNamespace(returncode=0, poll=lambda: 0 if waits else None)
    process.wait = lambda *, timeout: waits.append(timeout)

    @contextmanager
    def collect():
        try:
            yield process
        finally:
            journal["collector_stopped"] = True

    api = SimpleNamespace(exited=[], processes=processes, capture=lambda _path: None)
    registry = Registry((0, 4))
    assert hosted.run_prepared(api, registry, tmp_path, collect, journal) == 0
    assert waits == [0.1] and journal["quiet_sweeps"] == 2
    assert registry.value == (0, 4) and journal["policy"]["restored"]


@pytest.fixture
def pending_wsl(fast_minimize_clock):
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.root, api.session = ROOT, 3
    state = SimpleNamespace(
        row=window(title=ROOT + r"\System32\wsl.exe"), owner=20, active=100
    )
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258), GetProcessId=Mock(return_value=20)
    )

    def owner(_hwnd, value):
        value._obj.value = state.owner
        return 21

    api.user = SimpleNamespace(
        ShowWindow=Mock(return_value=1),
        GetWindowThreadProcessId=Mock(side_effect=owner),
    )
    api.package = Mock(return_value=(hosted._TERMINAL, 0))
    api.windows = Mock(side_effect=lambda _pid: [dict(state.row)])
    api.foreground = lambda: state.active
    api.desktop = SimpleNamespace(wait_for=Mock())
    return api, process, state, fast_minimize_clock


def test_wsl_pending_deadline_requires_fresh_process_window_and_package_proof(
    pending_wsl,
):
    api, process, state, now = pending_wsl
    record = {}
    with pytest.raises(hosted.WslDismissalPendingError) as failed:
        api.perform(process, "minimize", dict(state.row), record=record)
    assert failed.value.code == 1460 and 2 <= now[0] < 2.2
    assert api.kernel.WaitForSingleObject.call_count == 2
    assert api.kernel.GetProcessId.call_count == 2
    api.package.assert_called_once_with(process.handle)
    api.user.GetWindowThreadProcessId.assert_called_once()
    api.user.ShowWindow.assert_called_once_with(100, 6)
    assert record["pending_identity_verified"] and record["pending_window"] == 100
    assert record["window_state"] == "visible" and record["foreground_after"] == 100


@pytest.mark.parametrize(
    "changed", ("process", "handle", "owner", "title", "class", "hwnd", "package")
)
def test_pending_wsl_revalidation_failure_cannot_be_deferred(pending_wsl, changed):
    api, process, state, _now = pending_wsl

    def changed_after_command(_hwnd, _command):
        if changed == "process":
            api.kernel.WaitForSingleObject.return_value = 0
        elif changed == "handle":
            api.kernel.GetProcessId.return_value = 303
        elif changed == "owner":
            state.owner = 303
        elif changed == "package":
            api.package.return_value = ("Unrelated.Application", 5)
        else:
            state.row[changed] = 999 if changed == "hwnd" else "unrelated"
        return 1

    api.user.ShowWindow.side_effect = changed_after_command
    record = {}
    with pytest.raises(hosted.PreparationError) as failed:
        api.perform(process, "minimize", dict(state.row), record=record)
    assert not isinstance(failed.value, hosted.WslDismissalPendingError)
    assert "pending_identity_verified" not in record
    api.user.ShowWindow.assert_called_once_with(100, 6)


@pytest.mark.parametrize(
    "error",
    (
        PermissionError(13, "owned API"),
        AssertionError("readback invariant"),
        KeyboardInterrupt(),
    ),
)
def test_readback_native_assertion_and_cancellation_errors_are_not_pending(
    pending_wsl, error
):
    api, process, state, _now = pending_wsl
    captured = dict(state.row)

    def changed_after_command(_hwnd, _command):
        api.windows.side_effect = error

    api.user.ShowWindow.side_effect = changed_after_command
    with pytest.raises(type(error)) as failed:
        api.perform(process, "minimize", captured, record={})
    assert failed.value is error


def test_pending_package_access_failure_remains_fatal(pending_wsl):
    api, process, state, _now = pending_wsl
    api.package.side_effect = PermissionError(13, "owned package")
    with pytest.raises(PermissionError):
        api.perform(process, "minimize", dict(state.row), record={})


def test_pending_window_owner_query_error_preserves_native_code(
    pending_wsl, monkeypatch
):
    api, process, state, _now = pending_wsl
    api.user.GetWindowThreadProcessId.side_effect = None
    api.user.GetWindowThreadProcessId.return_value = 0
    monkeypatch.setattr(hosted.c, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(hosted.PreparationError) as failed:
        api.perform(process, "minimize", dict(state.row), record={})
    assert not isinstance(failed.value, hosted.WslDismissalPendingError)
    assert failed.value.code == 5
    assert failed.value.operation == "Read pending WSL window owner"


def test_pending_owner_binding_preserves_the_shared_desktop_dword_buffer(pending_wsl):
    api, process, state, _now = pending_wsl
    callback = c.CFUNCTYPE(c.c_uint32, c.c_void_p, c.POINTER(c.c_uint32))

    @callback
    def owner(_hwnd, output):
        output.contents.value = 20
        return 21

    api.user.GetWindowThreadProcessId = owner
    with pytest.raises(hosted.WslDismissalPendingError):
        api.perform(process, "minimize", dict(state.row), record={})
    # The next WindowsDesktop.windows() sweep uses the cached function with
    # ctypes.wintypes.DWORD, which is nominally distinct from c_uint32.
    buffer = hosted.wintypes.DWORD()
    assert owner(100, c.byref(buffer)) == 21 and buffer.value == 20


@pytest.mark.parametrize("status", (0, 1))
@pytest.mark.parametrize("eventually_dismissed", (False, True))
def test_live_pending_wsl_preserves_child_completion_and_policy_cleanup(
    tmp_path, status, eventually_dismissed
):
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )
    journal, order, waits, attempts = {}, [], [], []
    polls = [0]

    @contextmanager
    def processes():
        polls[0] += 1
        yield [] if polls[0] <= 2 else [process]

    def perform(_identity, action, _window, *, record):
        assert action == "minimize"
        attempts.append(action)
        if eventually_dismissed and len(attempts) == 2:
            record["window_state"] = "minimized"
            return "dismissed"
        record.update(
            window_state="visible", pending_window=100, pending_identity_verified=True
        )
        raise hosted.WslDismissalPendingError()

    child = SimpleNamespace(returncode=None, poll=lambda: child.returncode, kill=Mock())

    def wait(*, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("owned collector", timeout)
        child.returncode = status
        order.append("strict-children-completed")
        return status

    child.wait = wait

    @contextmanager
    def collect():
        journal["collector_started"], journal["collector_stopped"] = True, False
        try:
            yield child
        finally:
            assert child.returncode == status
            order.append("collector-stopped")
            journal["collector_stopped"] = True

    registry = Registry((0, 4))
    original_set = registry.SetValueEx

    def set_value(*args):
        original_set(*args)
        if registry.value == (0, 4):
            order.append("policy-restored")

    registry.SetValueEx = set_value
    api = SimpleNamespace(
        root=ROOT,
        session=3,
        exited=[],
        processes=processes,
        windows=lambda _pid: [window(title=ROOT + r"\System32\wsl.exe")],
        foreground=lambda: 100,
        perform=perform,
        capture=lambda _path: None,
    )
    assert hosted.run_prepared(api, registry, tmp_path, collect, journal) == status
    assert journal["input_status"] == status and journal["ok"] is (status == 0)
    assert waits == [0.1, 0.1] and len(attempts) == 2
    assert order == [
        "strict-children-completed",
        "collector-stopped",
        "policy-restored",
    ]
    assert journal["actions"][0]["after"] == "pending"
    assert journal["actions"][0]["verification"]["code"] == 1460
    assert journal["actions"][1]["after"] == (
        "dismissed" if eventually_dismissed else "pending"
    )
    assert journal["known_visible_blockers"]
    child.kill.assert_not_called()
    assert journal["collector_stopped"] and journal["policy"]["restored"]


def test_initial_pending_wsl_is_fatal_before_collector_launch(tmp_path):
    process = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
    )

    @contextmanager
    def processes():
        yield [process]

    api = SimpleNamespace(
        root=ROOT,
        session=3,
        exited=[],
        processes=processes,
        windows=lambda _pid: [window(title=ROOT + r"\System32\wsl.exe")],
        foreground=lambda: 100,
        perform=Mock(side_effect=hosted.WslDismissalPendingError()),
        capture=lambda _path: None,
    )
    collect, journal, registry = Mock(), {}, Registry((0, 4))
    assert hosted.run_prepared(api, registry, tmp_path, collect, journal) == 1
    collect.assert_not_called()
    assert "prepared" not in journal and journal["error"]["code"] == 1460
    assert "after" not in journal["actions"][0]
    assert journal["policy"]["restored"]


def test_pending_exception_for_another_action_is_not_deferred():
    process = identity(
        "systempropertiesperformance.exe",
        ROOT + r"\System32\SystemPropertiesPerformance.exe",
    )

    @contextmanager
    def processes():
        yield [process]

    api = SimpleNamespace(
        root=ROOT,
        session=3,
        exited=[],
        processes=processes,
        windows=lambda _pid: [window(**{"class": "#32770"})],
        foreground=lambda: 100,
        perform=Mock(side_effect=hosted.WslDismissalPendingError()),
    )
    journal = {"actions": []}
    with pytest.raises(hosted.WslDismissalPendingError):
        hosted.sweep(api, journal, allow_pending_wsl=True)
    assert "after" not in journal["actions"][0]


@pytest.fixture
def updater_api(monkeypatch):
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.root, api.session, api.exited = ROOT, 3, []
    original = SimpleNamespace(
        pid=20,
        path=ROOT + r"\System32\wsl.exe",
        created=0x1234567800000040,
        live=True,
    )
    state = SimpleNamespace(
        handles={10: original, 11: original},
        session=3,
        wait_timeout=False,
        on_open=None,
        close_failed=False,
    )
    api.session_of = Mock(side_effect=lambda _pid: state.session)
    api.package = Mock(return_value=(None, 0))
    api.windows = Mock(return_value=[])
    api.foreground = Mock(return_value=999)

    def image(handle, _flags, path, size):
        path.value = state.handles[handle].path
        size._obj.value = len(path.value)
        return 1

    def times(handle, created, *_others):
        value = state.handles[handle].created
        created._obj.low, created._obj.high = value & 0xFFFFFFFF, value >> 32
        return 1

    def wait(handle, timeout):
        if timeout == 2000 and state.wait_timeout:
            return 258
        return 258 if state.handles[handle].live else 0

    def opened(access, inherit, pid):
        assert not inherit and pid == 20
        assert access in (0x101000, 0x101001)
        if access == 0x101001 and state.on_open:
            state.on_open()
        return 11 if access == 0x101001 else 10

    def terminate(handle, exitcode):
        assert handle == 11 and exitcode == 0
        state.handles[handle].live = False
        return 1

    api.kernel = SimpleNamespace(
        QueryFullProcessImageNameW=Mock(side_effect=image),
        GetProcessTimes=Mock(side_effect=times),
        WaitForSingleObject=Mock(side_effect=wait),
        GetProcessId=Mock(side_effect=lambda handle: state.handles[handle].pid),
        OpenProcess=Mock(side_effect=opened),
        CloseHandle=Mock(
            side_effect=lambda handle: int(not (handle == 11 and state.close_failed))
        ),
        TerminateProcess=Mock(side_effect=terminate),
    )
    api._wsl_arguments = Mock(return_value=["wsl.exe", *hosted._WSL_UPDATE_ARGS])
    process = SimpleNamespace(pid=20, info={"name": "wsl.exe"})
    fake_psutil = SimpleNamespace(
        process_iter=lambda _attrs: [process] if original.live else []
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(hosted.c, "get_last_error", lambda: 5, raising=False)
    return api, state, original


@pytest.mark.parametrize("program", ["wsl.exe", ROOT + r"\System32\wsl.exe"])
def test_only_exact_known_wsl_update_arguments_are_authorized(program):
    assert hosted.matches_wsl_update(
        ROOT, ROOT + r"\System32\wsl.exe", [program, *hosted._WSL_UPDATE_ARGS]
    )


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["wsl.exe"],
        ["wsl.exe", "--update"],
        ["wsl.exe", "--install", "--confirm", "--prompt-before-exit"],
        ["wsl.exe", "--confirm", "--update", "--prompt-before-exit"],
        ["wsl.exe", *hosted._WSL_UPDATE_ARGS, "--web-download"],
        ["wsl.exe", "--update", "--update", "--prompt-before-exit"],
        [r"C:\unrelated\wsl.exe", *hosted._WSL_UPDATE_ARGS],
    ],
)
def test_unknown_wsl_arguments_get_no_termination_handle(updater_api, arguments):
    api, _state, _original = updater_api
    api._wsl_arguments.return_value = arguments
    with api.processes() as rows:
        assert not rows[0].wsl_update
        assert (
            hosted.action_for(rows[0], None, root=ROOT, session=3, foreground=0) is None
        )
    assert [call.args[0] for call in api.kernel.OpenProcess.call_args_list] == [
        0x101000
    ]
    api.kernel.TerminateProcess.assert_not_called()
    api.kernel.CloseHandle.assert_called_once_with(10)


@pytest.mark.parametrize("mismatch", ("image", "session"))
def test_wrong_wsl_image_or_session_is_never_queried_for_arguments(
    updater_api, mismatch
):
    api, state, original = updater_api
    if mismatch == "image":
        original.path = r"C:\unrelated\wsl.exe"
    else:
        state.session = 4
    with api.processes() as rows:
        assert not rows or not rows[0].wsl_update
    api._wsl_arguments.assert_not_called()
    api.kernel.TerminateProcess.assert_not_called()


def test_exact_updater_uses_verified_termination_handle_and_redacted_journal(
    updater_api,
):
    api, _state, _original = updater_api
    with api.processes() as rows:
        (process,) = rows
        assert process.wsl_update and process.created == 0x1234567800000040
        record = process.journal()
        assert (
            api.perform(process, "stop-wsl-update", None, record=record) == "dismissed"
        )
        assert record["updater_stopped"]
        assert api._wsl_arguments.call_count == 3  # Discovery and both pinned handles.
        assert all(argument not in str(record) for argument in hosted._WSL_UPDATE_ARGS)
        api.kernel.CloseHandle.assert_called_once_with(11)
    assert [call.args[0] for call in api.kernel.CloseHandle.call_args_list] == [11, 10]
    api.kernel.TerminateProcess.assert_called_once_with(11, 0)


@pytest.mark.parametrize("phase", ("before-rights", "after-rights"))
def test_updater_argument_change_prevents_termination_and_closes_handles(
    updater_api, phase
):
    api, _state, _original = updater_api
    known = ["wsl.exe", *hosted._WSL_UPDATE_ARGS]
    private = ["wsl.exe", "private-user-command"]
    api._wsl_arguments.side_effect = (
        [known, private] if phase == "before-rights" else [known, known, private]
    )
    with api.processes() as rows:
        with pytest.raises(hosted.PreparationError, match="arguments changed"):
            api.perform(rows[0], "stop-wsl-update", None, record={})
        assert "private-user-command" not in str(rows[0].journal())
    api.kernel.TerminateProcess.assert_not_called()
    assert [call.args[0] for call in api.kernel.CloseHandle.call_args_list] == (
        [10] if phase == "before-rights" else [11, 10]
    )


@pytest.mark.parametrize("changed", ("pid", "created", "path", "session"))
def test_second_handle_must_be_the_original_process_instance(updater_api, changed):
    api, state, original = updater_api

    def replace_process():
        replacement = SimpleNamespace(**vars(original))
        state.handles[11] = replacement
        if changed == "session":
            state.session = 4
        else:
            setattr(
                replacement,
                changed,
                {
                    "pid": 303,
                    "created": original.created + 1,
                    "path": r"C:\unrelated\wsl.exe",
                }[changed],
            )

    state.on_open = replace_process
    with (
        api.processes() as rows,
        pytest.raises(hosted.PreparationError, match="identity changed"),
    ):
        api.perform(rows[0], "stop-wsl-update", None, record={})
    api.kernel.TerminateProcess.assert_not_called()
    assert [call.args[0] for call in api.kernel.CloseHandle.call_args_list] == [11, 10]


@pytest.mark.parametrize("phase", ("query", "open", "terminate"))
def test_updater_exit_races_never_terminate_a_reused_pid(updater_api, phase):
    api, state, original = updater_api
    with api.processes() as rows:
        if phase == "query":

            def arguments(_pid):
                original.live = False
                return ["wsl.exe", *hosted._WSL_UPDATE_ARGS]

            api._wsl_arguments.side_effect = arguments
        elif phase == "open":
            state.on_open = lambda: setattr(original, "live", False)
        else:

            def exited(_handle, _code):
                original.live = False
                return 0

            api.kernel.TerminateProcess.side_effect = exited
        assert (
            api.perform(rows[0], "stop-wsl-update", None, record={}) == "already-exited"
        )
    if phase != "terminate":
        api.kernel.TerminateProcess.assert_not_called()
    assert [call.args[0] for call in api.kernel.CloseHandle.call_args_list] == (
        [10] if phase == "query" else [11, 10]
    )


@pytest.mark.parametrize("failure", ("open", "terminate", "wait", "close", "query"))
def test_updater_native_failures_remain_fatal_and_close_every_opened_handle(
    updater_api, failure
):
    api, state, _original = updater_api
    with api.processes() as rows:
        if failure == "open":
            api.kernel.OpenProcess.side_effect = lambda *_args: 0
        elif failure == "terminate":
            api.kernel.TerminateProcess.side_effect = lambda *_args: 0
        elif failure == "wait":
            state.wait_timeout = True
        elif failure == "close":
            state.close_failed = True
        else:
            api._wsl_arguments.side_effect = hosted.PreparationError(
                "Read updater arguments", 5
            )
        with pytest.raises(hosted.PreparationError):
            api.perform(rows[0], "stop-wsl-update", None, record={})
    assert [call.args[0] for call in api.kernel.CloseHandle.call_args_list] == (
        [10] if failure in ("open", "query") else [11, 10]
    )


@pytest.mark.parametrize("arguments", ("raw private command", ["wsl.exe", None]))
def test_invalid_commandline_api_data_is_not_logged(monkeypatch, arguments):
    fake = SimpleNamespace(
        Process=lambda _pid: SimpleNamespace(cmdline=lambda: arguments),
        NoSuchProcess=ProcessLookupError,
        AccessDenied=PermissionError,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    with pytest.raises(hosted.PreparationError) as failed:
        api._wsl_arguments(20)
    assert "private" not in str(failed.value)


@pytest.mark.parametrize(
    "error,code", [(ProcessLookupError(), 87), (PermissionError(), 5)]
)
def test_commandline_query_errors_keep_only_safe_numeric_context(
    monkeypatch, error, code
):
    def command():
        raise error

    fake = SimpleNamespace(
        Process=lambda _pid: SimpleNamespace(cmdline=command),
        NoSuchProcess=ProcessLookupError,
        AccessDenied=PermissionError,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    with pytest.raises(hosted.PreparationError) as failed:
        api._wsl_arguments(20)
    assert failed.value.code == code


def test_initial_gate_stops_windowless_updater_then_requires_two_quiet_sweeps(
    updater_api,
):
    api, _state, original = updater_api
    journal = {"actions": []}
    hosted.settle_desktop(api, journal, pause=lambda _seconds: None)
    assert not original.live and journal["quiet_sweeps"] == 2
    assert [row["action"] for row in journal["actions"]] == ["stop-wsl-update"]


@pytest.mark.parametrize("status", (0, 1))
def test_recurring_exact_updater_stops_during_collection_without_changing_child_status(
    updater_api, tmp_path, status
):
    api, _state, original = updater_api
    original.live = False
    api.capture = Mock()
    child = SimpleNamespace(returncode=None, poll=lambda: child.returncode)
    child.wait = lambda *, timeout: setattr(child, "returncode", status)
    journal = {}

    @contextmanager
    def collect():
        assert journal["prepared"]
        original.live = True
        journal.update(collector_started=True, collector_stopped=False)
        try:
            yield child
        finally:
            assert child.returncode == status
            journal["collector_stopped"] = True

    assert (
        hosted.run_prepared(api, Registry((0, 4)), tmp_path, collect, journal) == status
    )
    assert not original.live and journal["input_status"] == status
    assert journal["policy"]["restored"] and journal["collector_stopped"]
    assert journal["actions"][0]["action"] == "stop-wsl-update"
    assert journal["actions"][0]["after"] == "dismissed"
    api.kernel.TerminateProcess.assert_called_once_with(11, 0)


@pytest.mark.parametrize("code", (5, 13, 87))
def test_updater_discovery_query_failure_closes_the_read_only_handle(updater_api, code):
    api, _state, _original = updater_api
    api._wsl_arguments.side_effect = hosted.PreparationError(
        "Read updater arguments", code
    )
    with pytest.raises(hosted.PreparationError), api.processes():
        pytest.fail("Unverified discovery must not authorize an action")
    api.kernel.CloseHandle.assert_called_once_with(10)
    api.kernel.TerminateProcess.assert_not_called()


def test_updater_exit_during_failed_command_query_is_a_normal_no_action(updater_api):
    api, _state, original = updater_api
    with api.processes() as rows:

        def gone(_pid):
            original.live = False
            raise hosted.PreparationError("WSL updater command line unavailable", 87)

        api._wsl_arguments.side_effect = gone
        assert api.perform(rows[0], "stop-wsl-update", None) == "already-exited"
    api.kernel.TerminateProcess.assert_not_called()
    api.kernel.CloseHandle.assert_called_once_with(10)


def test_updater_stops_before_terminal_fallback_and_refreshes_closed_windows():
    terminal = identity(
        "windowsterminal.exe",
        r"C:\Program Files\WindowsApps\owned\WindowsTerminal.exe",
        hosted._TERMINAL,
        pid=30,
    )
    updater = identity("wsl.exe", ROOT + r"\System32\wsl.exe", wsl_update=True)
    remaining, calls = [[window(title=ROOT + r"\System32\wsl.exe")]], []

    @contextmanager
    def processes():
        yield [terminal, updater]

    def perform(process, action, _window, *, record):
        assert process is updater and action == "stop-wsl-update"
        calls.append(action)
        remaining[0] = []
        return "dismissed"

    api = SimpleNamespace(
        root=ROOT,
        session=3,
        exited=[],
        processes=processes,
        windows=lambda pid: list(remaining[0]) if pid == 30 else [],
        foreground=lambda: 100,
        perform=perform,
    )
    journal = {"actions": []}
    assert hosted.sweep(api, journal, allow_pending_wsl=True) == 1
    assert calls == ["stop-wsl-update"] and not journal["known_visible_blockers"]


@pytest.fixture
def window_reader():
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.desktop = SimpleNamespace(windows=Mock(side_effect=lambda _pid: [window()]))
    api.user = SimpleNamespace(
        IsWindow=Mock(return_value=1), IsIconic=Mock(return_value=0)
    )
    api.dwm = SimpleNamespace(DwmGetWindowAttribute=Mock(return_value=0))
    return api


def test_destroyed_enumerated_window_is_skipped_before_dwm_query(window_reader):
    api = window_reader
    api.user.IsWindow.return_value = 0
    assert api.windows(20) == []
    api.dwm.DwmGetWindowAttribute.assert_not_called()
    api.user.IsIconic.assert_not_called()


@pytest.mark.parametrize(
    "status,alive",
    [(0x80070006, False), (0x80070006, True), (0x80070005, False), (0x80070057, False)],
)
def test_only_dwm_invalid_handle_for_a_proven_destroyed_window_is_benign(
    window_reader, status, alive
):
    api = window_reader
    api.user.IsWindow.side_effect = [1, int(alive)]
    api.dwm.DwmGetWindowAttribute.return_value = c.c_int32(status).value
    if status == 0x80070006 and not alive:
        assert api.windows(20) == []
    else:
        with pytest.raises(hosted.PreparationError) as failed:
            api.windows(20)
        assert (
            failed.value.operation == "DwmGetWindowAttribute"
            and failed.value.code == status
        )
    api.user.IsIconic.assert_not_called()


def test_window_destroyed_during_iconic_read_does_not_publish_a_ghost_row(
    window_reader,
):
    api = window_reader
    api.user.IsWindow.side_effect = [1, 0]
    assert api.windows(20) == []
    api.user.IsIconic.assert_called_once_with(100)


def test_successful_live_dwm_and_iconic_reads_keep_actual_attributes(window_reader):
    api = window_reader

    def attribute(_hwnd, kind, output, size):
        assert kind == 14 and size == 4
        output._obj.value = 1
        return 0

    api.dwm.DwmGetWindowAttribute.side_effect = attribute
    api.user.IsIconic.return_value = 1
    assert api.windows(20) == [window(cloaked=True, minimized=True)]


def test_performance_wm_close_accepts_window_destroyed_during_dwm_readback(
    window_reader,
):
    api = window_reader
    api.root, api.session, api.foreground = ROOT, 3, lambda: 999
    api.kernel = SimpleNamespace(
        WaitForSingleObject=Mock(return_value=258), GetProcessId=Mock(return_value=20)
    )
    state = SimpleNamespace(closing=False, alive=True)
    api.desktop.windows.side_effect = lambda _pid: [window(**{"class": "#32770"})]
    api.user.IsWindow.side_effect = lambda _hwnd: int(state.alive)

    def close(_hwnd, _message, _first, _second):
        state.closing = True
        return 1

    def attribute(_hwnd, _kind, _output, _size):
        if state.closing:
            state.alive = False
            return c.c_int32(0x80070006).value
        return 0

    def wait_for(predicate, message, seconds):
        assert seconds == 2
        assert predicate(), message

    api.user.PostMessageW = Mock(side_effect=close)
    api.dwm.DwmGetWindowAttribute.side_effect = attribute
    api.desktop.wait_for = wait_for
    process = identity(
        "systempropertiesperformance.exe",
        ROOT + r"\System32\SystemPropertiesPerformance.exe",
    )
    assert api.perform(process, "close", window(**{"class": "#32770"})) == "dismissed"
    api.user.PostMessageW.assert_called_once_with(100, 0x10, 0, 0)
    assert state.closing and not state.alive
