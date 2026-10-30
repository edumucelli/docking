"""Native Windows process identity, without Unix procfs assumptions."""

import importlib
import re
from pathlib import Path, PureWindowsPath
from typing import Any

from docking.platform.applications.process_backend import ProcessBackend
from docking.platform.applications.steam import valid_steam_app_id


def _psutil() -> Any:
    """Keep the Windows-only runtime dependency lazy on other platforms."""
    return importlib.import_module("psutil")


class WindowsProcessBackend(ProcessBackend):
    def executable(self, pid: int) -> Path | None:
        util = _psutil()
        try:
            executable = util.Process(pid).exe()
        except (util.Error, OSError):
            return None
        return Path(executable) if executable else None

    def script(self, pid: int) -> str | None:
        util = _psutil()
        try:
            argv = util.Process(pid).cmdline()
        except (util.Error, OSError):
            return None
        if len(argv) < 2 or not re.fullmatch(
            r"python(?:w|\d+(?:\.\d+)*)?", PureWindowsPath(argv[0]).stem.lower()
        ):
            return None
        return (
            PureWindowsPath(argv[1]).name if argv[1].lower().endswith(".py") else None
        )

    def sandbox_app_id(self, pid: int) -> str | None:
        return None

    def steam_app_id(self, pid: int) -> str | None:
        util = _psutil()
        try:
            process = util.Process(pid)
            if not hasattr(process, "environ"):
                return None
            environment = process.environ()
        except (util.Error, OSError):
            return None
        ids = {
            valid_steam_app_id(environment[key])
            for key in ("SteamAppId", "SteamGameId")
            if key in environment
        } - {None}
        return next(iter(ids)) if len(ids) == 1 else None
