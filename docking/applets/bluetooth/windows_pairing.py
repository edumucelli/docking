"""Bound an owned Windows authentication wizard without blocking GTK."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path

from docking.platform import environment
from docking.platform.backends.windows.ffi import bind

_CANCELLED = 1223
_TIMEOUT = 1460
_GENERAL_FAILURE = 31


class PairingOperation:
    """Keep one prompt in a disposable process; cancel() performs no IO."""

    def __init__(self, *, command: Sequence[str] | None = None) -> None:
        self._command = tuple(command) if command is not None else None
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(
        self,
        radio: int,
        device: int,
        timeout_s: int,
        *,
        on_ready: Callable[[int, int], None] | None = None,
    ) -> bool:
        if (
            any(
                type(value) is not int or not 0 < value < 1 << 48
                for value in (radio, device)
            )
            or type(timeout_s) is not int
            or not 0 < timeout_s <= 120
        ):
            raise ValueError("Invalid Bluetooth pairing request")
        if self._cancelled.is_set():
            return False
        deadline = time.monotonic() + timeout_s
        command = self._command
        if command is None:
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            isolation = ("-I",) if sys.flags.isolated else ()
            command = (
                str(executable),
                *isolation,
                "-u",
                "-m",
                "docking.applets.bluetooth.windows_pairing",
            )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=0x08000000 if environment.is_windows() else 0,
        )
        messages: queue.Queue[str | None] = queue.Queue(maxsize=8)
        malformed = threading.Event()
        reader = None
        job = None
        try:
            if environment.is_windows():
                from docking.platform.backends.windows.process_job import ProcessJob

                job = ProcessJob()
                # CPython owns this handle; avoid reopening a reusable PID.
                job.assign(int(process._handle))
            assert process.stdin is not None and process.stdout is not None
            if self._cancelled.is_set() or time.monotonic() >= deadline:
                return False
            # The child waits for input before creating a window or touching a
            # radio. Its process tree is already assigned to our Windows job.
            process.stdin.write(
                json.dumps({"radio": radio, "device": device, "timeout": timeout_s})
                + "\n"
            )
            process.stdin.flush()
            output = process.stdout

            def receive() -> None:
                try:
                    while line := output.readline(4097):
                        if len(line) > 4096:
                            malformed.set()
                            break
                        messages.put_nowait(line)
                except queue.Full:
                    malformed.set()
                except (OSError, ValueError):
                    pass  # Parent cleanup may close the completed child's pipe.
                finally:
                    try:
                        messages.put_nowait(None)
                    except queue.Full:
                        malformed.set()

            reader = threading.Thread(target=receive, daemon=True)
            reader.start()
            ready = False
            cancelling = False
            while True:
                now = time.monotonic()
                if (self._cancelled.is_set() or now >= deadline) and not cancelling:
                    cancelling = True
                    deadline = now + 1
                    try:
                        process.stdin.write("cancel\n")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        return False
                if malformed.is_set():
                    raise ValueError("Invalid Bluetooth pairing reply")
                if cancelling and now >= deadline:
                    return False
                try:
                    line = messages.get(timeout=0.05)
                except queue.Empty:
                    continue
                if line is None:
                    return False
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Invalid Bluetooth pairing reply")
                if value.get("phase") == "ready" and not ready:
                    owner = value.get("owner")
                    if type(owner) is not int or not owner:
                        raise ValueError("Invalid Bluetooth pairing owner")
                    ready = True
                    if on_ready is not None:
                        on_ready(process.pid, owner)
                elif value.get("phase") == "result":
                    status = value.get("status")
                    if type(status) is not int or not 0 <= status <= 0xFFFFFFFF:
                        raise ValueError("Invalid Bluetooth pairing status")
                    return (
                        ready
                        and status in (0, 259)
                        and not cancelling
                        and not self._cancelled.is_set()
                    )
                else:
                    raise ValueError("Invalid Bluetooth pairing reply")
        finally:
            if process.stdin is not None:
                with suppress(OSError):
                    process.stdin.close()
            try:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            finally:
                if job is not None:
                    job.close()
                if reader is not None:
                    reader.join(timeout=1)
                if process.stdout is not None:
                    process.stdout.close()


class PairingWindow:
    """Pump the helper's hidden owner and dismiss only its operation windows."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self._message_type = wintypes.MSG
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._owner = 0
        self._thread = 0
        self._create = bind(
            self._user,
            "CreateWindowExW",
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        self._destroy = bind(
            self._user, "DestroyWindow", ctypes.c_int32, ctypes.c_void_p
        )
        self._post = bind(
            self._user,
            "PostMessageW",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        self._ancestor = bind(
            self._user, "GetAncestor", ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
        )
        self._window_thread = bind(
            self._user,
            "GetWindowThreadProcessId",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self._thread_id = bind(
            ctypes.WinDLL("kernel32"), "GetCurrentThreadId", ctypes.c_uint32
        )
        self._peek = bind(
            self._user,
            "PeekMessageW",
            ctypes.c_int32,
            ctypes.POINTER(wintypes.MSG),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
        )
        self._translate = bind(
            self._user, "TranslateMessage", ctypes.c_int32, ctypes.POINTER(wintypes.MSG)
        )
        self._dispatch = bind(
            self._user,
            "DispatchMessageW",
            ctypes.c_ssize_t,
            ctypes.POINTER(wintypes.MSG),
        )
        self._metric = bind(self._user, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)

    def _pump(self) -> None:
        message = self._message_type()
        while self._peek(ctypes.byref(message), None, 0, 0, 1):
            self._translate(ctypes.byref(message))
            self._dispatch(ctypes.byref(message))

    def _dismiss(self) -> None:
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p, ctypes.c_ssize_t
        )

        def close(window, _parameter):
            pid = ctypes.c_uint32()
            thread = self._window_thread(window, ctypes.byref(pid))
            if window != self._owner and (
                self._ancestor(window, 3) == self._owner
                or (pid.value == os.getpid() and thread == self._thread)
            ):
                self._post(window, 0x10, 0, 0)
            return True

        callback = callback_type(close)
        bind(
            self._user, "EnumWindows", ctypes.c_int32, callback_type, ctypes.c_ssize_t
        )(callback, 0)

    def run(
        self, action: Callable[[int], int], timeout_s: int, cancelled: threading.Event
    ) -> int:
        owner = self._create(
            0x80,
            "STATIC",
            "Docking Bluetooth Pairing",
            0,
            self._metric(0) // 2 - 200,
            self._metric(1) // 2 - 100,
            400,
            200,
            None,
            None,
            None,
            None,
        )
        if not owner:
            raise ctypes.WinError(ctypes.get_last_error())
        self._owner = int(owner)
        done = threading.Event()
        result = [_GENERAL_FAILURE]

        def execute() -> None:
            self._thread = int(self._thread_id())
            try:
                result[0] = _CANCELLED if cancelled.is_set() else action(self._owner)
            except (OSError, ValueError):
                result[0] = _GENERAL_FAILURE
            finally:
                done.set()

        worker = threading.Thread(target=execute, daemon=True)
        deadline = time.monotonic() + timeout_s
        cancellation_status = None
        try:
            print(json.dumps({"phase": "ready", "owner": self._owner}), flush=True)
            worker.start()
            while not done.is_set():
                self._pump()
                now = time.monotonic()
                if cancelled.is_set() or now >= deadline:
                    if cancellation_status is None:
                        cancellation_status = (
                            _CANCELLED if cancelled.is_set() else _TIMEOUT
                        )
                        deadline = now + 0.75
                        cancelled.set()
                    self._dismiss()
                    if now >= deadline:
                        break
                done.wait(0.02)
            return cancellation_status if cancellation_status is not None else result[0]
        finally:
            self._dismiss()
            self._pump()
            self._destroy(self._owner)
            self._owner = 0


def authenticate(owner: int, radio_address: int, device_address: int) -> int:
    from docking.applets.bluetooth.windows_radio import ClassicInventory, _DeviceInfo

    if (
        type(owner) is not int
        or not owner
        or any(
            type(value) is not int or not 0 < value < 1 << 48
            for value in (radio_address, device_address)
        )
    ):
        return 87
    with ClassicInventory().radio_handle(radio_address) as radio:
        if radio is None or not radio.value:
            return 1168
        library = ctypes.WinDLL("bthprops.cpl", use_last_error=True)
        read = bind(
            library,
            "BluetoothGetDeviceInfo",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(_DeviceInfo),
        )
        pair = bind(
            library,
            "BluetoothAuthenticateDeviceEx",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_DeviceInfo),
            ctypes.c_void_p,
            ctypes.c_int32,
        )
        info = _DeviceInfo()
        info.size, info.address = ctypes.sizeof(info), device_address
        status = int(read(radio, ctypes.byref(info)))
        if status:
            return status
        if info.address != device_address:
            return 87
        if info.authenticated:
            return 259
        # General bonding; Windows selects the ceremony and owns PIN entry.
        return int(pair(owner, radio, ctypes.byref(info), None, 4))


def serve(action: Callable[[int, int, int], int]) -> None:
    """Read a bounded request and cancel if the owning parent's pipe closes."""
    if not environment.is_windows():
        raise SystemExit("Requires Windows")
    value = json.loads(sys.stdin.readline(4097))
    if not isinstance(value, dict):
        raise ValueError("Invalid Bluetooth pairing request")
    radio, device, timeout = (
        value.get("radio"),
        value.get("device"),
        value.get("timeout"),
    )
    if (
        any(type(item) is not int or not 0 < item < 1 << 48 for item in (radio, device))
        or type(timeout) is not int
        or not 0 < timeout <= 120
    ):
        raise ValueError("Invalid Bluetooth pairing request")
    cancelled = threading.Event()

    def control() -> None:
        sys.stdin.readline(64)
        cancelled.set()  # Explicit cancellation, or the owning parent exited.

    threading.Thread(target=control, daemon=True).start()
    status = PairingWindow().run(
        lambda owner: action(owner, radio, device), timeout, cancelled
    )
    print(json.dumps({"phase": "result", "status": status}), flush=True)


if __name__ == "__main__":
    serve(authenticate)
