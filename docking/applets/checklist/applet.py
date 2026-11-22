"""A local, reusable checklist in a dock-anchored popup."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Pango

from docking.applets.base import Applet
from docking.applets.checklist import meta
from docking.applets.checklist.render import render_icon
from docking.applets.checklist.state import (
    ChecklistItem,
    items_from_prefs,
    items_to_prefs,
)
from docking.applets.popup import create_popup_window, show_wrapped_popup
from docking.i18n import _, ngettext

if TYPE_CHECKING:
    from docking.core.config import Config


class ChecklistApplet(Applet):
    id = meta.id
    name = _("Checklist")
    icon_name = "view-list-symbolic"

    def __init__(self, icon_size: int, config: Config) -> None:
        super().__init__(icon_size=icon_size, config=config)
        self._items = items_from_prefs(self.load_prefs().get("items"))
        self._popup: Gtk.Window | None = None
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, unchecked=self._unchecked_count())

    def refresh_tooltip(self) -> None:
        count = self._unchecked_count()
        status = ngettext("{count} unchecked item", "{count} unchecked items", count)
        self.item.name = f"{self.name}\n{status.format(count=count)}"

    def _unchecked_count(self) -> int:
        return sum(not item.checked for item in self._items)

    @property
    def popup_visible(self) -> bool:
        return self._popup is not None and self._popup.get_visible()

    def on_clicked(self) -> None:
        if self._popup is not None and self._popup.get_visible():
            self._popup.hide()
            return
        if self._popup is None:
            self._popup = create_popup_window(managed=True)
            self._popup.set_title(self.name)
            self._popup.connect("key-press-event", self._on_popup_key)
            self._popup.connect("destroy", self._on_popup_destroy)
        content, entry = self._build_popup()
        show_wrapped_popup(
            window=self._popup, content=content, anchor=self.popup_anchor
        )
        entry.grab_focus()
        self._popup.present()

    def stop(self) -> None:
        if self._popup is not None:
            self._popup.destroy()
        super().stop()

    @staticmethod
    def _on_popup_key(window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            window.hide()
            return True
        return False

    def _on_popup_destroy(self, _window: Gtk.Window) -> None:
        self._popup = None

    def _build_popup(self) -> tuple[Gtk.Box, Gtk.Entry]:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_border_width(12)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(280)
        scroll.set_min_content_height(160)
        scroll.add(rows)
        content.pack_start(scroll, True, True, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text(_("Add an item"))
        entry.get_accessible().set_name(_("Add an item"))
        add = Gtk.Button(label=_("Add"))
        input_row = Gtk.Box(spacing=6)
        input_row.pack_start(entry, True, True, 0)
        input_row.pack_start(add, False, False, 0)
        content.pack_start(input_row, False, False, 0)

        clear = Gtk.Button(label=_("Clear Completed"))
        clear.set_halign(Gtk.Align.END)
        clear.set_sensitive(any(item.checked for item in self._items))
        clear.connect("clicked", self._clear_completed, rows)
        content.pack_start(clear, False, False, 0)

        entry.connect("activate", self._add_item, rows, clear)
        add.connect("clicked", lambda _button: entry.activate())
        for item in self._items:
            self._append_row(rows, item, clear)
        return content, entry

    def _append_row(
        self, rows: Gtk.Box, item: ChecklistItem, clear: Gtk.Button
    ) -> None:
        check = Gtk.CheckButton(label=item.text, active=item.checked)
        label = check.get_child()
        if isinstance(label, Gtk.Label):
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(32)
        check.connect("toggled", self._toggle_item, item, clear)
        rows.pack_start(check, False, False, 0)
        check.show_all()

    def _add_item(self, entry: Gtk.Entry, rows: Gtk.Box, clear: Gtk.Button) -> None:
        text = entry.get_text().strip()
        if not text:
            return
        item = ChecklistItem(text)
        self._items.append(item)
        self._append_row(rows, item, clear)
        entry.set_text("")
        entry.grab_focus()
        self._save_and_present()

    def _toggle_item(
        self, check: Gtk.CheckButton, item: ChecklistItem, clear: Gtk.Button
    ) -> None:
        item.checked = check.get_active()
        clear.set_sensitive(any(item.checked for item in self._items))
        self._save_and_present()

    def _clear_completed(self, clear: Gtk.Button, rows: Gtk.Box) -> None:
        for row, item in zip(rows.get_children(), self._items, strict=True):
            if item.checked:
                row.destroy()
        self._items = [item for item in self._items if not item.checked]
        clear.set_sensitive(False)
        self._save_and_present()

    def _save_and_present(self) -> None:
        self.save_prefs({"items": items_to_prefs(self._items)})
        self.present()
