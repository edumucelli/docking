"""GTK dialog lifecycle for the URL Shortener applet.

How this applet is structured

Unlike the calculator and converter, the shortener uses a ``Gtk.Dialog`` rather
than a custom popup window. That choice fits the interaction model better: the
user pastes a URL, triggers one network request, optionally copies the result,
and closes the tool.

This module owns the interaction shell around that flow:

- show and dismiss the dialog,
- gather URL input,
- launch the shortening request off the main thread,
- reveal result/copy controls only when they are useful,
- persist the last submitted URL for convenience.

Why the network call is not in the UI thread

The is.gd request is small but still unbounded relative to GTK responsiveness.
Spawning a background thread keeps the dialog responsive while the request is in
flight. The pure request logic stays in ``state.py`` so the applet module only
has to worry about UI transitions and result presentation.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.urlshortener import meta
from docking.applets.urlshortener.render import create_icon
from docking.applets.urlshortener.state import prefs_payload, shorten_url
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12
DIALOG_VERTICAL_MARGIN_PX = 8
RESULT_LABEL_MAX_CHARS = 45
COPY_FEEDBACK_RESET_MS = 1500


class UrlShortenerApplet(Applet):
    """Shorten URLs via is.gd with one click."""

    id = meta.id
    name = _("URL Shortener")
    icon_name = "chain"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._dialog: Gtk.Dialog | None = None
        self._url_entry: Gtk.Entry | None = None
        self._result_label: Gtk.Label | None = None
        self._shorten_btn: Gtk.Button | None = None
        self._copy_btn: Gtk.Button | None = None
        self._last_result: str = ""

        prefs = config.applet_prefs.get("urlshortener", {}) if config else {}
        self._last_url = str(prefs.get("last_url", ""))

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = _("URL Shortener")

    def on_clicked(self) -> None:
        if self._dialog and self._dialog.get_visible():
            self._dialog.hide()
            return
        self._show_dialog()

    def stop(self) -> None:
        if self._dialog:
            self._dialog.destroy()
            self._dialog = None
        super().stop()

    # -- Dialog ---------------------------------------------------------------

    def _show_dialog(self) -> None:
        self._dialog = Gtk.Dialog(
            title=_("URL Shortener"),
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        self._dialog.set_default_size(DIALOG_WIDTH_PX, -1)
        self._dialog.set_position(Gtk.WindowPosition.MOUSE)
        self._dialog.set_resizable(False)

        box = self._dialog.get_content_area()
        box.set_spacing(DIALOG_CONTENT_SPACING_PX)
        box.set_margin_start(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_end(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_top(DIALOG_VERTICAL_MARGIN_PX)
        box.set_margin_bottom(DIALOG_VERTICAL_MARGIN_PX)

        # URL entry
        self._url_entry = Gtk.Entry()
        self._url_entry.set_placeholder_text("Paste URL here...")
        self._url_entry.set_text(self._last_url)
        self._url_entry.connect("activate", lambda _: self._do_shorten())
        box.pack_start(self._url_entry, False, False, 0)

        # Shorten button
        self._shorten_btn = Gtk.Button(label=_("Shorten"))
        self._shorten_btn.connect("clicked", lambda _: self._do_shorten())
        box.pack_start(self._shorten_btn, False, False, 0)

        # Result row (added dynamically after first shorten)
        self._result_box: Gtk.Box | None = None
        self._result_label = None
        self._copy_btn = None

        def on_response(_dlg: Gtk.Dialog, _response_id: int) -> None:
            self._dialog.destroy()
            self._dialog = None

        self._dialog.connect("response", on_response)
        self._dialog.show_all()
        self._url_entry.grab_focus()

    def _do_shorten(self) -> None:
        if not self._url_entry or not self._shorten_btn:
            return
        url = self._url_entry.get_text().strip()
        if not url:
            return

        self._shorten_btn.set_sensitive(False)
        if self._result_label:
            self._result_label.set_text(_("Shortening..."))

        def _worker() -> None:
            result = shorten_url(url=url)
            GLib.idle_add(self._on_result, url, result)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_result(self, url: str, result: str) -> bool:
        if self._shorten_btn:
            self._shorten_btn.set_sensitive(True)
        is_error = result.startswith("Error")
        self._last_result = "" if is_error else result
        self._ensure_result_row()
        if self._result_label:
            self._result_label.set_text(result)
        if self._copy_btn:
            self._copy_btn.set_visible(not is_error)
        if not is_error:
            self._last_url = url
            self._save_prefs()
        return False

    def _ensure_result_row(self) -> None:
        if self._result_box or not self._dialog:
            return
        box = self._dialog.get_content_area()

        self._result_label = Gtk.Label(label="")
        self._result_label.set_selectable(True)
        self._result_label.set_line_wrap(True)
        self._result_label.set_max_width_chars(RESULT_LABEL_MAX_CHARS)
        box.pack_start(self._result_label, False, False, 0)

        self._copy_btn = Gtk.Button(label=_("Copy"))
        self._copy_btn.connect("clicked", lambda _: self._do_copy())
        box.pack_start(self._copy_btn, False, False, 0)

        self._result_box = box
        box.show_all()

    def _do_copy(self) -> None:
        if not self._last_result:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self._last_result, -1)
        clipboard.store()
        if self._copy_btn:
            self._copy_btn.set_label(_("Copied!"))
            GLib.timeout_add(COPY_FEEDBACK_RESET_MS, self._reset_copy_label)

    def _reset_copy_label(self) -> bool:
        if self._copy_btn:
            self._copy_btn.set_label(_("Copy"))
        return False

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_payload(last_url=self._last_url))
