"""Ownership, privacy and read-only contracts of input acceptance diagnostics."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import threading
import uuid
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import input_environment as environment


class _Environment:
    def __init__(self):
        self.facts = {
            "session": 3,
            "thread": 7,
            "desktop": "Default",
            "window_station": "WinSta0",
        }
        self.inventory = {"complete": True, "items": []}
        self.initialize = Mock(return_value=0)
        self.uninitialize = Mock(return_value=0)

    def desktop(self):
        return dict(self.facts)

    def processes(self):
        return self.inventory

    def session_context(self, session):
        return {"session": session, "wts_connection_state": 0}


@pytest.fixture
def native(monkeypatch):
    native = _Environment()
    monkeypatch.setattr(environment, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(environment, "_Environment", lambda: native)
    assert not environment._OWNED
    yield native
    assert not environment._OWNED


def test_fixture_pairs_successful_monitor_even_when_composition_fails(native):
    with (
        pytest.raises(ValueError, match="composition failed"),
        environment.text_services() as journal,
    ):
        assert journal["initialized"]
        assert not journal["cleanup_completed"]
        raise ValueError("composition failed")
    native.initialize.assert_called_once_with()
    native.uninitialize.assert_called_once_with()
    assert journal["uninitialize_hresult"] == "0x00000000"
    assert journal["cleanup_completed"]
    json.dumps(journal)


@pytest.mark.parametrize(
    "reason", ["existing-desktop-monitor", "monitor-desktop-unknown"]
)
def test_existing_or_ambiguous_monitor_is_never_owned_or_stopped(native, reason):
    native.inventory["items"] = [
        {
            "name": "ctfmon.exe",
            "session": 3,
            "machine": "x64",
            "desktops": ["Default"] if reason == "existing-desktop-monitor" else [],
            "desktop_scan_complete": reason == "existing-desktop-monitor",
        }
    ]
    with environment.text_services() as journal:
        assert journal["reason"] == reason
    native.initialize.assert_not_called()
    native.uninitialize.assert_not_called()
    assert not journal["initialized"] and journal["cleanup_completed"]


@pytest.mark.parametrize(
    "other",
    [
        {"session": 4, "machine": "x64", "desktops": ["Default"]},
        {"session": 3, "machine": "x86", "desktops": ["Default"]},
        {"session": 3, "machine": "x64", "desktops": ["Other"]},
    ],
)
def test_another_session_architecture_or_desktop_does_not_claim_our_monitor(
    native, other
):
    native.inventory["items"] = [
        {"name": "ctfmon.exe", "desktop_scan_complete": True, **other}
    ]
    with environment.text_services() as journal:
        assert journal["initialized"]
    native.uninitialize.assert_called_once_with()


def test_nested_fixture_borrows_outer_monitor_without_double_uninitialization(native):
    with environment.text_services() as outer:
        with environment.text_services() as inner:
            assert inner["reason"] == "existing-fixture-monitor"
            assert not inner["initialized"]
        native.uninitialize.assert_not_called()
        assert outer["initialized"]
    native.initialize.assert_called_once_with()
    native.uninitialize.assert_called_once_with()


@pytest.mark.parametrize(
    "failure",
    [-2147467259, OSError(5, "private/fixture/path"), AttributeError("missing export")],
)
def test_failed_initialization_keeps_diagnostics_available_without_uninitializing(
    native, failure
):
    if isinstance(failure, Exception):
        native.initialize.side_effect = failure
    else:
        native.initialize.return_value = failure
    with environment.text_services() as journal:
        assert not journal["initialized"]
        assert "initialize_hresult" in journal or "initialize_error" in journal
    native.uninitialize.assert_not_called()
    assert journal["cleanup_completed"]
    assert "private/fixture/path" not in json.dumps(journal)


def test_cleanup_failure_is_reported_and_propagates(native):
    native.uninitialize.return_value = -2147467259
    with (
        pytest.raises(OSError, match="0x80004005"),
        environment.text_services() as journal,
    ):
        pass
    assert journal["uninitialize_hresult"] == "0x80004005"
    assert not journal["cleanup_completed"]


def test_desktop_change_is_detected_before_uninitializing_the_wrong_desktop(native):
    with (
        pytest.raises(RuntimeError, match="before switching desktop"),
        environment.text_services() as journal,
    ):
        native.facts["desktop"] = "Other"
    native.uninitialize.assert_not_called()
    assert journal["cleanup_error"] == "caller-desktop-changed"


def test_wrong_thread_cannot_uninitialize_an_owned_monitor(native):
    context = environment.text_services()
    journal = context.__enter__()
    failures = []

    def finish():
        try:
            context.__exit__(None, None, None)
        except RuntimeError as exc:
            failures.append(str(exc))

    thread = threading.Thread(target=finish)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert failures == ["TSF monitor must close on its calling thread"]
    native.uninitialize.assert_not_called()
    assert journal["cleanup_error"] == "caller-thread-changed"


def test_incomplete_inventory_does_not_guess_monitor_ownership(native):
    native.inventory["complete"] = False
    with environment.text_services() as journal:
        assert journal["reason"] == "process-inventory-incomplete"
    native.initialize.assert_not_called()


def test_non_windows_diagnostics_do_not_load_windows_or_docking(monkeypatch):
    monkeypatch.setattr(environment, "sys", SimpleNamespace(platform="linux"))
    factory = Mock(side_effect=AssertionError("loaded Windows"))
    monkeypatch.setattr(environment, "_Environment", factory)
    assert not environment.runtime_snapshot()["available"]
    assert not environment.thread_snapshot(123)["available"]
    assert not environment.target_modules(123)["available"]
    with environment.text_services() as journal:
        assert journal["cleanup_completed"] and not journal["initialized"]
    factory.assert_not_called()


def test_pe_reader_has_bounded_offsets_and_reports_actual_architecture(tmp_path):
    file = tmp_path / "owned.dll"
    data = bytearray(134)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, 0x14C)
    file.write_bytes(data)
    assert environment._machine(file) == "x86"
    struct.pack_into("<I", data, 60, 0xFFFFFFFF)
    file.write_bytes(data)
    with pytest.raises(ValueError, match="bound"):
        environment._machine(file)


def test_registry_diagnostics_never_inspect_or_publish_private_or_unc_paths(
    monkeypatch,
):
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    native = object.__new__(environment._Environment)
    for value in (
        r"C:\Users\Private\input.dll",
        r"\\private-server\share\input.dll",
        r"C:\Windows\..\Private\input.dll",
    ):
        assert native.file(value) == {"path": "<outside-system-directory>"}


def test_x86_registry_binary_uses_the_wow64_file_view(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setattr(environment, "Path", PureWindowsPath)
    machine = Mock(return_value="x86")
    monkeypatch.setattr(environment, "_machine", machine)
    monkeypatch.setattr(
        environment._Environment, "_version", lambda self, path: "1.2.3.4"
    )
    native = object.__new__(environment._Environment)
    facts = native.file(r"C:\Windows\System32\IME\imetip.dll", view="x86")
    machine.assert_called_once_with(
        PureWindowsPath(r"C:\Windows\SysWOW64\IME\imetip.dll")
    )
    assert facts["machine"] == "x86"
    assert facts["registered_path"] == r"%SystemRoot%\System32\IME\imetip.dll"


def test_thread_read_does_not_create_or_activate_manager_and_releases_imm(monkeypatch):
    native = object.__new__(environment._Environment)
    native.user, native.kernel = "user", "kernel"
    monkeypatch.setattr(native, "desktop", lambda: {"session": 3, "thread": 7})
    calls = []

    def output(pointer, value):
        pointer._obj.value = value
        return 1

    functions = {
        "GetWindowThreadProcessId": lambda window, pid: output(pid, os.getpid()) and 7,
        "GetKeyboardLayout": lambda thread: 0x04090409,
        "ImmIsIME": lambda layout: 0,
        "ImmGetContext": lambda window: 99,
        "ImmGetOpenStatus": lambda context: 1,
        "ImmGetConversionStatus": lambda context, conversion, sentence: (
            output(conversion, 1) and output(sentence, 2)
        ),
        "ImmReleaseContext": lambda window, context: (
            calls.append((window, context)) or 1
        ),
        "TF_GetThreadMgr": lambda pointer: output(pointer, None) and 0,
    }

    def bind(library, name, result, *arguments):
        return functions[name]  # Unknown/mutating APIs fail the test immediately.

    monkeypatch.setattr(environment, "bind", bind)
    monkeypatch.setattr(
        environment.c, "WinDLL", lambda *args, **kwargs: "library", raising=False
    )
    facts = native.thread(17)
    assert not facts["tsf_manager"]
    assert facts["tsf_hresult"] == "0x00000000"
    assert facts["imm_conversion"] == 1 and facts["imm_sentence"] == 2
    assert calls == [(17, 99)]


def test_bootstrap_uses_sdk_language_bar_off_flag_without_other_initialization(
    monkeypatch,
):
    native = object.__new__(environment._Environment)
    calls, libraries = [], []
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")

    def bind(library, name, result, *arguments):
        return lambda *values: calls.append((name, values)) or 0

    monkeypatch.setattr(environment, "bind", bind)

    def load(path, *, use_last_error):
        assert use_last_error
        libraries.append(path)
        return "monitor"

    monkeypatch.setattr(environment.c, "WinDLL", load, raising=False)
    assert native.initialize() == 0 and native.uninitialize() == 0
    assert calls == [("InitLocalMsCtfMonitor", (2,)), ("UninitLocalMsCtfMonitor", ())]
    assert [PureWindowsPath(path) for path in libraries] == [
        PureWindowsPath(r"C:\Windows\System32\MsCtfMonitor.dll")
    ] * 2


@pytest.mark.parametrize("disappeared", [False, True])
def test_empty_or_disappearing_ctfmon_cannot_establish_desktop_absence(
    monkeypatch, disappeared
):
    class DisappearedError(RuntimeError):
        pass

    threads = Mock(
        side_effect=DisappearedError() if disappeared else None, return_value=[]
    )
    process = SimpleNamespace(
        pid=19,
        info={"name": "ctfmon.exe"},
        exe=lambda: "system-input",
        threads=threads,
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            process_iter=lambda attributes: [process],
            Error=DisappearedError,
        ),
    )
    native = object.__new__(environment._Environment)
    native.kernel = "kernel"
    monkeypatch.setattr(native, "file", lambda value: {"machine": "x64"})

    def session(pid, result):
        result._obj.value = 3
        return 1

    monkeypatch.setattr(environment, "bind", lambda *args: session)
    inventory = native.processes()
    assert not inventory["items"][0]["desktop_scan_complete"]
    desktop = {"session": 3, "desktop": "Default", "window_station": "WinSta0"}
    assert environment._monitor_reason(desktop, inventory) == "monitor-desktop-unknown"


def test_empty_desktop_list_remains_ambiguous_even_if_inventory_claims_complete(native):
    native.inventory["items"] = [
        {
            "name": "ctfmon.exe",
            "session": 3,
            "machine": "x64",
            "desktops": [],
            "desktop_scan_complete": True,
        }
    ]
    with environment.text_services() as journal:
        assert journal["reason"] == "monitor-desktop-unknown"
    native.initialize.assert_not_called()
    native.uninitialize.assert_not_called()


def test_diagnostic_import_cannot_preload_source_before_installed_wheel_selection():
    script = """
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("input_environment", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert not any(name == "docking" or name.startswith("docking.") for name in sys.modules)
assert "gi" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(Path(environment.__file__).resolve())],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def tsf_thread(monkeypatch):
    native_method, native_release = environment.method, environment.release
    native = object.__new__(environment._Environment)
    native.user, native.kernel = "user", "kernel"
    monkeypatch.setattr(native, "desktop", lambda: {"session": 3, "thread": 7})
    released, queried = [], []
    values = SimpleNamespace(
        manager=1,
        manager_hr=0,
        keys=5,
        keys_hr=0,
        foreground_hr=0,
        thread_focus=True,
        thread_focus_hr=0,
        document=2,
        document_hr=0,
        context=3,
        context_hr=0,
        status_hr=0,
        view=6,
        view_hr=0,
        window=17,
        window_hr=0,
        failure=None,
    )

    def output(pointer, value, status=0):
        pointer._obj.value = value
        return status

    functions = {
        "GetWindowThreadProcessId": lambda window, pid: output(pid, os.getpid(), 7),
        "GetKeyboardLayout": lambda thread: 0x04090409,
        "ImmIsIME": lambda layout: 0,
        "ImmGetContext": lambda window: 0,
        "TF_GetThreadMgr": lambda pointer: output(
            pointer, values.manager, values.manager_hr
        ),
    }

    def query(pointer, iid, result):
        queried.append(bytes(iid._obj))
        if bytes(iid._obj) == bytes(environment._KEYSTROKE_MANAGER):
            return output(result, values.keys, values.keys_hr)
        assert bytes(iid._obj) == bytes(environment._THREAD_MANAGER_EX)
        return output(result, 4)

    def foreground(pointer, result):
        result._obj[:] = uuid.UUID(environment._TIP).bytes_le
        return values.foreground_hr

    def status(pointer, result):
        result._obj.dynamic, result._obj.static = 0x80000001, 0x40000002
        return values.status_hr

    methods = {
        (1, 0): query,
        (1, 10): lambda pointer, result: output(
            result, values.thread_focus, values.thread_focus_hr
        ),
        (5, 5): foreground,
        (4, 15): lambda pointer, result: output(result, 0x80000000),
        (1, 7): lambda pointer, result: output(
            result, values.document, values.document_hr
        ),
        (2, 6): lambda pointer, result: output(
            result, values.context, values.context_hr
        ),
        (3, 11): status,
        (3, 9): lambda pointer, result: output(result, values.view, values.view_hr),
        (6, 6): lambda pointer, result: output(result, values.window, values.window_hr),
    }
    p = environment.c.POINTER
    signatures = {
        (1, 0): (p(environment._GUID), p(environment._P)),
        (1, 10): (p(environment._B),),
        (5, 5): (p(environment._GUID),),
        (4, 15): (p(environment._U),),
        (1, 7): (p(environment._P),),
        (2, 6): (p(environment._P),),
        (3, 11): (p(environment._ContextStatus),),
        (3, 9): (p(environment._P),),
        (6, 6): (p(environment._P),),
    }

    def method(pointer, index, result, *arguments):
        key = (pointer.value, index)
        assert result is environment.HRESULT
        assert arguments == signatures[key]
        if values.failure == key:
            raise OSError(5, "private/context/path")
        return methods[key]

    monkeypatch.setattr(
        environment, "bind", lambda library, name, *args: functions[name]
    )
    monkeypatch.setattr(
        environment,
        "method",
        method,
    )
    monkeypatch.setattr(
        environment, "release", lambda pointer: released.append(pointer.value)
    )
    monkeypatch.setattr(
        environment.c, "WinDLL", lambda *args, **kwargs: "library", raising=False
    )
    return SimpleNamespace(
        native=native,
        values=values,
        released=released,
        queried=queried,
        functions=functions,
        native_method=native_method,
        native_release=native_release,
    )


