"""Persistent executable launchers, independent of the desktop file format."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from docking.platform.environment import is_windows

from . import entries
from .types import ApplicationInfo


class ExecutableRegistrationBackend(Protocol):
    def create(self, target: str | Path, *, startup_wm_class: str) -> str | None: ...

    def applications(self) -> tuple[ApplicationInfo, ...]: ...

    def directories(self) -> tuple[Path, ...]: ...


def executable_format(path: Path) -> Literal["elf", "pe"] | None:
    """Read bounded image headers without executing a prospective launcher."""
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if header[:4] == b"\x7fELF":
                return "elf"
            if len(header) < 64 or header[:2] != b"MZ":
                return None
            stream.seek(int.from_bytes(header[0x3C:0x40], "little"))
            return "pe" if stream.read(4) == b"PE\0\0" else None
    except OSError:
        return None


def executable_application_id(path: Path) -> str:
    if is_windows():
        from .windows.registration import executable_id

        return executable_id(path)
    return entries.generated_desktop_id_for_path(path)


def create_registration_backend() -> ExecutableRegistrationBackend:
    if is_windows():
        from .windows.registration import WindowsExecutableRegistration

        return WindowsExecutableRegistration()
    from .unix_registration import UnixExecutableRegistration

    return UnixExecutableRegistration()
