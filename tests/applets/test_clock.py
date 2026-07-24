"""Tests for the clock applet -- state helpers, prefs, rendering, alarms."""

import math
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.applets.clock.applet as clock_mod
from docking.applets.clock.applet import ClockApplet
from docking.applets.clock.state import (
    build_tooltip,
    check_alarm,
    compute_alarm_target,
    hour_rotation_12h,
    hour_rotation_24h,
    load_prefs,
    minute_rotation,
    seconds_rotation,
)
from docking.core.config import Config


class TestMinuteRotation:
    def test_minute_0_points_up(self):
        assert minute_rotation(minute=0) == pytest.approx(math.pi)

    def test_minute_15_points_right(self):
        assert minute_rotation(minute=15) == pytest.approx(1.5 * math.pi)

    def test_minute_30_points_down(self):
        assert minute_rotation(minute=30) == pytest.approx(2 * math.pi)

    def test_minute_45_points_left(self):
        assert minute_rotation(minute=45) == pytest.approx(2.5 * math.pi)

    def test_continuous_increase(self):
        angles = [minute_rotation(minute=m) for m in range(60)]
        for i in range(1, len(angles)):
            assert angles[i] > angles[i - 1]


class TestSecondsRotation:
    def test_second_0_points_up(self):
        assert seconds_rotation(second=0) == pytest.approx(math.pi)

    def test_second_15_points_right(self):
        assert seconds_rotation(second=15) == pytest.approx(1.5 * math.pi)

    def test_continuous_increase(self):
        angles = [seconds_rotation(second=s) for s in range(60)]
        for i in range(1, len(angles)):
            assert angles[i] > angles[i - 1]


class TestHourRotation12h:
    def test_12_oclock(self):
        assert hour_rotation_12h(hour=0, minute=0) == pytest.approx(math.pi)
        assert hour_rotation_12h(hour=12, minute=0) == pytest.approx(math.pi)

    def test_3_oclock(self):
        assert hour_rotation_12h(hour=3, minute=0) == pytest.approx(1.5 * math.pi)

    def test_6_oclock(self):
        assert hour_rotation_12h(hour=6, minute=0) == pytest.approx(2 * math.pi)

    def test_9_oclock(self):
        assert hour_rotation_12h(hour=9, minute=0) == pytest.approx(2.5 * math.pi)

    def test_minutes_advance_hour_hand(self):
        at_3_00 = hour_rotation_12h(hour=3, minute=0)
        at_3_30 = hour_rotation_12h(hour=3, minute=30)
        assert at_3_30 > at_3_00


class TestHourRotation24h:
    def test_0_oclock(self):
        assert hour_rotation_24h(hour=0, minute=0) == pytest.approx(math.pi)

    def test_6_oclock(self):
        assert hour_rotation_24h(hour=6, minute=0) == pytest.approx(1.5 * math.pi)

    def test_12_oclock(self):
        assert hour_rotation_24h(hour=12, minute=0) == pytest.approx(2 * math.pi)

    def test_18_oclock(self):
        assert hour_rotation_24h(hour=18, minute=0) == pytest.approx(2.5 * math.pi)

    def test_minutes_advance_hour_hand(self):
        assert hour_rotation_24h(hour=6, minute=30) > hour_rotation_24h(
            hour=6, minute=0
        )


