"""Shared captured-image clipboard, preview and explicit PNG export UI."""

from __future__ import annotations

import tempfile
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.popup import PopupAnchor, prepare_dialog_content
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger

from .state import ScreenshotImage

log = get_logger("screenshot.preview")


def save_png(image: GdkPixbuf.Pixbuf, path: Path) -> None:
    """Finish encoding before replacing a user-selected file."""
    with tempfile.NamedTemporaryFile(
        dir=path.parent, suffix=".png", delete=False
    ) as temporary:
        pending = Path(temporary.name)
    try:
        image.savev(str(pending), "png", [], [])
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


class ScreenshotPreview:
    def __init__(self, image: ScreenshotImage, *, anchor: PopupAnchor | None) -> None:
        self._closed = False
        self._chooser: Gtk.FileChooserNative | None = None
        self._worker = BackgroundWorker(
            thread_factory=lambda target, daemon: threading.Thread(
                target=target, daemon=False, name="screenshot-save"
            )
        )
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(image.pixels),
            GdkPixbuf.Colorspace.RGB,
            image.alpha,
            8,
            image.width,
            image.height,
            image.width * (4 if image.alpha else 3),
        )
        parent = anchor.parent if anchor is not None else None
        self.dialog = Gtk.Dialog(title=_("Screenshot"), transient_for=parent)
        if parent is not None:
            self.dialog.set_destroy_with_parent(True)
        content = prepare_dialog_content(dialog=self.dialog, resizable=False)
        display = self.dialog.get_display()
        native_parent = parent.get_window() if parent is not None else None
        monitor = (
            display.get_monitor_at_window(native_parent)
            if native_parent is not None
            else display.get_primary_monitor()
        )
        if monitor is None:
            monitor = display.get_monitor(0)
        workarea = monitor.get_workarea()
        max_width = max(1, min(900, workarea.width - 80))
        max_height = max(1, min(500, workarea.height - 140))
        scale = min(1.0, max_width / image.width, max_height / image.height)
        shown = self.pixbuf.scale_simple(
            max(1, int(image.width * scale)),
            max(1, int(image.height * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )
        content.add(Gtk.Image.new_from_pixbuf(shown))
        self.dialog.add_buttons(
            Gtk.STOCK_COPY,
            Gtk.ResponseType.APPLY,
            Gtk.STOCK_SAVE_AS,
            Gtk.ResponseType.ACCEPT,
            Gtk.STOCK_CLOSE,
            Gtk.ResponseType.CLOSE,
        )
        self.dialog.connect("response", self._response)
        self.dialog.connect("destroy", self._destroyed)
        self.copy()
        self.dialog.show_all()
        self.dialog.present()

    def copy(self) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_image(self.pixbuf)
        clipboard.store()

    def close(self) -> None:
        self.dialog.destroy()

    def _destroyed(self, _dialog) -> None:
        self._closed = True
        if self._chooser is not None:
            self._chooser.destroy()
            self._chooser = None

    def _response(self, _dialog, response: int) -> None:
        if response == Gtk.ResponseType.APPLY:
            self.copy()
        elif response == Gtk.ResponseType.ACCEPT:
            self._save_as()
        else:
            self.close()

    def _save_as(self) -> None:
        if self._chooser is not None:
            self._chooser.show()
            return
        chooser = Gtk.FileChooserNative.new(
            _("Screenshot"), self.dialog, Gtk.FileChooserAction.SAVE, None, None
        )
        self._chooser = chooser
        chooser.set_modal(True)
        chooser.set_do_overwrite_confirmation(True)
        chooser.set_current_name(
            "Screenshot_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".png"
        )
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pictures:
            chooser.set_current_folder(pictures)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("PNG")
        file_filter.add_pattern("*.png")
        chooser.add_filter(file_filter)
        chooser.connect("response", self._save_response)
        chooser.show()

    def _save_response(self, chooser: Gtk.FileChooserNative, response: int) -> None:
        filename = (
            chooser.get_filename() if response == Gtk.ResponseType.ACCEPT else None
        )
        self._chooser = None
        chooser.destroy()
        if filename:
            path = Path(filename)
            # Preserve the exact path whose overwrite the chooser confirmed.
            self._worker.run(
                name="screenshot-save",
                fn=lambda: save_png(self.pixbuf, path),
                on_error=self._save_error,
            )

    def _save_error(self, error: Exception) -> bool:
        log.warning("Could not save screenshot: %s", error)
        if not self._closed:
            dialog = Gtk.MessageDialog(
                transient_for=self.dialog,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text=_("Unavailable"),
            )
            dialog.connect("response", lambda widget, _response: widget.destroy())
            dialog.show_all()
        return False
