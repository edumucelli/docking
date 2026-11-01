"""Linux mounted-volume discovery."""

import os
from pathlib import Path

from docking.log import get_logger, with_context

log = with_context(get_logger("trash.linux"))


def _decode_mountinfo_path(value: str) -> str:
    decoded = ""
    index = 0
    while index < len(value):
        if (
            value[index] == "\\"
            and index + 3 < len(value)
            and all(char in "01234567" for char in value[index + 1 : index + 4])
        ):
            decoded += chr(int(value[index + 1 : index + 4], 8))
            index += 4
            continue
        decoded += value[index]
        index += 1
    return decoded


def mount_points() -> tuple[Path, ...]:
    try:
        lines = Path("/proc/self/mountinfo").read_bytes().split(b"\n")
    except OSError as exc:
        log.bind(action="discover_trash_roots").debug(
            "Could not read mountinfo: %s",
            exc,
        )
        return ()

    mount_points: list[Path] = []
    for line in lines:
        fields = line.split(b" ")
        if len(fields) < 5:
            continue
        mount_point = Path(_decode_mountinfo_path(os.fsdecode(fields[4])))
        if mount_point not in mount_points:
            mount_points.append(mount_point)
    return tuple(mount_points)
