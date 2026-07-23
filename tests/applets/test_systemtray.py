"""Tests for the System Tray applet helpers."""

from __future__ import annotations

from gi.repository import GLib

from docking.applets.systemtray.dbusmenu import parse_menu_node
from docking.applets.systemtray.render import create_status_tray_icon
from docking.applets.systemtray.state import (
    DEFAULT_ITEM_PATH,
    RegisteredItemAddress,
    StatusTrayState,
    parse_registered_item,
    tooltip_text,
    tray_item_from_properties,
    unavailable_state,
)


class TestRegisteredItemParsing:
    def test_service_only_uses_default_path(self):
        address = parse_registered_item("org.example.Tray")

        assert address == RegisteredItemAddress(
            service="org.example.Tray",
            path=DEFAULT_ITEM_PATH,
        )

    def test_service_and_path(self):
        address = parse_registered_item(":1.42/Tray/Icon")

        assert address == RegisteredItemAddress(service=":1.42", path="/Tray/Icon")

    def test_path_only_uses_sender_service(self):
        address = parse_registered_item(
            "/StatusNotifierItem",
            default_service=":1.99",
        )

        assert address == RegisteredItemAddress(
            service=":1.99",
            path="/StatusNotifierItem",
        )

    def test_path_only_without_sender_is_invalid(self):
        assert parse_registered_item("/StatusNotifierItem") is None
        assert parse_registered_item("") is None


class TestTrayItemParsing:
    def test_tray_item_from_status_notifier_properties(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(
                service="org.example.App",
                path="/StatusNotifierItem",
            ),
            properties={
                "Id": "example-id",
                "Title": "Example",
                "Status": "Active",
                "Category": "ApplicationStatus",
                "IconName": "example-icon",
                "AttentionIconName": "example-alert",
                "IconThemePath": "/tmp/icons",
                "IconPixmap": [(1, 1, [255, 17, 34, 51])],
                "Menu": "/Menu",
                "ToolTip": ("", [], "Tooltip title", "Tooltip body"),
                "ItemIsMenu": True,
            },
        )

        assert item.identifier == "org.example.App/StatusNotifierItem"
        assert item.item_id == "example-id"
        assert item.display_title == "Example"
        assert item.effective_icon_name == "example-icon"
        assert item.menu_path == "/Menu"
        assert item.icon_theme_path == "/tmp/icons"
        assert item.icon_pixmap is not None
        assert item.icon_pixmap.width == 1
        assert item.icon_pixmap.height == 1
        assert item.icon_pixmap.rgba == b"\x11\x22\x33\xff"
        assert item.tooltip_title == "Tooltip title"
        assert item.tooltip_text == "Tooltip body"
        assert item.item_is_menu is True

    def test_attention_icon_wins_when_status_needs_attention(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.example.App", path="/Item"),
            properties={
                "Title": "Example",
                "Status": "NeedsAttention",
                "IconName": "normal",
                "AttentionIconName": "alert",
            },
        )

        assert item.effective_icon_name == "alert"


class TestTooltipText:
    def test_unavailable_state_mentions_error(self):
        assert "session bus unavailable" in tooltip_text(
            unavailable_state("session bus unavailable")
        )

    def test_host_waiting_text(self):
        text = tooltip_text(
            StatusTrayState(available=True, watcher_mode="host", items=())
        )

        assert text == "System Tray: waiting for tray apps"

    def test_legacy_tray_owner_text(self):
        text = tooltip_text(
            StatusTrayState(
                available=True,
                watcher_mode="watcher",
                items=(),
                legacy_tray_owner="notification-area-applet",
            )
        )

        assert text == "System Tray: legacy tray owned by notification-area-applet"

    def test_lists_item_titles(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.example.App", path="/Item"),
            properties={"Title": "Example", "Status": "Active"},
        )

        assert "- Example" in tooltip_text(
            StatusTrayState(available=True, watcher_mode="watcher", items=(item,))
        )


class TestDBusMenuParsing:
    def test_parse_menu_tree(self):
        root = parse_menu_node(
            (
                0,
                {},
                [
                    (
                        1,
                        {
                            "label": "_Open",
                            "enabled": True,
                            "icon-name": "document-open",
                        },
                        [],
                    ),
                    (2, {"type": "separator"}, []),
                    (
                        3,
                        {
                            "label": "_Enabled",
                            "toggle-type": "checkmark",
                            "toggle-state": 1,
                            "icon-data": [1, 2, 3],
                        },
                        [],
                    ),
                ],
            )
        )

        assert root is not None
        assert [child.label for child in root.children] == ["Open", "", "Enabled"]
        assert root.children[0].icon_name == "document-open"
        assert root.children[1].is_separator is True
        assert root.children[2].toggle_type == "checkmark"
        assert root.children[2].toggle_state == 1
        assert root.children[2].icon_data == b"\x01\x02\x03"

    def test_parse_glib_variant_node(self):
        root = parse_menu_node(
            GLib.Variant(
                "(ia{sv}av)",
                (
                    0,
                    {"label": GLib.Variant("s", "_Root")},
                    [],
                ),
            )
        )

        assert root is not None
        assert root.label == "Root"


def test_create_status_tray_icon_dimensions():
    pixbuf = create_status_tray_icon(size=48, available=True, item_count=3)

    assert pixbuf is not None
    assert pixbuf.get_width() == 48
    assert pixbuf.get_height() == 48
