"""BSD process identity through kernel-supported process evidence."""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import Any

from docking.log import get_logger
from docking.platform.applications.backend import ProcessBackend

log = get_logger("applications.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


def process_executable(pid: int) -> Path | None:
    try:
        process = _psutil().Process(pid)
        if sys.platform.startswith("openbsd"):
            # OpenBSD cannot query a kernel executable path. psutil.exe() guesses
            # via the observer's PATH, which can misidentify another executable.
            argv = process.cmdline()
            value = str(argv[0]) if argv else ""
        else:
            value = str(process.exe())
        path = Path(value)
        return (
            path.resolve() if value and path.is_absolute() and path.is_file() else None
        )
    except Exception as exc:
        # Processes can exit or deny access between enumeration and this read.
        log.debug("Process executable unavailable for %d: %s", pid, exc)
        return None


def process_script(pid: int) -> str | None:
    try:
        argv = _psutil().Process(pid).cmdline()
        if len(argv) < 2 or not re.fullmatch(
            r"python(?:\d+(?:\.\d+)*)?", Path(str(argv[0])).name
        ):
            return None
        script = str(argv[1])
        return (
            Path(script).name
            if not script.startswith("-") and script.endswith(".py")
            else None
        )
    except Exception as exc:
        log.debug("Process command unavailable for %d: %s", pid, exc)
        return None


class BsdProcessBackend(ProcessBackend):
    def executable(self, pid: int) -> Path | None:
        return process_executable(pid)

    def script(self, pid: int) -> str | None:
        return process_script(pid)

    def sandbox_app_id(self, pid: int) -> str | None:
        return None

    def steam_app_id(self, pid: int) -> str | None:
        # Steam's Linux process environment is not a native BSD identity source.
        return None
