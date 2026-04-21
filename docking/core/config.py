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
- no schema-level pinned items by default (first-run bootstrap may seed a starter dock)

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
import tempfile
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from docking.applets.identity import applet_desktop_id, is_applet_desktop_id
from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND, ItemKind
from docking.core.position import Position
from docking.log import get_logger

DEFAULT_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "docking"
)
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "dock.json"
DEFAULT_CONFIG_BACKUP_FILE = DEFAULT_CONFIG_DIR / "dock.json.bak"

DEFAULT_BROWSER_DESKTOP_IDS: tuple[str, ...] = (
    "firefox.desktop",
    "org.mozilla.firefox.desktop",
    "chromium.desktop",
    "chromium-browser.desktop",
    "google-chrome.desktop",
    "brave-browser.desktop",
    "org.gnome.Epiphany.desktop",
    "epiphany.desktop",
)
DEFAULT_FILE_MANAGER_DESKTOP_IDS: tuple[str, ...] = (
    "org.gnome.Nautilus.desktop",
    "nautilus.desktop",
    "nemo.desktop",
    "caja.desktop",
    "thunar.desktop",
    "org.kde.dolphin.desktop",
    "dolphin.desktop",
    "pcmanfm.desktop",
)
DEFAULT_TERMINAL_DESKTOP_IDS: tuple[str, ...] = (
    "org.gnome.Terminal.desktop",
    "gnome-terminal.desktop",
    "mate-terminal.desktop",
    "xfce4-terminal.desktop",
    "org.kde.konsole.desktop",
    "konsole.desktop",
    "terminator.desktop",
    "kitty.desktop",
    "Alacritty.desktop",
    "com.mitchellh.ghostty.desktop",
    "org.codeberg.dnkl.foot.desktop",
)
DEFAULT_EDITOR_DESKTOP_IDS: tuple[str, ...] = (
    "code.desktop",
    "codium.desktop",
    "sublime_text.desktop",
    "gedit.desktop",
    "org.gnome.gedit.desktop",
    "xed.desktop",
    "mousepad.desktop",
    "kate.desktop",
    "org.gnome.TextEditor.desktop",
)
DEFAULT_MAIL_DESKTOP_IDS: tuple[str, ...] = (
    "org.mozilla.Thunderbird.desktop",
    "thunderbird.desktop",
    "geary.desktop",
    "org.gnome.Evolution.desktop",
    "evolution.desktop",
    "kmail.desktop",
    "org.kde.kmail2.desktop",
    "claws-mail.desktop",
)
DEFAULT_CALCULATOR_DESKTOP_IDS: tuple[str, ...] = (
    "org.gnome.Calculator.desktop",
    "gnome-calculator.desktop",
    "mate-calc.desktop",
    "galculator.desktop",
    "kcalc.desktop",
)
DEFAULT_SOFTWARE_STORE_DESKTOP_IDS: tuple[str, ...] = (
    "org.gnome.Software.desktop",
    "gnome-software.desktop",
    "snap-store.desktop",
    "plasma-discover.desktop",
    "org.kde.discover.desktop",
    "pamac-manager.desktop",
    "ubuntu-software-center.desktop",
    "appcenter.desktop",
    "synaptic.desktop",
)
STARTER_APPLET_IDS: tuple[str, ...] = (
    "applications",
    "clock",
    "calendar",
    "weather",
    "systemmonitor",
    "hydration",
    "notifications",
    "session",
)

DEFAULT_PINNED: list[PinnedEntry] = []
DEFAULT_ICON_SIZE = 48
DEFAULT_ZOOM_ENABLED = True
DEFAULT_ZOOM_PERCENT = 1.5
DEFAULT_ZOOM_RANGE = 3
DEFAULT_POSITION = Position.BOTTOM.value
DEFAULT_MONITOR_INDEX = -1
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
DEFAULT_TRANSPARENCY = 1.0
MIN_ICON_SIZE = 32
MAX_ICON_SIZE = 128
MIN_ZOOM_PERCENT = 1.0
MAX_ZOOM_PERCENT = 4.0
MIN_TRANSPARENCY = 0.15
MAX_TRANSPARENCY = 1.0

logger = get_logger("config")
_SAVE_LOCK = threading.RLock()


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


DEFAULT_HIDE_MODE = HideMode.NONE.value


def _build_initial_pinned() -> list[PinnedEntry]:
    applications_entry = PinnedEntry(
        kind=APPLET_KIND,
        target=applet_desktop_id(applet_id=STARTER_APPLET_IDS[0]),
    )
    launcher_entries = _build_initial_launcher_entries()
    applet_entries = [
        PinnedEntry(kind=APPLET_KIND, target=applet_desktop_id(applet_id=applet_id))
        for applet_id in STARTER_APPLET_IDS[1:]
    ]
    return [applications_entry, *launcher_entries, *applet_entries]


