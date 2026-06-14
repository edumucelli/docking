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

"""Wayland portal-backed services."""

from __future__ import annotations

from collections.abc import Callable

from docking.platform.backends.base import ScreenCaptureService


class WaylandPortalColorPickerService(ScreenCaptureService):
    """ScreenCaptureService using XDG Desktop Portal PickColor."""

    def __init__(
        self, *, picker: Callable[[], tuple[float, float, float] | None]
    ) -> None:
        self._picker = picker

    def start(self) -> None:
        """No persistent portal state is held."""

    def stop(self) -> None:
        """No persistent portal state is held."""

    def pick_color(self, *, x: int, y: int) -> tuple[int, int, int] | None:
        color = self._picker()
        if color is None:
            return None
        red, green, blue = color
        return (
            _float_channel_to_byte(red),
            _float_channel_to_byte(green),
            _float_channel_to_byte(blue),
        )


class XdgDesktopPortalColorPicker:
    """Synchronous wrapper around org.freedesktop.portal.Screenshot.PickColor."""

    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self._timeout_seconds = timeout_seconds

    def __call__(self) -> tuple[float, float, float] | None:
        try:
            from gi.repository import Gio, GLib
        except (ImportError, ValueError):
            return None

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Screenshot",
                None,
            )
            result = proxy.call_sync(
                "PickColor",
                GLib.Variant("(sa{sv})", ("", {})),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error:
            return None

        handle = result.unpack()[0]
        loop = GLib.MainLoop()
        picked: dict[str, tuple[float, float, float] | None] = {"color": None}
        timed_out = {"value": False}

        def on_response(
            _connection: object,
            _sender: str,
            _path: str,
            _interface: str,
            _signal: str,
            parameters,
        ) -> None:
            response, results = parameters.unpack()
            if response == 0 and "color" in results:
                picked["color"] = tuple(results["color"])  # type: ignore[assignment]
            loop.quit()

        signal_id = bus.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Request",
            "Response",
            handle,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )

        def on_timeout() -> bool:
            timed_out["value"] = True
            loop.quit()
            return False

        timeout_id = GLib.timeout_add_seconds(self._timeout_seconds, on_timeout)
        try:
            loop.run()
        finally:
            if timeout_id:
                GLib.source_remove(timeout_id)
            bus.signal_unsubscribe(signal_id)

        return None if timed_out["value"] else picked["color"]


def load_portal_color_picker() -> WaylandPortalColorPickerService | None:
    """Return the portal color service when Gio is importable."""
    try:
        from gi.repository import Gio  # noqa: F401
    except (ImportError, ValueError):
        return None
    if not _portal_frontend_available():
        return None
    return WaylandPortalColorPickerService(picker=XdgDesktopPortalColorPicker())


def _portal_frontend_available(*, timeout_ms: int = 250) -> bool:
    try:
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", ("org.freedesktop.portal.Desktop",)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            timeout_ms,
            None,
        )
    except GLib.Error:
        return False
    return bool(result.unpack()[0])


def _float_channel_to_byte(value: float) -> int:
    clamped = max(0.0, min(1.0, float(value)))
    return round(clamped * 255)
