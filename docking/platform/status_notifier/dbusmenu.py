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

"""DBusMenu client and models for StatusNotifier/AppIndicator menus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

DBUSMENU_IFACE = "com.canonical.dbusmenu"
DBUSMENU_METHOD_TIMEOUT_MS = 1400


@dataclass(frozen=True, slots=True)
class DBusMenuItem:
    """One menu item from a DBusMenu layout tree."""

    item_id: int
    label: str
    enabled: bool = True
    visible: bool = True
    item_type: str = ""
    icon_name: str = ""
    icon_data: bytes = b""
    toggle_type: str = ""
    toggle_state: int = -1
    children: tuple[DBusMenuItem, ...] = ()

    @property
    def is_separator(self) -> bool:
        return self.item_type == "separator"


@dataclass(frozen=True, slots=True)
class DBusMenuLayout:
    """Parsed DBusMenu layout."""

    revision: int
    root: DBusMenuItem


class DBusMenuClient:
    """Small synchronous DBusMenu client used by the tray popup/menu UI."""

    def __init__(
        self,
        *,
        bus: Gio.DBusConnection,
        service: str,
        path: str,
    ) -> None:
        self._bus = bus
        self._service = service
        self._path = path

    def get_layout(self) -> DBusMenuLayout | None:
        try:
            result = self._bus.call_sync(
                self._service,
                self._path,
                DBUSMENU_IFACE,
                "GetLayout",
                GLib.Variant("(iias)", (0, -1, [])),
                GLib.VariantType.new("(u(ia{sv}av))"),
                Gio.DBusCallFlags.NONE,
                DBUSMENU_METHOD_TIMEOUT_MS,
                None,
            )
        except GLib.Error:
            return None
        revision, raw_root = _unpack_variant(result)
        root = parse_menu_node(raw_root)
        if root is None:
            return None
        return DBusMenuLayout(revision=int(revision), root=root)

    def about_to_show(self, item_id: int) -> bool:
        try:
            result = self._bus.call_sync(
                self._service,
                self._path,
                DBUSMENU_IFACE,
                "AboutToShow",
                GLib.Variant("(i)", (item_id,)),
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE,
                DBUSMENU_METHOD_TIMEOUT_MS,
                None,
            )
        except GLib.Error:
            return False
        unpacked = _unpack_variant(result)
        return bool(unpacked[0]) if isinstance(unpacked, (tuple, list)) else False

    def event(self, item_id: int, event_id: str = "clicked") -> bool:
        try:
            self._bus.call_sync(
                self._service,
                self._path,
                DBUSMENU_IFACE,
                "Event",
                GLib.Variant("(isvu)", (item_id, event_id, GLib.Variant("s", ""), 0)),
                None,
                Gio.DBusCallFlags.NONE,
                DBUSMENU_METHOD_TIMEOUT_MS,
                None,
            )
        except GLib.Error:
            return False
        return True


def parse_menu_node(raw: object) -> DBusMenuItem | None:
    """Parse one DBusMenu ``(ia{sv}av)`` node after GLib unpacking.

    Children are parsed recursively and malformed child nodes are dropped.
    Real tray apps vary in how strictly they follow DBusMenu, so callers get a
    usable partial menu instead of losing the whole tree.
    """
    raw = _unpack_variant(raw)
    if not isinstance(raw, (tuple, list)) or len(raw) != 3:
        return None
    item_id, properties, children = raw
    if not isinstance(properties, dict):
        properties = {}
    parsed_children = tuple(
        child
        for raw_child in children
        for child in (parse_menu_node(raw_child),)
        if child is not None
    )
    return DBusMenuItem(
        item_id=int(item_id),
        label=_clean_label(str(properties.get("label") or "")),
        enabled=bool(properties.get("enabled", True)),
        visible=bool(properties.get("visible", True)),
        item_type=str(properties.get("type") or ""),
        icon_name=str(properties.get("icon-name") or ""),
        icon_data=_icon_data(properties.get("icon-data")),
        toggle_type=str(properties.get("toggle-type") or ""),
        toggle_state=int(properties.get("toggle-state", -1)),
        children=parsed_children,
    )


def _clean_label(label: str) -> str:
    """Convert DBusMenu mnemonic underscores into GTK labels."""
    return label.replace("__", "\0").replace("_", "").replace("\0", "_")


def _icon_data(value: object) -> bytes:
    value = _unpack_variant(value)
    if isinstance(value, bytes):
        return value
    if isinstance(value, list):
        return bytes(int(byte) & 0xFF for byte in value)
    return b""


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