def _build_initial_launcher_entries() -> list[PinnedEntry]:
    entries: list[PinnedEntry] = []
    seen_targets: set[str] = set()
    slots: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (DEFAULT_BROWSER_DESKTOP_IDS, ("x-scheme-handler/http",)),
        (DEFAULT_FILE_MANAGER_DESKTOP_IDS, ("inode/directory",)),
        (DEFAULT_TERMINAL_DESKTOP_IDS, ()),
        (DEFAULT_EDITOR_DESKTOP_IDS, ("text/plain",)),
        (DEFAULT_MAIL_DESKTOP_IDS, ("x-scheme-handler/mailto",)),
        (DEFAULT_CALCULATOR_DESKTOP_IDS, ()),
        (DEFAULT_SOFTWARE_STORE_DESKTOP_IDS, ()),
    )
    for candidates, fallback_content_types in slots:
        desktop_id = _resolve_initial_desktop_id(
            candidates=candidates,
            fallback_content_types=fallback_content_types,
        )
        if desktop_id is None or desktop_id in seen_targets:
            continue
        seen_targets.add(desktop_id)
        entries.append(PinnedEntry(kind=APP_KIND, target=desktop_id))
    return entries


def _resolve_initial_desktop_id(
    *,
    candidates: tuple[str, ...],
    fallback_content_types: tuple[str, ...],
) -> str | None:
    for desktop_id in candidates:
        if _desktop_id_exists(desktop_id):
            return desktop_id
    for content_type in fallback_content_types:
        desktop_id = _default_desktop_id_for(content_type)
        if desktop_id and _desktop_id_exists(desktop_id):
            return desktop_id
    return None


def _desktop_id_exists(desktop_id: str) -> bool:
    from docking.platform.launcher import Launcher

    return Launcher().resolve(desktop_id=desktop_id, log_failures=False) is not None


def _default_desktop_id_for(content_type: str) -> str | None:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    try:
        app_info = Gio.AppInfo.get_default_for_type(content_type, False)
    except GLib.Error as exc:
        logger.warning(
            "Failed to resolve default app for %s while seeding first-run pins: %s",
            content_type,
            exc,
        )
        return None
    if app_info is None:
        return None
    desktop_id = app_info.get_id()
    return desktop_id if isinstance(desktop_id, str) and desktop_id else None


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


class LeftClickAction(str, Enum):
    """Primary-click behavior for running applications.

    Action  Behavior
    ──────  ─────────────────────────────────────────────────────────────
    TOGGLE  Focuses the app, or minimizes its windows if already focused.
    CYCLE   Advances focus through the app's open windows.
    """

    TOGGLE = "toggle"
    CYCLE = "cycle"


class MiddleClickAction(str, Enum):
    """Middle-click behavior for application items.

    Action         Behavior
    ─────────────  ──────────────────────────────────────────────────────
    NEW_WINDOW     Opens a new window for the application when possible.
    MINIMIZE       Minimizes the application's open windows.
    CLOSE_FOCUSED  Closes the app's currently focused window.
    """

    NEW_WINDOW = "new-window"
    MINIMIZE = "minimize"
    CLOSE_FOCUSED = "close-focused"


DEFAULT_LEFT_CLICK_ACTION = LeftClickAction.TOGGLE.value
DEFAULT_MIDDLE_CLICK_ACTION = MiddleClickAction.NEW_WINDOW.value


def _normalize_left_click_action(value: object) -> str:
    if isinstance(value, str):
        try:
            return LeftClickAction(value=value).value
        except ValueError as exc:
            logger.warning(
                "Invalid left click action %r; using default %r (%s)",
                value,
                LeftClickAction.TOGGLE.value,
                exc,
            )
    return LeftClickAction.TOGGLE.value


def _normalize_middle_click_action(value: object) -> str:
    if isinstance(value, str):
        try:
            return MiddleClickAction(value=value).value
        except ValueError as exc:
            logger.warning(
                "Invalid middle click action %r; using default %r (%s)",
                value,
                MiddleClickAction.NEW_WINDOW.value,
                exc,
            )
    return MiddleClickAction.NEW_WINDOW.value


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
            # Infer ItemKind from string shape: file:// URIs -> FILE/FOLDER,
            # applet prefix -> APPLET, everything else -> APP.
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


