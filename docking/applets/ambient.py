"""Ambient sound applet -- looping nature sounds and procedural noise."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import GdkPixbuf, Gst, Gtk  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="ambient")

Gst.init(None)

VOLUME_STEP = 0.1
SOUNDS_DIR = Path(__file__).resolve().parent.parent / "assets" / "sounds"


class Sound(NamedTuple):
    """An ambient sound entry."""

    name: str
    label: str
    kind: str  # "file" or "noise"


# Bundled OGG files (CC0 / Public Domain)
_FILE_SOUNDS: list[Sound] = [
    Sound(name="birds", label="Birds", kind="file"),
    Sound(name="boat", label="Boat", kind="file"),
    Sound(name="coffee-shop", label="Coffee Shop", kind="file"),
    Sound(name="fireplace", label="Fireplace", kind="file"),
    Sound(name="stream", label="Stream", kind="file"),
    Sound(name="summer-night", label="Summer Night", kind="file"),
    Sound(name="wind", label="Wind", kind="file"),
]

# Procedural noise via GStreamer audiotestsrc
_NOISE_SOUNDS: list[Sound] = [
    Sound(name="white-noise", label="White Noise", kind="noise"),
    Sound(name="pink-noise", label="Pink Noise", kind="noise"),
]

ALL_SOUNDS: list[Sound] = _FILE_SOUNDS + _NOISE_SOUNDS

_NOISE_WAVES: dict[str, int] = {
    "white-noise": 0,  # audiotestsrc wave=white-noise
    "pink-noise": 6,  # audiotestsrc wave=pink-noise (tpd)
}

DEFAULT_SOUND = "birds"
DEFAULT_VOLUME = 0.5


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
    pipeline = Gst.parse_launch(
        f"audiotestsrc wave={wave} ! volume name=vol volume={volume} ! autoaudiosink"
    )
    return pipeline


class AmbientApplet(Applet):
    """Looping ambient soundscape player.

    Click to toggle play/stop. Scroll to adjust volume.
    Right-click menu lists all available sounds.
    """

    id = "ambient"
    name = "Ambient"
    icon_name = "audio-speakers"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._current = DEFAULT_SOUND
        self._volume = DEFAULT_VOLUME
        self._playing = False
        self._pipeline: Gst.Element | None = None

        if config:
            prefs = config.applet_prefs.get("ambient", {})
            self._current = prefs.get("sound", DEFAULT_SOUND)
            self._volume = prefs.get("volume", DEFAULT_VOLUME)

        super().__init__(icon_size, config)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        if self._playing:
            label = next(
                (s.label for s in ALL_SOUNDS if s.name == self._current),
                self._current,
            )
            vol_pct = int(self._volume * 100)
            self.item.name = f"Playing: {label} ({vol_pct}%)"
        else:
            self.item.name = "Ambient"

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return load_theme_icon(name="audio-speakers", size=size)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)

    def stop(self) -> None:
        self._stop_playback()
        super().stop()

    def on_clicked(self) -> None:
        if self._playing:
            self._stop_playback()
        else:
            self._start_playback()
        self._update_tooltip()
        self.refresh_icon()

    def on_scroll(self, direction_up: bool) -> None:
        if direction_up:
            self._volume = min(1.0, self._volume + VOLUME_STEP)
        else:
            self._volume = max(0.0, self._volume - VOLUME_STEP)
        self._apply_volume()
        self._save()
        self._update_tooltip()
        self.refresh_icon()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        for sound in ALL_SOUNDS:
            mi = Gtk.CheckMenuItem(label=sound.label)
            mi.set_active(self._current == sound.name and self._playing)
            mi.connect(
                "toggled",
                lambda _w, s=sound.name: self._select_sound(name=s),
            )
            items.append(mi)
        return items

    def _select_sound(self, name: str) -> None:
        was_playing = self._playing
        if self._playing:
            self._stop_playback()
        self._current = name
        self._save()
        if was_playing or self._current != name:
            self._start_playback()
        self._update_tooltip()
        self.refresh_icon()

    def _start_playback(self) -> None:
        self._stop_playback()
        sound = next((s for s in ALL_SOUNDS if s.name == self._current), None)
        if not sound:
            return

        if sound.kind == "file":
            path = SOUNDS_DIR / f"{sound.name}.ogg"
            if not path.exists():
                _log.warning("Sound file not found: %s", path)
                return
            self._pipeline = _build_file_pipeline(path=path, volume=self._volume)
        else:
            wave = _NOISE_WAVES.get(sound.name, 0)
            self._pipeline = _build_noise_pipeline(wave=wave, volume=self._volume)

        if not self._pipeline:
            _log.warning("Failed to create pipeline for %s", sound.name)
            return

        # Loop on EOS (file sounds only — noise is infinite)
        if sound.kind == "file":
            bus = self._pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)

        self._pipeline.set_state(Gst.State.PLAYING)
        self._playing = True

    def _stop_playback(self) -> None:
        if self._pipeline:
            bus = self._pipeline.get_bus()
            if bus:
                bus.remove_signal_watch()
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._playing = False

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        """Loop: seek back to start on end-of-stream."""
        if self._pipeline:
            self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)

    def _apply_volume(self) -> None:
        if not self._pipeline:
            return
        # playbin has a volume property directly
        if self._pipeline.find_property("volume"):
            self._pipeline.set_property("volume", self._volume)
        else:
            # noise pipeline: find the volume element by name
            vol = self._pipeline.get_by_name("vol")
            if vol:
                vol.set_property("volume", self._volume)

    def _save(self) -> None:
        self.save_prefs(prefs={"sound": self._current, "volume": self._volume})
