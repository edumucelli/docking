# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Window-dock overlap detection and Wnck monitoring for dodge autohide modes."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, NamedTuple

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Wnck

from docking.core.config import HideMode
from docking.log import get_logger, with_context
from docking.platform.backends.base import VisibilityMonitor

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="dodge"))

DEBOUNCE_MS = 200
_OWN_PID = os.getpid()

_SKIP_TYPES = frozenset(
    {
        Wnck.WindowType.DESKTOP,
        Wnck.WindowType.DOCK,
        Wnck.WindowType.MENU,
        Wnck.WindowType.SPLASHSCREEN,
    }
)


class ScreenRect(NamedTuple):
    x: int
    y: int
    width: int
    height: int


def rects_overlap(a: ScreenRect, b: ScreenRect) -> bool:
    """Return True if two screen rectangles overlap."""
    return (
        a.x < b.x + b.width
        and a.x + a.width > b.x
        and a.y < b.y + b.height
        and a.y + a.height > b.y
    )


class WindowDodgeMonitor(VisibilityMonitor):
    """Watches windows and reports whether the dock should hide due to overlap."""

    def __init__(
        self,
        config: Config,
        get_dock_rect: Callable[[], ScreenRect | None],
        on_change: Callable[[bool], None],
    ) -> None:
        self._config = config
        self._get_dock_rect = get_dock_rect
        self._on_change = on_change
        self._screen: Wnck.Screen | None = None
        self._should_hide = False
        self._debounce_id: int = 0
        self._signal_ids: list[tuple[object, int]] = []
        self._window_signal_ids: dict[int, tuple[Wnck.Window, list[int]]] = {}

    def start(self) -> None:
        self._screen = Wnck.Screen.get_default()
        if not self._screen:
            return
        self._force_screen_update()
        self._connect(self._screen, "active-window-changed", self._on_window_event)
        self._connect(self._screen, "window-opened", self._on_window_opened)
        self._connect(self._screen, "window-closed", self._on_window_closed)
        self._connect(self._screen, "active-workspace-changed", self._on_window_event)
        for win in self._screen.get_windows():
            self._connect_window(win)
        self._schedule_evaluate()

    def stop(self) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        for obj, sid in self._signal_ids:
            with suppress(Exception):
                obj.disconnect(sid)
        self._signal_ids.clear()
        for window, sids in self._window_signal_ids.values():
            for sid in sids:
                with suppress(Exception):
                    window.disconnect(sid)
        self._window_signal_ids.clear()
        self._screen = None

    def _connect(self, obj: object, signal: str, handler: Callable) -> None:
        sid = obj.connect_after(signal, handler)
        self._signal_ids.append((obj, sid))

    def _connect_window(self, window: Wnck.Window) -> None:
        try:
            xid = window.get_xid()
        except Exception as exc:
            log.debug("Failed to read dodge-monitor window xid: %s", exc)
            return
        if xid in self._window_signal_ids:
            return
        sids: list[int] = []
        try:
            sids.append(window.connect_after("geometry-changed", self._on_window_event))
            sids.append(window.connect_after("state-changed", self._on_window_event))
        except Exception as exc:
            log.debug("Failed to connect dodge monitor window signals: %s", exc)
        self._window_signal_ids[xid] = (window, sids)

    def _disconnect_window(self, window: Wnck.Window) -> None:
        try:
            xid = window.get_xid()
        except Exception as exc:
            log.debug(
                "Failed to read dodge-monitor window xid while disconnecting: %s",
                exc,
            )
            return
        _window, sids = self._window_signal_ids.pop(xid, (window, []))
        for sid in sids:
            with suppress(Exception):
                window.disconnect(sid)

    def _on_window_opened(self, _screen: object, window: Wnck.Window) -> None:
        self._connect_window(window)
        self._schedule_evaluate()

    def _on_window_closed(self, _screen: object, window: Wnck.Window) -> None:
        self._disconnect_window(window)
        self._schedule_evaluate()

    def _on_window_event(self, *_args: object) -> None:
        self._schedule_evaluate()

    def _force_screen_update(self) -> None:
        """Ask libwnck to flush pending X11 state before reading windows."""
        if self._screen is None:
            return
        trapped = False
        try:
            Gdk.error_trap_push()
            trapped = True
            self._screen.force_update()
            Gdk.flush()
            if Gdk.error_trap_pop() != 0:
                log.debug("Wnck force_update produced an X11 error")
            trapped = False
        except Exception as exc:
            if trapped:
                with suppress(Exception):
                    Gdk.error_trap_pop()
            log.debug("Failed to force Wnck update before dodge evaluation: %s", exc)

    def _schedule_evaluate(self) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(DEBOUNCE_MS, self._do_evaluate)

    def evaluate_now(self) -> None:
        """Cancel any pending debounce and evaluate overlap immediately."""
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        self._do_evaluate()

    def _do_evaluate(self) -> bool:
        self._debounce_id = 0
        self._force_screen_update()
        should_hide = self._evaluate()
        if should_hide != self._should_hide:
            self._should_hide = should_hide
            self._on_change(should_hide)
        return False

    def _evaluate(self) -> bool:
        mode = self._config.hide_mode_enum
        if mode in (HideMode.NONE, HideMode.AUTOHIDE):
            return False

        dock_rect = self._get_dock_rect()
        if dock_rect is None:
            return False

        screen = self._screen
        if not screen:
            return False

        active_workspace = screen.get_active_workspace()
        active_window = screen.get_active_window()

        if mode == HideMode.INTELLIGENT:
            return self._eval_intelligent(
                dock_rect,
                screen,
                active_window,
                active_workspace,
            )
        if mode == HideMode.DODGE_ACTIVE:
            return self._eval_dodge_active(dock_rect, active_window, active_workspace)
        if mode == HideMode.WINDOW_DODGE:
            return self._eval_window_dodge(dock_rect, screen, active_workspace)
        if mode == HideMode.DODGE_MAXIMIZED:
            return self._eval_dodge_maximized(
                dock_rect,
                screen,
                active_window,
                active_workspace,
            )
        return False

    def _eval_intelligent(
        self,
        dock_rect: ScreenRect,
        screen: Wnck.Screen,
        active_window: Wnck.Window | None,
        active_workspace: Wnck.Workspace | None,
    ) -> bool:
        """Hide if any window from the active app overlaps dock."""
        if not active_window:
            return False

        if self._is_dodge_candidate(active_window, active_workspace) and (
            self._window_overlaps(active_window, dock_rect)
        ):
            return True

        active_pid = self._window_pid(active_window)
        active_class = self._window_class(active_window)
        if active_pid is None and not active_class:
            return False

        for win in self._visible_windows(screen, active_workspace):
            try:
                win_pid = self._window_pid(win)
                win_class = self._window_class(win)
                same_process = (
                    active_pid is not None
                    and win_pid is not None
                    and win_pid == active_pid
                )
                same_class = bool(active_class and win_class == active_class)
                if not (same_process or same_class):
                    continue
                if self._window_overlaps(win, dock_rect):
                    return True
            except Exception as exc:
                log.debug(
                    "Failed to inspect candidate window for intelligent dodge: %s",
                    exc,
                )
                continue
        return False

    @staticmethod
    def _window_pid(window: Wnck.Window) -> int | None:
        try:
            pid = int(window.get_pid())
        except Exception as exc:
            log.debug("Failed to read window pid for dodge overlap: %s", exc)
            return None
        return pid if pid > 0 else None

    @staticmethod
    def _window_class(window: Wnck.Window) -> str | None:
        try:
            return window.get_class_group_name() or None
        except Exception as exc:
            log.debug("Failed to read window class for dodge overlap: %s", exc)
            return None

    def _eval_dodge_active(
        self,
        dock_rect: ScreenRect,
        active_window: Wnck.Window | None,
        active_workspace: Wnck.Workspace | None,
    ) -> bool:
        """Hide if the active window overlaps dock."""
        if not active_window:
            return False
        if not self._is_dodge_candidate(active_window, active_workspace):
            return False
        return self._window_overlaps(active_window, dock_rect)

    def _eval_window_dodge(
        self,
        dock_rect: ScreenRect,
        screen: Wnck.Screen,
        active_workspace: Wnck.Workspace | None,
    ) -> bool:
        """Hide if any window overlaps dock."""
        return any(
            self._window_overlaps(win, dock_rect)
            for win in self._visible_windows(screen, active_workspace)
        )

    def _eval_dodge_maximized(
        self,
        dock_rect: ScreenRect,
        screen: Wnck.Screen,
        active_window: Wnck.Window | None,
        active_workspace: Wnck.Workspace | None,
    ) -> bool:
        """Hide if active window is maximized or a dialog overlaps."""
        active_pid = self._window_pid(active_window) if active_window else None
        active_class = self._window_class(active_window) if active_window else None
        if active_window:
            try:
                if (
                    self._is_dodge_candidate(active_window, active_workspace)
                    and self._window_is_maximized(active_window)
                    and self._window_overlaps(
                        active_window,
                        dock_rect,
                    )
                ):
                    return True
            except Exception as exc:
                log.debug("Failed to inspect active window maximized state: %s", exc)
        for win in self._visible_windows(screen, active_workspace):
            try:
                if (
                    win.get_window_type() == Wnck.WindowType.DIALOG
                    and self._matches_active_app(
                        win,
                        active_pid=active_pid,
                        active_class=active_class,
                    )
                    and self._window_overlaps(win, dock_rect)
                ):
                    return True
            except Exception as exc:
                log.debug(
                    "Failed to inspect dialog window for dodge-maximized: %s",
                    exc,
                )
                continue
        return False

    def _visible_windows(
        self,
        screen: Wnck.Screen,
        active_workspace: Wnck.Workspace | None,
    ):
        """Yield normal windows on active workspace that aren't minimized."""
        for win in screen.get_windows():
            try:
                if self._is_dodge_candidate(win, active_workspace):
                    yield win
            except Exception as exc:
                log.debug(
                    "Failed to inspect window while listing visible windows: %s",
                    exc,
                )
                continue

    def _is_dodge_candidate(
        self,
        window: Wnck.Window,
        active_workspace: Wnck.Workspace | None,
    ) -> bool:
        try:
            if window.is_minimized():
                return False
            if window.get_window_type() in _SKIP_TYPES:
                return False
            if active_workspace and not window.is_visible_on_workspace(
                active_workspace,
            ):
                return False
        except Exception as exc:
            log.debug("Failed to inspect dodge candidate window: %s", exc)
            return False

        pid = self._window_pid(window)
        return pid != _OWN_PID

    def _matches_active_app(
        self,
        window: Wnck.Window,
        *,
        active_pid: int | None,
        active_class: str | None,
    ) -> bool:
        win_pid = self._window_pid(window)
        win_class = self._window_class(window)
        same_process = (
            active_pid is not None and win_pid is not None and win_pid == active_pid
        )
        same_class = bool(active_class and win_class == active_class)
        return same_process or same_class

    @staticmethod
    def _window_is_maximized(window: Wnck.Window) -> bool:
        try:
            return bool(
                window.is_maximized()
                or window.is_maximized_vertically()
                or window.is_maximized_horizontally()
            )
        except Exception as exc:
            log.debug("Failed to read window maximized state: %s", exc)
        return False

    def _window_overlaps(
        self,
        window: Wnck.Window,
        dock_rect: ScreenRect,
    ) -> bool:
        try:
            x, y, w, h = window.get_geometry()
            win_rect = ScreenRect(x=x, y=y, width=w, height=h)
            return rects_overlap(a=win_rect, b=dock_rect)
        except Exception as exc:
            log.debug("Failed to read window geometry for dodge overlap: %s", exc)
            return False
