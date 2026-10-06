"""Shared overlap policy for authoritative compositor window snapshots."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from gi.repository import GLib

from docking.core.config import HideMode
from docking.platform.backends.base import (
    Rect,
    VisibilityMonitor,
    VisibilityService,
    WindowService,
    WindowSnapshot,
)

if TYPE_CHECKING:
    from docking.core.config import Config


def should_hide(
    *, windows: Sequence[WindowSnapshot], dock: Rect, mode: HideMode
) -> bool:
    """Unknown visibility/geometry never establishes an overlap."""
    candidates = [
        window
        for window in windows
        if window.visible is True
        and window.minimized is not True
        and window.pid != os.getpid()
        and window.geometry is not None
        and window.geometry.width > 0
        and window.geometry.height > 0
    ]
    focused = next((window for window in candidates if window.active), None)

    def overlaps(window: WindowSnapshot) -> bool:
        return window.geometry is not None and dock.overlaps(window.geometry)

    def same_app(window: WindowSnapshot) -> bool:
        if focused is None:
            return False
        return bool(
            (focused.pid and focused.pid == window.pid)
            or (focused.desktop_id and focused.desktop_id == window.desktop_id)
            or (focused.app_id and focused.app_id == window.app_id)
        )

    if mode is HideMode.WINDOW_DODGE:
        return any(overlaps(window) for window in candidates)
    if mode is HideMode.DODGE_ACTIVE:
        return focused is not None and overlaps(focused)
    if mode is HideMode.INTELLIGENT:
        return any(same_app(window) and overlaps(window) for window in candidates)
    if mode is HideMode.DODGE_MAXIMIZED:
        return bool(
            focused
            and (focused.maximized is True or focused.fullscreen is True)
            and overlaps(focused)
        ) or any(
            window.dialog and same_app(window) and overlaps(window)
            for window in candidates
        )
    return False


class SnapshotVisibilityMonitor(VisibilityMonitor):
    def __init__(
        self,
        *,
        windows: WindowService,
        visible_windows: Callable[[Rect], Sequence[WindowSnapshot]],
        config: Config | None,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
        poll_ms: int,
    ) -> None:
        self._windows = windows
        self._visible_windows = visible_windows
        self._config = config
        self._get_rect = get_dock_rect
        self._on_change = on_change
        self._poll_ms = poll_ms
        self._watch: object | None = None
        self._timer = 0
        self._pending = 0
        self._started = False
        self._hidden = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._watch = self._windows.watch(self._schedule)
        if self._poll_ms:
            self._timer = GLib.timeout_add(self._poll_ms, self._poll)
        self.evaluate_now()

    def stop(self) -> None:
        self._started = False
        if self._watch is not None:
            self._windows.unwatch(self._watch)
            self._watch = None
        for source in (self._timer, self._pending):
            if source:
                GLib.source_remove(source)
        self._timer = self._pending = 0

    def _schedule(self) -> None:
        # Coalesce without restarting: sustained movement cannot starve updates.
        if self._started and not self._pending:
            self._pending = GLib.idle_add(self._dispatch)

    def _dispatch(self) -> bool:
        self._pending = 0
        if self._started:
            self.evaluate_now()
        return False

    def _poll(self) -> bool:
        if not self._started:
            self._timer = 0
            return False
        mode = self._config.hide_mode_enum if self._config else HideMode.NONE
        if mode in {
            HideMode.WINDOW_DODGE,
            HideMode.DODGE_ACTIVE,
            HideMode.INTELLIGENT,
            HideMode.DODGE_MAXIMIZED,
        }:
            self._windows.refresh()
            self.evaluate_now()
        return True

    def evaluate_now(self) -> None:
        rect = self._get_rect()
        hidden = False
        if rect is not None:
            hidden = should_hide(
                windows=self._visible_windows(rect),
                dock=rect,
                mode=self._config.hide_mode_enum if self._config else HideMode.NONE,
            )
        if hidden != self._hidden:
            self._hidden = hidden
            self._on_change(hidden)


class SnapshotVisibilityService(VisibilityService):
    def __init__(
        self,
        *,
        windows: WindowService,
        visible_windows: Callable[[Rect], Sequence[WindowSnapshot]],
        config: Config | None,
        poll_ms: int = 0,
    ) -> None:
        self._windows = windows
        self._visible_windows = visible_windows
        self._config = config
        self._poll_ms = poll_ms
        self._monitors: list[SnapshotVisibilityMonitor] = []

    def start(self) -> None:
        """Subscriptions belong to individual monitors."""

    def stop(self) -> None:
        for monitor in self._monitors:
            monitor.stop()
        self._monitors.clear()

    def create_monitor(self, *, get_dock_rect, on_change) -> SnapshotVisibilityMonitor:
        monitor = SnapshotVisibilityMonitor(
            windows=self._windows,
            visible_windows=self._visible_windows,
            config=self._config,
            get_dock_rect=get_dock_rect,
            on_change=on_change,
            poll_ms=self._poll_ms,
        )
        self._monitors.append(monitor)
        return monitor


class WindowChanges:
    """Backend-owned subscriptions, notified after committing a snapshot."""

    def __init__(self) -> None:
        self._callbacks: dict[object, Callable[[], None]] = {}

    def watch(self, callback: Callable[[], None]) -> object:
        handle = object()
        self._callbacks[handle] = callback
        return handle

    def unwatch(self, handle: object) -> None:
        self._callbacks.pop(handle, None)

    def notify(self) -> None:
        for callback in tuple(self._callbacks.values()):
            callback()

    def clear(self) -> None:
        self._callbacks.clear()
