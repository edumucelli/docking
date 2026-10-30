"""Per-player Core Audio volume, preserving endpoint master and mute state."""

from __future__ import annotations

import ctypes
import hashlib
import math
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import PureWindowsPath

from docking.log import get_logger
from docking.platform.applications.windows.process import WindowsProcessBackend
from docking.platform.backends.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    object_result,
    query,
    release,
    value,
)
from docking.platform.backends.windows.ffi import bind
from docking.platform.backends.windows.winrt import check

log = get_logger("music.windows_audio")
_POINTER = ctypes.c_void_p
_ENUMERATOR_CLASS = GUID.parse("bcde0395-e52f-467c-8e3d-c4579291692e")
_ENUMERATOR_INTERFACE = GUID.parse("a95664d2-9614-4f35-a746-de8db63617e6")
_SESSION_MANAGER = GUID.parse("77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f")
_SESSION_CONTROL = GUID.parse("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
_SIMPLE_VOLUME = GUID.parse("87ce5498-68d6-44e5-9215-6da47ef883d8")


@dataclass(frozen=True)
class PlayerVolume:
    identifier: str
    percent: int


@dataclass(frozen=True)
class _Session:
    key: str
    pid: int
    level: float
    volume: ctypes.c_void_p


def _text(ole, pointer, index):
    result = _POINTER()
    try:
        check(
            method(pointer, index, HRESULT, ctypes.POINTER(_POINTER))(
                pointer, ctypes.byref(result)
            )
        )
        return ctypes.wstring_at(result) if result.value else ""
    finally:
        if result.value:
            bind(ole, "CoTaskMemFree", None, _POINTER)(result)


def _identity(rows: tuple[_Session, ...]) -> str:
    return hashlib.sha256(
        "\0".join(sorted(row.key for row in rows)).encode()
    ).hexdigest()


class WindowsPlayerAudio:
    def __init__(self) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._open = bind(
            kernel,
            "OpenProcess",
            _POINTER,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_uint32,
        )
        self._close = bind(kernel, "CloseHandle", ctypes.c_int32, _POINTER)
        self._app_id = bind(
            kernel,
            "GetApplicationUserModelId",
            ctypes.c_int32,
            _POINTER,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_wchar_p,
        )
        self._processes = WindowsProcessBackend()

    def _process_identity(self, pid: int) -> tuple[str, str]:
        process = self._open(0x1000, False, pid)
        application = ""
        if process:
            try:
                count = ctypes.c_uint32()
                if (
                    self._app_id(process, ctypes.byref(count), None) == 122
                    and 0 < count.value <= 4096
                ):
                    buffer = ctypes.create_unicode_buffer(count.value)
                    if self._app_id(process, ctypes.byref(count), buffer) == 0:
                        application = str(buffer.value)
            finally:
                self._close(process)
        executable = self._processes.executable(pid)
        return application, str(executable) if executable is not None else ""

    @contextmanager
    def _scan(self):
        with apartment(multithreaded=True) as ole, ExitStack() as scope:
            enumerator = _POINTER()
            status = bind(
                ole,
                "CoCreateInstance",
                HRESULT,
                ctypes.POINTER(GUID),
                _POINTER,
                ctypes.c_uint32,
                ctypes.POINTER(GUID),
                ctypes.POINTER(_POINTER),
            )(
                ctypes.byref(_ENUMERATOR_CLASS),
                None,
                1,
                ctypes.byref(_ENUMERATOR_INTERFACE),
                ctypes.byref(enumerator),
            )
            if enumerator.value:
                scope.callback(release, enumerator)
            check(status)
            devices = object_result(
                scope, enumerator, 3, ctypes.c_int32, 0, ctypes.c_uint32, 1
            )
            count = value(devices, 3, ctypes.c_uint32).value
            if count > 64:
                raise OSError("Audio endpoint count exceeded its limit")
            result = []
            for index in range(count):
                device = object_result(scope, devices, 4, ctypes.c_uint32, index)
                endpoint = _text(ole, device, 5)
                manager = object_result(
                    scope,
                    device,
                    3,
                    ctypes.POINTER(GUID),
                    ctypes.byref(_SESSION_MANAGER),
                    ctypes.c_uint32,
                    1,
                    _POINTER,
                    None,
                )
                sessions = object_result(scope, manager, 5)
                total = value(sessions, 3, ctypes.c_int32).value
                if not 0 <= total <= 1024 or len(result) + total > 2048:
                    raise OSError("Audio session count exceeded its limit")
                for number in range(total):
                    control = object_result(scope, sessions, 4, ctypes.c_int32, number)
                    if value(control, 3, ctypes.c_int32).value not in (0, 1):
                        continue
                    extended = query(scope, control, _SESSION_CONTROL)
                    pid = ctypes.c_uint32()
                    status = method(
                        extended, 14, HRESULT, ctypes.POINTER(ctypes.c_uint32)
                    )(extended, ctypes.byref(pid))
                    # A cross-process session reports its creator. Match that
                    # live application's identity and control the whole group.
                    if status not in (0, 0x0889000D) or not pid.value:
                        continue
                    instance = _text(ole, extended, 13)
                    if not instance:
                        continue
                    volume = query(scope, control, _SIMPLE_VOLUME)
                    level = value(volume, 4, ctypes.c_float).value
                    if not math.isfinite(level) or not 0 <= level <= 1:
                        continue
                    result.append(
                        _Session(endpoint + "\0" + instance, pid.value, level, volume)
                    )
            yield tuple(result)

    def _match(self, source: str, rows: tuple[_Session, ...]) -> tuple[_Session, ...]:
        if not rows:
            return ()
        identities = {
            pid: self._process_identity(pid) for pid in {row.pid for row in rows}
        }
        source = source.casefold()
        exact, names = set(), set()
        paths = set()
        for pid, (application, executable) in identities.items():
            path = PureWindowsPath(executable)
            if source and source in {application.casefold(), executable.casefold()}:
                exact.add(pid)
            elif executable and source in {path.name.casefold(), path.stem.casefold()}:
                names.add(pid)
                paths.add(executable.casefold())
        if not exact and len(paths) > 1:
            return ()
        matches = exact or names
        if not matches and source:
            # Some desktop publishers set an explicit window AppUserModelID.
            from docking.platform.backends.windows.properties import (
                WindowsWindowProperties,
            )
            from docking.platform.backends.windows.windows import Win32WindowAPI

            native, properties = Win32WindowAPI(), WindowsWindowProperties()
            matches = {
                window.pid
                for window in native.windows()
                if window.pid in identities
                and (properties.app_id(int(window.id.value)) or "").casefold() == source
                and native.matches(window)
            }

        return tuple(row for row in rows if row.pid in matches)

    def read(self, source: str) -> PlayerVolume | None:
        with self._scan() as rows:
            matches = self._match(source, rows)
            if not matches:
                return None
            # Windows groups multiple output streams from one player. Display
            # its highest stream level; an explicit change sets that same group.
            return PlayerVolume(
                _identity(matches), round(max(row.level for row in matches) * 100)
            )

    def set_volume(self, source: str, identifier: str, percent: int) -> bool:
        target = max(0, min(100, percent)) / 100
        with self._scan() as rows:
            matches = self._match(source, rows)
            if not matches or _identity(matches) != identifier:
                return False
            changed = []
            try:
                for row in matches:
                    check(
                        method(row.volume, 3, HRESULT, ctypes.c_float, _POINTER)(
                            row.volume, target, None
                        )
                    )
                    changed.append(row)
                if not all(
                    abs(value(row.volume, 4, ctypes.c_float).value - target) < 0.001
                    for row in matches
                ):
                    raise OSError("Player volume changed during request")
                return True
            except OSError:
                for row in reversed(changed):
                    try:
                        # Leave a concurrent external volume change alone.
                        if (
                            abs(value(row.volume, 4, ctypes.c_float).value - target)
                            < 0.001
                        ):
                            check(
                                method(
                                    row.volume, 3, HRESULT, ctypes.c_float, _POINTER
                                )(row.volume, row.level, None)
                            )
                    except OSError as exc:
                        log.debug("Could not roll back player volume: %s", exc)

                raise
