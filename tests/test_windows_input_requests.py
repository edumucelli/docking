"""Input controls preserve real failure, ownership and bounded request evidence."""

import ctypes
import struct
import threading
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.native.windows import input_thread as native
from docking.platform.native.windows.input_context import InputContext
from tools.windows.visual import input_requests as mod


def snapshot(layout=0x04090409):
    return native.InputSnapshot(None, layout, "", False, False, -1, 0, 0)


def failure(phase=111):
    value = native.Request()
    value.state, value.phase, value.result = 2, phase, -2147023673
    value.operation, value.machine, value.dispatched = 3, 0x8664, 1
    return native.InputRequestError(
        0x800704C7, "Rejected owned request", native.InputRequestDiagnostic.read(value)
    )


def test_cleanup_records_every_outcome_and_keeps_original_failure():
    original, second = ValueError("original"), OSError(5, "cleanup denied")
    errors, report, events = [original], {}, []
    cleanup = mod.CleanupJournal(report, errors, lambda: events.append("publish"))
    cleanup.attempt("first", Mock(side_effect=second))
    assert cleanup.attempt("second", lambda: events.append("done") or 42) == 42
    assert errors == [original, second]
    assert report["cleanup"] == [
        {"name": "first", "ok": False, "error": {"type": "OSError", "code": 5}},
        {"name": "second", "ok": True},
    ]
    assert events == ["publish", "done", "publish"]
    assert report["ok"] is False


def test_fixture_cleanup_retains_exercise_error_and_runs_later_release():
    original, cleanup = AssertionError("composition"), OSError(5, "reset")
    journal, released = {}, Mock()
    with (
        pytest.raises(AssertionError) as failed,
        mod.fixture_cleanup(released, journal, "close"),
        mod.fixture_cleanup(Mock(side_effect=cleanup), journal, "keyboard"),
    ):
        raise original
    assert failed.value is original
    released.assert_called_once_with()
    assert journal["exercise_error"] == {"type": "AssertionError"}
    assert journal["cleanup_errors"] == [
        {"phase": "keyboard", "type": "OSError", "code": 5}
    ]


def test_fixture_cleanup_failure_is_fatal_after_successful_exercise():
    error, journal = OSError(5, "reset"), {}
    with (
        pytest.raises(OSError) as failed,
        mod.fixture_cleanup(Mock(side_effect=error), journal, "keyboard"),
    ):
        pass
    assert failed.value is error
    assert journal["cleanup_errors"][0]["phase"] == "keyboard"


def test_independent_close_and_reset_failures_never_replace_the_exercise_error():
    original, journal = AssertionError("composition"), {}
    with (
        pytest.raises(AssertionError) as failed,
        mod.fixture_cleanup(Mock(side_effect=OSError(6, "close")), journal, "close"),
        mod.fixture_cleanup(Mock(side_effect=OSError(5, "reset")), journal, "reset"),
    ):
        raise original
    assert failed.value is original
    assert journal["cleanup_errors"] == [
        {"phase": "reset", "type": "OSError", "code": 5},
        {"phase": "close", "type": "OSError", "code": 6},
    ]


def test_fixture_cancellation_still_closes_owned_window_and_preserves_cancellation():
    original, close, journal = KeyboardInterrupt(), Mock(), {}
    with (
        pytest.raises(KeyboardInterrupt) as failed,
        mod.fixture_cleanup(close, journal, "close"),
        mod.fixture_cleanup(Mock(side_effect=OSError(5, "reset")), journal, "reset"),
    ):
        raise original
    assert failed.value is original
    close.assert_called_once_with()
    assert journal["exercise_error"] == {"type": "KeyboardInterrupt"}


