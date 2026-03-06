"""Configuration loading, saving, and defaults for the dock."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from docking.applets.identity import is_applet_desktop_id
from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND, ItemKind
from docking.core.position import Position

DEFAULT_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "docking"
)
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "dock.json"

DEFAULT_PINNED: list["PinnedEntry"] = []


@dataclass
class PinnedEntry:
    """Structured persisted dock entry."""

    kind: ItemKind
    target: str

    @property
    def id(self) -> str:
        return self.target

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "target": self.target}

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.target == other
        if isinstance(other, dict):
            return self.to_dict() == other
        if isinstance(other, PinnedEntry):
            return self.kind == other.kind and self.target == other.target
        return False

    @classmethod
    def from_raw(cls, raw: object) -> PinnedEntry | None:
        if isinstance(raw, str):
            if raw.startswith("file://"):
                return cls(
                    kind=FOLDER_KIND if _uri_is_dir(raw) else FILE_KIND,
                    target=raw,
                )
            return cls(
                kind=APPLET_KIND if is_applet_desktop_id(desktop_id=raw) else APP_KIND,
                target=raw,
            )
        if not isinstance(raw, dict):
            return None

        kind_raw = raw.get("kind")
        target_raw = raw.get("target")
        if not isinstance(kind_raw, str) or not isinstance(target_raw, str):
            return None
        if kind_raw not in {APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND}:
            return None
        return cls(kind=kind_raw, target=target_raw)


def _uri_is_dir(target: str) -> bool:
    if not target.startswith("file://"):
        return False
    try:
        return Path(unquote(urlparse(target).path)).is_dir()
    except ValueError:
        return False


@dataclass
class Config:
    """Dock configuration with sensible defaults."""

    # Base icon size in pixels (before zoom)
    icon_size: int = 48
    # Whether parabolic zoom on hover is enabled
    zoom_enabled: bool = True
    # Max zoom multiplier (1.0-4.0, default 1.5 = 150%)
    zoom_percent: float = 1.5
    # Number of icon widths over which the zoom tapers off
    zoom_range: int = 3
    # Screen edge where the dock is placed
    position: str = "bottom"
    # Target monitor index (-1 means "primary monitor")
    monitor_index: int = -1
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
    # Whether icon reordering, drag-in, and drag-off removal are locked
    lock_icons: bool = False
    # Only show running apps from the active workspace
    current_workspace_only: bool = False
    # Whether applets are anchored to the end of the dock
    anchor_applets: bool = False
    # Whether file/folder entries are anchored to the end independently
    anchor_files: bool = False
    # Whether to show hover tooltips for dock items
    tooltips_enabled: bool = True
    # Whether the dock follows the cursor across monitors
    active_display: bool = False
    # Theme name (loads from assets/themes/{name}.json)
    theme: str = "default"
    # Gap between dock edge and screen edge (0 = flush, like Plank GapSize)
    gap_size: int = 0
    # Typed pinned entries in display order.
    pinned: list[PinnedEntry] = field(default_factory=lambda: list(DEFAULT_PINNED))
    # Per-applet preferences keyed by applet id (e.g. "clock")
    applet_prefs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Per-item preferences keyed by stable target URI/id.
    item_prefs: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def pos(self) -> Position:
        """Position as enum."""
        return Position(value=self.position)

    def __post_init__(self) -> None:
        self._path: Path = DEFAULT_CONFIG_FILE
        if self.pinned and not isinstance(self.pinned[0], PinnedEntry):
            self.pinned = normalize_pinned_entries(list(self.pinned))

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        """Load config from JSON file, falling back to defaults for missing keys."""
        path = Path(path) if path else DEFAULT_CONFIG_FILE
        if not path.exists():
            config = cls()
            config._path = path
            config.save(path=path)
            return config

        with open(file=path) as f:
            data: dict[str, Any] = json.load(fp=f)

        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        if "pinned" in filtered and isinstance(filtered["pinned"], list):
            filtered["pinned"] = normalize_pinned_entries(filtered["pinned"])
        config = cls(**filtered)
        config._path = path
        # Validate position (fallback to default if unknown)
        try:
            Position(value=config.position)
        except ValueError:
            config.position = "bottom"
        if config.monitor_index < -1:
            config.monitor_index = -1
        return config

    def save(self, path: Path | str | None = None) -> None:
        """Save config to JSON file."""
        path = Path(path) if path else self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(file=path, mode="w") as f:
            json.dump(obj=self.to_dict(), fp=f, indent=2)
            f.write("\n")

    def to_dict(self) -> dict[str, Any]:
        return {
            "icon_size": self.icon_size,
            "zoom_enabled": self.zoom_enabled,
            "zoom_percent": self.zoom_percent,
            "zoom_range": self.zoom_range,
            "position": self.position,
            "monitor_index": self.monitor_index,
            "autohide": self.autohide,
            "hide_delay_ms": self.hide_delay_ms,
            "unhide_delay_ms": self.unhide_delay_ms,
            "hide_time_ms": self.hide_time_ms,
            "previews_enabled": self.previews_enabled,
            "lock_icons": self.lock_icons,
            "current_workspace_only": self.current_workspace_only,
            "anchor_applets": self.anchor_applets,
            "anchor_files": self.anchor_files,
            "tooltips_enabled": self.tooltips_enabled,
            "active_display": self.active_display,
            "theme": self.theme,
            "gap_size": self.gap_size,
            "pinned": [entry.to_dict() for entry in self.pinned],
            "applet_prefs": self.applet_prefs,
            "item_prefs": self.item_prefs,
        }


def normalize_pinned_entries(raw_entries: list[object]) -> list[PinnedEntry]:
    entries: list[PinnedEntry] = []
    for raw in raw_entries:
        entry = PinnedEntry.from_raw(raw)
        if entry is not None:
            entries.append(entry)
    return entries
