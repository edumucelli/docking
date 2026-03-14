"""Tests for the Color Picker applet."""

from typing import ClassVar

import docking.applets.colorpicker.applet as colorpicker_applet_mod
import docking.applets.colorpicker.state as colorpicker_state_mod
from docking.applets.colorpicker import ColorPickerApplet, rgb_to_hex
from docking.applets.colorpicker.render import create_icon


class TestRgbToHex:
    def test_black(self):
        assert rgb_to_hex(r=0, g=0, b=0) == "#000000"

    def test_white(self):
        assert rgb_to_hex(r=255, g=255, b=255) == "#FFFFFF"

    def test_red(self):
        assert rgb_to_hex(r=255, g=0, b=0) == "#FF0000"

    def test_mixed(self):
        assert rgb_to_hex(r=18, g=52, b=86) == "#123456"


class TestRenderIcon:
    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            pixbuf = create_icon(size=size, r=0.5, g=0.2, b=0.8)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_renders_with_hex_label(self):
        pixbuf = create_icon(size=48, r=1.0, g=0.0, b=0.0, hex_label="#FF0000")
        assert pixbuf is not None

    def test_renders_without_hex_label(self):
        pixbuf = create_icon(size=48, r=0.5, g=0.5, b=0.5, hex_label=None)
        assert pixbuf is not None


