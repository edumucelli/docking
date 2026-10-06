"""Tests for shared StatusNotifier models and parsing helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from gi.repository import GLib

from docking.platform.status_notifier import backend
from docking.platform.status_notifier.backend import (
    RegisteredItemAddress,
    StatusNotifierBackend,
    TrayItem,
    _argb_to_rgba,
    _best_icon_pixmap,
    _bytes_from_dbus_array,
    _tooltip_parts,
    _unpack_variant,
    parse_registered_item,
    tray_item_from_properties,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestWatcherIdentifiers:
    @pytest.mark.parametrize("service", [":1.74", "org.example.Tray"])
    def test_gnome_identifier(self, service):
        assert parse_registered_item(
            f"{service}@/org/ayatana/NotificationItem/remmina_icon"
        ) == RegisteredItemAddress(
            service=service, path="/org/ayatana/NotificationItem/remmina_icon"
        )

    @pytest.mark.parametrize(
        "identifier",
        [
            ":1.74@@/Tray",
            "invalid/Tray",
            ":1.74@/bad-path",
            ":1.74/Tray//Icon",
            "org.example.Tray@",
            "@/Tray",
        ],
    )
    def test_invalid_address_is_rejected(self, identifier):
        assert parse_registered_item(identifier) is None

    def test_invalid_sender_is_rejected(self):
        assert parse_registered_item("/Tray", default_service=":1.74@") is None


class TestItemReadFailures:
    @pytest.mark.parametrize("reply", [None, (), ({}, {}), ("invalid",)])
    def test_missing_or_malformed_reply_is_skipped(self, reply):
        bus = Mock()
        bus.call_sync.return_value = reply
        assert (
            backend._read_item(
                bus=bus,
                address=RegisteredItemAddress(service=":1.74", path="/Tray"),
            )
            is None
        )

    def test_invalid_address_never_reaches_gio(self):
        bus = Mock()
        assert (
            backend._read_item(
                bus=bus,
                address=RegisteredItemAddress(service=":1.74@", path="/Tray"),
            )
            is None
        )
        bus.call_sync.assert_not_called()

    def test_poll_keeps_good_items_after_failed_reads(self, monkeypatch):
        bus = Mock()
        bus.call_sync.side_effect = [
            None,
            ({"IconPixmap": [(1, 1, ["invalid byte"])]},),
            GLib.Variant("(a{sv})", ({"Title": GLib.Variant("s", "Healthy")},)),
        ]
        client = StatusNotifierBackend()
        monkeypatch.setattr(client, "_connection", lambda: bus)
        monkeypatch.setattr(backend, "_name_has_owner", lambda **kwargs: True)
        monkeypatch.setattr(
            client, "_register_with_existing_watcher", lambda **kw: None
        )
        monkeypatch.setattr(
            client,
            "_existing_watcher_addresses",
            lambda **kw: [
                RegisteredItemAddress(service=f":1.{number}", path="/Tray")
                for number in (74, 75, 76)
            ],
        )
        state = client.get_state()
        assert state.available
        assert [item.title for item in state.items] == ["Healthy"]
        assert state.items[0].service == ":1.76"
        assert bus.call_sync.call_count == 3


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

    def test_variant_with_pixmap_reads_text_without_unpacking(self):
        variant = GLib.Variant(
            "(sa(iiay)ss)",
            ("icon", [(64, 64, bytes(64 * 64 * 4))], "TTitle", "TBody"),
        )
        assert _tooltip_parts(variant) == ("TTitle", "TBody")

    def test_variant_with_unexpected_child_types_matches_plain_tuple(self):
        variant = GLib.Variant("(siis)", ("icon", 1, 2, "body"))
        assert _tooltip_parts(variant) == _tooltip_parts(("icon", 1, 2, "body"))

    def test_short_variant_tuple_returns_empty(self):
        assert _tooltip_parts(GLib.Variant("(ss)", ("a", "b"))) == ("", "")

    def test_array_variant_matches_plain_list(self):
        variant = GLib.Variant("as", ["a", "b", "title", "body"])
        assert _tooltip_parts(variant) == _tooltip_parts(["a", "b", "title", "body"])

    def test_dictionary_variant_returns_empty(self):
        variant = GLib.Variant(
            "a{sv}",
            {f"k{index}": GLib.Variant("s", "v") for index in range(4)},
        )
        assert _tooltip_parts(variant) == ("", "")


class TestMalformedTooltipsDoNotAbort:
    """A non-container ToolTip must not kill the process.

    ``Variant.n_children()`` on a scalar reaches a GLib ``g_error()`` that
    aborts the interpreter with SIGABRT, which no exception handler can catch,
    so the offending shapes have to be exercised in a subprocess.
    """

    SCRIPT = """
from unittest.mock import Mock

from gi.repository import GLib

from docking.platform.status_notifier.backend import (
    RegisteredItemAddress,
    _read_item,
    _tooltip_parts,
)

# A tray app publishing a scalar ToolTip used to abort the dock here.
assert _tooltip_parts(GLib.Variant("s", "")) == ("", "")
assert _tooltip_parts(GLib.Variant("i", 7)) == ("", "")
assert _tooltip_parts(GLib.Variant("v", GLib.Variant("s", ""))) == ("", "")

TOOLTIPS = [
    GLib.Variant(signature, value)
    for signature, value in [
        ("s", ""),
        ("i", 1),
        ("b", True),
        ("as", ["a", "b", "c", "d"]),
        ("ay", bytes(8)),
        ("a{sv}", {f"k{index}": GLib.Variant("s", "v") for index in range(5)}),
        ("(ss)", ("a", "b")),
        ("mi", 5),
        ("v", GLib.Variant("s", "")),
    ]
] + [GLib.Variant("(sa(iiay)ss)", ("", [(4, 4, bytes(64))], "T", "B"))]

