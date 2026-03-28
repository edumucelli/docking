"""Tests for the System Monitor applet -- parsing, temperature, and rendering."""

import pytest

import docking.applets.systemmonitor.applet as systemmonitor_mod
from docking.applets.systemmonitor.applet import SystemMonitorApplet
from docking.applets.systemmonitor.state import (
    CpuSample,
    cpu_hue_rgb,
    cpu_percent,
    parse_proc_meminfo,
    parse_proc_stat,
    tooltip_text,
)
from docking.applets.systemmonitor.temperature import (
    TemperatureReader,
    parse_acpi_output,
    parse_sensors_output,
    parse_sysfs_temperature,
    parse_vcgencmd_output,
)


class TestParseProcStat:
    def test_parses_first_line(self):
        text = "cpu  1000 200 300 5000 100 50 25\ncpu0 500 100 150 2500 50 25 12"
        sample = parse_proc_stat(text=text)
        assert sample.total == 6675
        assert sample.idle == 5100

    def test_zero_values(self):
        text = "cpu  0 0 0 0 0 0 0"
        sample = parse_proc_stat(text=text)
        assert sample.total == 0
        assert sample.idle == 0


class TestCpuPercent:
    def test_idle_system(self):
        prev = CpuSample(total=1000, idle=900)
        curr = CpuSample(total=2000, idle=1900)
        assert cpu_percent(prev=prev, curr=curr) == pytest.approx(0.0)

    def test_full_usage(self):
        prev = CpuSample(total=1000, idle=900)
        curr = CpuSample(total=2000, idle=900)
        assert cpu_percent(prev=prev, curr=curr) == pytest.approx(1.0)

    def test_half_usage(self):
        prev = CpuSample(total=1000, idle=500)
        curr = CpuSample(total=2000, idle=1000)
        assert cpu_percent(prev=prev, curr=curr) == pytest.approx(0.5)

    def test_zero_diff_returns_zero(self):
        s = CpuSample(total=1000, idle=500)
        assert cpu_percent(prev=s, curr=s) == 0.0


class TestParseProcMeminfo:
    def test_parses_meminfo(self):
        text = (
            "MemTotal:       16000000 kB\n"
            "MemFree:         2000000 kB\n"
            "MemAvailable:    8000000 kB\n"
        )
        usage = parse_proc_meminfo(text=text)
        assert usage == pytest.approx(0.5)

    def test_full_memory(self):
        text = "MemTotal:  1000 kB\nMemFree:  0 kB\nMemAvailable:  0 kB\n"
        assert parse_proc_meminfo(text=text) == pytest.approx(1.0)

    def test_empty_returns_zero(self):
        assert parse_proc_meminfo(text="") == 0.0


class TestTemperatureParsing:
    def test_parse_sysfs_temperature_from_millidegrees(self):
        assert parse_sysfs_temperature("55123\n") == pytest.approx(55.123)

    def test_parse_sysfs_temperature_from_celsius(self):
        assert parse_sysfs_temperature("54.5\n") == pytest.approx(54.5)

    def test_parse_sysfs_temperature_ignores_invalid_values(self):
        assert parse_sysfs_temperature("") is None
        assert parse_sysfs_temperature("0\n") is None
        assert parse_sysfs_temperature("oops\n") is None

    def test_parse_sensors_output_prefers_cpu_labels(self):
        text = (
            "acpitz-acpi-0\n"
            "Adapter: ACPI interface\n"
            "temp1:        +45.0°C\n"
            "\n"
            "coretemp-isa-0000\n"
            "Adapter: ISA adapter\n"
            "Package id 0: +58.0°C\n"
            "Core 0:       +54.0°C\n"
        )
        assert parse_sensors_output(text) == pytest.approx(58.0)

    def test_parse_vcgencmd_output(self):
        assert parse_vcgencmd_output("temp=48.2'C\n") == pytest.approx(48.2)

    def test_parse_acpi_output_uses_hottest_zone(self):
        text = "Thermal 0: ok, 51.0 degrees C\nThermal 1: ok, 57.5 degrees C\n"
        assert parse_acpi_output(text) == pytest.approx(57.5)


