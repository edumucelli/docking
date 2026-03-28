"""Tests for the battery applet -- sysfs parsing and icon mapping."""

from unittest.mock import MagicMock, patch

import pytest

import docking.applets.battery.applet as battery_applet_mod
from docking.applets.battery.applet import BatteryApplet
from docking.applets.battery.state import read_battery, resolve_battery_icon


class TestResolveBatteryIcon:
    @pytest.mark.parametrize(
        "level, status, expected",
        [
            ("Full", "Full", "battery-full-charging"),
            ("Full", "Discharging", "battery-full"),
            ("High", "Charging", "battery-good-charging"),
            ("Normal", "Discharging", "battery-good"),
            ("Low", "Discharging", "battery-low"),
            ("Low", "Charging", "battery-low-charging"),
            ("Critical", "Discharging", "battery-caution"),
            ("Unknown", "Unknown", "battery-empty"),
        ],
    )
    def test_icon_mapping(self, level, status, expected):
        assert resolve_battery_icon(capacity_level=level, status=status) == expected

    def test_unknown_level_returns_missing(self):
        assert (
            resolve_battery_icon(capacity_level="bogus", status="Discharging")
            == "battery-missing"
        )


class TestReadBattery:
    def test_reads_sysfs(self, tmp_path):
        # Given a fake BAT0 directory
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("85\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")

        # When
        state = read_battery("BAT0", base=tmp_path)

        # Then
        assert state is not None
        assert state.capacity == 85
        assert state.icon_name == "battery-good"

    def test_charging_suffix(self, tmp_path):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("50\n")
        (bat / "capacity_level").write_text("Low\n")
        (bat / "status").write_text("Charging\n")

        state = read_battery("BAT0", base=tmp_path)
        assert state is not None
        assert state.icon_name == "battery-low-charging"

    def test_returns_none_when_missing(self, tmp_path):
        assert read_battery("BAT0", base=tmp_path) is None

    def test_returns_none_on_bad_data(self, tmp_path):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("not_a_number\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")
        assert read_battery("BAT0", base=tmp_path) is None


class TestBatteryAppletRendering:
    def test_renders_valid_pixbuf(self):
        applet = BatteryApplet(48)
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None

    def test_no_menu_items(self):
        applet = BatteryApplet(48)
        assert applet.get_menu_items() == []

    def test_tooltip_shows_percentage(self, tmp_path):
        # Given battery at 72%
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("72\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")
        with patch(
            "docking.applets.battery.applet.read_battery",
            return_value=read_battery("BAT0", base=tmp_path),
        ):
            applet = BatteryApplet(48)
        assert applet.item.name == "Battery: 72%"

    def test_tooltip_no_battery(self):
        with patch("docking.applets.battery.applet.read_battery", return_value=None):
            applet = BatteryApplet(48)
        assert applet.item.name == "No battery"

    def test_full_charging_icon(self, tmp_path):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("100\n")
        (bat / "capacity_level").write_text("Full\n")
        (bat / "status").write_text("Full\n")
        state = read_battery("BAT0", base=tmp_path)
        assert state is not None
        assert state.icon_name == "battery-full-charging"

    def test_start_stop_and_tick_manage_polling_lifecycle(self, monkeypatch):
        timer_ids: list[int] = []
        removed: list[int] = []
        monkeypatch.setattr(
            battery_applet_mod.GLib,
            "timeout_add_seconds",
            lambda interval, cb: timer_ids.append(interval) or 77,
        )
        monkeypatch.setattr(
            battery_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        next_state = MagicMock()
        monkeypatch.setattr(battery_applet_mod, "read_battery", lambda: next_state)
        monkeypatch.setattr(
            battery_applet_mod, "render_icon", lambda **_kwargs: object()
        )

        applet = BatteryApplet(48)
        applet.start(lambda: None)
        assert applet._timer_id == 77
        assert timer_ids == [60]

        assert applet._tick() is True
        assert applet._state is next_state

        applet.stop()
        assert removed == [77]
        assert applet._timer_id == 0
