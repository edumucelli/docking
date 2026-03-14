"""Volume applet behavior and GTK wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import create_volume_icon
from .state import _BACKENDS, STEP, VolumeState, _detect_backend, _volume_icon_name

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="volume"), applet_id=str(AppletId.VOLUME))


class VolumeApplet(Applet):
    """Volume control via scroll and click."""

    id = AppletId.VOLUME
    name = _("Volume")
    icon_name = "audio-volume-medium"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend = _detect_backend()
        if not self._backend:
            _log.bind(action="detect_backend").warning(
                "No audio backend found (%s)",
                ", ".join(b.command for b in _BACKENDS),
            )
        self._volume = 0
        self._muted = False
        self._timer_id: int = 0
        self._worker = BackgroundWorker(logger=_log)
        self._poll()
        super().__init__(icon_size, config)
        self.present()

    def _update_tooltip(self) -> None:
        self.item.name = (
            _("Muted") if self._muted else _("Volume: {pct}%").format(pct=self._volume)
        )
        self.item.icon_name = _volume_icon_name(volume=self._volume, muted=self._muted)

    def refresh_tooltip(self) -> None:
        self._update_tooltip()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_volume_icon(size=size, volume=self._volume, muted=self._muted)

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
            self.present()

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
        self.present()

    def _poll(self) -> None:
        """Read current volume state from backend."""
        if not self._backend:
            return
        state = self._backend.get_state()
        if state is not None:
            self._volume = state.volume
            self._muted = state.muted

    def _tick(self) -> bool:
        """Periodic poll - fetch state in background thread."""
        if not self._backend:
            return True
        self._worker.run(
            name="volume-poll",
            fn=self._backend.get_state,
            on_result=self._on_poll_result,
        )
        return True

    def _on_poll_result(self, state: VolumeState | None) -> bool:
        """Apply polled state on main thread, refresh if changed."""
        if state is None:
            return False
        if (state.volume, state.muted) != (self._volume, self._muted):
            self._volume = state.volume
            self._muted = state.muted
            self._update_tooltip()
            self.present()
        return False
