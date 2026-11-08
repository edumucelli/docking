"""A bounded, cancellable client of the KDE Connect session-bus API."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.kdeconnect.state import (
    BATTERY_PLUGIN,
    RING_PLUGIN,
    SHARE_PLUGIN,
    ConnectState,
    Device,
)
from docking.i18n import _
from docking.platform.environment import is_flatpak
from docking.platform.environment.flatpak import host_command

SERVICE = "org.kde.kdeconnect"
ROOT_PATH = "/modules/kdeconnect"
DAEMON_IFACE = "org.kde.kdeconnect.daemon"
DEVICE_IFACE = "org.kde.kdeconnect.device"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
METHOD_TIMEOUT_MS = 1500
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_]+$")


class ConnectError(RuntimeError):
    """A user-facing integration failure without provider internals."""


class KdeConnectBackend:
    def __init__(self, connection: Gio.DBusConnection | None = None) -> None:
        self._connection = connection
        self._bus: Gio.DBusConnection | None = None
        self._owner = ""
        self._watch = 0
        self._subscriptions: list[int] = []
        self._pending: set[Gio.Cancellable] = set()
        self._lock = threading.Lock()
        self._on_change: Callable[[], None] | None = None

    def start(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        if self._connection is None:
            self._watch = Gio.bus_watch_name(
                Gio.BusType.SESSION,
                SERVICE,
                Gio.BusNameWatcherFlags.AUTO_START,
                self._appeared,
                self._vanished,
            )
        else:
            self._watch = Gio.bus_watch_name_on_connection(
                self._connection,
                SERVICE,
                Gio.BusNameWatcherFlags.AUTO_START,
                self._appeared,
                self._vanished,
            )

    def close(self) -> None:
        self._on_change = None
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        self._clear_connection()

    def _clear_connection(self) -> None:
        if self._bus is not None:
            for subscription in self._subscriptions:
                self._bus.signal_unsubscribe(subscription)
        self._subscriptions.clear()
        with self._lock:
            self._owner = ""
            self._bus = None
            for cancellable in self._pending:
                cancellable.cancel()

    def _appeared(self, connection: Gio.DBusConnection, _name: str, owner: str) -> None:
        if self._on_change is None:
            return
        self._clear_connection()
        self._bus, self._owner = connection, owner
        for interface in (
            DAEMON_IFACE,
            DEVICE_IFACE,
            DEVICE_IFACE + ".battery",
            PROPERTIES_IFACE,
        ):
            self._subscriptions.append(
                connection.signal_subscribe(
                    owner,
                    interface,
                    None,
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._signal,
                    None,
                )
            )
        if self._on_change is not None:
            self._on_change()

    def _vanished(self, _connection: Gio.DBusConnection, _name: str) -> None:
        self._clear_connection()
        if self._on_change is not None:
            self._on_change()

    def _signal(self, *_args: Any) -> None:
        if self._on_change is not None:
            self._on_change()

    def _call(
        self,
        path: str,
        interface: str,
        method: str,
        parameters: GLib.Variant | None = None,
    ) -> Any:
        with self._lock:
            connection, owner = self._bus, self._owner
            cancellable = Gio.Cancellable()
            self._pending.add(cancellable)
        try:
            if connection is None or not owner:
                raise ConnectError(
                    _("Install and start KDE Connect to connect a phone")
                )
            result = connection.call_sync(
                owner,
                path,
                interface,
                method,
                parameters,
                None,
                Gio.DBusCallFlags.NONE,
                METHOD_TIMEOUT_MS,
                cancellable,
            )
            if owner != self._owner:
                raise ConnectError(_("KDE Connect restarted; refreshing devices"))
            return result.unpack()
        except GLib.Error:
            raise ConnectError(
                _("Could not contact KDE Connect; retry or open its settings")
            ) from None
        finally:
            with self._lock:
                self._pending.discard(cancellable)

    @staticmethod
    def _path(device_id: str) -> str:
        if not _DEVICE_ID.fullmatch(device_id):
            raise ConnectError(_("KDE Connect returned an invalid device identifier"))
        return f"{ROOT_PATH}/devices/{device_id}"

    def _properties(self, path: str, interface: str) -> dict[str, Any]:
        raw = self._call(
            path, PROPERTIES_IFACE, "GetAll", GLib.Variant("(s)", (interface,))
        )[0]
        return {
            key: value.unpack() if isinstance(value, GLib.Variant) else value
            for key, value in raw.items()
        }

    def snapshot(self) -> ConnectState:
        if not self._owner:
            return ConnectState()
        try:
            ids = self._call(
                ROOT_PATH, DAEMON_IFACE, "devices", GLib.Variant("(bb)", (False, True))
            )[0]
            devices = []
            for device_id in ids:
                path = self._path(device_id)
                properties = self._properties(path, DEVICE_IFACE)
                # The daemon query already filters paired devices. Also honor
                # the properties used by newer and older KDE Connect versions.
                if not properties.get("isPaired", properties.get("isTrusted", True)):
                    continue
                reachable = bool(properties.get("isReachable", False))
                plugins = (
                    frozenset(self._call(path, DEVICE_IFACE, "loadedPlugins")[0])
                    if reachable
                    else frozenset()
                )
                charge, charging = None, False
                if BATTERY_PLUGIN in plugins:
                    try:
                        battery = self._properties(
                            path + "/battery", DEVICE_IFACE + ".battery"
                        )
                        value = battery.get("charge")
                        if (
                            battery.get("hasBattery", True)
                            and isinstance(value, int)
                            and not isinstance(value, bool)
                            and 0 <= value <= 100
                        ):
                            charge, charging = (
                                value,
                                bool(battery.get("isCharging", False)),
                            )
                    except ConnectError:
                        pass  # Battery metadata must not hide a usable device.
                devices.append(
                    Device(
                        device_id,
                        str(properties.get("name") or device_id),
                        str(properties.get("type") or "phone"),
                        reachable,
                        plugins,
                        charge,
                        charging,
                    )
                )
            return ConnectState(
                True, tuple(sorted(devices, key=lambda device: device.name.casefold()))
            )
        except (ConnectError, TypeError, ValueError, KeyError, IndexError):
            return ConnectState(
                error=_(
                    "Could not read KDE Connect devices; retry or open its settings"
                )
            )

    def _require_plugin(self, device_id: str, plugin: str) -> str:
        path = self._path(device_id)
        properties = self._properties(path, DEVICE_IFACE)
        if not properties.get(
            "isPaired", properties.get("isTrusted", False)
        ) or not properties.get("isReachable", False):
            raise ConnectError(_("The selected device is offline or no longer paired"))
        plugins = self._call(path, DEVICE_IFACE, "loadedPlugins")[0]
        if plugin not in plugins:
            raise ConnectError(_("The selected device does not support this action"))
        return path

    def ring(self, device_id: str) -> None:
        path = self._require_plugin(device_id, RING_PLUGIN)
        self._call(path + "/findmyphone", DEVICE_IFACE + ".findmyphone", "ring")

    def send_files(self, device_id: str, paths: tuple[Path, ...]) -> None:
        path = self._require_plugin(device_id, SHARE_PLUGIN)
        for file in paths:
            if not file.is_file():
                raise ConnectError(_("A selected file is no longer available"))
            self._call(
                path + "/share",
                DEVICE_IFACE + ".share",
                "shareUrl",
                GLib.Variant("(s)", (file.as_uri(),)),
            )


def open_settings() -> bool:
    candidates = (
        ("kdeconnect-app",),
        ("kdeconnect-settings",),
        ("kcmshell6", "kcm_kdeconnect"),
        ("kcmshell5", "kcm_kdeconnect"),
    )
    for candidate in candidates:
        if is_flatpak():
            command = host_command(candidate)
        else:
            binary = shutil.which(candidate[0])
            command = [binary, *candidate[1:]] if binary else None
        if command is None:
            continue
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            continue
    return False
