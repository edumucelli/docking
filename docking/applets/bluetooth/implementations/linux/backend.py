"""Linux Bluetooth integration through BlueZ D-Bus."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.bluetooth.backend import BluetoothBackend
from docking.applets.bluetooth.implementations.unix import desktop as unix_desktop
from docking.applets.bluetooth.state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    _device_sort_key,
    adapter_from_state,
    unavailable_state,
)
from docking.log import get_logger, with_context

log = with_context(get_logger("bluetooth.linux"), applet_id="bluetooth")

BLUEZ_SERVICE = "org.bluez"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
DBUS_SERVICE = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
DBUS_IFACE = "org.freedesktop.DBus"

ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
BATTERY_IFACE = "org.bluez.Battery1"
BLUEZ_ERR_NOT_READY = "org.bluez.Error.NotReady"
# Power-off retries: BlueZ returns Busy while discovery/connections are active.
POWER_OFF_RETRY_COUNT = 5  # attempts before disconnecting devices
POWER_OFF_FINAL_RETRY_COUNT = 3  # attempts after disconnect cleanup
POWER_RETRY_SLEEP_S = 0.15  # pause between retry attempts
# Settlement timeouts: wait for BlueZ to propagate state after stop/disconnect.
DISCOVERY_STOP_SETTLE_TIMEOUT_S = 1.2  # after StopDiscovery call
DISCOVERY_DISCONNECT_SETTLE_TIMEOUT_S = 2.5  # after disconnecting all devices
DISCOVERY_POLL_INTERVAL_S = 0.1
DBUS_NAME_HAS_OWNER_TIMEOUT_MS = 1200
DBUS_GET_MANAGED_OBJECTS_TIMEOUT_MS = 1800
DBUS_SET_PROPERTY_TIMEOUT_MS = 1800
DBUS_METHOD_TIMEOUT_MS = 5000
BLUETOOTHCTL_POWER_TIMEOUT_S = 8


class LinuxBluetoothBackend(BluetoothBackend):
    """BlueZ system bus backend.

    A few BlueZ semantics are important for callers:
    - Discovery is session-owned per DBus client.
      `Adapter1.Discovering == true` does not imply this process owns
      a discovery session.
    - `StopDiscovery` may return `org.bluez.Error.NotReady` when no session is
      owned by this process, even if another process is actively discovering.
    - Power-off can fail with `org.bluez.Error.Busy` when discovery or other
      controller activity is still active.
    """

    def desktop_actions(self):
        return unix_desktop.desktop_actions()

    def open_desktop_action(self, action):
        return unix_desktop.open_desktop_action(action)

    def close(self) -> None:
        # The applet releases its owned BlueZ discovery sessions before closing.
        return None

    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._dbus_proxy: Gio.DBusProxy | None = None
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._dbus_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                DBUS_SERVICE,
                DBUS_PATH,
                DBUS_IFACE,
                None,
            )
        except GLib.Error as exc:
            log.bind(action="bus_init").warning(
                "Failed to connect system bus: %s",
                exc,
            )
            self._bus = None
            self._dbus_proxy = None

    def get_state(self, active_adapter_path: str | None = None) -> BluetoothState:
        if not self._has_bluez_owner():
            return unavailable_state(error="BlueZ service unavailable")

        objects = self._get_managed_objects()
        if objects is None:
            return unavailable_state(error="BlueZ query failed")

        adapters, devices = _parse_objects(objects=objects)
        if not adapters:
            return unavailable_state(error="No Bluetooth adapter found")

        selected = adapter_from_state(
            state=BluetoothState(
                available=True,
                adapters=tuple(adapters),
                devices=tuple(devices),
                active_adapter_path=active_adapter_path or "",
            ),
            preferred_path=active_adapter_path,
        )
        return BluetoothState(
            available=True,
            adapters=tuple(adapters),
            devices=tuple(devices),
            active_adapter_path=selected or adapters[0].path,
        )

    def set_adapter_power(self, adapter_path: str, powered: bool) -> bool:
        # BlueZ can return org.bluez.Error.Busy while discovery is running.
        # The power-off path is defensive:
        # 1) ask to stop discovery (best-effort),
        # 2) if adapter is still discovering, treat this as likely external
        #    ownership and fail fast after one property set + bluetoothctl
        #    fallback attempt,
        # 3) if not discovering, retry with additional disconnect cleanup.
        if not powered:
            self.stop_discovery(adapter_path, quiet=True)
            self._wait_for_discovery_state(
                adapter_path=adapter_path,
                target_discovering=False,
                timeout_s=DISCOVERY_STOP_SETTLE_TIMEOUT_S,
            )
            props = self._get_adapter_props(adapter_path=adapter_path)
            if isinstance(props, dict) and _as_bool(props.get("Discovering")):
                # Another DBus client is likely holding an active discovery session.
                # Repeating many set attempts here only produces Busy churn.
                if self._set_property(
                    path=adapter_path,
                    interface=ADAPTER_IFACE,
                    property_name="Powered",
                    signature="b",
                    value=False,
                    quiet=True,
                ):
                    return True
                if self._set_power_with_bluetoothctl(powered=False):
                    return True
                log.bind(action="set_Powered").debug(
                    "Power off blocked while discovery is externally owned "
                    "(adapter still discovering)."
                )
                return False
            for _attempt in range(POWER_OFF_RETRY_COUNT):
                if self._set_property(
                    path=adapter_path,
                    interface=ADAPTER_IFACE,
                    property_name="Powered",
                    signature="b",
                    value=False,
                    quiet=True,
                ):
                    return True
                time.sleep(POWER_RETRY_SLEEP_S)

            self._disconnect_connected_devices(adapter_path=adapter_path)
            self.stop_discovery(adapter_path, quiet=True)
            self._wait_for_discovery_state(
                adapter_path=adapter_path,
                target_discovering=False,
                timeout_s=DISCOVERY_DISCONNECT_SETTLE_TIMEOUT_S,
            )
            for _attempt in range(POWER_OFF_FINAL_RETRY_COUNT):
                if self._set_property(
                    path=adapter_path,
                    interface=ADAPTER_IFACE,
                    property_name="Powered",
                    signature="b",
                    value=False,
                    quiet=True,
                ):
                    return True
                time.sleep(POWER_RETRY_SLEEP_S)
            if self._set_power_with_bluetoothctl(powered=False):
                return True
            log.bind(action="set_Powered").debug(
                "Power off failed after retries/fallback "
                "(Busy/NotReady likely external scan)",
            )
            return False

        if self._set_property(
            path=adapter_path,
            interface=ADAPTER_IFACE,
            property_name="Powered",
            signature="b",
            value=True,
            quiet=True,
        ):
            return True
        if self._set_power_with_bluetoothctl(powered=True):
            return True
        log.bind(action="set_Powered").debug("Power on failed after retries/fallback")
        return False

    def start_discovery(self, adapter_path: str) -> bool:
        return self._call_method(
            path=adapter_path,
            interface=ADAPTER_IFACE,
            method="StartDiscovery",
        )

    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool:
        # NotReady is expected when this process does not own a discovery
        # session. Treat it as a benign "already stopped for this client".
        return self._call_method(
            path=adapter_path,
            interface=ADAPTER_IFACE,
            method="StopDiscovery",
            quiet=quiet,
            tolerate_errors=(BLUEZ_ERR_NOT_READY,),
        )

    def connect_device(self, device_path: str) -> bool:
        return self._call_method(
            path=device_path,
            interface=DEVICE_IFACE,
            method="Connect",
        )

    def disconnect_device(self, device_path: str) -> bool:
        return self._call_method(
            path=device_path,
            interface=DEVICE_IFACE,
            method="Disconnect",
        )

    def pair_device(
        self,
        device_path: str,
        address: str = "",
        timeout_s: int = 20,
    ) -> bool:
        if self._call_method(path=device_path, interface=DEVICE_IFACE, method="Pair"):
            return True
        return self._pair_with_bluetoothctl(address=address, timeout_s=timeout_s)

    def remove_device(self, adapter_path: str, device_path: str) -> bool:
        return self._call_method(
            path=adapter_path,
            interface=ADAPTER_IFACE,
            method="RemoveDevice",
            parameters=GLib.Variant("(o)", (device_path,)),
        )

    def set_trusted(self, device_path: str, trusted: bool) -> bool:
        return self._set_property(
            path=device_path,
            interface=DEVICE_IFACE,
            property_name="Trusted",
            signature="b",
            value=bool(trusted),
        )

    def _has_bluez_owner(self) -> bool:
        if self._dbus_proxy is None:
            return False
        try:
            result = self._dbus_proxy.call_sync(
                "NameHasOwner",
                GLib.Variant("(s)", (BLUEZ_SERVICE,)),
                Gio.DBusCallFlags.NONE,
                DBUS_NAME_HAS_OWNER_TIMEOUT_MS,
                None,
            )
            unpacked = result.unpack() if result is not None else ()
            return bool(unpacked[0]) if unpacked else False
        except GLib.Error as exc:
            log.debug("BlueZ NameHasOwner failed: %s", exc)
            return False

    def _get_managed_objects(self) -> dict[str, Any] | None:
        if self._bus is None:
            return None
        try:
            result = self._bus.call_sync(
                BLUEZ_SERVICE,
                "/",
                OBJECT_MANAGER_IFACE,
                "GetManagedObjects",
                None,
                GLib.VariantType("(a{oa{sa{sv}}})"),
                Gio.DBusCallFlags.NONE,
                DBUS_GET_MANAGED_OBJECTS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            log.bind(action="get_managed_objects").debug("BlueZ query failed: %s", exc)
            return None

        unpacked = result.unpack() if result is not None else ()
        if not unpacked:
            return {}
        return _unpack(unpacked[0])

    def _call_method(
        self,
        *,
        path: str,
        interface: str,
        method: str,
        parameters: GLib.Variant | None = None,
        quiet: bool = False,
        tolerate_errors: tuple[str, ...] = (),
    ) -> bool:
        if self._bus is None:
            return False
        try:
            self._bus.call_sync(
                BLUEZ_SERVICE,
                path,
                interface,
                method,
                parameters,
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_METHOD_TIMEOUT_MS,
                None,
            )
            return True
        except GLib.Error as exc:
            # Some BlueZ method errors are expected in normal multi-client use.
            # For those, callers can pass tolerated error tokens and we return
            # success to keep higher-level state transitions quiet and stable.
            if any(token in str(exc) for token in tolerate_errors):
                return True
            if not quiet:
                log.bind(action=method, object_path=path).debug(
                    "BlueZ call failed: %s",
                    exc,
                )
            return False

    def _set_property(
        self,
        *,
        path: str,
        interface: str,
        property_name: str,
        signature: str,
        value: Any,
        quiet: bool = False,
    ) -> bool:
        if self._bus is None:
            return False
        try:
            self._bus.call_sync(
                BLUEZ_SERVICE,
                path,
                PROPERTIES_IFACE,
                "Set",
                GLib.Variant(
                    "(ssv)",
                    (interface, property_name, GLib.Variant(signature, value)),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_SET_PROPERTY_TIMEOUT_MS,
                None,
            )
            return True
        except GLib.Error as exc:
            if not quiet:
                log.bind(action=f"set_{property_name}", object_path=path).debug(
                    "BlueZ set failed: %s",
                    exc,
                )
            return False

    def _pair_with_bluetoothctl(self, *, address: str, timeout_s: int) -> bool:
        if not address:
            return False
        if shutil.which("bluetoothctl") is None:
            return False
        try:
            result = subprocess.run(
                ["bluetoothctl", "pair", address],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.debug("bluetoothctl pair failed for %s: %s", address, exc)
            return False
        text = f"{result.stdout}\n{result.stderr}".lower()
        if "failed" in text or "not available" in text:
            return False
        return result.returncode == 0

    def _disconnect_connected_devices(self, *, adapter_path: str) -> None:
        objects = self._get_managed_objects()
        if not isinstance(objects, dict):
            return
        for path, ifaces in objects.items():
            if not isinstance(ifaces, dict):
                continue
            props = ifaces.get(DEVICE_IFACE)
            if not isinstance(props, dict):
                continue
            if _as_str(props.get("Adapter")) != adapter_path:
                continue
            if not _as_bool(props.get("Connected")):
                continue
            self._call_method(path=path, interface=DEVICE_IFACE, method="Disconnect")

    def _wait_for_discovery_state(
        self,
        *,
        adapter_path: str,
        target_discovering: bool,
        timeout_s: float,
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            props = self._get_adapter_props(adapter_path=adapter_path)
            if props is None:
                return False
            discovering = _as_bool(props.get("Discovering"))
            if discovering == target_discovering:
                return True
            time.sleep(DISCOVERY_POLL_INTERVAL_S)
        return False

    def _get_adapter_props(self, *, adapter_path: str) -> dict[str, Any] | None:
        objects = self._get_managed_objects()
        if not isinstance(objects, dict):
            return None
        ifaces = objects.get(adapter_path)
        if not isinstance(ifaces, dict):
            return None
        props = ifaces.get(ADAPTER_IFACE)
        if not isinstance(props, dict):
            return None
        return props

    def _set_power_with_bluetoothctl(self, *, powered: bool) -> bool:
        if shutil.which("bluetoothctl") is None:
            return False
        value = "on" if powered else "off"
        try:
            result = subprocess.run(
                ["bluetoothctl", "power", value],
                capture_output=True,
                text=True,
                timeout=BLUETOOTHCTL_POWER_TIMEOUT_S,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.debug("bluetoothctl power %s failed: %s", value, exc)
            return False
        text = f"{result.stdout}\n{result.stderr}".lower()
        if "failed" in text or "not available" in text:
            return False
        return result.returncode == 0


def _parse_objects(
    *,
    objects: dict[str, Any],
) -> tuple[list[BluetoothAdapterState], list[BluetoothDeviceState]]:
    adapters: list[BluetoothAdapterState] = []
    devices: list[BluetoothDeviceState] = []
    battery_by_path: dict[str, int] = {}

    for path, ifaces in objects.items():
        if not isinstance(ifaces, dict):
            continue
        battery_props = ifaces.get(BATTERY_IFACE)
        if isinstance(battery_props, dict):
            percentage = _as_int(battery_props.get("Percentage"), default=-1)
            if isinstance(percentage, int) and percentage >= 0:
                battery_by_path[path] = percentage

    for path, ifaces in objects.items():
        if not isinstance(ifaces, dict):
            continue
        adapter_props = ifaces.get(ADAPTER_IFACE)
        if isinstance(adapter_props, dict):
            name = _as_str(adapter_props.get("Name"))
            alias = _as_str(adapter_props.get("Alias")) or name
            adapters.append(
                BluetoothAdapterState(
                    path=path,
                    name=name or path.rsplit("/", 1)[-1],
                    alias=alias or path.rsplit("/", 1)[-1],
                    powered=_as_bool(adapter_props.get("Powered")),
                    discovering=_as_bool(adapter_props.get("Discovering")),
                    address=_as_str(adapter_props.get("Address")),
                )
            )

        device_props = ifaces.get(DEVICE_IFACE)
        if isinstance(device_props, dict):
            adapter_path = _as_str(device_props.get("Adapter"))
            device = BluetoothDeviceState(
                path=path,
                adapter_path=adapter_path,
                name=_as_str(device_props.get("Name")),
                alias=_as_str(device_props.get("Alias")),
                address=_as_str(device_props.get("Address")),
                icon_name=_as_str(device_props.get("Icon")),
                paired=_as_bool(device_props.get("Paired")),
                trusted=_as_bool(device_props.get("Trusted")),
                connected=_as_bool(device_props.get("Connected")),
                battery_percent=battery_by_path.get(path),
                rssi=_as_int(device_props.get("RSSI"), default=None),
            )
            devices.append(device)

    adapters.sort(key=lambda adapter: adapter.path)
    devices.sort(key=_device_sort_key)
    return adapters, devices


def _unpack(value: Any) -> Any:
    if hasattr(value, "unpack"):
        try:
            return _unpack(value.unpack())
        except Exception as exc:
            log.debug("Failed to unpack Bluetooth variant-like value: %s", exc)
            return value
    if isinstance(value, dict):
        return {k: _unpack(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_unpack(v) for v in value)
    if isinstance(value, list):
        return [_unpack(v) for v in value]
    return value


def _as_str(value: Any) -> str:
    unpacked = _unpack(value)
    if unpacked is None:
        return ""
    return str(unpacked)


def _as_bool(value: Any) -> bool:
    unpacked = _unpack(value)
    if isinstance(unpacked, bool):
        return unpacked
    return False


def _as_int(value: Any, default: int | None = 0) -> int | None:
    unpacked = _unpack(value)
    if unpacked is None:
        return default
    if isinstance(unpacked, str):
        unpacked = unpacked.strip()
        if not unpacked:
            return default
    try:
        return int(unpacked)
    except (TypeError, ValueError) as exc:
        log.debug("Failed to coerce Bluetooth value %r to int: %s", unpacked, exc)
        return default
