"""Owned pairing IPC deadlines, cancellation and radio-handle lifetime."""

import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from docking.applets.bluetooth import windows_pairing as mod


def peer(mode):
    script = "\n".join(
        (
            "import json,sys,time",
            "request=json.loads(sys.stdin.readline())",
            "assert request['radio']==0x112233445566",
            "assert request['device']==0xAABBCCDDEEFF",
            "print(json.dumps({'phase':'ready','owner':42}),flush=True)",
            "mode=" + repr(mode),
            "if mode in ('cancel','late-success'):",
            " assert sys.stdin.readline().strip()=='cancel'",
            "elif mode=='stall': time.sleep(30)",
            "status=0 if mode in ('success','late-success') else 259 if mode=='already' else 1223",
            "print(json.dumps({'phase':'result','status':status}),flush=True)",
        )
    )
    return (sys.executable, "-u", "-c", script)


@pytest.mark.parametrize(
    "mode,expected", [("success", True), ("already", True), ("rejected", False)]
)
def test_pairing_operation_requires_an_explicit_success_result(mode, expected):
    operation = mod.PairingOperation(command=peer(mode))
    assert operation.run(0x112233445566, 0xAABBCCDDEEFF, 5) is expected


@pytest.mark.parametrize("mode", ["cancel", "late-success", "stall"])
def test_cancellation_is_nonblocking_and_reaps_the_owned_process(monkeypatch, mode):
    processes = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mod.subprocess, "Popen", spawn)
    operation = mod.PairingOperation(command=peer(mode))
    ready = threading.Event()
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            operation.run(
                0x112233445566,
                0xAABBCCDDEEFF,
                10,
                on_ready=lambda _pid, _owner: ready.set(),
            )
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert ready.wait(5)
        before = time.monotonic()
        operation.cancel()
        assert time.monotonic() - before < 0.25
        worker.join(5)
        assert not worker.is_alive() and results == [False]
        assert processes and all(process.poll() is not None for process in processes)
    finally:
        operation.cancel()
        worker.join(5)


def test_timeout_cancels_a_prompt_that_has_not_completed():
    operation = mod.PairingOperation(command=peer("cancel"))
    before = time.monotonic()
    assert not operation.run(0x112233445566, 0xAABBCCDDEEFF, 1)
    assert time.monotonic() - before < 4


def test_cancel_before_start_never_creates_a_process(monkeypatch):
    launch = Mock(side_effect=AssertionError("Cancelled operation launched"))
    monkeypatch.setattr(mod.subprocess, "Popen", launch)
    operation = mod.PairingOperation(command=peer("success"))
    operation.cancel()
    assert not operation.run(0x112233445566, 0xAABBCCDDEEFF, 5)
    launch.assert_not_called()


@pytest.mark.parametrize(
    "radio,device,timeout",
    [(0, 1, 5), (True, 1, 5), (1, 1 << 48, 5), (1, 2, 0), (1, 2, True)],
)
def test_invalid_requests_never_start_an_authentication_process(
    monkeypatch, radio, device, timeout
):
    launch = Mock(side_effect=AssertionError("Invalid operation launched"))
    monkeypatch.setattr(mod.subprocess, "Popen", launch)
    with pytest.raises(ValueError):
        mod.PairingOperation(command=peer("success")).run(radio, device, timeout)
    launch.assert_not_called()


@pytest.mark.parametrize(
    "message",
    [
        [],
        {"phase": "ready", "owner": True},
        {"phase": "result", "status": True},
        {"phase": "other"},
    ],
)
def test_malformed_helper_messages_are_rejected_and_the_child_exits(message):
    code = (
        "import json,sys;sys.stdin.readline();print("
        + repr(json.dumps(message))
        + ",flush=True)"
    )
    operation = mod.PairingOperation(command=(sys.executable, "-u", "-c", code))
    with pytest.raises(ValueError):
        operation.run(0x112233445566, 0xAABBCCDDEEFF, 5)


def test_authentication_without_a_matching_radio_never_loads_an_authenticator(
    monkeypatch,
):
    from docking.applets.bluetooth import windows_radio

    @contextmanager
    def missing(_address):
        yield None

    inventory = Mock()
    inventory.radio_handle = missing
    monkeypatch.setattr(windows_radio, "ClassicInventory", lambda: inventory)
    load = Mock(side_effect=AssertionError("Authentication DLL loaded without radio"))
    monkeypatch.setattr(mod.ctypes, "WinDLL", load, raising=False)
    assert mod.authenticate(123, 0x112233445566, 0xAABBCCDDEEFF) == 1168
    load.assert_not_called()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("success", 0),
        ("cancelled", 1223),
        ("paired", 259),
        ("stale", 87),
        ("denied", 5),
    ],
)
def test_authentication_uses_the_selected_handle_and_preserves_native_results(
    monkeypatch, mode, expected
):
    import ctypes

    from docking.applets.bluetooth import windows_radio

    closed, calls = [], []

    @contextmanager
    def selected(address):
        assert address == 0x112233445566
        try:
            yield ctypes.c_void_p(42)
        finally:
            closed.append(True)

    inventory = Mock()
    inventory.radio_handle = selected
    monkeypatch.setattr(windows_radio, "ClassicInventory", lambda: inventory)
    monkeypatch.setattr(
        mod.ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )

    def read(handle, info):
        assert handle.value == 42 and info._obj.address == 0xAABBCCDDEEFF
        assert info._obj.size == ctypes.sizeof(windows_radio._DeviceInfo)
        if mode == "denied":
            return 5
        if mode == "stale":
            info._obj.address += 1
        info._obj.authenticated = mode == "paired"
        return 0

    def pair(owner, handle, info, oob, requirements):
        calls.append(True)
        assert owner == 123 and handle.value == 42
        assert info._obj.address == 0xAABBCCDDEEFF
        assert oob is None and requirements == 4
        return 1223 if mode == "cancelled" else 0

    def binding(_library, name, *_signature):
        return {"BluetoothGetDeviceInfo": read, "BluetoothAuthenticateDeviceEx": pair}[
            name
        ]

    monkeypatch.setattr(mod, "bind", binding)
    assert mod.authenticate(123, 0x112233445566, 0xAABBCCDDEEFF) == expected
    assert calls == ([True] if mode in ("success", "cancelled") else [])
    assert closed == [True]


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows")
def test_native_helper_reports_a_missing_radio_and_destroys_its_owner():
    import ctypes

    from docking.applets.bluetooth.windows_radio import ClassicInventory
    from docking.platform.backends.windows.ffi import bind

    addresses = ClassicInventory().snapshot()
    missing = next(value for value in range(1, 258) if value not in addresses)
    owners = []
    operation = mod.PairingOperation()
    assert not operation.run(
        missing, 0xAABBCCDDEEFF, 20, on_ready=lambda pid, owner: owners.append(owner)
    )
    assert owners
    exists = bind(ctypes.WinDLL("user32"), "IsWindow", ctypes.c_int32, ctypes.c_void_p)
    assert not any(exists(owner) for owner in owners)
