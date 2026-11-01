"""Native Windows process identity, without Unix procfs assumptions."""

import re
from pathlib import PureWindowsPath

from docking.platform.applications.backend import ProcessBackend
from docking.platform.applications.steam import valid_steam_app_id
from docking.platform.native.windows import process as native_process
from docking.platform.native.windows.process import NativeProcessReader


class WindowsProcessBackend(NativeProcessReader, ProcessBackend):
    def script(self, pid: int) -> str | None:
        util = native_process.psutil_module()
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
        util = native_process.psutil_module()
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
