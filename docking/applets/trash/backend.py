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

"""Trash backend adapters for desktop-specific behavior."""

from __future__ import annotations

import configparser
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.trash import meta
from docking.log import get_logger, with_context
from docking.platform.environment import (
    Desktop,
    detect_desktop,
    flatpak,
    is_flatpak,
    is_gnome_session,
    is_kde_session,
    is_mate_session,
    xdg_config_home,
    xdg_data_home,
)

log = with_context(get_logger(name="trash"), applet_id=meta.id)


class TrashBackend(Protocol):
    """Desktop-specific trash behavior used by the Trash applet."""

    name: str
    uri: str

    def count_items(self) -> int: ...

    def monitor_file(self) -> Gio.File: ...

    def open(self) -> None: ...

    def empty(self, confirm: Callable[[], bool]) -> None: ...


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _host_user_data_home() -> Path:
    return Path.home() / ".local" / "share"


def _kde_trash_directory() -> Path:
    return xdg_data_home() / "Trash"


def _kde_trash_files_directory() -> Path:
    return _kde_trash_directory() / "files"


def _kde_trash_info_directory() -> Path:
    return _kde_trash_directory() / "info"


def _visible_trash_files_directory() -> Path:
    if is_flatpak():
        return _host_user_data_home() / "Trash" / "files"
    return xdg_data_home() / "Trash" / "files"


def _kde_kiorc_file() -> Path:
    if is_flatpak():
        return Path.home() / ".config" / "kiorc"
    return xdg_config_home() / "kiorc"


def _open_trash_uri(uri: str) -> None:
    if is_flatpak() and _open_host_trash_uri(uri=uri):
        return

    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error as exc:
        log.bind(action="open_trash").warning("Failed to open trash: %s", exc)


def _open_host_trash_uri(*, uri: str) -> bool:
    command = flatpak.host_command(["gio", "open", uri])
    if command is None:
        return False
    try:
        subprocess.Popen(command)
        return True
    except OSError as exc:
        log.bind(action="open_trash").warning(
            "Failed to open host trash with flatpak-spawn: %s",
            exc,
        )
        return False


def _empty_host_trash() -> bool:
    if not is_flatpak():
        return False
    command = flatpak.host_command(["gio", "trash", "--empty"])
    if command is None:
        return False
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action="empty_trash_host").warning(
            "Failed to empty host trash with flatpak-spawn: %s",
            exc,
        )
        return False
    if result.returncode == 0:
        return True
    log.bind(action="empty_trash_host").warning(
        "Host trash empty command failed: %s",
        result.stderr.strip() or result.stdout.strip() or result.returncode,
    )
    return False


class GioTrashBackend:
    """Generic trash backend using Gio trash:/// behavior."""

    name = "gio"
    uri = "trash:///"
    confirm_trash_key = "confirm-trash"
    dbus_targets: tuple[tuple[str, str], ...] = (
        ("org.mate.Caja", "/org/mate/Caja"),
        ("org.gnome.Nautilus", "/org/gnome/Nautilus"),
    )
    confirmation_schema: tuple[str, str] | None = None

    def count_items(self) -> int:
        if is_flatpak():
            files_dir = _visible_trash_files_directory()
            try:
                return sum(1 for _child in files_dir.iterdir())
            except OSError as exc:
                log.bind(action="count_items").debug(
                    "Could not enumerate visible trash files: %s", exc
                )
                return 0

        trash = Gio.File.new_for_uri(self.uri)
        try:
            enumerator = trash.enumerate_children(
                Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE, None
            )
        except GLib.Error as exc:
            log.bind(action="count_items").debug(
                "Could not enumerate trash items: %s", exc
            )
            return 0

        count = 0
        while enumerator.next_file(None) is not None:
            count += 1
        enumerator.close(None)
        return count

    def monitor_file(self) -> Gio.File:
        if is_flatpak():
            return Gio.File.new_for_path(str(_visible_trash_files_directory()))
        return Gio.File.new_for_uri(self.uri)

    def open(self) -> None:
        _open_trash_uri(self.uri)

    def empty(self, confirm: Callable[[], bool]) -> None:
        _ = confirm
        if self._confirmation_preference() is False:
            if _empty_host_trash():
                return
            self._delete_contents()
            return
        if self._empty_via_dbus():
            return
        if _empty_host_trash():
            return
        self._delete_contents()

    def _confirmation_preference(self) -> bool | None:
        if self.confirmation_schema is None:
            return None
        schema_id, path = self.confirmation_schema
        schema_source = Gio.SettingsSchemaSource.get_default()
        if schema_source is None:
            return None
        schema = schema_source.lookup(schema_id, True)
        if schema is None or not schema.has_key(self.confirm_trash_key):
            return None
        try:
            settings = Gio.Settings.new_full(schema, None, path)
            return bool(settings.get_boolean(self.confirm_trash_key))
        except Exception as exc:
            log.bind(action="read_confirm_trash").debug(
                "Could not read %s %s: %s",
                schema_id,
                self.confirm_trash_key,
                exc,
            )
            return None

    def _empty_via_dbus(self) -> bool:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            log.bind(action="empty_trash_dbus").debug(
                "Could not connect to session bus for trash cleanup: %s",
                exc,
            )
            return False

        for bus_name, obj_path in self.dbus_targets:
            try:
                bus.call_sync(
                    bus_name,
                    obj_path,
                    "org.gnome.Nautilus.FileOperations",
                    "EmptyTrash",
                    None,
                    None,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                )
                return True
            except GLib.Error as exc:
                log.bind(action="empty_trash_dbus").debug(
                    "DBus EmptyTrash failed for %s at %s: %s",
                    bus_name,
                    obj_path,
                    exc,
                )
        return False

    def _delete_contents(self) -> None:
        trash = Gio.File.new_for_uri(self.uri)
        try:
            enumerator = trash.enumerate_children(
                Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE, None
            )
        except GLib.Error as exc:
            log.bind(action="empty_trash_delete").debug(
                "Could not enumerate trash for deletion: %s",
                exc,
            )
            return

        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            child = trash.get_child(info.get_name())
            try:
                child.delete(None)
            except GLib.Error as exc:
                log.bind(action="empty_trash_delete").debug(
                    "Could not delete trash item %s: %s",
                    info.get_name(),
                    exc,
                )
        enumerator.close(None)


