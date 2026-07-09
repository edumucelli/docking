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
import errno
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class TrashEmptyResult:
    """Outcome of an Empty Trash request as far as Docking can observe it."""

    attempted: bool
    before_count: int
    remaining_count: int
    failures: int = 0
    permission_denied: bool = False
    delegated: bool = False


@dataclass(slots=True)
class _DeleteStats:
    failures: int = 0
    permission_denied: bool = False

    def add_failure(self, exc: BaseException) -> None:
        self.failures += 1
        self.permission_denied = self.permission_denied or _is_permission_error(exc)

    def merge(self, other: _DeleteStats) -> None:
        self.failures += other.failures
        self.permission_denied = self.permission_denied or other.permission_denied


def _coerce_delete_stats(stats: object) -> _DeleteStats:
    if isinstance(stats, _DeleteStats):
        return stats
    return _DeleteStats()


def _is_permission_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM):
        return True
    message = str(exc).casefold()
    return "permission denied" in message or "operation not permitted" in message


class TrashBackend(Protocol):
    """Desktop-specific trash behavior used by the Trash applet."""

    name: str
    uri: str

    def count_items(self) -> int: ...

    def monitor_files(self) -> tuple[Gio.File, ...]: ...

    def open(self) -> None: ...

    def empty(self, confirm: Callable[[], bool]) -> TrashEmptyResult: ...


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _host_user_data_home() -> Path:
    return Path.home() / ".local" / "share"


def _visible_trash_directory() -> Path:
    if is_flatpak():
        return _host_user_data_home() / "Trash"
    return xdg_data_home() / "Trash"


def _kde_trash_directory() -> Path:
    return xdg_data_home() / "Trash"


def _kde_trash_files_directory() -> Path:
    return _kde_trash_directory() / "files"


def _kde_trash_info_directory() -> Path:
    return _kde_trash_directory() / "info"


def _visible_trash_files_directory() -> Path:
    return _visible_trash_directory() / "files"


def _visible_trash_info_directory() -> Path:
    return _visible_trash_directory() / "info"


def _decode_mountinfo_path(value: str) -> str:
    decoded = ""
    index = 0
    while index < len(value):
        if (
            value[index] == "\\"
            and index + 3 < len(value)
            and all(char in "01234567" for char in value[index + 1 : index + 4])
        ):
            decoded += chr(int(value[index + 1 : index + 4], 8))
            index += 4
            continue
        decoded += value[index]
        index += 1
    return decoded


def _mount_points() -> tuple[Path, ...]:
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.bind(action="discover_trash_roots").debug(
            "Could not read mountinfo: %s",
            exc,
        )
        return ()

    mount_points: list[Path] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 5:
            continue
        mount_point = Path(_decode_mountinfo_path(fields[4]))
        if mount_point not in mount_points:
            mount_points.append(mount_point)
    return tuple(mount_points)


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError as exc:
        log.bind(action="discover_trash_roots").debug(
            "Could not stat trash path %s: %s",
            path,
            exc,
        )
        return False


def _volume_trash_directories() -> tuple[Path, ...]:
    uid = os.getuid()
    directories: list[Path] = []
    for mount_point in _mount_points():
        for directory in (
            mount_point / f".Trash-{uid}",
            mount_point / ".Trash" / str(uid),
        ):
            if _path_exists(directory) and directory not in directories:
                directories.append(directory)
    return tuple(directories)


def _visible_trash_directories() -> tuple[Path, ...]:
    directories = [_visible_trash_directory()]
    for directory in _volume_trash_directories():
        if directory not in directories:
            directories.append(directory)
    return tuple(directories)


def _visible_trash_files_directories() -> tuple[Path, ...]:
    return tuple(directory / "files" for directory in _visible_trash_directories())


def _visible_trash_info_directories() -> tuple[Path, ...]:
    return tuple(directory / "info" for directory in _visible_trash_directories())


def _count_visible_trash_files(*, action: str) -> int:
    count = 0
    for files_dir in _visible_trash_files_directories():
        try:
            count += sum(1 for _child in files_dir.iterdir())
        except OSError as exc:
            log.bind(action=action).debug("Could not enumerate trash files: %s", exc)
    return count


def _kde_kiorc_file() -> Path:
    if is_flatpak():
        return Path.home() / ".config" / "kiorc"
    return xdg_config_home() / "kiorc"


def _open_trash_uri(
    uri: str, *, fallback_commands: tuple[tuple[str, ...], ...] = ()
) -> None:
    if is_flatpak() and _open_host_trash_uri(uri=uri):
        return

    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return
    except GLib.Error as exc:
        gio_error = exc

    if _open_trash_with_commands(uri=uri, commands=fallback_commands):
        return

    log.bind(action="open_trash").warning("Failed to open trash: %s", gio_error)