@pytest.fixture
def reset_fixture():
    from docking.applets.keyboardlayout.state import LayoutState
    from docking.platform.native.windows.input_profiles import InputProfile

    context, foreign = InputContext(1, 2, 3, 4), InputContext(10, 20, 30, 40)
    wanted = "win32:04090409"
    profile = InputProfile(2, 0x409, "", "", "", 0, 0x04090409, 3)
    observed = native.InputSnapshot(profile, 0x04090409, "en-US", True, False, 0, 1, 0)
    selected = LayoutState(wanted, [wanted], context=context.key)
    state = [LayoutState("tsf:fixture", [wanted], context=context.key)]
    current, calls = [foreign], []

    def focus():
        calls.append("physical-focus")
        current[0] = context
        return context

    def query():
        calls.append("query")
        return state[0]

    def switch(code, *, context):
        calls.append(("switch", code, context))
        state[0] = selected

    api = SimpleNamespace(
        foreground=Mock(side_effect=lambda: current[0]),
        valid=Mock(return_value=True),
        input_desktop_active=Mock(return_value=True),
        active=Mock(return_value=0x04090409),
    )
    backend = SimpleNamespace(
        query=Mock(side_effect=query), switch=Mock(side_effect=switch)
    )
    service = SimpleNamespace(read=Mock(return_value=observed))
    ui = Mock(side_effect=lambda: state[0])

    def wait(predicate, message):
        for _ in range(3):
            if predicate():
                return
        raise AssertionError(message)

    kwargs = {
        "context": context,
        "focus": focus,
        "api": api,
        "backend": backend,
        "service": service,
        "wait_for": wait,
        "journal": {},
        "ui_readback": ui,
    }
    return SimpleNamespace(
        kwargs=kwargs,
        context=context,
        foreign=foreign,
        current=current,
        calls=calls,
        state=state,
        selected=selected,
        snapshot=observed,
    )


def test_reset_physically_refocuses_pinned_owned_context_before_switch(reset_fixture):
    fixture = reset_fixture
    result = mod.restore_owned_keyboard(**fixture.kwargs)
    assert fixture.calls[:3] == [
        "physical-focus",
        "query",
        ("switch", "win32:04090409", fixture.context.key),
    ]
    assert result == asdict(fixture.snapshot)
    fixture.kwargs["service"].read.assert_called_once()
    assert fixture.kwargs["service"].read.call_args.args[0] == fixture.context
    assert fixture.kwargs["journal"]["restored"]
    assert fixture.kwargs["journal"]["foreground_before"] == asdict(fixture.foreign)


def test_reset_waits_for_real_gtk_state_without_retargeting_or_repeating_switch(
    reset_fixture,
):
    fixture, kwargs = reset_fixture, reset_fixture.kwargs
    kwargs["ui_readback"].side_effect = [
        fixture.selected._replace(active="tsf:fixture"),
        fixture.selected,
    ]
    mod.restore_owned_keyboard(**kwargs)
    assert kwargs["service"].read.call_count == 2
    kwargs["backend"].switch.assert_called_once_with(
        "win32:04090409", context=fixture.context.key
    )
    assert kwargs["journal"]["restored"]


@pytest.mark.parametrize(
    "boundary", ["invalid", "desktop", "wrong-focus", "wrong-query"]
)
def test_reset_never_switches_an_unknown_or_changed_target(reset_fixture, boundary):
    fixture, kwargs = reset_fixture, reset_fixture.kwargs
    if boundary == "invalid":
        kwargs["api"].valid.return_value = False
    elif boundary == "desktop":
        kwargs["api"].input_desktop_active.return_value = False
    elif boundary == "wrong-focus":
        kwargs["focus"] = lambda: fixture.foreign
    else:
        fixture.state[0] = fixture.state[0]._replace(context=fixture.foreign.key)
    with pytest.raises(AssertionError):
        mod.restore_owned_keyboard(**kwargs)
    kwargs["backend"].switch.assert_not_called()
    kwargs["service"].read.assert_not_called()
    assert kwargs["journal"]["restored"] is False


@pytest.mark.parametrize("boundary", ["foreground", "invalid", "desktop"])
def test_reset_rechecks_identity_after_query_before_dispatch(reset_fixture, boundary):
    fixture, kwargs = reset_fixture, reset_fixture.kwargs
    original = kwargs["backend"].query.side_effect

    def changed():
        result = original()
        if boundary == "foreground":
            fixture.current[0] = fixture.foreign
        elif boundary == "invalid":
            kwargs["api"].valid.return_value = False
        else:
            kwargs["api"].input_desktop_active.return_value = False
        return result

    kwargs["backend"].query.side_effect = changed
    with pytest.raises(AssertionError):
        mod.restore_owned_keyboard(**kwargs)
    kwargs["backend"].switch.assert_not_called()


