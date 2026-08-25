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

"""Recent Files applet behavior and GTK wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import TargetServicesApplet
from docking.applets.menu import menu_sections
from docking.applets.recentfiles import meta
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.icons import IconLoader
from docking.platform.targets import TargetService
from docking.ui.stack import StackContent, StackEntry

from .render import render_icon
from .state import MAX_ENTRIES, RecentEntry, tooltip_text, truncate_name

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="recentfiles"), applet_id=meta.id)


class RecentFilesApplet(TargetServicesApplet):
    """Show recently opened files through the reusable item stack."""

    id = meta.id
    name = _("Recent Files")
    icon_name = "document-open-recent"

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        icon_loader: IconLoader,
        target_service: TargetService,
    ) -> None:
        self._entries: list[RecentEntry] = []
        self._signal_id: int | None = None
        self._refresh_entries()
        super().__init__(
            icon_size=icon_size,
            config=config,
            icon_loader=icon_loader,
            target_service=target_service,
        )
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, has_files=bool(self._entries))

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(entries=self._entries)

    def stack_content(self, icon_size: int) -> StackContent | None:
        """Return the current recent-file snapshot for the shared stack popup."""
        if not self._entries:
            return None
        return StackContent(
            entries=tuple(
                StackEntry(
                    key=entry.uri,
                    label=entry.name,
                    icon=self._stack_icon(entry=entry, size=icon_size),
                    activate=lambda uri=entry.uri: self._open_uri(uri=uri),
                )
                for entry in self._entries
            ),
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        manager = Gtk.RecentManager.get_default()
        self._signal_id = manager.connect("changed", self._on_changed)

    def stop(self) -> None:
        if self._signal_id is not None:
            manager = Gtk.RecentManager.get_default()
            manager.disconnect(self._signal_id)
            self._signal_id = None
        super().stop()

    def on_clicked(self) -> None:
        """Open the most recent file."""
        if not self._entries:
            return
        self._open_uri(uri=self._entries[0].uri)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        primary: list[Gtk.MenuItem] = []

        for entry in self._entries:
            mi = Gtk.MenuItem(label=truncate_name(text=entry.name))
            uri = entry.uri
            mi.connect("activate", lambda _, u=uri: self._open_uri(uri=u))
            primary.append(mi)

        clear_item = Gtk.MenuItem(label=_("Clear Recent Files"))
        clear_item.set_sensitive(bool(self._entries))
        clear_item.connect("activate", lambda _: self._clear_recent())

        return menu_sections(primary=primary, destructive=[clear_item], gtk=Gtk)

    def _refresh_entries(self) -> None:
        """Read from Gtk.RecentManager, sort by modified time descending."""
        manager = Gtk.RecentManager.get_default()
        raw_items = manager.get_items()
        existing = [it for it in raw_items if it.exists()]
        existing.sort(key=lambda it: it.get_modified(), reverse=True)
        self._entries = [
            RecentEntry(name=it.get_display_name(), uri=it.get_uri())
            for it in existing[:MAX_ENTRIES]
        ]

    def _on_changed(self, *_args: object) -> None:
        self._refresh_entries()
        self.present()

    def _open_uri(self, *, uri: str) -> None:
        self._target_service.open_target(uri)

    def _stack_icon(
        self,
        *,
        entry: RecentEntry,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        target = self._target_service.resolve_file(entry.uri, size)
        if target is not None and target.icon is not None:
            return target.icon
        return self._icon_loader.load_icon("text-x-generic", size)

    def _clear_recent(self) -> None:
        try:
            Gtk.RecentManager.get_default().purge_items()
        except GLib.Error as exc:
            log.bind(action="clear_recent").warning(
                "Failed to clear recent files: %s", exc
            )