class TestTemperatureReader:
    def test_prefers_sysfs_over_command_backends(self, tmp_path):
        thermal_root = tmp_path / "thermal"
        hwmon_root = tmp_path / "hwmon"
        zone = thermal_root / "thermal_zone0"
        zone.mkdir(parents=True)
        hwmon_root.mkdir()
        (zone / "type").write_text("x86_pkg_temp\n", encoding="utf-8")
        (zone / "temp").write_text("53000\n", encoding="utf-8")

        called = []
        reader = TemperatureReader(
            thermal_root=thermal_root,
            hwmon_root=hwmon_root,
            which=lambda _cmd: called.append("which") or None,
            run_command=lambda _cmd, _timeout: called.append("run") or None,
        )

        assert reader.read() == pytest.approx(53.0)
        assert called == []

    def test_tries_all_available_commands_until_one_returns_temperature(self, tmp_path):
        thermal_root = tmp_path / "thermal"
        hwmon_root = tmp_path / "hwmon"
        thermal_root.mkdir()
        hwmon_root.mkdir()
        calls = []

        def fake_which(command: str) -> str | None:
            if command in {"sensors", "acpi"}:
                return f"/usr/bin/{command}"
            return None

        def fake_run(cmd: list[str], _timeout: float) -> str | None:
            calls.append(tuple(cmd))
            if cmd == ["sensors"]:
                return "Adapter: ISA adapter\n"
            if cmd == ["acpi", "-t"]:
                return "Thermal 0: ok, 49.5 degrees C\n"
            return None

        reader = TemperatureReader(
            thermal_root=thermal_root,
            hwmon_root=hwmon_root,
            which=fake_which,
            run_command=fake_run,
        )

        assert reader.read() == pytest.approx(49.5)
        assert calls == [("sensors",), ("acpi", "-t")]


class TestTooltipText:
    def test_without_temperature(self):
        text = tooltip_text(cpu=0.423, mem=0.671)
        assert text == "CPU: 42.3% | Mem: 67.1%"

    def test_with_temperature(self):
        text = tooltip_text(cpu=0.423, mem=0.671, temperature_c=58.4)
        assert text == "CPU: 42.3% | Mem: 67.1% | Temp: 58.4°C"


class TestCpuHueRgb:
    def test_zero_cpu_is_green(self):
        r, g, b = cpu_hue_rgb(cpu=0.0)
        assert g > r

    def test_full_cpu_is_red(self):
        r, g, b = cpu_hue_rgb(cpu=1.0)
        assert r > g

    def test_returns_valid_rgb(self):
        for cpu in [0.0, 0.25, 0.5, 0.75, 1.0]:
            r, g, b = cpu_hue_rgb(cpu=cpu)
            assert 0 <= r <= 1 and 0 <= g <= 1 and 0 <= b <= 1


class TestSystemMonitorRendering:
    @pytest.mark.parametrize("size", [32, 48, 64])
    def test_renders_valid_pixbuf(self, size):
        applet = SystemMonitorApplet(size)
        pixbuf = applet.create_icon(size)
        assert pixbuf is not None
        assert pixbuf.get_width() == size
        assert pixbuf.get_height() == size

    def test_no_menu_items(self):
        applet = SystemMonitorApplet(48)
        assert applet.get_menu_items() == []

    def test_tooltip_format(self):
        applet = SystemMonitorApplet(48)
        applet._cpu = 0.423
        applet._mem = 0.671
        applet._temperature_c = 58.4
        applet.refresh_tooltip()
        assert "CPU: 42.3%" in applet.item.name
        assert "Mem: 67.1%" in applet.item.name
        assert "Temp: 58.4°C" in applet.item.name

    def test_icon_has_visible_content(self):
        applet = SystemMonitorApplet(48)
        applet._cpu = 0.5
        applet._mem = 0.3
        pixbuf = applet.create_icon(48)
        pixels = pixbuf.get_pixels()
        non_transparent = sum(1 for i in range(0, len(pixels), 4) if pixels[i + 3] > 0)
        assert non_transparent > 100


