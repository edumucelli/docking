"""CPU monitor applet -- circular gauge showing CPU and memory usage.

Reads /proc/stat and /proc/meminfo every second. Renders a circular gauge
where CPU usage fills the center (green->red hue) and memory usage draws
a white arc around the edge. Matching Plank's CPUMonitor rendering.
"""

from __future__ import annotations

import colorsys
import math
from typing import TYPE_CHECKING, Callable, NamedTuple

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId

if TYPE_CHECKING:
    from docking.core.config import Config

TWO_PI = 2 * math.pi
RADIUS_PERCENT = 0.9

# Redraw thresholds (avoid excessive redraws)
CPU_THRESHOLD = 0.03
MEM_THRESHOLD = 0.01


# -- Pure data functions (testable without GTK) ------------------------------


class CpuSample(NamedTuple):
    """Raw CPU jiffies from /proc/stat."""

    total: int
    idle: int


def parse_proc_stat(text: str) -> CpuSample:
    """Parse first line of /proc/stat into total and idle jiffies."""
    # cpu  user nice system idle iowait irq softirq [steal guest guest_nice]
    parts = text.split("\n")[0].split()
    values = [int(v) for v in parts[1:8]]
    user, nice, system, idle, iowait, irq, softirq = values
    total = user + nice + system + idle + iowait + irq + softirq
    idle_total = idle + iowait
    return CpuSample(total, idle_total)


def cpu_percent(prev: CpuSample, curr: CpuSample) -> float:
    """Compute CPU usage fraction (0.0-1.0) from two samples."""
    total_diff = curr.total - prev.total
    if total_diff == 0:
        return 0.0
    idle_diff = curr.idle - prev.idle
    return 1.0 - idle_diff / total_diff


def parse_proc_meminfo(text: str) -> float:
    """Parse /proc/meminfo, return memory usage fraction (0.0-1.0)."""
    mem_total = 0
    mem_available = 0
    for line in text.split("\n"):
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            mem_available = int(line.split()[1])
    if mem_total == 0:
        return 0.0
    return 1.0 - mem_available / mem_total


def cpu_hue_rgb(cpu: float) -> tuple[float, float, float]:
    """Map CPU usage to color: green (0%) -> red (100%)."""
    hue = (1.0 - cpu) * 120.0 / 360.0
    return colorsys.hsv_to_rgb(hue, 1.0, 1.0)


# -- Applet -----------------------------------------------------------------


class CpuMonitorApplet(Applet):
    """Circular gauge: CPU radial fill + memory arc at edge.

    Updates every 1 second. CPU value is smoothed with previous sample
    to avoid jitter. Redraws only when change exceeds threshold (3% CPU
    or 1% memory) to avoid excessive rendering.
    """

    id = AppletId.CPUMONITOR
    name = "CPU Monitor"
    icon_name = "utilities-system-monitor"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._prev_sample: CpuSample | None = None
        self._cpu: float = 0.0
        self._mem: float = 0.0
        self._last_drawn_cpu: float = -1.0
        self._last_drawn_mem: float = -1.0
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render circular gauge to pixbuf; updates tooltip with CPU/Mem %."""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_gauge(cr=cr, size=size, cpu=self._cpu, mem=self._mem)

        if hasattr(self, "item"):
            self.item.name = (
                f"CPU: {self._cpu * 100:.1f}% | Mem: {self._mem * 100:.1f}%"
            )

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def start(self, notify: Callable[[], None]) -> None:
        """Start 1-second polling timer for /proc/stat and /proc/meminfo."""
        super().start(notify)
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        """Stop the polling timer."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _tick(self) -> bool:
        """Read CPU + memory, smooth, and redraw if change exceeds threshold."""
        try:
            with open("/proc/stat") as f:
                curr = parse_proc_stat(text=f.read())
        except OSError:
            return True

        if self._prev_sample is not None:
            raw = cpu_percent(prev=self._prev_sample, curr=curr)
            # Smooth with previous value
            self._cpu = (raw + self._cpu) / 2.0
        self._prev_sample = curr

        try:
            with open("/proc/meminfo") as f:
                self._mem = parse_proc_meminfo(text=f.read())
        except OSError:
            pass

        # Only redraw if change exceeds threshold
        cpu_delta = abs(self._cpu - self._last_drawn_cpu)
        mem_delta = abs(self._mem - self._last_drawn_mem)
        if cpu_delta >= CPU_THRESHOLD or mem_delta >= MEM_THRESHOLD:
            self._last_drawn_cpu = self._cpu
            self._last_drawn_mem = self._mem
            self.refresh_icon()

        return True


def _render_gauge(cr: cairo.Context, size: int, cpu: float, mem: float) -> None:
    """Draw circular CPU gauge with memory arc (matching Plank).

    Layers (bottom to top):
      1. Black underlay circle
      2. Background color gradient (full circle, fades to 0.15 at edge)
      3. CPU indicator gradient (scales with usage, brighter)
      4. White highlight arc (gloss)
      5. Two border rings (white + gray)
      6. Memory arc (white, from 9 o'clock counter-clockwise)
    """
    center = size / 2.0
    radius = center * RADIUS_PERCENT

    r, g, b = cpu_hue_rgb(cpu=cpu)
    base_alpha = 0.5
    cpu_clamped = max(0.001, min(cpu * 1.3, 1.0))

    # 1. Black underlay
    cr.arc(center, center, radius, 0, TWO_PI)
    cr.set_source_rgba(0, 0, 0, 0.5)
    cr.fill_preserve()

    # 2. Background color gradient (shade spreading to borders)
    bg = cairo.RadialGradient(center, center, 0, center, center, radius)
    bg.add_color_stop_rgba(0, r, g, b, base_alpha)
    bg.add_color_stop_rgba(0.2, r, g, b, base_alpha)
    bg.add_color_stop_rgba(1.0, r, g, b, 0.15)
    cr.set_source(bg)
    cr.fill_preserve()

    # 3. CPU indicator gradient (brighter core, scales with usage)
    ind = cairo.RadialGradient(center, center, 0, center, center, radius * cpu_clamped)
    ind.add_color_stop_rgba(0, r, g, b, 1.0)
    ind.add_color_stop_rgba(0.2, r, g, b, 1.0)
    edge_alpha = max(0.0, cpu * 1.3 - 1.0)
    ind.add_color_stop_rgba(1.0, r, g, b, edge_alpha)
    cr.set_source(ind)
    cr.fill()

    # 4. White highlight (gloss in upper portion)
    cr.arc(center, center * 0.8, center * 0.6, 0, TWO_PI)
    highlight = cairo.LinearGradient(0, 0, 0, center)
    highlight.add_color_stop_rgba(0, 1, 1, 1, 0.35)
    highlight.add_color_stop_rgba(1, 1, 1, 1, 0)
    cr.set_source(highlight)
    cr.fill()

    # 5. Double border rings
    cr.set_line_width(1.0)
    cr.arc(center, center, radius, 0, TWO_PI)
    cr.set_source_rgba(1, 1, 1, 0.75)
    cr.stroke()

    cr.set_line_width(1.0)
    cr.arc(center, center, radius - 1, 0, TWO_PI)
    cr.set_source_rgba(0.8, 0.8, 0.8, 0.75)
    cr.stroke()

    # 6. Memory arc (white, from 9 o'clock counter-clockwise)
    if mem > 0.001:
        cr.set_line_width(size / 32.0)
        cr.arc_negative(
            center,
            center,
            radius - 1,
            math.pi,
            math.pi - math.pi * 2.0 * mem,
        )
        cr.set_source_rgba(1, 1, 1, 0.85)
        cr.stroke()
