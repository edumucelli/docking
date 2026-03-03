"""Tests for the hydration reminder applet."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.hydration.applet as hydration_mod
from docking.applets.hydration import (
    HydrationApplet,
)
from docking.applets.hydration.state import (
    DEFAULT_INTERVAL,
    mouth_curvature,
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


class TestMouthCurvature:
    def test_full_is_smile(self):
        assert mouth_curvature(fill=1.0) > 0.0

    def test_half_is_neutral(self):
        assert mouth_curvature(fill=0.5) == 0.0

    def test_empty_is_frown(self):
        assert mouth_curvature(fill=0.0) < 0.0

    def test_clamps_low(self):
        assert mouth_curvature(fill=-1.0) == -1.0

    def test_clamps_high(self):
        assert mouth_curvature(fill=2.0) == 1.0


class TestHydrationLifecycle:
    def test_property_accessors_cover_interval_show_timer_and_tick_count(self):
        applet = HydrationApplet(48)
        applet._interval_min = 30
        applet._show_timer = False
        applet._tick_count = 12
        assert applet._interval_min == 30
        assert applet._show_timer is False
        assert applet._tick_count == 12

    def test_start_and_stop_manage_timer(self, monkeypatch):
        applet = HydrationApplet(48)
        monkeypatch.setattr(
            hydration_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 444
        )
        removed = []
        monkeypatch.setattr(
            hydration_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        assert applet._timer_id == 444

        applet.stop()
        assert removed == [444]
        assert applet._timer_id == 0

    def test_toggle_timer_updates_state_and_saves(self):
        applet = HydrationApplet(48)
        widget = MagicMock()
        widget.get_active.return_value = False
        applet._save = MagicMock()
        applet.refresh_presentation = MagicMock()

        applet._on_toggle_timer(widget)

        assert applet._show_timer is False
        applet._save.assert_called_once()
        applet.refresh_presentation.assert_called_once()

    def test_tick_should_refresh_branch_updates_tooltip(self, monkeypatch):
        applet = HydrationApplet(48)
        refreshed = []
        monkeypatch.setattr(
            applet, "refresh_presentation", lambda: refreshed.append(True)
        )
        monkeypatch.setattr(
            hydration_mod,
            "tick",
            lambda state: SimpleNamespace(
                state=state, became_empty=False, should_refresh=True
            ),
        )

        assert applet._tick() is True
        assert refreshed == [True]

    def test_set_interval_saves_preferences(self):
        applet = HydrationApplet(48)
        applet._save = MagicMock()
        applet._set_interval(minutes=90)
        assert applet._interval_min == 90
        applet._save.assert_called_once()