@pytest.mark.parametrize("focused", [False, True])
def test_thread_inspection_balances_existing_com_references_without_activation(
    tsf_thread, focused
):
    if not focused:
        tsf_thread.values.document = None
        tsf_thread.values.document_hr = 1
    facts = tsf_thread.native.thread(17)
    assert facts["tsf_flags"] == 0x80000000
    assert facts["tsf_document"] is focused
    assert facts["tsf_thread_focus"]
    assert facts["tsf_foreground_clsid"] == environment._TIP.strip("{}").lower()
    if focused:
        assert facts["tsf_dynamic_flags"] == 0x80000001
        assert facts["tsf_static_flags"] == 0x40000002
        assert facts["tsf_view_matches_window"]
    assert tsf_thread.released == ([6, 3, 2, 4, 5, 1] if focused else [4, 5, 1])
    json.dumps(facts)


@pytest.mark.parametrize("status", [1, -2147467259])
def test_missing_or_failed_foreground_service_does_not_report_garbage_clsid(
    tsf_thread, status
):
    tsf_thread.values.foreground_hr = status
    facts = tsf_thread.native.thread(17)
    assert facts["tsf_foreground_hresult"] == environment._hr(status)
    if status == 1:
        assert facts["tsf_foreground_clsid"] is None
    else:
        assert "tsf_foreground_clsid" not in facts
    assert tsf_thread.released == [6, 3, 2, 4, 5, 1]


