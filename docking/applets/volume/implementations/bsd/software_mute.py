"""Recover software-muted mixer channels across dock restarts."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import NamedTuple

from docking.core.paths import ensure_parent_dir
from docking.log import get_logger

log = get_logger("volume.bsd")


class SavedMute(NamedTuple):
    control: str
    levels: tuple[int, ...]


def load_mute(path: Path) -> SavedMute | None:
    """Read only a complete, supported recovery record."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
    ):
        return None
    control, levels = data.get("control"), data.get("levels")
    if (
        not isinstance(control, str)
        or not isinstance(levels, list)
        or not levels
        or any(type(level) is not int or not 0 <= level <= 255 for level in levels)
    ):
        return None
    return SavedMute(control, tuple(int(level) for level in levels))


def save_mute(path: Path, state: SavedMute | None) -> bool:
    """Publish recovery data atomically before zeroing the hardware channels."""
    temporary: Path | None = None
    try:
        if state is None:
            path.unlink(missing_ok=True)
            return True
        ensure_parent_dir(path)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(
                {"version": 1, "control": state.control, "levels": state.levels}, stream
            )
            stream.write("\n")
        temporary.replace(path)
        return True
    except OSError as exc:
        log.debug("Could not save software mute recovery: %s", exc.strerror)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                log.debug("Could not remove temporary mute recovery: %s", exc.strerror)
