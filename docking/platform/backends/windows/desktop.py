"""Explorer Show Desktop actions and the desktop's current window ordering.

Show Desktop raises the shell desktop above non-topmost windows, including
windows which cannot be minimized. MinimizeAll is a different operation.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from typing import Protocol

from docking.log import get_logger
from docking.platform.backends.base import ActionResult, DesktopActionService

from .com import GUID, HRESULT, apartment, method, release
from .ffi import bind

log = get_logger("windows.desktop")
_SHELL_CLASS = GUID.parse("13709620-c279-11ce-a49e-444553540000")
_SHELL_INTERFACE = GUID.parse("efd84b2d-4bcf-4298-be25-eb542a59fbda")
_WINDOWS_CLASS = GUID.parse("9ba05972-f6a8-11cf-a442-00a0c90a8f39")
_WINDOWS_INTERFACE = GUID.parse("85cb6900-4d95-11cf-960c-0080c7f4ee85")
_SHELL_READY_SECONDS = 10.0


class _VariantData(ctypes.Union):
    _fields_ = (("integer", ctypes.c_int32), ("record", ctypes.c_void_p * 2))


class _Variant(ctypes.Structure):
    _fields_ = (
        ("vt", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16 * 3),
        ("data", _VariantData),
    )


class DesktopAPI(Protocol):
    def covered_windows(self) -> frozenset[int] | None: ...
    def toggle(self) -> bool:
        """Return False only when shell readiness prevented dispatch."""
        ...


class WindowsDesktopAPI:
    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        self._shell = bind(user, "GetShellWindow", ctypes.c_void_p)
        self._visible = bind(user, "IsWindowVisible", ctypes.c_int32, ctypes.c_void_p)
        self._next = bind(
            user, "GetWindow", ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
        )
        self._style = bind(
            user, "GetWindowLongPtrW", ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int
        )
        self._class = bind(
            user,
            "GetClassNameW",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int32,
        )
        self._owner = bind(
            user,
            "GetWindowThreadProcessId",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self._allow = bind(
            user, "AllowSetForegroundWindow", ctypes.c_int32, ctypes.c_uint32
        )
        self._cloaked = bind(
            ctypes.WinDLL("dwmapi", use_last_error=True),
            "DwmGetWindowAttribute",
            HRESULT,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )

    def _pid(self, window: int) -> int:
        pid = ctypes.c_uint32()
        self._owner(window, ctypes.byref(pid))
        return int(pid.value)

    def covered_windows(self) -> frozenset[int] | None:
        desktop = self._shell()
        if not desktop or not self._visible(desktop):
            return None
        owner = self._pid(desktop)
        if not owner:
            return None
        result: set[int] = set()
        seen = {int(desktop)}
        window = self._next(desktop, 2)  # GW_HWNDNEXT, below the desktop in Z order.
        while window:
            if window in seen or len(seen) >= 4096:
                return None
            seen.add(window)
            # Hidden native surfaces still participate in Z order. In
            # particular, the dock's AppBar proxy keeps an empty desktop's
            # raised/lowered position observable in non-reserving modes.
            if not self._style(window, -20) & 8:
                name = ctypes.create_unicode_buffer(256)
                self._class(window, name, len(name))
                cloaked = ctypes.c_uint32()
                status = self._cloaked(window, 14, ctypes.byref(cloaked), 4)
                if (
                    name.value
                    not in {
                        "Progman",
                        "WorkerW",
                        "Shell_TrayWnd",
                        "Shell_SecondaryTrayWnd",
                    }
                    and status >= 0
                    and not cloaked.value
                ):
                    result.add(int(window))
            window = self._next(window, 2)
        if self._shell() != desktop or self._pid(desktop) != owner:
            return None
        return frozenset(result)

    def _registered(self, ole, desktop: int) -> bool:
        windows, found = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            create = bind(
                ole,
                "CoCreateInstance",
                HRESULT,
                ctypes.POINTER(GUID),
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )
            status = create(
                ctypes.byref(_WINDOWS_CLASS),
                None,
                4,
                ctypes.byref(_WINDOWS_INTERFACE),
                ctypes.byref(windows),
            )
            if status < 0 or not windows.value:
                raise OSError(f"Shell windows unavailable: 0x{status & 0xFFFFFFFF:08x}")
            location, empty, window = _Variant(), _Variant(), ctypes.c_int32()
            location.vt = 3  # VT_I4, CSIDL_DESKTOP = 0.
            # IShellWindows::FindWindowSW, SWC_DESKTOP, SWFO_NEEDDISPATCH.
            status = method(
                windows,
                15,
                HRESULT,
                ctypes.POINTER(_Variant),
                ctypes.POINTER(_Variant),
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_int32),
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_void_p),
            )(
                windows,
                ctypes.byref(location),
                ctypes.byref(empty),
                8,
                ctypes.byref(window),
                1,
                ctypes.byref(found),
            )
            if status < 0:
                raise OSError(f"Desktop lookup failed: 0x{status & 0xFFFFFFFF:08x}")
            return bool(
                status == 0
                and bool(found.value)
                and window.value & 0xFFFFFFFF == desktop & 0xFFFFFFFF
            )
        finally:
            release(found)
            release(windows)

    def toggle(self) -> bool:
        desktop = self._shell()
        pid = self._pid(desktop) if desktop else 0
        if not pid:
            raise OSError("Windows desktop shell is unavailable")
        with apartment() as ole:
            # A new shell HWND can exist before Explorer registers its desktop.
            # ToggleDesktop can silently do nothing during that interval.
            if not self._registered(ole, desktop):
                return False
            pointer = ctypes.c_void_p()
            try:
                create = bind(
                    ole,
                    "CoCreateInstance",
                    HRESULT,
                    ctypes.POINTER(GUID),
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                )
                status = create(
                    ctypes.byref(_SHELL_CLASS),
                    None,
                    5,
                    ctypes.byref(_SHELL_INTERFACE),
                    ctypes.byref(pointer),
                )
                if status < 0 or not pointer.value:
                    raise OSError(
                        f"Shell activation failed: 0x{status & 0xFFFFFFFF:08x}"
                    )
                if self._shell() != desktop or self._pid(desktop) != pid:
                    raise OSError("Windows desktop shell changed before the action")
                self._allow(pid)
                # IShellDispatch4::ToggleDesktop, SDK ShlDisp.h vtable slot 41.
                status = method(pointer, 41, HRESULT)(pointer)
                if status < 0:
                    raise OSError(f"Show Desktop failed: 0x{status & 0xFFFFFFFF:08x}")
                return True
            finally:
                release(pointer)


class WindowsDesktopActionService(DesktopActionService):
    """Serialize shell actions off GTK; OK means the request was accepted.

    Explorer processes ToggleDesktop asynchronously. Waiting for each transition
    before the next request keeps repeated explicit show/hide calls idempotent.
    The worker owns each COM apartment and releases its interface per operation.
    """

    def __init__(self, *, api: DesktopAPI | None = None) -> None:
        self._api = api
        self._stopped = False
        self._generation = 0
        self._condition = threading.Condition()
        self._requests: deque[tuple[int, bool | None]] = deque()
        self._thread: threading.Thread | None = None
        self._busy = False
        self._last_result: ActionResult | None = None

    @property
    def pending(self) -> bool:
        with self._condition:
            return self._busy or bool(self._requests)

    @property
    def last_result(self) -> ActionResult | None:
        with self._condition:
            return self._last_result

    def start(self) -> None:
        with self._condition:
            self._generation += 1
            self._stopped = False
            self._last_result = None
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._generation += 1
            self._stopped = True
            self._requests.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        with self._condition:
            if self._stopped:
                return ActionResult.NOT_FOUND
            if len(self._requests) >= 64:
                return ActionResult.FAILED
            try:
                if self._api is None:
                    self._api = WindowsDesktopAPI()
            except OSError as exc:
                log.debug("Desktop interface unavailable: %s", exc)
                return ActionResult.FAILED
            self._requests.append((self._generation, show))
            self._last_result = None
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="windows-desktop", daemon=True
                )
                self._thread.start()
            self._condition.notify_all()
            return ActionResult.OK

    def _run(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stopped or self._requests)
                    if self._stopped:
                        self._thread = None
                        return
                    generation, show = self._requests.popleft()
                    self._busy = True
                try:
                    result = self._perform(generation, show)
                except Exception:
                    log.exception("Desktop request failed unexpectedly")
                    result = ActionResult.FAILED
                with self._condition:
                    self._busy = False
                    if not self._stopped and generation == self._generation:
                        self._last_result = result
                    self._condition.notify_all()
        finally:
            with self._condition:
                if self._thread is current:
                    self._busy = False
                    self._thread = None
                self._condition.notify_all()

    def _perform(self, generation: int, show: bool | None) -> ActionResult:
        api = self._api
        if api is None:
            return ActionResult.NOT_FOUND
        try:
            # Explorer's taskbar can reappear before its desktop is registered.
            # Keep the accepted request off GTK while that shell recovers.
            deadline = time.monotonic() + _SHELL_READY_SECONDS
            target = show
            while True:
                current = api.covered_windows()
                with self._condition:
                    if self._stopped or generation != self._generation:
                        return ActionResult.NOT_FOUND
                if current is not None:
                    if target is None:
                        target = not bool(current)
                    if bool(current) == target:
                        return ActionResult.OK
                    if api.toggle():
                        break
                with self._condition:
                    if self._stopped or generation != self._generation:
                        return ActionResult.NOT_FOUND
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        log.debug("Windows desktop did not become ready")
                        return ActionResult.NOT_FOUND
                    self._condition.wait(min(0.05, remaining))
            deadline = time.monotonic() + 1.5
            while True:
                with self._condition:
                    if self._stopped or generation != self._generation:
                        return ActionResult.NOT_FOUND
                state = api.covered_windows()
                if state is not None and bool(state) == target:
                    return ActionResult.OK
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.debug(
                        "Show Desktop transition was not observed before its deadline"
                    )
                    return ActionResult.FAILED
                with self._condition:
                    self._condition.wait(min(0.01, remaining))
        except OSError as exc:
            log.debug("Show Desktop failed: %s", exc)
            return ActionResult.FAILED
