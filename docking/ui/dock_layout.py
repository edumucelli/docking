"""Preferences dialog for editing a single draft of the dock's pins."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, Gio, Gtk, Pango

from docking.applets import get_applet_catalog
from docking.applets.identity import (
    applet_desktop_id,
    next_applet_instance_id,
    parse_applet_id,
)
from docking.applets.separator import meta as separator
from docking.core.config import PinnedEntry
from docking.core.items import (
    APP_KIND,
    APPLET_KIND,
    FILE_KIND,
    FOLDER_KIND,
    pinned_section,
)
from docking.i18n import _
from docking.platform.applications.types import ApplicationOrigin
from docking.platform.model import LayoutConflictError
from docking.platform.targets import normalize_file_target

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


class _LayoutDraft:
    def __init__(self, config: Config) -> None:
        self.original = tuple(replace(entry) for entry in config.pinned)
        self.entries = list(self.original)
        self.anchors = (config.anchor_files, config.anchor_applets)

    def section(self, entry: PinnedEntry) -> int:
        files, applets = self.anchors
        return pinned_section(entry.kind, anchor_files=files, anchor_applets=applets)

    def display_order(self) -> list[int]:
        return sorted(
            range(len(self.entries)),
            key=lambda index: self.section(self.entries[index]),
        )

    def neighbor(self, index: int, offset: int) -> int | None:
        order = self.display_order()
        position = order.index(index) + offset
        if 0 <= position < len(order):
            other = order[position]
            if self.section(self.entries[other]) == self.section(self.entries[index]):
                return other
        return None

    def add(self, entry: PinnedEntry, selected: int | None) -> int | None:
        if any(existing.id == entry.id for existing in self.entries):
            return None
        section = self.section(entry)
        if selected is not None and self.section(self.entries[selected]) == section:
            insert_at = selected + 1
        else:
            insert_at = next(
                (
                    index + 1
                    for index in range(len(self.entries) - 1, -1, -1)
                    if self.section(self.entries[index]) == section
                ),
                len(self.entries),
            )
        self.entries.insert(insert_at, entry)
        return insert_at


class DockLayoutEditor:
    def __init__(
        self,
        parent: Gtk.Window,
        config: Config,
        model: DockModel,
        registry: ApplicationRegistry,
    ) -> None:
        self._config = config
        self._model = model
        self._registry = registry
        self._draft = _LayoutDraft(config)
        self._display_order: list[int] = []
        self._conflict = False
        self._dialog = Gtk.Dialog(
            title=_("Edit Dock Layout"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self._dialog.set_default_size(520, 400)
        self._dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self._dialog.add_button(_("Apply"), Gtk.ResponseType.APPLY)

        content = self._dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(8)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._list.set_placeholder(Gtk.Label(label=_("No pinned items")))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        content.pack_start(scroller, True, True, 0)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        content.pack_start(toolbar, False, False, 0)
        add = Gtk.MenuButton(label=_("Add"))
        menu = Gtk.Menu()
        for label, callback in (
            (_("Application…"), lambda: self._add_catalog(APP_KIND)),
            (_("Applet…"), lambda: self._add_catalog(APPLET_KIND)),
            (_("File…"), lambda: self._add_file(folder=False)),
            (_("Folder…"), lambda: self._add_file(folder=True)),
            (_("Separator"), self._add_separator),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, action=callback: action())
            menu.append(item)
        menu.show_all()
        add.set_popup(menu)
        toolbar.pack_start(add, False, False, 0)
        self._remove = Gtk.Button(label=_("Remove"))
        self._up = Gtk.Button(label=_("Move Up"))
        self._down = Gtk.Button(label=_("Move Down"))
        self._remove.connect("clicked", self._remove_selected)
        self._up.connect("clicked", self._move_selected, -1)
        self._down.connect("clicked", self._move_selected, 1)
        for button in (self._remove, self._up, self._down):
            toolbar.pack_start(button, False, False, 0)
        self._selection_handler = self._list.connect(
            "row-selected", self._update_controls
        )

        self._status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self._status.set_no_show_all(True)
        content.pack_start(self._status, False, False, 0)
        self._reload = Gtk.Button(label=_("Reload Layout"), halign=Gtk.Align.START)
        self._reload.set_no_show_all(True)
        self._reload.connect("clicked", self._reload_layout)
        content.pack_start(self._reload, False, False, 0)
        self._render()

    def run(self) -> bool:
        """Return whether the saved layout changed during this dialog session."""
        applied = False
        self._dialog.show_all()
        try:
            while self._dialog.run() == Gtk.ResponseType.APPLY:
                try:
                    failed = self._model.apply_pinned_layout(
                        self._draft.entries,
                        expected_pins=self._draft.original,
                        expected_anchors=self._draft.anchors,
                    )
                except LayoutConflictError:
                    self._conflict = True
                    self._model.clear_pinned_order_preview()
                    self._reload.show()
                    self._show_status(
                        _("The dock layout changed while you were editing it.")
                    )
                    self._update_controls()
                    continue
                except ValueError as exc:
                    self._show_status(str(exc))
                    continue
                except OSError:
                    self._show_status(_("Could not save the dock layout."))
                    continue
                applied = True
                if not failed:
                    break
                self._draft = _LayoutDraft(self._config)
                self._render()
                self._show_status(
                    _("Layout saved. Some new items could not be loaded.")
                )
        finally:
            self._model.clear_pinned_order_preview()
            self._dialog.destroy()
        return applied

    def _reload_layout(self, _button: Gtk.Button) -> None:
        self._draft = _LayoutDraft(self._config)
        self._conflict = False
        self._reload.hide()
        self._status.hide()
        self._render()

    def _show_status(self, text: str) -> None:
        self._status.set_text(text)
        self._status.show()

    def _selected(self) -> int | None:
        row = self._list.get_selected_row()
        return self._display_order[row.get_index()] if row is not None else None

    def _update_controls(self, *_args: object) -> None:
        selected = self._selected()
        self._remove.set_sensitive(selected is not None)
        self._up.set_sensitive(
            selected is not None and self._draft.neighbor(selected, -1) is not None
        )
        self._down.set_sensitive(
            selected is not None and self._draft.neighbor(selected, 1) is not None
        )
        self._dialog.set_response_sensitive(
            Gtk.ResponseType.APPLY,
            not self._conflict and tuple(self._draft.entries) != self._draft.original,
        )

    def _render(self, selected: int | None = None) -> None:
        with self._list.handler_block(self._selection_handler):
            for row in self._list.get_children():
                self._list.remove(row)
        self._display_order = self._draft.display_order()
        items = {
            (item.kind, item.desktop_id): item for item in self._model.pinned_items
        }
        section = 0
        for index in self._display_order:
            entry = self._draft.entries[index]
            item = items.get((entry.kind, entry.id))
            name, icon_name = self._describe(entry)
            if (
                entry.kind != APPLET_KIND
                and item is not None
                and item.name
                and item.name != entry.target
            ):
                name = item.name
            unavailable = entry in self._draft.original and (
                item is None
                or (
                    entry.kind in {FILE_KIND, FOLDER_KIND} and item.name == entry.target
                )
            )
            row = Gtk.ListBoxRow()
            group = self._draft.section(entry)
            if group != section:
                header = Gtk.Label(
                    label=_("Anchored Files and Folders")
                    if group == 1
                    else _("Anchored Applets"),
                    xalign=0,
                    margin_top=8,
                    margin_bottom=4,
                )
                header.show()
                row.set_header(header)
                section = group
            box = Gtk.Box(
                spacing=8, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4
            )
            image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
            if item is not None and isinstance(item.icon, GdkPixbuf.Pixbuf):
                width, height = item.icon.get_width(), item.icon.get_height()
                scale = min(1.0, 24 / max(width, height))
                image.set_from_pixbuf(
                    item.icon.scale_simple(
                        max(1, round(width * scale)),
                        max(1, round(height * scale)),
                        GdkPixbuf.InterpType.BILINEAR,
                    )
                )
            box.pack_start(image, False, False, 0)
            label = Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            box.pack_start(label, True, True, 0)
            if unavailable:
                box.pack_end(Gtk.Label(label=_("Unavailable")), False, False, 0)
            if entry.kind != APPLET_KIND:
                row.set_tooltip_text(entry.target)
            row.add(box)
            self._list.add(row)
        self._list.show_all()
        if self._display_order:
            position = (
                self._display_order.index(selected) if selected is not None else 0
            )
            self._list.select_row(self._list.get_row_at_index(position))
        self._update_controls()
        if not self._conflict:
            self._model.preview_pinned_order(
                self._draft.entries,
                expected_pins=self._draft.original,
                expected_anchors=self._draft.anchors,
            )

    def _describe(self, entry: PinnedEntry) -> tuple[str, str]:
        if entry.kind == APP_KIND:
            info = self._registry.get(entry.target)
            return (info.name if info else entry.target, "application-x-executable")
        if entry.kind == APPLET_KIND:
            applet_id = parse_applet_id(entry.target)
            meta = get_applet_catalog().get(applet_id or "")
            name = _(meta.name) if meta else (applet_id or entry.target)
            icon = "list-remove" if applet_id == separator.id else "application-x-addon"
            return name, icon
        return (
            Gio.File.new_for_uri(entry.target).get_basename() or entry.target,
            "folder" if entry.kind == FOLDER_KIND else "text-x-generic",
        )

    def _remove_selected(self, _button: Gtk.Button) -> None:
        selected = self._selected()
        if selected is not None:
            self._draft.entries.pop(selected)
            remaining = min(selected, len(self._draft.entries) - 1)
            self._render(remaining if remaining >= 0 else None)

    def _move_selected(self, _button: Gtk.Button, offset: int) -> None:
        selected = self._selected()
        if (
            selected is not None
            and (other := self._draft.neighbor(selected, offset)) is not None
        ):
            entries = self._draft.entries
            entries[selected], entries[other] = entries[other], entries[selected]
            self._render(other)

    def _add(self, entry: PinnedEntry) -> None:
        index = self._draft.add(entry, self._selected())
        if index is None:
            self._show_status(_("This item is already pinned."))
            return
        if not self._conflict:
            self._status.hide()
        self._render(index)

    def _add_separator(self) -> None:
        target = next_applet_instance_id(
            separator.id,
            (entry.id for entry in (*self._draft.original, *self._draft.entries)),
        )
        self._add(PinnedEntry(APPLET_KIND, target))

    def _add_file(self, *, folder: bool) -> None:
        chooser = Gtk.FileChooserNative(
            title=_("Choose a Folder") if folder else _("Choose a File"),
            transient_for=self._dialog,
            action=Gtk.FileChooserAction.SELECT_FOLDER
            if folder
            else Gtk.FileChooserAction.OPEN,
            accept_label=_("Add"),
            cancel_label=_("Cancel"),
        )
        chooser.set_modal(True)
        chooser.set_local_only(True)
        try:
            if chooser.run() != Gtk.ResponseType.ACCEPT:
                return
            filename = chooser.get_filename()
            target = normalize_file_target(filename) if filename else None
        finally:
            chooser.destroy()
        if target is None:
            self._show_status(_("Choose a local file or folder."))
            return
        self._add(PinnedEntry(FOLDER_KIND if folder else FILE_KIND, target))

    def _add_catalog(self, kind: str) -> None:
        applications = kind == APP_KIND
        title = _("Add Application") if applications else _("Add Applet")
        dialog = Gtk.Dialog(
            title=title,
            transient_for=self._dialog,
            modal=True,
            destroy_with_parent=True,
        )
        dialog.set_default_size(400, 320)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Add"), Gtk.ResponseType.ACCEPT)
        store = Gtk.ListStore(str, str)
        view = Gtk.TreeView(model=store, headers_visible=False, search_column=0)
        renderer = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        column = Gtk.TreeViewColumn(title, renderer)
        column.add_attribute(renderer, "text", 0)
        view.append_column(column)
        selection = view.get_selection()
        selection.connect(
            "changed",
            lambda selected: dialog.set_response_sensitive(
                Gtk.ResponseType.ACCEPT,
                selected.get_selected()[1] is not None,
            ),
        )
        view.connect(
            "row-activated", lambda *_args: dialog.response(Gtk.ResponseType.ACCEPT)
        )
        scroller = Gtk.ScrolledWindow()
        scroller.add(view)
        dialog.get_content_area().pack_start(scroller, True, True, 0)
        occupied = {entry.id for entry in self._draft.entries}

        def populate() -> None:
            if applications:
                choices = [
                    (app.name, app.desktop_id)
                    for app in self._registry.snapshot()
                    if app.origin is not ApplicationOrigin.RUNTIME
                ]
            else:
                choices = [
                    (_(meta.name), applet_desktop_id(meta.id))
                    for meta in get_applet_catalog().values()
                    if meta.id != separator.id and meta.available_on_host
                ]
            store.clear()
            dialog.set_response_sensitive(Gtk.ResponseType.ACCEPT, False)
            for name, target in sorted(
                choices, key=lambda row: (row[0].casefold(), row[1])
            ):
                if target not in occupied:
                    store.append((name, target))

        populate()
        unsubscribe = self._registry.subscribe(populate) if applications else None
        try:
            dialog.show_all()
            view.grab_focus()
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                model, row = selection.get_selected()
                if row is not None:
                    self._add(
                        PinnedEntry(
                            APP_KIND if applications else APPLET_KIND, model[row][1]
                        )
                    )
        finally:
            if unsubscribe is not None:
                unsubscribe()
            dialog.destroy()
