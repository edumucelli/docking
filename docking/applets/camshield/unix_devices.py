"""Filesystem camera-device helpers shared by Unix implementations."""

from pathlib import Path


def _video_devices(dev_root: Path) -> dict[str, Path]:
    try:
        entries = dev_root.glob("video*")
        return {
            entry.name: _normalized_path(entry)
            for entry in entries
            if entry.name.startswith("video") and entry.name[5:].isdigit()
        }
    except OSError:
        return {}


def _normalized_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()