@pytest.mark.parametrize(
    "boundary", ["snapshot", "profile", "hkl", "backend", "ui", "context"]
)
def test_reset_requires_matching_native_backend_and_gtk_readback(
    reset_fixture, boundary
):
    fixture, kwargs = reset_fixture, reset_fixture.kwargs
    if boundary == "snapshot":
        kwargs["service"].read.return_value = None
    elif boundary == "profile":
        kwargs["service"].read.return_value = native.InputSnapshot(
            None, 0x04090409, "", True, False, 0, 1, 0
        )
    elif boundary == "hkl":
        kwargs["api"].active.return_value = 0x042A042A
    elif boundary == "backend":
        kwargs["backend"].switch.side_effect = None
    elif boundary == "ui":
        kwargs["ui_readback"].side_effect = lambda: fixture.selected._replace(
            active="tsf:fixture"
        )
    else:
        kwargs["ui_readback"].side_effect = lambda: fixture.selected._replace(
            context=fixture.foreign.key
        )
    with pytest.raises(AssertionError, match="did not restore"):
        mod.restore_owned_keyboard(**kwargs)
    assert kwargs["journal"]["restored"] is False


def test_reset_rejects_foreground_change_while_reading_native_snapshot(reset_fixture):
    fixture, kwargs = reset_fixture, reset_fixture.kwargs

    def read(*_args):
        fixture.current[0] = fixture.foreign
        return fixture.snapshot

    kwargs["service"].read.side_effect = read
    with pytest.raises(AssertionError, match="lost foreground"):
        mod.restore_owned_keyboard(**kwargs)
    assert not kwargs["journal"]["restored"]


def test_failed_cleanup_journal_write_does_not_stop_later_releases():
    original, write_error = AssertionError("original"), OSError(28, "full")
    errors, report = [original], {}
    cleanup = mod.CleanupJournal(report, errors, Mock(side_effect=write_error))
    cleanup.attempt("first", lambda: None)
    last = Mock()
    cleanup.attempt("last", last)
    last.assert_called_once()
    assert errors == [original, write_error, write_error]


@pytest.fixture
def request_boundary(monkeypatch):
    def request(client, context, cancelled, profile=None, *, layout=None):
        value = native.Request()
        value.operation = 3 if layout is not None else (2 if profile else 1)
        value.machine, value.requested.layout = 0x8664, layout or 0
        return client._dispatch(
            context,
            cancelled,
            profile,
            0x8664,
            64,
            None,
            "owned mapping",
            value,
            layout=layout,
        )

    def dispatch(
        client,
        context,
        cancelled,
        profile,
        machine,
        bits,
        path,
        name,
        value,
        *,
        layout=None,
    ):
        value.state, value.dispatched, value.authorized = 2, 1, 1
        value.phase, value.result = client.phase, client.result
        value.observed = layout or 0x04090409
        if client.result < 0:
            raise native.InputRequestError(
                client.result & 0xFFFFFFFF,
                "native error",
                native.InputRequestDiagnostic.read(value),
            )
        return None if client.phase == 0 else snapshot(value.observed)

    monkeypatch.setattr(native.InputThread, "request", request)
    monkeypatch.setattr(native.InputThread, "_dispatch", dispatch)
    client = native.InputThread.__new__(native.InputThread)
    client.phase, client.result = 32, 0
    return client, request, dispatch


def test_trace_retains_typed_native_failure_without_changing_dispatch(request_boundary):
    client, original, dispatch = request_boundary
    client.phase, client.result = 111, -2147023673
    journal, publish = {}, Mock()
    context = InputContext(1, 2, 3, 4)
    with (
        mod.trace_requests(journal, publish),
        pytest.raises(native.InputRequestError) as error,
    ):
        client.request(context, threading.Event(), layout=0x040C040C)
    assert native.InputThread.request is original
    assert native.InputThread._dispatch is dispatch
    assert error.value.diagnostic.phase == 111
    row = journal["requests"][0]
    assert row["outcome"] == "failed" and row["context"] == asdict(context)
    assert row["wire"]["hresult"] == 0x800704C7
    assert row["validation"] == "gui-thread-query-failed"
    assert row["wire"]["dispatched"] and row["helper_bits"] == 64
    assert row["requested"] == 0x040C040C and row["operation"] == 3
    assert publish.call_count == 1