@pytest.mark.parametrize("window", [None, 1234])
def test_windowless_or_other_tsf_view_is_observed_without_reassociating_it(
    tsf_thread, window
):
    tsf_thread.values.window = window
    facts = tsf_thread.native.thread(17)
    assert facts["tsf_view_window"] == window
    assert not facts["tsf_view_matches_window"]
    assert tsf_thread.released == [6, 3, 2, 4, 5, 1]


@pytest.mark.parametrize(
    "stage,pointer,released",
    [
        ("manager", 1, [1]),
        ("manager", None, []),
        ("keys", 5, [6, 3, 2, 4, 5, 1]),
        ("keys", None, [6, 3, 2, 4, 1]),
        ("document", 2, [2, 4, 5, 1]),
        ("document", None, [4, 5, 1]),
        ("context", 3, [3, 2, 4, 5, 1]),
        ("context", None, [2, 4, 5, 1]),
        ("view", 6, [6, 3, 2, 4, 5, 1]),
        ("view", None, [3, 2, 4, 5, 1]),
    ],
)
def test_failed_pointer_getters_release_every_nonnull_reference(
    tsf_thread, stage, pointer, released
):
    setattr(tsf_thread.values, stage, pointer)
    setattr(tsf_thread.values, stage + "_hr", -2147467259)
    facts = tsf_thread.native.thread(17)
    assert tsf_thread.released == released
    json.dumps(facts)


