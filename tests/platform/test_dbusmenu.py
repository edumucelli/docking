"""Tests for the DBusMenu client and layout parsing."""

from __future__ import annotations

from unittest.mock import Mock

from gi.repository import GLib

from docking.platform.status_notifier.dbusmenu import (
    DBusMenuClient,
    DBusMenuItem,
    parse_menu_node,
)


def _node(
    item_id: int = 0,
    *,
    label: str = "_Root",
    icon_bytes: bytes | None = None,
    children: list | None = None,
    extra: dict | None = None,
) -> GLib.Variant:
    properties: dict[str, GLib.Variant] = {
        "label": GLib.Variant("s", label),
        "enabled": GLib.Variant("b", True),
        "visible": GLib.Variant("b", True),
        "type": GLib.Variant("s", "normal"),
    }
    if icon_bytes is not None:
        properties["icon-data"] = GLib.Variant("ay", icon_bytes)
    if extra:
        properties.update(extra)
    return GLib.Variant("(ia{sv}av)", (item_id, properties, children or []))


def _reply(*children: GLib.Variant, revision: int = 7) -> GLib.Variant:
    root = (
        0,
        {
            "label": GLib.Variant("s", "_Root"),
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
            "type": GLib.Variant("s", "normal"),
        },
        list(children),
    )
    return GLib.Variant("(u(ia{sv}av))", (revision, root))


def _client(reply: object) -> DBusMenuClient:
    bus = Mock()
    bus.call_sync.return_value = reply
    return DBusMenuClient(bus=bus, service=":1.9", path="/MenuBar")


def _forbid_byte_array_unpacking(monkeypatch) -> None:
    """Fail if any ``ay`` variant is turned into a Python list of ints."""
    original = GLib.Variant.unpack

    def guarded(self):
        assert "ay" not in self.get_type_string(), "byte arrays must not be unpacked"
        return original(self)

    monkeypatch.setattr(GLib.Variant, "unpack", guarded)


class TestVariantNodeParsing:
    def test_parses_children_and_icon_data(self):
        icon = bytes(range(16))
        root = parse_menu_node(
            _reply(
                _node(1, label="_Open", icon_bytes=icon),
                _node(2, label="", extra={"type": GLib.Variant("s", "separator")}),
                _node(
                    3,
                    label="_Check",
                    extra={
                        "toggle-type": GLib.Variant("s", "checkmark"),
                        "toggle-state": GLib.Variant("i", 1),
                        "icon-name": GLib.Variant("s", "document-open"),
                    },
                ),
            ).get_child_value(1)
        )

        assert root is not None
        assert [child.label for child in root.children] == ["Open", "", "Check"]
        assert root.children[0].icon_data == icon
        assert root.children[1].is_separator is True
        assert root.children[2].toggle_type == "checkmark"
        assert root.children[2].toggle_state == 1
        assert root.children[2].icon_name == "document-open"

    def test_matches_the_plain_python_result(self):
        icon = bytes(range(64))
        variant_root = _reply(_node(1, label="_Open", icon_bytes=icon)).get_child_value(
            1
        )
        plain_root = (
            0,
            {"label": "_Root", "enabled": True, "visible": True, "type": "normal"},
            [
                (
                    1,
                    {
                        "label": "_Open",
                        "enabled": True,
                        "visible": True,
                        "type": "normal",
                        "icon-data": list(icon),
                    },
                    [],
                )
            ],
        )

        assert parse_menu_node(variant_root) == parse_menu_node(plain_root)

    def test_byte_arrays_never_reach_variant_unpack(self, monkeypatch):
        _forbid_byte_array_unpacking(monkeypatch)
        root = parse_menu_node(
            _reply(
                _node(1, label="_Open", icon_bytes=bytes(64 * 64 * 4)),
                _node(
                    2,
                    label="_Other",
                    extra={"x-custom-blob": GLib.Variant("ay", bytes(4096))},
                ),
            ).get_child_value(1)
        )

        assert root is not None
        assert len(root.children[0].icon_data) == 64 * 64 * 4
        assert root.children[1].icon_data == b""

    def test_variant_wrapper_is_unwrapped(self):
        wrapped = GLib.Variant("v", _node(1, label="_Wrapped"))
        root = parse_menu_node(
            GLib.Variant("v", _reply(_node(1, label="_Wrapped")).get_child_value(1))
        )

        assert root is not None
        assert root.children[0].label == "Wrapped"
        assert parse_menu_node(wrapped) is not None

    def test_unexpected_variant_type_is_ignored(self):
        assert parse_menu_node(GLib.Variant("s", "")) is None
        assert parse_menu_node(GLib.Variant("(iii)", (1, 2, 3))) is None
        assert parse_menu_node(GLib.Variant("v", GLib.Variant("i", 1))) is None

    def test_malformed_children_are_dropped(self):
        root = parse_menu_node(
            _reply(
                _node(1, label="_Open"),
                GLib.Variant("(iii)", (2, 3, 4)),
                GLib.Variant("v", GLib.Variant("s", "junk")),
            ).get_child_value(1)
        )

        assert root is not None
        assert [child.label for child in root.children] == ["Open"]

    def test_missing_properties_fall_back_to_defaults(self):
        node = GLib.Variant("(ia{sv}av)", (5, {}, []))
        item = parse_menu_node(node)

        assert item == DBusMenuItem(item_id=5, label="", children=())


class TestDBusMenuClient:
    def test_get_layout_reads_the_reply_without_unpacking(self, monkeypatch):
        _forbid_byte_array_unpacking(monkeypatch)
        icon = bytes(32 * 32 * 4)

        layout = _client(_reply(_node(1, label="_Open", icon_bytes=icon))).get_layout()

        assert layout is not None
        assert layout.revision == 7
        assert [child.label for child in layout.root.children] == ["Open"]
        assert layout.root.children[0].icon_data == icon

    def test_get_layout_returns_none_for_malformed_root(self):
        assert _client(GLib.Variant("(us)", (7, "junk"))).get_layout() is None
        assert _client((7, None)).get_layout() is None

    def test_get_layout_still_accepts_plain_python_replies(self):
        layout = _client((7, (0, {"label": "_Root"}, []))).get_layout()

        assert layout is not None
        assert layout.revision == 7
        assert layout.root.label == "Root"
