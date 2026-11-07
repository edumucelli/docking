"""OpenBSD native process executable evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psutil import Process


def executable_path(process: Process) -> str:
    # OpenBSD cannot query a kernel executable path. psutil.exe() guesses
    # via the observer's PATH, which can misidentify another executable.
    argv = process.cmdline()
    return str(argv[0]) if argv else ""
