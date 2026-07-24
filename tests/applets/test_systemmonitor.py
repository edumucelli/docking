"""Tests for the System Monitor applet -- parsing, temperature, and rendering."""

import subprocess

import pytest

import docking.applets.systemmonitor.applet as systemmonitor_mod
import docking.applets.systemmonitor.gpu as gpu_mod
import docking.applets.systemmonitor.temperature as temperature_mod
from docking.applets.systemmonitor.applet import SystemMonitorApplet
from docking.applets.systemmonitor.gpu import (
    GpuReader,
    GpuStats,
    gpu_summary,
    parse_nvidia_smi_output,
)
from docking.applets.systemmonitor.state import (
    CpuSample,
    SystemMonitorPrefs,
    TemperatureUnit,
    cpu_hue_rgb,
    cpu_percent,
    parse_proc_meminfo,
    parse_proc_stat,
    prefs_from_mapping,
    prefs_payload,
    tooltip_text,
)
from docking.applets.systemmonitor.temperature import (
    CommandBackend,
    TemperatureReader,
    discover_available_commands,
    discover_sysfs_temperature_path,
    parse_acpi_output,
    parse_sensors_output,
    parse_sysfs_temperature,
    parse_vcgencmd_output,
    read_command_temperature,
    read_temperature_file,
)
from docking.core.config import Config


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

    def test_parse_sensors_output_handles_no_matches_and_tiebreak(self):
        assert parse_sensors_output("Adapter: ISA adapter\n") is None
        text = "coretemp-isa-0000\nCore 0: +51.0°C\nCore 1: +59.0°C\n"
        assert parse_sensors_output(text) == pytest.approx(59.0)

    def test_parse_command_outputs_ignore_bad_shapes(self):
        assert parse_vcgencmd_output("temp=bad") is None
        assert parse_acpi_output("Thermal 0: ok") is None


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

    def test_invalid_cached_sysfs_path_is_reprobed(self, tmp_path):
        thermal_root = tmp_path / "thermal"
        hwmon_root = tmp_path / "hwmon"
        zone = thermal_root / "thermal_zone0"
        zone.mkdir(parents=True)
        hwmon_root.mkdir()
        (zone / "type").write_text("cpu\n", encoding="utf-8")
        temp = zone / "temp"
        temp.write_text("42000\n", encoding="utf-8")

        reader = TemperatureReader(
            thermal_root=thermal_root,
            hwmon_root=hwmon_root,
            which=lambda _cmd: None,
        )
        assert reader.read() == pytest.approx(42.0)

        temp.write_text("bad\n", encoding="utf-8")
        assert reader.read() is None
        assert reader._sysfs_path is None
        assert reader._sysfs_probed is False

    def test_command_probe_is_cached_when_no_commands(self, tmp_path):
        calls: list[str] = []
        reader = TemperatureReader(
            thermal_root=tmp_path / "thermal",
            hwmon_root=tmp_path / "hwmon",
            which=lambda command: calls.append(command) or None,
        )

        assert reader.read() is None
        assert reader.read() is None
        assert calls == ["sensors", "vcgencmd", "acpi"]


