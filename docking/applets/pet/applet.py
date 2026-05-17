# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""GTK lifecycle glue for pet applet."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from docking.applets.base import Applet
from docking.applets.pet import meta
from docking.applets.pet.render import render_icon
from docking.applets.pet.state import (
    CpuSample,
    Mood,
    PetState,
    cpu_percent,
    parse_proc_stat,
    reset_to_happy,
    tick,
    tooltip_text,
)
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="pet"), applet_id=meta.id)
_PROC_STAT = Path("/proc/stat")


class PetApplet(Applet):
    """Tamagotchi-style blob that reacts to CPU load and idle time."""

    id = meta.id
    name = _("Pet")
    icon_name = "face-smile"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._state = PetState()
        self._prev_sample: CpuSample | None = None
        self._timer_id: int = 0
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, state=self._state)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(mood=self._state.mood, cpu=self._state.cpu)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(2, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Pet the creature - reset to happy."""
        self._state = reset_to_happy(state=self._state)
        self.item.is_urgent = False
        self.refresh_tooltip()
        self.present()

    def _tick(self) -> bool:
        """Poll /proc/stat, update mood, redraw if needed."""
        try:
            with _PROC_STAT.open() as fh:
                curr = parse_proc_stat(text=fh.read())
        except OSError as exc:
            log.bind(action="read_proc_stat").debug(
                "Could not read /proc/stat: %s",
                exc,
            )
            return True

        if self._prev_sample is None:
            self._prev_sample = curr
            return True

        raw_cpu = cpu_percent(prev=self._prev_sample, curr=curr)
        self._prev_sample = curr

        result = tick(state=self._state, raw_cpu=raw_cpu)
        self._state = result.state

        if result.mood_changed and self._state.mood == Mood.EXCITED:
            self.item.is_urgent = True
            self.item.last_urgent = GLib.get_monotonic_time()

        if result.should_refresh:
            self.refresh_tooltip()
            self.present()

        return True
