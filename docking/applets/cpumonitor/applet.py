"""GTK lifecycle glue for CPU monitor applet."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from docking.applets.base import Applet
from docking.applets.cpumonitor.render import render_icon
from docking.applets.cpumonitor.state import (
    CPU_THRESHOLD,
    MEM_THRESHOLD,
    CpuSample,
    cpu_percent,
    parse_proc_meminfo,
    parse_proc_stat,
    tooltip_text,
)
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="cpumonitor"), applet_id=str(AppletId.CPUMONITOR))
_PROC_STAT = Path("/proc/stat")
_PROC_MEMINFO = Path("/proc/meminfo")


class CpuMonitorApplet(Applet):
    """Circular gauge: CPU radial fill + memory arc at edge."""

    id = AppletId.CPUMONITOR
    name = _("CPU Monitor")
    icon_name = "utilities-system-monitor"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._prev_sample: CpuSample | None = None
        self._cpu: float = 0.0
        self._mem: float = 0.0
        self._last_drawn_cpu: float = -1.0
        self._last_drawn_mem: float = -1.0
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render circular gauge to pixbuf."""
        return render_icon(size=size, cpu=self._cpu, mem=self._mem)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(cpu=self._cpu, mem=self._mem)

    def start(self, notify: Callable[[], None]) -> None:
        """Start 1-second polling timer for /proc/stat and /proc/meminfo."""
        super().start(notify=notify)
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
            with _PROC_STAT.open() as f:
                curr = parse_proc_stat(text=f.read())
        except OSError as exc:
            _log.bind(action="read_proc_stat").debug(
                "Could not read /proc/stat: %s",
                exc,
            )
            return True

        if self._prev_sample is not None:
            raw = cpu_percent(prev=self._prev_sample, curr=curr)
            # Smooth with previous value
            self._cpu = (raw + self._cpu) / 2.0
        self._prev_sample = curr

        try:
            with _PROC_MEMINFO.open() as f:
                self._mem = parse_proc_meminfo(text=f.read())
        except OSError as exc:
            _log.bind(action="read_proc_meminfo").debug(
                "Could not read /proc/meminfo: %s",
                exc,
            )

        # Only redraw if change exceeds threshold
        cpu_delta = abs(self._cpu - self._last_drawn_cpu)
        mem_delta = abs(self._mem - self._last_drawn_mem)
        if cpu_delta >= CPU_THRESHOLD or mem_delta >= MEM_THRESHOLD:
            self._last_drawn_cpu = self._cpu
            self._last_drawn_mem = self._mem
            self.present()

        return True
