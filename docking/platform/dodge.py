"""Window-dock overlap detection and Wnck monitoring for dodge autohide modes."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, NamedTuple

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Wnck  # noqa: E402

from docking.core.config import HideMode
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="dodge"))

DEBOUNCE_MS = 200

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


class WindowDodgeMonitor:
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
        self._window_signal_ids: dict[int, list[int]] = {}

    def start(self) -> None:
        self._screen = Wnck.Screen.get_default()
        if not self._screen:
            return
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
        self._window_signal_ids.clear()
        self._screen = None

    def _connect(self, obj: object, signal: str, handler: Callable) -> None:
        sid = obj.connect(signal, handler)
        self._signal_ids.append((obj, sid))

    def _connect_window(self, window: Wnck.Window) -> None:
        xid = window.get_xid()
        if xid in self._window_signal_ids:
            return
        sids: list[int] = []
        try:
            sids.append(window.connect("geometry-changed", self._on_window_event))
            sids.append(window.connect("state-changed", self._on_window_event))
        except Exception:
            pass
        self._window_signal_ids[xid] = sids

    def _disconnect_window(self, window: Wnck.Window) -> None:
        xid = window.get_xid()
        sids = self._window_signal_ids.pop(xid, [])
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

    def _schedule_evaluate(self) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(DEBOUNCE_MS, self._do_evaluate)

    def _do_evaluate(self) -> bool:
        self._debounce_id = 0
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
            return self._eval_dodge_active(dock_rect, active_window)
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
        try:
            active_class = active_window.get_class_group_name()
        except Exception:
            return False
        if not active_class:
            return False
        for win in self._visible_windows(screen, active_workspace):
            try:
                if win.get_class_group_name() != active_class:
                    continue
                if self._window_overlaps(win, dock_rect):
                    return True
            except Exception:
                continue
        return False

    def _eval_dodge_active(
        self,
        dock_rect: ScreenRect,
        active_window: Wnck.Window | None,
    ) -> bool:
        """Hide if the active window overlaps dock."""
        if not active_window:
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
        if active_window:
            try:
                if active_window.is_maximized() and self._window_overlaps(
                    active_window,
                    dock_rect,
                ):
                    return True
            except Exception:
                pass
        for win in self._visible_windows(screen, active_workspace):
            try:
                if (
                    win.get_window_type() == Wnck.WindowType.DIALOG
                    and self._window_overlaps(win, dock_rect)
                ):
                    return True
            except Exception:
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
                if win.is_minimized():
                    continue
                if win.get_window_type() in _SKIP_TYPES:
                    continue
                if active_workspace and not win.is_visible_on_workspace(
                    active_workspace,
                ):
                    continue
                yield win
            except Exception:
                continue

    def _window_overlaps(
        self,
        window: Wnck.Window,
        dock_rect: ScreenRect,
    ) -> bool:
        try:
            x, y, w, h = window.get_geometry()
            win_rect = ScreenRect(x=x, y=y, width=w, height=h)
            return rects_overlap(a=win_rect, b=dock_rect)
        except Exception:
            return False