def _normalize_transparency(value: object) -> float:
    return _normalize_float(
        value,
        default=DEFAULT_TRANSPARENCY,
        minimum=MIN_TRANSPARENCY,
        maximum=MAX_TRANSPARENCY,
    )


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
    # Left-click behavior for running apps
    left_click_action: str = DEFAULT_LEFT_CLICK_ACTION
    # Middle-click behavior for app items
    middle_click_action: str = DEFAULT_MIDDLE_CLICK_ACTION
    # Theme name (loads from assets/themes/{name}.json)
    theme: str = DEFAULT_THEME
    # Multiplier applied to theme alpha values for the dock shelf
    transparency: float = DEFAULT_TRANSPARENCY
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
        self.left_click_action = _normalize_left_click_action(
            self.left_click_action,
        )
        self.middle_click_action = _normalize_middle_click_action(
            self.middle_click_action,
        )
        self.theme = _normalize_theme(self.theme)
        self.transparency = _normalize_transparency(self.transparency)
        self.pinned = normalize_pinned_entries(list(self.pinned))
        self.applet_prefs = _normalize_pref_map(self.applet_prefs)
        self.item_prefs = _normalize_pref_map(self.item_prefs)

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        """Load config from JSON file, falling back to defaults for missing keys."""
        path = Path(path) if path else DEFAULT_CONFIG_FILE
        if not path.exists():
            config = cls(pinned=_build_initial_pinned())
            config._path = path
            config.save(path=path)
            return config

        try:
            config = cls._load_existing_file(path=path)
        except Exception as exc:
            backup_path = _backup_path_for(path)
            logger.warning("Failed to load config %s: %s", path, exc)
            if backup_path.exists():
                try:
                    config = cls._load_existing_file(path=backup_path)
                except Exception as backup_exc:
                    logger.warning(
                        "Failed to load config backup %s: %s",
                        backup_path,
                        backup_exc,
                    )
                else:
                    logger.warning("Loaded config fallback from backup %s", backup_path)
                    config._path = path
                    return config
            logger.warning("Falling back to default config for %s", path)
            config = cls()
            config._path = path
            return config
        backup_path = _backup_path_for(path)
        if path.exists() and not backup_path.exists():
            try:
                _write_backup_copy(source=path, backup_path=backup_path)
            except Exception as backup_exc:
                logger.warning(
                    "Failed to create initial config backup %s: %s",
                    backup_path,
                    backup_exc,
                )
        config._path = path
        return config

    def save(self, path: Path | str | None = None) -> None:
        """Save config to JSON file."""
        path = Path(path) if path else self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file and replace in one step so Ctrl+C or
        # process death never leaves the real config half-written.
        backup_path = _backup_path_for(path)
        with _SAVE_LOCK:
            tmp_path = _new_tmp_path(path=path)
            try:
                _write_json_atomic_candidate(path=tmp_path, payload=self.to_dict())
                self._load_existing_file(path=tmp_path)
                if path.exists():
                    if _is_valid_config_file(path=path):
                        _write_backup_copy(source=path, backup_path=backup_path)
                    else:
                        logger.warning(
                            "Skipping config backup refresh because current file %s "
                            "is invalid",
                            path,
                        )
                tmp_path.replace(path)
                _fsync_directory(path.parent)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    logger.warning(
                        "Failed to clean up temporary config file %s: %s",
                        tmp_path,
                        cleanup_exc,
                    )
                raise

    @classmethod
    def _load_existing_file(cls, path: Path) -> Config:
        data = _read_config_data(path=path)
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        if "pinned" in filtered and isinstance(filtered["pinned"], list):
            filtered["pinned"] = normalize_pinned_entries(filtered["pinned"])
        filtered["applet_prefs"] = _normalize_pref_map(filtered.get("applet_prefs"))
        filtered["item_prefs"] = _normalize_pref_map(filtered.get("item_prefs"))
        config = cls(**filtered)
        config._path = path
        return config

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
            "left_click_action": self.left_click_action,
            "middle_click_action": self.middle_click_action,
            "theme": self.theme,
            "transparency": self.transparency,
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


def _backup_path_for(path: Path) -> Path:
    if path == DEFAULT_CONFIG_FILE:
        return DEFAULT_CONFIG_BACKUP_FILE
    return path.with_name(f"{path.name}.bak")


def _new_tmp_path(*, path: Path) -> Path:
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    os.close(fd)
    return Path(tmp_name)


def _write_json_atomic_candidate(*, path: Path, payload: dict[str, Any]) -> None:
    with path.open(mode="w", encoding="utf-8") as f:
        json.dump(obj=payload, fp=f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def _write_backup_copy(*, source: Path, backup_path: Path) -> None:
    backup_tmp = _new_tmp_path(path=backup_path)
    try:
        data = source.read_bytes()
        with backup_tmp.open(mode="wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        backup_tmp.replace(backup_path)
        _fsync_directory(backup_path.parent)
    except Exception:
        try:
            backup_tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning(
                "Failed to clean up temporary backup file %s: %s",
                backup_tmp,
                cleanup_exc,
            )
        raise


def _read_config_data(*, path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(fp=f)
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} does not contain a JSON object")
    return data


def _is_valid_config_file(*, path: Path) -> bool:
    try:
        Config._load_existing_file(path=path)
    except Exception as exc:
        logger.warning("Config file validation failed for %s: %s", path, exc)
        return False
    return True


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