@pytest.mark.parametrize("phase,outcome", [(0, "unavailable"), (32, "returned")])
def test_trace_does_not_turn_timeout_into_success(request_boundary, phase, outcome):
    client, _original, _dispatch = request_boundary
    client.phase = phase
    journal = {}
    with mod.trace_requests(journal, lambda: None):
        result = client.request(InputContext(1, 2, 3, 4), threading.Event())
    assert (result is not None) is (outcome == "returned")
    assert journal["requests"][0]["outcome"] == outcome
    assert journal["requests"][0]["wire"]["phase"] == phase


def test_trace_is_bounded_and_restores_methods_when_reporting_fails(request_boundary):
    client, original, _dispatch = request_boundary
    journal = {}
    with mod.trace_requests(journal, lambda: None):
        for _ in range(mod._TRACE_LIMIT + 2):
            client.request(InputContext(1, 2, 3, 4), threading.Event())
    assert len(journal["requests"]) == mod._TRACE_LIMIT and journal["dropped"] == 2
    with (
        pytest.raises(OSError, match="write failed"),
        mod.trace_requests({}, Mock(side_effect=OSError("write failed"))),
    ):
        client.request(InputContext(1, 2, 3, 4), threading.Event())
    assert native.InputThread.request is original
    client.phase, client.result = 111, -2147023673
    with (
        pytest.raises(native.InputRequestError),
        mod.trace_requests({}, Mock(side_effect=OSError("write failed"))),
    ):
        client.request(InputContext(1, 2, 3, 4), threading.Event())


def test_control_collection_reaches_remaining_boundaries_after_typed_failure():
    row, events = {}, []
    operations = (
        ("read", Mock(side_effect=failure())),
        ("classic-fr", lambda: snapshot(0x040C040C)),
        ("classic-us", snapshot),
    )
    mod.collect_controls(
        row=row,
        operations=operations,
        validate=lambda: events.append("validate"),
        close=lambda: events.append("close"),
    )
    assert [r["ok"] for r in row["operations"]] == [False, True, True]
    assert row["closed"] and not row["ok"]
    assert events == ["validate"] * 6 + ["close"]
    assert row["operations"][0]["wire"]["phase"] == 111


@pytest.mark.parametrize(
    "cause", [OSError("unknown failure"), AssertionError("identity")]
)
def test_unknown_control_failure_stops_collection_and_preserves_close_failure(cause):
    row, last = {}, Mock()
    with pytest.raises(type(cause)) as failed:
        mod.collect_controls(
            row=row,
            operations=(("first", Mock(side_effect=cause)), ("later", last)),
            validate=lambda: None,
            close=Mock(side_effect=OSError(5, "close failed")),
        )
    assert failed.value is cause
    assert row["close_error"] == {"type": "OSError", "code": 5}
    assert "ok" not in row
    last.assert_not_called()


@pytest.mark.parametrize("closed,restored", [(False, True), (True, False)])
def test_compatibility_release_requires_closed_owned_contexts(closed, restored):
    unload = Mock()
    with pytest.raises(AssertionError, match="must close"):
        mod.release_fixture_layouts(
            owned={0x042A042A},
            baseline={0x04090409},
            loaded=lambda: (0x042A042A,),
            unload=unload,
            targets_closed=closed,
            profiles_restored=restored,
            journal={},
        )
    unload.assert_not_called()


