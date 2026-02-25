"""Tests for the clock applet -- rotation math, prefs, rendering."""

import json
import math
import time

import pytest

from docking.core.config import Config
from docking.applets.clock import (
    ClockApplet,
    hour_rotation_12h,
    hour_rotation_24h,
    minute_rotation,
)

# -- Rotation pure functions -------------------------------------------------


class TestMinuteRotation:
    """minute_rotation(m) should point the hand at the correct position."""

    def test_minute_0_points_up(self):
        # Given minute = 0 (12 o'clock)
        # When
        angle = minute_rotation(0)
        # Then -- pi rotates the downward line to point up
        assert angle == pytest.approx(math.pi)

    def test_minute_15_points_right(self):
        # Given minute = 15 (3 o'clock)
        # When
        angle = minute_rotation(15)
        # Then -- 1.5*pi = 270 degrees
        assert angle == pytest.approx(1.5 * math.pi)

    def test_minute_30_points_down(self):
        # Given minute = 30 (6 o'clock)
        # When
        angle = minute_rotation(30)
        # Then -- 2*pi = 360 degrees (= 0, points down)
        assert angle == pytest.approx(2 * math.pi)

    def test_minute_45_points_left(self):
        # Given minute = 45 (9 o'clock)
        # When
        angle = minute_rotation(45)
        # Then -- 2.5*pi
        assert angle == pytest.approx(2.5 * math.pi)

    def test_continuous_increase(self):
        # Given sequential minutes
        # When / Then -- rotation increases monotonically
        angles = [minute_rotation(m) for m in range(60)]
        for i in range(1, len(angles)):
            assert angles[i] > angles[i - 1]


class TestHourRotation12h:
    """hour_rotation_12h should complete one revolution per 12 hours."""

    def test_12_oclock(self):
        # Given 12:00 (hour=0 or 12)
        # When
        angle = hour_rotation_12h(0, 0)
        # Then -- points up (pi)
        assert angle == pytest.approx(math.pi)
        assert hour_rotation_12h(12, 0) == pytest.approx(math.pi)

    def test_3_oclock(self):
        # Given 3:00
        assert hour_rotation_12h(3, 0) == pytest.approx(1.5 * math.pi)

    def test_6_oclock(self):
        # Given 6:00
        assert hour_rotation_12h(6, 0) == pytest.approx(2 * math.pi)

    def test_9_oclock(self):
        # Given 9:00
        assert hour_rotation_12h(9, 0) == pytest.approx(2.5 * math.pi)

    def test_minutes_advance_hour_hand(self):
        # Given 3:30 vs 3:00
        # When
        at_3_00 = hour_rotation_12h(3, 0)
        at_3_30 = hour_rotation_12h(3, 30)
        # Then -- 3:30 should be further along than 3:00
        assert at_3_30 > at_3_00

    def test_full_revolution_is_12_hours(self):
        # Given hour 0 and hour 12 (mod 12 = 0)
        # Then -- same angle (one full revolution)
        assert hour_rotation_12h(0, 0) == pytest.approx(hour_rotation_12h(12, 0))


class TestHourRotation24h:
    """hour_rotation_24h should complete one revolution per 24 hours."""

    def test_0_oclock(self):
        # Given midnight (hour=0)
        assert hour_rotation_24h(0, 0) == pytest.approx(math.pi)

    def test_6_oclock(self):
        # Given 06:00 -- quarter of the way around
        assert hour_rotation_24h(6, 0) == pytest.approx(1.5 * math.pi)

    def test_12_oclock(self):
        # Given 12:00 -- half way around
        assert hour_rotation_24h(12, 0) == pytest.approx(2 * math.pi)

    def test_18_oclock(self):
        # Given 18:00 -- three quarters around
        assert hour_rotation_24h(18, 0) == pytest.approx(2.5 * math.pi)

    def test_full_revolution_is_24_hours(self):
        # Given hour 0 and hour 24 (mod 24 = 0)
        assert hour_rotation_24h(0, 0) == pytest.approx(hour_rotation_24h(24, 0))

    def test_minutes_advance_hour_hand(self):
        at_6_00 = hour_rotation_24h(6, 0)
        at_6_30 = hour_rotation_24h(6, 30)
        assert at_6_30 > at_6_00


# -- Preferences -------------------------------------------------------------


