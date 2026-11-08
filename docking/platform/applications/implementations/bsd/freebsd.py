"""FreeBSD native process executable evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psutil import Process


def executable_path(process: Process) -> str:
    return str(process.exe())
