"""Tests for the battery applet -- sysfs parsing and icon mapping."""

import pytest
from unittest.mock import patch

from docking.applets.battery import (
    BatteryApplet,
    BatteryState,
    read_battery,
    resolve_battery_icon,
)


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
        assert resolve_battery_icon(level, status) == expected

    def test_unknown_level_returns_missing(self):
        assert resolve_battery_icon("bogus", "Discharging") == "battery-missing"


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
            "docking.applets.battery.read_battery",
            return_value=read_battery("BAT0", base=tmp_path),
        ):
            applet = BatteryApplet(48)
        assert applet.item.name == "72%"

    def test_tooltip_no_battery(self):
        with patch("docking.applets.battery.read_battery", return_value=None):
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
