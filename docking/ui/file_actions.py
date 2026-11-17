"""Own interactive file actions beyond the lifetime of their originating menu."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.platform.desktop.base import OpenWithMode, OpenWithResult
from docking.platform.targets import TargetService


class FileActionsController:
    def __init__(self, *, parent: Gtk.Window, target_service: TargetService) -> None:
        self._parent = parent
        self._targets = target_service
        self._worker = BackgroundWorker()
        self._stopped = False
        self._pending = False
        self._dialog: Gtk.AppChooserDialog | None = None
        self._error_dialog: Gtk.MessageDialog | None = None
        self._cancel_request: Callable[[], None] | None = None
        parent.connect("destroy", lambda _window: self.stop())

    def stop(self) -> None:
        self._stopped = True
        self._pending = False
        if self._cancel_request is not None:
            self._cancel_request()
            self._cancel_request = None
        if self._dialog is not None:
            self._dialog.destroy()
            self._dialog = None
        if self._error_dialog is not None:
            self._error_dialog.destroy()
            self._error_dialog = None

    def open_in_terminal(self, target: str) -> None:
        if self._stopped:
            return
        self._worker.run(
            name="open-folder-terminal",
            fn=lambda: (
                self._targets.open_in_terminal(target) if not self._stopped else True
            ),
            on_result=lambda opened: (
                None if opened else self._error(_("Could not open folder in terminal."))
            ),
            on_error=lambda error: self._error(_("Could not open folder in terminal.")),
        )

    def open_with(self, target: str) -> None:
        if self._stopped or self._pending:
            return
        self._pending = True
        self._worker.run(
            name="prepare-open-with",
            fn=lambda: self._targets.prepare_open_with(target),
            on_result=self._choose,
            on_error=lambda error: self._finished(OpenWithResult.FAILED),
        )

    def _choose(self, prepared: tuple[str, str]) -> None:
        if self._stopped:
            return
        try:
            uri, content_type = prepared
            mode = self._targets.open_with_mode(
                is_dir=content_type == "inode/directory"
            )
            if mode is None:
                self._finished(OpenWithResult.UNSUPPORTED)
            elif mode is OpenWithMode.GTK:
                dialog = Gtk.AppChooserDialog.new_for_content_type(
                    self._parent, Gtk.DialogFlags.MODAL, content_type
                )
                self._dialog = dialog
                dialog.connect("response", self._chosen, uri)
                dialog.show_all()
                dialog.present()
            else:
                self._cancel_request = self._targets.request_open_with(
                    uri,
                    parent=self._parent,
                    on_result=self._finished,
                )
        except (GLib.Error, OSError):
            if self._dialog is not None:
                self._dialog.destroy()
                self._dialog = None
            self._finished(OpenWithResult.FAILED)

    def _chosen(self, dialog: Gtk.AppChooserDialog, response: int, uri: str) -> None:
        handler = dialog.get_app_info() if response == Gtk.ResponseType.OK else None
        self._dialog = None
        dialog.destroy()
        if self._stopped:
            return
        if handler is None:
            self._finished(OpenWithResult.CANCELLED)
            return
        try:
            cancellable = self._targets.launch_with_handler(
                uri, handler, on_result=self._finished
            )
        except GLib.Error:
            self._finished(OpenWithResult.FAILED)
            return
        self._cancel_request = cancellable.cancel

    def _finished(self, result: OpenWithResult) -> None:
        self._pending = False
        self._cancel_request = None
        if result in {OpenWithResult.FAILED, OpenWithResult.UNSUPPORTED}:
            self._error(_("Could not open this item with another application."))

    def _error(self, message: str) -> None:
        if self._stopped:
            return
        if self._error_dialog is not None:
            self._error_dialog.destroy()
        dialog = Gtk.MessageDialog(
            transient_for=self._parent,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=message,
        )
        self._error_dialog = dialog
        dialog.connect("destroy", self._error_closed)
        dialog.connect("response", lambda window, response: window.destroy())
        dialog.show_all()

    def _error_closed(self, dialog: Gtk.MessageDialog) -> None:
        if self._error_dialog is dialog:
            self._error_dialog = None
