"""Tests for the CPU monitor applet -- parsing and rendering."""

import io

import pytest

import docking.applets.cpumonitor.applet as cpumonitor_mod
from docking.applets.cpumonitor import (
    CpuMonitorApplet,
    CpuSample,
    cpu_hue_rgb,
    cpu_percent,
    parse_proc_meminfo,
    parse_proc_stat,
)


class TestParseProcStat:
    def test_parses_first_line(self):
        text = "cpu  1000 200 300 5000 100 50 25\ncpu0 500 100 150 2500 50 25 12"
        sample = parse_proc_stat(text=text)
        # total = 1000+200+300+5000+100+50+25 = 6675
        assert sample.total == 6675
        # idle = 5000+100 = 5100
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
        # idle_diff=1000, total_diff=1000 -> 0% usage
        assert cpu_percent(prev=prev, curr=curr) == pytest.approx(0.0)

    def test_full_usage(self):
        prev = CpuSample(total=1000, idle=900)
        curr = CpuSample(total=2000, idle=900)
        # idle_diff=0, total_diff=1000 -> 100% usage
        assert cpu_percent(prev=prev, curr=curr) == pytest.approx(1.0)

    def test_half_usage(self):
        prev = CpuSample(total=1000, idle=500)
        curr = CpuSample(total=2000, idle=1000)
        # idle_diff=500, total_diff=1000 -> 50%
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
        # 1 - 8000000/16000000 = 0.5
        assert usage == pytest.approx(0.5)

    def test_full_memory(self):
        text = "MemTotal:  1000 kB\nMemFree:  0 kB\nMemAvailable:  0 kB\n"
        assert parse_proc_meminfo(text=text) == pytest.approx(1.0)

    def test_empty_returns_zero(self):
        assert parse_proc_meminfo(text="") == 0.0


class TestCpuHueRgb:
    def test_zero_cpu_is_green(self):
        r, g, b = cpu_hue_rgb(cpu=0.0)
        assert g > r  # green dominant

    def test_full_cpu_is_red(self):
        r, g, b = cpu_hue_rgb(cpu=1.0)
        assert r > g  # red dominant

    def test_returns_valid_rgb(self):
        for cpu in [0.0, 0.25, 0.5, 0.75, 1.0]:
            r, g, b = cpu_hue_rgb(cpu=cpu)
            assert 0 <= r <= 1 and 0 <= g <= 1 and 0 <= b <= 1


class TestCpuMonitorRendering:
    @pytest.mark.parametrize("size", [32, 48, 64])
    def test_renders_valid_pixbuf(self, size):
        applet = CpuMonitorApplet(size)
        pixbuf = applet.create_icon(size)
        assert pixbuf is not None
        assert pixbuf.get_width() == size
        assert pixbuf.get_height() == size

    def test_no_menu_items(self):
        applet = CpuMonitorApplet(48)
        assert applet.get_menu_items() == []

    def test_tooltip_format(self):
        applet = CpuMonitorApplet(48)
        applet._cpu = 0.423
        applet._mem = 0.671
        applet.refresh_tooltip()
        assert "CPU: 42.3%" in applet.item.name
        assert "Mem: 67.1%" in applet.item.name

    def test_icon_has_visible_content(self):
        # Given 50% CPU
        applet = CpuMonitorApplet(48)
        applet._cpu = 0.5
        applet._mem = 0.3
        pixbuf = applet.create_icon(48)
        pixels = pixbuf.get_pixels()
        non_transparent = sum(1 for i in range(0, len(pixels), 4) if pixels[i + 3] > 0)
        # Then
        assert non_transparent > 100


class TestCpuMonitorLifecycle:
    def test_start_registers_timer(self, monkeypatch):
        applet = CpuMonitorApplet(48)
        monkeypatch.setattr(
            cpumonitor_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 123
        )
        applet.start(lambda: None)
        assert applet._timer_id == 123

    def test_stop_removes_timer(self, monkeypatch):
        applet = CpuMonitorApplet(48)
        applet._timer_id = 77
        removed = []
        monkeypatch.setattr(
            cpumonitor_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        applet.stop()
        assert removed == [77]
        assert applet._timer_id == 0

    def test_tick_refreshes_when_values_change(self, monkeypatch):
        applet = CpuMonitorApplet(48)

        def fake_open(path, *args, **kwargs):
            if path == "/proc/stat":
                return io.StringIO("cpu  200 0 100 100 0 0 0\n")
            if path == "/proc/meminfo":
                return io.StringIO("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
            raise AssertionError(path)

        monkeypatch.setattr("builtins.open", fake_open)
        refresh = []
        monkeypatch.setattr(
            applet, "refresh_presentation", lambda: refresh.append(True)
        )

        assert applet._tick() is True
        assert applet._prev_sample is not None
        assert refresh == [True]

    def test_tick_handles_proc_stat_read_error(self, monkeypatch):
        applet = CpuMonitorApplet(48)

        def boom(*_args, **_kwargs):
            raise OSError("boom")

        monkeypatch.setattr("builtins.open", boom)
        assert applet._tick() is True

    def test_tick_handles_meminfo_read_error(self, monkeypatch):
        applet = CpuMonitorApplet(48)

        def fake_open(path, *args, **kwargs):
            if path == "/proc/stat":
                return io.StringIO("cpu  200 0 100 100 0 0 0\n")
            if path == "/proc/meminfo":
                raise OSError("boom")
            raise AssertionError(path)

        monkeypatch.setattr("builtins.open", fake_open)
        assert applet._tick() is True

    def test_tick_skips_refresh_when_deltas_below_threshold(self, monkeypatch):
        applet = CpuMonitorApplet(48)
        applet._cpu = 0.5
        applet._mem = 0.25
        applet._last_drawn_cpu = 0.5
        applet._last_drawn_mem = 0.25
        applet._prev_sample = CpuSample(total=100, idle=50)

        monkeypatch.setattr(cpumonitor_mod, "cpu_percent", lambda prev, curr: 0.5)
        monkeypatch.setattr(cpumonitor_mod, "parse_proc_meminfo", lambda text: 0.25)

        def fake_open(path, *args, **kwargs):
            if path == "/proc/stat":
                return io.StringIO("cpu  200 0 100 100 0 0 0\n")
            if path == "/proc/meminfo":
                return io.StringIO("MemTotal: 1000 kB\nMemAvailable: 750 kB\n")
            raise AssertionError(path)

        monkeypatch.setattr("builtins.open", fake_open)
        refresh = []
        monkeypatch.setattr(
            applet, "refresh_presentation", lambda: refresh.append(True)
        )

        assert applet._tick() is True
        assert refresh == []
