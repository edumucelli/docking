"""Native Windows session actions and hardware/privilege availability."""

from __future__ import annotations

import ctypes
import threading
from contextlib import contextmanager

from docking.applets.session.backend import SessionBackend
from docking.log import get_logger
from docking.platform.native.windows.ffi import bind

log = get_logger("session.windows")
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p
_BYTE = ctypes.c_ubyte
_REASON = 0x80040000  # Planned application request, other reason.
_EXIT_FLAGS = {"logout": 0, "reboot": 2, "poweroff": 8}
_PRIVILEGE_LOCK = threading.Lock()


class _Luid(ctypes.Structure):
    _fields_ = [("low", _DWORD), ("high", ctypes.c_int32)]


class _Privilege(ctypes.Structure):
    _fields_ = [("luid", _Luid), ("attributes", _DWORD)]


class _TokenPrivileges(ctypes.Structure):
    _fields_ = [("count", _DWORD), ("privileges", _Privilege * 1)]


class _PowerCapabilities(ctypes.Structure):
    """Windows 10+ SYSTEM_POWER_CAPABILITIES, 76 bytes on both x86 and x64."""

    _fields_ = [
        (name, _BYTE)
        for name in (
            "power_button",
            "sleep_button",
            "lid",
            "s1",
            "s2",
            "s3",
            "s4",
            "s5",
            "hiber_file",
            "full_wake",
            "video_dim",
            "apm",
            "ups",
            "thermal",
            "throttle",
            "min_throttle",
            "max_throttle",
            "fast_s4",
            "hiberboot",
            "wake_alarm",
            "aoac",
            "disk_spin_down",
            "hiber_type",
            "aoac_connectivity",
        )
    ] + [
        ("spare", _BYTE * 6),
        ("batteries", _BYTE),
        ("short_term_batteries", _BYTE),
        ("battery_scale", _DWORD * 6),
        ("wake_states", ctypes.c_int32 * 5),
    ]


def _check(result, action: str) -> None:
    if not result:
        raise OSError(ctypes.get_last_error(), action + " failed")


