"""Tests for the separator applet."""

from docking.applets.base import applet_id_from
from docking.applets.identity import AppletId
from docking.applets.separator import (
    DEFAULT_SIZE,
    MAX_SIZE,
    MIN_SIZE,
    STEP,
    SeparatorApplet,
)


class TestAppletIdFrom:
    def test_simple_applet(self):
        assert applet_id_from(desktop_id="applet://clock") == AppletId.CLOCK

    def test_separator_instance(self):
        assert applet_id_from(desktop_id="applet://separator#0") == AppletId.SEPARATOR

    def test_separator_high_instance(self):
        assert applet_id_from(desktop_id="applet://separator#42") == AppletId.SEPARATOR

    def test_no_instance_suffix(self):
        assert applet_id_from(desktop_id="applet://weather") == AppletId.WEATHER


class TestSeparatorApplet:
    def test_creates_with_icon(self):
        applet = SeparatorApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Separator"

    def test_default_gap(self):
        applet = SeparatorApplet(48)
        assert applet.item.main_size == DEFAULT_SIZE

    def test_icon_width_matches_gap(self):
        applet = SeparatorApplet(48)
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None
        assert pixbuf.get_width() == DEFAULT_SIZE
        assert pixbuf.get_height() == 48

    def test_menu_has_increase_decrease(self):
        applet = SeparatorApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert labels == ["Increase Gap", "Decrease Gap"]

    def test_scroll_up_increases_gap(self):
        applet = SeparatorApplet(48)
        before = applet._gap
        applet.on_scroll(direction_up=True)
        assert applet._gap == before + STEP
        assert applet.item.main_size == applet._gap

    def test_scroll_down_decreases_gap(self):
        applet = SeparatorApplet(48)
        before = applet._gap
        applet.on_scroll(direction_up=False)
        assert applet._gap == before - STEP
        assert applet.item.main_size == applet._gap

    def test_gap_clamps_at_min(self):
        applet = SeparatorApplet(48)
        applet._gap = MIN_SIZE
        applet.on_scroll(direction_up=False)
        assert applet._gap == MIN_SIZE

    def test_gap_clamps_at_max(self):
        applet = SeparatorApplet(48)
        applet._gap = MAX_SIZE
        applet.on_scroll(direction_up=True)
        assert applet._gap == MAX_SIZE

    def test_desktop_id_can_be_overridden(self):
        applet = SeparatorApplet(48)
        applet.item.desktop_id = "applet://separator#5"
        assert applet.item.desktop_id == "applet://separator#5"
