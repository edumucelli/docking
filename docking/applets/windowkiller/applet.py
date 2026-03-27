"""Overlay lifecycle and window-picking flow for the Window Killer applet.

What this applet is trying to emulate

The user experience is intentionally close to ``xkill``: click the dock icon,
get a full-screen crosshair overlay, then click a window to terminate the
process behind it.

That means this module is mostly about control flow and event capture:

- create a transparent full-screen popup window,
- grab pointer and keyboard input so the next click is unambiguous,
- translate a root-window click into the topmost Wnck window at that point,
- hand the selected PID to the state layer,
- cleanly dismiss the overlay on success or Escape.

Why the kill call is delegated

Actually sending ``SIGKILL`` is kept in ``state.py`` so the destructive act is
small, explicit, and separately testable. This file stays focused on selection
mechanics and GTK/Wnck integration.

Why this module needs long-form explanation

Overlay input grabs are one of the more fragile interaction patterns in the
codebase. Documenting the intended flow here reduces the risk of future changes
accidentally leaving the seat grabbed or changing the target-selection rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Wnck

from docking.applets.base import Applet
from docking.applets.windowkiller import meta
from docking.applets.windowkiller.render import create_icon
from docking.applets.windowkiller.state import kill_pid
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="windowkiller"), applet_id=meta.id)


class WindowKillerApplet(Applet):
    """Click to select a window, then force-kill it (xkill-style)."""

    id = meta.id
    name = _("Window Killer")
    icon_name = "process-stop"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._overlay: Gtk.Window | None = None
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = _("Click, then select a window to kill")

    def on_clicked(self) -> None:
        self._start_pick()

    def stop(self) -> None:
        self._dismiss_overlay()
        super().stop()

    # -- Overlay (reuses colorpicker pattern) ---------------------------------

    def _start_pick(self) -> None:
        if self._overlay:
            return

        overlay = Gtk.Window(type=Gtk.WindowType.POPUP)
        overlay.set_decorated(False)
        overlay.set_app_paintable(True)

        screen = overlay.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            overlay.set_visual(visual)

        overlay.set_default_size(screen.get_width(), screen.get_height())
        overlay.move(0, 0)

        overlay.connect("draw", self._on_overlay_draw)
        overlay.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK
        )
        overlay.connect("button-press-event", self._on_overlay_click)
        overlay.connect("key-press-event", self._on_overlay_key)

        display = Gdk.Display.get_default()
        crosshair = Gdk.Cursor.new_for_display(display, Gdk.CursorType.PIRATE)

        overlay.show_all()
        overlay.get_window().set_cursor(crosshair)

        seat = display.get_default_seat()
        seat.grab(
            overlay.get_window(),
            Gdk.SeatCapabilities.ALL_POINTING | Gdk.SeatCapabilities.KEYBOARD,
            True,
            crosshair,
            None,
            None,
            None,
        )

        self._overlay = overlay

    @staticmethod
    def _on_overlay_draw(widget: Gtk.Window, cr) -> bool:
        cr.set_source_rgba(0, 0, 0, 0.01)
        cr.paint()
        return True

    def _on_overlay_click(self, _widget: Gtk.Window, event: Gdk.EventButton) -> bool:
        self._dismiss_overlay()

        x, y = int(event.x_root), int(event.y_root)
        screen = Wnck.Screen.get_default()
        if screen:
            screen.force_update()
            target = self._window_at(screen=screen, x=x, y=y)
            if target:
                pid = target.get_pid()
                name = target.get_name() or "unknown"
                if pid > 0:
                    killed = kill_pid(pid=pid)
                    _log.bind(action="kill").info(
                        "Killed %s (pid=%d): %s", name, pid, killed
                    )
                else:
                    _log.bind(action="kill").warning("No PID for window: %s", name)
        return True

    def _on_overlay_key(self, _widget: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._dismiss_overlay()
        return True

    def _dismiss_overlay(self) -> None:
        if self._overlay:
            display = Gdk.Display.get_default()
            seat = display.get_default_seat()
            seat.ungrab()
            self._overlay.destroy()
            self._overlay = None

    @staticmethod
    def _window_at(screen: Wnck.Screen, x: int, y: int) -> Wnck.Window | None:
        """Find the topmost window containing (x, y)."""
        # Wnck stacking order: last = topmost
        for win in reversed(screen.get_windows_stacked()):
            if win.get_window_type() != Wnck.WindowType.NORMAL:
                continue
            if win.is_minimized():
                continue
            geo = win.get_geometry()
            wx, wy, ww, wh = geo
            if wx <= x < wx + ww and wy <= y < wy + wh:
                return win
        return None
