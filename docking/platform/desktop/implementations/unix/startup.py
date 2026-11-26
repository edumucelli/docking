"""Manage Docking's user entry for XDG desktop autostart."""

from __future__ import annotations

import stat
from configparser import ConfigParser, Error, SectionProxy
from pathlib import Path
from tempfile import TemporaryDirectory

from docking.platform.desktop.base import LoginStartup

_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Docking
Exec=docking
TryExec=docking
Icon=org.docking.Docking
Terminal=false
X-Docking-Autostart=true
"""


class LinuxLoginStartup(LoginStartup):
    DESKTOP_ID = "org.docking.Docking.desktop"

    def __init__(self, directory: Path) -> None:
        self._path = directory / self.DESKTOP_ID

    def enabled(self) -> bool:
        entry = self._read_entry()
        return entry is not None and self._entry_enabled(entry)

    def set_enabled(self, enabled: bool) -> None:
        entry = self._read_entry()
        if not enabled:
            if entry is not None:
                self._path.unlink(missing_ok=True)
            return
        if entry is not None and self._entry_enabled(entry):
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".docking-", dir=self._path.parent) as work:
            candidate = Path(work) / self.DESKTOP_ID
            candidate.write_text(_ENTRY, encoding="utf-8")
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

        if parser.get("Desktop Entry", "X-Docking-Autostart", fallback=None) != "true":
            raise FileExistsError(f"Startup entry already exists: {self._path}")
        return parser["Desktop Entry"]

    @staticmethod
    def _entry_enabled(entry: SectionProxy) -> bool:
        return (
            entry.get("Hidden") != "true"
            and entry.get("X-GNOME-Autostart-enabled") != "false"
        )
