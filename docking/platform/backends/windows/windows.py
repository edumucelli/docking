"""Windows WindowService implementation: Win32 snapshots and taskbar actions."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import replace
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

from gi.repository import GLib

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowSnapshot,
)
from docking.platform.backends.window_projection import SnapshotWindowService
from docking.platform.native.windows.capture import (
    WindowObservation,
    WindowsWindowTarget,
)
from docking.platform.native.windows.ffi import MonitorInfo
from docking.platform.native.windows.ffi import bind as _bind

from .ghosts import WindowsGhostWindows
from .virtual_desktops import WindowWorkspace

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel

    from .workspaces import WindowsWorkspaceService

log = get_logger("windows.windows")


class Win32WindowAPI(WindowObservation):
    """Own the small native API boundary used by window tracking."""

    def __init__(
        self,
        *,
        workspace_membership: Callable[[int, int], WindowWorkspace | None]
        | None = None,
    ) -> None:
        from docking.platform.native.windows.properties import WindowsWindowProperties

        from .desktop import WindowsDesktopAPI

        self._desktop = WindowsDesktopAPI()
        self._membership = workspace_membership
        self._properties = WindowsWindowProperties()
        user = ctypes.WinDLL("user32", use_last_error=True)
        self._ghosts = WindowsGhostWindows(user)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self._enumerate = _bind(
            user, "EnumWindows", wintypes.BOOL, self._callback_type, wintypes.LPARAM
        )
        self._foreground = _bind(user, "GetForegroundWindow", wintypes.HWND)
        self._visible = _bind(user, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
        self._exists = _bind(user, "IsWindow", wintypes.BOOL, wintypes.HWND)
        self._iconic = _bind(user, "IsIconic", wintypes.BOOL, wintypes.HWND)
        self._zoomed = _bind(user, "IsZoomed", wintypes.BOOL, wintypes.HWND)
        self._monitor_from_window = _bind(
            user, "MonitorFromWindow", wintypes.HMONITOR, wintypes.HWND, wintypes.DWORD
        )
        self._monitor_info = _bind(
            user,
            "GetMonitorInfoW",
            wintypes.BOOL,
            wintypes.HMONITOR,
            ctypes.POINTER(MonitorInfo),
        )
        self._owner = _bind(
            user, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT
        )
        self._style = _bind(
            user, "GetWindowLongPtrW", ctypes.c_ssize_t, wintypes.HWND, ctypes.c_int
        )
        self._pid = _bind(
            user,
            "GetWindowThreadProcessId",
            wintypes.DWORD,
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._title = _bind(
            user,
            "GetWindowTextW",
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self._class = _bind(
            user,
            "GetClassNameW",
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self._rect = _bind(
            user,
            "GetWindowRect",
            wintypes.BOOL,
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        self._dwm_attribute = _bind(
            dwm,
            "DwmGetWindowAttribute",
            wintypes.LONG,
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self._open_process = _bind(
            kernel,
            "OpenProcess",
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        self._close_handle = _bind(
            kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE
        )
        self._executable = _bind(
            kernel,
            "QueryFullProcessImageNameW",
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._aumid = _bind(
            kernel,
            "GetApplicationUserModelId",
            wintypes.LONG,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.UINT),
            wintypes.LPWSTR,
        )
        self._show = _bind(
            user, "ShowWindowAsync", wintypes.BOOL, wintypes.HWND, ctypes.c_int
        )
        self._activate = _bind(
            user, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND
        )
        self._post = _bind(
            user,
            "PostMessageW",
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

    def _process_identity(self, pid: int) -> tuple[str | None, str]:
        process = self._open_process(0x1000, False, pid)  # query limited information
        if not process:
            return None, ""
        try:
            application_id = None
            size = wintypes.UINT()
            status = self._aumid(process, ctypes.byref(size), None)
            if status == 122 and 0 < size.value <= 4096:  # insufficient buffer
                buffer = ctypes.create_unicode_buffer(size.value)
                if self._aumid(process, ctypes.byref(size), buffer) == 0:
                    application_id = str(buffer.value)
            size = wintypes.DWORD(32768)
            executable = ctypes.create_unicode_buffer(size.value)
            path = (
                executable.value
                if self._executable(process, 0, executable, ctypes.byref(size))
                else ""
            )
            return application_id, PureWindowsPath(path).stem.lower() if path else ""
        finally:
            self._close_handle(process)

    def windows(self) -> tuple[WindowSnapshot, ...]:
        foreground = self._foreground()
        covered = self._desktop.covered_windows() or frozenset()
        rows: dict[WindowId, WindowSnapshot] = {}
        projected: set[WindowId] = set()
        identities: dict[int, tuple[str | None, str]] = {}

        def visit(hwnd: int, _data: int) -> bool:
            try:
                row = self._snapshot(hwnd, foreground, identities)
                if row is not None:
                    if row.wm_class == "Ghost":
                        original = self._ghosts.original(hwnd)
                        application = (
                            self._snapshot(
                                original, foreground, identities, include_hidden=True
                            )
                            if original
                            else None
                        )
                        if (
                            application is None
                            or application.wm_class == "Ghost"
                            or self._ghosts.original(hwnd) != original
                        ):
                            return True
                        row = replace(
                            row,
                            id=application.id,
                            pid=application.pid,
                            app_id=application.app_id,
                            wm_class=application.wm_class,
                            dialog=application.dialog,
                            skip_taskbar=application.skip_taskbar,
                            workspace_id=application.workspace_id,
                            on_current_workspace=application.on_current_workspace,
                            sticky=application.sticky,
                        )
                        projected.add(row.id)
                        rows.pop(row.id, None)
                    elif row.id in projected:
                        return True
                    # Non-minimizable windows remain mapped behind a raised
                    # desktop. Keep their taskbar entry but exclude overlap.
                    rows[row.id] = replace(
                        row, visible=row.visible and hwnd not in covered
                    )
            except Exception:
                # Windows may disappear or deny access between native calls;
                # exceptions must never escape a ctypes callback.
                log.exception("Could not inspect native window %s", hwnd)
            return True

        callback = self._callback_type(visit)
        if not self._enumerate(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return tuple(rows.values())

    def original_window(self, ghost: int) -> int | None:
        return self._ghosts.original(ghost)

    def _application_id(
        self,
        hwnd: int,
        pid: int,
        class_name: str,
        identities: dict[int, tuple[str | None, str]],
    ) -> str:
        # One host process can own windows from multiple packaged applications.
        # Windows' explicit per-window ID takes precedence over process identity.
        try:
            declared = self._properties.app_id(hwnd)
        except OSError as exc:
            log.debug("Window application identity unavailable: %s", exc)
            declared = None
        if declared:
            return declared
        if pid not in identities:
            identities[pid] = self._process_identity(pid)
        app_id, executable = identities[pid]
        return app_id or executable or class_name

    def snapshot(
        self, hwnd: int, *, include_hidden: bool = False
    ) -> WindowSnapshot | None:
        """Inspect one live application window using the shared native filters."""
        if not self._exists(hwnd):
            return None
        return self._snapshot(
            hwnd, self._foreground(), {}, include_hidden=include_hidden
        )

    def _snapshot(
        self,
        hwnd: int,
        foreground: int | None,
        identities: dict[int, tuple[str | None, str]],
        *,
        include_hidden: bool = False,
    ) -> WindowSnapshot | None:
        visible = bool(self._visible(hwnd))
        if not visible and not include_hidden:
            return None
        pid = wintypes.DWORD()
        self._pid(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == os.getpid():
            return None
        membership = (
            self._membership(hwnd, pid.value) if self._membership is not None else None
        )
        extended_style = self._style(hwnd, -20)
        # Keep owned dialogs for overlap policy, but never give them a separate
        # taskbar entry. Tool palettes and passive shell surfaces are excluded.
        app_window = bool(extended_style & 0x00040000)
        if not app_window and extended_style & (0x00000080 | 0x08000000):
            return None
        dialog = not app_window and bool(self._owner(hwnd, 4))
        class_name = ctypes.create_unicode_buffer(256)
        self._class(hwnd, class_name, len(class_name))
        if class_name.value in {
            "Progman",
            "WorkerW",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
        }:
            return None
        cloaked = wintypes.DWORD()
        if (
            self._dwm_attribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            == 0
            and cloaked.value
        ):
            # Shell cloaking also represents a real application on another
            # virtual desktop. Keep that row for all-workspace taskbars; it
            # remains invisible to overlap policy. Other cloaked surfaces stay
            # excluded, including retained UWP frames on the current desktop.
            if (
                membership is None
                or membership.current is not False
                or cloaked.value != 2
            ):
                return None
            visible = False
        title = ctypes.create_unicode_buffer(1024)
        self._title(hwnd, title, len(title))
        if not title.value:
            return None
        app_id = self._application_id(hwnd, pid.value, class_name.value, identities)
        rectangle = wintypes.RECT()
        geometry = None
        fullscreen = None
        if self._rect(hwnd, ctypes.byref(rectangle)):
            geometry = Rect(
                rectangle.left,
                rectangle.top,
                max(0, rectangle.right - rectangle.left),
                max(0, rectangle.bottom - rectangle.top),
            )
            monitor = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
            handle = self._monitor_from_window(hwnd, 2)
            if self._monitor_info(handle, ctypes.byref(monitor)):
                bounds = monitor.monitor
                fullscreen = bool(
                    not self._iconic(hwnd)
                    and rectangle.left <= bounds.left
                    and rectangle.top <= bounds.top
                    and rectangle.right >= bounds.right
                    and rectangle.bottom >= bounds.bottom
                )
        return WindowSnapshot(
            id=WindowId(DisplayServer.WINDOWS, hwnd),
            desktop_id="",
            title=title.value,
            app_id=app_id,
            wm_class=class_name.value,
            pid=pid.value,
            active=hwnd == foreground,
            visible=visible,
            minimized=bool(self._iconic(hwnd)),
            maximized=bool(self._zoomed(hwnd)),
            fullscreen=fullscreen,
            dialog=dialog,
            skip_taskbar=dialog,
            workspace_id=membership.desktop_id if membership is not None else None,
            on_current_workspace=membership.current if membership is not None else None,
            sticky=membership.sticky if membership is not None else False,
            geometry=geometry,
            can_activate=True,
            can_minimize=True,
            can_close=True,
        )

    def foreground_capture_snapshot(self) -> WindowSnapshot | None:
        hwnd = self._foreground()
        return self.capture_snapshot(hwnd) if hwnd else None

    def capture_snapshot(
        self, hwnd: int, *, expected_pid: int | None = None
    ) -> WindowSnapshot | None:
        """Read capture identity without applying taskbar/own-window filters."""
        if not self._exists(hwnd):
            return None
        owner = wintypes.DWORD()
        self._pid(hwnd, ctypes.byref(owner))
        if not owner.value or (
            expected_pid is not None and owner.value != expected_pid
        ):
            return None
        original = self._ghosts.original(hwnd) or hwnd
        self._pid(original, ctypes.byref(owner))
        if not owner.value:
            return None
        window = WindowSnapshot(
            id=WindowId(DisplayServer.WINDOWS, original),
            desktop_id="",
            pid=owner.value,
            app_id=self._application_id(original, owner.value, "", {}),
        )
        return window if self.matches(window) else None

    def matches(self, window: WindowSnapshot) -> bool:
        if window.id.backend is not DisplayServer.WINDOWS:
            return False
        hwnd = int(window.id.value)
        if not self._exists(hwnd):
            return False
        pid = wintypes.DWORD()
        self._pid(hwnd, ctypes.byref(pid))
        if pid.value != window.pid:
            return False
        return (
            not window.app_id
            or self._application_id(hwnd, pid.value, window.wm_class or "", {})
            == window.app_id
        )

    def perform(self, window: WindowSnapshot, action: str) -> ActionResult:
        target = self.target(window)
        if target is None:
            return ActionResult.NOT_FOUND
        hwnd = target.hwnd
        if action == "close":
            # Ghosts handle the caption's system close command, which opens
            # Windows' unresponsive-application prompt. WM_CLOSE is ignored.
            accepted = (
                self._post(hwnd, 0x0112, 0xF060, 0)
                if hwnd != window.id.value
                else self._post(hwnd, 0x0010, 0, 0)
            )
        elif action == "minimize":
            if self._iconic(hwnd):
                return ActionResult.OK
            accepted = (
                self._post(hwnd, 0x0112, 0xF020, 0)
                if hwnd != window.id.value
                else self._show(hwnd, 6)
            )
        elif action == "activate":
            if self._iconic(hwnd):
                restored = (
                    self._post(hwnd, 0x0112, 0xF120, 0)
                    if hwnd != window.id.value
                    else self._show(hwnd, 9)
                )
                if not restored:
                    return ActionResult.FAILED
            accepted = self._activate(hwnd)
        else:
            return ActionResult.UNSUPPORTED
        return ActionResult.OK if accepted else ActionResult.FAILED

    def target(self, window: WindowSnapshot) -> WindowsWindowTarget | None:
        if not self.matches(window):
            return None
        original = int(window.id.value)
        hwnd = self._ghosts.presentation(original) or original
        pid = wintypes.DWORD()
        self._pid(hwnd, ctypes.byref(pid))
        return WindowsWindowTarget(hwnd, pid.value) if pid.value else None


class WindowsWindowService(SnapshotWindowService):
    def __init__(
        self,
        *,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        config: Config | None = None,
        native: Win32WindowAPI | None = None,
        workspaces: WindowsWorkspaceService | None = None,
    ) -> None:
        super().__init__(
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            config=config,
            allow_unlisted_executables=True,
        )
        self._native = native
        self._workspaces = workspaces
        self._workspace_watch: object | None = None
        self._timer = 0
        self._previews_available = False

    def start(self) -> None:
        if self._timer:
            return
        if self._native is None:
            self._native = Win32WindowAPI(
                workspace_membership=self._workspaces.membership
                if self._workspaces is not None
                else None
            )
        if self._workspaces is not None:
            self._workspace_watch = self._workspaces.watch_active_workspace(
                self.refresh
            )
        self.refresh()
        self._timer = GLib.timeout_add(500, self._poll)

    def stop(self) -> None:
        if self._workspaces is not None and self._workspace_watch is not None:
            self._workspaces.unwatch_active_workspace(self._workspace_watch)
            self._workspace_watch = None
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        super().stop()

    def _poll(self) -> bool:
        self.refresh()
        return True

    def refresh(self) -> None:
        if self._native is None:
            return
        try:
            self.replace_windows(
                tuple(
                    replace(window, can_preview=self._previews_available)
                    for window in self._native.windows()
                )
            )
        except OSError:
            log.exception("Could not refresh native Windows application windows")

    def set_previews_available(self, available: bool) -> None:
        if self._previews_available != available:
            self._previews_available = available
            self.refresh()

    def _taskbar_window(self, window: WindowSnapshot) -> bool:
        # Membership can be unknown for windows outside the public desktop
        # manager contract or while Explorer recovers. Only a known other
        # desktop establishes that a taskbar entry should be filtered out.
        return not window.skip_taskbar and (
            self._config is None
            or not self._config.current_workspace_only
            or window.on_current_workspace is not False
        )

    def perform(self, window_id: WindowId, action: str) -> ActionResult:
        window = self._snapshots.get(window_id)
        if self._native is None or window is None:
            return ActionResult.NOT_FOUND
        native = self._native
        if (
            action == "activate"
            and window.on_current_workspace is False
            and window.workspace_id is not None
            and self._workspaces is not None
        ):
            return self._workspaces.activate_then(
                window.workspace_id, lambda: native.perform(window, action)
            )
        return native.perform(window, action)

    def capture_is_current(
        self,
        window_id: WindowId,
        pid: int,
        app_id: str | None,
        target: WindowsWindowTarget,
    ) -> bool:
        """Validate a worker's immutable frame identity without changing GTK state."""
        window = self._snapshots.get(window_id)
        return bool(
            self._native is not None
            and window is not None
            and (window.pid, window.app_id) == (pid, app_id)
            and self._native.target(window) == target
        )

    def capture_target(
        self, window_id: WindowId, pid: int, app_id: str | None
    ) -> WindowsWindowTarget | None:
        window = self._snapshots.get(window_id)
        if (
            self._native is None
            or window is None
            or (window.pid, window.app_id) != (pid, app_id)
        ):
            return None
        return self._native.target(window)
