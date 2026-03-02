"""Ambient sound applet -- looping nature sounds and procedural noise."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gst, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
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


def _rounded_rect_path(
    cr: cairo.Context, x: float, y: float, w: float, h: float, r: float
) -> None:
    """Build a rounded-rectangle path."""
    r = max(0.0, min(r, min(w, h) / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_waveform_icon(cr: cairo.Context, size: int) -> None:
    """Draw purple rounded tile with centered waveform bars."""
    margin = size * 0.09
    x = margin
    y = margin
    w = size - (2 * margin)
    h = w
    radius = 0.13 * w

    # Subtle drop shadow.
    _rounded_rect_path(
        cr=cr, x=x + (size * 0.01), y=y + (size * 0.018), w=w, h=h, r=radius
    )
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.20)
    cr.fill()

    # Tile fill gradient.
    gradient = cairo.LinearGradient(x, y, x + w, y + h)
    gradient.add_color_stop_rgb(0.0, 0xD2 / 255.0, 0x80 / 255.0, 0xB9 / 255.0)
    gradient.add_color_stop_rgb(1.0, 0x89 / 255.0, 0x40 / 255.0, 0xA8 / 255.0)
    _rounded_rect_path(cr=cr, x=x, y=y, w=w, h=h, r=radius)
    cr.set_source(gradient)
    cr.fill_preserve()

    # Border for crisp edge at smaller sizes.
    cr.set_source_rgba(0.48, 0.20, 0.55, 0.70)
    cr.set_line_width(max(1.0, size * 0.016))
    cr.stroke()

    # Wave bars.
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.88)
    bar_count = 16
    heights = [
        0.30,
        0.55,
        0.75,
        0.88,
        1.00,
        0.84,
        0.54,
        0.34,
        0.26,
        0.34,
        0.54,
        0.84,
        1.00,
        0.75,
        0.55,
        0.30,
    ]

    waveform_w = w * 0.76
    spacing = w * 0.024
    total_spacing = spacing * (bar_count - 1)
    bar_w = max(size * 0.01, (waveform_w - total_spacing) / bar_count)
    max_bar_h = h * 0.52
    start_x = x + (w - ((bar_w * bar_count) + total_spacing)) / 2
    center_y = y + (h / 2)

    for i, height in enumerate(heights):
        bar_h = height * max_bar_h
        if i >= bar_count // 2:
            # Keep horizontal spacing symmetric; compact right half vertically.
            bar_h *= 0.84
        bar_x = start_x + i * (bar_w + spacing)
        bar_y = center_y - (bar_h / 2)
        bar_radius = bar_w * 0.35
        _rounded_rect_path(cr=cr, x=bar_x, y=bar_y, w=bar_w, h=bar_h, r=bar_radius)
        cr.fill()


class AmbientApplet(Applet):
    """Looping ambient soundscape player.

    Click to toggle play/stop. Scroll to adjust volume.
    Right-click menu lists all available sounds.
    """

    id = AppletId.AMBIENT
    name = "Ambient"
    icon_name = "audio-speakers"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._current = DEFAULT_SOUND
        self._volume = DEFAULT_VOLUME
        self._playing = False
        self._pipeline: Gst.Element | None = None
        self._bus_watching = False

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
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _draw_waveform_icon(cr=cr, size=size)
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

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
                _log.warning(f"Sound file not found: {path}")
                return
            self._pipeline = _build_file_pipeline(path=path, volume=self._volume)
        else:
            wave = _NOISE_WAVES.get(sound.name, 0)
            self._pipeline = _build_noise_pipeline(wave=wave, volume=self._volume)

        if not self._pipeline:
            _log.warning(f"Failed to create pipeline for {sound.name}")
            return

        # Loop on EOS (file sounds only - noise is infinite)
        self._bus_watching = False
        if sound.kind == "file":
            bus = self._pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)
            self._bus_watching = True

        self._pipeline.set_state(Gst.State.PLAYING)
        self._playing = True

    def _stop_playback(self) -> None:
        if self._pipeline:
            _log.debug(f"Stopping pipeline: {self._current}")
            if self._bus_watching:
                bus = self._pipeline.get_bus()
                if bus:
                    bus.remove_signal_watch()
                self._bus_watching = False
            self._pipeline.set_state(Gst.State.NULL)
            # Wait for state change to complete
            self._pipeline.get_state(Gst.CLOCK_TIME_NONE)
            del self._pipeline
            self._pipeline = None
            _log.debug("Pipeline stopped")
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
