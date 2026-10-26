"""Scoped Windows virtual-desktop COM calls, used only by a backend worker.

Window membership uses the public IVirtualDesktopManager contract. Enumeration
and switching use the 24H2/25H2 shell ABI, restricted to its identified builds
and interface IDs; an unknown build never gets a guessed vtable call.
"""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from types import MappingProxyType

from docking.log import get_logger
from docking.platform.backends.base import ActionResult, WorkspaceSnapshot

from .com import GUID, HRESULT, apartment, method, release
from .ffi import bind

log = get_logger("windows.virtual_desktops")
_SHELL = GUID.parse("c2f03a33-21f5-47fa-b4bb-156362a2f239")
_PROVIDER = GUID.parse("6d5140c1-7436-11ce-8034-00aa006009fa")
_MANAGER_SERVICE = GUID.parse("c5e0cdca-7b6e-41b2-9fc4-d93975cc467b")
_MANAGER = GUID.parse("53f5ca0b-158f-4124-900c-057158060b27")
_DESKTOP = GUID.parse("3f07f4be-b107-441a-af0f-39d82529072c")
_PUBLIC_CLASS = GUID.parse("aa509086-5ca9-4c25-8f95-589d3c07b48a")
_PUBLIC = GUID.parse("a5cd92ff-29be-454c-8d04-d82879fb3f1b")
_VIEWS = GUID.parse("1841c6d7-4f9d-42c0-af41-8747538f10e5")
_PINNED_SERVICE = GUID.parse("b5a399e7-1c87-46b8-88e9-fc5747b171bd")
_PINNED = GUID.parse("4ce81583-1e4c-4632-a621-07a53543148f")
_BUILDS = frozenset({26100, 26200})
_Create = Callable[..., int]


def _check(status: int) -> None:
    if status < 0:
        raise OSError(f"Virtual desktop call failed: 0x{status & 0xFFFFFFFF:08x}")


def _identifier(value: GUID) -> str:
    return str(uuid.UUID(bytes_le=bytes(value)))


def _object(stack: ExitStack, pointer: ctypes.c_void_p, index: int, *arguments):
    value = ctypes.c_void_p()
    try:
        _check(
            method(
                pointer,
                index,
                HRESULT,
                *arguments[::2],
                ctypes.POINTER(ctypes.c_void_p),
            )(pointer, *arguments[1::2], ctypes.byref(value))
        )
        if not value.value:
            raise OSError("Missing virtual desktop interface")
        return value
    finally:
        if value.value:
            stack.callback(release, value)


@dataclass(frozen=True)
class WindowWorkspace:
    pid: int
    desktop_id: str | None
    current: bool | None
    sticky: bool = False


@dataclass(frozen=True)
class VirtualDesktopState:
    desktops: tuple[WorkspaceSnapshot, ...]
    windows: Mapping[int, WindowWorkspace]


