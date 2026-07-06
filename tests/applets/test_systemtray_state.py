"""Tests for system tray state helper functions."""

from __future__ import annotations

from gi.repository import GLib

from docking.applets.systemtray.state import (
    RegisteredItemAddress,
    StatusTrayState,
    TrayItem,
    _argb_to_rgba,
    _best_icon_pixmap,
    _bytes_from_dbus_array,
    _tooltip_parts,
    _unpack_variant,
    tooltip_text,
    tray_item_from_properties,
)


class TestTooltipParts:
    def test_four_element_tuple_returns_parts(self):
        title, body = _tooltip_parts(("icon", [], "Title", "Body text"))
        assert title == "Title"
        assert body == "Body text"

    def test_short_tuple_returns_empty(self):
        assert _tooltip_parts(("icon",)) == ("", "")
        assert _tooltip_parts(()) == ("", "")

    def test_non_tuple_returns_empty(self):
        assert _tooltip_parts("string") == ("", "")
        assert _tooltip_parts(42) == ("", "")
        assert _tooltip_parts(None) == ("", "")

    def test_glib_variant_unwrapped_first(self):
        variant = GLib.Variant("(ssss)", ("icon", "ignored", "VTitle", "VBody"))
        title, body = _tooltip_parts(variant)
        assert title == "VTitle"
        assert body == "VBody"


class TestBytesFromDBusArray:
    def test_returns_bytes_directly(self):
        assert _bytes_from_dbus_array(b"\x01\x02\x03") == b"\x01\x02\x03"

    def test_converts_list_of_ints(self):
        assert _bytes_from_dbus_array([255, 128, 64]) == b"\xff\x80\x40"

    def test_converts_tuple_of_ints(self):
        assert _bytes_from_dbus_array((10, 20, 30)) == b"\x0a\x14\x1e"

    def test_clamps_to_byte_range(self):
        assert _bytes_from_dbus_array([300, -10]) == b"\x2c\xf6"

    def test_returns_empty_for_non_bytes_non_sequence(self):
        assert _bytes_from_dbus_array(None) == b""
        assert _bytes_from_dbus_array(42) == b""
        assert _bytes_from_dbus_array("string") == b""

    def test_unwraps_glib_variant_first(self):
        variant = GLib.Variant("ay", [0xAB, 0xCD])
        result = _bytes_from_dbus_array(variant)
        assert result == b"\xab\xcd"


class TestArgbToRgba:
    def test_converts_single_pixel(self):
        # ARGB = 0xAARRGGBB → RGBA = 0xRRGGBBAA
        argb = bytes([0xFF, 0x11, 0x22, 0x33])
        rgba = _argb_to_rgba(argb)
        assert rgba == bytes([0x11, 0x22, 0x33, 0xFF])

    def test_converts_multiple_pixels(self):
        argb = bytes(
            [
                0xFF,
                0xAA,
                0xBB,
                0xCC,
                0x80,
                0xDD,
                0xEE,
                0xFF,
            ]
        )
        rgba = _argb_to_rgba(argb)
        assert rgba == bytes(
            [
                0xAA,
                0xBB,
                0xCC,
                0xFF,
                0xDD,
                0xEE,
                0xFF,
                0x80,
            ]
        )

    def test_empty_bytes(self):
        assert _argb_to_rgba(b"") == b""

    def test_partial_pixel_raises_index_error(self):
        """If length is not multiple of 4, the function raises IndexError."""
        import pytest

        argb = bytes([0xFF, 0x11, 0x22])  # 3 bytes, not a full pixel
        with pytest.raises(IndexError):
            _argb_to_rgba(argb)


class TestBestIconPixmap:
    def test_returns_none_for_non_list(self):
        assert _best_icon_pixmap(None) is None
        assert _best_icon_pixmap("not a list") is None
        assert _best_icon_pixmap(42) is None

    def test_returns_largest_pixmap(self):
        # Two pixmaps of different sizes
        value = [
            (1, 1, [0xFF, 0x11, 0x22, 0x33]),
            (4, 4, [0xFF] * 64),  # 4*4*4 = 64 bytes
        ]
        result = _best_icon_pixmap(value)
        assert result is not None
        assert result.width == 4
        assert result.height == 4

    def test_skips_invalid_entries(self):
        value = [
            "not a tuple",
            (1, 1, [0xFF, 0x11, 0x22, 0x33]),
            (0, 0, [0xFF] * 4),  # zero-size
            (-1, 5, [0xFF] * 4),  # negative
        ]
        result = _best_icon_pixmap(value)
        assert result is not None
        assert result.width == 1

    def test_skips_entries_with_insufficient_data(self):
        value = [
            (10, 10, [0xFF] * 4),  # needs 400 bytes, has only 4
            (1, 1, [0xFF, 0x11, 0x22, 0x33]),
        ]
        result = _best_icon_pixmap(value)
        assert result is not None
        assert result.width == 1

    def test_skips_entries_with_non_int_dimensions(self):
        value = [
            ("big", "small", [0xFF] * 4),
            (1, 1, [0xFF, 0x11, 0x22, 0x33]),
        ]
        result = _best_icon_pixmap(value)
        assert result is not None
        assert result.width == 1

    def test_returns_none_when_all_invalid(self):
        value = [
            (0, 5, [0xFF] * 4),
            (-1, 5, [0xFF] * 4),
        ]
        assert _best_icon_pixmap(value) is None

    def test_unwraps_glib_variants(self):
        inner = GLib.Variant("(iiay)", (2, 2, [0x11, 0x22, 0x33, 0x44] * 4))
        value = GLib.Variant("a(iiay)", [inner])
        result = _best_icon_pixmap(value)
        assert result is not None
        assert result.width == 2
        assert result.height == 2