@pytest.mark.parametrize(
    "stage,fields",
    [
        ("thread_focus", ("tsf_thread_focus",)),
        ("status", ("tsf_dynamic_flags", "tsf_static_flags")),
        ("window", ("tsf_view_window", "tsf_view_matches_window")),
    ],
)
def test_failed_value_getters_leave_outputs_unknown_and_keep_other_observations(
    tsf_thread, stage, fields
):
    setattr(tsf_thread.values, stage + "_hr", -2147467259)
    facts = tsf_thread.native.thread(17)
    assert all(field not in facts for field in fields)
    assert facts["tsf_document"] and facts["tsf_context"] and facts["tsf_view"]
    assert tsf_thread.released == [6, 3, 2, 4, 5, 1]


def test_late_getter_exception_still_releases_all_acquired_interfaces(tsf_thread):
    tsf_thread.values.failure = (6, 6)
    with pytest.raises(OSError, match="private/context/path"):
        tsf_thread.native.thread(17)
    assert tsf_thread.released == [6, 3, 2, 4, 5, 1]


def test_passive_getters_use_real_pointer_sized_com_vtables(tsf_thread, monkeypatch):
    c = environment.c
    # Exercise the same ctypes bridge on Linux and Windows; only the native
    # calling-convention factory differs at this external platform boundary.
    convention = c.WINFUNCTYPE if sys.platform == "win32" else c.CFUNCTYPE
    monkeypatch.setattr(c, "WINFUNCTYPE", convention, raising=False)
    monkeypatch.setattr(environment, "method", tsf_thread.native_method)
    monkeypatch.setattr(environment, "release", tsf_thread.native_release)
    keepalive, released = [], []
    p = c.POINTER

    class Object(c.Structure):
        _fields_ = (("vtable", p(environment._P)),)

    def interface(name, count, entries):
        table = (environment._P * count)()
        obj = Object(table)

        def release(_this):
            released.append(name)
            return 0

        entries[2] = (environment._U, (), release)
        for index, (result, arguments, callback) in entries.items():
            function = convention(result, environment._P, *arguments)(callback)
            table[index] = c.cast(function, environment._P).value
            keepalive.append(function)
        keepalive.extend((table, obj))
        return c.cast(c.pointer(obj), environment._P).value

    def assign(output, value):
        output[0] = value
        return 0

    hwnd = 0x123456789ABC if c.sizeof(environment._P) == 8 else 0x12345678
    hr = environment.HRESULT
    view = interface(
        "view", 7, {6: (hr, (p(environment._P),), lambda _, out: assign(out, hwnd))}
    )

    def status(_this, output):
        output.contents.dynamic = 0x80000001
        output.contents.static = 0x40000002
        return 0

    context = interface(
        "context",
        12,
        {
            9: (hr, (p(environment._P),), lambda _, out: assign(out, view)),
            11: (hr, (p(environment._ContextStatus),), status),
        },
    )
    document = interface(
        "document",
        7,
        {6: (hr, (p(environment._P),), lambda _, out: assign(out, context))},
    )
    extended = interface(
        "extended",
        16,
        {15: (hr, (p(environment._U),), lambda _, out: assign(out, 0x80000000))},
    )

    def foreground(_this, output):
        c.memmove(output, uuid.UUID(environment._TIP).bytes_le, 16)
        return 0

    keys = interface("keys", 6, {5: (hr, (p(environment._GUID),), foreground)})

    def query(_this, iid, output):
        identifier = uuid.UUID(bytes_le=bytes(iid.contents))
        assert str(identifier) in (
            "aa80e7f0-2021-11d2-93e0-0060b067b86e",
            "3e90ade3-7594-4cb0-bb58-69628f5f458c",
        )
        return assign(
            output, keys if str(identifier).startswith("aa80e7f0") else extended
        )

    manager = interface(
        "manager",
        11,
        {
            0: (hr, (p(environment._GUID), p(environment._P)), query),
            7: (hr, (p(environment._P),), lambda _, out: assign(out, document)),
            10: (hr, (p(environment._B),), lambda _, out: assign(out, 0)),
        },
    )
    tsf_thread.functions["TF_GetThreadMgr"] = lambda output: (
        setattr(output._obj, "value", manager) or 0
    )
    facts = tsf_thread.native.thread(hwnd)
    assert not facts["tsf_thread_focus"]
    assert facts["tsf_foreground_clsid"] == environment._TIP.strip("{}").lower()
    assert facts["tsf_dynamic_flags"] == 0x80000001
    assert facts["tsf_static_flags"] == 0x40000002
    assert facts["tsf_view_window"] == hwnd and facts["tsf_view_matches_window"]
    assert c.sizeof(environment._ContextStatus) == 8
    assert environment._ContextStatus.static.offset == 4
    assert released == ["view", "context", "document", "extended", "keys", "manager"]