def _open_trash_with_commands(
    *, uri: str, commands: tuple[tuple[str, ...], ...]
) -> bool:
    for command in (*commands, ("gio", "open", uri), ("xdg-open", uri)):
        resolved = _available_open_command(command)
        if resolved is None:
            continue
        try:
            subprocess.Popen(resolved)
            return True
        except OSError as exc:
            log.bind(action="open_trash").debug(
                "Failed to open trash with %s: %s",
                resolved[0],
                exc,
            )

    files_dir = _visible_trash_files_directory()
    if not files_dir.exists():
        return False
    resolved = _available_open_command(("xdg-open", str(files_dir)))
    if resolved is None:
        return False
    try:
        subprocess.Popen(resolved)
        return True
    except OSError as exc:
        log.bind(action="open_trash").debug(
            "Failed to open trash files directory with %s: %s",
            resolved[0],
            exc,
        )
        return False


def _available_open_command(command: tuple[str, ...]) -> tuple[str, ...] | None:
    if is_flatpak():
        host_command = flatpak.host_command(list(command))
        if flatpak.host_command_available(command[0]) and host_command is not None:
            return tuple(host_command)
        return None
    if shutil.which(command[0]):
        return command
    return None


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
    use_visible_trash_files = False
    confirm_trash_key = "confirm-trash"
    dbus_targets: tuple[tuple[str, str], ...] = (
        ("org.mate.Caja", "/org/mate/Caja"),
        ("org.gnome.Nautilus", "/org/gnome/Nautilus"),
    )
    confirmation_schema: tuple[str, str] | None = None
    open_commands: tuple[tuple[str, ...], ...] = ()

    def count_items(self) -> int:
        if is_flatpak() or self.use_visible_trash_files:
            return _count_visible_trash_files(action="count_items")

        trash = Gio.File.new_for_uri(self.uri)
        try:
            enumerator = trash.enumerate_children(
                Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE, None
            )
        except GLib.Error as exc:
            log.bind(action="count_items").debug(
                "Could not enumerate trash items: %s", exc
            )
            return _count_visible_trash_files(action="count_items_fallback")

        count = 0
        while enumerator.next_file(None) is not None:
            count += 1
        enumerator.close(None)
        return count

    def monitor_files(self) -> tuple[Gio.File, ...]:
        if is_flatpak() or self.use_visible_trash_files:
            return tuple(
                Gio.File.new_for_path(str(path))
                for path in _visible_trash_files_directories()
            )
        return (Gio.File.new_for_uri(self.uri),)

    def open(self) -> None:
        _open_trash_uri(self.uri, fallback_commands=self.open_commands)

    def empty(self, confirm: Callable[[], bool]) -> TrashEmptyResult:
        _ = confirm
        before_count = self.count_items()
        if self._confirmation_preference() is False:
            if _empty_host_trash():
                return self._empty_result(before_count=before_count)
            stats = _coerce_delete_stats(self._delete_contents())
            return self._empty_result(before_count=before_count, stats=stats)
        if self._empty_via_dbus():
            return TrashEmptyResult(
                attempted=False,
                before_count=before_count,
                remaining_count=self.count_items(),
                delegated=True,
            )
        if _empty_host_trash():
            return self._empty_result(before_count=before_count)
        stats = _coerce_delete_stats(self._delete_contents())
        return self._empty_result(before_count=before_count, stats=stats)

    def _empty_result(
        self,
        *,
        before_count: int,
        stats: _DeleteStats | None = None,
    ) -> TrashEmptyResult:
        stats = stats or _DeleteStats()
        return TrashEmptyResult(
            attempted=True,
            before_count=before_count,
            remaining_count=self.count_items(),
            failures=stats.failures,
            permission_denied=stats.permission_denied,
        )

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

    def _delete_contents(self) -> _DeleteStats:
        if self.use_visible_trash_files:
            return self._delete_visible_trash_contents()

        stats = _DeleteStats()
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
            stats.add_failure(exc)
            stats.merge(self._delete_visible_trash_contents())
            return stats

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
                stats.add_failure(exc)
        enumerator.close(None)
        return stats

    def _delete_visible_trash_contents(self) -> _DeleteStats:
        stats = _DeleteStats()
        for directory in _visible_trash_files_directories():
            stats.merge(self._delete_directory_contents(directory))
        for directory in _visible_trash_info_directories():
            stats.merge(self._delete_directory_contents(directory))
        return stats

    def _delete_directory_contents(self, directory: Path) -> _DeleteStats:
        stats = _DeleteStats()
        try:
            children = list(directory.iterdir())
        except FileNotFoundError:
            return stats
        except OSError as exc:
            log.bind(action="empty_trash_delete").debug(
                "Could not enumerate trash directory %s: %s",
                directory,
                exc,
            )
            stats.add_failure(exc)
            return stats

        for child in children:
            stats.merge(self._delete_path(child))
        return stats

    def _delete_path(self, path: Path) -> _DeleteStats:
        stats = _DeleteStats()
        try:
            if path.is_dir() and not path.is_symlink():
                try:
                    children = list(path.iterdir())
                except OSError as exc:
                    log.bind(action="empty_trash_delete").debug(
                        "Could not enumerate trash item %s: %s",
                        path,
                        exc,
                    )
                    stats.add_failure(exc)
                    return stats
                for child in children:
                    stats.merge(self._delete_path(child))
                path.rmdir()
            else:
                path.unlink()
        except OSError as exc:
            log.bind(action="empty_trash_delete").debug(
                "Could not delete trash item %s: %s",
                path,
                exc,
            )
            stats.add_failure(exc)
        return stats