class _PowerAPI:
    def __init__(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        power = ctypes.WinDLL("powrprof", use_last_error=True)
        security = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._lock = bind(user, "LockWorkStation", _BOOL)
        self._exit = bind(user, "ExitWindowsEx", _BOOL, _DWORD, _DWORD)
        self._suspend = bind(power, "SetSuspendState", _BYTE, _BYTE, _BYTE, _BYTE)
        self._caps = bind(
            power, "GetPwrCapabilities", _BYTE, ctypes.POINTER(_PowerCapabilities)
        )
        self._process = bind(kernel, "GetCurrentProcess", _HANDLE)
        self._close = bind(kernel, "CloseHandle", _BOOL, _HANDLE)
        self._open_token = bind(
            security,
            "OpenProcessToken",
            _BOOL,
            _HANDLE,
            _DWORD,
            ctypes.POINTER(_HANDLE),
        )
        self._lookup = bind(
            security,
            "LookupPrivilegeValueW",
            _BOOL,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.POINTER(_Luid),
        )
        self._token_info = bind(
            security,
            "GetTokenInformation",
            _BOOL,
            _HANDLE,
            ctypes.c_int32,
            ctypes.c_void_p,
            _DWORD,
            ctypes.POINTER(_DWORD),
        )
        self._adjust = bind(
            security,
            "AdjustTokenPrivileges",
            _BOOL,
            _HANDLE,
            _BOOL,
            ctypes.POINTER(_TokenPrivileges),
            _DWORD,
            ctypes.POINTER(_TokenPrivileges),
            ctypes.POINTER(_DWORD),
        )

    @contextmanager
    def _token(self, access: int):
        handle = _HANDLE()
        _check(
            self._open_token(self._process(), access, ctypes.byref(handle)),
            "OpenProcessToken",
        )
        try:
            yield handle
        finally:
            self._close(handle)

    def _shutdown_luid(self) -> _Luid:
        luid = _Luid()
        _check(
            self._lookup(None, "SeShutdownPrivilege", ctypes.byref(luid)),
            "LookupPrivilegeValueW",
        )
        return luid

    def _can_shutdown(self) -> bool:
        wanted = self._shutdown_luid()
        with self._token(0x8) as token:  # TOKEN_QUERY
            size = _DWORD()
            if (
                not self._token_info(token, 3, None, 0, ctypes.byref(size))
                and ctypes.get_last_error() != 122  # ERROR_INSUFFICIENT_BUFFER
            ):
                _check(False, "GetTokenInformation")
            for _ in range(3):
                if not 4 <= size.value <= 65536:
                    raise OSError("Invalid token privilege buffer size")
                capacity = size.value
                buffer = ctypes.create_string_buffer(capacity)
                if not self._token_info(token, 3, buffer, capacity, ctypes.byref(size)):
                    if ctypes.get_last_error() == 122:
                        continue
                    _check(False, "GetTokenInformation")
                count = _DWORD.from_buffer_copy(buffer).value
                offset = _TokenPrivileges.privileges.offset
                if offset + count * ctypes.sizeof(_Privilege) > capacity:
                    raise OSError("Token privilege count exceeds its buffer")
                for index in range(count):
                    item = _Privilege.from_buffer_copy(
                        buffer, offset + index * ctypes.sizeof(_Privilege)
                    )
                    if (item.luid.low, item.luid.high) == (wanted.low, wanted.high):
                        return not bool(item.attributes & 4)  # SE_PRIVILEGE_REMOVED
                return False
        raise OSError("Token privileges kept changing")

    @contextmanager
    def _shutdown_privilege(self):
        # Enable an existing right only for the request, then restore its exact
        # previous state. This cannot grant a right absent from the user's token.
        with _PRIVILEGE_LOCK, self._token(0x28) as token:
            request = _TokenPrivileges(count=1)
            request.privileges[0] = _Privilege(self._shutdown_luid(), 2)
            previous, size = _TokenPrivileges(), _DWORD()
            ctypes.set_last_error(0)
            _check(
                self._adjust(
                    token,
                    False,
                    ctypes.byref(request),
                    ctypes.sizeof(previous),
                    ctypes.byref(previous),
                    ctypes.byref(size),
                ),
                "AdjustTokenPrivileges",
            )
            error = ctypes.get_last_error()
            try:
                if error:
                    raise OSError(error, "Shutdown privilege unavailable")
                yield
            finally:
                if previous.count:
                    ctypes.set_last_error(0)
                    restored = self._adjust(
                        token, False, ctypes.byref(previous), 0, None, None
                    )
                    error = ctypes.get_last_error()
                    if not restored or error:
                        log.warning("Could not restore shutdown privilege: %s", error)

    def available_actions(self) -> frozenset[str]:
        actions = {"lock", "logout"}
        try:
            if not self._can_shutdown():
                return frozenset(actions)
            actions.add("reboot")
            caps = _PowerCapabilities()
            _check(self._caps(ctypes.byref(caps)), "GetPwrCapabilities")
            if caps.s1 or caps.s2 or caps.s3 or caps.aoac:
                actions.add("suspend")
            if caps.s5:
                actions.add("poweroff")
        except OSError as exc:
            log.debug("Power actions unavailable: %s", exc)
        return frozenset(actions)

    def perform(self, action: str) -> None:
        if action == "lock":
            _check(self._lock(), "LockWorkStation")
        elif action == "logout":
            _check(self._exit(0, _REASON), "ExitWindowsEx")
        elif action in ("suspend", "reboot", "poweroff"):
            with self._shutdown_privilege():
                if action == "suspend":
                    _check(self._suspend(False, False, False), "SetSuspendState")
                else:
                    # Never force applications closed or suppress their vetoes.
                    _check(self._exit(_EXIT_FLAGS[action], _REASON), "ExitWindowsEx")
        else:
            raise ValueError("Unknown session action")


class WindowsSessionBackend(SessionBackend):
    def __init__(self, *, api: _PowerAPI | None = None):
        self._api = api
        self._worker: threading.Thread | None = None
        self._stopped = False

    def _native(self) -> _PowerAPI:
        if self._api is None:
            self._api = _PowerAPI()
        return self._api

    def available_actions(self) -> frozenset[str]:
        if self._stopped or (self._worker is not None and self._worker.is_alive()):
            return frozenset[str]()
        try:
            return self._native().available_actions()
        except OSError as exc:
            log.debug("Session actions unavailable: %s", exc)
            return frozenset[str]()

    def perform(self, action: str) -> bool:
        if action not in self.available_actions():
            return False

        def request():
            try:
                api = self._native()
                if action in api.available_actions():
                    api.perform(action)
            except OSError as exc:
                log.warning("Could not request %s: %s", action, exc)

        self._worker = threading.Thread(
            target=request, name="windows-session-action", daemon=True
        )
        try:
            self._worker.start()
            return True
        except RuntimeError as exc:
            self._worker = None
            log.warning("Could not start session action: %s", exc)
            return False

    def stop(self) -> None:
        self._stopped = True

        # A suspend call may return only after resume. It owns no GTK callback
        # or persistent native handle; an accepted OS action is not cancelled.
