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

"""GTK dialog glue for the Run Application applet."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk

from docking.applets.base import ApplicationServicesApplet
from docking.applets.popup import prepare_dialog_content
from docking.applets.runcommand import meta
from docking.applets.runcommand.render import create_icon
from docking.applets.runcommand.state import (
    app_command_text,
    app_description,
    app_display_name,
    launch_application,
    match_application,
    normalize_history,
    prefs_payload,
    updated_history,
)
from docking.core.icons import IconSource
from docking.i18n import _
from docking.platform import commands
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.listing import (
    listing_gicon,
    visible_listings,
)
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import ApplicationListing

if TYPE_CHECKING:
    from docking.core.config import Config

RUN_DIALOG_WIDTH_PX = 575
RUN_DIALOG_HEIGHT_PX = 355
RUN_DIALOG_MARGIN_PX = 8
RUN_DIALOG_SPACING_PX = 6
LEFT_ICON_PX = 48
APP_LIST_HEIGHT_PX = 140
COMMAND_WIDTH_CHARS = 48


class _ApplicationRow(Gtk.ListBoxRow):
    """List row that retains the application represented by its child widgets."""

    def __init__(self, app: ApplicationListing) -> None:
        super().__init__()
        self.app = app


class RunCommandApplet(ApplicationServicesApplet):
    """Alt+F2-style command/application launcher."""

    id = meta.id
    name = _("Run Application")
    icon_name = "system-run"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        application_registry: ApplicationRegistry,
        application_launcher: ApplicationLauncher,
    ) -> None:
        prefs = config.applet_prefs.get(meta.id, {})
        self._history = normalize_history(prefs.get("history"))
        self._dialog: Gtk.Dialog | None = None
        self._entry_combo: Gtk.ComboBoxText | None = None
        self._entry: Gtk.Entry | None = None
        self._terminal_check: Gtk.CheckButton | None = None
        self._run_button: Gtk.Widget | None = None
        self._left_icon: Gtk.Image | None = None
        self._description_label: Gtk.Label | None = None
        self._apps: list[ApplicationListing] = []
        self._app_list: Gtk.ListBox | None = None
        self._app_rows: list[_ApplicationRow] = []
        self._selected_app: ApplicationListing | None = None
        self._selected_entry_text = ""
        super().__init__(
            icon_size=icon_size,
            config=config,
            application_registry=application_registry,
            application_launcher=application_launcher,
        )
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = _("Run Application")

    def on_clicked(self) -> None:
        if self._dialog and self._dialog.get_visible():
            self._dialog.present()
            return
        self._show_dialog()

    def stop(self) -> None:
        if self._dialog:
            self._dialog.destroy()
            self._dialog = None
        super().stop()

    # -- Dialog ---------------------------------------------------------------

    def _show_dialog(self) -> None:
        if self._dialog is None:
            self._dialog = self._create_dialog()
        self._refresh_app_list()
        self._sync_entry_history()
        self._sync_run_state()
        self._dialog.show_all()
        self._apply_app_filter(self._entry.get_text() if self._entry else "")
        self._dialog.present()
        if self._entry is not None:
            self._entry.grab_focus()

    def _create_dialog(self) -> Gtk.Dialog:
        dialog = Gtk.Dialog(
            title=_("Run Application"),
            destroy_with_parent=True,
        )
        dialog.add_button(_("Help"), Gtk.ResponseType.HELP)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Run"), Gtk.ResponseType.OK)
        dialog.connect("response", self._on_response)
        dialog.connect("delete-event", self._on_delete_event)
        self._run_button = dialog.get_widget_for_response(Gtk.ResponseType.OK)

        content = prepare_dialog_content(
            dialog=dialog,
            width=RUN_DIALOG_WIDTH_PX,
            height=RUN_DIALOG_HEIGHT_PX,
            spacing=RUN_DIALOG_SPACING_PX,
            margin=RUN_DIALOG_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
            resizable=False,
        )
        content.pack_start(self._build_dialog_content(), True, True, 0)
        return dialog

    def _build_dialog_content(self) -> Gtk.Box:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        outer.pack_start(top, False, False, 0)

        self._left_icon = Gtk.Image.new_from_icon_name(
            self.icon_name,
            Gtk.IconSize.DIALOG,
        )
        self._left_icon.set_pixel_size(LEFT_ICON_PX)
        self._left_icon.set_valign(Gtk.Align.START)
        top.pack_start(self._left_icon, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_hexpand(True)
        top.pack_start(body, True, True, 0)

        self._entry_combo = Gtk.ComboBoxText.new_with_entry()
        self._entry_combo.set_entry_text_column(0)
        self._entry_combo.set_hexpand(True)
        self._entry = cast(Gtk.Entry, self._entry_combo.get_child())
        self._entry.set_width_chars(COMMAND_WIDTH_CHARS)
        self._entry.set_activates_default(True)
        self._entry.connect("changed", self._on_entry_changed)
        body.pack_start(self._entry_combo, False, False, 0)

        options_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._terminal_check = Gtk.CheckButton(label=_("Run in terminal"))
        options_row.pack_start(self._terminal_check, False, False, 0)
        file_button = Gtk.Button(label=_("Run with file..."))
        file_button.connect("clicked", self._on_run_with_file)
        options_row.pack_end(file_button, False, False, 0)
        body.pack_start(options_row, False, False, 0)

        expander = Gtk.Expander(label=_("Show list of known applications"))
        expander.set_expanded(True)
        expander.add(self._build_app_list())
        outer.pack_start(expander, False, False, 0)

        self._description_label = Gtk.Label(
            label=_("Select an application to view its description."),
        )
        self._description_label.set_xalign(0.0)
        self._description_label.set_line_wrap(True)
        outer.pack_start(self._description_label, False, False, 0)

        return outer

    def _build_app_list(self) -> Gtk.ScrolledWindow:
        self._app_list = Gtk.ListBox()
        self._app_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._app_list.connect("row-selected", self._on_app_row_selected)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, APP_LIST_HEIGHT_PX)
        scroll.add(self._app_list)
        return scroll

    def _refresh_app_list(self) -> None:
        app_list = self._app_list
        if app_list is None:
            return
        for child in list(app_list.get_children()):
            app_list.remove(child)

        self._apps = list(visible_listings(self._application_registry))
        self._app_rows = []
        for app in self._apps:
            row = _ApplicationRow(app)
            row.add(self._build_app_row(app))
            app_list.add(row)
            self._app_rows.append(row)
        self._apply_app_filter(self._entry.get_text() if self._entry else "")

    def _build_app_row(self, app: ApplicationListing) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image()
        gicon = listing_gicon(self._application_registry, app)
        if gicon is not None:
            icon.set_from_gicon(gicon, Gtk.IconSize.MENU)
        icon.set_pixel_size(16)
        row.pack_start(icon, False, False, 0)

        label = Gtk.Label(label=app_display_name(app))
        label.set_xalign(0.0)
        row.pack_start(label, True, True, 0)
        return row

    def _sync_entry_history(self) -> None:
        if self._entry_combo is None:
            return
        current = self._entry.get_text() if self._entry is not None else ""
        self._entry_combo.remove_all()
        for command in self._history:
            self._entry_combo.append_text(command)
        if self._entry is not None and current:
            self._entry.set_text(current)

    def _on_delete_event(self, *_args: object) -> bool:
        if self._dialog is not None:
            self._dialog.hide()
        return True

    def _on_response(self, dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id == Gtk.ResponseType.OK:
            self._run_current()
            return
        if response_id == Gtk.ResponseType.HELP:
            self._show_help_dialog()
            return
        dialog.hide()

    def _show_help_dialog(self) -> None:
        parent = self._dialog
        help_dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=_("Run Application"),
        )
        help_dialog.format_secondary_text(
            _("Type a command or select an application to launch it."),
        )
        help_dialog.connect("response", lambda dlg, _response: dlg.destroy())
        help_dialog.show_all()

    # -- Input handling -------------------------------------------------------

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text()
        self._apply_app_filter(text)
        if self._selected_app is not None and text == self._selected_entry_text:
            matched = self._selected_app
        else:
            matched = match_application(apps=self._apps, text=text)
            self._selected_app = matched
            self._selected_entry_text = text if matched is not None else ""
        self._update_left_icon(matched)
        if self._description_label is not None:
            self._description_label.set_text(
                app_description(matched)
                if matched is not None
                else _("Select an application to view its description.")
            )
        self._sync_run_state()

    def _apply_app_filter(self, text: str) -> None:
        query = text.strip().casefold()
        for row in self._app_rows:
            visible = _app_matches_filter(app=row.app, query=query)
            if visible:
                row.show()
            else:
                row.hide()

    def _on_app_row_selected(
        self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if not isinstance(row, _ApplicationRow):
            return
        self._select_application(row.app)

    def _select_application(self, app: ApplicationListing) -> None:
        self._selected_app = app
        self._selected_entry_text = app_command_text(app)
        if self._entry is not None:
            self._entry.set_text(self._selected_entry_text)
            self._entry.set_position(-1)
        self._update_left_icon(app)
        if self._description_label is not None:
            self._description_label.set_text(app_description(app))
        self._sync_run_state()

    def _update_left_icon(self, app: ApplicationListing | None) -> None:
        if self._left_icon is None:
            return
        gicon = (
            listing_gicon(self._application_registry, app) if app is not None else None
        )
        if gicon is not None:
            self._left_icon.set_from_gicon(gicon, Gtk.IconSize.DIALOG)
        else:
            self._left_icon.set_from_icon_name(self.icon_name, Gtk.IconSize.DIALOG)
        self._left_icon.set_pixel_size(LEFT_ICON_PX)

    def _sync_run_state(self) -> None:
        if self._run_button is None or self._entry is None:
            return
        self._run_button.set_sensitive(bool(self._entry.get_text().strip()))

    def _on_run_with_file(self, _button: Gtk.Button) -> None:
        parent = self._dialog
        if parent is None:
            return
        dialog = Gtk.FileChooserDialog(
            title=_("Run with file"),
            parent=parent,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Open"), Gtk.ResponseType.OK)
        response = dialog.run()
        if response == Gtk.ResponseType.OK and self._entry is not None:
            filename = dialog.get_filename()
            if filename:
                command = self._entry.get_text()
                self._entry.set_text(
                    commands.append_file_argument(command=command, path=filename),
                )
                self._entry.set_position(-1)
        dialog.destroy()

    def _run_current(self) -> None:
        if self._entry is None:
            return
        command = self._entry.get_text().strip()
        if not command:
            return

        app = (
            self._selected_app
            if self._selected_app is not None and command == self._selected_entry_text
            else match_application(apps=self._apps, text=command)
        )
        run_in_terminal = bool(
            self._terminal_check and self._terminal_check.get_active()
        )
        if run_in_terminal:
            launched = commands.launch_command(
                command=app_command_text(app) if app is not None else command,
                run_in_terminal=True,
            )
        elif app is not None:
            launched = launch_application(
                app=app,
                launcher=self._application_launcher,
            )
        else:
            launched = commands.launch_command(
                command=command,
                run_in_terminal=False,
            )

        if launched:
            self._history = updated_history(history=self._history, command=command)
            self.save_prefs(prefs=prefs_payload(history=self._history))
            if self._dialog is not None:
                self._dialog.hide()


def _app_matches_filter(*, app: ApplicationListing, query: str) -> bool:
    if not query:
        return True
    name = app_display_name(app).casefold()
    command = app_command_text(app).casefold()
    return query in name or query in command