class TestSystemMonitorLifecycle:
    def test_start_registers_timer(self, monkeypatch):
        applet = SystemMonitorApplet(48)
        monkeypatch.setattr(
            systemmonitor_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 123
        )
        applet.start(lambda: None)
        assert applet._timer_id == 123

    def test_stop_removes_timer(self, monkeypatch):
        applet = SystemMonitorApplet(48)
        applet._timer_id = 77
        removed = []
        monkeypatch.setattr(
            systemmonitor_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        applet.stop()
        assert removed == [77]
        assert applet._timer_id == 0

    def test_tick_refreshes_when_values_change(self, tmp_path, monkeypatch):
        proc_stat = tmp_path / "stat"
        proc_meminfo = tmp_path / "meminfo"
        proc_stat.write_text("cpu  200 0 100 100 0 0 0\n", encoding="utf-8")
        proc_meminfo.write_text(
            "MemTotal: 1000 kB\nMemAvailable: 500 kB\n",
            encoding="utf-8",
        )

        applet = SystemMonitorApplet(48)
        monkeypatch.setattr(systemmonitor_mod, "_PROC_STAT", proc_stat)
        monkeypatch.setattr(systemmonitor_mod, "_PROC_MEMINFO", proc_meminfo)
        monkeypatch.setattr(applet._temperature_reader, "read", lambda: 55.2)
        refresh = []
        monkeypatch.setattr(applet, "present", lambda: refresh.append(True))

        assert applet._tick() is True
        assert applet._prev_sample is not None
        assert applet._temperature_c == pytest.approx(55.2)
        assert refresh == [True]

    def test_tick_handles_proc_stat_read_error(self, tmp_path, monkeypatch):
        applet = SystemMonitorApplet(48)
        monkeypatch.setattr(systemmonitor_mod, "_PROC_STAT", tmp_path / "missing-stat")
        assert applet._tick() is True

    def test_tick_handles_meminfo_read_error(self, tmp_path, monkeypatch):
        proc_stat = tmp_path / "stat"
        proc_stat.write_text("cpu  200 0 100 100 0 0 0\n", encoding="utf-8")

        applet = SystemMonitorApplet(48)
        monkeypatch.setattr(systemmonitor_mod, "_PROC_STAT", proc_stat)
        monkeypatch.setattr(
            systemmonitor_mod,
            "_PROC_MEMINFO",
            tmp_path / "missing-meminfo",
        )
        monkeypatch.setattr(applet._temperature_reader, "read", lambda: None)
        assert applet._tick() is True

    def test_tick_skips_refresh_when_deltas_below_threshold(
        self, tmp_path, monkeypatch
    ):
        proc_stat = tmp_path / "stat"
        proc_meminfo = tmp_path / "meminfo"
        proc_stat.write_text("cpu  200 0 100 100 0 0 0\n", encoding="utf-8")
        proc_meminfo.write_text(
            "MemTotal: 1000 kB\nMemAvailable: 750 kB\n",
            encoding="utf-8",
        )

        applet = SystemMonitorApplet(48)
        applet._cpu = 0.5
        applet._mem = 0.25
        applet._temperature_c = 55.0
        applet._last_drawn_cpu = 0.5
        applet._last_drawn_mem = 0.25
        applet._prev_sample = CpuSample(total=100, idle=50)

        monkeypatch.setattr(systemmonitor_mod, "_PROC_STAT", proc_stat)
        monkeypatch.setattr(systemmonitor_mod, "_PROC_MEMINFO", proc_meminfo)
        monkeypatch.setattr(systemmonitor_mod, "cpu_percent", lambda prev, curr: 0.5)
        monkeypatch.setattr(systemmonitor_mod, "parse_proc_meminfo", lambda text: 0.25)
        monkeypatch.setattr(applet._temperature_reader, "read", lambda: 55.0)
        refresh = []
        monkeypatch.setattr(applet, "present", lambda: refresh.append(True))

        assert applet._tick() is True
        assert refresh == []

    def test_tick_refreshes_tooltip_when_only_temperature_changes(
        self,
        tmp_path,
        monkeypatch,
    ):
        proc_stat = tmp_path / "stat"
        proc_meminfo = tmp_path / "meminfo"
        proc_stat.write_text("cpu  200 0 100 100 0 0 0\n", encoding="utf-8")
        proc_meminfo.write_text(
            "MemTotal: 1000 kB\nMemAvailable: 750 kB\n",
            encoding="utf-8",
        )

        applet = SystemMonitorApplet(48)
        applet._cpu = 0.5
        applet._mem = 0.25
        applet._temperature_c = 55.0
        applet._last_drawn_cpu = 0.5
        applet._last_drawn_mem = 0.25
        applet._prev_sample = CpuSample(total=100, idle=50)

        monkeypatch.setattr(systemmonitor_mod, "_PROC_STAT", proc_stat)
        monkeypatch.setattr(systemmonitor_mod, "_PROC_MEMINFO", proc_meminfo)
        monkeypatch.setattr(systemmonitor_mod, "cpu_percent", lambda prev, curr: 0.5)
        monkeypatch.setattr(systemmonitor_mod, "parse_proc_meminfo", lambda text: 0.25)
        monkeypatch.setattr(applet._temperature_reader, "read", lambda: 56.2)
        notified = []
        applet._notify = lambda: notified.append(True)

        assert applet._tick() is True
        assert notified == [True]
        assert "Temp: 56.2°C" in applet.item.name
