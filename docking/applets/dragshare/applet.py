"""GTK lifecycle for the Drag Share applet."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.dragshare import meta
from docking.applets.dragshare.render import render_icon
from docking.applets.dragshare.state import (
    DragshareStatus,
    UploadError,
    UploadResult,
    first_uploadable_file,
    upload_file,
)
from docking.applets.tooltip import structured_tooltip
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="dragshare"), applet_id=meta.id)


class DragshareApplet(Applet):
    """Drop a file onto the dock icon, upload it, and copy the URL."""

    id = meta.id
    name = _("Drag Share")
    icon_name = "folder-publicshare"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = config.applet_prefs.get(meta.id, {}) if config else {}
        self._status = DragshareStatus.IDLE
        self._last_url = str(prefs.get("last_url", ""))
        self._file_name = ""
        self._error = ""
        self._upload_thread: threading.Thread | None = None

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, status=self._status)

    def refresh_tooltip(self) -> None:
        if self._status is DragshareStatus.UPLOADING:
            self.item.name = structured_tooltip(
                title=_("Drag Share"),
                primary=_("Uploading {file}...").format(file=self._file_name),
            )
            return
        if self._status is DragshareStatus.DONE and self._last_url:
            self.item.name = structured_tooltip(
                title=_("Drag Share"),
                primary=_("Uploaded {file}; URL copied").format(file=self._file_name),
            )
            return
        if self._status is DragshareStatus.ERROR:
            self.item.name = structured_tooltip(
                title=_("Drag Share"),
                primary=_("Upload failed"),
                error=self._error,
            )
            return
        if self._last_url:
            self.item.name = structured_tooltip(
                title=_("Drag Share"),
                primary=_("Click to copy last URL"),
            )
            return
        self.item.name = structured_tooltip(
            title=_("Drag Share"),
            primary=_("Drop a file to upload"),
        )

    def accepts_drop_uris(self) -> bool:
        return True

    def on_drop_uris(self, uris: list[str]) -> bool:
        if self._status is DragshareStatus.UPLOADING:
            self._set_error(_("Upload already in progress"))
            return True

        path = first_uploadable_file(uris)
        if path is None:
            self._set_error(_("Drop a local file to upload"))
            return True

        self._start_upload(path)
        return True

    def on_clicked(self) -> None:
        if self._last_url:
            self._copy_to_clipboard(self._last_url)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        copy = Gtk.MenuItem(label=_("Copy Last URL"))
        copy.set_sensitive(bool(self._last_url))
        copy.connect("activate", lambda _: self.on_clicked())
        return [copy]

    def stop(self) -> None:
        self._upload_thread = None
        super().stop()

    def _start_upload(self, path: Path) -> None:
        self._status = DragshareStatus.UPLOADING
        self._file_name = path.name
        self._error = ""
        self.present()

        thread = threading.Thread(
            target=self._upload_in_background,
            args=(path,),
            daemon=True,
        )
        self._upload_thread = thread
        thread.start()

    def _upload_in_background(self, path: Path) -> None:
        try:
            result = upload_file(path)
        except UploadError as exc:
            GLib.idle_add(self._finish_upload_error, str(exc))
            return
        except Exception as exc:
            log.warning("Unexpected drag share upload failure: %s", exc)
            GLib.idle_add(self._finish_upload_error, _("Upload failed"))
            return
        GLib.idle_add(self._finish_upload_success, result)

    def _finish_upload_success(self, result: UploadResult) -> bool:
        self._status = DragshareStatus.DONE
        self._last_url = result.url
        self._file_name = result.file_name
        self._error = ""
        self._upload_thread = None
        self._copy_to_clipboard(result.url)
        self._save()
        self.present()
        return False

    def _finish_upload_error(self, message: str) -> bool:
        self._set_error(message)
        self._upload_thread = None
        return False

    def _set_error(self, message: str) -> None:
        self._status = DragshareStatus.ERROR
        self._error = message
        self.present()

    def _copy_to_clipboard(self, text: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()

    def _save(self) -> None:
        self.save_prefs(prefs={"last_url": self._last_url})
