"""Manage Docking's user entry for XDG desktop autostart."""

from __future__ import annotations

import stat
from configparser import ConfigParser, Error, SectionProxy
from pathlib import Path
from tempfile import TemporaryDirectory

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.platform.desktop.base import LoginStartup

_GROUP = "Desktop Entry"
_MANAGED_KEY = "X-Docking-Autostart"


class LinuxLoginStartup(LoginStartup):
    DESKTOP_ID = "org.docking.Docking.desktop"

    def __init__(self, directory: Path, *, command: str, try_exec: str) -> None:
        self._path = directory / self.DESKTOP_ID
        self._command = command
        self._try_exec = try_exec

    def enabled(self) -> bool:
        entry = self._read_entry()
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
            and app.get_string("X-GNOME-Autostart-enabled") != "false"
        )

    def set_enabled(self, enabled: bool) -> None:
        entry = self._read_entry()
        if not enabled and self._system_entry() is None:
            if entry is not None:
                self._path.unlink(missing_ok=True)
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

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".docking-", dir=self._path.parent) as work:
            candidate = Path(work) / self.DESKTOP_ID
            content, _ = desktop_entry.to_data()
            candidate.write_text(content, encoding="utf-8")
            if entry is None:
                # Publish the complete file without overwriting a concurrent entry.
                self._path.hardlink_to(candidate)
            else:
                candidate.replace(self._path)

    def _read_entry(self) -> SectionProxy | None:
        try:
            if not stat.S_ISREG(self._path.lstat().st_mode):
                raise FileExistsError(f"Startup entry already exists: {self._path}")
            parser = ConfigParser(interpolation=None)
            with self._path.open(encoding="utf-8") as stream:
                parser.read_file(stream)
        except FileNotFoundError:
            return None
        except (Error, UnicodeError) as exc:
            raise OSError(f"Could not read startup entry: {self._path}") from exc

        if parser.get(_GROUP, _MANAGED_KEY, fallback=None) != "true":
            raise FileExistsError(f"Startup entry already exists: {self._path}")
        return parser[_GROUP]

    def _system_entry(self) -> Path | None:
        for directory in GLib.get_system_config_dirs():
            path = Path(directory) / "autostart" / self.DESKTOP_ID
            if path.is_absolute() and path.is_file():
                return path
        return None

    @staticmethod
    def _entry_enabled(entry: SectionProxy) -> bool:
        return (
            entry.get("Hidden") != "true"
            and entry.get("X-GNOME-Autostart-enabled") != "false"
        )
