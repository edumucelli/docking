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

"""Display and screen-space helpers shared across UI surfaces."""

from __future__ import annotations

from typing import NamedTuple

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from docking.log import get_logger

log = get_logger("display")


class ScreenPosition(NamedTuple):
    """Screen-space coordinates."""

    x: int
    y: int


def backend_surface_position(window: object) -> ScreenPosition | None:
    """Return backend-owned screen coordinates for a window, when known."""
    surface_service = window_surface_service(window)
    get_surface_position = getattr(surface_service, "get_surface_position", None)
    if not callable(get_surface_position):
        return None
    try:
        position = get_surface_position()
    except Exception as exc:
        log.debug("Failed to query backend surface position: %s", exc)
        return None
    if position is None:
        return None
    try:
        x, y = position
    except (TypeError, ValueError):
        return None
    try:
        return ScreenPosition(x=int(x), y=int(y))
    except (TypeError, ValueError):
        return None


def window_screen_position(window: Gtk.Window) -> ScreenPosition:
    """Return the best known screen position for a GTK window.

    Native Wayland backends can position the dock through the compositor, which
    means GTK may report ``(0, 0)`` even when the surface is edge-anchored on a
    monitor.  Prefer the backend-owned position when it exists and fall back to
    GTK for X11 and generic GTK sessions.
    """
    backend_position = backend_surface_position(window)
    if backend_position is not None:
        return backend_position
    x, y = window.get_position()
    return ScreenPosition(x=int(x), y=int(y))


def window_surface_service(window: object) -> object | None:
    """Return an explicitly attached surface service, if one exists."""
    try:
        attrs = vars(window)
    except TypeError:
        return None
    if "surface_service" in attrs:
        return attrs["surface_service"]
    return None


def get_pointer_position(display: Gdk.Display) -> ScreenPosition | None:
    """Return screen coordinates of the pointer, or *None* if unavailable."""
    seat = display.get_default_seat()
    if not seat:
        return None
    pointer = seat.get_pointer()
    if not pointer:
        return None
    try:
        _, screen_x, screen_y = pointer.get_position()
    except Exception as exc:
        log.debug("Failed to query pointer position: %s", exc)
        return None
    return ScreenPosition(x=int(screen_x), y=int(screen_y))


def clamp_to_screen(
    rect_x: int,
    rect_y: int,
    rect_w: int,
    rect_h: int,
    screen_w: int,
    screen_h: int,
) -> ScreenPosition:
    """Clamp a rectangle so it stays within screen bounds."""
    return ScreenPosition(
        x=max(0, min(rect_x, screen_w - rect_w)),
        y=max(0, min(rect_y, screen_h - rect_h)),
    )


def clamp_popup(
    popup: Gtk.Window,
    popup_x: int,
    popup_y: int,
    popup_w: int,
    popup_h: int,
) -> ScreenPosition:
    """Clamp a popup to screen bounds, respecting the coordinate space.

    On X11 ``Gtk.Window.move()`` uses screen-absolute coordinates
    regardless of ``set_transient_for``, so we clamp to the screen.

    On Wayland a popup with a transient parent receives *parent-relative*
    coordinates, and the compositor handles on-screen clamping for us. Callers
    pass screen-absolute coordinates; this helper converts them to parent-local
    coordinates when the backend reports a known parent surface position.
    Which coordinate space is in use is determined by the surface service
    backing the transient parent (``popups_use_parent_relative_coordinates``
    property).
    """
    parent = popup.get_transient_for()
    surface_service = window_surface_service(parent) if parent is not None else None
    if (
        parent is not None
        and getattr(surface_service, "popups_use_parent_relative_coordinates", False)
    ):
        parent_position = backend_surface_position(parent)
        if parent_position is not None:
            return ScreenPosition(
                x=popup_x - parent_position.x,
                y=popup_y - parent_position.y,
            )
        return ScreenPosition(x=popup_x, y=popup_y)
    # Screen-absolute or no parent: clamp to screen bounds.
    screen = popup.get_screen()
    return clamp_to_screen(
        popup_x,
        popup_y,
        popup_w,
        popup_h,
        screen.get_width(),
        screen.get_height(),
    )
