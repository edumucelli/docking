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

"""About dialog controller for Docking."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking import __version__ as docking_version
from docking.i18n import _
from docking.log import get_logger
from docking.platform import targets

PROJECT_VERSION_FALLBACK = docking_version
PROJECT_LICENSE_FALLBACK = "GNU GPL v3.0 or later (GPL-3.0-or-later)"
PROJECT_LICENSE_PATH = Path(__file__).resolve().parents[2] / "LICENSE"
PROJECT_WEBSITE_URL = "https://docking.cc"
PROJECT_GITHUB_URL = "https://github.com/edumucelli/docking"
DEFAULT_LOGO_ICON_NAME = "org.docking.Docking"

log = get_logger("about")


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
        dialog.set_website(PROJECT_WEBSITE_URL)
        dialog.set_website_label(_("Website"))
        dialog.set_logo_icon_name(_logo_icon_name())
        dialog.set_authors(["Eduardo Mucelli Rezende Oliveira"])
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_license(self._project_license_text())
        dialog.set_wrap_license(True)
        dialog.add_button("GitHub", Gtk.ResponseType.HELP)

        dialog.connect("response", self._on_response)
        dialog.connect("hide", self._on_hide)
        dialog.show_all()
        self._dialog = dialog

    def _project_version(self) -> str:
        if docking_version:
            return docking_version
        try:
            return pkg_version("docking")
        except PackageNotFoundError as exc:
            log.debug("Package metadata unavailable, using fallback version: %s", exc)
            return PROJECT_VERSION_FALLBACK

    def _project_license_text(self) -> str:
        try:
            return PROJECT_LICENSE_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("Failed to read bundled LICENSE file: %s", exc)
            return PROJECT_LICENSE_FALLBACK

    def _on_response(self, dialog: Gtk.AboutDialog, _response: int) -> None:
        if _response == Gtk.ResponseType.HELP:
            self._open_project_github()
            return
        dialog.hide()

    def _on_hide(self, dialog: Gtk.AboutDialog) -> None:
        dialog.destroy()
        if self._dialog is dialog:
            self._dialog = None

    def _open_project_github(self) -> None:
        targets.open_target(PROJECT_GITHUB_URL)


def _logo_icon_name() -> str:
    return os.environ.get("FLATPAK_ID") or DEFAULT_LOGO_ICON_NAME