class GnomeTrashBackend(GioTrashBackend):
    """Trash backend for GNOME/Nautilus sessions."""

    name = "gnome"
    dbus_targets = (("org.gnome.Nautilus", "/org/gnome/Nautilus"),)
    open_commands = (("nautilus", "trash:///"),)


class MateTrashBackend(GioTrashBackend):
    """Trash backend for MATE/Caja sessions."""

    name = "mate"
    use_visible_trash_files = True
    dbus_targets = (("org.mate.Caja", "/org/mate/Caja"),)
    open_commands = (("caja", "trash:///"),)
    confirmation_schema = (
        "org.mate.caja.preferences",
        "/org/mate/caja/preferences/",
    )


class CinnamonTrashBackend(GioTrashBackend):
    """Trash backend for Cinnamon/Nemo sessions."""

    name = "cinnamon"
    open_commands = (("nemo", "trash:///"),)
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
        return _count_visible_trash_files(action="count_kde_items")

    def monitor_files(self) -> tuple[Gio.File, ...]:
        return tuple(
            Gio.File.new_for_path(str(path))
            for path in _visible_trash_files_directories()
        )

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

    def empty(self, confirm: Callable[[], bool]) -> TrashEmptyResult:
        before_count = self.count_items()
        if self._confirmation_preference() is False or confirm():
            if _empty_host_trash():
                return self._empty_result(before_count=before_count)
            stats = _coerce_delete_stats(self._delete_contents())
            return self._empty_result(before_count=before_count, stats=stats)
        return TrashEmptyResult(
            attempted=False,
            before_count=before_count,
            remaining_count=before_count,
        )

    def _empty_result(
        self,
        *,
        before_count: int,
        stats: _DeleteStats | None = None,
    ) -> TrashEmptyResult:
        stats = stats or _DeleteStats()
        return TrashEmptyResult(
            attempted=True,
            before_count=before_count,
            remaining_count=self.count_items(),
            failures=stats.failures,
            permission_denied=stats.permission_denied,
        )

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

    def _delete_contents(self) -> _DeleteStats:
        stats = _DeleteStats()
        for directory in _visible_trash_files_directories():
            stats.merge(self._delete_directory_contents(directory))
        for directory in _visible_trash_info_directories():
            stats.merge(self._delete_directory_contents(directory))
        return stats

    def _delete_directory_contents(self, directory: Path) -> _DeleteStats:
        stats = _DeleteStats()
        try:
            children = list(directory.iterdir())
        except FileNotFoundError:
            return stats
        except OSError as exc:
            log.bind(action="empty_kde_trash").debug(
                "Could not enumerate KDE trash directory %s: %s",
                directory,
                exc,
            )
            stats.add_failure(exc)
            return stats

        for child in children:
            stats.merge(self._delete_path(child))
        return stats

    def _delete_path(self, path: Path) -> _DeleteStats:
        stats = _DeleteStats()
        try:
            if path.is_dir() and not path.is_symlink():
                try:
                    children = list(path.iterdir())
                except OSError as exc:
                    log.bind(action="empty_kde_trash").debug(
                        "Could not enumerate KDE trash item %s: %s",
                        path,
                        exc,
                    )
                    stats.add_failure(exc)
                    return stats
                for child in children:
                    stats.merge(self._delete_path(child))
                path.rmdir()
            else:
                path.unlink()
        except OSError as exc:
            log.bind(action="empty_kde_trash").debug(
                "Could not delete KDE trash item %s: %s",
                path,
                exc,
            )
            stats.add_failure(exc)
        return stats


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
