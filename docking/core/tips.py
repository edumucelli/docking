# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Startup usage tips and persistent tip rotation state."""

from __future__ import annotations

import json
import random
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.core.paths import ensure_parent_dir
from docking.i18n import _
from docking.log import get_logger
from docking.platform.environment import docking_state_dir

logger = get_logger("tips")

DEFAULT_STATE_DIR = docking_state_dir()
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "tips.json"
FIRST_TIP_ID = "dock-menu-ctrl-right-click"


@dataclass(frozen=True, slots=True)
class StartupTip:
    """One translatable startup tip."""

    id: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class StartupTipsState:
    """Persistent tip rotation state."""

    shown_tip_ids: tuple[str, ...] = ()


STARTUP_TIPS: tuple[StartupTip, ...] = (
    StartupTip(
        FIRST_TIP_ID,
        _("Open the dock menu from anywhere"),
        _(
            "Hold Ctrl and right-click anywhere on the shelf to open the dock "
            "menu with Preferences, Applets, Diagnostics, and Quit."
        ),
    ),
    StartupTip(
        "start-with-preferences",
        _("Start with Preferences"),
        _(
            "Use Preferences to adjust position, monitor behavior, icon size, "
            "zoom, hiding, themes, previews, tooltips, and click actions."
        ),
    ),
    StartupTip(
        "add-applets",
        _("Add applets from the dock menu"),
        _(
            "Right-click the shelf background and choose Add Applet to add "
            "launchers, system status, media, productivity, and utility tools."
        ),
    ),
    StartupTip(
        "use-separators",
        _("Use separators for organization"),
        _("Right-click where a divider should go and choose Add Separator."),
    ),
    StartupTip(
        "pin-running-apps",
        _("Pin running apps"),
        _("Right-click a running app and choose Keep in Dock."),
    ),
    StartupTip(
        "remove-pinned-items",
        _("Remove pinned items quickly"),
        _(
            "Right-click a pinned item and choose Remove from Dock, or drag "
            "unlocked items off the dock."
        ),
    ),
    StartupTip(
        "drop-items",
        _("Drop items onto the dock"),
        _(
            "Drag applications, .desktop files, files, folders, and AppImages "
            "onto Docking to pin them."
        ),
    ),
    StartupTip(
        "open-files-with-apps",
        _("Open files with dock apps"),
        _("Drag a file onto an application icon to open it with that app."),
    ),
    StartupTip(
        "folder-stacks",
        _("Use folder stacks"),
        _(
            "Pin a folder and click it from the dock to browse its contents "
            "without opening a file manager first."
        ),
    ),
    StartupTip(
        "folder-stack-options",
        _("Tune folder stack behavior"),
        _(
            "Right-click a pinned folder to sort contents, show hidden files, "
            "or customize its icon."
        ),
    ),
    StartupTip(
        "custom-icons",
        _("Customize pinned icons"),
        _(
            "Right-click a pinned app, file, or folder and use Icon -> Choose "
            "From File."
        ),
    ),
    StartupTip(
        "applications-search",
        _("Use the Applications applet search"),
        _("Add the Applications applet, open it, and type to filter installed apps."),
    ),
    StartupTip(
        "run-application",
        _("Use Run Application like Alt+F2"),
        _(
            "Add Run Application to launch commands, apps, and terminal "
            "commands from the dock."
        ),
    ),
    StartupTip(
        "window-previews",
        _("Use window previews"),
        _("Hover running apps to inspect open windows before switching."),
    ),
    StartupTip(
        "click-behavior",
        _("Choose your click behavior"),
        _(
            "Preferences -> Behavior lets left click toggle, cycle, or focus "
            "the most recent window."
        ),
    ),
    StartupTip(
        "ctrl-click-new-window",
        _("Ctrl-click opens a new window"),
        _("Hold Ctrl while clicking an app icon to launch another window."),
    ),
    StartupTip(
        "app-context-menus",
        _("Use app right-click menus"),
        _(
            "Right-click apps for desktop actions, open windows, recent "
            "documents, pinning, and close actions."
        ),
    ),
    StartupTip(
        "recent-files",
        _("Use Recent Files"),
        _("Add the Recent Files applet to jump back into recently used documents."),
    ),
    StartupTip(
        "system-tray",
        _("Use System Tray when needed"),
        _(
            "Add System Tray for tray/status apps and use its menu to manage "
            "tray ownership when supported."
        ),
    ),
    StartupTip(
        "diagnostics",
        _("Use Diagnostics for support"),
        _(
            "Open Diagnostics before filing an issue to copy backend, session, "
            "and feature details."
        ),
    ),
)


def load_state(path: Path | str | None = None) -> StartupTipsState:
    """Load startup tip state, falling back to defaults on invalid data."""
    state_path = Path(path) if path is not None else DEFAULT_STATE_FILE
    if not state_path.exists():
        return StartupTipsState()
    try:
        with state_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:
        logger.warning("Failed to load startup tips state %s: %s", state_path, exc)
        return StartupTipsState()
    if not isinstance(raw, dict):
        logger.warning(
            "Invalid startup tips state payload in %s; using defaults",
            state_path,
        )
        return StartupTipsState()
    return StartupTipsState(
        shown_tip_ids=_normalize_shown_tip_ids(raw.get("shown_tip_ids")),
    )


def save_state(state: StartupTipsState, *, path: Path | str | None = None) -> None:
    """Persist startup tip state atomically."""
    state_path = Path(path) if path is not None else DEFAULT_STATE_FILE
    ensure_parent_dir(state_path)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=state_path.parent,
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        json.dump(
            {"shown_tip_ids": list(state.shown_tip_ids)},
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    tmp_path.replace(state_path)


def select_startup_tip(
    *,
    enabled: bool,
    path: Path | str | None = None,
    chooser: Callable[[Sequence[StartupTip]], StartupTip] | None = None,
) -> StartupTip | None:
    """Select and consume the next startup tip."""
    if not enabled:
        return None

    state = load_state(path=path)
    tip_by_id = {tip.id: tip for tip in STARTUP_TIPS}
    shown = tuple(tip_id for tip_id in state.shown_tip_ids if tip_id in tip_by_id)
    shown_set = set(shown)

    if FIRST_TIP_ID not in shown_set:
        selected = tip_by_id[FIRST_TIP_ID]
        next_shown = (*shown, selected.id)
    else:
        available = [tip for tip in STARTUP_TIPS if tip.id not in shown_set]
        if not available:
            available = [tip for tip in STARTUP_TIPS if tip.id != FIRST_TIP_ID]
            shown = (FIRST_TIP_ID,)
        selected = (chooser or random.choice)(available)
        next_shown = (*shown, selected.id)

    save_state(StartupTipsState(shown_tip_ids=_dedupe(next_shown)), path=path)
    return selected


def _normalize_shown_tip_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return _dedupe(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _dedupe(values: Sequence[str] | Any) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)
