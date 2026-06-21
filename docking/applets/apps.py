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

"""Shared desktop application discovery helpers for applets."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio

from docking.platform import desktop_entries
from docking.platform.launcher import launch as launch_desktop_id


class ApplicationEntry(NamedTuple):
    """Small app-info adapter for Gio and host-parsed desktop files."""

    desktop_id: str
    name: str
    categories: str
    icon_name: str
    app_info: Gio.DesktopAppInfo | None = None

    def get_id(self) -> str:
        return self.desktop_id

    def get_display_name(self) -> str:
        return self.name

    def get_categories(self) -> str:
        return self.categories

    def get_icon(self) -> object | None:
        if self.app_info is not None:
            return self.app_info.get_icon()
        if not self.icon_name:
            return None

        icon_path = Path(self.icon_name)
        if icon_path.is_absolute():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))
        return Gio.ThemedIcon.new(self.icon_name)

    def launch(self, files: list[object], context: object | None) -> None:
        if self.desktop_id:
            launch_desktop_id(desktop_id=self.desktop_id)
            return
        if self.app_info is not None:
            self.app_info.launch(files, context)


def all_desktop_app_infos() -> list[ApplicationEntry]:
    """Return launchable desktop applications visible to applet UIs."""
    return [
        ApplicationEntry(
            desktop_id=entry.desktop_id,
            name=entry.name,
            categories=entry.categories,
            icon_name=entry.icon_name,
            app_info=entry.app_info,
        )
        for entry in desktop_entries.all_desktop_app_listings()
    ]


__all__ = ["ApplicationEntry", "all_desktop_app_infos"]
