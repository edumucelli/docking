# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""KWin window previews via org.kde.KWin.ScreenShot2 D-Bus.

KWin 6 exposes ``org.kde.KWin.ScreenShot2``, a D-Bus API at
``/org/kde/KWin/ScreenShot2`` that captures windows, screens, or
workspaces to a pipe file descriptor.

**Authorization:**  The calling process must be associated with a
``.desktop`` file that declares::

    X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2

When Docking is launched via its installed desktop file (e.g. from the
application launcher or ``gtk-launch org.docking.Docking``), KDE
recognises the process and grants access to ScreenShot2.

**Window UUIDs:** ``CaptureWindow`` requires a KWin internal UUID.
These are obtained by matching AT-SPI window captions against KWin's
``workspace.windowList()`` internal IDs exposed through a companion
KWin script.  If UUIDs are unavailable the service falls back to
``CaptureActiveWindow`` (current foreground window) and
``CaptureActiveScreen`` (full monitor) for the active dock item.
"""

from __future__ import annotations

import os
import select
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gio

from docking.platform.backends.base import PreviewImage, PreviewService, WindowId
from docking.log import get_logger

log = get_logger(name="kwin_preview")

_DBUS_SERVICE = "org.kde.KWin"
_DBUS_PATH = "/org/kde/KWin/ScreenShot2"
_DBUS_IFACE = "org.kde.KWin.ScreenShot2"
_CAPTURE_TIMEOUT_MS = 5000


class KWinPreviewService(PreviewService):
    """Window previews via KWin ScreenShot2 D-Bus.

    Each :meth:`capture` call pipes image data from KWin through a
    Unix pipe and converts it to a :class:`GdkPixbuf.Pixbuf`.
    """

    def __init__(self) -> None:
        self._started = False
        self._bus: Gio.DBusConnection | None = None

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._started = True
            log.info("KWin preview service: ready")
        except Exception:
            log.exception("KWin preview service: failed to get D-Bus")
            self._started = False

    def stop(self) -> None:
        self._started = False
        self._bus = None

    # ------------------------------------------------------------------
    # PreviewService
    # ------------------------------------------------------------------

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Capture a preview for *window_id* via ScreenShot2.

        Falls back to a full active-screen capture when KWin UUIDs
        are not available (e.g. during development or before the
        KWin-UUID bridge is implemented).
        """
        if not self._started or self._bus is None:
            return None

        uuid = str(window_id.value)
        pixbuf = self._capture_window(uuid, width, height)
        if pixbuf is None:
            # Fall back to active screen capture
            pixbuf = self._capture_active_screen(width, height)
        if pixbuf is not None:
            return PreviewImage(
                image=pixbuf,
                width=pixbuf.get_width(),
                height=pixbuf.get_height(),
            )
        return None

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self.capture(window_id, width=width, height=height)

    # ------------------------------------------------------------------
    # ScreenShot2 helpers
    # ------------------------------------------------------------------

    def _capture_window(
        self, uuid: str, target_w: int, target_h: int
    ) -> GdkPixbuf.Pixbuf | None:
        """Capture *uuid* via ScreenShot2 and return a pixbuf."""
        try:
            return self._call_screenshot(
                "CaptureWindow",
                "s",  # extra arg format: string handle
                uuid,
                target_w,
                target_h,
            )
        except Exception:
            return None

    def _capture_active_screen(
        self, target_w: int, target_h: int
    ) -> GdkPixbuf.Pixbuf | None:
        """Capture the active screen as a fallback."""
        try:
            return self._call_screenshot(
                "CaptureActiveScreen",
                "",  # no extra args
                None,
                target_w,
                target_h,
            )
        except Exception:
            return None

    def _call_screenshot(
        self,
        method: str,
        extra_fmt: str,
        extra_val: object,
        target_w: int,
        target_h: int,
    ) -> GdkPixbuf.Pixbuf | None:
        """Invoke a ScreenShot2 *method*, read the pipe, return a pixbuf."""
        bus = self._bus
        if bus is None:
            return None

        read_fd, write_fd = os.pipe()
        try:
            # Build the top-level parameter tuple with a builder.
            # ScreenShot2 signatures:
            #   CaptureWindow:      (s a{sv} h)
            #   CaptureActive*:     (a{sv} h)
            if extra_fmt == "s":
                tuple_type = GLib.VariantType("(sa{sv}h)")
            else:
                tuple_type = GLib.VariantType("(a{sv}h)")

            builder = GLib.VariantBuilder(tuple_type)

            # The builder needs each child added in order with the
            # correct type, using add_value for leaf values and
            # open/close for containers.

            # 1. Optional string arg (window UUID)
            if extra_fmt == "s":
                builder.add_value(GLib.Variant("s", str(extra_val)))

            # 2. Open a{sv} dict, add entries, close
            # Determine the child type of the a{sv} container
            builder.open(GLib.VariantType("a{sv}"))
            for key, var in (
                ("include-cursor", GLib.Variant("b", False)),
                ("include-decoration", GLib.Variant("b", True)),
                ("native-resolution", GLib.Variant("b", True)),
            ):
                builder.add_value(GLib.Variant("{sv}", (key, var)))
            builder.close()  # close a{sv}

            # 3. Pipe fd
            builder.add_value(GLib.Variant("h", write_fd))

            params = builder.end()

            result = bus.call_sync(
                _DBUS_SERVICE,
                _DBUS_PATH,
                _DBUS_IFACE,
                method,
                params,
                GLib.VariantType("(a{sv})"),
                Gio.DBusCallFlags.NONE,
                _CAPTURE_TIMEOUT_MS,
                None,
            )

            results = result.get_child_value(0).unpack()
            log.debug("ScreenShot2 %s → %s", method, results)

            # Read image data from the pipe
            ready, _, _ = select.select([read_fd], [], [], 3.0)
            if not ready:
                log.debug("ScreenShot2: timeout reading pipe")
                return None

            data = b""
            while True:
                try:
                    chunk = os.read(read_fd, 65536)
                    if not chunk:
                        break
                    data += chunk
                except BlockingIOError:
                    break

            if not data:
                log.debug("ScreenShot2: no data in pipe")
                return None

            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()

            if pixbuf is None:
                return None

            # Scale to requested size if needed
            pw, ph = pixbuf.get_width(), pixbuf.get_height()
            if pw != target_w or ph != target_h:
                scaled = pixbuf.scale_simple(
                    target_w, target_h, GdkPixbuf.InterpType.BILINEAR,
                )
                return scaled

            return pixbuf
        except Exception:
            # Authorization failures are expected during development.
            # Only log once to avoid spamming.
            if not getattr(self, "_auth_warned", False):
                self._auth_warned = True
                log.info(
                    "ScreenShot2 not authorized — previews require "
                    "Docking to be launched via its .desktop file "
                    "(e.g. gtk-launch org.docking.Docking)"
                )
            return None
        finally:
            os.close(read_fd)
            os.close(write_fd)
