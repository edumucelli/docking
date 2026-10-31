"""Own asynchronous artwork bindings for one Applications menu lifetime."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk

from docking.platform.applications.types import ApplicationListing

from .render import MENU_ICON_PX

if TYPE_CHECKING:
    from docking.platform.application_icons import ApplicationIcons


class MenuIcons:
    def __init__(self, source: ApplicationIcons) -> None:
        self._source = source
        self._rows: dict[Gtk.MenuItem, tuple[ApplicationListing, Gtk.Image]] = {}
        self._closed = False
        self._unsubscribe = source.subscribe(self._changed)

    def bind(
        self, row: Gtk.MenuItem, application: ApplicationListing, image: Gtk.Image
    ) -> None:
        if self._closed:
            return
        self._rows[row] = application, image
        self._update(application, image)

    def forget(self, row: Gtk.MenuItem) -> None:
        self._rows.pop(row, None)

    def drag_icon(self, row: Gtk.MenuItem) -> GdkPixbuf.Pixbuf | None:
        binding = self._rows.get(row)
        if binding is None or self._closed:
            return None
        image = binding[1].get_pixbuf()
        if image is None:
            return None
        factor = 32 / max(image.get_width(), image.get_height())
        return image.scale_simple(
            max(1, round(image.get_width() * factor)),
            max(1, round(image.get_height() * factor)),
            GdkPixbuf.InterpType.BILINEAR,
        )

    def _update(self, application: ApplicationListing, image: Gtk.Image) -> None:
        pixbuf = self._source.load_listing(application, MENU_ICON_PX)
        if pixbuf is not None:
            image.set_from_pixbuf(pixbuf)

    def _changed(self, desktop_id: str | None) -> None:
        if self._closed:
            return
        for application, image in tuple(self._rows.values()):
            if desktop_id is None or application.desktop_id == desktop_id:
                self._update(application, image)

    def stop(self, *_args: object) -> None:
        if not self._closed:
            self._closed = True
            self._unsubscribe()
            self._rows.clear()