class TestClockPrefs:
    """Preferences load/save via Config.applet_prefs."""

    def test_defaults_when_no_config(self):
        # Given no config
        clock = ClockApplet(48)
        # Then -- defaults: analog 12h, no date
        assert clock._show_digital is False
        assert clock._show_military is False
        assert clock._show_date is False

    def test_loads_prefs_from_config(self):
        # Given config with saved prefs
        config = Config(
            applet_prefs={
                "clock": {
                    "show_digital": True,
                    "show_military": True,
                    "show_date": True,
                }
            }
        )
        # When
        clock = ClockApplet(48, config=config)
        # Then
        assert clock._show_digital is True
        assert clock._show_military is True
        assert clock._show_date is True

    def test_saves_prefs_to_config(self, tmp_path):
        # Given config with save path
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        clock = ClockApplet(48, config=config)

        # When
        clock._show_digital = True
        clock._save_prefs()

        # Then -- prefs written to config
        assert config.applet_prefs["clock"]["show_digital"] is True
        # And persisted to disk
        reloaded = Config.load(path)
        assert reloaded.applet_prefs["clock"]["show_digital"] is True

    def test_partial_prefs_use_defaults(self):
        # Given config with only one pref set
        config = Config(applet_prefs={"clock": {"show_military": True}})
        # When
        clock = ClockApplet(48, config=config)
        # Then -- missing prefs default to False
        assert clock._show_digital is False
        assert clock._show_military is True
        assert clock._show_date is False


# -- Rendering ---------------------------------------------------------------


class TestClockRendering:
    """create_icon produces a valid pixbuf in all modes."""

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_analog_12h_renders(self, size):
        # Given analog 12h mode (default)
        clock = ClockApplet(size)
        # When
        pixbuf = clock.create_icon(size)
        # Then
        assert pixbuf is not None
        assert pixbuf.get_width() == size
        assert pixbuf.get_height() == size

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_analog_24h_renders(self, size):
        # Given analog 24h mode
        config = Config(applet_prefs={"clock": {"show_military": True}})
        clock = ClockApplet(size, config=config)
        # When
        pixbuf = clock.create_icon(size)
        # Then
        assert pixbuf is not None
        assert pixbuf.get_width() == size

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_digital_12h_renders(self, size):
        # Given digital 12h mode
        config = Config(applet_prefs={"clock": {"show_digital": True}})
        clock = ClockApplet(size, config=config)
        # When
        pixbuf = clock.create_icon(size)
        # Then
        assert pixbuf is not None
        assert pixbuf.get_width() == size

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_digital_24h_with_date_renders(self, size):
        # Given digital 24h mode with date
        config = Config(
            applet_prefs={
                "clock": {
                    "show_digital": True,
                    "show_military": True,
                    "show_date": True,
                }
            }
        )
        clock = ClockApplet(size, config=config)
        # When
        pixbuf = clock.create_icon(size)
        # Then
        assert pixbuf is not None
        assert pixbuf.get_width() == size


class TestClockTooltip:
    """Tooltip (item.name) updates on each render."""

    def test_tooltip_updates_on_render(self):
        # Given
        clock = ClockApplet(48)
        # When
        clock.create_icon(48)
        # Then -- name is no longer the static "Clock"
        assert clock.item.name != "Clock"
        # And contains the current month abbreviation
        expected_month = time.strftime("%b")
        assert expected_month in clock.item.name


class TestClockMenuItems:
    """get_menu_items returns 3 toggle items."""

    def test_returns_three_items(self):
        # Given
        clock = ClockApplet(48)
        # When
        items = clock.get_menu_items()
        # Then
        assert len(items) == 3

    def test_date_insensitive_in_analog_mode(self):
        # Given analog mode (show_digital=False)
        clock = ClockApplet(48)
        # When
        items = clock.get_menu_items()
        # Then -- "Show Date" should be insensitive
        date_item = items[2]
        assert not date_item.get_sensitive()

    def test_date_sensitive_in_digital_mode(self):
        # Given digital mode
        config = Config(applet_prefs={"clock": {"show_digital": True}})
        clock = ClockApplet(48, config=config)
        # When
        items = clock.get_menu_items()
        # Then -- "Show Date" should be sensitive
        date_item = items[2]
        assert date_item.get_sensitive()
