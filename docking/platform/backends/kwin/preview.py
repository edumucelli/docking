"""Bounded, UUID-specific KWin ScreenShot2 capture with real Unix FD transfer.

Authorization belongs to KWin. An absent/denied API returns no preview, never an
active-window or whole-screen substitute. Installed desktop entries declare the
restricted interface; development launches may legitimately remain unauthorized.
"""

from __future__ import annotations

import os
import sys
import uuid

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib

from docking.log import get_logger
from docking.platform.backends.base import (
    DisplayServer,
    PreviewImage,
    PreviewService,
    WindowId,
)

log = get_logger(name="kwin_preview")
_MAX_BYTES = 64 * 1024 * 1024
_TIMEOUT_MS = 500


def raw_image_size(metadata: dict) -> int | None:
    width, height, stride = (metadata.get(key) for key in ("width", "height", "stride"))
    if metadata.get("type") != "raw" or metadata.get("format") not in {4, 5, 6, 17}:
        return None
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or not isinstance(stride, int)
    ):
        return None
    if any(isinstance(value, bool) for value in (width, height, stride)):
        return None
    if not (0 < width <= 16384 and 0 < height <= 16384 and width * 4 <= stride):
        return None
    size = stride * height
    return size if size <= _MAX_BYTES else None


def decode_raw_image(data: bytes, metadata: dict) -> GdkPixbuf.Pixbuf | None:
    size = raw_image_size(metadata)
    if size is None or len(data) != size:
        return None
    width, height, stride, image_format = (
        metadata[key] for key in ("width", "height", "stride", "format")
    )
    if image_format in {4, 6}:
        surface = cairo.ImageSurface.create_for_data(
            memoryview(bytearray(data)),
            cairo.FORMAT_RGB24 if image_format == 4 else cairo.FORMAT_ARGB32,
            width,
            height,
            stride,
        )
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
    if image_format == 5:
        rgba = bytearray(data)
        if sys.byteorder == "little":
            rgba[0::4], rgba[2::4] = data[2::4], data[0::4]
        else:
            rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = (
                data[1::4],
                data[2::4],
                data[3::4],
                data[0::4],
            )
        data = bytes(rgba)
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride
    )


class _Capture:
    def __init__(self, bus: Gio.DBusConnection, identifier: str) -> None:
        self.loop = GLib.MainLoop()
        self.cancel = Gio.Cancellable()
        self.sources: set[int] = set()
        self.metadata: dict | None = None
        self.data = bytearray()
        self.success = False
        self.finished = False
        self.fds = Gio.UnixFDList.new()
        self.read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            # KWin writes normally; only our GTK-side reader is nonblocking.
            os.set_blocking(write_fd, True)
            index = self.fds.append(write_fd)
        except (OSError, GLib.Error):
            self.close()
            raise
        finally:
            os.close(write_fd)
        self.sources.add(
            GLib.io_add_watch(
                self.read_fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self._read,
            )
        )
        self.sources.add(GLib.timeout_add(_TIMEOUT_MS, self._timeout))
        self.identifier = identifier
        options = {
            "include-cursor": GLib.Variant("b", False),
            "include-decoration": GLib.Variant("b", True),
            "native-resolution": GLib.Variant("b", True),
        }
        try:
            bus.call_with_unix_fd_list(
                "org.kde.KWin",
                "/org/kde/KWin/ScreenShot2",
                "org.kde.KWin.ScreenShot2",
                "CaptureWindow",
                GLib.Variant("(sa{sv}h)", (identifier, options, index)),
                GLib.VariantType.new("(a{sv})"),
                Gio.DBusCallFlags.NO_AUTO_START,
                _TIMEOUT_MS,
                self.fds,
                self.cancel,
                self._reply,
                None,
            )
        except (OSError, GLib.Error):
            self.close()
            raise

    def _reply(self, connection, result, _data) -> None:
        try:
            reply, _fds = connection.call_with_unix_fd_list_finish(result)
            if self.finished:
                return
            self.metadata = reply.unpack()[0]
            if (
                raw_image_size(self.metadata) is None
                or self.metadata.get("windowId") != self.identifier
            ):
                self.finish()
                return
            self._complete()
        except GLib.Error as exc:
            log.debug("KWin window capture unavailable: %s", exc.message)
            self.finish()

    def _read(self, _fd, _condition) -> bool:
        try:
            while True:
                chunk = os.read(self.read_fd, 65536)
                if not chunk:
                    if self.metadata is not None:
                        self._complete()
                        self.finish()
                    return False
                self.data.extend(chunk)
                if len(self.data) > _MAX_BYTES:
                    self.finish()
                    return False
                self._complete()
                if self.finished:
                    return False
        except BlockingIOError:
            return True
        except OSError:
            self.finish()
            return False

    def _complete(self) -> None:
        expected = raw_image_size(self.metadata) if self.metadata is not None else None
        if expected is not None and len(self.data) >= expected:
            self.success = len(self.data) == expected
            self.finish()

    def _timeout(self) -> bool:
        self.finish()
        return False

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.cancel.cancel()
        self.loop.quit()

    def close(self) -> None:
        self.finish()
        for source in self.sources:
            if GLib.MainContext.default().find_source_by_id(source) is not None:
                GLib.source_remove(source)
        self.sources.clear()
        os.close(self.read_fd)
        for fd in self.fds.steal_fds():
            os.close(fd)


class KWinPreviewService(PreviewService):
    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._capture: _Capture | None = None

    def start(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self._bus = None

    def stop(self) -> None:
        self._bus = None
        if self._capture is not None:
            self._capture.finish()

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        if self._bus is None or self._capture is not None or width <= 0 or height <= 0:
            return None
        if window_id.backend is not DisplayServer.WAYLAND or not str(
            window_id.value
        ).startswith("kwin:"):
            return None
        identifier = str(window_id.value).removeprefix("kwin:")
        try:
            uuid.UUID(identifier.strip("{}"))
        except ValueError:
            return None
        try:
            operation = _Capture(self._bus, identifier)
        except (OSError, GLib.Error) as exc:
            log.debug("KWin capture setup unavailable: %s", exc)
            return None
        self._capture = operation
        try:
            if not operation.finished:
                operation.loop.run()
            pixbuf = (
                decode_raw_image(bytes(operation.data), operation.metadata)
                if operation.success and operation.metadata is not None
                else None
            )
            if pixbuf is None:
                return None
            ratio = min(width / pixbuf.get_width(), height / pixbuf.get_height(), 1.0)
            if ratio < 1.0:
                pixbuf = pixbuf.scale_simple(
                    max(1, round(pixbuf.get_width() * ratio)),
                    max(1, round(pixbuf.get_height() * ratio)),
                    GdkPixbuf.InterpType.BILINEAR,
                )
            return PreviewImage(pixbuf, pixbuf.get_width(), pixbuf.get_height())
        finally:
            operation.close()
            self._capture = None

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self.capture(window_id, width=width, height=height)
