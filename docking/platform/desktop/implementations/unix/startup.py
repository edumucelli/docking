"""Manage Docking's user entry for XDG desktop autostart."""

from __future__ import annotations

import stat
from pathlib import Path
from tempfile import TemporaryDirectory

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.platform.applications.implementations.unix.entries import (
    desktop_entry_bool,
    desktop_entry_string,
)
from docking.platform.desktop.base import LoginStartup

_GROUP = "Desktop Entry"
_MANAGED_KEY = "X-Docking-Autostart"
_GNOME_ENABLED = "X-GNOME-Autostart-enabled"


class LinuxLoginStartup(LoginStartup):
    DESKTOP_ID = "org.docking.Docking.desktop"

    def __init__(self, directory: Path, *, command: str, try_exec: str) -> None:
        self._path = directory / self.DESKTOP_ID
        self._command = command
        self._try_exec = try_exec

    def enabled(self) -> bool:
        _, entry = self._user_entry()
        if entry is not None:
            return self._entry_enabled(entry)

        system_entry = self._system_entry()
        if system_entry is None:
            return False
        try:
            app = Gio.DesktopAppInfo.new_from_filename(str(system_entry))
        except TypeError:
            return False
        return (
            app is not None
            and not app.get_is_hidden()
            and app.get_show_in(None)
            and app.get_string(_GNOME_ENABLED) != "false"
        )

    def set_enabled(self, enabled: bool) -> None:
        path, entry = self._user_entry()
        if entry is not None and not desktop_entry_bool(entry, _MANAGED_KEY):
            if self._entry_enabled(entry) != enabled:
                entry.set_boolean(_GROUP, "Hidden", not enabled)
                if desktop_entry_string(entry, _GNOME_ENABLED):
                    entry.set_boolean(_GROUP, _GNOME_ENABLED, enabled)
                self._write_entry(path, entry, replace=True)
            return
        if not enabled and self._system_entry() is None:
            if entry is not None:
                path.unlink(missing_ok=True)
            return
        if enabled and entry is not None and self._entry_enabled(entry):
            return

        desktop_entry = GLib.KeyFile()
        desktop_entry.set_string(_GROUP, "Type", "Application")
        desktop_entry.set_string(_GROUP, "Name", "Docking")
        desktop_entry.set_string(_GROUP, "Exec", self._command)
        desktop_entry.set_string(_GROUP, "TryExec", self._try_exec)
        desktop_entry.set_string(_GROUP, "Icon", "org.docking.Docking")
        desktop_entry.set_boolean(_GROUP, "Terminal", False)
        desktop_entry.set_boolean(_GROUP, _MANAGED_KEY, True)
        if not enabled:
            desktop_entry.set_boolean(_GROUP, "Hidden", True)
        self._write_entry(path, desktop_entry, replace=entry is not None)

    @staticmethod
    def _write_entry(path: Path, desktop_entry: GLib.KeyFile, *, replace: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".docking-", dir=path.parent) as work:
            candidate = Path(work) / path.name
            content, _ = desktop_entry.to_data()
            candidate.write_text(content, encoding="utf-8")
            if replace:
                candidate.chmod(stat.S_IMODE(path.lstat().st_mode))
                candidate.replace(path)
            else:
                # Publish the complete file without overwriting a concurrent entry.
                path.hardlink_to(candidate)

    def _user_entry(self) -> tuple[Path, GLib.KeyFile | None]:
        entry = self._read_entry(self._path)
        if entry is not None:
            if desktop_entry_bool(entry, _MANAGED_KEY) or self._is_docking(entry):
                return self._path, entry
            raise FileExistsError(f"Startup entry already exists: {self._path}")

        legacy_path = self._path.with_name("docking.desktop")
        entry = self._read_entry(legacy_path)
        if entry is not None and self._is_docking(entry):
            return legacy_path, entry
        return self._path, None

    @staticmethod
    def _read_entry(path: Path) -> GLib.KeyFile | None:
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise FileExistsError(f"Startup entry already exists: {path}")
        except FileNotFoundError:
            return None

        entry = GLib.KeyFile()
        try:
            entry.load_from_file(
                str(path),
                GLib.KeyFileFlags.KEEP_COMMENTS | GLib.KeyFileFlags.KEEP_TRANSLATIONS,
            )
        except GLib.Error as exc:
            raise OSError(f"Could not read startup entry: {path}") from exc
        return entry

    def _is_docking(self, entry: GLib.KeyFile) -> bool:
        return (
            desktop_entry_string(entry, "Type") == "Application"
            and desktop_entry_string(entry, "Name") == "Docking"
            and desktop_entry_string(entry, "Exec") in {"docking", self._command}
        )

    def _system_entry(self) -> Path | None:
        for directory in GLib.get_system_config_dirs():
            path = Path(directory) / "autostart" / self.DESKTOP_ID
            if path.is_absolute() and path.is_file():
                return path
        return None

    @staticmethod
    def _entry_enabled(entry: GLib.KeyFile) -> bool:
        return (
            not desktop_entry_bool(entry, "Hidden")
            and desktop_entry_string(entry, _GNOME_ENABLED) != "false"
        )
