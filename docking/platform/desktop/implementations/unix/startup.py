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
        return self._effective_enabled(self._read_entry())

    def refresh_launcher(self) -> None:
        entry = self._read_entry()
        if (
            entry is None
            or not self._entry_unhidden(entry)
            or not self._launcher_changed(entry)
        ):
            return
        self._set_launcher(entry)
        self._write_entry(entry, replace=True)

    def _effective_enabled(self, entry: GLib.KeyFile | None) -> bool:
        if entry is not None:
            return (
                self._entry_unhidden(entry)
                and not self._launcher_changed(entry)
                and self._allows_desktop(entry)
            )

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
        entry = self._read_entry()
        if enabled and self._effective_enabled(entry):
            return
        if not enabled and self._system_entry() is None:
            if entry is not None:
                self._path.unlink(missing_ok=True)
            return
        if not enabled and entry is not None and desktop_entry_bool(entry, "Hidden"):
            return

        replace = entry is not None
        if entry is None:
            entry = GLib.KeyFile()
            entry.set_string(_GROUP, "Type", "Application")
            entry.set_string(_GROUP, "Name", "Docking")
            entry.set_string(_GROUP, "Icon", "org.docking.Docking")
            entry.set_boolean(_GROUP, "Terminal", False)
            entry.set_boolean(_GROUP, _MANAGED_KEY, True)
        self._set_launcher(entry)
        if not enabled:
            entry.set_boolean(_GROUP, "Hidden", True)
        elif desktop_entry_bool(entry, "Hidden"):
            entry.remove_key(_GROUP, "Hidden")
        if enabled and desktop_entry_string(entry, _GNOME_ENABLED) == "false":
            entry.set_boolean(_GROUP, _GNOME_ENABLED, True)
        if enabled and not self._allows_desktop(entry):
            raise OSError(f"Startup entry excludes the current desktop: {self._path}")
        self._write_entry(entry, replace=replace)

    def _set_launcher(self, entry: GLib.KeyFile) -> None:
        entry.set_string(_GROUP, "Exec", self._command)
        entry.set_string(_GROUP, "TryExec", self._try_exec)

    def _launcher_changed(self, entry: GLib.KeyFile) -> bool:
        return (
            desktop_entry_string(entry, "Exec") != self._command
            or desktop_entry_string(entry, "TryExec") != self._try_exec
        )

    def _write_entry(self, desktop_entry: GLib.KeyFile, *, replace: bool) -> None:
        path = self._path
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

    def _read_entry(self) -> GLib.KeyFile | None:
        path = self._path
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
        if not desktop_entry_bool(entry, _MANAGED_KEY):
            raise FileExistsError(f"Startup entry already exists: {path}")
        return entry

    def _system_entry(self) -> Path | None:
        for directory in GLib.get_system_config_dirs():
            path = Path(directory) / "autostart" / self.DESKTOP_ID
            if path.is_absolute() and path.is_file():
                return path
        return None

    @staticmethod
    def _entry_unhidden(entry: GLib.KeyFile) -> bool:
        return (
            not desktop_entry_bool(entry, "Hidden")
            and desktop_entry_string(entry, _GNOME_ENABLED) != "false"
        )

    @staticmethod
    def _allows_desktop(entry: GLib.KeyFile) -> bool:
        keys, _ = entry.get_keys(_GROUP)
        if not {"OnlyShowIn", "NotShowIn"}.intersection(keys):
            return True
        try:
            app = Gio.DesktopAppInfo.new_from_keyfile(entry)
        except TypeError:
            return False
        return app is not None and app.get_show_in(None)