class TestColorPickerApplet:
    def test_creates_with_icon(self):
        applet = ColorPickerApplet(48)
        assert applet.item.icon is not None

    def test_default_tooltip(self):
        applet = ColorPickerApplet(48)
        assert applet.item.name == "Color Picker"

    def test_tooltip_after_pick(self):
        applet = ColorPickerApplet(48)
        applet._hex = "#FF0000"
        applet.refresh_tooltip()
        assert applet.item.name == "#FF0000"

    def test_menu_has_show_hex_toggle(self):
        applet = ColorPickerApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert "Show Hex" in labels

    def test_menu_has_copy_when_color_picked(self):
        applet = ColorPickerApplet(48)
        applet._hex = "#ABCDEF"
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert "Copy #ABCDEF" in labels

    def test_menu_no_copy_when_no_color(self):
        applet = ColorPickerApplet(48)
        applet._hex = ""
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert not any("Copy" in label for label in labels)

    def test_icon_renders_at_various_sizes(self):
        applet = ColorPickerApplet(48)
        for size in [32, 48, 64]:
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_on_clicked_starts_pick_mode(self):
        applet = ColorPickerApplet(48)
        calls: list[str] = []
        applet._start_pick = lambda: calls.append("pick")  # type: ignore[method-assign]
        applet.on_clicked()
        assert calls == ["pick"]

    def test_toggle_hex_saves_and_refreshes(self):
        applet = ColorPickerApplet(48)
        calls: list[str] = []
        applet._save = lambda: calls.append("save")  # type: ignore[method-assign]
        applet.refresh_presentation = lambda: calls.append("refresh")  # type: ignore[method-assign]

        class _Widget:
            def get_active(self):
                return False

        applet._on_toggle_hex(_Widget())
        assert applet._show_hex is False
        assert calls == ["save", "refresh"]

    def test_overlay_draw_paints_transparent_layer(self):
        calls: list[str] = []

        class _Cr:
            def set_source_rgba(self, *args):
                calls.append("rgba")

            def paint(self):
                calls.append("paint")

        assert ColorPickerApplet._on_overlay_draw(None, _Cr()) is True
        assert calls == ["rgba", "paint"]

    def test_overlay_click_updates_state_when_pixel_found(self, monkeypatch):
        applet = ColorPickerApplet(48)
        calls: list[str] = []
        applet._dismiss_overlay = lambda: calls.append("dismiss")  # type: ignore[method-assign]
        applet._copy_to_clipboard = lambda: calls.append("copy")  # type: ignore[method-assign]
        applet._save = lambda: calls.append("save")  # type: ignore[method-assign]
        applet.refresh_presentation = lambda: calls.append("refresh")  # type: ignore[method-assign]
        monkeypatch.setattr(
            colorpicker_applet_mod, "pick_pixel", lambda x, y: (10, 20, 30)
        )
        monkeypatch.setattr(
            colorpicker_applet_mod, "rgb_to_hex", lambda r, g, b: "#0A141E"
        )

        class _Event:
            x_root = 100.4
            y_root = 200.9

        assert applet._on_overlay_click(None, _Event()) is True
        assert applet._hex == "#0A141E"
        assert applet._r == 10 / 255.0
        assert applet._g == 20 / 255.0
        assert applet._b == 30 / 255.0
        assert calls == ["dismiss", "copy", "save", "refresh"]

    def test_overlay_click_no_pixel_only_dismisses(self, monkeypatch):
        applet = ColorPickerApplet(48)
        calls: list[str] = []
        applet._dismiss_overlay = lambda: calls.append("dismiss")  # type: ignore[method-assign]
        applet._copy_to_clipboard = lambda: calls.append("copy")  # type: ignore[method-assign]
        monkeypatch.setattr(colorpicker_applet_mod, "pick_pixel", lambda x, y: None)

        class _Event:
            x_root = 1
            y_root = 2

        assert applet._on_overlay_click(None, _Event()) is True
        assert calls == ["dismiss"]

    def test_overlay_key_escape_dismisses(self):
        applet = ColorPickerApplet(48)
        calls: list[str] = []
        applet._dismiss_overlay = lambda: calls.append("dismiss")  # type: ignore[method-assign]

        class _Event:
            keyval = colorpicker_applet_mod.Gdk.KEY_Escape

        assert applet._on_overlay_key(None, _Event()) is True
        assert calls == ["dismiss"]

    def test_overlay_key_other_key_is_noop(self):
        applet = ColorPickerApplet(48)
        calls: list[str] = []
        applet._dismiss_overlay = lambda: calls.append("dismiss")  # type: ignore[method-assign]

        class _Event:
            keyval = 0

        assert applet._on_overlay_key(None, _Event()) is True
        assert calls == []

    def test_dismiss_overlay_ungrabs_and_destroys(self, monkeypatch):
        applet = ColorPickerApplet(48)
        calls: list[str] = []

        class _Seat:
            def ungrab(self):
                calls.append("ungrab")

        class _Display:
            def get_default_seat(self):
                return _Seat()

        class _Overlay:
            def destroy(self):
                calls.append("destroy")

        applet._overlay = _Overlay()
        monkeypatch.setattr(
            colorpicker_applet_mod.Gdk.Display, "get_default", lambda: _Display()
        )
        applet._dismiss_overlay()
        assert applet._overlay is None
        assert calls == ["ungrab", "destroy"]

    def test_copy_to_clipboard_noop_without_hex(self, monkeypatch):
        applet = ColorPickerApplet(48)
        applet._hex = ""
        monkeypatch.setattr(
            colorpicker_applet_mod.Gtk.Clipboard,
            "get",
            lambda selection: (_ for _ in ()).throw(
                RuntimeError("should not be called")
            ),
        )
        applet._copy_to_clipboard()

    def test_copy_to_clipboard_sets_text(self, monkeypatch):
        applet = ColorPickerApplet(48)
        applet._hex = "#FF00AA"
        calls: list[tuple[str, object]] = []

        class _Clipboard:
            def set_text(self, value, length):
                calls.append(("set_text", value))

            def store(self):
                calls.append(("store", None))

        monkeypatch.setattr(
            colorpicker_applet_mod.Gtk.Clipboard,
            "get",
            lambda selection: _Clipboard(),
        )
        applet._copy_to_clipboard()
        assert calls == [("set_text", "#FF00AA"), ("store", None)]

    def test_save_persists_preferences_payload(self):
        applet = ColorPickerApplet(48)
        applet._show_hex = False
        applet._r = 0.1
        applet._g = 0.2
        applet._b = 0.3
        applet._hex = "#112233"
        saved: list[dict[str, object]] = []
        applet.save_prefs = lambda prefs: saved.append(prefs)  # type: ignore[method-assign]
        applet._save()
        assert saved == [
            {
                "show_hex": False,
                "r": 0.1,
                "g": 0.2,
                "b": 0.3,
                "hex": "#112233",
            }
        ]

    def test_init_restores_prefs_from_config(self):
        class _Cfg:
            applet_prefs: ClassVar = {
                colorpicker_applet_mod.AppletId.COLORPICKER: {
                    "show_hex": False,
                    "r": 0.1,
                    "g": 0.2,
                    "b": 0.3,
                    "hex": "#123456",
                }
            }

        applet = ColorPickerApplet(48, config=_Cfg())
        assert applet._show_hex is False
        assert applet._hex == "#123456"
        assert applet._r == 0.1
        assert applet._g == 0.2
        assert applet._b == 0.3

    def test_start_pick_noop_when_overlay_exists(self):
        applet = ColorPickerApplet(48)
        marker = object()
        applet._overlay = marker
        applet._start_pick()
        assert applet._overlay is marker

    def test_start_pick_creates_overlay_and_grabs_pointer(self, monkeypatch):
        applet = ColorPickerApplet(48)
        calls: list[str] = []

        class _Screen:
            def get_rgba_visual(self):
                return object()

            def get_width(self):
                return 1920

            def get_height(self):
                return 1080

        class _WindowObj:
            def set_cursor(self, cursor):
                calls.append("set_cursor")

        class _Overlay:
            def __init__(self, type=None):
                self._window = _WindowObj()

            def set_decorated(self, value):
                calls.append("decorated")

            def set_app_paintable(self, value):
                calls.append("paintable")

            def get_screen(self):
                return _Screen()

            def set_visual(self, visual):
                calls.append("visual")

            def set_default_size(self, width, height):
                calls.append("size")

            def move(self, x, y):
                calls.append("move")

            def connect(self, signal, handler):
                calls.append(f"connect:{signal}")

            def set_events(self, mask):
                calls.append("events")

            def show_all(self):
                calls.append("show")

            def get_window(self):
                return self._window

        class _Seat:
            def grab(self, *args):
                calls.append("grab")

        class _Display:
            def get_default_seat(self):
                return _Seat()

        monkeypatch.setattr(colorpicker_applet_mod.Gtk, "Window", _Overlay)
        monkeypatch.setattr(
            colorpicker_applet_mod.Gdk.Display, "get_default", lambda: _Display()
        )
        monkeypatch.setattr(
            colorpicker_applet_mod.Gdk.Cursor,
            "new_for_display",
            lambda display, ctype: object(),
        )

        applet._start_pick()
        assert applet._overlay is not None
        assert "grab" in calls
        assert "set_cursor" in calls


class TestColorPickerState:
    def test_pick_pixel_without_root_window(self, monkeypatch):
        monkeypatch.setattr(
            colorpicker_state_mod.Gdk, "get_default_root_window", lambda: None
        )
        assert colorpicker_state_mod.pick_pixel(1, 2) is None

    def test_pick_pixel_without_pixbuf(self, monkeypatch):
        monkeypatch.setattr(
            colorpicker_state_mod.Gdk, "get_default_root_window", lambda: object()
        )
        monkeypatch.setattr(
            colorpicker_state_mod.Gdk,
            "pixbuf_get_from_window",
            lambda root, x, y, w, h: None,
        )
        assert colorpicker_state_mod.pick_pixel(1, 2) is None

    def test_pick_pixel_reads_rgb_triplet(self, monkeypatch):
        class _PB:
            def get_pixels(self):
                return [12, 34, 56, 200]

        monkeypatch.setattr(
            colorpicker_state_mod.Gdk, "get_default_root_window", lambda: object()
        )
        monkeypatch.setattr(
            colorpicker_state_mod.Gdk,
            "pixbuf_get_from_window",
            lambda root, x, y, w, h: _PB(),
        )
        assert colorpicker_state_mod.pick_pixel(10, 20) == (12, 34, 56)
