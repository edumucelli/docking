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

"""StatusNotifierItem/AppIndicator state and D-Bus helpers."""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.systemtray import meta
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(get_logger(name="systemtray"), applet_id=meta.id)

DBUS_SERVICE = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
DBUS_IFACE = "org.freedesktop.DBus"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

WATCHER_SERVICE = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
DEFAULT_ITEM_PATH = "/StatusNotifierItem"
METHOD_TIMEOUT_MS = 1200

WATCHER_INTROSPECTION_XML = f"""
<node>
  <interface name="{WATCHER_IFACE}">
    <method name="RegisterStatusNotifierItem">
      <arg name="service" type="s" direction="in"/>
    </method>
    <method name="RegisterStatusNotifierHost">
      <arg name="service" type="s" direction="in"/>
    </method>
    <signal name="StatusNotifierItemRegistered">
      <arg name="service" type="s"/>
    </signal>
    <signal name="StatusNotifierItemUnregistered">
      <arg name="service" type="s"/>
    </signal>
    <signal name="StatusNotifierHostRegistered"/>
    <signal name="StatusNotifierHostUnregistered"/>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
  </interface>
</node>
"""


@dataclass(frozen=True, slots=True)
class RegisteredItemAddress:
    """Resolved D-Bus service/path pair for one StatusNotifier item."""

    service: str
    path: str

    @property
    def identifier(self) -> str:
        return f"{self.service}{self.path}"


@dataclass(frozen=True, slots=True)
class TrayIconPixmap:
    """Raw StatusNotifier icon pixmap converted to RGBA bytes."""

    width: int
    height: int
    rgba: bytes


@dataclass(frozen=True, slots=True)
class TrayItem:
    """Applet-facing snapshot of one StatusNotifier item."""

    identifier: str
    service: str
    path: str
    title: str
    status: str
    category: str
    icon_name: str
    attention_icon_name: str
    overlay_icon_name: str
    icon_theme_path: str
    icon_pixmap: TrayIconPixmap | None
    menu_path: str
    tooltip_title: str
    tooltip_text: str
    item_is_menu: bool

    @property
    def display_title(self) -> str:
        return self.title or self.tooltip_title or self.service

    @property
    def effective_icon_name(self) -> str:
        if self.status.casefold() == "needsattention" and self.attention_icon_name:
            return self.attention_icon_name
        return self.icon_name or self.attention_icon_name or self.overlay_icon_name


@dataclass(frozen=True, slots=True)
class StatusTrayState:
    """Current applet state."""

    available: bool
    watcher_mode: str
    items: tuple[TrayItem, ...]
    error: str = ""
    legacy_tray_owner: str = ""


def unavailable_state(error: str = "") -> StatusTrayState:
    return StatusTrayState(
        available=False,
        watcher_mode="unavailable",
        items=(),
        error=error,
        legacy_tray_owner=legacy_tray_owner_name(),
    )


def tooltip_text(state: StatusTrayState) -> str:
    if not state.available:
        if state.error:
            return _("System Tray: {error}").format(error=state.error)
        return _("System Tray: D-Bus unavailable")
    if not state.items:
        if state.legacy_tray_owner:
            return _("System Tray: legacy tray owned by {owner}").format(
                owner=state.legacy_tray_owner
            )
        if state.watcher_mode == "host":
            return _("System Tray: waiting for tray apps")
        return _("System Tray: no tray apps")
    lines = [_("System Tray: {n} item(s)").format(n=len(state.items))]
    for item in state.items[:6]:
        lines.append(f"- {item.display_title}")
    if len(state.items) > 6:
        lines.append(_("and {n} more").format(n=len(state.items) - 6))
    return "\n".join(lines)


def parse_registered_item(
    value: str,
    *,
    default_service: str | None = None,
) -> RegisteredItemAddress | None:
    """Parse watcher item identifiers into service/path pairs.

    Common forms are ``org.example.App``, ``org.example.App/StatusNotifierItem``,
    ``:1.42/StatusNotifierItem``, or a path passed during registration, in which
    case the D-Bus sender becomes the service.
    """
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("/"):
        if not default_service:
            return None
        return RegisteredItemAddress(service=default_service, path=raw)
    if "/" not in raw:
        return RegisteredItemAddress(service=raw, path=DEFAULT_ITEM_PATH)
    service, suffix = raw.split("/", 1)
    if not service:
        return None
    return RegisteredItemAddress(service=service, path=f"/{suffix}")


