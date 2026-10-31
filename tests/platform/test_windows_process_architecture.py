"""Process ABI routing distinguishes x64 emulation from native Arm hook targets."""

import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.backends.windows import ffi, input_thread
from docking.platform.backends.windows.input_context import InputContext


@pytest.fixture
def kernel(monkeypatch):
    state = SimpleNamespace(guest=0, native=0xAA64, actual=0x8664, attributes=1)

    def wow(process, guest, native):
        guest._obj.value, native._obj.value = state.guest, state.native
        return 1

    def information(process, kind, output, size):
        assert kind == 9 and size == 8
        output._obj.machine = state.actual
        output._obj.attributes = state.attributes
        return 1

    library = SimpleNamespace(
        IsWow64Process2=Mock(side_effect=wow),
        GetProcessInformation=Mock(side_effect=information),
        OpenProcess=Mock(return_value=0x123456780001),
        CloseHandle=Mock(return_value=1),
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        ctypes,
        "WinError",
        lambda code: OSError(code, "native query failed"),
        raising=False,
    )
    return library, state


def test_process_machine_information_matches_sdk_abi_and_uses_borrowed_handle(kernel):
    library, _state = kernel
    assert ctypes.sizeof(ffi.ProcessMachineInformation) == 8
    assert ffi.ProcessMachineInformation.machine.offset == 0
    assert ffi.ProcessMachineInformation.reserved.offset == 2
    assert ffi.ProcessMachineInformation.attributes.offset == 4
    handle = 0x123456780001
    assert ffi.process_machine(library, handle) == 0x8664
    query = library.GetProcessInformation
    assert query.call_args.args[0] == handle
    assert query.call_args.args[1] == 9 and query.call_args.args[3] == 8
    assert query.restype is ctypes.c_int32
    assert query.argtypes == (
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.POINTER(ffi.ProcessMachineInformation),
        ctypes.c_uint32,
    )
    library.OpenProcess.assert_not_called()
    library.CloseHandle.assert_not_called()


@pytest.mark.parametrize(
    ("guest", "native", "expected"),
    [
        (0, 0x8664, 0x8664),
        (0x14C, 0x8664, 0x14C),
        (0x14C, 0xAA64, 0x14C),
        (0x8664, 0xAA64, 0x8664),
        (0, 0, 0),
        (0xA641, 0xAA64, 0xA641),
    ],
)
def test_unambiguous_wow_results_preserve_older_windows_behavior(
    kernel, guest, native, expected
):
    library, state = kernel
    state.guest, state.native = guest, native
    del library.GetProcessInformation  # No new API requirement on these older paths.
    assert ffi.process_machine(library, 9) == expected


@pytest.mark.parametrize("actual", [0x8664, 0x14C, 0xAA64, 0xA641, 0xA64E, 0])
def test_ambiguous_arm_host_returns_actual_process_abi_without_guessing(kernel, actual):
    library, state = kernel
    state.actual = actual
    assert ffi.process_machine(library, 9) == actual
    assert library.GetProcessInformation.call_count == 1


def test_older_arm_without_machine_type_info_cannot_authorize_a_guessed_amd64(kernel):
    library, _state = kernel
    del library.GetProcessInformation
    with pytest.raises(OSError, match="unavailable"):
        ffi.process_machine(library, 9)


@pytest.mark.parametrize("error", [5, 50, 87])
def test_failed_machine_query_cannot_fall_back_to_host_architecture(
    kernel, monkeypatch, error
):
    library, _state = kernel
    library.GetProcessInformation.side_effect = None
    library.GetProcessInformation.return_value = 0
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error)
    with pytest.raises(OSError) as failure:
        ffi.process_machine(library, 9)
    assert failure.value.errno == error


def test_wow_query_failure_does_not_consume_uninitialized_architecture(kernel):
    library, _state = kernel
    library.IsWow64Process2.side_effect = None
    library.IsWow64Process2.return_value = 0
    with pytest.raises(OSError):
        ffi.process_machine(library, 9)
    library.GetProcessInformation.assert_not_called()


@pytest.mark.parametrize("actual", [0xAA64, 0xA641, 0xA64E, 0])
def test_input_hook_rejects_native_arm_hybrid_or_unknown_before_loading_any_helper(
    kernel, actual
):
    library, state = kernel
    state.actual = actual
    client = input_thread.InputThread.__new__(input_thread.InputThread)
    client._kernel = library
    client._dispatch = Mock(side_effect=AssertionError("hooked an unsupported target"))
    with pytest.raises(OSError, match="Unsupported input-thread architecture"):
        client.request(InputContext(1, 1, 2, 3), threading.Event())
    client._dispatch.assert_not_called()
    library.CloseHandle.assert_called_once_with(0x123456780001)


@pytest.mark.parametrize("actual", [0x8664, 0x14C])
def test_input_machine_reader_accepts_actual_amd64_or_x86_and_closes_owned_handle(
    kernel, actual
):
    library, state = kernel
    state.actual = actual
    client = input_thread.InputThread.__new__(input_thread.InputThread)
    client._kernel = library
    assert client._machine(3) == actual
    library.OpenProcess.assert_called_once_with(0x1000, False, 3)
    library.CloseHandle.assert_called_once_with(0x123456780001)


def test_input_machine_error_still_releases_the_owned_process_handle(kernel):
    library, _state = kernel
    library.GetProcessInformation.side_effect = OSError(5, "denied")
    client = input_thread.InputThread.__new__(input_thread.InputThread)
    client._kernel = library
    with pytest.raises(OSError, match="denied"):
        client._machine(3)
    library.CloseHandle.assert_called_once_with(0x123456780001)


def test_failed_process_open_never_closes_a_borrowed_or_invalid_handle(kernel):
    library, _state = kernel
    library.OpenProcess.return_value = 0
    client = input_thread.InputThread.__new__(input_thread.InputThread)
    client._kernel = library
    with pytest.raises(OSError):
        client._machine(3)
    library.IsWow64Process2.assert_not_called()
    library.CloseHandle.assert_not_called()