def test_module_inventory_closes_process_handle_and_redacts_access_failure(monkeypatch):
    native = object.__new__(environment._Environment)
    native.kernel = "kernel"
    closed = []

    def architecture(process, machine, platform):
        machine._obj.value = 0
        platform._obj.value = 0x8664
        return 1

    def fail(*args):
        raise OSError(5, "private/target/path")

    functions = {
        "OpenProcess": lambda _rights, _inherit, _pid: 55,
        "IsWow64Process2": architecture,
        "ProcessIdToSessionId": lambda pid, result: 0,
        "K32EnumProcessModulesEx": fail,
        "CloseHandle": lambda handle: closed.append(handle) or 1,
    }
    monkeypatch.setattr(native, "registration", list)
    monkeypatch.setattr(
        environment, "bind", lambda library, name, *args: functions[name]
    )
    facts = native.modules(19)
    assert facts["machine"] == "x64"
    assert facts["errno"] == 5
    assert closed == [55]
    assert "private/target/path" not in json.dumps(facts)


def test_runtime_inventory_performs_only_read_methods(native):
    native.services = Mock(
        return_value=[{"name": "TabletInputService", "status": "stopped"}]
    )
    native.registration = Mock(return_value=[{"view": "x64", "machine": "x64"}])
    native.modules = Mock(return_value={"pid": os.getpid(), "modules": []})
    facts = environment.runtime_snapshot()
    assert facts["desktop"]["desktop"] == "Default"
    assert facts["registration"] == [{"view": "x64", "machine": "x64"}]
    assert facts["session_context"] == {"session": 3, "wts_connection_state": 0}
    native.modules.assert_called_once_with(os.getpid())
    native.initialize.assert_not_called()
    native.uninitialize.assert_not_called()
    json.dumps(facts)


