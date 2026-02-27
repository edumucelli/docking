"""Tests for the hydration reminder applet."""

from docking.applets.hydration import (
    DEFAULT_INTERVAL,
    HydrationApplet,
    tooltip_text,
    water_color,
)


class TestWaterColor:
    def test_is_blue(self):
        r, g, b = water_color()
        assert b > r  # blue dominant


class TestTooltipText:
    def test_full(self):
        result = tooltip_text(fill=1.0, interval_min=45)
        assert "45:00" in result

    def test_empty(self):
        assert tooltip_text(fill=0.0, interval_min=45) == "Drink water!"

    def test_half(self):
        result = tooltip_text(fill=0.5, interval_min=60)
        assert "30:00" in result


class TestHydrationApplet:
    def test_creates_with_icon(self):
        applet = HydrationApplet(48)
        assert applet.item.icon is not None
        assert "45:00" in applet.item.name

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = HydrationApplet(size)
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_starts_full(self):
        applet = HydrationApplet(48)
        assert applet._fill == 1.0

    def test_tick_decreases_fill(self):
        applet = HydrationApplet(48)
        applet._tick()
        assert applet._fill < 1.0

    def test_click_refills(self):
        applet = HydrationApplet(48)
        applet._fill = 0.5
        applet.on_clicked()
        assert applet._fill == 1.0

    def test_empty_triggers_urgent(self):
        applet = HydrationApplet(48)
        applet._fill = 1.0 / (DEFAULT_INTERVAL * 60)  # one tick from empty
        applet._tick()
        assert applet._fill <= 0
        assert applet.item.is_urgent is True

    def test_click_clears_urgent(self):
        applet = HydrationApplet(48)
        applet._fill = 0.0
        applet.item.is_urgent = True
        applet.on_clicked()
        assert applet.item.is_urgent is False

    def test_tick_noop_when_empty(self):
        applet = HydrationApplet(48)
        applet._fill = 0.0
        applet._tick()
        assert applet._fill == 0.0

    def test_menu_has_interval_presets(self):
        applet = HydrationApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert "30 min" in labels
        assert "45 min" in labels
        assert "60 min" in labels
        assert "90 min" in labels

    def test_renders_when_empty(self):
        applet = HydrationApplet(48)
        applet._fill = 0.0
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None

    def test_renders_at_half(self):
        applet = HydrationApplet(48)
        applet._fill = 0.5
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None