class TestTemperatureDiscovery:
    def test_discovers_best_hwmon_temperature(self, tmp_path):
        thermal_root = tmp_path / "thermal"
        hwmon_root = tmp_path / "hwmon"
        thermal_root.mkdir()
        hwmon = hwmon_root / "hwmon0"
        hwmon.mkdir(parents=True)
        (hwmon / "name").write_text("coretemp\n", encoding="utf-8")
        (hwmon / "temp1_input").write_text("48000\n", encoding="utf-8")
        (hwmon / "temp2_label").write_text("Package id 0\n", encoding="utf-8")
        (hwmon / "temp2_input").write_text("62000\n", encoding="utf-8")

        path = discover_sysfs_temperature_path(
            thermal_root=thermal_root,
            hwmon_root=hwmon_root,
        )

        assert path == hwmon / "temp2_input"

    def test_discovers_nothing_without_cpu_hints(self, tmp_path):
        thermal_root = tmp_path / "thermal"
        hwmon_root = tmp_path / "hwmon"
        zone = thermal_root / "thermal_zone0"
        zone.mkdir(parents=True)
        hwmon = hwmon_root / "hwmon0"
        hwmon.mkdir(parents=True)
        (zone / "type").write_text("battery\n", encoding="utf-8")
        (zone / "temp").write_text("45000\n", encoding="utf-8")
        (hwmon / "name").write_text("random\n", encoding="utf-8")
        (hwmon / "temp1_input").write_text("47000\n", encoding="utf-8")

        assert (
            discover_sysfs_temperature_path(
                thermal_root=thermal_root,
                hwmon_root=hwmon_root,
            )
            is None
        )

    def test_available_commands_keep_preference_order(self):
        commands = discover_available_commands(
            which=lambda command: f"/bin/{command}" if command != "vcgencmd" else None
        )

        assert [backend.command for backend in commands] == ["sensors", "acpi"]

    def test_read_command_temperature_dispatches_and_ignores_unknown(self):
        assert read_command_temperature(
            backend=CommandBackend("vcgencmd", ("vcgencmd", "measure_temp")),
            run_command=lambda _cmd, _timeout: "temp=47.0'C",
        ) == pytest.approx(47.0)
        assert (
            read_command_temperature(
                backend=CommandBackend("unknown", ("unknown",)),
                run_command=lambda _cmd, _timeout: "47",
            )
            is None
        )
        assert (
            read_command_temperature(
                backend=CommandBackend("sensors", ("sensors",)),
                run_command=lambda _cmd, _timeout: None,
            )
            is None
        )

    def test_read_temperature_file_handles_decode_errors(self, tmp_path):
        path = tmp_path / "temp"
        path.write_bytes(b"\xff")
        assert read_temperature_file(path) is None
        assert read_temperature_file(tmp_path / "missing") is None

    def test_run_command_success_error_and_exception(self, monkeypatch):
        monkeypatch.setattr(
            temperature_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=["sensors"],
                returncode=0,
                stdout="ok",
            ),
        )
        assert temperature_mod._run_command(["sensors"], 0.1) == "ok"

        monkeypatch.setattr(
            temperature_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=["sensors"],
                returncode=1,
                stdout="bad",
            ),
        )
        assert temperature_mod._run_command(["sensors"], 0.1) is None

        def fail(*_args, **_kwargs):
            raise OSError("nope")

        monkeypatch.setattr(temperature_mod.subprocess, "run", fail)
        assert temperature_mod._run_command(["sensors"], 0.1) is None

    def test_score_and_pick_best_helpers(self, tmp_path):
        assert temperature_mod._score_text("CPU package", (("cpu", 10),)) == 10
        low = temperature_mod._pick_best(
            None,
            score=10,
            temperature=40.0,
            path=tmp_path / "low",
        )
        high_score = temperature_mod._pick_best(
            low,
            score=20,
            temperature=35.0,
            path=tmp_path / "score",
        )
        high_temp = temperature_mod._pick_best(
            low,
            score=10,
            temperature=45.0,
            path=tmp_path / "temp",
        )
        assert high_score[2] == tmp_path / "score"
        assert high_temp[2] == tmp_path / "temp"
        assert (
            temperature_mod._pick_best(
                high_score,
                score=10,
                temperature=99.0,
                path=tmp_path / "ignored",
            )
            == high_score
        )


class TestTooltipText:
    def test_without_temperature(self):
        text = tooltip_text(cpu=0.423, mem=0.671)
        assert text == "System Monitor\nCPU: 42.3% | Mem: 67.1%"

    def test_with_temperature(self):
        text = tooltip_text(cpu=0.423, mem=0.671, temperature_c=58.4)
        assert text == "System Monitor\nCPU: 42.3% | Mem: 67.1% | Temp: 58.4°C"

    def test_with_temperature_in_fahrenheit(self):
        text = tooltip_text(
            cpu=0.423,
            mem=0.671,
            temperature_c=58.4,
            temperature_unit=TemperatureUnit.FAHRENHEIT,
        )

        assert text == "System Monitor\nCPU: 42.3% | Mem: 67.1% | Temp: 137.1°F"

    def test_with_disks(self):
        disks = [("/", 0.45), ("/home", 0.72)]
        text = tooltip_text(cpu=0.1, mem=0.5, disks=disks)
        assert "Disk:" in text
        assert "/: 45%" in text
        assert "/home: 72%" in text

    def test_with_gpu(self):
        gpu = GpuStats(
            name="GeForce 840M",
            utilization=0.42,
            memory_used_mib=256,
            memory_total_mib=2048,
        )
        text = tooltip_text(cpu=0.1, mem=0.5, gpu=gpu)

        assert "GPU: 42%" in text
        assert "Mem: 256/2048 MiB" in text

    def test_with_temperature_and_disks(self):
        disks = [("/", 0.8)]
        text = tooltip_text(cpu=0.1, mem=0.5, temperature_c=55.0, disks=disks)
        assert "Temp: 55.0" in text
        assert "Disk:" in text
        assert "/: 80%" in text