def tray_item_from_properties(
    *,
    address: RegisteredItemAddress,
    properties: dict[str, Any],
) -> TrayItem:
    tooltip_title, tooltip_text_value = _tooltip_parts(properties.get("ToolTip"))
    pixmap = _best_icon_pixmap(properties.get("IconPixmap"))
    return TrayItem(
        identifier=address.identifier,
        service=address.service,
        path=address.path,
        title=str(properties.get("Title") or properties.get("Id") or ""),
        status=str(properties.get("Status") or "Passive"),
        category=str(properties.get("Category") or ""),
        icon_name=str(properties.get("IconName") or ""),
        attention_icon_name=str(properties.get("AttentionIconName") or ""),
        overlay_icon_name=str(properties.get("OverlayIconName") or ""),
        icon_theme_path=str(properties.get("IconThemePath") or ""),
        icon_pixmap=pixmap,
        menu_path=str(properties.get("Menu") or ""),
        tooltip_title=tooltip_title,
        tooltip_text=tooltip_text_value,
        item_is_menu=bool(properties.get("ItemIsMenu") or False),
    )


class StatusNotifierBackend:
    """StatusNotifier watcher/client backend.

    The backend consumes an existing watcher when the desktop provides one. In
    minimal sessions it owns the watcher name and accepts registrations from
    tray apps started after Docking.
    """

    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._host: _StatusNotifierWatcherHost | None = None
        self._last_items: dict[str, TrayItem] = {}
        self._registered_with_existing_watcher = False

    def close(self) -> None:
        if self._host is not None:
            self._host.close()
            self._host = None

    def get_state(self) -> StatusTrayState:
        try:
            bus = self._connection()
        except GLib.Error as exc:
            log.debug("StatusNotifier session bus unavailable: %s", exc)
            return unavailable_state(_("session bus unavailable"))

        try:
            if self._host is not None:
                addresses = self._host.registered_addresses()
                mode = "host"
            elif _name_has_owner(bus=bus, name=WATCHER_SERVICE):
                self._register_with_existing_watcher(bus=bus)
                addresses = self._existing_watcher_addresses(bus=bus)
                mode = "watcher"
            else:
                host = self._ensure_host(bus=bus)
                addresses = host.registered_addresses()
                mode = "host"
        except GLib.Error as exc:
            log.debug("StatusNotifier watcher unavailable: %s", exc)
            return unavailable_state(str(exc))

        items: list[TrayItem] = []
        for address in addresses:
            item = _read_item(bus=bus, address=address)
            if item is not None:
                items.append(item)

        self._last_items = {item.identifier: item for item in items}
        items.sort(key=lambda item: item.display_title.casefold())
        return StatusTrayState(
            available=True,
            watcher_mode=mode,
            items=tuple(items),
            legacy_tray_owner=legacy_tray_owner_name() if not items else "",
        )

    def activate(self, identifier: str) -> bool:
        item = self._last_items.get(identifier)
        if item is None:
            return False
        method = "ContextMenu" if item.item_is_menu else "Activate"
        return self._call_item_method(item=item, method=method)

    def context_menu(self, identifier: str) -> bool:
        item = self._last_items.get(identifier)
        if item is None:
            return False
        return self._call_item_method(item=item, method="ContextMenu")

    def menu_client(self, identifier: str):
        item = self._last_items.get(identifier)
        if item is None or not item.menu_path:
            return None
        from docking.applets.systemtray.dbusmenu import DBusMenuClient

        return DBusMenuClient(
            bus=self._connection(),
            service=item.service,
            path=item.menu_path,
        )

    def _connection(self) -> Gio.DBusConnection:
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self._bus

    def _existing_watcher_addresses(
        self,
        *,
        bus: Gio.DBusConnection,
    ) -> tuple[RegisteredItemAddress, ...]:
        result = bus.call_sync(
            WATCHER_SERVICE,
            WATCHER_PATH,
            PROPERTIES_IFACE,
            "Get",
            GLib.Variant("(ss)", (WATCHER_IFACE, "RegisteredStatusNotifierItems")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            METHOD_TIMEOUT_MS,
            None,
        )
        values = _unpack_variant(result)[0]
        if isinstance(values, GLib.Variant):
            values = _unpack_variant(values)
        addresses = [
            parsed
            for value in values
            if isinstance(value, str)
            for parsed in (parse_registered_item(value),)
            if parsed is not None
        ]
        return tuple(addresses)

    def _register_with_existing_watcher(self, *, bus: Gio.DBusConnection) -> None:
        if self._registered_with_existing_watcher:
            return
        try:
            bus.call_sync(
                WATCHER_SERVICE,
                WATCHER_PATH,
                WATCHER_IFACE,
                "RegisterStatusNotifierHost",
                GLib.Variant("(s)", (bus.get_unique_name() or "org.docking.Docking",)),
                None,
                Gio.DBusCallFlags.NONE,
                METHOD_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            log.debug("Failed to register StatusNotifier host: %s", exc)
        else:
            self._registered_with_existing_watcher = True

    def _ensure_host(self, *, bus: Gio.DBusConnection) -> _StatusNotifierWatcherHost:
        if self._host is None:
            self._host = _StatusNotifierWatcherHost(bus=bus)
        return self._host

    def _call_item_method(self, *, item: TrayItem, method: str) -> bool:
        try:
            self._connection().call_sync(
                item.service,
                item.path,
                ITEM_IFACE,
                method,
                GLib.Variant("(ii)", (0, 0)),
                None,
                Gio.DBusCallFlags.NONE,
                METHOD_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            log.debug(
                "StatusNotifier %s failed for %s: %s",
                method,
                item.identifier,
                exc,
            )
            return False
        return True


class _StatusNotifierWatcherHost:
    """Minimal watcher implementation for sessions with no existing watcher."""

    def __init__(self, *, bus: Gio.DBusConnection) -> None:
        self._bus = bus
        self._registered: dict[str, RegisteredItemAddress] = {}
        self._item_senders: dict[str, str] = {}
        self._items_by_sender: dict[str, set[str]] = {}
        self._hosts: set[str] = set()
        node = Gio.DBusNodeInfo.new_for_xml(WATCHER_INTROSPECTION_XML)
        self._iface = node.interfaces[0]
        self._registration_id = bus.register_object(
            WATCHER_PATH,
            self._iface,
            self._on_method_call,
            self._get_property,
            None,
        )
        self._name_owner_subscription_id = bus.signal_subscribe(
            DBUS_SERVICE,
            DBUS_IFACE,
            "NameOwnerChanged",
            DBUS_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_name_owner_changed,
            None,
        )
        self._owner_id = Gio.bus_own_name_on_connection(
            bus,
            WATCHER_SERVICE,
            Gio.BusNameOwnerFlags.NONE,
            None,
            None,
        )

    def close(self) -> None:
        if self._name_owner_subscription_id:
            self._bus.signal_unsubscribe(self._name_owner_subscription_id)
            self._name_owner_subscription_id = 0
        if self._registration_id:
            self._bus.unregister_object(self._registration_id)
            self._registration_id = 0
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0

    def registered_addresses(self) -> tuple[RegisteredItemAddress, ...]:
        return tuple(self._registered.values())

    def _get_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        property_name: str,
    ) -> GLib.Variant | None:
        if property_name == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", tuple(self._registered))
        if property_name == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", bool(self._hosts))
        if property_name == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def _on_method_call(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method_name == "RegisterStatusNotifierHost":
            self._hosts.add(sender)
            invocation.return_value(None)
            self._emit_host_registered(connection=connection)
            return
        if method_name != "RegisterStatusNotifierItem":
            invocation.return_dbus_error(
                "org.docking.Docking.StatusNotifier.UnknownMethod",
                f"Unknown method: {method_name}",
            )
            return

        service = str(_unpack_variant(parameters)[0])
        address = parse_registered_item(service, default_service=sender)
        if address is None:
            invocation.return_dbus_error(
                "org.docking.Docking.StatusNotifier.InvalidItem",
                f"Invalid StatusNotifier item: {service}",
            )
            return
        if address.identifier not in self._registered:
            self._registered[address.identifier] = address
            self._item_senders[address.identifier] = sender
            self._items_by_sender.setdefault(sender, set()).add(address.identifier)
            connection.emit_signal(
                None,
                WATCHER_PATH,
                WATCHER_IFACE,
                "StatusNotifierItemRegistered",
                GLib.Variant("(s)", (address.identifier,)),
            )
        invocation.return_value(None)

    def _on_name_owner_changed(
        self,
        connection: Gio.DBusConnection,
        _sender_name: str,
        _object_path: str,
        _interface_name: str,
        _signal_name: str,
        parameters: GLib.Variant,
        _user_data: object,
    ) -> None:
        name, _old_owner, new_owner = _unpack_variant(parameters)
        if new_owner:
            return
        name = str(name)
        if name in self._hosts:
            self._hosts.remove(name)
            connection.emit_signal(
                None,
                WATCHER_PATH,
                WATCHER_IFACE,
                "StatusNotifierHostUnregistered",
                None,
            )
        for identifier in tuple(self._items_by_sender.pop(name, ())):
            self._registered.pop(identifier, None)
            self._item_senders.pop(identifier, None)
            connection.emit_signal(
                None,
                WATCHER_PATH,
                WATCHER_IFACE,
                "StatusNotifierItemUnregistered",
                GLib.Variant("(s)", (identifier,)),
            )

    def _emit_host_registered(self, *, connection: Gio.DBusConnection) -> None:
        connection.emit_signal(
            None,
            WATCHER_PATH,
            WATCHER_IFACE,
            "StatusNotifierHostRegistered",
            None,
        )


def _name_has_owner(*, bus: Gio.DBusConnection, name: str) -> bool:
    result = bus.call_sync(
        DBUS_SERVICE,
        DBUS_PATH,
        DBUS_IFACE,
        "NameHasOwner",
        GLib.Variant("(s)", (name,)),
        GLib.VariantType.new("(b)"),
        Gio.DBusCallFlags.NONE,
        METHOD_TIMEOUT_MS,
        None,
    )
    return bool(_unpack_variant(result)[0])


def _read_item(
    *,
    bus: Gio.DBusConnection,
    address: RegisteredItemAddress,
) -> TrayItem | None:
    try:
        result = bus.call_sync(
            address.service,
            address.path,
            PROPERTIES_IFACE,
            "GetAll",
            GLib.Variant("(s)", (ITEM_IFACE,)),
            GLib.VariantType.new("(a{sv})"),
            Gio.DBusCallFlags.NONE,
            METHOD_TIMEOUT_MS,
            None,
        )
    except GLib.Error as exc:
        log.debug("Failed to read StatusNotifier item %s: %s", address.identifier, exc)
        return None
    properties = _unpack_variant(result)[0]
    if not isinstance(properties, dict):
        return None
    return tray_item_from_properties(
        address=address,
        properties={
            str(key): _unpack_variant(value) for key, value in properties.items()
        },
    )


def _tooltip_parts(value: object) -> tuple[str, str]:
    if isinstance(value, GLib.Variant):
        value = _unpack_variant(value)
    if isinstance(value, (tuple, list)) and len(value) >= 4:
        return str(value[2] or ""), str(value[3] or "")
    return "", ""


def _best_icon_pixmap(value: object) -> TrayIconPixmap | None:
    value = _unpack_variant(value)
    if not isinstance(value, (tuple, list)):
        return None

    pixmaps: list[TrayIconPixmap] = []
    for raw_pixmap in value:
        raw_pixmap = _unpack_variant(raw_pixmap)
        if not isinstance(raw_pixmap, (tuple, list)) or len(raw_pixmap) != 3:
            continue
        width, height, payload = raw_pixmap
        try:
            width = int(width)
            height = int(height)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        argb = _bytes_from_dbus_array(payload)
        if len(argb) < width * height * 4:
            continue
        pixmaps.append(
            TrayIconPixmap(
                width=width,
                height=height,
                rgba=_argb_to_rgba(argb[: width * height * 4]),
            )
        )
    if not pixmaps:
        return None
    return max(pixmaps, key=lambda pixmap: pixmap.width * pixmap.height)


def _bytes_from_dbus_array(value: object) -> bytes:
    value = _unpack_variant(value)
    if isinstance(value, bytes):
        return value
    if isinstance(value, (tuple, list)):
        return bytes(int(byte) & 0xFF for byte in value)
    return b""


def _argb_to_rgba(argb: bytes) -> bytes:
    rgba = bytearray(len(argb))
    for index in range(0, len(argb), 4):
        alpha = argb[index]
        red = argb[index + 1]
        green = argb[index + 2]
        blue = argb[index + 3]
        rgba[index : index + 4] = bytes((red, green, blue, alpha))
    return bytes(rgba)


def legacy_tray_owner_name() -> str:
    """Return the XEmbed system tray selection owner name on X11, if any."""
    x11 = ctypes.util.find_library("X11")
    if not x11:
        return ""

    try:
        lib = ctypes.CDLL(x11)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        lib.XDefaultScreen.restype = ctypes.c_int
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XGetSelectionOwner.restype = ctypes.c_ulong
        lib.XFetchName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_char_p),
        ]
        lib.XFetchName.restype = ctypes.c_int
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    except (AttributeError, OSError):
        return ""

    display = lib.XOpenDisplay(None)
    if not display:
        return ""

    try:
        screen = lib.XDefaultScreen(display)
        selection = lib.XInternAtom(
            display,
            f"_NET_SYSTEM_TRAY_S{screen}".encode(),
            True,
        )
        if not selection:
            return ""
        owner = lib.XGetSelectionOwner(display, selection)
        if not owner:
            return ""
        name = ctypes.c_char_p()
        if lib.XFetchName(display, owner, ctypes.byref(name)) and name.value:
            value = name.value.decode(errors="replace")
            lib.XFree(name)
            return value
        return f"0x{owner:x}"
    finally:
        lib.XCloseDisplay(display)


def _unpack_variant(value: object) -> Any:
    if isinstance(value, GLib.Variant):
        return _unpack_variant(value.unpack())
    if isinstance(value, dict):
        return {key: _unpack_variant(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return tuple(_unpack_variant(item) for item in value)
    if isinstance(value, list):
        return [_unpack_variant(item) for item in value]
    return value