@pytest.fixture
def session_native(monkeypatch):
    c = environment.c
    native = object.__new__(environment._Environment)
    native.user, native.kernel = "user", "kernel"
    state = SimpleNamespace(
        connection=c.c_int32(0),
        console=3,
        remote=0,
        length=4,
        buffer=True,
        success=1,
        query_error=None,
    )
    calls, freed = [], []

    def query(server, session, kind, buffer, length):
        calls.append((server, session, kind))
        buffer._obj.value = c.addressof(state.connection) if state.buffer else None
        length._obj.value = state.length
        if state.query_error:
            raise state.query_error
        return state.success

    def metric(index):
        assert index == 0x1000  # SM_REMOTESESSION only.
        return state.remote

    functions = {
        "GetSystemMetrics": metric,
        "WTSGetActiveConsoleSessionId": lambda: state.console,
        "WTSQuerySessionInformationW": query,
        "WTSFreeMemory": lambda buffer: freed.append(buffer.value),
    }
    signatures = {
        "GetSystemMetrics": ("user", c.c_int32, (c.c_int32,)),
        "WTSGetActiveConsoleSessionId": ("kernel", environment._U, ()),
        "WTSQuerySessionInformationW": (
            "wts",
            environment._B,
            (
                environment._P,
                environment._U,
                c.c_int32,
                c.POINTER(environment._P),
                c.POINTER(environment._U),
            ),
        ),
        "WTSFreeMemory": ("wts", None, (environment._P,)),
    }

    def bind(library, name, result, *arguments):
        # An unexpected information class/API cannot silently query personal
        # data or add a mutating session operation to these diagnostics.
        assert (library, result, arguments) == signatures[name]
        return functions[name]

    monkeypatch.setattr(environment, "bind", bind)
    monkeypatch.setattr(c, "WinDLL", lambda *args, **kw: "wts", raising=False)
    monkeypatch.setattr(c, "get_last_error", lambda: 1722, raising=False)
    return SimpleNamespace(native=native, state=state, calls=calls, freed=freed)


