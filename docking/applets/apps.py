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
from gi.repository import Gio, GLib

from docking.platform.launcher import Launcher
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
    apps: list[ApplicationEntry] = []
    seen: set[str] = set()

    for app_info in Gio.AppInfo.get_all():
        if isinstance(app_info, Gio.DesktopAppInfo):
            if app_info.get_is_hidden() or app_info.get_nodisplay():
                continue
            app_id = app_info.get_id()
            if app_id:
                seen.add(app_id)
            apps.append(
                ApplicationEntry(
                    desktop_id=app_id or "",
                    name=app_info.get_display_name() or app_id or "Unknown",
                    categories=app_info.get_categories() or "",
                    icon_name=app_info.get_icon().to_string()
                    if app_info.get_icon()
                    else "",
                    app_info=app_info,
                )
            )

    for desktop_dir in Launcher._get_desktop_dirs():
        for path in desktop_dir.rglob("*.desktop"):
            if not path.is_file():
                continue
            desktop_id = path.relative_to(desktop_dir).as_posix()
            if desktop_id in seen:
                continue
            try:
                app_info = Gio.DesktopAppInfo.new_from_filename(str(path))
            except (TypeError, GLib.Error):
                app_info = None
            if app_info is None:
                entry = _entry_from_desktop_file(desktop_id=desktop_id, path=path)
            else:
                if app_info.get_is_hidden() or app_info.get_nodisplay():
                    continue
                entry = ApplicationEntry(
                    desktop_id=desktop_id,
                    name=app_info.get_display_name() or desktop_id,
                    categories=app_info.get_categories() or "",
                    icon_name=app_info.get_icon().to_string()
                    if app_info.get_icon()
                    else "",
                    app_info=app_info,
                )
            if entry is None:
                continue
            seen.add(desktop_id)
            apps.append(entry)

    return apps


def _entry_from_desktop_file(*, desktop_id: str, path: Path) -> ApplicationEntry | None:
    key_file = GLib.KeyFile()
    try:
        key_file.load_from_file(str(path), GLib.KeyFileFlags.NONE)
    except GLib.Error:
        return None
    if _desktop_string(key_file=key_file, key="Type") != "Application":
        return None
    if _desktop_bool(key_file=key_file, key="Hidden") or _desktop_bool(
        key_file=key_file,
        key="NoDisplay",
    ):
        return None
    return ApplicationEntry(
        desktop_id=desktop_id,
        name=_desktop_locale_string(key_file=key_file, key="Name") or desktop_id,
        categories=_desktop_string(key_file=key_file, key="Categories"),
        icon_name=_desktop_string(key_file=key_file, key="Icon"),
    )


def _desktop_string(*, key_file: GLib.KeyFile, key: str) -> str:
    try:
        return key_file.get_string("Desktop Entry", key).strip()
    except GLib.Error:
        return ""


def _desktop_locale_string(*, key_file: GLib.KeyFile, key: str) -> str:
    try:
        return key_file.get_locale_string("Desktop Entry", key, None).strip()
    except GLib.Error:
        return _desktop_string(key_file=key_file, key=key)


def _desktop_bool(*, key_file: GLib.KeyFile, key: str) -> bool:
    try:
        return bool(key_file.get_boolean("Desktop Entry", key))
    except GLib.Error:
        return False


__all__ = ["ApplicationEntry", "all_desktop_app_infos"]