class TestSystemMonitorPrefs:
    def test_defaults(self):
        assert prefs_from_mapping(None) == SystemMonitorPrefs()

    def test_loads_temperature_unit(self):
        prefs = prefs_from_mapping(
            {"show_disk": False, "temperature_unit": "fahrenheit"}
        )

        assert prefs.show_disk is False
        assert prefs.temperature_unit == TemperatureUnit.FAHRENHEIT

    def test_payload(self):
        assert prefs_payload(
            show_disk=False,
            temperature_unit=TemperatureUnit.FAHRENHEIT,
        ) == {"show_disk": False, "temperature_unit": "fahrenheit"}


class TestGpuReader:
    def test_parse_nvidia_smi_output(self):
        stats = parse_nvidia_smi_output("GeForce 840M, 37, 212, 2048\n")

        assert stats == GpuStats(
            name="GeForce 840M",
            utilization=pytest.approx(0.37),
            memory_used_mib=212,
            memory_total_mib=2048,
        )

    def test_parse_nvidia_smi_output_ignores_bad_lines(self):
        assert parse_nvidia_smi_output("") is None
        assert parse_nvidia_smi_output("bad\n") is None

    def test_gpu_summary_formats_minimal_info(self):
        stats = GpuStats(
            name="GPU",
            utilization=0.123,
            memory_used_mib=100,
            memory_total_mib=1000,
        )

        assert gpu_summary(stats) == "GPU: 12% | Mem: 100/1000 MiB"

    def test_reader_returns_none_without_nvidia_smi(self):
        reader = GpuReader(which=lambda _command: None)

        assert reader.read() is None

    def test_reader_returns_none_when_driver_unavailable(self):
        def fake_run(*_args, **_kwargs):
            return gpu_mod.subprocess.CompletedProcess(
                args=["nvidia-smi"],
                returncode=9,
                stdout="",
                stderr="driver unavailable",
            )

        reader = GpuReader(which=lambda _command: "/usr/bin/nvidia-smi", run=fake_run)

        assert reader.read() is None

    def test_reader_supports_amdgpu_sysfs(self, tmp_path):
        device_dir = tmp_path / "card0" / "device"
        device_dir.mkdir(parents=True)
        (device_dir / "vendor").write_text("0x1002\n", encoding="utf-8")
        (device_dir / "gpu_busy_percent").write_text("55\n", encoding="utf-8")
        (device_dir / "mem_info_vram_used").write_text(
            str(512 * 1024 * 1024),
            encoding="utf-8",
        )
        (device_dir / "mem_info_vram_total").write_text(
            str(4096 * 1024 * 1024),
            encoding="utf-8",
        )

        reader = GpuReader(which=lambda _command: None, drm_dir=tmp_path)

        assert reader.read() == GpuStats(
            name="Radeon GPU",
            utilization=pytest.approx(0.55),
            memory_used_mib=512,
            memory_total_mib=4096,
        )

    def test_reader_ignores_amdgpu_without_live_stats(self, tmp_path):
        device_dir = tmp_path / "card0" / "device"
        device_dir.mkdir(parents=True)
        (device_dir / "vendor").write_text("0x1002\n", encoding="utf-8")

        reader = GpuReader(which=lambda _command: None, drm_dir=tmp_path)

        assert reader.read() is None


class TestCpuHueRgb:
    def test_zero_cpu_is_green(self):
        r, g, _b = cpu_hue_rgb(cpu=0.0)
        assert g > r

    def test_full_cpu_is_red(self):
        r, g, _b = cpu_hue_rgb(cpu=1.0)
        assert r > g

    def test_returns_valid_rgb(self):
        for cpu in [0.0, 0.25, 0.5, 0.75, 1.0]:
            r, g, b = cpu_hue_rgb(cpu=cpu)
            assert 0 <= r <= 1 and 0 <= g <= 1 and 0 <= b <= 1


