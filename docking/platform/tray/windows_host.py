"""Owned native notification receiver, with bounded forwarding to Explorer.

The receiver is a hidden topmost Shell_TrayWnd and TrayNotifyWnd pair. Explorer
remains topmost and receives the original shell messages, including AppBar and
notification operations. Native resources and the message pump belong to one
thread. AppBar output uses the shell protocol's explicitly shared reply blocks.
"""

from __future__ import annotations

import ctypes as c
import os
import threading
from collections.abc import Callable
from ctypes import wintypes

from docking.log import get_logger
from docking.platform.backends.base import Rect
from docking.platform.backends.windows.ffi import bind, explorer_tray
from docking.platform.tray.state import TrayIconPixmap
from docking.platform.tray.windows_appbar import WindowsAppBarBridge
from docking.platform.tray.windows_icons import icon_pixels
from docking.platform.tray.windows_messages import (
    IconQuery,
    NotifyMessage,
    decode_icon_query,
    decode_message,
    icon_query_reply,
)

log = get_logger("tray.windows_host")
_P, _U, _W, _L = c.c_void_p, c.c_uint32, c.c_size_t, c.c_ssize_t


class _CopyData(c.Structure):
    _fields_ = [("kind", _W), ("size", _U), ("data", _P)]


class WindowsTrayHost:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._error: Exception | None = None
        self._window = 0
        self._explorer = 0
        self._user = None
        self._on_message: Callable[[NotifyMessage], bool] | None = None
        self._appbar = WindowsAppBarBridge()
        self._icon_bounds: Callable[[IconQuery], Rect | None] | None = None
        self._notify_window = 0
        self._mirror_rect: tuple[int, int, int, int] | None = None
        self._announced_explorer: tuple[int, int] | None = None

    def start(
        self,
        on_message: Callable[[NotifyMessage], bool],
        icon_bounds: Callable[[IconQuery], Rect | None] | None = None,
    ) -> None:
        with self._start_lock:
            if self._stopped.is_set():
                raise OSError("Notification host is stopped")
            if self._thread is None:
                self._on_message = on_message
                self._icon_bounds = icon_bounds
                self._thread = threading.Thread(
                    target=self._run, name="windows-tray", daemon=True
                )
                self._thread.start()
        if not self._ready.wait(4):
            raise OSError("Notification host startup deadline expired")
        if self._error is not None:
            raise OSError("Notification host failed") from self._error
        if self._thread is None or not self._thread.is_alive():
            raise OSError("Notification host exited")

    def owner(self, window: int) -> int:
        if self._user is None or not window:
            return 0
        pid = _U()
        bind(self._user, "GetWindowThreadProcessId", _U, _P, c.POINTER(_U))(
            window, c.byref(pid)
        )
        return pid.value

    def pixels(self, icon: int) -> TrayIconPixmap | None:
        return icon_pixels(icon)

    def point(self) -> tuple[int, int]:
        if self._user is None:
            return 0, 0
        point = wintypes.POINT()
        if not bind(self._user, "GetCursorPos", c.c_int32, c.POINTER(wintypes.POINT))(
            c.byref(point)
        ):
            raise c.WinError(c.get_last_error())
        return int(point.x), int(point.y)

    def post(self, window: int, message: int, first: int, second: int) -> bool:
        return self._user is not None and bool(
            bind(self._user, "PostMessageW", c.c_int32, _P, _U, _W, _L)(
                window, message, first, second
            )
        )

    def authorize(self, pid: int) -> None:
        if self._user is not None:
            bind(self._user, "AllowSetForegroundWindow", c.c_int32, _U)(pid)

    def close(self) -> None:
        self._stopped.set()
        thread = self._thread
        if thread is None:
            return
        if self._window and self.owner(self._window) == os.getpid():
            self.post(self._window, 0x10, 0, 0)
        if thread is not threading.current_thread():
            thread.join(3)
            if thread.is_alive():
                raise OSError("Notification host did not close within its deadline")

    def _find_explorer(self) -> int:
        if self._user is None:
            return 0
        identity = explorer_tray(self._user, exclude_pid=os.getpid())
        return identity[0] if identity else 0

    def _arrange(self) -> None:
        user = self._user
        if user is None:
            return
        self._mirror_explorer()
        first = bind(user, "FindWindowW", _P, c.c_wchar_p, c.c_wchar_p)(
            "Shell_TrayWnd", ""
        )
        if not self._explorer or first == self._window:
            return
        # Both windows are topmost. Positioning Explorer just after this
        # hidden receiver preserves its style and visible taskbar behavior.
        if not bind(
            user,
            "SetWindowPos",
            c.c_int32,
            _P,
            _P,
            c.c_int,
            c.c_int,
            c.c_int,
            c.c_int,
            _U,
        )(self._explorer, self._window, 0, 0, 0, 0, 0x13):
            raise c.WinError(c.get_last_error())

    def _mirror_explorer(self) -> None:
        if self._user is None or not self._window or not self._explorer:
            return
        rect = wintypes.RECT()
        if not bind(
            self._user, "GetWindowRect", c.c_int32, _P, c.POINTER(wintypes.RECT)
        )(self._explorer, c.byref(rect)):
            return
        bounds = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        if bounds == self._mirror_rect or bounds[2] <= 0 or bounds[3] <= 0:
            return
        position = bind(
            self._user,
            "SetWindowPos",
            c.c_int32,
            _P,
            _P,
            c.c_int,
            c.c_int,
            c.c_int,
            c.c_int,
            _U,
        )
        # Preserve routing and activation while matching Explorer's placement.
        # Explorer's fallback icon queries inspect the first Shell_TrayWnd.
        if not position(self._window, None, *bounds, 0x14):
            raise c.WinError(c.get_last_error())
        if self._notify_window and not position(
            self._notify_window, None, 0, 0, bounds[2], bounds[3], 0x14
        ):
            raise c.WinError(c.get_last_error())
        self._mirror_rect = bounds

    def _announce(self) -> None:
        user = self._user
        if user is not None:
            message = bind(user, "RegisterWindowMessageW", _U, c.c_wchar_p)(
                "TaskbarCreated"
            )
            if message:
                bind(user, "SendNotifyMessageW", c.c_int32, _P, _U, _W, _L)(
                    0xFFFF, message, 0, 0
                )

    def _seed_changed_explorer(self) -> None:
        owner = self.owner(self._explorer)
        identity = (self._explorer, owner)
        if not all(identity) or identity == self._announced_explorer:
            return
        if self._integrity(os.getpid()) != self._integrity(owner):
            raise OSError("Replacement Explorer has a different integrity level")
        # Record before broadcasting: clients and our own windows can respond
        # synchronously. Reseed once after replacement routing is established.
        self._announced_explorer = identity
        self._announce()

    def _forward(
        self,
        window: int,
        message: int,
        first: int,
        second: int,
        *,
        handled: bool = False,
    ) -> int:
        user = self._user
        if user is None:
            return 0
        if not self.owner(self._explorer):
            self._explorer = self._find_explorer()
        if not self._explorer:
            return int(handled)
        if message == 0x4A and second:
            data = c.cast(second, c.POINTER(_CopyData)).contents
            if data.kind == 0 and data.data and data.size == 64:

                def send(payload: bytes) -> int:
                    buffer = c.create_string_buffer(payload)
                    packet = _CopyData(0, len(payload), c.cast(buffer, _P))
                    return self._deliver(message, first, c.addressof(packet), handled)

                result = self._appbar.forward(
                    c.string_at(data.data, data.size), self.owner(self._explorer), send
                )
                if result is not None:
                    return result
        return self._deliver(message, first, second, handled)

    def _deliver(self, message: int, first: int, second: int, handled: bool) -> int:
        user = self._user
        if user is None or not self._explorer:
            return int(handled)
        if message == 0x400 + 372:
            self.post(self._explorer, message, first, second)
            return 0
        result = _W()
        forwarded = bind(
            user, "SendMessageTimeoutW", _L, _P, _U, _W, _L, _U, _U, c.POINTER(_W)
        )(self._explorer, message, first, second, 2, 1500, c.byref(result))
        return result.value if forwarded else int(handled)

    def _run(self) -> None:
        mutex, acquired = None, False
        registered: list[str] = []
        instance, child = None, None
        user = kernel = None
        try:
            user = self._user = c.WinDLL("user32", use_last_error=True)
            kernel = c.WinDLL("kernel32", use_last_error=True)
            mutex = bind(kernel, "CreateMutexW", _P, _P, c.c_int32, c.c_wchar_p)(
                None, False, r"Local\Docking.NotificationArea"
            )
            if not mutex:
                raise c.WinError(c.get_last_error())
            if bind(kernel, "WaitForSingleObject", _U, _P, _U)(mutex, 0) not in (
                0,
                0x80,
            ):
                raise OSError("Another Docking notification host is already running")
            acquired = True
            self._explorer = self._find_explorer()
            if not self._explorer:
                raise OSError("Windows Explorer notification area is unavailable")
            if self._integrity(os.getpid()) != self._integrity(
                self.owner(self._explorer)
            ):
                raise OSError(
                    "Notification host must run at Explorer's integrity level"
                )
            callback_type = c.WINFUNCTYPE(_L, _P, _U, _W, _L)
            default = bind(user, "DefWindowProcW", _L, _P, _U, _W, _L)

            def dispatch(window, message, first, second):
                try:
                    if message == 0x4A and second:
                        data = c.cast(second, c.POINTER(_CopyData)).contents
                        handled = False
                        if data.kind == 3 and data.data and data.size == 40:
                            query = decode_icon_query(c.string_at(data.data, data.size))
                            if query is not None and self._icon_bounds is not None:
                                bounds = self._icon_bounds(query)
                                if bounds is not None:
                                    reply = icon_query_reply(query, bounds)
                                    if reply is not None:
                                        return reply
                        if data.kind == 1 and data.data and 160 <= data.size <= 8192:
                            try:
                                decoded = decode_message(
                                    c.string_at(data.data, data.size)
                                )
                                if decoded is not None and self._on_message is not None:
                                    handled = self._on_message(decoded)
                            except Exception as exc:
                                log.debug("Notification projection failed: %s", exc)
                        forwarded = self._forward(
                            window, message, first, second, handled=handled
                        )
                        # Explorer may reject a duplicate NIM_ADD while an
                        # existing app re-registers after TaskbarCreated. This
                        # host accepted it and owns the displayed item.
                        return 1 if handled else forwarded
                    if message == 0x10:
                        bind(user, "DestroyWindow", c.c_int32, _P)(window)
                        return 0
                    if message == 2 and window == self._window:
                        bind(user, "PostQuitMessage", None, c.c_int)(0)
                        return 0
                    if message == 0x113:
                        if self._stopped.is_set():
                            self.post(self._window, 0x10, 0, 0)
                        else:
                            self._explorer = self._find_explorer()
                            self._arrange()
                            self._seed_changed_explorer()
                        return 0
                    if message in (0x1C, 0x111) or message >= 0x400:
                        return self._forward(window, message, first, second)
                except Exception as exc:
                    log.debug("Notification window message failed: %s", exc)
                return default(window, message, first, second)

            callback = callback_type(dispatch)

            class WindowClass(c.Structure):
                _fields_ = [
                    ("size", _U),
                    ("style", _U),
                    ("callback", callback_type),
                    ("class_extra", c.c_int),
                    ("window_extra", c.c_int),
                    ("instance", _P),
                    ("icon", _P),
                    ("cursor", _P),
                    ("background", _P),
                    ("menu", c.c_wchar_p),
                    ("name", c.c_wchar_p),
                    ("small", _P),
                ]

            instance = bind(kernel, "GetModuleHandleW", _P, c.c_wchar_p)(None)
            for name in ("Shell_TrayWnd", "TrayNotifyWnd"):
                descriptor = WindowClass(
                    size=c.sizeof(WindowClass),
                    style=8,
                    callback=callback,
                    instance=instance,
                    name=name,
                )
                if not bind(
                    user, "RegisterClassExW", c.c_uint16, c.POINTER(WindowClass)
                )(c.byref(descriptor)):
                    raise c.WinError(c.get_last_error())
                registered.append(name)
            create = bind(
                user,
                "CreateWindowExW",
                _P,
                _U,
                c.c_wchar_p,
                c.c_wchar_p,
                _U,
                c.c_int,
                c.c_int,
                c.c_int,
                c.c_int,
                _P,
                _P,
                _P,
                _P,
            )
            width = bind(user, "GetSystemMetrics", c.c_int, c.c_int)(0)
            self._window = create(
                0x08000088,
                "Shell_TrayWnd",
                "",
                0x86000000,
                0,
                0,
                width,
                24,
                None,
                None,
                instance,
                None,
            )
            if not self._window:
                raise c.WinError(c.get_last_error())
            child = create(
                0,
                "TrayNotifyWnd",
                "",
                0x46000000,
                0,
                0,
                width,
                24,
                self._window,
                None,
                instance,
                None,
            )
            if not child:
                raise c.WinError(c.get_last_error())
            self._notify_window = int(child)
            self._arrange()
            if not bind(user, "SetTimer", _W, _P, _W, _U, _P)(
                self._window, 1, 500, None
            ):
                raise c.WinError(c.get_last_error())
            self._ready.set()
            self._seed_changed_explorer()
            message = wintypes.MSG()
            while True:
                status = bind(
                    user, "GetMessageW", c.c_int32, c.POINTER(wintypes.MSG), _P, _U, _U
                )(c.byref(message), None, 0, 0)
                if status == -1:
                    raise c.WinError(c.get_last_error())
                if status == 0:
                    break
                bind(user, "TranslateMessage", c.c_int32, c.POINTER(wintypes.MSG))(
                    c.byref(message)
                )
                bind(user, "DispatchMessageW", _L, c.POINTER(wintypes.MSG))(
                    c.byref(message)
                )
        except Exception as exc:
            self._error = exc
            log.debug("Notification host failed: %s", exc)
        finally:
            if user is not None:
                for window in (child, self._window):
                    if window and self.owner(window) == os.getpid():
                        bind(user, "DestroyWindow", c.c_int32, _P)(window)
                self._window = 0
                self._notify_window = 0
                self._mirror_rect = None
                for name in reversed(registered):
                    bind(user, "UnregisterClassW", c.c_int32, c.c_wchar_p, _P)(
                        name, instance
                    )
                # Explorer received each registration too. Destroying this
                # proxy hands routing back without a synthetic shell restart.
            if mutex and kernel is not None:
                if acquired:
                    bind(kernel, "ReleaseMutex", c.c_int32, _P)(mutex)
                bind(kernel, "CloseHandle", c.c_int32, _P)(mutex)
            self._ready.set()

    @staticmethod
    def _integrity(pid: int) -> int:
        kernel = c.WinDLL("kernel32", use_last_error=True)
        advapi = c.WinDLL("advapi32", use_last_error=True)
        process = bind(kernel, "OpenProcess", _P, _U, c.c_int32, _U)(0x1000, False, pid)
        if not process:
            raise c.WinError(c.get_last_error())
        token = _P()
        try:
            if not bind(advapi, "OpenProcessToken", c.c_int32, _P, _U, c.POINTER(_P))(
                process, 8, c.byref(token)
            ):
                raise c.WinError(c.get_last_error())
            required = _U()
            query = bind(
                advapi, "GetTokenInformation", c.c_int32, _P, _U, _P, _U, c.POINTER(_U)
            )
            query(token, 25, None, 0, c.byref(required))
            if not c.sizeof(_P) + 4 <= required.value <= 4096:
                raise OSError("Invalid notification host integrity token")
            buffer = c.create_string_buffer(required.value)
            if not query(token, 25, buffer, len(buffer), c.byref(required)):
                raise c.WinError(c.get_last_error())
            sid = c.cast(buffer, c.POINTER(_P))[0]
            count = bind(advapi, "GetSidSubAuthorityCount", c.POINTER(c.c_ubyte), _P)(
                sid
            )
            if not count or not 1 <= count[0] <= 15:
                raise OSError("Invalid notification host integrity SID")
            authority = bind(advapi, "GetSidSubAuthority", c.POINTER(_U), _P, _U)(
                sid, count[0] - 1
            )
            if not authority:
                raise OSError("Missing notification host integrity level")
            return int(authority[0])
        finally:
            if token.value:
                bind(kernel, "CloseHandle", c.c_int32, _P)(token)
            bind(kernel, "CloseHandle", c.c_int32, _P)(process)
