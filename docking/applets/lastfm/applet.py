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

"""GTK lifecycle for the Last.fm applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.http import http_get_bytes, http_get_json
from docking.applets.lastfm import meta
from docking.applets.lastfm.render import (
    pixbuf_from_bytes,
    render_default_icon,
    round_pixbuf_corners,
)
from docking.applets.lastfm.state import (
    DEFAULT_REFRESH_SECONDS,
    LASTFM_SERVICE,
    LIBREFM_SERVICE,
    MAX_MAX_ENTRIES,
    MIN_MAX_ENTRIES,
    ImageCache,
    LastfmPrefs,
    PlayedTrack,
    best_image_url,
    build_recent_tracks_url,
    format_relative_time,
    is_placeholder_image,
    parse_recent_tracks,
    prefs_from_mapping,
    prefs_payload,
    profile_url,
    service_display_name,
    tooltip_for,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.popup import (
    DEFAULT_DIALOG_CONTENT_SPACING_PX,
    DEFAULT_DIALOG_MARGIN_PX,
    prepare_dialog_content,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="lastfm"), applet_id=meta.id)

MENU_ICON_SIZE = 32
MAX_TRACK_DISPLAY_CHARS = 40
STARTUP_FETCH_DELAY_S = 1
PREFS_DIALOG_WIDTH_PX = 360


class LastfmApplet(Applet):
    """Display the user's most recent Last.fm scrobbles."""

    id = meta.id
    name = _("Last.fm")
    icon_name = "lastfm"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = prefs_from_mapping(
            config.applet_prefs.get(meta.id, {}) if config else None
        )
        self._prefs = prefs
        self._tracks: list[PlayedTrack] = []
        self._image_cache = ImageCache(max_entries=prefs.max_entries)
        self._error = ""
        self._loading = False
        self._refresh_timer_id = 0
        self._startup_fetch_timer_id = 0
        self._fetch_request_id = 0
        self._worker = BackgroundWorker(logger=log)

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _current_track(self) -> PlayedTrack | None:
        return self._tracks[0] if self._tracks else None

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        track = self._current_track
        if track is not None:
            url = best_image_url(track)
            if url and not is_placeholder_image(url):
                data = self._image_cache.get(url)
                if data is not None:
                    pixbuf = pixbuf_from_bytes(data, size)
                    if pixbuf is not None:
                        return (
                            round_pixbuf_corners(pixbuf)
                            if self._prefs.rounded_corners
                            else pixbuf
                        )
        return render_default_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_for(self._prefs, self._current_track)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._refresh_timer_id = GLib.timeout_add_seconds(
            DEFAULT_REFRESH_SECONDS,
            self._refresh_tick,
        )
        self._startup_fetch_timer_id = GLib.timeout_add_seconds(
            STARTUP_FETCH_DELAY_S,
            self._run_startup_fetch,
        )

    def stop(self) -> None:
        for attr in ("_refresh_timer_id", "_startup_fetch_timer_id"):
            timer_id = getattr(self, attr, 0)
            if timer_id:
                GLib.source_remove(timer_id)
                setattr(self, attr, 0)
        super().stop()

    def on_clicked(self) -> None:
        if not self._prefs.is_configured:
            self._show_prefs_dialog()
            return
        track = self._current_track
        if track is not None and track.track_url:
            _open_uri(track.track_url)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        service_label = service_display_name(self._prefs.service)
        status: list[Gtk.MenuItem] = [
            disabled_menu_item(service_label, gtk=Gtk),
        ]
        if not self._prefs.is_configured:
            hint = (
                _("Set username...")
                if self._prefs.service == LIBREFM_SERVICE
                else _("Set API key and username...")
            )
            status.append(disabled_menu_item(hint, gtk=Gtk))
        else:
            status.append(
                disabled_menu_item(
                    _("Recent tracks for {user} ({count})").format(
                        user=self._prefs.username, count=len(self._tracks)
                    ),
                    gtk=Gtk,
                )
            )
            if self._error:
                status.append(
                    disabled_menu_item(
                        _("Error: {msg}").format(msg=self._error), gtk=Gtk
                    )
                )

        primary = [
            self._build_track_menu_item(t, i) for i, t in enumerate(self._tracks)
        ]

        configure = Gtk.MenuItem(label=_("Configure..."))
        configure.connect("activate", lambda _w: self._show_prefs_dialog())

        navigation: list[Gtk.MenuItem] = [configure]
        if self._prefs.username:
            profile = Gtk.MenuItem(
                label=_("Open {service} Profile").format(service=service_label),
            )
            profile.connect(
                "activate",
                lambda _w: _open_uri(
                    profile_url(self._prefs.service, self._prefs.username)
                ),
            )
            navigation.append(profile)

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.set_sensitive(self._prefs.is_configured)
        refresh.connect("activate", lambda _w: self._fetch_async())

        return menu_sections(
            status=status,
            primary=primary,
            navigation=navigation,
            refresh=[refresh],
            gtk=Gtk,
        )

    def _build_track_menu_item(self, track: PlayedTrack, index: int) -> Gtk.MenuItem:
        item = Gtk.MenuItem()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_start(8)
        hbox.set_margin_end(8)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)

        art = self._menu_album_art(track)
        hbox.pack_start(art, False, False, 0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_label = Gtk.Label()
        if track.is_now_playing:
            markup = f"<b>♪ {GLib.markup_escape_text(track.track_name)} ♪</b>"
        else:
            markup = GLib.markup_escape_text(track.track_name)
        title_label.set_markup(markup)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_ellipsize(_pango_ellipsize_end())
        title_label.set_max_width_chars(MAX_TRACK_DISPLAY_CHARS)
        text_box.pack_start(title_label, False, False, 0)

        artist_label = Gtk.Label()
        artist_label.set_markup(
            f"<small>{GLib.markup_escape_text(track.artist)}</small>"
        )
        artist_label.set_halign(Gtk.Align.START)
        artist_label.set_ellipsize(_pango_ellipsize_end())
        artist_label.set_max_width_chars(MAX_TRACK_DISPLAY_CHARS)
        text_box.pack_start(artist_label, False, False, 0)

        if track.album:
            album_label = Gtk.Label()
            album_label.set_markup(
                f"<small><i>{GLib.markup_escape_text(track.album)}</i></small>"
            )
            album_label.set_halign(Gtk.Align.START)
            album_label.set_ellipsize(_pango_ellipsize_end())
            album_label.set_max_width_chars(MAX_TRACK_DISPLAY_CHARS)
            text_box.pack_start(album_label, False, False, 0)

        hbox.pack_start(text_box, True, True, 0)

        if track.is_loved:
            heart = Gtk.Label()
            heart.set_markup("<span color='red'>♥</span>")
            hbox.pack_end(heart, False, False, 0)

        if not track.is_now_playing and track.timestamp:
            time_label = Gtk.Label()
            rel = GLib.markup_escape_text(format_relative_time(track.timestamp))
            time_label.set_markup(f"<small><span color='gray'>{rel}</span></small>")
            hbox.pack_end(time_label, False, False, 0)

        item.add(hbox)
        if track.track_url:
            item.connect("activate", lambda _w, url=track.track_url: _open_uri(url))
        else:
            item.set_sensitive(False)
        _ = index  # reserved for future click ordering
        return item

    def _menu_album_art(self, track: PlayedTrack) -> Gtk.Image:
        image = Gtk.Image()
        image.set_size_request(MENU_ICON_SIZE, MENU_ICON_SIZE)
        url = best_image_url(track)
        if url and not is_placeholder_image(url):
            data = self._image_cache.get(url)
            if data is not None:
                pixbuf = pixbuf_from_bytes(data, MENU_ICON_SIZE)
                if pixbuf is not None:
                    image.set_from_pixbuf(pixbuf)
                    return image
        image.set_from_icon_name("audio-x-generic", Gtk.IconSize.LARGE_TOOLBAR)
        return image

    def _refresh_tick(self) -> bool:
        self._fetch_async()
        return True

    def _run_startup_fetch(self) -> bool:
        self._startup_fetch_timer_id = 0
        self._fetch_async()
        return False

    def _fetch_async(self) -> None:
        if not self._prefs.is_configured:
            self._error = ""
            self.present()
            return

        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        self._loading = True
        self._error = ""
        prefs = self._prefs

        self._worker.run(
            name="lastfm-fetch",
            fn=lambda: _fetch_tracks_blocking(prefs=prefs),
            on_result=lambda tracks: self._on_fetch_result(
                request_id=request_id, tracks=tracks
            ),
            on_error=lambda exc: self._on_fetch_error(request_id=request_id, exc=exc),
        )

    def _on_fetch_result(self, *, request_id: int, tracks: list[PlayedTrack]) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._tracks = tracks
        self._error = ""
        self._prefetch_images()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._error = str(exc) or exc.__class__.__name__
        log.warning("Last.fm fetch failed: %s", exc)
        self.present()
        return False

    def _prefetch_images(self) -> None:
        """Download album art for visible tracks, then re-present."""
        urls = [
            url
            for url in (best_image_url(t) for t in self._tracks)
            if url
            and not is_placeholder_image(url)
            and self._image_cache.get(url) is None
        ]
        if not urls:
            self.present()
            return

        request_id = self._fetch_request_id

        def fetch_all() -> dict[str, bytes]:
            results: dict[str, bytes] = {}
            for url in urls:
                try:
                    results[url] = http_get_bytes(url)
                except Exception as exc:
                    log.debug("Failed to download album art %s: %s", url, exc)
            return results

        self._worker.run(
            name="lastfm-images",
            fn=fetch_all,
            on_result=lambda results: self._on_images_result(
                request_id=request_id, results=results
            ),
        )

    def _on_images_result(self, *, request_id: int, results: dict[str, bytes]) -> bool:
        if request_id != self._fetch_request_id:
            return False
        for url, data in results.items():
            self._image_cache.set(url, data)
        self.present()
        return False

    def _show_prefs_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Scrobbler Settings"),
            modal=True,
            destroy_with_parent=True,
        )
        self.register_popup_surface(dialog)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Save"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        box = prepare_dialog_content(
            dialog=dialog,
            width=PREFS_DIALOG_WIDTH_PX,
            spacing=DEFAULT_DIALOG_CONTENT_SPACING_PX,
            margin=DEFAULT_DIALOG_MARGIN_PX,
        )

        service_label = Gtk.Label(label=_("Service:"), xalign=0)
        service_combo = Gtk.ComboBoxText()
        service_combo.append(LASTFM_SERVICE, service_display_name(LASTFM_SERVICE))
        service_combo.append(LIBREFM_SERVICE, service_display_name(LIBREFM_SERVICE))
        service_combo.set_active_id(self._prefs.service)

        api_label = Gtk.Label(label=_("API Key:"), xalign=0)
        api_entry = Gtk.Entry()
        api_entry.set_text(self._prefs.api_key)
        api_entry.set_visibility(False)

        user_label = Gtk.Label(label=_("Username:"), xalign=0)
        user_entry = Gtk.Entry()
        user_entry.set_text(self._prefs.username)

        def refresh_service_dependent_fields() -> None:
            active = service_combo.get_active_id() or LASTFM_SERVICE
            name = service_display_name(active)
            api_entry.set_placeholder_text(
                _("{service} API key (optional)").format(service=name)
                if active == LIBREFM_SERVICE
                else _("{service} API key").format(service=name)
            )
            user_entry.set_placeholder_text(
                _("{service} username").format(service=name)
            )
            api_label.set_sensitive(active != LIBREFM_SERVICE)
            api_entry.set_sensitive(active != LIBREFM_SERVICE)

        service_combo.connect("changed", lambda _w: refresh_service_dependent_fields())
        refresh_service_dependent_fields()

        entries_label = Gtk.Label(label=_("Max entries:"), xalign=0)
        adjustment = Gtk.Adjustment(
            value=self._prefs.max_entries,
            lower=MIN_MAX_ENTRIES,
            upper=MAX_MAX_ENTRIES,
            step_increment=1,
        )
        entries_spin = Gtk.SpinButton(adjustment=adjustment, numeric=True)

        rounded_check = Gtk.CheckButton(label=_("Rounded album-art corners"))
        rounded_check.set_active(self._prefs.rounded_corners)

        for widget in (
            service_label,
            service_combo,
            api_label,
            api_entry,
            user_label,
            user_entry,
            entries_label,
            entries_spin,
            rounded_check,
        ):
            box.pack_start(widget, False, False, 0)

        def on_response(_dialog: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.OK:
                self._apply_new_prefs(
                    api_key=api_entry.get_text().strip(),
                    username=user_entry.get_text().strip(),
                    max_entries=int(entries_spin.get_value()),
                    rounded_corners=rounded_check.get_active(),
                    service=service_combo.get_active_id() or LASTFM_SERVICE,
                )
            _dialog.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()

    def _apply_new_prefs(
        self,
        *,
        api_key: str,
        username: str,
        max_entries: int,
        rounded_corners: bool,
        service: str = LASTFM_SERVICE,
    ) -> None:
        new_prefs = LastfmPrefs(
            api_key=api_key,
            username=username,
            max_entries=max(MIN_MAX_ENTRIES, min(MAX_MAX_ENTRIES, max_entries)),
            rounded_corners=rounded_corners,
            service=service,
        )
        creds_changed = (
            new_prefs.api_key != self._prefs.api_key
            or new_prefs.username != self._prefs.username
            or new_prefs.service != self._prefs.service
        )
        self._prefs = new_prefs
        self._image_cache.resize(new_prefs.max_entries)
        if creds_changed:
            self._tracks = []
            self._image_cache.clear()
        self.save_prefs(prefs_payload(new_prefs))
        self.present()
        self._fetch_async()


def _fetch_tracks_blocking(*, prefs: LastfmPrefs) -> list[PlayedTrack]:
    """Background-thread scrobbler fetch. Pure HTTP + JSON parse."""
    url = build_recent_tracks_url(
        api_key=prefs.api_key,
        username=prefs.username,
        limit=prefs.max_entries,
        service=prefs.service,
    )
    payload = http_get_json(url)
    return parse_recent_tracks(payload, limit=prefs.max_entries)


def _open_uri(uri: str) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error as exc:
        log.warning("Failed to open URI %s: %s", uri, exc)


def _pango_ellipsize_end():
    """Return ``Pango.EllipsizeMode.END`` while keeping the import lazy."""
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango

    return Pango.EllipsizeMode.END
