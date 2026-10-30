"""Independent input snapshots preserve native ABI and restoration evidence."""

import ctypes as c
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import input_oracle as probe


class Registry:
    HKEY_CURRENT_USER = 1
    KEY_READ = 0x20019

    def __init__(self):
        self.OpenKey = self.open_key
        self.QueryInfoKey = self.query_info_key
        self.EnumValue = self.enum_value
        self.closed = []
        self.values = {
            r"Keyboard Layout\Preload": [("1", "00000409", 1)],
            r"Keyboard Layout\Substitutes": [("d001040c", "0000040c", 1)],
        }

    @contextmanager
    def open_key(self, hive, path, reserved, access):
        assert (hive, reserved, access) == (self.HKEY_CURRENT_USER, 0, self.KEY_READ)
        try:
            yield path
        finally:
            self.closed.append(path)

    def query_info_key(self, path):
        return 0, len(self.values[path]), 0

    def enum_value(self, path, index):
        return self.values[path][index]


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(c, "get_last_error", lambda: 5, raising=False)
    state = SimpleNamespace(handles=[0xFFFFFFFFF0010409, 0x040C040C], focus=77)

    def layouts(capacity, handles):
        if capacity:
            for index, handle in enumerate(state.handles[:capacity]):
                handles[index] = handle
        return min(capacity, len(state.handles)) if capacity else len(state.handles)

    def thread(window, pid):
        pid._obj.value = 100
        return {99: 7, 77: 8}[window]

    def gui(thread, output):
        assert thread == 7 and output._obj.size == c.sizeof(probe.GUIThreadInfo)
        output._obj.focus = state.focus
        return 1

    def parameters(operation, reserved, output, flags):
        assert (reserved, flags) == (0, 0)
        if operation == 0x59:
            assert type(output._obj) is c.c_void_p
            output._obj.value = 0xFFFFFFFFF0010409
        else:
            assert operation == 0x201C and type(output._obj) is c.c_uint32
            output._obj.value = 2
        return 1

    user = SimpleNamespace(
        GetKeyboardLayoutList=Mock(side_effect=layouts),
        GetForegroundWindow=Mock(return_value=99),
        GetWindowThreadProcessId=Mock(side_effect=thread),
        GetGUIThreadInfo=Mock(side_effect=gui),
        GetKeyboardLayout=Mock(return_value=0xFFFFFFFFF0010409),
        SystemParametersInfoW=Mock(side_effect=parameters),
    )
    return user, Registry(), state


def test_native_snapshot_uses_fixed_windows_abi_and_focused_control_thread(native):
    user, registry, _state = native
    result = probe.snapshot(user, registry)
    assert result == {
        "schema": 1,
        "pointer_bits": c.sizeof(c.c_void_p) * 8,
        "loaded": [{"code": "f0010409"}, {"code": "040c040c"}],
        "active": "f0010409",
        "default": "f0010409",
        "wheel_route": 2,
        "preload": {"1": {"type": 1, "data": "00000409"}},
        "substitutes": {"d001040c": {"type": 1, "data": "0000040c"}},
    }
    assert probe.validate(result) is result
    user.GetKeyboardLayout.assert_called_once_with(8)
    assert c.sizeof(probe.Rect) == 16
    assert probe.GUIThreadInfo.active.offset == 8
    assert probe.GUIThreadInfo.rect.offset == 8 + c.sizeof(c.c_void_p) * 6
    assert c.sizeof(probe.GUIThreadInfo) == (72 if c.sizeof(c.c_void_p) == 8 else 48)
    assert user.GetKeyboardLayout.restype is c.c_void_p
    assert user.GetGUIThreadInfo.argtypes == (
        c.c_uint32,
        c.POINTER(probe.GUIThreadInfo),
    )
    assert len(registry.closed) == 2


def test_gui_thread_without_focused_child_keeps_foreground_thread(native):
    user, registry, state = native
    state.focus = 0
    probe.snapshot(user, registry)
    user.GetKeyboardLayout.assert_called_once_with(7)


@pytest.mark.parametrize("operation", (0x59, 0x201C))
def test_system_parameter_failure_is_numeric_and_never_becomes_restored_minus_one(
    native, operation
):
    user, registry, _state = native
    parameters = user.SystemParametersInfoW.side_effect
    user.SystemParametersInfoW.side_effect = lambda kind, *args: (
        0 if kind == operation else parameters(kind, *args)
    )
    with pytest.raises(probe.OracleError) as failed:
        probe.snapshot(user, registry)
    assert failed.value.code == 5
    assert failed.value.api == (
        "SPI_GETDEFAULTINPUTLANG" if operation == 0x59 else "SPI_GETMOUSEWHEELROUTING"
    )


@pytest.mark.parametrize(
    "api", ("GetKeyboardLayoutList", "GetWindowThreadProcessId", "GetGUIThreadInfo")
)
def test_required_query_failure_never_falls_back_to_caller_state(native, api):
    user, registry, _state = native
    function = vars(user)[api]
    function.side_effect, function.return_value = None, 0
    with pytest.raises(probe.OracleError) as failed:
        probe.snapshot(user, registry)
    assert (failed.value.api, failed.value.code) == (api, 5)


def test_transiently_growing_loaded_set_is_read_completely(native):
    user, registry, _state = native
    attempts = []

    def layouts(capacity, output):
        attempts.append(capacity)
        if not capacity:
            return 1 if len(attempts) == 1 else 2
        for index, handle in enumerate((0x04090409, 0x040C040C)):
            output[index] = handle
        return 2

    user.GetKeyboardLayoutList.side_effect = layouts
    assert len(probe.snapshot(user, registry)["loaded"]) == 2
    assert attempts == [0, 2, 0, 3]