@pytest.mark.parametrize(
    "owned,baseline", [({0x040C040C}, set()), ({0x042A042A}, {0x042A042A})]
)
def test_compatibility_release_never_unloads_preexisting_or_unknown_layout(
    owned, baseline
):
    unload = Mock()
    with pytest.raises(AssertionError, match="unowned"):
        mod.release_fixture_layouts(
            owned=owned,
            baseline=baseline,
            loaded=lambda: (),
            unload=unload,
            targets_closed=True,
            profiles_restored=True,
            journal={},
        )
    unload.assert_not_called()


def test_compatibility_release_checks_sdk_result_and_preserves_exact_handle(
    monkeypatch,
):
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 170, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "in use"), raising=False
    )
    journal, unload = {}, Mock(return_value=0)
    handle = 0xFFFFFFFF042A042A
    with pytest.raises(OSError) as failed:
        mod.release_fixture_layouts(
            owned={0x042A042A},
            baseline={0x04090409},
            loaded=lambda: (handle, 0x04090409),
            unload=unload,
            targets_closed=True,
            profiles_restored=True,
            journal=journal,
        )
    assert failed.value.errno == 170
    unload.assert_called_once_with(handle)
    assert journal["fixture_layout_releases"][0]["unloaded"] is False
    unload.return_value = 1
    mod.release_fixture_layouts(
        owned={0x042A042A},
        baseline={0x04090409},
        loaded=lambda: (handle,),
        unload=unload,
        targets_closed=True,
        profiles_restored=True,
        journal=journal,
    )
    assert journal["fixture_layout_releases"][0]["unloaded"] is True


def test_compatibility_layout_already_removed_is_recorded_without_second_unload():
    journal, unload = {}, Mock()
    mod.release_fixture_layouts(
        owned={0x042A042A},
        baseline={0x04090409},
        loaded=lambda: (0x04090409,),
        unload=unload,
        targets_closed=True,
        profiles_restored=True,
        journal=journal,
    )
    unload.assert_not_called()
    assert journal["fixture_layout_releases"] == [
        {"layout": "042a042a", "already_absent": True}
    ]


@pytest.mark.parametrize("machine", [0x8664, 0x14C, 0xAA64])
def test_owned_editor_pe_machine_is_read_from_actual_file(tmp_path, machine):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    path = tmp_path / "editor.exe"
    path.write_bytes(data)
    assert mod.pe_machine(path) == machine
    path.write_bytes(b"MZ")
    with pytest.raises(ValueError):
        mod.pe_machine(path)


@pytest.fixture
def controls(monkeypatch):
    from types import SimpleNamespace

    from docking.applets.keyboardlayout.implementations.windows import (
        backend as windows,
    )
    from docking.platform.native.windows import input_service
    from docking.platform.native.windows.input_profiles import (
        KEYBOARD_CATEGORY,
        InputProfile,
    )
    from tools.windows.visual import input_editor, input_environment

    layouts, editors, calls = {}, [], []
    api = SimpleNamespace(
        active=lambda thread: layouts.get(thread, 0x04090409),
        layouts=lambda: (0x04090409, 0x040C040C),
        foreground=lambda: editors[-1].context,
        valid=lambda context: context == editors[-1].context,
    )
    zero = "00000000-0000-0000-0000-000000000000"
    records = tuple(
        InputProfile(2, layout & 0xFFFF, zero, zero, KEYBOARD_CATEGORY, 0, layout, 2)
        for layout in (0x04090409, 0x040C040C)
    )
    options = SimpleNamespace(read_error=None, focus_error=None, pe_mismatch=False)

    class Editor:
        def __init__(self, native, directory, document, *, bits, machine):
            self.bits, self.journal = bits, {"architecture": bits}
            self.process = SimpleNamespace(_handle=bits, pid=bits)
            self.context = InputContext(bits, bits + 1, bits + 2, bits)
            self.close = Mock(side_effect=lambda: self.journal.update(closed=True))
            editors.append(self)

        def start(self):
            return {"hwnd": self.context.root, "pid": self.context.pid}

        def verify_identity(self, window):
            assert window == {"hwnd": self.context.root, "pid": self.context.pid}
            return window

    def read(context, cancelled):
        calls.append((context.pid, "read"))
        if options.read_error and context.pid == 64 and calls.count((64, "read")) > 1:
            raise options.read_error
        return snapshot(api.active(context.thread))

    def classic(context, cancelled, *, layout):
        calls.append((context.pid, "classic", layout))
        layouts[context.thread] = layout
        return snapshot(layout)

    def activate(context, profile, cancelled):
        calls.append((context.pid, "tsf", profile.layout))
        layouts[context.thread] = profile.layout
        return snapshot(profile.layout)

    service = SimpleNamespace(
        catalog=lambda _loaded: SimpleNamespace(keyboards=records),
        read=read,
        activate=activate,
        _thread=SimpleNamespace(_machine=Mock(), request=classic),
    )
    monkeypatch.setattr(windows, "WindowsKeyboardAPI", lambda: api)
    monkeypatch.setattr(input_service, "WindowsInputService", lambda: service)
    monkeypatch.setattr(input_editor, "OwnedInputEditor", Editor)

    def focus(*_, **_options):
        if options.focus_error:
            raise options.focus_error

    monkeypatch.setattr(input_editor, "focus_editor", focus)
    monkeypatch.setattr(input_environment, "bind", lambda *_: lambda: 100)
    abi = Mock(side_effect=lambda kernel, handle, **_: {"machine": handle})
    monkeypatch.setattr(input_environment, "process_architecture", abi)
    monkeypatch.setattr(ctypes, "WinDLL", Mock(), raising=False)
    monkeypatch.setattr(
        mod,
        "pe_machine",
        lambda path: (
            0xAA64
            if options.pe_mismatch
            else (0x8664 if path.stem == "editor64" else 0x14C)
        ),
    )
    return options, editors, calls, abi


