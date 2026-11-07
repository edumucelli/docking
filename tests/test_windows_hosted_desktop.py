"""Unprepared runners fail; only owned acceptance processes are cleaned up."""

import ctypes as c
import io
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import hosted_desktop as hosted

ENV = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}
FACTS = {"product_type": 1, "build": 26200, "native_machine": 0xAA64, "session": 3}
READY = {"input_desktop": "Default", "shell_ready": True, "blockers": []}


@pytest.fixture
def psutil_api(monkeypatch):
    # psutil is a Windows/BSD runtime dependency, optional on Linux test hosts.
    class NoSuchProcessError(Exception):
        pass

    class AccessDeniedError(Exception):
        pass

    api = SimpleNamespace(
        process_iter=Mock(return_value=[]),
        NoSuchProcess=NoSuchProcessError,
        AccessDenied=AccessDeniedError,
    )
    monkeypatch.setitem(sys.modules, "psutil", api)
    return api


@pytest.mark.parametrize(
    "platform,environment,facts",
    [
        ("linux", ENV, FACTS),
        ("win32", {}, FACTS),
        ("win32", {**ENV, "RUNNER_ENVIRONMENT": "self-hosted"}, FACTS),
        ("win32", ENV, {**FACTS, "product_type": 3}),
        ("win32", ENV, {**FACTS, "build": 19045}),
        ("win32", ENV, {**FACTS, "native_machine": 0x8664}),
        ("win32", ENV, {**FACTS, "session": 0}),
    ],
)
def test_hosted_arm_interactive_client_guard(platform, environment, facts):
    with pytest.raises(hosted.PreparationError):
        hosted.require_hosted(platform, environment, facts)


def test_native_arm_client_accepts_emulated_python():
    hosted.require_hosted("win32", ENV, FACTS)


@pytest.mark.parametrize("status", [0, 7])
@pytest.mark.parametrize("blocked_at", [None, 0, 2])
def test_readiness_blocks_start_or_stops_only_the_owned_collector(
    tmp_path, status, blocked_at
):
    journal, events = {}, []
    child = SimpleNamespace(returncode=None)
    child.poll = lambda: child.returncode

    def wait(*, timeout):
        child.returncode = status
        return status

    child.wait = wait
    blocker = {"name": "setuphost.exe", "pid": 25, "class": "#32770"}
    observations = [0]

    def ready():
        observations[0] += 1
        if observations[0] == blocked_at:
            raise hosted.PreparationError(
                "Runner setup is incomplete", blockers=[blocker]
            )
        return dict(READY)

    def preflight():
        if blocked_at == 0:
            raise hosted.PreparationError(
                "Runner setup is incomplete", blockers=[blocker]
            )
        return {**READY, "session_ready": True, "wsl_ready": True}

    @contextmanager
    def collect():
        events.append("collector-started")
        journal.update(collector_started=True, collector_stopped=False)
        try:
            yield child
        finally:
            events.append("owned-collector-cleaned")
            child.returncode = child.returncode if child.returncode is not None else -1
            journal["collector_stopped"] = True

    # This API deliberately exposes no policy/window/process mutation methods.
    api = SimpleNamespace(
        preflight=preflight, check_ready=ready, capture=lambda _: None
    )
    result = hosted.run_prepared(api, tmp_path, collect, journal)
    if blocked_at is not None:
        assert result == 1 and not journal["ok"]
        assert journal["blockers"] == [blocker]
        assert events == (
            [] if blocked_at == 0 else ["collector-started", "owned-collector-cleaned"]
        )
    else:
        assert result == status and journal["ok"] is (status == 0)
        assert journal["input_status"] == status
        assert journal["readiness_checks"] >= 2
    assert journal["collector_stopped"]
    assert journal["mode"] == "verify-only"
    assert journal["policy_changed"] is False and journal["actions"] == []
    assert (tmp_path / "desktop-preparation.json").exists()


def test_preflight_timeout_is_recorded_and_never_starts_acceptance(tmp_path):
    def preflight():
        raise subprocess.TimeoutExpired("read-only prerequisite query", 30)

    collect = Mock()
    journal = {}
    api = SimpleNamespace(preflight=preflight, capture=lambda _: None)
    assert hosted.run_prepared(api, tmp_path, collect, journal) == 1
    collect.assert_not_called()
    assert journal["error"]["code"] == 1460 and not journal["ok"]


@pytest.mark.parametrize(
    "station,desktop,state",
    [(None, "Default", 0), ("WinSta0", None, 0), ("WinSta0", "Default", 4)],
)
def test_disconnected_or_unknown_session_never_queries_or_provisions_wsl(
    monkeypatch, station, desktop, state
):
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.session = 3
    monkeypatch.setattr(
        hosted,
        "runtime_snapshot",
        lambda: {
            "available": True,
            "desktop": {"session": 3, "window_station": station, "desktop": desktop},
            "session_context": {"wts_connection_state": state},
        },
    )
    run = Mock()
    monkeypatch.setattr(hosted.subprocess, "run", run)
    with pytest.raises(hosted.PreparationError, match="interactive"):
        api.preflight()
    run.assert_not_called()