def test_missing_registry_key_is_empty_and_reads_close_even_on_enumeration_error(
    monkeypatch,
):
    registry = Registry()
    monkeypatch.setattr(
        registry, "OpenKey", Mock(side_effect=FileNotFoundError(2, "missing"))
    )
    assert probe.registry_values(registry, "missing") == {}
    registry = Registry()
    monkeypatch.setattr(
        registry, "EnumValue", Mock(side_effect=PermissionError(13, "denied"))
    )
    with pytest.raises(PermissionError):
        probe.registry_values(registry, r"Keyboard Layout\Preload")
    assert registry.closed == [r"Keyboard Layout\Preload"]


def test_registry_values_include_default_names_and_binary_without_mutations():
    registry = Registry()
    registry.values["owned"] = [("", b"\x00\xff", 3), ("multi", ["one", "two"], 7)]
    assert probe.registry_values(registry, "owned") == {
        "": {"type": 3, "data": {"binary_hex": "00ff"}},
        "multi": {"type": 7, "data": ["one", "two"]},
    }


def test_registry_type_only_mutation_cannot_be_reported_as_restored(native):
    user, registry, _state = native
    baseline = probe.snapshot(user, registry)
    registry.values[r"Keyboard Layout\Preload"] = [("1", "00000409", 2)]
    restored = probe.snapshot(user, registry)
    assert probe.validate(restored) is restored
    assert baseline["preload"]["1"]["data"] == restored["preload"]["1"]["data"]
    assert baseline["preload"] != restored["preload"]


@pytest.fixture
def subprocess_result(monkeypatch, native):
    result = probe.snapshot(*native[:2])
    completed = SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr(probe.subprocess, "run", run)
    return result, completed, run


def test_oracle_uses_fresh_same_python_isolated_stdlib_process_with_fixed_deadline(
    subprocess_result,
):
    expected, _completed, run = subprocess_result
    assert probe.oracle() == expected
    (command,), options = run.call_args
    assert command == [
        sys.executable,
        "-I",
        "-S",
        str(Path(probe.__file__).resolve()),
        "--snapshot",
    ]
    assert options["timeout"] == 30 and options["capture_output"]
    assert options["stdin"] == subprocess.DEVNULL and options["encoding"] == "utf-8"


@pytest.mark.parametrize(
    "stdout", ("{} extra", '{"schema":1,"schema":1}', '{"value":NaN}')
)
def test_oracle_rejects_non_strict_json(subprocess_result, stdout):
    _expected, completed, _run = subprocess_result
    completed.stdout = stdout
    with pytest.raises(ValueError):
        probe.oracle()


@pytest.mark.parametrize(
    "change",
    (
        {"schema": True},
        {"pointer_bits": 32 if c.sizeof(c.c_void_p) == 8 else 64},
        {"loaded": []},
        {"active": "0x0409"},
        {"wheel_route": -1},
        {"wheel_route": True},
        {"preload": []},
        {"preload": {"1": {"type": 1, "data": 1.25}}},
        {"preload": {"1": {"type": True, "data": "00000409"}}},
        {"preload": {"1": {"type": 1, "data": {"binary_hex": "0zz"}}}},
    ),
)
def test_oracle_rejects_schema_abi_and_incomplete_native_state(
    subprocess_result, change
):
    expected, completed, _run = subprocess_result
    completed.stdout = json.dumps({**expected, **change})
    with pytest.raises(ValueError):
        probe.oracle()


def test_child_numeric_error_is_retained_without_stderr_or_private_paths(
    subprocess_result,
):
    _expected, completed, _run = subprocess_result
    completed.returncode = 1
    completed.stdout = '{"schema":1,"error":{"api":"GetGUIThreadInfo","code":5}}'
    completed.stderr = "a private loader path must not escape"
    with pytest.raises(probe.OracleError) as failed:
        probe.oracle()
    assert (failed.value.api, failed.value.code) == ("GetGUIThreadInfo", 5)
    assert "private" not in str(failed.value)


def test_failed_child_cannot_publish_an_apparently_valid_snapshot(subprocess_result):
    _expected, completed, _run = subprocess_result
    completed.returncode = 3
    with pytest.raises(RuntimeError, match=r"exited \(3\)"):
        probe.oracle()


def test_timeout_propagates_without_shell_or_second_attempt(subprocess_result):
    _expected, _completed, run = subprocess_result
    run.side_effect = subprocess.TimeoutExpired("owned oracle", 30)
    with pytest.raises(subprocess.TimeoutExpired):
        probe.oracle()
    assert run.call_count == 1


def test_child_preserves_native_registry_error_without_private_paths(
    monkeypatch, capsys
):
    error = PermissionError(13, "private registry or loader path")
    error.winerror = 5
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(probe.sys, "argv", ["oracle.py", "--snapshot"])
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace())
    monkeypatch.setattr(c, "WinDLL", Mock(), raising=False)
    monkeypatch.setattr(probe, "snapshot", Mock(side_effect=error))
    assert probe.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "schema": 1,
        "error": {"api": "registry-or-loader", "code": 5},
    }
    assert "private" not in output


def test_module_import_is_independent_of_docking_gi_and_site_customizations():
    script = (
        "import importlib.util,sys;"
        "s=importlib.util.spec_from_file_location('oracle',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "assert not any(n=='docking' or n.startswith('docking.') or "
        "n=='gi' or n.startswith('gi.') for n in sys.modules)"
    )
    subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(Path(probe.__file__).resolve())],
        check=True,
        timeout=30,
    )