class TestSystemMonitorRendering:
    @pytest.mark.parametrize("size", [32, 48, 64])
    def test_renders_valid_pixbuf(self, size):
        applet = SystemMonitorApplet(size, config=Config())
        pixbuf = applet.create_icon(size)
        assert pixbuf is not None
        assert pixbuf.get_width() == size
        assert pixbuf.get_height() == size

    def test_menu_has_show_disk(self):
        applet = SystemMonitorApplet(48, config=Config())
        items = applet.get_menu_items()
        assert items[0].get_label() == "Show Disk Usage"
        assert items[1].get_label() == "Temperature Unit"

    def test_tooltip_format(self):
        applet = SystemMonitorApplet(48, config=Config())
        applet._cpu = 0.423
        applet._mem = 0.671
        applet._temperature_c = 58.4
        applet._gpu = GpuStats(
            name="GeForce 840M",
            utilization=0.33,
            memory_used_mib=128,
            memory_total_mib=2048,
        )
        applet.refresh_tooltip()
        assert "CPU: 42.3%" in applet.item.name
        assert "Mem: 67.1%" in applet.item.name
        assert "Temp: 58.4°C" in applet.item.name
        assert "GPU: 33%" in applet.item.name

    def test_tooltip_uses_selected_temperature_unit(self):
        applet = SystemMonitorApplet(48, config=Config())
        applet._cpu = 0.423
        applet._mem = 0.671
        applet._temperature_c = 58.4
        applet._temperature_unit = TemperatureUnit.FAHRENHEIT
        applet.refresh_tooltip()

        assert "Temp: 137.1°F" in applet.item.name

    def test_saves_temperature_unit_pref(self):
        from docking.core.config import Config

        config = Config(applet_prefs={})
        applet = SystemMonitorApplet(48, config=config)
        item = systemmonitor_mod.Gtk.RadioMenuItem(label="Fahrenheit")
        item.set_active(True)

        applet._on_temperature_unit_selected(
            widget=item,
            temperature_unit=TemperatureUnit.FAHRENHEIT,
        )

        assert config.applet_prefs["systemmonitor"] == {
            "show_disk": True,
            "temperature_unit": "fahrenheit",
        }

    def test_icon_has_visible_content(self):
        applet = SystemMonitorApplet(48, config=Config())
        applet._cpu = 0.5
        applet._mem = 0.3
        pixbuf = applet.create_icon(48)
        pixels = pixbuf.get_pixels()
        non_transparent = sum(1 for i in range(0, len(pixels), 4) if pixels[i + 3] > 0)
        assert non_transparent > 100


class TestSystemMonitorLifecycle:
    def test_start_registers_timer(self, monkeypatch):
        applet = SystemMonitorApplet(48, config=Config())
        monkeypatch.setattr(
            systemmonitor_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 123
        )
        applet.start(lambda: None)
        assert applet._timer_id == 123

    def test_stop_removes_timer(self, monkeypatch):
        applet = SystemMonitorApplet(48, config=Config())
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

        applet = SystemMonitorApplet(48, config=Config())
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
        applet = SystemMonitorApplet(48, config=Config())
        monkeypatch.setattr(systemmonitor_mod, "_PROC_STAT", tmp_path / "missing-stat")
        assert applet._tick() is True

    def test_tick_handles_meminfo_read_error(self, tmp_path, monkeypatch):
        proc_stat = tmp_path / "stat"
        proc_stat.write_text("cpu  200 0 100 100 0 0 0\n", encoding="utf-8")

        applet = SystemMonitorApplet(48, config=Config())
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

        applet = SystemMonitorApplet(48, config=Config())
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

        applet = SystemMonitorApplet(48, config=Config())
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
        monkeypatch.setattr(applet._gpu_reader, "read", lambda: None)
        notified = []
        applet._notify = lambda: notified.append(True)

        assert applet._tick() is True
        assert notified == [True]
        assert "Temp: 56.2°C" in applet.item.name

    def test_tick_refreshes_tooltip_when_only_gpu_changes(
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

        applet = SystemMonitorApplet(48, config=Config())
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
        monkeypatch.setattr(
            applet._gpu_reader,
            "read",
            lambda: GpuStats(name="GPU", utilization=0.4),
        )
        notified = []
        applet._notify = lambda: notified.append(True)

        assert applet._tick() is True
        assert notified == [True]
        assert "GPU: 40%" in applet.item.name
