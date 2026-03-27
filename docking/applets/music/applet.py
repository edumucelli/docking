"""GTK lifecycle glue for Music applet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.music import meta
from docking.applets.worker import BackgroundWorker
from docking.i18n import _

from .artwork import CoverArtResolver
from .render import create_music_icon
from .state import (
    VOLUME_STEP,
    HybridBackend,
    MusicState,
    clamp_percent,
    play_pause_menu_label,
    tooltip_text,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.core.config import Config

POLL_INTERVAL_S = 1
SCROLL_SYNC_DELAY_MS = 220


class MusicApplet(Applet):
    """Media control applet with album-art rendering."""

    id = meta.id
    name = _("Music")
    icon_name = "audio-x-generic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend = HybridBackend()
        self._cover_art = CoverArtResolver()
        self._state = unavailable_state()
        self._album_art: GdkPixbuf.Pixbuf | None = None
        self._timer_id: int = 0
        self._scroll_sync_id: int = 0
        self._worker = BackgroundWorker()

        self._state = self._backend.poll()
        self._album_art = self._cover_art.resolve(state=self._state)
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_music_icon(
            size=size,
            playback_status=self._state.playback_status,
            album_art=self._album_art,
            volume_percent=self._state.volume_percent,
            available=self._state.available,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(state=self._state)
        self.item.tooltip_builder = self._build_tooltip_widget

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._scroll_sync_id:
            GLib.source_remove(self._scroll_sync_id)
            self._scroll_sync_id = 0
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        if self._backend.play_pause(state=self._state):
            self._refresh_now()

    def on_scroll(self, direction_up: bool) -> None:
        if not self._state.available:
            return
        delta = VOLUME_STEP if direction_up else -VOLUME_STEP
        target = clamp_percent(self._state.volume_percent + delta)
        if self._backend.set_volume(state=self._state, volume_percent=target):
            # Avoid synchronous re-poll on rapid scroll bursts (can stall UI).
            self._state = replace(self._state, volume_percent=target)
            self.present()
            self._schedule_scroll_sync()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        if not self._state.available:
            placeholder = Gtk.MenuItem(label=_("No active player"))
            placeholder.set_sensitive(False)
            return [placeholder]

        previous_item = Gtk.MenuItem(label=_("Previous"))
        previous_item.set_sensitive(self._state.can_go_previous)
        previous_item.connect("activate", lambda _w: self._action_previous())

        play_pause_item = Gtk.MenuItem(label=play_pause_menu_label(state=self._state))
        play_pause_item.set_sensitive(self._state.can_play_pause)
        play_pause_item.connect("activate", lambda _w: self._action_play_pause())

        next_item = Gtk.MenuItem(label=_("Next"))
        next_item.set_sensitive(self._state.can_go_next)
        next_item.connect("activate", lambda _w: self._action_next())

        volume_up_item = Gtk.MenuItem(label=_("Volume Up"))
        volume_up_item.connect(
            "activate",
            lambda _w: self._action_volume(direction_up=True),
        )

        volume_down_item = Gtk.MenuItem(label=_("Volume Down"))
        volume_down_item.connect(
            "activate", lambda _w: self._action_volume(direction_up=False)
        )

        return [
            previous_item,
            play_pause_item,
            next_item,
            Gtk.SeparatorMenuItem(),
            volume_up_item,
            volume_down_item,
        ]

    def _action_previous(self) -> None:
        if self._backend.previous_track(state=self._state):
            self._refresh_now()

    def _action_play_pause(self) -> None:
        if self._backend.play_pause(state=self._state):
            self._refresh_now()

    def _action_next(self) -> None:
        if self._backend.next_track(state=self._state):
            self._refresh_now()

    def _action_volume(self, direction_up: bool) -> None:
        self.on_scroll(direction_up=direction_up)

    def _tick(self) -> bool:
        self._worker.run_guarded(
            key="poll",
            name="music-poll",
            fn=self._poll_worker,
            on_result=self._on_poll_result,
        )
        return True

    def _poll_worker(self) -> tuple[MusicState, GdkPixbuf.Pixbuf | None]:
        state = self._backend.poll()
        art = self._cover_art.resolve(state=state)
        return state, art

    def _on_poll_result(
        self,
        result: tuple[MusicState, GdkPixbuf.Pixbuf | None],
    ) -> bool:
        state, art = result
        return self._apply_poll_result(state, art)

    def _refresh_now(self) -> None:
        state, art = self._poll_worker()
        self._apply_poll_result(state, art)

    def _apply_poll_result(
        self,
        state: MusicState,
        art: GdkPixbuf.Pixbuf | None,
    ) -> bool:
        if state == self._state and art is self._album_art:
            return False
        self._state = state
        self._album_art = art
        self.present()
        return False

    def _schedule_scroll_sync(self) -> None:
        if self._scroll_sync_id:
            GLib.source_remove(self._scroll_sync_id)
        self._scroll_sync_id = GLib.timeout_add(
            SCROLL_SYNC_DELAY_MS,
            self._run_scroll_sync,
        )

    def _run_scroll_sync(self) -> bool:
        self._scroll_sync_id = 0
        self._worker.run_guarded(
            key="poll",
            name="music-poll",
            fn=self._poll_worker,
            on_result=self._on_poll_result,
        )
        return False

    def _build_tooltip_widget(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        lines = tooltip_text(state=self._state).splitlines() or ["Music"]
        for line in lines:
            label = Gtk.Label(label=line)
            label.set_xalign(0.5)
            label.set_justify(Gtk.Justification.CENTER)
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            box.pack_start(label, False, False, 0)
        return box
