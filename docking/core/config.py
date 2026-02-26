"""Configuration loading, saving, and defaults for the dock."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from docking.core.position import Position

DEFAULT_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "docking"
)
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "dock.json"

DEFAULT_PINNED: list[str] = []


@dataclass
class Config:
    """Dock configuration with sensible defaults."""

    # Base icon size in pixels (before zoom)
    icon_size: int = 48
    # Whether parabolic zoom on hover is enabled
    zoom_enabled: bool = True
    # Max zoom multiplier (1.5 = 150%, Plank default)
    zoom_percent: float = 1.5
    # Number of icon widths over which the zoom tapers off
    zoom_range: int = 3
    # Screen edge where the dock is placed
    position: str = "bottom"
    # Whether the dock hides when the cursor leaves
    autohide: bool = False
    # Delay in ms before the dock starts hiding after cursor leaves (Plank default: 0)
    hide_delay_ms: int = 0
    # Delay in ms before the dock starts showing when cursor returns
    unhide_delay_ms: int = 0
    # Duration of the hide/show slide animation in ms
    hide_time_ms: int = 250
    # Whether to show window preview thumbnails on hover
    previews_enabled: bool = True
    # Theme name (loads from assets/themes/{name}.json)
    theme: str = "default"
    # Desktop file IDs of pinned applications, in display order
    pinned: list[str] = field(default_factory=lambda: list(DEFAULT_PINNED))
    # Per-applet preferences keyed by applet id (e.g. "clock")
    applet_prefs: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def pos(self) -> Position:
        """Position as enum."""
        return Position(self.position)

    def __post_init__(self) -> None:
        self._path: Path = DEFAULT_CONFIG_FILE

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        """Load config from JSON file, falling back to defaults for missing keys."""
        path = Path(path) if path else DEFAULT_CONFIG_FILE
        if not path.exists():
            config = cls()
            config._path = path
            config.save(path)
            return config

        with open(path) as f:
            data: dict[str, Any] = json.load(f)

        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        config = cls(**filtered)
        config._path = path
        # Validate position (fallback to default if unknown)
        try:
            Position(config.position)
        except ValueError:
            config.position = "bottom"
        return config

    def save(self, path: Path | str | None = None) -> None:
        """Save config to JSON file."""
        path = Path(path) if path else self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.write("\n")