class WindowsVirtualDesktopAPI:
    @staticmethod
    def supported() -> bool:
        return sys.platform == "win32" and sys.getwindowsversion().build in _BUILDS

    @contextmanager
    def _shell(self) -> Iterator[tuple[ExitStack, ctypes.c_void_p, _Create]]:
        if not self.supported():
            raise OSError("Virtual desktop shell ABI is unavailable on this build")
        with apartment(multithreaded=True) as ole, ExitStack() as stack:
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
            shell = self._create(stack, create, _SHELL, _PROVIDER, 4)
            yield stack, shell, create

    @contextmanager
    def _session(self) -> Iterator[tuple[ExitStack, ctypes.c_void_p, _Create]]:
        with self._shell() as (stack, shell, create):
            manager = _object(
                stack,
                shell,
                3,
                ctypes.POINTER(GUID),
                ctypes.byref(_MANAGER_SERVICE),
                ctypes.POINTER(GUID),
                ctypes.byref(_MANAGER),
            )
            yield stack, manager, create

    def set_pinned(self, hwnd: int, pinned: bool) -> None:
        """Apply stickiness to one owned view, never a shared application ID."""
        if not self.supported():
            raise OSError("Virtual desktop shell ABI is unavailable on this build")
        user = ctypes.WinDLL("user32", use_last_error=True)
        owner = bind(
            user,
            "GetWindowThreadProcessId",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )

        def owned() -> bool:
            pid = ctypes.c_uint32()
            owner(hwnd, ctypes.byref(pid))
            return pid.value == os.getpid()

        if not owned():
            raise OSError("Dock surface is no longer owned")
        with self._shell() as (stack, shell, _create):
            views = _object(
                stack,
                shell,
                3,
                ctypes.POINTER(GUID),
                ctypes.byref(_VIEWS),
                ctypes.POINTER(GUID),
                ctypes.byref(_VIEWS),
            )
            view = _object(stack, views, 6, ctypes.c_void_p, hwnd)
            pinning = _object(
                stack,
                shell,
                3,
                ctypes.POINTER(GUID),
                ctypes.byref(_PINNED_SERVICE),
                ctypes.POINTER(GUID),
                ctypes.byref(_PINNED),
            )
            current = ctypes.c_int32()
            _check(
                method(
                    pinning, 6, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)
                )(pinning, view, ctypes.byref(current))
            )
            if bool(current.value) != pinned and owned():
                _check(
                    method(pinning, 7 if pinned else 8, HRESULT, ctypes.c_void_p)(
                        pinning, view
                    )
                )

    @staticmethod
    def _create(stack: ExitStack, create: _Create, cls: GUID, iid: GUID, context: int):
        value = ctypes.c_void_p()
        try:
            _check(
                create(
                    ctypes.byref(cls),
                    None,
                    context,
                    ctypes.byref(iid),
                    ctypes.byref(value),
                )
            )
            if not value.value:
                raise OSError("Missing virtual desktop COM object")
            return value
        finally:
            if value.value:
                stack.callback(release, value)

    @staticmethod
    def _id(desktop: ctypes.c_void_p) -> str:
        value = GUID()
        _check(
            method(desktop, 4, HRESULT, ctypes.POINTER(GUID))(
                desktop, ctypes.byref(value)
            )
        )
        if not any(bytes(value)):
            raise OSError("Windows returned an empty desktop ID")
        return _identifier(value)

    @staticmethod
    def _name(desktop: ctypes.c_void_p) -> str:
        runtime, value = ctypes.WinDLL("combase"), ctypes.c_void_p()
        clear = bind(runtime, "WindowsDeleteString", HRESULT, ctypes.c_void_p)
        raw = bind(
            runtime,
            "WindowsGetStringRawBuffer",
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        try:
            _check(
                method(desktop, 5, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
                    desktop, ctypes.byref(value)
                )
            )
            size = ctypes.c_uint32()
            data = raw(value, ctypes.byref(size))
            if size.value > 4096 or (size.value and not data):
                raise OSError("Invalid desktop name")
            return ctypes.wstring_at(data, size.value).strip() if size.value else ""
        finally:
            _check(clear(value))

    def _desktops(self, stack: ExitStack, manager: ctypes.c_void_p):
        array = _object(stack, manager, 7)
        size = ctypes.c_uint32()
        _check(
            method(array, 3, HRESULT, ctypes.POINTER(ctypes.c_uint32))(
                array, ctypes.byref(size)
            )
        )
        if not 0 < size.value <= 256:
            raise OSError("Invalid virtual desktop count")
        return tuple(
            _object(
                stack,
                array,
                4,
                ctypes.c_uint32,
                index,
                ctypes.POINTER(GUID),
                ctypes.byref(_DESKTOP),
            )
            for index in range(size.value)
        )

    def read(self) -> VirtualDesktopState:
        with self._session() as (stack, manager, create):
            desktops = self._desktops(stack, manager)
            active = self._id(_object(stack, manager, 6))
            rows = tuple(
                WorkspaceSnapshot(
                    id=identifier,
                    number=index,
                    name=self._name(desktop),
                    active=identifier == active,
                )
                for index, desktop in enumerate(desktops)
                for identifier in (self._id(desktop),)
            )
            if (
                len({row.id for row in rows}) != len(rows)
                or sum(row.active for row in rows) != 1
            ):
                raise OSError("Virtual desktops changed during enumeration")
            public = self._create(stack, create, _PUBLIC_CLASS, _PUBLIC, 1)
            windows = self._windows(public, frozenset(row.id for row in rows))
            if self._id(_object(stack, manager, 6)) != active:
                raise OSError("Active desktop changed during membership queries")
            return VirtualDesktopState(rows, MappingProxyType(windows))

    def activate(self, desktop_id: str, cancelled: Callable[[], bool]) -> ActionResult:
        with self._session() as (stack, manager, _create):
            target = next(
                (
                    desktop
                    for desktop in self._desktops(stack, manager)
                    if self._id(desktop) == desktop_id
                ),
                None,
            )
            if target is None or cancelled():
                return ActionResult.NOT_FOUND
            if self._id(_object(stack, manager, 6)) == desktop_id:
                return ActionResult.OK
            _check(method(manager, 9, HRESULT, ctypes.c_void_p)(manager, target))
            return ActionResult.OK

    @staticmethod
    def _windows(
        public: ctypes.c_void_p, desktops: frozenset[str]
    ) -> dict[int, WindowWorkspace]:
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p, ctypes.c_ssize_t
        )
        enumerate_windows = bind(
            user, "EnumWindows", ctypes.c_int32, callback_type, ctypes.c_ssize_t
        )
        visible = bind(user, "IsWindowVisible", ctypes.c_int32, ctypes.c_void_p)
        owner = bind(
            user,
            "GetWindowThreadProcessId",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        current = method(
            public, 3, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)
        )
        get_id = method(public, 4, HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID))
        rows: dict[int, WindowWorkspace] = {}
        failed = False

        def visit(hwnd: int, _data: int) -> bool:
            nonlocal failed
            try:
                if not visible(hwnd):
                    return True
                pid, after = ctypes.c_uint32(), ctypes.c_uint32()
                owner(hwnd, ctypes.byref(pid))
                if not pid.value or pid.value == os.getpid():
                    return True
                value, on_current = GUID(), ctypes.c_int32()
                state_status, id_status = (
                    current(public, hwnd, ctypes.byref(on_current)),
                    get_id(public, hwnd, ctypes.byref(value)),
                )
                owner(hwnd, ctypes.byref(after))
                if after.value != pid.value:
                    return True
                identifier = (
                    _identifier(value) if id_status >= 0 and any(bytes(value)) else None
                )
                is_current = bool(on_current.value) if state_status >= 0 else None
                rows[hwnd] = WindowWorkspace(
                    pid.value,
                    identifier,
                    is_current,
                    bool(identifier and identifier not in desktops and is_current),
                )
                return True
            except Exception:
                # Never let Python exceptions escape a ctypes callback.
                log.exception("Could not query window desktop membership")
                failed = True
                return False

        if not enumerate_windows(callback_type(visit), 0) or failed:
            raise OSError("Could not enumerate desktop membership")
        return rows
