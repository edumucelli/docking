"""Linux sysfs backlight capability."""

from pathlib import Path

from docking.applets.brightness.backend import BacklightSource
from docking.log import get_logger

log = get_logger("brightness")

_BACKLIGHT_DIR = Path("/sys/class/backlight")


def _find_sysfs_backlight() -> Path | None:
    """Find the first sysfs backlight directory, if any."""
    try:
        for entry in sorted(_BACKLIGHT_DIR.iterdir()):
            if (entry / "brightness").is_file() and (
                entry / "max_brightness"
            ).is_file():
                return entry
    except OSError as exc:
        log.debug("Failed to inspect %s: %s", _BACKLIGHT_DIR, exc)
    return None


def _get_brightness_sysfs(path: Path) -> float | None:
    """Read brightness from /sys/class/backlight as a 0.0-1.0 fraction."""
    try:
        current = int((path / "brightness").read_text().strip())
        maximum = int((path / "max_brightness").read_text().strip())
        if maximum > 0:
            return current / maximum
    except (OSError, ValueError) as exc:
        log.warning("Failed to read sysfs backlight: %s", exc)
    return None


def _set_brightness_sysfs(path: Path, value: float) -> bool:
    """Write brightness to /sys/class/backlight as an integer level."""
    try:
        maximum = int((path / "max_brightness").read_text().strip())
        if maximum <= 0:
            return False
        current = max(1, round(maximum * value))
        (path / "brightness").write_text(f"{current}\n")
        return True
    except (OSError, ValueError) as exc:
        log.warning("Failed to write sysfs backlight: %s", exc)
    return False


class LinuxBacklightSource(BacklightSource):
    def find(self) -> Path | None:
        return _find_sysfs_backlight()

    def read(self, path: Path) -> float | None:
        return _get_brightness_sysfs(path)

    def write(self, path: Path, value: float) -> bool:
        return _set_brightness_sysfs(path, value)