PIXMAPS = [
    GLib.Variant(signature, value)
    for signature, value in [
        ("s", ""),
        ("i", 3),
        ("as", ["a"]),
        ("a{sv}", {"k": GLib.Variant("s", "v")}),
        ("a(iiay)", []),
        ("a(iiay)", [(0, 0, b"")]),
        ("a(iiay)", [(4, 4, bytes(4))]),
        ("a(iiay)", [(2, 2, bytes(16))]),
    ]
]

for pixmap in PIXMAPS:
    for tooltip in TOOLTIPS:
        reply = GLib.Variant(
            "(a{sv})",
            (
                {
                    "Title": GLib.Variant("s", "Tray"),
                    "IconPixmap": pixmap,
                    "ToolTip": tooltip,
                    "AttentionIconPixmap": pixmap,
                    "OverlayIconPixmap": GLib.Variant("s", ""),
                },
            ),
        )
        bus = Mock()
        bus.call_sync.return_value = reply
        item = _read_item(
            bus=bus, address=RegisteredItemAddress(service=":1.74", path="/Tray")
        )
        assert item is not None
        assert item.title == "Tray"

well_formed = GLib.Variant(
    "(a{sv})",
    (
        {
            "Title": GLib.Variant("s", "Tray"),
            "IconPixmap": GLib.Variant("a(iiay)", [(2, 2, bytes(16))]),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [(4, 4, bytes(64))], "T", "B")),
        },
    ),
)
bus = Mock()
bus.call_sync.return_value = well_formed
item = _read_item(bus=bus, address=RegisteredItemAddress(service=":1.74", path="/Tray"))
assert item is not None
assert (item.tooltip_title, item.tooltip_text) == ("T", "B")
assert item.icon_pixmap is not None and item.icon_pixmap.width == 2
print("ok")
"""

    def test_scalar_tooltips_do_not_abort(self):
        result = subprocess.run(
            [sys.executable, "-c", self.SCRIPT],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"


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


class TestBestIconPixmapVariants:
    def test_matches_plain_python_result(self):
        pixmaps = [(2, 2, bytes(range(16))), (4, 4, bytes(range(64)))]
        variant = GLib.Variant("a(iiay)", pixmaps)
        assert _best_icon_pixmap(variant) == _best_icon_pixmap(pixmaps)

    def test_skips_larger_entry_with_insufficient_data(self):
        value = GLib.Variant("a(iiay)", [(10, 10, bytes(4)), (1, 1, bytes(4))])
        result = _best_icon_pixmap(value)
        assert result is not None
        assert result.width == 1

    def test_non_pixmap_variant_is_ignored(self):
        assert _best_icon_pixmap(GLib.Variant("as", ["nope"])) is None

    def test_unwraps_variant_wrapper(self):
        pixmaps = GLib.Variant("a(iiay)", [(3, 3, bytes(3 * 3 * 4))])
        wrapper = GLib.Variant("v", pixmaps)
        result = _best_icon_pixmap(wrapper)
        assert result is not None
        assert result.width == 3


class TestReadItemKeepsIconBytesAsVariants:
    """Icon byte arrays must never be unpacked into Python lists (issue #377)."""

    @staticmethod
    def _reply(*, title="Tray", sizes=(2, 4)):
        pixmaps = [(size, size, bytes(size * size * 4)) for size in sizes]
        return GLib.Variant(
            "(a{sv})",
            (
                {
                    "Title": GLib.Variant("s", title),
                    "IconPixmap": GLib.Variant("a(iiay)", pixmaps),
                    "ToolTip": GLib.Variant(
                        "(sa(iiay)ss)", ("", [(8, 8, bytes(8 * 8 * 4))], "TT", "TB")
                    ),
                },
            ),
        )

    def test_icon_properties_are_handed_over_as_variants(self, monkeypatch):
        captured: dict = {}
        original = backend.tray_item_from_properties

        def spy(*, address, properties):
            captured.update(properties)
            return original(address=address, properties=properties)

        monkeypatch.setattr(backend, "tray_item_from_properties", spy)
        bus = Mock()
        bus.call_sync.return_value = self._reply()
        item = backend._read_item(
            bus=bus,
            address=RegisteredItemAddress(service=":1.74", path="/Tray"),
        )
        assert item is not None
        assert isinstance(captured["IconPixmap"], GLib.Variant)
        assert isinstance(captured["ToolTip"], GLib.Variant)
        assert captured["Title"] == "Tray"
        assert item.icon_pixmap is not None
        assert item.icon_pixmap.width == 4
        assert (item.tooltip_title, item.tooltip_text) == ("TT", "TB")

    def test_byte_arrays_never_reach_variant_unpack(self, monkeypatch):
        original_unpack = GLib.Variant.unpack

        def guarded(self):
            assert "ay" not in self.get_type_string(), (
                "icon byte arrays must not be unpacked"
            )
            return original_unpack(self)

        monkeypatch.setattr(GLib.Variant, "unpack", guarded)
        bus = Mock()
        bus.call_sync.return_value = self._reply(sizes=(16, 32, 128))
        item = backend._read_item(
            bus=bus,
            address=RegisteredItemAddress(service=":1.74", path="/Tray"),
        )
        assert item is not None
        assert item.icon_pixmap is not None
        assert item.icon_pixmap.width == 128

    def test_reply_of_unexpected_variant_type_is_skipped(self):
        bus = Mock()
        bus.call_sync.return_value = GLib.Variant("(as)", (["Tray"],))
        assert (
            backend._read_item(
                bus=bus,
                address=RegisteredItemAddress(service=":1.74", path="/Tray"),
            )
            is None
        )


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
