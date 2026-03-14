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
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="ambient")

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

    id = AppletId.AMBIENT
    name = _("Ambient")
    icon_name = "audio-speakers"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = config.applet_prefs.get("ambient", {}) if config else None
        self._state = state_from_prefs(prefs=prefs)
        self._pipeline: Gst.Element | None = None
        self._bus_watching = False

        super().__init__(icon_size, config)
        self._update_tooltip()

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
            volume=max(0.0, min(1.0, value)),
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
        self.refresh_presentation()

    def on_scroll(self, direction_up: bool) -> None:
        self._state = adjust_volume(state=self._state, direction_up=direction_up)
        self._apply_volume()
        self._save()
        self._update_tooltip()
        self.refresh_presentation()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        for sound in ALL_SOUNDS:
            menu_item = Gtk.CheckMenuItem(label=sound.label)
            menu_item.set_active(
                self._state.current == sound.name and self._state.playing
            )
            menu_item.connect(
                "toggled",
                lambda _w, s=sound.name: self._select_sound(name=s),
            )
            items.append(menu_item)
        return items

    def _select_sound(self, name: str) -> None:
        was_playing = self._state.playing
        if self._state.playing:
            self._stop_playback()
        self._state = set_sound(state=self._state, name=name)
        self._save()
        if was_playing:
            self._start_playback()
        self._update_tooltip()
        self.refresh_presentation()

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
                _log.warning(f"Sound file not found: {path}")
                return
            self._pipeline = _build_file_pipeline(path=path, volume=self._state.volume)
        else:
            wave = NOISE_WAVES.get(sound.name, 0)
            self._pipeline = _build_noise_pipeline(wave=wave, volume=self._state.volume)

        if not self._pipeline:
            _log.warning(
                f"Failed to create pipeline for {sound_label(name=sound.name)}"
            )
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
            _log.debug(f"Stopping pipeline: {self._state.current}")
            if self._bus_watching:
                bus = self._pipeline.get_bus()
                if bus:
                    bus.remove_signal_watch()
                self._bus_watching = False
            self._pipeline.set_state(Gst.State.NULL)
            # Wait for state change to complete
            self._pipeline.get_state(Gst.CLOCK_TIME_NONE)
            self._pipeline = None
            _log.debug("Pipeline stopped")
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
