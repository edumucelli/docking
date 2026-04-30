"""Tests for the Thermals applet."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import gi
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

import docking.applets.thermals.applet as thermals_applet_mod
from docking.applets.thermals.applet import ThermalsApplet
from docking.applets.thermals.render import render_icon
from docking.applets.thermals.state import (
    FanReading,
    TemperatureUnit,
    ThermalReading,
    ThermalSnapshot,
    build_tooltip,
    format_rpm_compact,
    format_temperature,
    format_temperature_compact,
    parse_sensors_output,
    prefs_from_mapping,
    prefs_payload,
    read_thermal_snapshot,
    reading_label,
    thermal_color,
    thermal_level,
)
from docking.core.config import Config


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)


def _snapshot() -> ThermalSnapshot:
    return ThermalSnapshot(
        available=True,
        hottest=ThermalReading(
            chip="coretemp-isa-0000",
            label="Package id 0",
            celsius=74.2,
        ),
        fan=FanReading(chip="thinkpad-isa-0000", label="fan1", rpm=2987),
    )


def _make_applet(config: Config | None = None) -> ThermalsApplet:
    with patch("docking.applets.thermals.applet.BackgroundWorker", _ImmediateWorker):
        return ThermalsApplet(48, config=config)


class TestThermalsState:
    def test_parse_sensors_output_picks_hottest_temp_and_fastest_fan(self):
        degree = "\N{DEGREE SIGN}"
        text = (
            "acpitz-acpi-0\n"
            "Adapter: ACPI interface\n"
            "temp1:        +45.0 C\n"
            "\n"
            "coretemp-isa-0000\n"
            "Adapter: ISA adapter\n"
            f"Package id 0: +68.0{degree}C  (high = +100.0{degree}C)\n"
            f"Core 0:       +74.2{degree}C\n"
            "\n"
            "thinkpad-isa-0000\n"
            "Adapter: ISA adapter\n"
            "fan1:        2987 RPM\n"
            "fan2:        2200 RPM\n"
        )

        snapshot = parse_sensors_output(text)

        assert snapshot.available is True
        assert snapshot.error == ""
        assert snapshot.hottest is not None
        assert snapshot.hottest.label == "Core 0"
        assert snapshot.hottest.celsius == pytest.approx(74.2)
        assert snapshot.fan is not None
        assert snapshot.fan.label == "fan1"
        assert snapshot.fan.rpm == 2987

    def test_parse_sensors_output_handles_empty_output(self):
        snapshot = parse_sensors_output("Adapter: ISA adapter\n")

        assert snapshot.available is True
        assert snapshot.hottest is None
        assert snapshot.fan is None

    def test_read_thermal_snapshot_without_sensors(self):
        snapshot = read_thermal_snapshot(which=lambda _cmd: None)

        assert snapshot.available is False
        assert "lm-sensors" in snapshot.error

    def test_read_thermal_snapshot_runs_sensors(self):
        def run(cmd, **kwargs):
            assert cmd == ["sensors"]
            assert kwargs["capture_output"] is True
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="chip\nAdapter: ISA adapter\ntemp1: +51.0 C\nfan1: 1200 RPM\n",
                stderr="",
            )

        snapshot = read_thermal_snapshot(which=lambda _cmd: "/usr/bin/sensors", run=run)

        assert snapshot.hottest is not None
        assert snapshot.hottest.celsius == pytest.approx(51.0)
        assert snapshot.fan is not None
        assert snapshot.fan.rpm == 1200

    def test_read_thermal_snapshot_reports_command_failure(self):
        snapshot = read_thermal_snapshot(
            which=lambda _cmd: "/usr/bin/sensors",
            run=lambda cmd, **kwargs: subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr="no sensors",
            ),
        )

        assert snapshot.available is True
        assert snapshot.error == "no sensors"

    def test_formatting_helpers(self):
        reading = ThermalReading(chip="coretemp", label="Package", celsius=61.4)

        assert reading_label(reading) == "coretemp Package"
        assert format_temperature(61.44) == "61.4\N{DEGREE SIGN}C"
        assert (
            format_temperature(
                61.44,
                temperature_unit=TemperatureUnit.FAHRENHEIT,
            )
            == "142.6\N{DEGREE SIGN}F"
        )
        assert (
            format_temperature_compact(
                74.2,
                temperature_unit=TemperatureUnit.FAHRENHEIT,
            )
            == "166\N{DEGREE SIGN}"
        )
        assert format_rpm_compact(980) == "980"
        assert format_rpm_compact(2987) == "3.0k"
        assert thermal_level(35.0) == pytest.approx(0.0)
        assert thermal_level(90.0) == pytest.approx(1.0)
        r, g, b = thermal_color(90.0)
        assert r > g > b

    def test_prefs_helpers(self):
        prefs = prefs_from_mapping({"temperature_unit": "fahrenheit"})

        assert prefs.temperature_unit == TemperatureUnit.FAHRENHEIT
        assert prefs_payload(temperature_unit=TemperatureUnit.FAHRENHEIT) == {
            "temperature_unit": "fahrenheit"
        }

    def test_build_tooltip(self):
        text = build_tooltip(snapshot=_snapshot(), cadence_seconds=5)

        assert "Thermals" in text
        assert "Hot: coretemp-isa-0000 Package id 0 74.2\N{DEGREE SIGN}C" in text
        assert "Fan: thinkpad-isa-0000 fan1 2987 RPM" in text
        assert "Samples every 5 seconds" in text
        assert "Loading" in build_tooltip(snapshot=None, loading=True)
        assert "lm-sensors" in build_tooltip(
            snapshot=ThermalSnapshot(available=False, error="lm-sensors not installed")
        )

    def test_build_tooltip_uses_selected_unit(self):
        text = build_tooltip(
            snapshot=_snapshot(),
            temperature_unit=TemperatureUnit.FAHRENHEIT,
        )

        assert "Hot: coretemp-isa-0000 Package id 0 165.6\N{DEGREE SIGN}F" in text


class TestThermalsRender:
    @pytest.mark.parametrize("size", [32, 48, 64])
    def test_render_icon_states(self, size):
        for snapshot in (_snapshot(), None, ThermalSnapshot(available=False)):
            pixbuf = render_icon(
                size=size,
                snapshot=snapshot,
                loading=snapshot is None,
                temperature_unit=TemperatureUnit.FAHRENHEIT,
            )

            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size


class TestThermalsApplet:
    def test_initial_tooltip(self):
        applet = _make_applet()

        assert "Thermals" in applet.item.name

    def test_menu_contains_snapshot_and_refresh(self):
        applet = _make_applet()
        applet._snapshot = _snapshot()

        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]

        assert any(label.startswith("Hot:") for label in labels)
        assert any(label.startswith("Fan:") for label in labels)
        assert "Samples every 5 seconds" in labels
        assert "Temperature Unit" in labels
        assert "Refresh Now" in labels

    def test_loads_and_saves_temperature_unit_pref(self):
        config = Config(applet_prefs={"thermals": {"temperature_unit": "fahrenheit"}})
        applet = _make_applet(config=config)

        assert applet._temperature_unit == TemperatureUnit.FAHRENHEIT

        item = Gtk.RadioMenuItem(label="Celsius")
        item.set_active(True)
        applet._on_temperature_unit_selected(
            widget=item,
            temperature_unit=TemperatureUnit.CELSIUS,
        )

        assert config.applet_prefs["thermals"] == {"temperature_unit": "celsius"}

    def test_start_schedules_timers(self, monkeypatch):
        add = MagicMock(side_effect=[11, 12])
        monkeypatch.setattr(thermals_applet_mod.GLib, "timeout_add_seconds", add)
        applet = _make_applet()

        applet.start(lambda: None)

        assert applet._timer_id == 11
        assert applet._startup_fetch_timer_id == 12

    def test_stop_removes_timers(self, monkeypatch):
        removed = []
        applet = _make_applet()
        applet._timer_id = 11
        applet._startup_fetch_timer_id = 12
        monkeypatch.setattr(
            thermals_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.stop()

        assert removed == [11, 12]
        assert applet._timer_id == 0
        assert applet._startup_fetch_timer_id == 0

    def test_fetch_result_updates_snapshot(self):
        applet = _make_applet()
        applet._request_id = 7

        assert applet._on_fetch_result(request_id=7, snapshot=_snapshot()) is False

        assert applet._loading is False
        assert applet._snapshot == _snapshot()

    def test_stale_fetch_result_ignored(self):
        applet = _make_applet()
        applet._request_id = 7
        applet.present = MagicMock()

        assert applet._on_fetch_result(request_id=6, snapshot=_snapshot()) is False

        applet.present.assert_not_called()

    def test_fetch_error_sets_error_snapshot(self):
        applet = _make_applet()
        applet._request_id = 7

        assert applet._on_fetch_error(request_id=7, exc=RuntimeError("boom")) is False

        assert applet._loading is False
        assert applet._snapshot is not None
        assert applet._snapshot.error == "boom"