class TestClockState:
    def test_load_prefs_defaults(self):
        assert load_prefs(None, now_ts=100) == (False, False, False, False, None)

    def test_load_prefs_discards_past_alarm(self):
        result = load_prefs({"alarm_target": 99, "show_seconds": True}, now_ts=100)
        assert result == (False, False, False, True, None)

    def test_compute_alarm_target_rolls_to_next_day_when_needed(self, monkeypatch):
        fake_now = time.struct_time((2024, 1, 2, 10, 30, 0, 1, 2, -1))
        base = time.mktime(fake_now)
        monkeypatch.setattr(clock_mod.time, "localtime", lambda *_args: fake_now)
        monkeypatch.setattr(clock_mod.time, "mktime", time.mktime)
        target = compute_alarm_target(now_ts=base, hour=10, minute=30)
        assert target == int(time.mktime((2024, 1, 3, 10, 30, 0, 1, 2, -1)))

    def test_check_alarm_only_fires_at_or_after_target(self):
        assert not check_alarm(now_ts=99, alarm_target=100)
        assert check_alarm(now_ts=100, alarm_target=100)

    def test_build_tooltip_includes_alarm(self):
        now = time.struct_time((2024, 1, 2, 10, 15, 0, 1, 2, -1))
        alarm = int(time.mktime((2024, 1, 2, 12, 45, 0, 1, 2, -1)))
        tooltip = build_tooltip(now, True, alarm_target=alarm)
        assert "Alarm:" in tooltip
        assert "12:45" in tooltip


