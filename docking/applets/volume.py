"""Volume applet -- scroll to adjust, click to mute, auto-detected backend."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Callable, NamedTuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="volume")

STEP = 5


class VolumeState(NamedTuple):
    """Current audio output state."""

    volume: int
    muted: bool


# ---------------------------------------------------------------------------
# Parsers - pure functions, no side effects
# ---------------------------------------------------------------------------

_PACTL_VOL_RE = re.compile(r"(\d+)%")
_AMIXER_RE = re.compile(r"\[(\d+)%\].*?\[(on|off)\]")


def _parse_pactl_volume(output: str) -> int | None:
    """Extract first percentage from pactl get-sink-volume output."""
    m = _PACTL_VOL_RE.search(output)
    return int(m.group(1)) if m else None


def _parse_pactl_mute(output: str) -> bool | None:
    """Parse 'Mute: yes/no' from pactl get-sink-mute output."""
    if "yes" in output.lower():
        return True
    if "no" in output.lower():
        return False
    return None


def _parse_amixer(output: str) -> VolumeState | None:
    """Extract volume % and on/off from amixer get Master output."""
    m = _AMIXER_RE.search(output)
    if not m:
        return None
    return VolumeState(volume=int(m.group(1)), muted=m.group(2) == "off")


# ---------------------------------------------------------------------------
# Icon name resolution
# ---------------------------------------------------------------------------


def _volume_icon_name(volume: int, muted: bool) -> str:
    """Map volume level + mute state to a FreeDesktop icon name."""
    if muted or volume == 0:
        return "audio-volume-muted"
    if volume <= 33:
        return "audio-volume-low"
    if volume <= 66:
        return "audio-volume-medium"
    return "audio-volume-high"


# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------


class Backend(NamedTuple):
    """Audio backend with commands for reading/setting volume."""

    command: str
    get_state: Callable[[], VolumeState | None]
    set_volume: Callable[[int], None]
    toggle_mute: Callable[[], None]


def _run(cmd: list[str]) -> str | None:
    """Run command, return stdout or None on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("Failed to run %s: %s", cmd, exc)
    return None


def _pactl_get_state() -> VolumeState | None:
    vol_out = _run(cmd=["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    mute_out = _run(cmd=["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
    if vol_out is None or mute_out is None:
        return None
    vol = _parse_pactl_volume(output=vol_out)
    muted = _parse_pactl_mute(output=mute_out)
    if vol is None or muted is None:
        return None
    return VolumeState(volume=vol, muted=muted)


def _pactl_set_volume(volume: int) -> None:
    _run(cmd=["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"])


def _pactl_toggle_mute() -> None:
    _run(cmd=["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])


def _amixer_get_state() -> VolumeState | None:
    out = _run(cmd=["amixer", "get", "Master"])
    if out is None:
        return None
    return _parse_amixer(output=out)


def _amixer_set_volume(volume: int) -> None:
    _run(cmd=["amixer", "set", "Master", f"{volume}%"])


def _amixer_toggle_mute() -> None:
    _run(cmd=["amixer", "set", "Master", "toggle"])


_BACKENDS: tuple[Backend, ...] = (
    Backend(
        command="pactl",
        get_state=_pactl_get_state,
        set_volume=_pactl_set_volume,
        toggle_mute=_pactl_toggle_mute,
    ),
    Backend(
        command="amixer",
        get_state=_amixer_get_state,
        set_volume=_amixer_set_volume,
        toggle_mute=_amixer_toggle_mute,
    ),
)


def _detect_backend() -> Backend | None:
    """Return the first available audio backend, or None."""
    for backend in _BACKENDS:
        if shutil.which(backend.command):
            return backend
    return None


# ---------------------------------------------------------------------------
# Applet
# ---------------------------------------------------------------------------


class VolumeApplet(Applet):
    """Volume control via scroll and click.

    Scroll adjusts volume ±5%. Left-click toggles mute.
    Auto-detects pactl (PulseAudio/PipeWire) or amixer (ALSA).
    """

    id = "volume"
    name = "Volume"
    icon_name = "audio-volume-medium"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend = _detect_backend()
        if not self._backend:
            _log.warning(
                "No audio backend found (%s)",
                ", ".join(b.command for b in _BACKENDS),
            )
        self._volume = 0
        self._muted = False
        self._timer_id: int = 0
        self._poll()
        super().__init__(icon_size, config)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        """Sync item name and icon_name with current state."""
        self.item.name = "Muted" if self._muted else f"Volume: {self._volume}%"
        self.item.icon_name = _volume_icon_name(volume=self._volume, muted=self._muted)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        icon = _volume_icon_name(volume=self._volume, muted=self._muted)
        return load_theme_icon(name=icon, size=size)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Toggle mute on left-click."""
        if self._backend:
            self._backend.toggle_mute()
            self._poll()
            self._update_tooltip()
            self.refresh_icon()

    def on_scroll(self, direction_up: bool) -> None:
        """Adjust volume ±5% on scroll."""
        if not self._backend:
            return
        if direction_up:
            new = min(100, self._volume + STEP)
        else:
            new = max(0, self._volume - STEP)
        self._backend.set_volume(new)
        self._poll()
        self._update_tooltip()
        self.refresh_icon()

    def _poll(self) -> None:
        """Read current volume state from backend."""
        if not self._backend:
            return
        state = self._backend.get_state()
        if state is not None:
            self._volume = state.volume
            self._muted = state.muted

    def _tick(self) -> bool:
        """Periodic poll - refresh icon only if state changed."""
        prev = VolumeState(volume=self._volume, muted=self._muted)
        self._poll()
        if (self._volume, self._muted) != prev:
            self._update_tooltip()
            self.refresh_icon()
        return True
