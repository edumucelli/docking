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

"""GTK lifecycle glue for Music applet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.music import meta
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.launcher import launch as launch_desktop_id

from .artwork import CoverArtResolver
from .render import create_music_icon
from .state import (
    VOLUME_STEP,
    HybridBackend,
    MusicState,
    clamp_percent,
    has_active_media,
    play_pause_menu_label,
    tooltip_text,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.core.config import Config

POLL_INTERVAL_S = 1
SCROLL_SYNC_DELAY_MS = 220
MEDIA_CONTENT_TYPES = (
    "audio/mpeg",
    "audio/flac",
    "audio/ogg",
    "audio/x-wav",
    "video/mp4",
)
log = with_context(get_logger(name="music"), applet_id=meta.id)


def _find_media_app() -> Gio.AppInfo | None:
    """Find the default or another installed application for common media."""
    for content_type in MEDIA_CONTENT_TYPES:
        try:
            app_info = Gio.AppInfo.get_default_for_type(content_type, False)
        except GLib.Error as exc:
            log.bind(action="find_media_app", content_type=content_type).debug(
                "Failed to resolve default media application: %s",
                exc,
            )
            continue
        if app_info is not None:
            return app_info

    for lookup_name in ("get_recommended_for_type", "get_all_for_type"):
        lookup = getattr(Gio.AppInfo, lookup_name, None)
        if not callable(lookup):
            continue
        for content_type in MEDIA_CONTENT_TYPES:
            try:
                candidates = lookup(content_type)
            except GLib.Error as exc:
                log.bind(action="find_media_app", content_type=content_type).debug(
                    "Failed to list media applications: %s",
                    exc,
                )
                continue
            for app_info in candidates or ():
                if app_info is not None and app_info.should_show():
                    return app_info
    return None


def launch_default_media_app() -> bool:
    """Launch an installed media application, preferring desktop defaults."""
    app_info = _find_media_app()
    if app_info is None:
        return False

    desktop_id = app_info.get_id()
    if desktop_id:
        launch_desktop_id(desktop_id=desktop_id)
        return True

    try:
        return bool(app_info.launch([], None))
    except GLib.Error as exc:
        log.bind(action="launch_media_app").warning(
            "Failed to launch media application: %s",
            exc,
        )
        return False


class MusicApplet(Applet):
    """Media control applet with album-art rendering."""

    id = meta.id
    name = _("Music")
    icon_name = "audio-x-generic"

    def __init__(self, icon_size: int, config: Config) -> None:
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
        if not has_active_media(self._state):
            launch_default_media_app()
            return
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
            return [disabled_menu_item(_("No active player"), gtk=Gtk)]

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

        return menu_sections(
            primary=[play_pause_item],
            navigation=[previous_item, next_item],
            display=[volume_up_item, volume_down_item],
            gtk=Gtk,
        )

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
