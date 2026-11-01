"""Linux process identity from bounded procfs evidence."""

import configparser
import re
from pathlib import Path

from docking.platform.applications.backend import ProcessBackend
from docking.platform.applications.implementations.unix.flatpak import (
    valid_flatpak_app_id,
)
from docking.platform.applications.steam import valid_steam_app_id


def _process_executable_path(
    pid: int, *, proc_root: Path = Path("/proc")
) -> Path | None:
    """Resolve a process executable through its host's native interface."""
    try:
        path = (proc_root / str(pid) / "exe").resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None


def _bounded_process_file(path: Path, limit: int) -> bytes | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except (OSError, RuntimeError):
        return None
    return data if len(data) <= limit else None


def _process_sandbox_app_id(process_dir: Path) -> str | None:
    """Read the Flatpak identity in the process's own mount namespace."""
    data = _bounded_process_file(process_dir / "root" / ".flatpak-info", 65536)
    if data is None:
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode("utf-8"))
        app_id = parser.get("Application", "name", fallback="").strip()
    except (UnicodeDecodeError, configparser.Error):
        return None
    return app_id if valid_flatpak_app_id(app_id) else None


def _process_python_script(process_dir: Path) -> str | None:
    """Read only the script basename from a direct Python invocation."""
    data = _bounded_process_file(process_dir / "cmdline", 4096)
    if data is None:
        return None
    try:
        argv = data.rstrip(b"\0").decode("utf-8").split("\0")
    except UnicodeDecodeError:
        return None
    if len(argv) < 2 or not re.fullmatch(
        r"python(?:\d+(?:\.\d+)*)?", Path(argv[0]).name
    ):
        return None
    script = argv[1]
    if script.startswith("-") or not script.endswith(".py"):
        return None
    return Path(script).name


def _process_steam_app_id(process_dir: Path) -> str | None:
    """Retain only an allowlisted Steam ID, never the process environment."""
    data = _bounded_process_file(process_dir / "environ", 131072)
    if data is None:
        return None
    identities: set[str] = set()
    for item in data.split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator and key in {b"SteamAppId", b"SteamGameId"}:
            try:
                app_id = valid_steam_app_id(value.decode("ascii"))
            except UnicodeDecodeError:
                continue
            if app_id is not None:
                identities.add(app_id)
    return next(iter(identities)) if len(identities) == 1 else None


class LinuxProcessBackend(ProcessBackend):
    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        self._proc_root = proc_root

    def executable(self, pid: int) -> Path | None:
        return _process_executable_path(pid, proc_root=self._proc_root)

    def script(self, pid: int) -> str | None:
        return _process_python_script(self._proc_root / str(pid))

    def sandbox_app_id(self, pid: int) -> str | None:
        return _process_sandbox_app_id(self._proc_root / str(pid))

    def steam_app_id(self, pid: int) -> str | None:
        return _process_steam_app_id(self._proc_root / str(pid))
