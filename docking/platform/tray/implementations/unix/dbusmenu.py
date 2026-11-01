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

_LAYOUT_TYPE = GLib.VariantType.new("(u(ia{sv}av))")
_MENU_NODE_TYPE = GLib.VariantType.new("(ia{sv}av)")
_ICON_DATA_PROPERTY = "icon-data"
# Properties the model reads. Names outside this set are never touched, so a
# menu carrying large payloads under custom names costs nothing to skip.
_MENU_PROPERTY_NAMES = frozenset(
    {
        "label",
        "enabled",
        "visible",
        "type",
        "icon-name",
        _ICON_DATA_PROPERTY,
        "toggle-type",
        "toggle-state",
    }
)


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
                _LAYOUT_TYPE,
                Gio.DBusCallFlags.NONE,
                DBUSMENU_METHOD_TIMEOUT_MS,
                None,
            )
        except GLib.Error:
            return None
        if isinstance(result, GLib.Variant):
            revision = int(result.get_child_value(0).get_uint32())
            raw_root: object = result.get_child_value(1)
        else:
            # Plain Python replies: kept for tests and defensive callers.
            revision_text, raw_root = _unpack_variant(result)
            revision = int(revision_text)
        root = parse_menu_node(raw_root)
        if root is None:
            return None
        return DBusMenuLayout(revision=revision, root=root)

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
    """Parse one DBusMenu ``(ia{sv}av)`` node.

    Accepts the ``GLib.Variant`` that D-Bus delivers, which is read without
    unpacking icon byte arrays, or an already unpacked Python structure.

    Children are parsed recursively and malformed child nodes are dropped.
    Real tray apps vary in how strictly they follow DBusMenu, so callers get a
    usable partial menu instead of losing the whole tree.
    """
    node = _unbox_variant(raw)
    if node is not None:
        return _parse_variant_node(node)

    raw = _unpack_variant(raw)
    if not isinstance(raw, (tuple, list)) or len(raw) != 3:
        return None
    item_id, properties, children = raw
    if not isinstance(properties, dict):
        properties = {}
    return _build_menu_item(
        item_id=int(item_id),
        properties=properties,
        children=tuple(
            child
            for raw_child in children
            for child in (parse_menu_node(raw_child),)
            if child is not None
        ),
    )


def _parse_variant_node(node: GLib.Variant) -> DBusMenuItem | None:
    if not node.is_of_type(_MENU_NODE_TYPE):
        return None
    children_variant = node.get_child_value(2)
    return _build_menu_item(
        item_id=node.get_child_value(0).get_int32(),
        properties=_variant_properties(node.get_child_value(1)),
        children=tuple(
            child
            for index in range(children_variant.n_children())
            for child in (parse_menu_node(children_variant.get_child_value(index)),)
            if child is not None
        ),
    )


def _variant_properties(properties: GLib.Variant) -> dict[str, Any]:
    """Read the properties the model uses, without unpacking icon data.

    PyGObject's ``Variant.unpack()`` has no fast path for ``ay``: it builds a
    Python list holding one ``int`` per byte. Menu items carry their icon as an
    ``ay`` payload, so it is read straight from the variant; the other
    properties are scalars and unpack for free.
    """
    found: dict[str, Any] = {}
    for index in range(properties.n_children()):
        entry = properties.get_child_value(index)
        name = entry.get_child_value(0).get_string()
        if name not in _MENU_PROPERTY_NAMES:
            continue
        value = _unbox_variant(entry.get_child_value(1))
        if value is None:
            continue
        found[name] = (
            _variant_icon_data(value)
            if name == _ICON_DATA_PROPERTY
            else _unpack_variant(value)
        )
    return found


def _variant_icon_data(value: GLib.Variant) -> bytes:
    """Read ``icon-data`` in one C call, whatever shape the item published."""
    if value.get_type_string() == "ay":
        return value.get_data_as_bytes().get_data() or b""
    return _icon_data(_unpack_variant(value))


def _build_menu_item(
    *,
    item_id: int,
    properties: dict[str, Any],
    children: tuple[DBusMenuItem, ...],
) -> DBusMenuItem:
    """Build one item from a property mapping, and its parsed children."""
    return DBusMenuItem(
        item_id=item_id,
        label=_clean_label(str(properties.get("label") or "")),
        enabled=bool(properties.get("enabled", True)),
        visible=bool(properties.get("visible", True)),
        item_type=str(properties.get("type") or ""),
        icon_name=str(properties.get("icon-name") or ""),
        icon_data=_icon_data(properties.get("icon-data")),
        toggle_type=str(properties.get("toggle-type") or ""),
        toggle_state=int(properties.get("toggle-state", -1)),
        children=children,
    )


def _unbox_variant(value: object) -> GLib.Variant | None:
    """Return the concrete variant behind an optional ``v`` wrapper."""
    if not isinstance(value, GLib.Variant):
        return None
    while value.get_type_string() == "v":
        value = value.get_variant()
    return value


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
