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

"""Overlay lifecycle and window-picking flow for the Window Killer applet.

What this applet is trying to emulate

The user experience is intentionally close to ``xkill``: click the dock icon,
get a full-screen crosshair overlay, then click a window to terminate the
process behind it.

That means this module is mostly about control flow and event capture:

- create a transparent full-screen popup window,
- grab pointer and keyboard input so the next click is unambiguous,
- translate a root-window click into a backend-selected window at that point,
- hand the selected PID to the state layer,
- cleanly dismiss the overlay on success or Escape.

Why the kill call is delegated

Actually sending ``SIGKILL`` is kept in ``state.py`` so the destructive act is
small, explicit, and separately testable. This file stays focused on selection
mechanics and backend window-pick integration.

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
from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.popup import (
    create_capture_overlay,
    dismiss_capture_overlay,
    draw_transparent_capture_overlay,
)
from docking.applets.services import AppletServices
from docking.applets.windowkiller import meta
from docking.applets.windowkiller.render import create_icon
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.backends.base import ActionResult, WindowPickService

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="windowkiller"), applet_id=meta.id)


class WindowKillerApplet(Applet):
    """Click to select a window, then force-kill it (xkill-style)."""

    id = meta.id
    name = _("Window Killer")
    icon_name = "process-stop"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._overlay: Gtk.Window | None = None
        self._window_picker: WindowPickService | None = None
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def set_services(self, services: AppletServices) -> None:
        self._window_picker = services.window_picker

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
        if self._overlay or self._window_picker is None:
            return

        self._overlay = create_capture_overlay(
            draw_handler=self._on_overlay_draw,
            click_handler=self._on_overlay_click,
            key_handler=self._on_overlay_key,
            cursor_type=Gdk.CursorType.PIRATE,
        )

    @staticmethod
    def _on_overlay_draw(widget: Gtk.Window, cr) -> bool:
        return draw_transparent_capture_overlay(widget, cr)

    def _on_overlay_click(self, _widget: Gtk.Window, event: Gdk.EventButton) -> bool:
        self._dismiss_overlay()

        picker = self._window_picker
        if picker is None:
            return True
        x, y = int(event.x_root), int(event.y_root)
        target = picker.pick_window_at(x=x, y=y)
        if target is None:
            return True
        pid = picker.pid_for(target.id)
        name = target.title or "unknown"
        if pid is None:
            log.bind(action="kill").warning("No PID for window: %s", name)
            return True
        result = picker.kill(target.id)
        log.bind(action="kill").info(
            "Killed %s (pid=%d): %s",
            name,
            pid,
            result is ActionResult.OK,
        )
        return True

    def _on_overlay_key(self, _widget: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._dismiss_overlay()
        return True

    def _dismiss_overlay(self) -> None:
        if self._overlay:
            dismiss_capture_overlay(self._overlay)
            self._overlay = None