@pytest.mark.parametrize("code", [0, 1])
def test_wsl_preflight_only_reads_version_and_requires_existing_provisioning(
    monkeypatch, code
):
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.session = 3
    api.check_ready = lambda: dict(READY)
    monkeypatch.setattr(
        hosted,
        "runtime_snapshot",
        lambda: {
            "available": True,
            "desktop": {
                "session": 3,
                "window_station": "WinSta0",
                "desktop": "Default",
            },
            "session_context": {"wts_connection_state": 0},
        },
    )
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    run = Mock(return_value=SimpleNamespace(returncode=code))
    monkeypatch.setattr(hosted.subprocess, "run", run)
    if code:
        with pytest.raises(hosted.PreparationError, match="provisioned"):
            api.preflight()
    else:
        assert api.preflight()["wsl_ready"]
    assert len(run.call_args.args[0]) == 2
    assert run.call_args.args[0][-1] == "--version"
    assert run.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("read_ok", [False, True])
def test_input_desktop_is_opened_read_only_and_closed_even_on_query_failure(
    monkeypatch, read_ok
):
    opened, closed = [], []

    def open_desktop(*args):
        opened.append(args)
        return 50

    def name(_handle, _kind, value, _size, _needed):
        if not read_ok:
            return 0
        text = c.create_unicode_buffer("Default")
        c.memmove(value, text, c.sizeof(text))
        return 1

    def close(handle):
        closed.append(handle)
        return 1

    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.user = SimpleNamespace(
        OpenInputDesktop=open_desktop,
        GetUserObjectInformationW=name,
        CloseDesktop=close,
    )
    monkeypatch.setattr(c, "get_last_error", lambda: 5, raising=False)
    if read_ok:
        assert api.input_desktop() == "Default"
    else:
        with pytest.raises(hosted.PreparationError):
            api.input_desktop()
    assert opened == [(0, False, 1)] and closed == [50]


@pytest.mark.parametrize(
    "name",
    [
        "useroobebroker.exe",
        "wwahost.exe",
        "startmenuexperiencehost.exe",
        "msiexec.exe",
        "wsl.exe",
    ],
)
def test_setup_windows_are_reported_without_dismissal_or_private_captions(
    psutil_api, name
):
    process = SimpleNamespace(pid=25, info={"name": name}, cmdline=lambda: ["wsl.exe"])
    psutil_api.process_iter.return_value = [process]
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.session = 3
    api.session_of = lambda _: 3
    api.desktop = SimpleNamespace(
        windows=lambda *_, **__: [
            {"hwnd": 10, "class": "#32770", "title": "Private caption"}
        ]
    )
    api.user = SimpleNamespace(IsIconic=lambda _: False)
    assert api.blockers() == [{"pid": 25, "name": name, "class": "#32770"}]


def test_background_wsl_update_is_reported_without_terminating_it(psutil_api):
    process = SimpleNamespace(
        pid=25, info={"name": "wsl.exe"}, cmdline=lambda: ["wsl.exe", "--update"]
    )
    psutil_api.process_iter.return_value = [process]
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.session = 3
    api.session_of = lambda _: 3
    assert api.blockers() == [
        {"pid": 25, "name": "wsl.exe", "reason": "setup-or-update-running"}
    ]


@pytest.mark.parametrize("platform", ("win32", "linux"))
@pytest.mark.parametrize("failure", (None, "assign", "body", "cancel"))
def test_owned_collector_is_contained_before_permission_and_killed_before_wait(
    tmp_path, monkeypatch, failure, platform
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
        "docking.platform.native.windows.process_job",
        SimpleNamespace(ProcessJob=lambda: job),
    )
    spawn = Mock(return_value=process)
    monkeypatch.setattr(hosted.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        hosted, "sys", SimpleNamespace(platform=platform, executable=sys.executable)
    )

    def exercise():
        with hosted.collector(
            tmp_path,
            tmp_path / "installed",
            journal,
            wheel=tmp_path / "candidate.whl",
            source_sha="a" * 40,
        ):
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
    assert spawn.call_args.kwargs["creationflags"] == (
        0x08000000 if platform == "win32" else 0
    )
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
    assert (
        journal["collector_spawn_started"]["monotonic_ns"]
        <= journal["collector_started_at"]["monotonic_ns"]
    )
    if failure != "assign":
        assert (
            journal["collector_started_at"]["monotonic_ns"]
            <= journal["collector_permitted_at"]["monotonic_ns"]
        )


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
        "docking.platform.native.windows.process_job",
        SimpleNamespace(ProcessJob=lambda: job),
    )
    monkeypatch.setattr(hosted.subprocess, "Popen", Mock(return_value=process))
    with (
        pytest.raises(BrokenPipeError),
        hosted.collector(
            tmp_path,
            tmp_path,
            journal,
            wheel=tmp_path / "candidate.whl",
            source_sha="a" * 40,
        ),
    ):
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
            "--wheel",
            str(tmp_path / "candidate.whl"),
            "--source-sha",
            "a" * 40,
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
        assert collect.call_args.kwargs == {
            "owned_amd64_editor": True,
            "source_sha": "a" * 40,
        }
    else:
        with pytest.raises(hosted.PreparationError, match="not authorized"):
            hosted.main()
        collect.assert_not_called()


@pytest.mark.parametrize("code", [5, 87])
def test_disappeared_setup_process_is_ignored_but_permission_failure_is_not(
    psutil_api, code
):
    process = SimpleNamespace(pid=25, info={"name": "setuphost.exe"})
    psutil_api.process_iter.return_value = [process]
    api = hosted.NativeDesktop.__new__(hosted.NativeDesktop)
    api.session = 3
    api.session_of = Mock(
        side_effect=hosted.PreparationError("ProcessIdToSessionId", code)
    )
    if code == 87:
        assert api.blockers() == []
    else:
        with pytest.raises(hosted.PreparationError):
            api.blockers()
