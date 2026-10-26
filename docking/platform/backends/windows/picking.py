"""Windows hit testing and process termination behind WindowPickService."""

from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath
from typing import Protocol

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowPickService,
    WindowSnapshot,
)

from .ffi import bind
from .geometry import WindowsMonitorGeometry
from .windows import Win32WindowAPI

log = get_logger("windows.picking")
_QUERY = 0x1000 | 0x100000  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE.
_SHELL_PROCESSES = frozenset(
    {
        "applicationframehost.exe",
        "sihost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "searchhost.exe",
        "dwm.exe",
    }
)


@dataclass(frozen=True)
class PickedWindow:
    window: WindowSnapshot
    root_pid: int
    process_window: int
    created: int
    ghost_window: int | None = None


class PickAPI(Protocol):
    def pick(self, x: int, y: int) -> PickedWindow | None: ...
    def valid(self, target: PickedWindow) -> bool: ...
    def terminate(self, target: PickedWindow) -> ActionResult: ...


class _FileTime(ctypes.Structure):
    _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))


class WindowsPickAPI:
    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._windows = Win32WindowAPI()
        self._geometry = WindowsMonitorGeometry(user)
        self._at_point = bind(user, "WindowFromPoint", ctypes.c_void_p, wintypes.POINT)
        try:
            # This named user32 export is optional, outside the documented
            # Win32 contract. A ghost must never fall back to DWM's own PID.
            self._hung = bind(
                user, "HungWindowFromGhostWindow", ctypes.c_void_p, ctypes.c_void_p
            )
        except AttributeError:
            self._hung = None
        self._ancestor = bind(
            user, "GetAncestor", ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
        )
        self._is_child = bind(
            user, "IsChild", ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p
        )
        self._exists = bind(user, "IsWindow", ctypes.c_int32, ctypes.c_void_p)
        self._visible = bind(user, "IsWindowVisible", ctypes.c_int32, ctypes.c_void_p)
        self._owner = bind(
            user,
            "GetWindowThreadProcessId",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self._shell = bind(user, "GetShellWindow", ctypes.c_void_p)
        self._class = bind(
            user,
            "GetClassNameW",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int32,
        )
        self._callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p, ctypes.c_ssize_t
        )
        self._children = bind(
            user,
            "EnumChildWindows",
            ctypes.c_int32,
            ctypes.c_void_p,
            self._callback_type,
            ctypes.c_ssize_t,
        )
        self._open = bind(
            kernel,
            "OpenProcess",
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_uint32,
        )
        self._close = bind(kernel, "CloseHandle", ctypes.c_int32, ctypes.c_void_p)
        self._times = bind(
            kernel,
            "GetProcessTimes",
            ctypes.c_int32,
            ctypes.c_void_p,
            *([ctypes.POINTER(_FileTime)] * 4),
        )
        self._wait = bind(
            kernel,
            "WaitForSingleObject",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        self._critical = bind(
            kernel,
            "IsProcessCritical",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int32),
        )
        self._image = bind(
            kernel,
            "QueryFullProcessImageNameW",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self._terminate = bind(
            kernel,
            "TerminateProcess",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        self._flush = bind(
            ctypes.WinDLL("dwmapi", use_last_error=True), "DwmFlush", ctypes.c_int32
        )

    def _pid(self, window: int) -> int:
        pid = ctypes.c_uint32()
        self._owner(window, ctypes.byref(pid))
        return int(pid.value)

    @contextmanager
    def _process(self, pid: int, *, terminate: bool = False):
        handle = self._open(_QUERY | int(terminate), False, pid)
        try:
            yield handle
        finally:
            if handle:
                self._close(handle)

    def _created(self, handle: int) -> int | None:
        if self._wait(handle, 0) != 258:  # WAIT_TIMEOUT: still running.
            return None
        times = [_FileTime() for _ in range(4)]
        if not self._times(handle, *(ctypes.byref(t) for t in times)):
            return None
        return int(times[0].high) << 32 | int(times[0].low)

    def _allowed(self, handle: int, pid: int) -> bool:
        if pid <= 4 or pid == os.getpid():
            return False
        shell = self._shell()
        if shell and pid == self._pid(shell):
            return False
        critical = ctypes.c_int32()
        if not self._critical(handle, ctypes.byref(critical)) or critical.value:
            return False
        name, length = ctypes.create_unicode_buffer(32768), ctypes.c_uint32(32768)
        if not self._image(handle, 0, name, ctypes.byref(length)):
            return False
        return bool(name.value) and PureWindowsPath(name.value).name.casefold() not in (
            _SHELL_PROCESSES
        )

    def _content(self, root: int, root_pid: int) -> tuple[int, int] | None:
        candidates: dict[int, int] = {}
        failed = False

        def visit(window: int, _data: int) -> bool:
            nonlocal failed
            try:
                name = ctypes.create_unicode_buffer(256)
                self._class(window, name, len(name))
                pid = self._pid(window)
                if (
                    name.value == "Windows.UI.Core.CoreWindow"
                    and self._visible(window)
                    and pid > 4
                    and pid != root_pid
                ):
                    candidates.setdefault(pid, window)
                return True
            except Exception:
                log.exception("Could not inspect hosted application window")
                failed = True
                return False

        self._children(root, self._callback_type(visit), 0)
        if failed or len(candidates) != 1:
            return None
        pid, window = next(iter(candidates.items()))
        return window, pid

    def _live_window(self, target: PickedWindow) -> bool:
        root = int(target.window.id.value)
        content = target.process_window
        return bool(
            (
                target.ghost_window is None
                or (
                    self._hung is not None
                    and self._exists(target.ghost_window)
                    and self._hung(target.ghost_window) == root
                )
            )
            and self._exists(root)
            and self._pid(root) == target.root_pid
            and self._exists(content)
            and self._pid(content) == target.window.pid
            and (root == content or self._is_child(root, content))
        )

    def pick(self, x: int, y: int) -> PickedWindow | None:
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        point = self._geometry.point(x, y, display) if display is not None else None
        if point is None or self._flush() < 0:
            return None
        hit = self._at_point(wintypes.POINT(*point))
        root = self._ancestor(hit, 2) if hit else 0  # GA_ROOT, preserving dialogs.
        ghost = None
        if root:
            name = ctypes.create_unicode_buffer(256)
            self._class(root, name, len(name))
            if name.value == "Ghost":
                ghost = root
                root = self._hung(ghost) if self._hung is not None else 0
        window = (
            self._windows.snapshot(root, include_hidden=ghost is not None)
            if root
            else None
        )
        if window is None or window.minimized or not window.pid:
            return None
        process_window, pid = int(root), window.pid
        if window.wm_class == "ApplicationFrameWindow":
            content = self._content(root, window.pid)
            if content is None:
                return None
            process_window, pid = content
        with self._process(pid) as handle:
            if not handle or not self._allowed(handle, pid):
                return None
            created = self._created(handle)
            if created is None:
                return None
        target = PickedWindow(
            replace(window, pid=pid), window.pid, process_window, created, ghost
        )
        return target if self._live_window(target) else None

    def valid(self, target: PickedWindow) -> bool:
        if not self._live_window(target) or target.window.pid is None:
            return False
        with self._process(target.window.pid) as handle:
            return bool(
                handle
                and self._created(handle) == target.created
                and self._allowed(handle, target.window.pid)
                and self._live_window(target)
            )

    def terminate(self, target: PickedWindow) -> ActionResult:
        if not self._live_window(target) or target.window.pid is None:
            return ActionResult.NOT_FOUND
        with self._process(target.window.pid, terminate=True) as handle:
            if not handle:
                return ActionResult.FAILED
            if self._created(handle) != target.created:
                return ActionResult.NOT_FOUND
            if not self._allowed(handle, target.window.pid):
                return ActionResult.UNSUPPORTED
            # Keep the validated process handle through TerminateProcess. PID
            # reuse cannot redirect termination to a different kernel object.
            if not self._live_window(target):
                return ActionResult.NOT_FOUND
            return (
                ActionResult.OK if self._terminate(handle, 1) else ActionResult.FAILED
            )


class WindowsWindowPickService(WindowPickService):
    """Keep only the latest picked process identity, without retaining handles."""

    def __init__(self, *, api: PickAPI | None = None) -> None:
        self._api = api
        self._selected: PickedWindow | None = None
        self._stopped = False
        self._generation = 0

    def start(self) -> None:
        self._generation += 1
        self._selected = None
        self._stopped = False

    def stop(self) -> None:
        self._generation += 1
        self._selected = None
        self._stopped = True

    def pick_window_at(self, *, x: int, y: int) -> WindowSnapshot | None:
        self._selected = None
        if self._stopped:
            return None
        generation = self._generation
        try:
            if self._api is None:
                self._api = WindowsPickAPI()
            target = self._api.pick(x, y)
        except OSError as exc:
            log.debug("Window selection unavailable: %s", exc)
            return None
        if self._stopped or generation != self._generation:
            return None
        self._selected = target
        return target.window if target is not None else None

    def pid_for(self, window_id: WindowId) -> int | None:
        selected, api = self._selected, self._api
        if self._stopped or selected is None or selected.window.id != window_id:
            return None
        generation = self._generation
        try:
            valid = api is not None and api.valid(selected)
        except OSError as exc:
            log.debug("Picked process query failed: %s", exc)
            return None
        if not valid or self._stopped or generation != self._generation:
            return None
        return selected.window.pid

    def kill(self, window_id: WindowId) -> ActionResult:
        selected, self._selected = self._selected, None
        if (
            self._stopped
            or selected is None
            or selected.window.id != window_id
            or window_id.backend is not DisplayServer.WINDOWS
            or self._api is None
        ):
            return ActionResult.NOT_FOUND
        try:
            return self._api.terminate(selected)
        except OSError as exc:
            log.debug("Picked process termination failed: %s", exc)
            return ActionResult.FAILED
