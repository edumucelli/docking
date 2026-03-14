"""Quote applet behavior and GTK wiring."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import draw_bulb_icon
from .state import (
    DEFAULT_SOURCE,
    SOURCE_LABELS,
    QuoteEntry,
    fetch_quotes,
    format_quote,
    source_fallback,
)

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="quote"), applet_id=str(AppletId.QUOTE))


class QuoteApplet(Applet):
    """Quote of the day style applet inspired by the legacy Cairo-Dock plugin."""

    id = AppletId.QUOTE
    name = _("Quote")
    icon_name = "idea"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._source = DEFAULT_SOURCE
        self._quotes: list[QuoteEntry] = []
        self._index = -1
        self._current: QuoteEntry | None = None
        self._loading = False
        self._clipboard: Gtk.Clipboard | None = None

        if config:
            prefs = config.applet_prefs.get("quote", {})
            source = prefs.get("source", DEFAULT_SOURCE)
            if source in SOURCE_LABELS:
                self._source = source

        super().__init__(icon_size, config)
        self._quotes = source_fallback(source=self._source)
        self._advance_quote()
        self.refresh_tooltip()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        draw_bulb_icon(cr=cr, size=size)
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        if self._advance_quote():
            self.refresh_presentation()
            # Fetch next batch in background when we reach the end.
            if self._index >= len(self._quotes) - 1:
                self._fetch_async(show_first=False)
            return
        self._current = None
        self._fetch_async(show_first=True)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        source_header = Gtk.MenuItem(label=SOURCE_LABELS.get(self._source, _("Quote")))
        source_header.set_sensitive(False)
        items.append(source_header)

        next_item = Gtk.MenuItem(label=_("Next Quote"))
        next_item.connect("activate", lambda _: self.on_clicked())
        items.append(next_item)

        copy_item = Gtk.MenuItem(label=_("Copy Quote"))
        copy_item.connect("activate", lambda _: self._copy_current_quote())
        items.append(copy_item)

        refresh_item = Gtk.MenuItem(label=_("Refresh from Web"))
        refresh_item.connect("activate", lambda _: self._refresh_from_web())
        items.append(refresh_item)

        items.append(Gtk.SeparatorMenuItem())

        source_title = Gtk.MenuItem(label=_("Source"))
        source_title.set_sensitive(False)
        items.append(source_title)

        group: Gtk.RadioMenuItem | None = None
        for source_id, label in SOURCE_LABELS.items():
            radio = Gtk.RadioMenuItem(label=label)
            if group:
                radio.join_group(group)
            else:
                group = radio
            radio.set_active(source_id == self._source)
            radio.connect("toggled", self._on_source_toggled, source_id)
            items.append(radio)

        return items

    def _on_source_toggled(self, widget: Gtk.RadioMenuItem, source_id: str) -> None:
        if not widget.get_active():
            return
        self._set_source(source_id=source_id)

    def _set_source(self, source_id: str) -> None:
        if source_id not in SOURCE_LABELS or source_id == self._source:
            return
        self._source = source_id
        self.save_prefs({"source": self._source})
        self._quotes = source_fallback(source=self._source)
        self._index = -1
        self._current = None
        self._advance_quote()
        self.refresh_presentation()
        self._fetch_async(show_first=False)

    def _refresh_from_web(self) -> None:
        self._fetch_async(show_first=True)

    def _copy_current_quote(self) -> None:
        if not self._current:
            return
        text = format_quote(self._current)
        try:
            if self._clipboard is None:
                self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            self._clipboard.set_text(text, -1)
            self._clipboard.store()
        except Exception as exc:
            _log.bind(action="copy_quote").warning(
                "Failed to copy quote to clipboard: %s",
                exc,
            )

    def _fetch_async(self, show_first: bool) -> None:
        if self._loading:
            return
        self._loading = True
        if show_first:
            self.refresh_presentation()

        source = self._source

        def worker() -> None:
            quotes = fetch_quotes(source=source, limit=20)
            GLib.idle_add(self._on_fetch_result, source, quotes, show_first)

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_result(
        self,
        source_id: str,
        quotes: list[QuoteEntry],
        show_first: bool,
    ) -> bool:
        self._loading = False
        # Ignore stale async results if source changed in the meantime.
        if source_id != self._source:
            return False

        if quotes:
            self._quotes = quotes
            self._index = -1
            if show_first:
                self._current = None
                self._advance_quote()
        elif self._current is None:
            # Always recover to local fallback.
            self._quotes = source_fallback(source=self._source)
            self._index = -1
            self._advance_quote()

        self.refresh_tooltip()
        self.refresh_presentation()
        return False

    def _advance_quote(self) -> bool:
        nxt = self._index + 1
        if nxt >= len(self._quotes):
            return False
        self._index = nxt
        self._current = self._quotes[nxt]
        self.refresh_tooltip()
        return True

    def refresh_tooltip(self) -> None:
        if self._loading and self._current is None:
            self.item.name = _("{source}: loading...").format(
                source=SOURCE_LABELS.get(self._source, _("Quote"))
            )
            return
        if self._current:
            self.item.name = format_quote(self._current)
            return
        self.item.name = _("Quote")
