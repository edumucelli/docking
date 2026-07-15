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

"""GTK lifecycle glue for quick note applet."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.menu import menu_sections
from docking.applets.popup import prepare_dialog_content
from docking.applets.quicknote import meta
from docking.applets.quicknote.render import render_icon
from docking.applets.quicknote.state import (
    note_from_prefs,
    prefs_from_note,
    tooltip_text,
)
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="quicknote"), applet_id=meta.id)

EDIT_DIALOG_WIDTH_PX = 350
EDIT_DIALOG_HEIGHT_PX = 250
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12


class QuickNoteApplet(Applet):
    """Sticky note applet -- click to edit, tooltip shows content preview."""

    id = meta.id
    name = _("Quick Note")
    icon_name = "text-editor"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = config.applet_prefs.get("quicknote", {}) if config else None
        self._note = note_from_prefs(prefs)
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, has_content=bool(self._note.strip()))

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(note=self._note)

    def on_clicked(self) -> None:
        self._show_edit_dialog()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        edit = Gtk.MenuItem(label=_("Edit Note"))
        edit.connect("activate", lambda _: self._show_edit_dialog())

        clear = Gtk.MenuItem(label=_("Clear Note"))
        clear.connect("activate", lambda _: self._clear_note())

        return menu_sections(manage=[edit], destructive=[clear], gtk=Gtk)

    def _show_edit_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Quick Note"),
            modal=True,
            destroy_with_parent=True,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("OK"), Gtk.ResponseType.OK)
        box = prepare_dialog_content(
            dialog=dialog,
            width=EDIT_DIALOG_WIDTH_PX,
            height=EDIT_DIALOG_HEIGHT_PX,
            spacing=DIALOG_CONTENT_SPACING_PX,
            margin=DIALOG_HORIZONTAL_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
        )

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        text_view = Gtk.TextView()
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.get_buffer().set_text(self._note)
        scroll.add(text_view)
        box.pack_start(scroll, True, True, 0)

        def on_response(_dlg: Gtk.Dialog, _response_id: int) -> None:
            if _response_id == Gtk.ResponseType.OK:
                buf = text_view.get_buffer()
                start, end = buf.get_bounds()
                self._note = buf.get_text(start, end, include_hidden_chars=True)
                self._save()
                self.present()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()
        text_view.grab_focus()

    def _clear_note(self) -> None:
        self._note = ""
        self._save()
        self.present()

    def _save(self) -> None:
        self.save_prefs(prefs=prefs_from_note(note=self._note))
