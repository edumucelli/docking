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

"""Shared desktop application discovery helpers for applets.

The Applications and Run Application applets need the same launchable desktop
entry list but present it differently. This module keeps that discovery logic
out of individual applets and adapts host-parsed desktop files to the small
`Gio.DesktopAppInfo`-like surface those applets already use.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio

from docking.platform import desktop_entries
from docking.platform.launcher import launch as launch_desktop_id


class ApplicationEntry(NamedTuple):
    """Small app-info adapter for Gio and host-parsed desktop files.

    Entries discovered through `Gio.AppInfo.get_all()` keep their original
    `Gio.DesktopAppInfo`. Entries found only by walking desktop directories
    still expose the same methods so applet filtering, icon loading, and launch
    code do not need separate branches.
    """

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

    def get_icon(self) -> Gio.Icon | None:
        if self.app_info is not None:
            return self.app_info.get_icon()
        if not self.icon_name:
            return None

        icon_path = Path(self.icon_name)
        if icon_path.is_absolute():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))
        return Gio.ThemedIcon.new(self.icon_name)

    def desktop_file_uri(self) -> str | None:
        """Return the ``.desktop`` file URI used for drag-to-pin operations."""
        filename: str | None = None
        if self.app_info is not None:
            try:
                filename = self.app_info.get_filename()
            except (AttributeError, TypeError):
                filename = None

        if not filename:
            path = desktop_entries.find_desktop_file(self.desktop_id)
            if path is None:
                return None
            filename = str(path)

        return Path(filename).expanduser().resolve().as_uri()

    def launch(
        self,
        files: list[Gio.File] | None,
        context: Gio.AppLaunchContext | None,
    ) -> None:
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
