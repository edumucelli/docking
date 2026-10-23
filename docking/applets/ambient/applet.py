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

"""GTK lifecycle glue for Ambient applet."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gst, Gtk

from docking.applets.ambient import meta
from docking.applets.ambient.render import render_icon
from docking.applets.ambient.state import (
    ALL_SOUNDS,
    NOISE_WAVES,
    AmbientState,
    adjust_volume,
    prefs_from_state,
    set_playing,
    set_sound,
    sound_label,
    state_from_prefs,
    tooltip_text,
)
from docking.applets.base import Applet
from docking.applets.menu import menu_sections
from docking.core.math import clamp
from docking.i18n import _
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

log = get_logger(name="ambient")

Gst.init(None)

SOUNDS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "sounds"


def _build_file_pipeline(path: Path, volume: float) -> Gst.Element | None:
    """Build a playbin pipeline for an OGG file."""
    playbin = Gst.ElementFactory.make("playbin", "ambient-playbin")
    if not playbin:
        return None
    playbin.set_property("uri", path.as_uri())
    playbin.set_property("volume", volume)
    return playbin


def _build_noise_pipeline(wave: int, volume: float) -> Gst.Element | None:
    """Build a pipeline for procedural noise."""
    return Gst.parse_launch(
        f"audiotestsrc wave={wave} ! volume name=vol volume={volume} ! autoaudiosink"
    )


class AmbientApplet(Applet):
    """Looping ambient soundscape player."""

    id = meta.id
    name = _("Ambient")
    icon_name = "audio-speakers"

    def __init__(self, icon_size: int, config: Config) -> None:
        prefs = config.applet_prefs.get("ambient", {})
        self._state = state_from_prefs(prefs=prefs)
        self._pipeline: Gst.Element | None = None
        self._bus_watching = False

        super().__init__(icon_size, config)
        self.present()

    # Compatibility accessors used by existing tests
    @property
    def _current(self) -> str:
        return self._state.current

    @_current.setter
    def _current(self, value: str) -> None:
        self._state = set_sound(state=self._state, name=value)

    @property
    def _volume(self) -> float:
        return self._state.volume

    @_volume.setter
    def _volume(self, value: float) -> None:
        self._state = AmbientState(
            current=self._state.current,
            volume=clamp(value, 0.0, 1.0),
            playing=self._state.playing,
        )

    @property
    def _playing(self) -> bool:
        return self._state.playing

    @_playing.setter
    def _playing(self, value: bool) -> None:
        self._state = set_playing(state=self._state, playing=value)

    def _update_tooltip(self) -> None:
        self.item.name = tooltip_text(state=self._state)

    def refresh_tooltip(self) -> None:
        self._update_tooltip()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)

    def stop(self) -> None:
        self._stop_playback()
        super().stop()

    def on_clicked(self) -> None:
        if self._state.playing:
            self._stop_playback()
        else:
            self._start_playback()
        self._update_tooltip()
        self.present()

    def on_scroll(self, direction_up: bool) -> None:
        self._state = adjust_volume(state=self._state, direction_up=direction_up)
        self._apply_volume()
        self._save()
        self._update_tooltip()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        display: list[Gtk.MenuItem] = []
        for sound in ALL_SOUNDS:
            menu_item = Gtk.CheckMenuItem(label=sound.label)
            menu_item.set_active(
                self._state.current == sound.name and self._state.playing
            )
            menu_item.connect(
                "toggled",
                lambda _w, s=sound.name: self._select_sound(name=s),
            )
            display.append(menu_item)
        return menu_sections(display=display, gtk=Gtk)

    def _select_sound(self, name: str) -> None:
        was_playing = self._state.playing
        if self._state.playing:
            self._stop_playback()
        self._state = set_sound(state=self._state, name=name)
        self._save()
        if was_playing:
            self._start_playback()
        self._update_tooltip()
        self.present()

    def _start_playback(self) -> None:
        self._stop_playback()
        sound = next(
            (entry for entry in ALL_SOUNDS if entry.name == self._state.current), None
        )
        if not sound:
            return

        if sound.kind == "file":
            path = SOUNDS_DIR / f"{sound.name}.ogg"
            if not path.exists():
                log.warning(f"Sound file not found: {path}")
                return
            self._pipeline = _build_file_pipeline(path=path, volume=self._state.volume)
        else:
            wave = NOISE_WAVES.get(sound.name, 0)
            self._pipeline = _build_noise_pipeline(wave=wave, volume=self._state.volume)

        if not self._pipeline:
            log.warning(f"Failed to create pipeline for {sound_label(name=sound.name)}")
            return

        # Loop on EOS (file sounds only -- noise is infinite)
        self._bus_watching = False
        if sound.kind == "file":
            bus = self._pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)
            self._bus_watching = True

        self._pipeline.set_state(Gst.State.PLAYING)
        self._state = set_playing(state=self._state, playing=True)

    def _stop_playback(self) -> None:
        if self._pipeline:
            log.debug(f"Stopping pipeline: {self._state.current}")
            if self._bus_watching:
                bus = self._pipeline.get_bus()
                if bus:
                    bus.remove_signal_watch()
                self._bus_watching = False
            self._pipeline.set_state(Gst.State.NULL)
            # Wait for state change to complete
            self._pipeline.get_state(Gst.CLOCK_TIME_NONE)
            self._pipeline = None
            log.debug("Pipeline stopped")
        self._state = set_playing(state=self._state, playing=False)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        """Loop: seek back to start on end-of-stream."""
        if self._pipeline:
            self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)

    def _apply_volume(self) -> None:
        if not self._pipeline:
            return
        # playbin has a volume property directly
        if self._pipeline.find_property("volume"):
            self._pipeline.set_property("volume", self._state.volume)
        else:
            # noise pipeline: find the volume element by name
            volume_element = self._pipeline.get_by_name("vol")
            if volume_element:
                volume_element.set_property("volume", self._state.volume)

    def _save(self) -> None:
        self.save_prefs(prefs=prefs_from_state(state=self._state))