def test_both_control_architectures_run_all_six_boundaries(controls, tmp_path):
    _options, editors, calls, abi = controls
    journal = {}
    mod.request_controls(
        object(), tmp_path, french=0x040C040C, journal=journal, publish=lambda: None
    )
    assert journal["controls_passed"]
    assert [r["architecture"] for r in journal["controls"]] == [64, 32]
    assert len(calls) == 14
    assert all(row["readiness"]["ready"] for row in journal["controls"])
    for editor, row in zip(editors, journal["controls"], strict=True):
        assert row["closed"] and row["editor"]["closed"]
        assert row["process_abi"]["machine"] == editor.process._handle
        assert row["pe_machine"] == ("0x8664" if editor.bits == 64 else "0x014c")
        assert [r["name"] for r in row["operations"]] == [
            "initial-read",
            "classic-fr",
            "classic-us",
            "tsf-keyboard-fr",
            "tsf-keyboard-us",
            "final-read",
        ]
    assert [call.args[1] for call in abi.call_args_list] == [100, 64, 32]


def test_failed_x64_requests_do_not_hide_x86_control_boundary(controls, tmp_path):
    options, editors, _calls, _abi = controls
    options.read_error = failure()
    journal = {}
    mod.request_controls(
        object(), tmp_path, french=0x040C040C, journal=journal, publish=lambda: None
    )
    assert not journal["controls_passed"]
    assert not journal["controls"][0]["ok"] and journal["controls"][1]["ok"]
    assert all(editor.journal["closed"] for editor in editors)


@pytest.mark.parametrize("cause", ["focus", "machine"])
def test_control_setup_identity_failure_closes_owned_editor_and_stops(
    controls, tmp_path, cause
):
    options, editors, calls, _abi = controls
    if cause == "focus":
        options.focus_error = AssertionError("lost focus")
    else:
        options.pe_mismatch = True
    with pytest.raises(AssertionError):
        mod.request_controls(
            object(), tmp_path, french=0x040C040C, journal={}, publish=lambda: None
        )
    assert len(editors) == 1 and editors[0].journal["closed"]
    assert calls == []