class GnomeTrashBackend(GioTrashBackend):
    """Trash backend for GNOME/Nautilus sessions."""

    name = "gnome"
    dbus_targets = (("org.gnome.Nautilus", "/org/gnome/Nautilus"),)


class MateTrashBackend(GioTrashBackend):
    """Trash backend for MATE/Caja sessions."""

    name = "mate"
    dbus_targets = (("org.mate.Caja", "/org/mate/Caja"),)
    confirmation_schema = (
        "org.mate.caja.preferences",
        "/org/mate/caja/preferences/",
    )


class CinnamonTrashBackend(GioTrashBackend):
    """Trash backend for Cinnamon/Nemo sessions."""

    name = "cinnamon"
    confirmation_schema = (
        "org.nemo.preferences",
        "/org/nemo/preferences/",
    )


class KdeTrashBackend:
    """Trash backend for KDE's trash:/ KIO behavior."""

    name = "kde"
    uri = "trash:/"
    confirm_empty_trash_key = "ConfirmEmptyTrash"
    confirmations_section = "Confirmations"
    open_commands: tuple[tuple[str, ...], ...] = (
        ("kioclient6", "exec", uri),
        ("kioclient5", "exec", uri),
        ("kioclient", "exec", uri),
        ("dolphin", uri),
        ("kde-open5", uri),
        ("kde-open", uri),
    )

    def count_items(self) -> int:
        # Inside Flatpak, trash:/// enumeration can report the sandbox trash.
        # The host trash files are visible through home access, so count them.
        files_dir = (
            _visible_trash_files_directory()
            if is_flatpak()
            else _kde_trash_files_directory()
        )
        try:
            return sum(1 for _child in files_dir.iterdir())
        except OSError as exc:
            log.bind(action="count_kde_items").debug(
                "Could not enumerate KDE trash items: %s",
                exc,
            )
            return 0

    def monitor_file(self) -> Gio.File:
        if is_flatpak():
            return Gio.File.new_for_path(str(_visible_trash_files_directory()))
        return Gio.File.new_for_path(str(_kde_trash_files_directory()))

    def open(self) -> None:
        command = self._available_open_command()
        if command is not None:
            try:
                subprocess.Popen(command)
                return
            except OSError as exc:
                log.bind(action="open_trash").warning(
                    "Failed to open KDE trash with %s: %s",
                    command[0],
                    exc,
                )

        _open_trash_uri(self.uri)

    def empty(self, confirm: Callable[[], bool]) -> None:
        if self._confirmation_preference() is False or confirm():
            if _empty_host_trash():
                return
            self._delete_contents()

    def _confirmation_preference(self) -> bool:
        parser = _CaseSensitiveConfigParser()
        try:
            if not parser.read(_kde_kiorc_file()):
                return True
        except configparser.Error as exc:
            log.bind(action="read_confirm_trash").debug(
                "Could not read KDE trash confirmation preference: %s",
                exc,
            )
            return True

        if not parser.has_option(
            self.confirmations_section, self.confirm_empty_trash_key
        ):
            return True
        try:
            return parser.getboolean(
                self.confirmations_section,
                self.confirm_empty_trash_key,
            )
        except ValueError as exc:
            log.bind(action="read_confirm_trash").debug(
                "Invalid KDE trash confirmation preference: %s",
                exc,
            )
            return True

    def _available_open_command(self) -> tuple[str, ...] | None:
        for command in self.open_commands:
            if is_flatpak():
                # KDE open helpers live on the host, not in the GNOME runtime.
                host_command = flatpak.host_command(list(command))
                if (
                    flatpak.host_command_available(command[0])
                    and host_command is not None
                ):
                    return tuple(host_command)
                continue
            if shutil.which(command[0]):
                return command
        return None

    def _delete_contents(self) -> None:
        self._delete_directory_contents(_kde_trash_files_directory())
        self._delete_directory_contents(_kde_trash_info_directory())

    def _delete_directory_contents(self, directory: Path) -> None:
        try:
            children = list(directory.iterdir())
        except FileNotFoundError:
            return
        except OSError as exc:
            log.bind(action="empty_kde_trash").debug(
                "Could not enumerate KDE trash directory %s: %s",
                directory,
                exc,
            )
            return

        for child in children:
            self._delete_path(child)

    def _delete_path(self, path: Path) -> None:
        try:
            if path.is_dir() and not path.is_symlink():
                for child in path.iterdir():
                    self._delete_path(child)
                path.rmdir()
            else:
                path.unlink()
        except OSError as exc:
            log.bind(action="empty_kde_trash").debug(
                "Could not delete KDE trash item %s: %s",
                path,
                exc,
            )


def select_trash_backend(*, desktop: Desktop | None = None) -> TrashBackend:
    """Select the trash backend for the current desktop session."""
    resolved = desktop if desktop is not None else detect_desktop()
    if is_kde_session(desktop=resolved):
        return KdeTrashBackend()
    if is_mate_session(desktop=resolved):
        return MateTrashBackend()
    if resolved & Desktop.CINNAMON:
        return CinnamonTrashBackend()
    if is_gnome_session(desktop=resolved):
        return GnomeTrashBackend()
    return GioTrashBackend()
