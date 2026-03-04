"""Tests for the Color Picker applet."""

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
