"""Reusable executable-path observation without application registry policy."""

import importlib
from pathlib import Path
from typing import Any


def psutil_module() -> Any:
    """Load the optional native process dependency only when queried."""
    return importlib.import_module("psutil")


class NativeProcessReader:
    def executable(self, pid: int) -> Path | None:
        util = psutil_module()
        try:
            executable = util.Process(pid).exe()
        except (util.Error, OSError):
            return None
        return Path(executable) if executable else None