def test_concurrent_requests_publish_only_completed_rows_on_driver(
    request_boundary, monkeypatch
):
    from copy import deepcopy

    client, _original, dispatch = request_boundary
    barrier, failures = threading.Barrier(2), []

    def delayed(*args, **kwargs):
        barrier.wait(timeout=3)
        return dispatch(*args, **kwargs)

    monkeypatch.setattr(native.InputThread, "_dispatch", delayed)
    journal, publish = {}, Mock()

    def run(layout):
        try:
            client.request(
                InputContext(layout, layout, 3, 4), threading.Event(), layout=layout
            )
        except BaseException as error:
            failures.append(error)

    with mod.trace_requests(journal, publish):
        workers = [
            threading.Thread(target=run, args=(layout,))
            for layout in (0x04090409, 0x040C040C)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        assert not failures and not any(worker.is_alive() for worker in workers)
        publish.assert_not_called()
        completed = deepcopy(journal)
        assert all(
            row["outcome"] == "returned" and "wire" in row
            for row in journal["requests"]
        )
    publish.assert_called_once()
    assert journal == completed
    assert {row["wire"]["observed_layout"] for row in journal["requests"]} == {
        0x04090409,
        0x040C040C,
    }


def test_cleanup_retains_independent_oracle_api_and_raw_win32_error():
    from tools.windows.visual.input_oracle import OracleError

    report, errors = {}, []
    cleanup = mod.CleanupJournal(report, errors, lambda: None)
    cleanup.attempt("oracle", Mock(side_effect=OracleError("GetGUIThreadInfo", 87)))
    winerror = OSError(22, "translated errno")
    winerror.winerror = 87
    cleanup.attempt("native", Mock(side_effect=winerror))
    assert report["cleanup"][0]["error"] == {
        "type": "OracleError",
        "api": "GetGUIThreadInfo",
        "code": 87,
    }
    assert report["cleanup"][1]["error"]["code"] == 87


def test_cleanup_plain_windows_oserror_uses_errno_when_winerror_is_none():
    report, errors = {}, []
    error = OSError(5, "plain Windows OS error")
    error.winerror = None
    cleanup = mod.CleanupJournal(report, errors, lambda: None)
    cleanup.attempt("native", Mock(side_effect=error))
    assert errors == [error]
    assert report["cleanup"][0]["error"] == {"type": "OSError", "code": 5}


def _runtime_fixture(root, marker):
    directory = root / "docking/platform/native/windows"
    directory.mkdir(parents=True, exist_ok=True)
    (root / "docking/__init__.py").write_text(f"marker={marker!r}\n")
    for bits, machine in ((64, 0x8664), (32, 0x14C)):
        data = bytearray(128)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, machine)
        (directory / f"input-thread-{bits}.dll").write_bytes(data)


