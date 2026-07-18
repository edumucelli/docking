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

"""Diagnostics window controller for Docking."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from docking.i18n import _
from docking.log import get_logger
from docking.platform.diagnostics import (
    DiagnosticCheck,
    DiagnosticFeature,
    DiagnosticsSnapshot,
    collect_diagnostics,
    format_diagnostics_report,
)

DIAGNOSTICS_WINDOW_WIDTH_PX = 720
DIAGNOSTICS_WINDOW_HEIGHT_PX = 560
WINDOW_BORDER_PX = 12
WINDOW_SPACING_PX = 10
SECTION_SPACING_PX = 8
ROW_SPACING_PX = 8
LABEL_MAX_CHARS = 72
REPORT_MARGIN_PX = 8

log = get_logger("diagnostics")


class DiagnosticsDialogController:
    """Owns the runtime diagnostics window lifecycle."""

    def __init__(
        self,
        *,
        parent: Gtk.Window,
        backend: object,
        register_tooltip_blocker: Callable[[Gtk.Widget], None] | None = None,
    ) -> None:
        self._parent = parent
        self._backend = backend
        self._register_tooltip_blocker = register_tooltip_blocker
        self._window: Gtk.Window | None = None
        self._snapshot: DiagnosticsSnapshot | None = None

    def show(self) -> None:
        """Show diagnostics, rebuilding the snapshot on every open."""
        self._snapshot = collect_diagnostics(
            backend=self._backend,
            display=self._parent.get_display(),
        )
        if self._window is not None:
            self._window.destroy()
        self._window = self._build_window(self._snapshot)
        self._window.show_all()
        self._window.present()

    def _build_window(self, snapshot: DiagnosticsSnapshot) -> Gtk.Window:
        window = Gtk.Window(
            title=_("Diagnostics"),
            transient_for=self._parent,
            destroy_with_parent=True,
        )
        if self._register_tooltip_blocker is not None:
            self._register_tooltip_blocker(window)
        window.set_default_size(
            DIAGNOSTICS_WINDOW_WIDTH_PX,
            DIAGNOSTICS_WINDOW_HEIGHT_PX,
        )
        window.set_position(Gtk.WindowPosition.CENTER)
        window.set_modal(False)
        window.set_resizable(True)
        window.connect("destroy", self._on_destroy)

        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=WINDOW_SPACING_PX,
        )
        outer.set_border_width(WINDOW_BORDER_PX)

        notebook = Gtk.Notebook()
        notebook.append_page(
            self._build_overview_tab(snapshot),
            Gtk.Label(label=_("Overview")),
        )
        notebook.append_page(
            self._build_features_tab(snapshot),
            Gtk.Label(label=_("Features")),
        )
        notebook.append_page(
            self._build_checks_tab(snapshot),
            Gtk.Label(label=_("Checks")),
        )
        notebook.append_page(
            self._build_environment_tab(snapshot),
            Gtk.Label(label=_("Environment")),
        )
        notebook.append_page(
            self._build_report_tab(snapshot),
            Gtk.Label(label=_("Report")),
        )

        buttons = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
        buttons.set_layout(Gtk.ButtonBoxStyle.END)
        buttons.set_spacing(ROW_SPACING_PX)

        refresh = Gtk.Button(label=_("Refresh"))
        refresh.connect("clicked", lambda *_a: self.show())
        copy = Gtk.Button(label=_("Copy Report"))
        copy.connect("clicked", self._on_copy_report)
        save = Gtk.Button(label=_("Save Report..."))
        save.connect("clicked", self._on_save_report)
        close = Gtk.Button(label=_("Close"))
        close.connect("clicked", lambda *_a: window.destroy())
        for button in (refresh, copy, save, close):
            buttons.add(button)

        outer.pack_start(notebook, True, True, 0)
        outer.pack_start(buttons, False, False, 0)
        window.add(outer)
        return window

    def _build_overview_tab(self, snapshot: DiagnosticsSnapshot) -> Gtk.Widget:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SECTION_SPACING_PX,
        )
        outer.set_border_width(WINDOW_BORDER_PX)
        status = Gtk.Label()
        status.set_markup(f"<b>{GLib.markup_escape_text(snapshot.health_label)}</b>")
        status.set_xalign(0.0)
        outer.pack_start(status, False, False, 0)

        grid = self._new_kv_grid()
        rows = [
            (_("Docking"), snapshot.docking_version),
            (_("OS"), snapshot.os_name),
            (_("Desktop"), snapshot.desktop),
            (_("Session"), snapshot.session_type),
            (_("Selected Backend"), snapshot.backend_name),
            (_("Display Server"), snapshot.display_server.value),
            (_("GTK Backend"), snapshot.gtk_backend),
            (_("Forced Backend"), snapshot.forced_backend or _("None")),
            (_("XWayland"), self._yes_no(snapshot.xwayland)),
            (_("X11 Compositor"), self._unknown_yes_no(snapshot.compositor_active)),
        ]
        for index, (label, value) in enumerate(rows):
            self._append_kv_row(grid, index, label, value)
        outer.pack_start(grid, False, False, 0)

        if snapshot.warnings:
            outer.pack_start(self._new_section_header(_("Warnings")), False, False, 0)
            for check in snapshot.warnings[:6]:
                outer.pack_start(self._check_row(check), False, False, 0)
        else:
            ok = Gtk.Label(label=_("No compatibility warnings detected."))
            ok.set_xalign(0.0)
            outer.pack_start(ok, False, False, 0)
        return self._scrolled(outer)

    def _build_features_tab(self, snapshot: DiagnosticsSnapshot) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ROW_SPACING_PX)
        box.set_border_width(WINDOW_BORDER_PX)
        for feature in snapshot.features:
            box.pack_start(self._feature_row(feature), False, False, 0)
        return self._scrolled(box)

    def _build_checks_tab(self, snapshot: DiagnosticsSnapshot) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ROW_SPACING_PX)
        box.set_border_width(WINDOW_BORDER_PX)
        for check in snapshot.checks:
            box.pack_start(self._check_row(check), False, False, 0)
        return self._scrolled(box)

    def _build_environment_tab(self, snapshot: DiagnosticsSnapshot) -> Gtk.Widget:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SECTION_SPACING_PX,
        )
        outer.set_border_width(WINDOW_BORDER_PX)

        outer.pack_start(self._new_section_header(_("Runtime")), False, False, 0)
        runtime = self._new_kv_grid()
        for index, (label, value) in enumerate(
            [
                (_("Python"), snapshot.python_version),
                (_("GTK"), snapshot.gtk_version),
                (_("Backend Class"), snapshot.backend_class),
                (_("Wayland Session"), self._yes_no(snapshot.wayland_session)),
                (_("X11 GTK Backend"), self._yes_no(snapshot.x11_backend)),
            ]
        ):
            self._append_kv_row(runtime, index, label, value)
        outer.pack_start(runtime, False, False, 0)

        outer.pack_start(self._new_section_header(_("Monitors")), False, False, 0)
        if snapshot.monitors:
            monitors = self._new_kv_grid()
            for index, monitor in enumerate(snapshot.monitors):
                primary = _("primary") if monitor.primary else _("secondary")
                name = f" ({monitor.name})" if monitor.name else ""
                self._append_kv_row(
                    monitors,
                    index,
                    f"#{monitor.index} {primary}{name}",
                    f"{monitor.geometry}, scale {monitor.scale}",
                )
            outer.pack_start(monitors, False, False, 0)
        else:
            label = Gtk.Label(label=_("Monitor details unavailable."))
            label.set_xalign(0.0)
            outer.pack_start(label, False, False, 0)

        outer.pack_start(self._new_section_header(_("Environment")), False, False, 0)
        env_grid = self._new_kv_grid()
        for index, (key, value) in enumerate(snapshot.environment.items()):
            self._append_kv_row(env_grid, index, key, value)
        outer.pack_start(env_grid, False, False, 0)
        return self._scrolled(outer)

    def _build_report_tab(self, snapshot: DiagnosticsSnapshot) -> Gtk.Widget:
        report = format_diagnostics_report(snapshot)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_border_width(REPORT_MARGIN_PX)
        view.get_buffer().set_text(report)
        return self._scrolled(view)

    def _feature_row(self, feature: DiagnosticFeature) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=ROW_SPACING_PX)
        icon = Gtk.Label(label="OK" if feature.available else "!")
        icon.set_size_request(24, -1)
        icon.set_xalign(0.5)
        row.pack_start(icon, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=feature.label)
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        detail = Gtk.Label(label=feature.detail)
        detail.set_xalign(0.0)
        detail.set_max_width_chars(LABEL_MAX_CHARS)
        detail.set_line_wrap(True)
        text.pack_start(title, False, False, 0)
        text.pack_start(detail, False, False, 0)
        row.pack_start(text, True, True, 0)
        return row

    def _check_row(self, check: DiagnosticCheck) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=ROW_SPACING_PX)
        icon = Gtk.Label(label=self._status_symbol(check.status))
        icon.set_size_request(24, -1)
        icon.set_xalign(0.5)
        row.pack_start(icon, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=f"{check.label}: {check.detail}")
        title.set_xalign(0.0)
        title.set_max_width_chars(LABEL_MAX_CHARS)
        title.set_line_wrap(True)
        text.pack_start(title, False, False, 0)
        if check.fix_hint:
            hint = Gtk.Label(label=check.fix_hint)
            hint.set_xalign(0.0)
            hint.set_max_width_chars(LABEL_MAX_CHARS)
            hint.set_line_wrap(True)
            text.pack_start(hint, False, False, 0)
        row.pack_start(text, True, True, 0)
        return row

    def _new_kv_grid(self) -> Gtk.Grid:
        grid = Gtk.Grid()
        grid.set_column_spacing(ROW_SPACING_PX * 2)
        grid.set_row_spacing(ROW_SPACING_PX)
        return grid

    def _append_kv_row(
        self,
        grid: Gtk.Grid,
        row: int,
        label_text: str,
        value_text: str,
    ) -> None:
        label = Gtk.Label(label=label_text)
        label.set_xalign(0.0)
        label.get_style_context().add_class("dim-label")
        value = Gtk.Label(label=value_text)
        value.set_xalign(0.0)
        value.set_selectable(True)
        value.set_max_width_chars(LABEL_MAX_CHARS)
        value.set_ellipsize(Pango.EllipsizeMode.END)
        grid.attach(label, 0, row, 1, 1)
        grid.attach(value, 1, row, 1, 1)

    def _new_section_header(self, text: str) -> Gtk.Label:
        label = Gtk.Label()
        label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        label.set_xalign(0.0)
        return label

    def _scrolled(self, child: Gtk.Widget) -> Gtk.ScrolledWindow:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(child)
        return scrolled

    def _current_report(self) -> str:
        snapshot = self._snapshot
        if snapshot is None:
            snapshot = collect_diagnostics(
                backend=self._backend,
                display=self._parent.get_display(),
            )
            self._snapshot = snapshot
        return format_diagnostics_report(snapshot)

    def _on_copy_report(self, *_args: object) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self._current_report(), -1)
        clipboard.store()

    def _on_save_report(self, *_args: object) -> None:
        if self._window is None:
            return
        chooser = Gtk.FileChooserNative(
            title=_("Save Diagnostics Report"),
            transient_for=self._window,
            action=Gtk.FileChooserAction.SAVE,
            accept_label=_("Save"),
            cancel_label=_("Cancel"),
        )
        chooser.set_current_name("docking-diagnostics.md")
        chooser.set_do_overwrite_confirmation(True)
        try:
            response = chooser.run()
            if response != Gtk.ResponseType.ACCEPT:
                return
            filename = chooser.get_filename()
            if not filename:
                return
            try:
                with Path(filename).open("w", encoding="utf-8") as report_file:
                    report_file.write(self._current_report())
            except OSError as exc:
                log.warning("Failed to save diagnostics report: %s", exc)
                self._show_error(_("Failed to save diagnostics report."))
        finally:
            chooser.destroy()

    def _show_error(self, text: str) -> None:
        if self._window is None:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self._window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=text,
        )
        if self._register_tooltip_blocker is not None:
            self._register_tooltip_blocker(dialog)
        dialog.run()
        dialog.destroy()

    def _on_destroy(self, window: Gtk.Window) -> None:
        if self._window is window:
            self._window = None

    @staticmethod
    def _status_symbol(status: str) -> str:
        if status == "ok":
            return "OK"
        if status == "error":
            return "X"
        if status == "warning":
            return "!"
        return "i"

    @staticmethod
    def _yes_no(value: bool) -> str:
        return _("Yes") if value else _("No")

    @staticmethod
    def _unknown_yes_no(value: bool | None) -> str:
        if value is None:
            return _("Unknown")
        return DiagnosticsDialogController._yes_no(value)
