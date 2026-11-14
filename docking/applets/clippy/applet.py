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

"""GTK lifecycle glue for Clippy applet."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.clippy import meta
from docking.applets.clippy.render import clip_pixbuf, clip_preview, create_icon
from docking.applets.clippy.state import (
    MAX_IMAGE_BYTES,
    Clip,
    ClipKey,
    add_clip,
    cycle_position,
    normalize_max_entries,
    tooltip_text,
    trim_clips,
)
from docking.applets.menu import menu_sections, radio_submenu
from docking.i18n import _
from docking.platform.environment import is_x11_backend

if TYPE_CHECKING:
    from docking.core.config import Config


DEBOUNCE_MS = 500
PASSWORD_MANAGER_HINT = "x-kde-passwordManagerHint"


@dataclass(eq=False)
class _Selection:
    clipboard: Gtk.Clipboard
    primary: bool = False
    handler_id: int = 0
    timer_id: int = 0
    generation: int = 0


class ClippyApplet(Applet):
    """Session clipboard history; scroll to select and click to copy."""

    id = meta.id
    name = _("Clippy")
    icon_name = "edit-paste"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._clips: list[Clip] = []
        self._cur_position: int = 0
        self._clipboard: Gtk.Clipboard | None = None
        self._selections: list[_Selection] = []
        self._primary_available = False
        self._stopped = True
        self._previews: dict[ClipKey, GdkPixbuf.Pixbuf] = {}
        prefs = config.applet_prefs.get("clippy", {})
        self._max_entries = normalize_max_entries(prefs.get("max_entries", 15))
        self._tracking_enabled = prefs.get("tracking_enabled", True) is not False
        self._track_selections = prefs.get("track_selections", False) is True
        self._track_images = prefs.get("track_images", True) is not False

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Static edit-paste icon."""
        return create_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(
            clips=self._clips,
            cur_position=self._cur_position,
        )
        if not self._tracking_enabled:
            self.item.name += "\n" + _("Clipboard tracking paused")

    def on_clicked(self) -> None:
        if self._clips and 0 < self._cur_position <= len(self._clips):
            self._copy_to_clipboard(self._clips[self._cur_position - 1])

    def on_scroll(self, direction_up: bool) -> None:
        """Cycle through clipboard history."""
        self._cur_position = cycle_position(
            clips_len=len(self._clips),
            cur_position=self._cur_position,
            direction_up=direction_up,
        )
        if self._cur_position:
            self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        primary: list[Gtk.MenuItem] = []
        for clip in reversed(self._clips):
            menu_item = Gtk.MenuItem(label=clip.label)
            preview = self._previews.get(clip.key)
            if preview is None:
                preview = clip_preview(clip)
                if preview is not None:
                    self._previews[clip.key] = preview
            if preview is not None:
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                box.pack_start(Gtk.Image.new_from_pixbuf(preview), False, False, 0)
                label = Gtk.Label(label=clip.label)
                label.set_xalign(0)
                box.pack_start(label, True, True, 0)
                child = menu_item.get_child()
                if child is not None:
                    menu_item.remove(child)
                menu_item.add(box)
            # Retain only the key: open menus must not pin evicted image payloads.
            menu_item.connect("activate", self._copy_retained_clip, clip.key)
            primary.append(menu_item)

        enabled = Gtk.CheckMenuItem(label=_("Enable Clipboard Tracking"))
        enabled.set_active(self._tracking_enabled)
        enabled.connect("toggled", self._on_tracking_toggled)
        selections = Gtk.CheckMenuItem(label=_("Track Mouse Selections"))
        selections.set_active(self._track_selections)
        selections.set_sensitive(self._primary_available)
        if not self._primary_available:
            selections.set_tooltip_text(_("Mouse selection tracking requires X11"))
        selections.connect("toggled", self._on_selections_toggled)
        images = Gtk.CheckMenuItem(label=_("Track Images"))
        images.set_active(self._track_images)
        images.connect("toggled", self._on_images_toggled)
        limits = sorted({0, 5, 10, 15, 20, 25, self._max_entries})
        maximum = radio_submenu(
            label=_("Maximum Entries"),
            choices=((_("Disabled") if n == 0 else str(n), n) for n in limits),
            active_value=self._max_entries,
            on_selected=lambda _widget, value: self._set_max_entries(value),
            gtk=Gtk,
        )
        destructive: list[Gtk.MenuItem] = []
        if self._clips:
            clear = Gtk.MenuItem(label=_("Clear"))
            clear.connect("activate", lambda _: self._clear())
            destructive.append(clear)

        return menu_sections(
            primary=primary,
            display=[enabled, selections, images, maximum],
            destructive=destructive,
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        if not self._stopped:
            return
        self._stopped = False
        self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        display = Gdk.Display.get_default()
        self._primary_available = bool(
            display
            and is_x11_backend(display=display)
            and display.supports_selection_notification()
        )
        self._selections = [_Selection(self._clipboard)]
        if self._primary_available:
            self._selections.append(
                _Selection(Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY), primary=True)
            )
        for selection in self._selections:
            selection.handler_id = selection.clipboard.connect(
                "owner-change", self._on_owner_change, selection
            )

    def stop(self) -> None:
        self._stopped = True
        self._invalidate_requests()
        for selection in self._selections:
            selection.clipboard.disconnect(selection.handler_id)
        self._selections.clear()
        self._clipboard = None
        self._primary_available = False
        self._clips.clear()
        self._previews.clear()
        self._cur_position = 0
        super().stop()

    def _eligible(self, selection: _Selection, generation: int) -> bool:
        return (
            not self._stopped
            and selection in self._selections
            and selection.generation == generation
            and self._tracking_enabled
            and self._max_entries > 0
            and (not selection.primary or self._track_selections)
        )

    def _on_owner_change(
        self, _clipboard: Gtk.Clipboard, _event: object, selection: _Selection
    ) -> None:
        self._cancel_request(selection)
        if self._eligible(selection, selection.generation):
            selection.timer_id = GLib.timeout_add(
                DEBOUNCE_MS, self._request_targets, selection, selection.generation
            )

    def _request_targets(self, selection: _Selection, generation: int) -> bool:
        if selection.generation != generation:
            return False
        selection.timer_id = 0
        if self._eligible(selection, generation):
            selection.clipboard.request_targets(
                self._on_targets, (selection, generation)
            )
        return False

    def _on_targets(
        self,
        clipboard: Gtk.Clipboard,
        targets: Sequence[Gdk.Atom] | None,
        _count: int,
        request: tuple[_Selection, int],
    ) -> None:
        if not self._eligible(*request) or not targets:
            return
        if any(target.name() == PASSWORD_MANAGER_HINT for target in targets):
            return
        if self._track_images and Gtk.targets_include_image(targets, False):
            clipboard.request_image(self._on_image, request)
        elif Gtk.targets_include_text(targets):
            clipboard.request_text(self._on_text, request)

    def _on_image(
        self,
        clipboard: Gtk.Clipboard,
        image: GdkPixbuf.Pixbuf | None,
        request: tuple[_Selection, int],
    ) -> None:
        if not self._eligible(*request):
            return
        if (
            image is not None
            and image.get_bits_per_sample() == 8
            and image.get_colorspace() == GdkPixbuf.Colorspace.RGB
            and image.get_byte_length() <= MAX_IMAGE_BYTES
        ):
            try:
                clip = Clip.from_image(
                    pixels=image.get_pixels(),
                    width=image.get_width(),
                    height=image.get_height(),
                    rowstride=image.get_rowstride(),
                    alpha=image.get_has_alpha(),
                )
            except ValueError:
                pass
            else:
                self._retain_clip(clip)
                return
        clipboard.request_text(self._on_text, request)

    def _on_text(
        self,
        _clipboard: Gtk.Clipboard,
        text: str | None,
        request: tuple[_Selection, int],
    ) -> None:
        if self._eligible(*request) and text:
            self.add_clip(text)

    def add_clip(self, text: str) -> None:
        self._retain_clip(Clip(text=text))

    def _retain_clip(self, clip: Clip) -> None:
        self._clips, self._cur_position = add_clip(
            clips=self._clips,
            clip=clip,
            max_entries=self._max_entries,
        )
        self._prune_previews()
        self.present()

    def _copy_to_clipboard(self, clip: Clip) -> None:
        if self._clipboard is not None:
            if clip.pixels:
                self._clipboard.set_image(clip_pixbuf(clip))
            else:
                self._clipboard.set_text(clip.text, -1)
            self._clipboard.store()

    def _copy_retained_clip(self, _widget: Gtk.MenuItem, key: ClipKey) -> None:
        for clip in self._clips:
            if clip.key == key:
                self._copy_to_clipboard(clip)
                break

    def _prune_previews(self) -> None:
        retained = {clip.key for clip in self._clips}
        self._previews = {
            key: preview for key, preview in self._previews.items() if key in retained
        }

    def _cancel_request(self, selection: _Selection) -> None:
        selection.generation += 1
        if selection.timer_id:
            GLib.source_remove(selection.timer_id)
            selection.timer_id = 0

    def _invalidate_requests(self) -> None:
        for selection in self._selections:
            self._cancel_request(selection)

    def _save_prefs(self) -> None:
        self._invalidate_requests()
        self.save_prefs(
            {
                "max_entries": self._max_entries,
                "tracking_enabled": self._tracking_enabled,
                "track_selections": self._track_selections,
                "track_images": self._track_images,
            }
        )
        self.present()

    def _on_tracking_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._tracking_enabled = widget.get_active()
        self._save_prefs()

    def _on_selections_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._track_selections = widget.get_active()
        self._save_prefs()

    def _on_images_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._track_images = widget.get_active()
        self._save_prefs()

    def _set_max_entries(self, value: int) -> None:
        self._max_entries = normalize_max_entries(value)
        selected = (
            self._clips[self._cur_position - 1]
            if 0 < self._cur_position <= len(self._clips)
            else None
        )
        self._clips = trim_clips(self._clips, self._max_entries)
        self._cur_position = (
            self._clips.index(selected) + 1
            if selected is not None and selected in self._clips
            else len(self._clips)
        )
        self._prune_previews()
        self._save_prefs()

    def _clear(self) -> None:
        self._invalidate_requests()
        self._clips.clear()
        self._previews.clear()
        self._cur_position = 0
        self.present()
