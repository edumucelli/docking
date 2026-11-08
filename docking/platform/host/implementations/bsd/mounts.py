"""BSD mounted-volume discovery."""

import importlib
from pathlib import Path
from typing import Any

from docking.log import get_logger

log = get_logger("trash.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


def mount_points() -> tuple[Path, ...]:
    try:
        return tuple(
            dict.fromkeys(
                Path(str(p.mountpoint)) for p in _psutil().disk_partitions(all=True)
            )
        )
    except Exception as exc:
        log.debug("Mount table unavailable: %s", exc)
        return ()
