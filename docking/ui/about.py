"""About dialog controller for Docking."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from docking.i18n import _

PROJECT_VERSION_FALLBACK = "0.0.0"
PROJECT_LICENSE_FALLBACK = "GNU GPL v3.0 or later (GPL-3.0-or-later)"
PROJECT_LICENSE_PATH = Path(__file__).resolve().parents[2] / "LICENSE"


class AboutDialogController:
    """Owns About dialog lifecycle and single-instance behavior."""

    def __init__(self, parent: Gtk.Window) -> None:
        self._parent = parent
        self._dialog: Gtk.AboutDialog | None = None

    def show(self) -> None:
        """Show About dialog, reusing existing instance when available."""
        if self._dialog is not None:
            self._dialog.show_all()
            return

        dialog = Gtk.AboutDialog(
            transient_for=self._parent,
            modal=True,
            destroy_with_parent=True,
        )
        dialog.set_program_name("Docking")
        dialog.set_version(self._project_version())
        dialog.set_comments(
            "A lightweight, feature-rich dock for Linux written in Python "
            "with GTK 3 and Cairo."
        )
        dialog.set_website("https://github.com/edumucelli/docking")
        dialog.set_website_label(_("Website"))
        dialog.set_logo_icon_name("org.docking.Docking")
        dialog.set_authors(["Eduardo Mucelli Rezende Oliveira"])
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_license(self._project_license_text())
        dialog.set_wrap_license(True)

        dialog.connect("response", self._on_response)
        dialog.connect("hide", self._on_hide)
        dialog.show_all()
        self._dialog = dialog

    def _project_version(self) -> str:
        try:
            return pkg_version("docking")
        except PackageNotFoundError:
            return PROJECT_VERSION_FALLBACK

    def _project_license_text(self) -> str:
        try:
            return PROJECT_LICENSE_PATH.read_text(encoding="utf-8")
        except OSError:
            return PROJECT_LICENSE_FALLBACK

    def _on_response(self, dialog: Gtk.AboutDialog, _response: int) -> None:
        dialog.hide()

    def _on_hide(self, dialog: Gtk.AboutDialog) -> None:
        dialog.destroy()
        if self._dialog is dialog:
            self._dialog = None