class TestUnpackVariant:
    def test_unpacks_glib_variant(self):
        variant = GLib.Variant("s", "hello")
        assert _unpack_variant(variant) == "hello"

    def test_recursively_unpacks_dict(self):
        variant = GLib.Variant("a{sv}", {"key": GLib.Variant("s", "value")})
        result = _unpack_variant({"outer": variant})
        assert result == {"outer": {"key": "value"}}

    def test_recursively_unpacks_tuple(self):
        variant = GLib.Variant("(si)", ("hello", 42))
        result = _unpack_variant((variant,))
        assert result == (("hello", 42),)

    def test_recursively_unpacks_list(self):
        variant = GLib.Variant("i", 99)
        result = _unpack_variant([variant])
        assert result == [99]

    def test_plain_values_pass_through(self):
        assert _unpack_variant("plain") == "plain"
        assert _unpack_variant(42) == 42
        assert _unpack_variant(None) is None


class TestTrayItemDisplayTitle:
    def test_uses_title_when_present(self):
        item = TrayItem(
            identifier="test",
            service="svc",
            path="/p",
            title="MyTitle",
            status="Active",
            category="",
            icon_name="",
            attention_icon_name="",
            overlay_icon_name="",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="TT",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.display_title == "MyTitle"

    def test_falls_back_to_tooltip_title(self):
        item = TrayItem(
            identifier="test",
            service="svc",
            path="/p",
            title="",
            status="Active",
            category="",
            icon_name="",
            attention_icon_name="",
            overlay_icon_name="",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="TooltipTitle",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.display_title == "TooltipTitle"

    def test_falls_back_to_service(self):
        item = TrayItem(
            identifier="test",
            service="org.example.App",
            path="/p",
            title="",
            status="Active",
            category="",
            icon_name="",
            attention_icon_name="",
            overlay_icon_name="",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.display_title == "org.example.App"


class TestTrayItemEffectiveIconName:
    def test_returns_attention_icon_when_needs_attention(self):
        item = TrayItem(
            identifier="test",
            service="svc",
            path="/p",
            title="",
            status="NeedsAttention",
            category="",
            icon_name="normal",
            attention_icon_name="alert",
            overlay_icon_name="",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.effective_icon_name == "alert"

    def test_returns_icon_name_when_no_attention(self):
        item = TrayItem(
            identifier="test",
            service="svc",
            path="/p",
            title="",
            status="Active",
            category="",
            icon_name="normal",
            attention_icon_name="alert",
            overlay_icon_name="overlay",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.effective_icon_name == "normal"

    def test_falls_back_to_attention_then_overlay(self):
        item = TrayItem(
            identifier="test",
            service="svc",
            path="/p",
            title="",
            status="Active",
            category="",
            icon_name="",
            attention_icon_name="alert",
            overlay_icon_name="overlay",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.effective_icon_name == "alert"

    def test_falls_back_to_overlay_when_both_empty(self):
        item = TrayItem(
            identifier="test",
            service="svc",
            path="/p",
            title="",
            status="Active",
            category="",
            icon_name="",
            attention_icon_name="",
            overlay_icon_name="overlay",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.effective_icon_name == "overlay"

    def test_status_case_insensitive_for_needs_attention(self):
        item = TrayItem(
            identifier="test",
            service="svc",
            path="/p",
            title="",
            status="needsattention",
            category="",
            icon_name="normal",
            attention_icon_name="alert",
            overlay_icon_name="",
            icon_theme_path="",
            icon_pixmap=None,
            menu_path="",
            tooltip_title="",
            tooltip_text="",
            item_is_menu=False,
        )
        assert item.effective_icon_name == "alert"


class TestRegisteredItemAddress:
    def test_identifier_combines_service_and_path(self):
        addr = RegisteredItemAddress(service=":1.42", path="/Tray/Icon")
        assert addr.identifier == ":1.42/Tray/Icon"


class TestTooltipTextExtended:
    def test_no_error_no_items_no_legacy_unavailable(self):
        text = tooltip_text(
            StatusTrayState(
                available=False,
                watcher_mode="unavailable",
                items=(),
                error="",
                legacy_tray_owner="",
            )
        )
        assert "D-Bus unavailable" in text

    def test_watcher_mode_no_items_default_message(self):
        text = tooltip_text(
            StatusTrayState(
                available=True,
                watcher_mode="watcher",
                items=(),
                legacy_tray_owner="",
            )
        )
        assert "no tray apps" in text

    def test_more_than_six_items_shows_truncation(self):
        items = tuple(
            tray_item_from_properties(
                address=RegisteredItemAddress(service=f"org.ex{i}.App", path="/Item"),
                properties={"Title": f"Item {i}", "Status": "Active"},
            )
            for i in range(8)
        )
        text = tooltip_text(
            StatusTrayState(
                available=True,
                watcher_mode="watcher",
                items=items,
            )
        )
        assert "8 item(s)" in text
        assert "2 more" in text
        assert "Item 0" in text
        assert "Item 5" in text
        assert "Item 6" not in text  # truncated


class TestTrayItemExtended:
    def test_falls_back_to_id_when_no_title(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.ex.App", path="/Item"),
            properties={"Id": "fallback-id", "Status": "Active"},
        )
        assert item.title == "fallback-id"

    def test_default_status_is_passive(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.ex.App", path="/Item"),
            properties={},
        )
        assert item.status == "Passive"

    def test_empty_properties_returns_sensible_defaults(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.ex.App", path="/Item"),
            properties={},
        )
        assert item.title == ""
        assert item.icon_name == ""
        assert item.menu_path == ""
        assert item.item_is_menu is False
