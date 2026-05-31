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

"""X11 window preview capture service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk, Wnck

from docking.log import get_logger
from docking.platform.backends.base import PreviewImage, WindowId

if TYPE_CHECKING:
    from docking.platform.window_tracker import WindowTracker

log = get_logger(name="x11_previews")

THUMB_W = 200
THUMB_H = 150
ICON_FALLBACK_SIZE = 64
CAPTURE_SAMPLE_GRID_MAX = 8
CAPTURE_ALPHA_MIN = 8
CAPTURE_MAX_CHANNEL_THRESHOLD = 10
CAPTURE_AVERAGE_LUMA_THRESHOLD = 5


class X11PreviewService:
    """PreviewService implementation backed by X11 foreign-window capture."""

    def __init__(self, window_tracker: WindowTracker) -> None:
        self._tracker = window_tracker

    def start(self) -> None:
        """No runtime loop is needed for X11 preview capture."""

    def stop(self) -> None:
        """No persistent resources are held by the preview service."""

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Capture one X11 window preview by backend-neutral window ID."""
        xid = _xid_from_window_id(window_id=window_id)
        if xid is None:
            return None

        # Preserve the old PreviewPopup behavior exactly: preview thumbnails
        # captured pixels directly from the XID and did not ask Wnck whether the
        # window was minimized. Routing through capture_window() here would make
        # minimized or transiently hidden windows immediately turn into fallback
        # icons, which changes the visible popup behavior while the pointer moves
        # from the dock toward the preview.
        pixbuf = capture_xid(xid=xid, thumb_w=width, thumb_h=height)
        if pixbuf is None:
            return None
        return PreviewImage(
            image=pixbuf,
            width=int(pixbuf.get_width()),
            height=int(pixbuf.get_height()),
        )

    def fallback_icon_name(self, window_id: WindowId) -> str | None:
        """Return no per-window icon override for the current X11 path."""
        return None


def capture_window(
    wnck_window: Wnck.Window, thumb_w: int = THUMB_W, thumb_h: int = THUMB_H
) -> GdkPixbuf.Pixbuf | None:
    """Capture a window's content as a scaled thumbnail pixbuf."""
    if wnck_window.is_minimized():
        return _icon_fallback(thumb_w=thumb_w, thumb_h=thumb_h)

    xid = wnck_window.get_xid()
    pixbuf = capture_xid(xid=xid, thumb_w=thumb_w, thumb_h=thumb_h)
    if pixbuf is None:
        return _icon_fallback(thumb_w=thumb_w, thumb_h=thumb_h)
    return pixbuf


def capture_xid(
    xid: int, thumb_w: int = THUMB_W, thumb_h: int = THUMB_H
) -> GdkPixbuf.Pixbuf | None:
    """Capture a window thumbnail by XID."""
    display = GdkX11.X11Display.get_default()

    try:
        foreign = GdkX11.X11Window.foreign_new_for_display(display, xid)
    except (TypeError, GLib.Error) as exc:
        log.warning(f"Failed to create foreign X11 window for xid={xid}: {exc}")
        foreign = None

    if foreign:
        try:
            width = foreign.get_width()
            height = foreign.get_height()
            if width > 0 and height > 0:
                display.error_trap_push()
                pixbuf = Gdk.pixbuf_get_from_window(foreign, 0, 0, width, height)
                x_error = display.error_trap_pop()
                if x_error or not pixbuf:
                    log.debug(f"X11 capture failed for xid={xid} (error={x_error})")
                    return None
                if _looks_unavailable_capture(pixbuf=pixbuf):
                    log.debug(f"Capture looked unavailable (black) for xid={xid}")
                    return None
                scale = min(thumb_w / width, thumb_h / height)
                new_width = max(int(width * scale), 1)
                new_height = max(int(height * scale), 1)
                return pixbuf.scale_simple(
                    new_width, new_height, GdkPixbuf.InterpType.BILINEAR
                )
        except (TypeError, GLib.Error) as exc:
            log.warning(f"Window preview capture failed for xid={xid}: {exc}")

    return None


def _looks_unavailable_capture(pixbuf: GdkPixbuf.Pixbuf) -> bool:
    """Detect near-black captures that should fallback to app icon."""
    try:
        width = int(pixbuf.get_width())
        height = int(pixbuf.get_height())
        channels = int(pixbuf.get_n_channels())
        rowstride = int(pixbuf.get_rowstride())
        has_alpha = bool(pixbuf.get_has_alpha())
        data = pixbuf.get_pixels()
    except (AttributeError, TypeError, ValueError) as exc:
        log.debug("Failed to inspect captured pixbuf: %s", exc)
        return False

    if width <= 0 or height <= 0 or channels < 3 or rowstride <= 0:
        return False
    if not isinstance(data, bytes | bytearray | memoryview):
        return False

    sample_x = max(1, min(CAPTURE_SAMPLE_GRID_MAX, width))
    sample_y = max(1, min(CAPTURE_SAMPLE_GRID_MAX, height))
    max_channel = 0
    total_luma = 0
    count = 0

    for yi in range(sample_y):
        y = int((yi + 0.5) * height / sample_y)
        if y >= height:
            y = height - 1
        for xi in range(sample_x):
            x = int((xi + 0.5) * width / sample_x)
            if x >= width:
                x = width - 1
            p = y * rowstride + x * channels
            r = data[p]
            g = data[p + 1]
            b = data[p + 2]
            a = data[p + 3] if has_alpha and channels >= 4 else 255
            if a < CAPTURE_ALPHA_MIN:
                continue
            max_channel = max(max_channel, r, g, b)
            total_luma += (r + g + b) // 3
            count += 1

    if count == 0:
        return True

    avg_luma = total_luma / count
    return (
        max_channel < CAPTURE_MAX_CHANNEL_THRESHOLD
        and avg_luma < CAPTURE_AVERAGE_LUMA_THRESHOLD
    )


def _icon_fallback(thumb_w: int, thumb_h: int) -> GdkPixbuf.Pixbuf | None:
    """Create a dark placeholder pixbuf with the generic app icon centered."""
    bg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, thumb_w, thumb_h)
    bg.fill(0x1E1E1EFF)

    icon_theme = Gtk.IconTheme.get_default()
    if icon_theme is None:
        return bg

    icon_size = min(ICON_FALLBACK_SIZE, thumb_w, thumb_h)
    try:
        icon = icon_theme.load_icon("application-x-executable", icon_size, 0)
    except GLib.Error as exc:
        log.warning(f"Failed to load fallback preview icon: {exc}")
        icon = None

    if icon:
        scaled_icon = icon.scale_simple(
            icon_size, icon_size, GdkPixbuf.InterpType.BILINEAR
        )
    else:
        scaled_icon = None

    if scaled_icon is not None:
        x = (thumb_w - icon_size) // 2
        y = (thumb_h - icon_size) // 2
        scaled_icon.composite(
            bg,
            x,
            y,
            icon_size,
            icon_size,
            x,
            y,
            1.0,
            1.0,
            GdkPixbuf.InterpType.BILINEAR,
            255,
        )
    return bg


def _xid_from_window_id(*, window_id: WindowId) -> int | None:
    if window_id.backend != "x11":
        return None
    try:
        return int(window_id.value)
    except (TypeError, ValueError):
        return None