class TestClockPrefs:
    def test_defaults_when_no_config(self):
        clock = ClockApplet(48, config=Config())
        assert clock._show_digital is False
        assert clock._show_military is False
        assert clock._show_date is False
        assert clock._show_seconds is False
        assert clock._alarm_target is None

    def test_loads_prefs_from_config(self):
        config = Config(
            applet_prefs={
                "clock": {
                    "show_digital": True,
                    "show_military": True,
                    "show_date": True,
                    "show_seconds": True,
                    "alarm_target": int(time.time()) + 600,
                }
            }
        )
        clock = ClockApplet(48, config=config)
        assert clock._show_digital is True
        assert clock._show_military is True
        assert clock._show_date is True
        assert clock._show_seconds is True
        assert clock._alarm_target is not None

    def test_saves_prefs_to_config(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        clock = ClockApplet(48, config=config)

        clock._show_digital = True
        clock._show_seconds = True
        clock._alarm_target = 1_700_000_000
        clock._save_prefs()

        assert config.applet_prefs["clock"]["show_digital"] is True
        assert config.applet_prefs["clock"]["show_seconds"] is True
        assert config.applet_prefs["clock"]["alarm_target"] == 1_700_000_000
        reloaded = Config.load(path)
        assert reloaded.applet_prefs["clock"]["show_seconds"] is True
        assert reloaded.applet_prefs["clock"]["alarm_target"] == 1_700_000_000

    def test_stale_alarm_is_discarded_on_startup(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(applet_prefs={"clock": {"alarm_target": 1}})
        config.save(path)
        config = Config.load(path)

        clock = ClockApplet(48, config=config)

        assert clock._alarm_target is None
        assert config.applet_prefs["clock"]["alarm_target"] is None
        assert Config.load(path).applet_prefs["clock"]["alarm_target"] is None


class TestClockRendering:
    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_analog_12h_renders(self, size):
        clock = ClockApplet(size, config=Config())
        pixbuf = clock.create_icon(size)
        assert pixbuf is not None
        assert pixbuf.get_width() == size
        assert pixbuf.get_height() == size

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_analog_with_seconds_renders(self, size):
        config = Config(applet_prefs={"clock": {"show_seconds": True}})
        clock = ClockApplet(size, config=config)
        pixbuf = clock.create_icon(size)
        assert pixbuf is not None
        assert pixbuf.get_width() == size

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_digital_12h_renders(self, size):
        config = Config(applet_prefs={"clock": {"show_digital": True}})
        clock = ClockApplet(size, config=config)
        pixbuf = clock.create_icon(size)
        assert pixbuf is not None
        assert pixbuf.get_width() == size

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_digital_with_seconds_and_date_renders(self, size):
        config = Config(
            applet_prefs={
                "clock": {
                    "show_digital": True,
                    "show_military": True,
                    "show_date": True,
                    "show_seconds": True,
                }
            }
        )
        clock = ClockApplet(size, config=config)
        pixbuf = clock.create_icon(size)
        assert pixbuf is not None
        assert pixbuf.get_width() == size


class TestClockTooltip:
    def test_tooltip_updates_on_render(self):
        clock = ClockApplet(48, config=Config())
        clock.create_icon(48)
        assert clock.item.name != "Clock"
        assert time.strftime("%b") in clock.item.name

    def test_tooltip_shows_alarm_target(self):
        config = Config(
            applet_prefs={"clock": {"alarm_target": int(time.time()) + 3600}}
        )
        clock = ClockApplet(48, config=config)
        clock.refresh_tooltip()
        assert "Alarm:" in clock.item.name


class TestClockMenuItems:
    def test_returns_six_items_without_alarm(self):
        clock = ClockApplet(48, config=Config())
        items = clock.get_menu_items()
        assert len(items) == 6

    def test_date_insensitive_in_analog_mode(self):
        clock = ClockApplet(48, config=Config())
        items = clock.get_menu_items()
        assert not items[2].get_sensitive()

    def test_date_sensitive_in_digital_mode(self):
        config = Config(applet_prefs={"clock": {"show_digital": True}})
        clock = ClockApplet(48, config=config)
        items = clock.get_menu_items()
        assert items[2].get_sensitive()

    def test_clear_alarm_item_is_visible_when_alarm_is_set(self):
        config = Config(
            applet_prefs={"clock": {"alarm_target": int(time.time()) + 600}}
        )
        clock = ClockApplet(48, config=config)
        labels = [
            item.get_label()
            for item in clock.get_menu_items()
            if hasattr(item, "get_label")
        ]
        assert "Clear Alarm" in labels

    def test_acknowledge_item_is_visible_when_alarm_is_urgent(self):
        clock = ClockApplet(48, config=Config())
        clock.item.is_urgent = True
        labels = [
            item.get_label()
            for item in clock.get_menu_items()
            if hasattr(item, "get_label")
        ]
        assert "Acknowledge Alarm" in labels


class TestClockInteractions:
    def test_toggle_handlers_update_state_and_refresh(self):
        clock = ClockApplet(48, config=Config())
        clock._save_prefs = MagicMock()
        clock.present = MagicMock()

        w_true = MagicMock()
        w_true.get_active.return_value = True
        clock._on_toggle_digital(w_true)
        clock._on_toggle_military(w_true)
        clock._on_toggle_seconds(w_true)

        w_false = MagicMock()
        w_false.get_active.return_value = False
        clock._on_toggle_date(w_false)

        assert clock._show_digital is True
        assert clock._show_military is True
        assert clock._show_seconds is True
        assert clock._show_date is False
        assert clock._save_prefs.call_count == 4
        assert clock.present.call_count == 4

    def test_start_and_stop_delegate_to_clock_timer(self):
        clock = ClockApplet(48, config=Config())
        clock._timer = MagicMock()
        clock.start(lambda: None)
        clock.stop()
        clock._timer.start.assert_called_once()
        clock._timer.stop.assert_called_once()

    def test_tick_redraws_every_second_when_show_seconds_enabled(self, monkeypatch):
        clock = ClockApplet(
            48, config=Config(applet_prefs={"clock": {"show_seconds": True}})
        )
        clock.present = MagicMock()
        monkeypatch.setattr(
            clock_mod.time, "localtime", lambda *_args: SimpleNamespace(tm_min=10)
        )
        monkeypatch.setattr(clock_mod.time, "time", lambda: 100)
        clock._on_tick()
        assert clock.present.call_count == 1

    def test_tick_only_redraws_on_minute_change_when_seconds_disabled(
        self, monkeypatch
    ):
        clock = ClockApplet(48, config=Config())
        clock.present = MagicMock()
        clock._last_minute = 10
        monkeypatch.setattr(
            clock_mod.time, "localtime", lambda *_args: SimpleNamespace(tm_min=10)
        )
        monkeypatch.setattr(clock_mod.time, "time", lambda: 100)
        clock._on_tick()
        assert clock.present.call_count == 0

        monkeypatch.setattr(
            clock_mod.time, "localtime", lambda *_args: SimpleNamespace(tm_min=11)
        )
        clock._on_tick()
        assert clock.present.call_count == 1
        assert clock._last_minute == 11

    def test_tick_triggers_alarm_and_clears_target(self, monkeypatch):
        clock = ClockApplet(48, config=Config())
        clock.present = MagicMock()
        clock._save_prefs = MagicMock()
        clock._alarm_target = 100
        monkeypatch.setattr(
            clock_mod.time, "localtime", lambda *_args: SimpleNamespace(tm_min=10)
        )
        monkeypatch.setattr(clock_mod.time, "time", lambda: 100)
        monkeypatch.setattr(clock_mod.GLib, "get_monotonic_time", lambda: 555)

        clock._on_tick()

        assert clock.item.is_urgent is True
        assert clock.item.last_urgent == 555
        assert clock._alarm_target is None
        clock._save_prefs.assert_called_once()
        assert clock.present.call_count == 1

    def test_set_alarm_persists_future_target(self, monkeypatch):
        clock = ClockApplet(48, config=Config())
        clock.present = MagicMock()
        clock._save_prefs = MagicMock()
        fake_now = 1_700_000_000
        real_localtime = time.localtime
        monkeypatch.setattr(clock_mod.time, "time", lambda: fake_now)
        monkeypatch.setattr(
            clock_mod.time, "localtime", lambda *_args: real_localtime(fake_now)
        )
        monkeypatch.setattr(clock_mod.time, "mktime", time.mktime)

        clock._set_alarm(hour=11, minute=45)

        assert clock._alarm_target is not None
        clock._save_prefs.assert_called_once()
        assert clock.present.call_count == 1

    def test_click_acknowledges_urgent_alarm(self):
        clock = ClockApplet(48, config=Config())
        clock.item.is_urgent = True
        clock.present = MagicMock()
        clock._calendar_popup = MagicMock()
        clock._calendar_popup.get_visible.return_value = True

        clock.on_clicked()

        assert clock.item.is_urgent is False
        assert clock.present.call_count == 1
        clock._calendar_popup.hide.assert_called_once()

    def test_click_shows_shared_calendar_popup(self, monkeypatch):
        clock = ClockApplet(48, config=Config())
        popup = MagicMock()
        show_calendar_popup = MagicMock(return_value=popup)
        monkeypatch.setattr(clock_mod, "show_calendar_popup", show_calendar_popup)

        clock.on_clicked()

        assert clock._calendar_popup is popup
        show_calendar_popup.assert_called_once_with(
            popup=None, anchor=clock.popup_anchor
        )

    def test_stop_destroys_calendar_popup(self):
        clock = ClockApplet(48, config=Config())
        clock._timer = MagicMock()
        popup = MagicMock()
        clock._calendar_popup = popup

        clock.stop()

        popup.destroy.assert_called_once()
        assert clock._calendar_popup is None

    def test_clear_alarm_clears_target_and_urgency(self):
        clock = ClockApplet(48, config=Config())
        clock._alarm_target = int(time.time()) + 60
        clock.item.is_urgent = True
        clock._save_prefs = MagicMock()
        clock.present = MagicMock()

        clock._clear_alarm()

        assert clock._alarm_target is None
        assert clock.item.is_urgent is False
        clock._save_prefs.assert_called_once()
        assert clock.present.call_count == 1


class TestClockTimer:
    def test_start_registers_glib_timeout(self, monkeypatch):
        timer = clock_mod._ClockTimer()
        monkeypatch.setattr(clock_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 222)
        cb = MagicMock()
        timer.start(cb)
        assert timer._timer_id == 222
        assert timer._callback is cb

    def test_stop_removes_source_and_clears_callback(self, monkeypatch):
        timer = clock_mod._ClockTimer()
        timer._timer_id = 88
        timer._callback = MagicMock()
        removed = []
        monkeypatch.setattr(
            clock_mod.GLib, "source_remove", lambda timer_id: removed.append(timer_id)
        )
        timer.stop()
        assert removed == [88]
        assert timer._timer_id == 0
        assert timer._callback is None

    def test_tick_calls_callback_each_second(self):
        timer = clock_mod._ClockTimer()
        callback = MagicMock()
        timer._callback = callback

        assert timer._tick() is True
        assert timer._tick() is True
        assert callback.call_count == 2
