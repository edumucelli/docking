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

"""Shared Brightness applet lifecycle and queued user requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.brightness import meta
from docking.applets.brightness.backend import create_backend
from docking.applets.brightness.render import create_icon
from docking.applets.brightness.state import STEP, BrightnessState
from docking.applets.menu import menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="brightness"), applet_id=meta.id)
POLL_INTERVAL_S = 5


class BrightnessApplet(Applet):
    """Control the primary display. Scroll changes 2%; click resets to 100%."""

    id = meta.id
    name = _("Brightness")
    icon_name = "display-brightness-symbolic"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._backend = create_backend()
        self._state = BrightnessState()
        self._brightness: float | None = None
        self._show_level = config.applet_prefs.get(meta.id, {}).get("show_level", False)
        self._timer_id = 0
        self._set_in_progress = False
        self._pending_brightness: tuple[str, float] | None = None
        self._poll_generation = 0
        self._stopped = False
        self._worker = BackgroundWorker(logger=log)
        super().__init__(icon_size=icon_size, config=config)
        self.present()
        # Discovery can involve slow drivers; never hold up dock creation.
        self._tick()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(
            size=size, brightness=self._brightness, show_level=self._show_level
        )

    def refresh_tooltip(self) -> None:
        if self._brightness is None:
            self.item.name = f"{_('Brightness')}: {_('Unavailable')}"
        else:
            self.item.name = _("Brightness: {pct}%").format(
                pct=round(self._brightness * 100)
            )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        self._stopped = True
        self._poll_generation += 1
        self._pending_brightness = None
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._backend.stop()
        super().stop()

    def on_clicked(self) -> None:
        if not self._stopped and self._state.available:
            self._queue_brightness(1.0)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        show = Gtk.CheckMenuItem(label=_("Show Level"))
        show.set_active(self._show_level)
        show.connect("toggled", self._on_toggle_level)
        return menu_sections(display=[show], gtk=Gtk)

    def _on_toggle_level(self, widget) -> None:
        self._show_level = widget.get_active()
        self.save_prefs(prefs={"show_level": self._show_level})
        self.present()

    def on_scroll(self, direction_up: bool) -> None:
        if self._stopped or not self._state.available or self._brightness is None:
            return
        value = self._brightness + (STEP if direction_up else -STEP)
        if levels := self._state.levels:
            value = (
                next((level for level in levels if level >= value - 1e-8), levels[-1])
                if direction_up
                else next(
                    (level for level in reversed(levels) if level <= value + 1e-8),
                    levels[0],
                )
            )
        self._queue_brightness(max(0.1, min(1.0, value)))

    def _queue_brightness(self, value: float) -> None:
        self._poll_generation += 1
        self._brightness = value
        self._pending_brightness = (self._state.target, value)
        self.present()
        self._start_pending_brightness()

    def _start_pending_brightness(self) -> None:
        if self._stopped or self._set_in_progress or self._pending_brightness is None:
            return
        target, value = self._pending_brightness
        self._pending_brightness = None
        self._set_in_progress = True
        self._worker.run(
            name="brightness-set",
            fn=lambda: (
                BrightnessState()
                if self._stopped
                else self._backend.set_brightness(value=value, target=target)
            ),
            on_result=self._on_brightness_set,
            on_error=lambda _error: self._on_brightness_set(BrightnessState()),
        )

    def _on_brightness_set(self, state: BrightnessState) -> bool:
        self._set_in_progress = False
        if self._stopped:
            return False
        if self._pending_brightness is None:
            self._apply_state(state)
        elif state.target != self._pending_brightness[0] and state.target:
            # The primary display changed while a request was running.
            self._pending_brightness = None
            self._apply_state(state)
        self._start_pending_brightness()
        return False

    def _apply_state(self, state: BrightnessState) -> None:
        self._state = state if state.available else BrightnessState()
        self._brightness = self._state.level
        self.present()

    def _tick(self) -> bool:
        if self._stopped:
            return False
        if self._set_in_progress:
            return True
        generation = self._poll_generation
        self._worker.run_guarded(
            key="brightness-poll",
            name="brightness-poll",
            fn=lambda: (
                BrightnessState() if self._stopped else self._backend.get_state()
            ),
            on_result=lambda state: self._on_poll_result(state, generation=generation),
            on_error=lambda _error: self._on_poll_result(
                BrightnessState(), generation=generation
            ),
        )
        return True

    def _on_poll_result(
        self, state: BrightnessState, *, generation: int | None = None
    ) -> bool:
        if (
            self._stopped
            or self._set_in_progress
            or (generation is not None and generation != self._poll_generation)
        ):
            return False
        if state != replace(self._state, level=self._brightness):
            self._apply_state(state)
        return False
