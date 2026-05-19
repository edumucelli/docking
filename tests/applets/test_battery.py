"""Tests for the battery applet -- sysfs parsing, launchers, and icon mapping."""

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import docking.applets.battery.applet as battery_applet_mod
import docking.applets.battery.state as battery_state_mod
from docking.applets.battery.applet import BatteryApplet
from docking.applets.battery.state import (
    BatteryState,
    open_power_settings,
    power_settings_command,
    read_battery,
    resolve_battery_icon,
    tooltip_text,
)
from docking.core.config import Config


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
    def test_reads_sysfs(self, tmp_path, monkeypatch):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("85\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")

        monkeypatch.setattr(
            battery_state_mod,
            "_upower_seconds_remaining",
            lambda **_kwargs: None,
        )
        state = read_battery("BAT0", base=tmp_path)

        assert state is not None
        assert state.capacity == 85
        assert state.icon_name == "battery-good"
        assert state.status == "Discharging"
        assert state.seconds_remaining is None

    def test_charging_suffix(self, tmp_path, monkeypatch):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("50\n")
        (bat / "capacity_level").write_text("Low\n")
        (bat / "status").write_text("Charging\n")

        state = read_battery("BAT0", base=tmp_path)
        assert state is not None
        assert state.icon_name == "battery-low-charging"

    def test_estimates_discharge_time_from_energy_and_power(
        self,
        tmp_path,
        monkeypatch,
    ):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("40\n")
        (bat / "capacity_level").write_text("Low\n")
        (bat / "status").write_text("Discharging\n")
        (bat / "energy_now").write_text("2000000\n")
        (bat / "power_now").write_text("1000000\n")
        monkeypatch.setattr(
            battery_state_mod,
            "_upower_seconds_remaining",
            lambda **_kwargs: None,
        )

        state = read_battery("BAT0", base=tmp_path)

        assert state is not None
        assert state.seconds_remaining == 7200

    def test_estimates_charge_time_from_energy_gap_and_power(
        self,
        tmp_path,
        monkeypatch,
    ):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("50\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Charging\n")
        (bat / "energy_now").write_text("1000000\n")
        (bat / "energy_full").write_text("3000000\n")
        (bat / "power_now").write_text("1000000\n")
        monkeypatch.setattr(
            battery_state_mod,
            "_upower_seconds_remaining",
            lambda **_kwargs: None,
        )

        state = read_battery("BAT0", base=tmp_path)

        assert state is not None
        assert state.seconds_remaining == 7200

    def test_prefers_upower_estimate_when_available(self, tmp_path, monkeypatch):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("50\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")
        (bat / "charge_now").write_text("900000\n")
        (bat / "current_now").write_text("450000\n")
        monkeypatch.setattr(
            battery_state_mod,
            "_upower_seconds_remaining",
            lambda **_kwargs: 5400,
        )

        state = read_battery("BAT0", base=tmp_path)

        assert state is not None
        assert state.seconds_remaining == 5400

    def test_estimates_discharge_time_from_charge_and_current(
        self, tmp_path, monkeypatch
    ):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("50\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")
        (bat / "charge_now").write_text("900000\n")
        (bat / "current_now").write_text("450000\n")

        monkeypatch.setattr(
            battery_state_mod,
            "_upower_seconds_remaining",
            lambda **_kwargs: None,
        )

        state = read_battery("BAT0", base=tmp_path)

        assert state is not None
        assert state.seconds_remaining == 7200

    def test_estimates_charge_time_from_charge_gap_and_current(
        self, tmp_path, monkeypatch
    ):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("25\n")
        (bat / "capacity_level").write_text("Low\n")
        (bat / "status").write_text("Charging\n")
        (bat / "charge_now").write_text("250000\n")
        (bat / "charge_full").write_text("1000000\n")
        (bat / "current_now").write_text("250000\n")

        monkeypatch.setattr(
            battery_state_mod,
            "_upower_seconds_remaining",
            lambda **_kwargs: None,
        )

        state = read_battery("BAT0", base=tmp_path)

        assert state is not None
        assert state.seconds_remaining == 10800

    def test_returns_none_when_missing(self, tmp_path):
        assert read_battery("BAT0", base=tmp_path) is None

    def test_returns_none_on_bad_data(self, tmp_path):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("not_a_number\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")
        assert read_battery("BAT0", base=tmp_path) is None