@pytest.mark.parametrize(
    "connection,name",
    [(0, "WTSActive"), (4, "WTSDisconnected"), (9, "WTSInit"), (99, "unknown")],
)
def test_connection_query_reads_only_owned_session_enum_and_frees_buffer(
    session_native, connection, name
):
    session_native.state.connection.value = connection
    facts = session_native.native.session_context(3)
    assert facts == {
        "session": 3,
        "remote_session": False,
        "active_console_session": 3,
        "matches_active_console": True,
        "wts_connection_state": connection,
        "wts_connection_name": name,
    }
    assert session_native.calls == [(None, 3, 8)]
    assert session_native.freed == [
        environment.c.addressof(session_native.state.connection)
    ]


@pytest.mark.parametrize("console", [1, 0xFFFFFFFF])
def test_remote_and_missing_console_facts_do_not_guess_session_attachment(
    session_native, console
):
    session_native.state.remote = 1
    session_native.state.console = console
    facts = session_native.native.session_context(3)
    assert facts["remote_session"]
    assert facts["active_console_session"] == (
        None if console == 0xFFFFFFFF else console
    )
    assert not facts["matches_active_console"]


@pytest.mark.parametrize("buffer", [False, True])
def test_failed_wts_query_keeps_other_session_facts_and_frees_any_returned_buffer(
    session_native, buffer
):
    session_native.state.success = 0
    session_native.state.buffer = buffer
    facts = session_native.native.session_context(3)
    assert facts["wts_error"] == {
        "error": "WTSQuerySessionInformationW",
        "winerror": 1722,
    }
    assert facts["session"] == 3 and facts["active_console_session"] == 3
    assert len(session_native.freed) == int(buffer)
    assert "wts_connection_state" not in facts


