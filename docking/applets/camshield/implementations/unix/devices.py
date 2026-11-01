"""Unix camera device discovery."""

from pathlib import Path


def enumerate_video_devices(dev_root: Path) -> dict[str, Path]:
    try:
        entries = dev_root.glob("video*")
        return {
            entry.name: normalized_path(entry)
            for entry in entries
            if entry.name.startswith("video") and entry.name[5:].isdigit()
        }
    except OSError:
        return {}


def normalized_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()