class TestUpowerParsing:
    def test_parses_fractional_hours(self):
        text = "time to empty:       1.3 hours"

        assert (
            battery_state_mod._parse_upower_duration_seconds(
                text=text, key="time to empty"
            )
            == 4680
        )

    def test_parses_hours_and_minutes(self):
        assert battery_state_mod._parse_duration_seconds(raw="1 hour 2 minutes") == 3720

    def test_ignores_unknown_duration(self):
        assert battery_state_mod._parse_duration_seconds(raw="unknown") is None

    def test_upower_seconds_remaining_edges(self, monkeypatch):
        monkeypatch.setattr(battery_state_mod.shutil, "which", lambda _cmd: None)
        assert (
            battery_state_mod._upower_seconds_remaining(
                bat_name="BAT0",
                status="Discharging",
            )
            is None
        )

        monkeypatch.setattr(
            battery_state_mod.shutil, "which", lambda _cmd: "/bin/upower"
        )
        assert (
            battery_state_mod._upower_seconds_remaining(bat_name="BAT0", status="Full")
            is None
        )

        monkeypatch.setattr(
            battery_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout="time to full: 30 minutes\n",
                stderr="",
            ),
        )
        assert (
            battery_state_mod._upower_seconds_remaining(
                bat_name="BAT0",
                status="Charging",
            )
            == 1800
        )

        monkeypatch.setattr(
            battery_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1, stdout="", stderr=""
            ),
        )
        assert (
            battery_state_mod._upower_seconds_remaining(
                bat_name="BAT0",
                status="Charging",
            )
            is None
        )

        monkeypatch.setattr(
            battery_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["upower"], 2)
            ),
        )
        assert (
            battery_state_mod._upower_seconds_remaining(
                bat_name="BAT0",
                status="Charging",
            )
            is None
        )

        assert (
            battery_state_mod._parse_upower_duration_seconds(
                text="state: charging",
                key="time to full",
            )
            is None
        )

    def test_duration_helpers_cover_short_values(self):
        assert battery_state_mod._format_duration(59) == "1m"
        assert battery_state_mod._format_duration(0) is None
        assert battery_state_mod._parse_duration_seconds(raw="10 seconds") == 10
        assert battery_state_mod._parse_duration_seconds(raw="bad") is None
        assert battery_state_mod._seconds_from_rate(remaining=0, rate=1) is None
        assert battery_state_mod._positive_delta(full=None, now=1) is None
        assert battery_state_mod._positive_delta(full=1, now=1) is None


class TestTooltipText:
    def test_shows_time_left_when_estimate_is_known(self):
        state = BatteryState(
            icon_name="battery-good",
            capacity=67,
            status="Discharging",
            seconds_remaining=8040,
        )

        assert tooltip_text(state) == "Battery: 67% • 2h 14m left"

    def test_shows_until_full_when_charging_estimate_is_known(self):
        state = BatteryState(
            icon_name="battery-good-charging",
            capacity=43,
            status="Charging",
            seconds_remaining=3720,
        )

        assert tooltip_text(state) == "Battery: 43% • 1h 02m until full"

    def test_hides_estimate_when_time_is_unknown(self):
        state = BatteryState(
            icon_name="battery-good",
            capacity=100,
            status="Full",
            seconds_remaining=None,
        )

        assert tooltip_text(state) == "Battery: 100%"

    def test_hides_estimate_for_non_charging_status(self):
        state = BatteryState(
            icon_name="battery-full",
            capacity=100,
            status="Full",
            seconds_remaining=60,
        )

        assert tooltip_text(state) == "Battery: 100%"


