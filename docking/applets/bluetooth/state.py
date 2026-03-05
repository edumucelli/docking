"""State and backend helpers for Bluetooth applet (BlueZ DBus)."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import gi

from docking.i18n import _

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from docking.applets.identity import AppletId
from docking.log import get_logger, with_context

_log = with_context(get_logger(name="bluetooth"), applet_id=str(AppletId.BLUETOOTH))

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
BLUEZ_ERR_BUSY = "org.bluez.Error.Busy"


@dataclass(frozen=True, slots=True)
class BluetoothAdapterState:
    path: str
    name: str
    alias: str
    powered: bool
    discovering: bool
    address: str


@dataclass(frozen=True, slots=True)
class BluetoothDeviceState:
    path: str
    adapter_path: str
    name: str
    alias: str
    address: str
    icon_name: str
    paired: bool
    trusted: bool
    connected: bool
    battery_percent: int | None
    rssi: int | None


@dataclass(frozen=True, slots=True)
class BluetoothState:
    available: bool
    adapters: tuple[BluetoothAdapterState, ...]
    devices: tuple[BluetoothDeviceState, ...]
    active_adapter_path: str
    error: str = ""


def unavailable_state(error: str = "") -> BluetoothState:
    return BluetoothState(
        available=False,
        adapters=(),
        devices=(),
        active_adapter_path="",
        error=error,
    )


def connected_count(state: BluetoothState) -> int:
    return sum(1 for device in state.devices if device.connected)


def adapter_from_state(
    state: BluetoothState,
    preferred_path: str | None,
) -> str | None:
    if not state.adapters:
        return None
    if preferred_path and any(a.path == preferred_path for a in state.adapters):
        return preferred_path
    powered = [adapter for adapter in state.adapters if adapter.powered]
    if powered:
        return powered[0].path
    return state.adapters[0].path


def build_tooltip(state: BluetoothState, active_adapter_path: str) -> str:
    if not state.available:
        if state.error:
            return _("Bluetooth: {error}").format(error=state.error)
        return _("Bluetooth: No adapter/service")

    adapter = _find_adapter(state=state, path=active_adapter_path)
    if adapter is None:
        return _("Bluetooth: No adapter/service")

    powered = _("On") if adapter.powered else _("Off")
    paired = [d for d in state.devices if d.adapter_path == adapter.path and d.paired]
    connected = [d for d in paired if d.connected]

    lines = [
        _("Bluetooth: {state}").format(state=powered),
        _("Adapter: {name}").format(name=adapter.alias or adapter.name),
        _("Connected: {n}").format(n=len(connected)),
        _("Paired: {n}").format(n=len(paired)),
        _("Discovering: {state}").format(
            state=_("Yes") if adapter.discovering else _("No")
        ),
    ]

    device_with_battery = next(
        (d for d in connected if d.battery_percent is not None),
        None,
    )
    if device_with_battery and device_with_battery.battery_percent is not None:
        lines.append(
            f"Battery: {_device_display_name(device_with_battery)} "
            f"{device_with_battery.battery_percent}%"
        )
    return "\n".join(lines)


def device_menu_label(device: BluetoothDeviceState) -> str:
    base = _device_display_name(device=device)
    tags: list[str] = []
    if device.connected:
        tags.append("Connected")
    if device.paired:
        tags.append("Paired")
    if device.battery_percent is not None:
        tags.append(f"{device.battery_percent}%")
    if not tags:
        return base
    return f"{base} ({', '.join(tags)})"


class BluezBackend:
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
            _log.bind(action="bus_init").warning(
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
                timeout_s=1.2,
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
                _log.bind(action="set_Powered").debug(
                    "Power off blocked while discovery is externally owned "
                    "(adapter still discovering)."
                )
                return False
            for _ in range(5):
                if self._set_property(
                    path=adapter_path,
                    interface=ADAPTER_IFACE,
                    property_name="Powered",
                    signature="b",
                    value=False,
                    quiet=True,
                ):
                    return True
                time.sleep(0.15)

            self._disconnect_connected_devices(adapter_path=adapter_path)
            self.stop_discovery(adapter_path, quiet=True)
            self._wait_for_discovery_state(
                adapter_path=adapter_path,
                target_discovering=False,
                timeout_s=2.5,
            )
            for _ in range(3):
                if self._set_property(
                    path=adapter_path,
                    interface=ADAPTER_IFACE,
                    property_name="Powered",
                    signature="b",
                    value=False,
                    quiet=True,
                ):
                    return True
                time.sleep(0.15)
            if self._set_power_with_bluetoothctl(powered=False):
                return True
            _log.bind(action="set_Powered").debug(
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
        _log.bind(action="set_Powered").debug("Power on failed after retries/fallback")
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
                1200,
                None,
            )
            unpacked = result.unpack() if result is not None else ()
            return bool(unpacked[0]) if unpacked else False
        except GLib.Error:
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
                1800,
                None,
            )
        except GLib.Error as exc:
            _log.bind(action="get_managed_objects").debug("BlueZ query failed: %s", exc)
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
                5000,
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
                _log.bind(action=method, object_path=path).debug(
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
                1800,
                None,
            )
            return True
        except GLib.Error as exc:
            if not quiet:
                _log.bind(action=f"set_{property_name}", object_path=path).debug(
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
        except (subprocess.TimeoutExpired, OSError):
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
            time.sleep(0.1)
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
                timeout=8,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
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


def _device_sort_key(device: BluetoothDeviceState) -> tuple[int, int, str]:
    return (
        0 if device.connected else 1,
        0 if device.paired else 1,
        _device_display_name(device=device).lower(),
    )


def _find_adapter(state: BluetoothState, path: str) -> BluetoothAdapterState | None:
    return next((adapter for adapter in state.adapters if adapter.path == path), None)


def _device_display_name(device: BluetoothDeviceState) -> str:
    return (
        device.alias or device.name or device.address or device.path.rsplit("/", 1)[-1]
    )


def _unpack(value: Any) -> Any:
    if hasattr(value, "unpack"):
        try:
            return _unpack(value.unpack())
        except Exception:
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
    try:
        return int(unpacked)
    except (TypeError, ValueError):
        return default
