# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Single owner for Docking's versioned session-bus interfaces."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.app_identity import application_id

log = get_logger("ipc.host")

_DBUS_NAME = "org.freedesktop.DBus"
_DBUS_PATH = "/org/freedesktop/DBus"
_DBUS_INTERFACE = "org.freedesktop.DBus"
_NAME_FLAG_DO_NOT_QUEUE = 4
_REQUEST_NAME_PRIMARY_OWNER = 1
_REQUEST_NAME_ALREADY_OWNER = 4


class BusInterface(Protocol):
    def register(self, *, connection: Gio.DBusConnection) -> None: ...

    def stop(self) -> None: ...


class DockBusHost:
    """Acquire one package-aware bus name and host all Docking interfaces."""

    def __init__(
        self,
        *,
        name: str | None = None,
        connection: Gio.DBusConnection | None = None,
    ) -> None:
        self.name = name or application_id()
        self.connection = connection
        self._acquired = False
        self._interfaces: list[BusInterface] = []

    @property
    def acquired(self) -> bool:
        return self._acquired

    def acquire(self) -> bool:
        """Acquire the bus name synchronously without queueing."""
        if self._acquired:
            return True
        try:
            if self.connection is None:
                self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = self.connection.call_sync(
                _DBUS_NAME,
                _DBUS_PATH,
                _DBUS_INTERFACE,
                "RequestName",
                GLib.Variant("(su)", (self.name, _NAME_FLAG_DO_NOT_QUEUE)),
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except Exception as exc:
            log.warning("Could not acquire session-bus name %s: %s", self.name, exc)
            return False
        code = int(reply.unpack()[0])
        self._acquired = code in {
            _REQUEST_NAME_PRIMARY_OWNER,
            _REQUEST_NAME_ALREADY_OWNER,
        }
        return self._acquired

    def register_interfaces(self, interfaces: Iterable[BusInterface]) -> None:
        if not self._acquired or self.connection is None:
            raise RuntimeError("Docking bus name is not acquired")
        for interface in interfaces:
            interface.register(connection=self.connection)
            self._interfaces.append(interface)

    def stop(self) -> None:
        for interface in reversed(self._interfaces):
            interface.stop()
        self._interfaces.clear()
        if not self._acquired or self.connection is None:
            return
        try:
            self.connection.call_sync(
                _DBUS_NAME,
                _DBUS_PATH,
                _DBUS_INTERFACE,
                "ReleaseName",
                GLib.Variant("(s)", (self.name,)),
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except Exception as exc:
            log.warning("Could not release session-bus name %s: %s", self.name, exc)
        self._acquired = False


__all__ = ["BusInterface", "DockBusHost"]