class TestPowerSettingsLauncher:
    def test_prefers_first_available_power_settings_command(self, monkeypatch):
        monkeypatch.setattr(
            battery_state_mod.shutil,
            "which",
            lambda cmd: (
                "/usr/bin/gnome-control-center"
                if cmd == "gnome-control-center"
                else None
            ),
        )

        assert power_settings_command() == ["gnome-control-center", "power"]

    def test_returns_none_when_no_power_settings_tool_exists(self, monkeypatch):
        monkeypatch.setattr(battery_state_mod.shutil, "which", lambda _cmd: None)

        assert power_settings_command() is None

    def test_open_power_settings_launches_detected_command(self, monkeypatch):
        launched: list[list[str]] = []
        monkeypatch.setattr(
            battery_state_mod,
            "power_settings_command",
            lambda: ["mate-power-preferences"],
        )
        monkeypatch.setattr(
            battery_state_mod.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(cmd),
        )

        assert open_power_settings() is True
        assert launched == [["mate-power-preferences"]]

    def test_open_power_settings_handles_missing_and_launch_failure(self, monkeypatch):
        monkeypatch.setattr(battery_state_mod, "power_settings_command", lambda: None)
        assert open_power_settings() is False

        monkeypatch.setattr(
            battery_state_mod,
            "power_settings_command",
            lambda: ["mate-power-preferences"],
        )
        monkeypatch.setattr(
            battery_state_mod.subprocess,
            "Popen",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")),
        )
        assert open_power_settings() is False


class TestBatteryAppletRendering:
    def test_renders_valid_pixbuf(self):
        applet = BatteryApplet(48)
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None

    def test_renders_with_percent_label_enabled(self):
        state = BatteryState(
            icon_name="battery-good",
            capacity=72,
            status="Discharging",
            seconds_remaining=None,
        )
        applet = BatteryApplet(48, config=Config(applet_prefs={"battery": {}}))
        applet._state = state
        applet._show_percent = True

        pixbuf = applet.create_icon(48)

        assert pixbuf is not None

    def test_menu_shows_power_settings_when_available(self, monkeypatch):
        opened: list[str] = []
        monkeypatch.setattr(
            battery_applet_mod,
            "power_settings_command",
            lambda: ["mate-power-preferences"],
        )
        monkeypatch.setattr(
            battery_applet_mod,
            "open_power_settings",
            lambda: opened.append("opened") or True,
        )

        applet = BatteryApplet(48)
        items = applet.get_menu_items()

        assert [item.get_label() for item in items] == [
            "Show Percent",
            "",
            "Power Settings",
        ]
        callback, args = items[2]._signals["activate"][0]
        callback(None, *args)
        assert opened == ["opened"]

    def test_menu_keeps_show_percent_without_power_settings_tool(self, monkeypatch):
        monkeypatch.setattr(battery_applet_mod, "power_settings_command", lambda: None)

        applet = BatteryApplet(48)
        assert [item.get_label() for item in applet.get_menu_items()] == [
            "Show Percent"
        ]

    def test_loads_and_saves_show_percent_pref(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(applet_prefs={"battery": {"show_percent": True}})
        config.save(path)
        config = Config.load(path)
        applet = BatteryApplet(48, config=config)

        assert applet._show_percent is True

        applet.present = MagicMock()
        widget = MagicMock()
        widget.get_active.return_value = False
        applet._on_toggle_percent(widget)

        assert config.applet_prefs["battery"] == {"show_percent": False}
        applet.present.assert_called_once()

    def test_tooltip_shows_percentage(self, tmp_path, monkeypatch):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("72\n")
        (bat / "capacity_level").write_text("Normal\n")
        (bat / "status").write_text("Discharging\n")
        monkeypatch.setattr(
            battery_state_mod,
            "_upower_seconds_remaining",
            lambda **_kwargs: None,
        )
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

    def test_full_charging_icon(self, tmp_path, monkeypatch):
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
        next_state = BatteryState(
            icon_name="battery-good",
            capacity=80,
            status="Discharging",
            seconds_remaining=None,
        )
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