def _select_runtime_subprocess(source, installed, *, preloaded=False):
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = r"""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tools.windows.visual.input_requests import select_input_runtime,record_input_origins
assert 'docking' not in sys.modules
source,installed=Path(sys.argv[2]),Path(sys.argv[3]) if sys.argv[3] else None
if sys.argv[4]=='1':
 sys.path.insert(0,str(source))
 import docking
journal={}
try:
 root=select_input_runtime(installed,journal,source=source)
 import docking
 record_input_origins(root,journal,('docking',))
 print(json.dumps({'runtime':journal,'marker':docking.marker}))
except (AssertionError,ValueError) as error:
 print(json.dumps({'runtime':journal,'error':str(error)}))
 sys.exit(1)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            script,
            str(Path(mod.__file__).resolve().parents[3]),
            str(source),
            str(installed) if installed is not None else "",
            str(int(preloaded)),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode, json.loads(result.stdout)


def test_installed_input_selection_precedes_all_application_imports(tmp_path):
    source, installed = tmp_path / "source", tmp_path / "installed"
    _runtime_fixture(source, "source")
    _runtime_fixture(installed, "installed")
    status, result = _select_runtime_subprocess(source, installed)
    assert status == 0 and result["marker"] == "installed"
    runtime = result["runtime"]
    assert runtime["implementation"] == "installed-wheel"
    assert runtime["preloaded_application_modules"] == []
    assert runtime["module_origins"] == {"docking": "docking/__init__.py"}
    assert [r["machine"] for r in runtime["helpers"]] == ["0x8664", "0x014c"]
    assert all(r["present"] and len(r["sha256"]) == 64 for r in runtime["helpers"])
    assert str(tmp_path) not in str(runtime)


def test_local_source_acceptance_requires_both_built_helpers(tmp_path):
    source = tmp_path / "source"
    _runtime_fixture(source, "source")
    (source / "docking/platform/native/windows/input-thread-32.dll").unlink()
    status, result = _select_runtime_subprocess(source, None)
    assert status == 1 and "Build both native input helpers" in result["error"]
    assert result["runtime"]["helpers"][1]["present"] is False
    _runtime_fixture(source, "source")
    status, result = _select_runtime_subprocess(source, None)
    assert status == 0 and result["marker"] == "source"


def test_runtime_selection_refuses_preloaded_source_and_installed_source_alias(
    tmp_path,
):
    source, installed = tmp_path / "source", tmp_path / "installed"
    _runtime_fixture(source, "source")
    _runtime_fixture(installed, "installed")
    status, result = _select_runtime_subprocess(source, installed, preloaded=True)
    assert status == 1 and "before importing Docking" in result["error"]
    status, result = _select_runtime_subprocess(source, source)
    assert status == 1 and "outside the source checkout" in result["error"]


def test_runtime_preflight_rejects_wrong_helper_machine_before_import(tmp_path):
    source, installed = tmp_path / "source", tmp_path / "installed"
    _runtime_fixture(source, "source")
    _runtime_fixture(installed, "installed")
    path = installed / "docking/platform/native/windows/input-thread-64.dll"
    content = bytearray(path.read_bytes())
    struct.pack_into("<H", content, 68, 0xAA64)
    path.write_bytes(content)
    status, result = _select_runtime_subprocess(source, installed)
    assert status == 1 and "wrong PE architecture" in result["error"]
    assert result["runtime"]["preloaded_application_modules"] == []


def test_input_tool_imports_do_not_preload_source_application():
    import subprocess
    import sys
    from pathlib import Path

    script = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "import tools.windows.visual.keyboardlayout_smoke;"
        "import tools.windows.visual.input_profile_smoke;"
        "assert not any(n=='docking' or n.startswith('docking.') for n in sys.modules)"
    )
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(Path(mod.__file__).resolve().parents[3]),
        ],
        check=True,
        timeout=15,
    )


def test_cold_readiness_unavailable_does_not_replace_measured_initial_read():
    from dataclasses import dataclass

    @dataclass
    class State:
        layout: int = 0x04090409

    reads = iter([None, State(), State()])
    events = []
    journal = {}
    mod.input_service_ready(
        read=lambda: next(reads),
        validate=lambda: events.append("validate"),
        journal=journal,
        publish=lambda: None,
    )
    assert journal["ready"]
    assert [row["available"] for row in journal["attempts"]] == [False, True]
    measured = {}
    mod.collect_controls(
        row=measured,
        operations=(("initial-read", lambda: next(reads)),),
        validate=lambda: None,
        close=lambda: None,
    )
    assert measured["operations"] == [
        {"name": "initial-read", "ok": True, "snapshot": {"layout": 0x04090409}}
    ]
    assert events == ["validate"] * 4


@pytest.mark.parametrize(
    "failure", ["unavailable", "deadline", "identity", "api", "cancelled"]
)
def test_readiness_cannot_accept_timeout_target_loss_api_error_or_cancel(failure):
    from dataclasses import dataclass

    @dataclass
    class State:
        layout: int = 0x04090409

    cancelled = threading.Event()
    journal = {}
    validations = 0

    def validate():
        nonlocal validations
        validations += 1
        if failure == "identity" and validations == 2:
            raise RuntimeError("Owned target changed")

    def read():
        if failure == "api":
            raise OSError(5, "Native API failed")
        if failure == "cancelled":
            cancelled.set()
        return None if failure == "unavailable" else State()

    kwargs = {"seconds": 0} if failure == "deadline" else {}
    expected = (
        OSError if failure in ("api", "unavailable", "deadline") else RuntimeError
    )
    with pytest.raises(expected):
        mod.input_service_ready(
            read=read,
            validate=validate,
            journal=journal,
            publish=lambda: None,
            cancelled=cancelled,
            **kwargs,
        )
    assert not journal["ready"]
    assert len(journal["attempts"]) <= 3
