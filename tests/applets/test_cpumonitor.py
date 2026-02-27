"""Tests for the CPU monitor applet -- parsing and rendering."""

import pytest

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
        applet.create_icon(48)
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
        # Then -- gauge should have substantial visible content
        assert non_transparent > 100