@pytest.mark.parametrize(
    "length,buffer", [(0, True), (3, True), (8, True), (0xFFFFFFFF, True), (4, False)]
)
def test_invalid_wts_buffers_are_bounded_freed_and_not_read(
    session_native, length, buffer
):
    session_native.state.length = length
    session_native.state.buffer = buffer
    facts = session_native.native.session_context(3)
    assert facts["wts_error"] == {
        "error": "invalid-connect-state-buffer",
        "bytes": length,
    }
    assert len(session_native.freed) == int(buffer)
    assert "wts_connection_state" not in facts


def test_wts_exception_is_redacted_and_cannot_lose_buffer_or_desktop_identity(
    session_native, native
):
    session_native.state.query_error = OSError(5, "private/client/path")
    native.session_context = session_native.native.session_context
    native.services, native.registration = list, list
    native.modules = lambda pid: {"pid": pid, "modules": []}
    facts = environment.runtime_snapshot()
    assert facts["desktop"] == native.facts
    assert facts["session_context"]["wts_error"] == {
        "error": "OSError",
        "errno": 5,
    }
    assert len(session_native.freed) == 1
    assert "private/client/path" not in json.dumps(facts)


def test_runtime_session_query_is_skipped_when_core_session_identity_is_unknown(native):
    native.desktop = Mock(side_effect=OSError(5, "private/desktop/path"))
    native.session_context = Mock(side_effect=AssertionError("Unknown session"))
    native.services, native.registration = list, list
    native.modules = lambda pid: {"pid": pid, "modules": []}
    facts = environment.runtime_snapshot()
    native.session_context.assert_not_called()
    assert facts["session_context"] == {"error": "desktop-session-unavailable"}
    assert "private/desktop/path" not in json.dumps(facts)


def test_modern_system_input_modules_are_visible_without_com_registration(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    native = object.__new__(environment._Environment)
    native.kernel = "kernel"
    paths = {
        1: r"C:\Windows\System32\InputMethod\Shared\ModernTip.dll",
        2: r"C:\Windows\SysWOW64\IME\Shared\InputProxy.dll",
        3: r"C:\Windows\IME\Shared\AnotherTip.dll",
        4: r"C:\Windows\System32\MsCtfMonitor.dll",
        5: r"C:\Windows\System32\Unrelated.dll",
        6: r"\\private-server\InputMethod\PrivateTip.dll",
        7: r"C:\Users\Private\InputMethod\PrivateTip.dll",
        8: r"C:\Windows\System32\InputMethod\..\..\PrivateTip.dll",
        9: r"C:\Users\Private\msctf.dll",
    }
    inspected = []

    def executable(path):
        inspected.append(str(path))
        return "x64"

    def enumerate_modules(process, handles, size, needed, flags):
        for index, handle in enumerate(paths):
            handles[index] = handle
        needed._obj.value = len(paths) * environment.c.sizeof(environment._P)
        return 1

    def filename(process, handle, output, size):
        output.value = paths[handle]
        return len(output.value)

    functions = {
        "OpenProcess": lambda *args: 55,
        "IsWow64Process2": lambda *args: 0,
        "ProcessIdToSessionId": lambda *args: 0,
        "K32EnumProcessModulesEx": enumerate_modules,
        "K32GetModuleFileNameExW": filename,
        "CloseHandle": lambda handle: 1,
    }
    monkeypatch.setattr(native, "registration", list)
    monkeypatch.setattr(native, "_version", lambda path: "1.2.3.4")
    monkeypatch.setattr(environment, "_machine", executable)
    monkeypatch.setattr(
        environment, "bind", lambda library, name, *args: functions[name]
    )
    facts = native.modules(19)
    assert len(facts["modules"]) == 5
    assert [PureWindowsPath(path) for path in inspected] == [
        PureWindowsPath(path) for path in list(paths.values())[:4]
    ]
    serialized = json.dumps(facts)
    assert "ModernTip.dll" in serialized and "MsCtfMonitor.dll" in serialized
    assert "private-server" not in serialized and "Private" not in serialized
    assert facts["modules"][-1] == {"path": "<outside-system-directory>"}
