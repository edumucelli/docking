"""Idle duration from a compositor's native IdleMonitor D-Bus interface."""

from __future__ import annotations

from gi.repository import Gio, GLib

from docking.platform.backends.base import IdleService


class MutterIdleService(IdleService):
    def __init__(self, proxy: Gio.DBusProxy) -> None:
        self._proxy = proxy

    @classmethod
    def connect(cls) -> MutterIdleService | None:
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                "org.gnome.Mutter.IdleMonitor",
                "/org/gnome/Mutter/IdleMonitor/Core",
                "org.gnome.Mutter.IdleMonitor",
                None,
            )
            service = cls(proxy)
            return service if service.idle_seconds() is not None else None
        except GLib.Error:
            return None

    def start(self) -> None:
        """The proxy follows service ownership."""

    def stop(self) -> None:
        """No owned subscriptions."""

    def idle_seconds(self) -> float | None:
        try:
            value = self._proxy.call_sync(
                "GetIdletime",
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                250,
                None,
            ).unpack()[0]
            return value / 1000 if type(value) is int and value >= 0 else None
        except GLib.Error:
            return None
