"""Configuration schema, defaults, normalization, loading, and saving.

What configuration means in this project

Configuration is the persisted expression of user intent:

- where the dock lives,
- how it hides (hide mode),
- how large icons are,
- which items are pinned,
- which theme is active,
- applet- and item-specific preferences.

This module is the authoritative schema for that intent. It is intentionally
simple and explicit: the dock should not have several competing "settings"
shapes spread across menus, runtime state, and disk files.

Why a schema module matters

Without a real configuration schema, settings drift in several ways:

- JSON on disk can contain stale or malformed values,
- menus can assume fields exist that persistence never writes,
- runtime code can quietly invent settings that are not durable,
- tests can accidentally pass against shapes users cannot actually store.

This module prevents that by defining:

- the persisted fields,
- their defaults,
- normalization rules,
- how disk data is filtered,
- how typed entries are reconstructed.

Pinned entries are structured, not opaque strings

One of the most important evolutions in the config model is that pinned items
are stored as typed entries:

    {
      "kind": "...",
      "target": "..."
    }

Instead of an untyped list of strings.

Why:
- apps, applets, files, and folders do not mean the same thing,
- two entries may both look like strings but need different runtime handling,
- persistence should preserve meaning, not just syntax.

ASCII view:

    pinned:
      [ {kind: app,    target: firefox.desktop},
        {kind: applet, target: applet://clock},
        {kind: folder, target: file:///home/user/Downloads} ]

That is what `PinnedEntry` represents.

Load model

Loading a config file follows this sequence:

    JSON on disk
      |
      +--> filter to known dataclass fields
      |
      +--> normalize pinned entries
      |
      +--> construct Config
      |
      +--> validate/coerce selected values

Important behavior:

- unknown keys are ignored instead of crashing load,
- invalid pinned entries are dropped during normalization,
- invalid position falls back to `"bottom"`,
- invalid monitor index is clamped to `-1` or a non-negative choice.

This is a user-facing file. It must be robust against manual edits, older
versions, and partial corruption.

Save model

Saving is the reverse:

    Config instance
      |
      +--> to_dict()
      |
      +--> JSON with stable field names
      |
      +--> newline-terminated file on disk

The important property is that save only writes the schema this module owns.
Runtime-only state should not quietly leak into persisted config.

Defaults as product behavior

The defaults in this module are not arbitrary. They define the out-of-the-box
behavior of the dock:

- icon size
- zoom enabled and default factor
- bottom position
- hide mode none (no hiding)
- previews on
- tooltips on
- no pinned items by default

Changing a default here is a user-visible product decision, not just a code
cleanup.

Boundary with runtime objects

This module owns persisted preference state, not live UI state. For example:

- current hovered item -> not config
- current geometry frame -> not config
- current monitor in active-display mode -> not config
- theme name -> config
- active-display enabled flag -> config

That boundary is important. The dock should be able to rebuild its live runtime
state entirely from config plus current environment, not from hidden mutable
state that was never persisted.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from docking.applets.identity import is_applet_desktop_id
from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND, ItemKind
from docking.core.position import Position
from docking.log import get_logger

DEFAULT_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "docking"
)
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "dock.json"

DEFAULT_PINNED: list[PinnedEntry] = []
DEFAULT_ICON_SIZE = 48
DEFAULT_ZOOM_ENABLED = True
DEFAULT_ZOOM_PERCENT = 1.5
DEFAULT_ZOOM_RANGE = 3
DEFAULT_POSITION = Position.BOTTOM.value
DEFAULT_MONITOR_INDEX = -1
DEFAULT_HIDE_MODE = "none"
DEFAULT_HIDE_DELAY_MS = 0
DEFAULT_UNHIDE_DELAY_MS = 0
DEFAULT_HIDE_TIME_MS = 250
DEFAULT_PREVIEWS_ENABLED = True
DEFAULT_LOCK_ICONS = False
DEFAULT_CURRENT_WORKSPACE_ONLY = False
DEFAULT_ANCHOR_APPLETS = False
DEFAULT_ANCHOR_FILES = False
DEFAULT_TOOLTIPS_ENABLED = True
DEFAULT_ACTIVE_DISPLAY = False
DEFAULT_THEME = "default"
MIN_ICON_SIZE = 32
MAX_ICON_SIZE = 128
MIN_ZOOM_PERCENT = 1.0
MAX_ZOOM_PERCENT = 4.0

logger = get_logger("config")


class HideMode(str, Enum):
    """Dock hide behavior mode.

    Mode            Behavior
    ──────────────  ──────────────────────────────────────────────────────
    NONE            Never hides, reserves struts for window managers.
    AUTOHIDE        Hides when the mouse leaves the dock area.
    INTELLIGENT     Hides when any window from the active app overlaps
                    the dock (matched by WM_CLASS).
    DODGE_ACTIVE    Hides when the focused window itself overlaps the dock.
    WINDOW_DODGE    Hides when any window on the active workspace overlaps.
    DODGE_MAXIMIZED Hides when the active window is maximized or a dialog
                    window overlaps the dock.

    Decision logic (pseudocode)::

        show = hovered OR disabled OR (mode-specific):
            AUTOHIDE:        always hide when not hovered
            INTELLIGENT:     NOT active_app_overlaps
            DODGE_ACTIVE:    NOT active_window_overlaps
            WINDOW_DODGE:    NOT any_window_overlaps
            DODGE_MAXIMIZED: NOT (maximized_overlaps OR dialog_overlaps)
    """

    NONE = "none"
    AUTOHIDE = "autohide"
    INTELLIGENT = "intelligent"
    DODGE_ACTIVE = "dodge-active"
    WINDOW_DODGE = "window-dodge"
    DODGE_MAXIMIZED = "dodge-maximized"


def _normalize_hide_mode(value: object) -> str:
    if isinstance(value, str):
        try:
            return HideMode(value=value).value
        except ValueError as exc:
            logger.warning(
                "Invalid hide mode %r; using default %r (%s)",
                value,
                HideMode.NONE.value,
                exc,
            )
    return HideMode.NONE.value


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
        if isinstance(raw, PinnedEntry):
            return raw
        if isinstance(raw, str):
            if not raw:
                return None
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

        raw_dict = {str(k): v for k, v in raw.items()}
        kind_raw = raw_dict.get("kind")
        target_raw = raw_dict.get("target")
        if not isinstance(kind_raw, str) or not isinstance(target_raw, str):
            return None
        if not target_raw:
            return None
        if kind_raw not in {APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND}:
            return None
        return cls(kind=cast(ItemKind, kind_raw), target=target_raw)


def _uri_is_dir(target: str) -> bool:
    if not target.startswith("file://"):
        return False
    try:
        return Path(unquote(urlparse(target).path)).is_dir()
    except ValueError:
        logger.warning("Invalid file URI in persisted config: %r", target)
        return False


def _normalize_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _normalize_int(
    value: object,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        if isinstance(value, (bool, int, float)):
            parsed = int(value)
        elif isinstance(value, str):
            parsed = int(value.strip())
        else:
            return default
    except (TypeError, ValueError):
        logger.warning(
            "Invalid integer config value %r; using default %r",
            value,
            default,
        )
        return default
    if minimum is not None and parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


def _normalize_float(
    value: object,
    *,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        if isinstance(value, bool):
            parsed = float(int(value))
        elif isinstance(value, (int, float)):
            parsed = float(value)
        elif isinstance(value, str):
            parsed = float(value.strip())
        else:
            return default
    except (TypeError, ValueError):
        logger.warning(
            "Invalid float config value %r; using default %r",
            value,
            default,
        )
        return default
    if minimum is not None and parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


def _normalize_position(value: object) -> str:
    if isinstance(value, str):
        try:
            return Position(value=value).value
        except ValueError as exc:
            logger.warning(
                "Invalid dock position %r; using default %r (%s)",
                value,
                Position.BOTTOM.value,
                exc,
            )
            return Position.BOTTOM.value
    return Position.BOTTOM.value


def _normalize_theme(value: object) -> str:
    return value if isinstance(value, str) and value else "default"


def _normalize_pref_map(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            result[key] = {
                str(inner_key): inner_value for inner_key, inner_value in value.items()
            }
    return result


@dataclass
class Config:
    """Dock configuration with sensible defaults."""

    # Base icon size in pixels (before zoom)
    icon_size: int = DEFAULT_ICON_SIZE
    # Whether parabolic zoom on hover is enabled
    zoom_enabled: bool = DEFAULT_ZOOM_ENABLED
    # Max zoom multiplier (1.0-4.0, default 1.5 = 150%)
    zoom_percent: float = DEFAULT_ZOOM_PERCENT
    # Number of icon widths over which the zoom tapers off
    zoom_range: int = DEFAULT_ZOOM_RANGE
    # Screen edge where the dock is placed
    position: str = DEFAULT_POSITION
    # Target monitor index (-1 means "primary monitor")
    monitor_index: int = DEFAULT_MONITOR_INDEX
    # Dock hide behavior (see HideMode enum)
    hide_mode: str = DEFAULT_HIDE_MODE
    # Delay in ms before the dock starts hiding after cursor leaves (Plank default: 0)
    hide_delay_ms: int = DEFAULT_HIDE_DELAY_MS
    # Delay in ms before the dock starts showing when cursor returns
    unhide_delay_ms: int = DEFAULT_UNHIDE_DELAY_MS
    # Duration of the hide/show slide animation in ms
    hide_time_ms: int = DEFAULT_HIDE_TIME_MS
    # Whether to show window preview thumbnails on hover
    previews_enabled: bool = DEFAULT_PREVIEWS_ENABLED
    # Whether icon reordering, drag-in, and drag-off removal are locked
    lock_icons: bool = DEFAULT_LOCK_ICONS
    # Only show running apps from the active workspace
    current_workspace_only: bool = DEFAULT_CURRENT_WORKSPACE_ONLY
    # Whether applets are anchored to the end of the dock
    anchor_applets: bool = DEFAULT_ANCHOR_APPLETS
    # Whether file/folder entries are anchored to the end independently
    anchor_files: bool = DEFAULT_ANCHOR_FILES
    # Whether to show hover tooltips for dock items
    tooltips_enabled: bool = DEFAULT_TOOLTIPS_ENABLED
    # Whether the dock follows the cursor across monitors
    active_display: bool = DEFAULT_ACTIVE_DISPLAY
    # Theme name (loads from assets/themes/{name}.json)
    theme: str = DEFAULT_THEME
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

    @property
    def hide_mode_enum(self) -> HideMode:
        """Hide mode as enum."""
        return HideMode(value=self.hide_mode)

    def __post_init__(self) -> None:
        self._path: Path = DEFAULT_CONFIG_FILE
        self.icon_size = _normalize_int(
            self.icon_size,
            default=DEFAULT_ICON_SIZE,
            minimum=MIN_ICON_SIZE,
            maximum=MAX_ICON_SIZE,
        )
        self.zoom_enabled = _normalize_bool(
            self.zoom_enabled,
            default=DEFAULT_ZOOM_ENABLED,
        )
        self.zoom_percent = _normalize_float(
            self.zoom_percent,
            default=DEFAULT_ZOOM_PERCENT,
            minimum=MIN_ZOOM_PERCENT,
            maximum=MAX_ZOOM_PERCENT,
        )
        self.zoom_range = _normalize_int(
            self.zoom_range,
            default=DEFAULT_ZOOM_RANGE,
            minimum=0,
        )
        self.position = _normalize_position(self.position)
        self.monitor_index = max(
            DEFAULT_MONITOR_INDEX,
            _normalize_int(
                self.monitor_index,
                default=DEFAULT_MONITOR_INDEX,
                minimum=DEFAULT_MONITOR_INDEX,
            ),
        )
        self.hide_mode = _normalize_hide_mode(self.hide_mode)
        self.hide_delay_ms = _normalize_int(
            self.hide_delay_ms,
            default=DEFAULT_HIDE_DELAY_MS,
            minimum=0,
        )
        self.unhide_delay_ms = _normalize_int(
            self.unhide_delay_ms,
            default=DEFAULT_UNHIDE_DELAY_MS,
            minimum=0,
        )
        self.hide_time_ms = _normalize_int(
            self.hide_time_ms,
            default=DEFAULT_HIDE_TIME_MS,
            minimum=0,
        )
        self.previews_enabled = _normalize_bool(
            self.previews_enabled,
            default=DEFAULT_PREVIEWS_ENABLED,
        )
        self.lock_icons = _normalize_bool(
            self.lock_icons,
            default=DEFAULT_LOCK_ICONS,
        )
        self.current_workspace_only = _normalize_bool(
            self.current_workspace_only,
            default=DEFAULT_CURRENT_WORKSPACE_ONLY,
        )
        self.anchor_applets = _normalize_bool(
            self.anchor_applets,
            default=DEFAULT_ANCHOR_APPLETS,
        )
        self.anchor_files = _normalize_bool(
            self.anchor_files,
            default=DEFAULT_ANCHOR_FILES,
        )
        self.tooltips_enabled = _normalize_bool(
            self.tooltips_enabled,
            default=DEFAULT_TOOLTIPS_ENABLED,
        )
        self.active_display = _normalize_bool(
            self.active_display,
            default=DEFAULT_ACTIVE_DISPLAY,
        )
        self.theme = _normalize_theme(self.theme)
        self.pinned = normalize_pinned_entries(list(self.pinned))
        self.applet_prefs = _normalize_pref_map(self.applet_prefs)
        self.item_prefs = _normalize_pref_map(self.item_prefs)

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        """Load config from JSON file, falling back to defaults for missing keys."""
        path = Path(path) if path else DEFAULT_CONFIG_FILE
        if not path.exists():
            config = cls()
            config._path = path
            config.save(path=path)
            return config

        with path.open() as f:
            data: dict[str, Any] = json.load(fp=f)

        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        if "pinned" in filtered and isinstance(filtered["pinned"], list):
            filtered["pinned"] = normalize_pinned_entries(filtered["pinned"])
        filtered["applet_prefs"] = _normalize_pref_map(filtered.get("applet_prefs"))
        filtered["item_prefs"] = _normalize_pref_map(filtered.get("item_prefs"))
        config = cls(**filtered)
        config._path = path
        return config

    def save(self, path: Path | str | None = None) -> None:
        """Save config to JSON file."""
        path = Path(path) if path else self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode="w") as f:
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
            "hide_mode": self.hide_mode,
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
