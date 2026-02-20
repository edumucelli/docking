"""Configuration loading, saving, and defaults for the dock."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "docking"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "dock.json"

DEFAULT_PINNED = [
    "org.gnome.Nautilus.desktop",
    "org.gnome.Terminal.desktop",
]


@dataclass
class Config:
    """Dock configuration with sensible defaults."""

    icon_size: int = 48
    zoom_enabled: bool = True
    zoom_percent: float = 2.0
    zoom_range: int = 3
    position: str = "bottom"
    autohide: bool = False
    hide_delay_ms: int = 500
    unhide_delay_ms: int = 0
    hide_time_ms: int = 250
    theme: str = "default"
    pinned: list[str] = field(default_factory=lambda: list(DEFAULT_PINNED))

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        """Load config from JSON file, falling back to defaults for missing keys."""
        path = Path(path) if path else DEFAULT_CONFIG_FILE
        if not path.exists():
            config = cls()
            config.save(path)
            return config

        with open(path) as f:
            data: dict[str, Any] = json.load(f)

        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def save(self, path: Path | str | None = None) -> None:
        """Save config to JSON file."""
        path = Path(path) if path else DEFAULT_CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.write("\n")
