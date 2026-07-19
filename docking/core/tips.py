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
            "menu. This is the quickest path to Preferences, Applets, "
            "Diagnostics, and Quit when there is no empty space near the "
            "icons."
        ),
    ),
    StartupTip(
        "start-with-preferences",
        _("Start with Preferences"),
        _(
            "Use Preferences to adjust position, monitor behavior, icon size, "
            "zoom, hiding, themes, previews, tooltips, and click actions. It "
            "is the best first stop when the dock does not feel quite right."
        ),
    ),
    StartupTip(
        "add-applets",
        _("Add applets from the dock menu"),
        _(
            "Right-click the shelf background and choose Add Applet to add "
            "launchers, system status, media controls, productivity helpers, "
            "and small utility tools directly to the dock."
        ),
    ),
    StartupTip(
        "use-separators",
        _("Use separators for organization"),
        _(
            "Right-click where a divider should go and choose Add Separator. "
            "Separators make it easier to keep pinned apps, folders, and "
            "applets in clear groups."
        ),
    ),
    StartupTip(
        "pin-running-apps",
        _("Pin running apps"),
        _(
            "Right-click a running app and choose Keep in Dock. The launcher "
            "will stay available after the app closes, so you can start it "
            "again with one click."
        ),
    ),
    StartupTip(
        "remove-pinned-items",
        _("Remove pinned items quickly"),
        _(
            "Right-click a pinned item and choose Remove from Dock, or drag "
            "unlocked items off the dock. Removing a launcher does not "
            "uninstall the application or delete your files."
        ),
    ),
    StartupTip(
        "drop-items",
        _("Drop items onto the dock"),
        _(
            "Drag applications, .desktop files, files, folders, and AppImages "
            "onto Docking to pin them. Dropped folders become folder stacks, "
            "and dropped AppImages can get their own launcher."
        ),
    ),
    StartupTip(
        "open-files-with-apps",
        _("Open files with dock apps"),
        _(
            "Drag a file onto an application icon to open it with that app. "
            "This is useful when the file manager would otherwise choose the "
            "wrong default application."
        ),
    ),
    StartupTip(
        "folder-stacks",
        _("Use folder stacks"),
        _(
            "Pin a folder and click it from the dock to browse its contents "
            "without opening a file manager first. It works well for common "
            "places like Downloads, Projects, and screenshots."
        ),
    ),
    StartupTip(
        "folder-stack-options",
        _("Tune folder stack behavior"),
        _(
            "Right-click a pinned folder to sort contents, show hidden files, "
            "or customize its icon. These options let each stack behave like "
            "the folder it represents."
        ),
    ),
    StartupTip(
        "devices-stack",
        _("Keep mounted devices within reach"),
        _(
            "Add the Devices applet to browse every mounted device in a live "
            "stack. Mounts appear and disappear automatically, and "
            "Preferences -> Behavior controls whether stacks open on hover "
            "or click."
        ),
    ),
    StartupTip(
        "country-news",
        _("Follow news sources by country"),
        _(
            "Add the News applet, choose a country, and select a publication "
            "or edition. Scroll through its latest headlines, or add more "
            "sources and switch between them from the applet menu."
        ),
    ),
    StartupTip(
        "custom-icons",
        _("Customize pinned icons"),
        _(
            "Right-click a pinned app, file, or folder and use Icon -> Choose "
            "From File. Custom icons are stored in Docking's config, so the "
            "original desktop file or file on disk is left untouched."
        ),
    ),
    StartupTip(
        "applications-search",
        _("Use the Applications applet search"),
        _(
            "Add the Applications applet, open it, and type to filter "
            "installed apps. It gives you a compact launcher menu without "
            "leaving the dock."
        ),
    ),
    StartupTip(
        "applications-drag-to-dock",
        _("Drag apps straight to the dock"),
        _(
            "Open the Applications applet and drag any application from its "
            "menu onto the shelf. Drop it where you want its launcher to "
            "stay in the dock."
        ),
    ),
    StartupTip(
        "run-application",
        _("Use Run Application like Alt+F2"),
        _(
            "Add Run Application to launch commands, apps, and terminal "
            "commands from the dock. As you type, matching applications are "
            "shown so you can launch by name or run a standalone command."
        ),
    ),
    StartupTip(
        "window-previews",
        _("Use window previews"),
        _(
            "Hover running apps to inspect open windows before switching. "
            "Previews help when one application has several windows open."
        ),
    ),
    StartupTip(
        "click-behavior",
        _("Choose your click behavior"),
        _(
            "Preferences -> Behavior lets left click toggle, cycle, or focus "
            "the most recent window. Pick the behavior that matches how you "
            "switch between running applications."
        ),
    ),
    StartupTip(
        "ctrl-click-new-window",
        _("Ctrl-click opens a new window"),
        _(
            "Hold Ctrl while clicking an app icon to launch another window. "
            "This works even when the app is already running and a normal "
            "click would focus it."
        ),
    ),
    StartupTip(
        "clock-calendar",
        _("Use Clock as a calendar"),
        _(
            "Left-click the Clock applet to open the same calendar popup as "
            "the Calendar applet. Use Clock when you want the time and "
            "calendar in one dock item."
        ),
    ),
    StartupTip(
        "app-context-menus",
        _("Use app right-click menus"),
        _(
            "Right-click apps for desktop actions, open windows, recent "
            "documents, pinning, and close actions. Many apps expose useful "
            "shortcuts there, such as opening a private window or composing a "
            "new message."
        ),
    ),
    StartupTip(
        "recent-files",
        _("Use Recent Files"),
        _(
            "Add the Recent Files applet to jump back into documents you used "
            "recently. It is a faster path when you remember the file, not the "
            "folder."
        ),
    ),
    StartupTip(
        "system-tray",
        _("Use System Tray when needed"),
        _(
            "Add System Tray for tray/status apps and use its menu to manage "
            "tray ownership when supported. This helps with legacy apps that "
            "still rely on a classic notification area."
        ),
    ),
    StartupTip(
        "diagnostics",
        _("Use Diagnostics for support"),
        _(
            "Open Diagnostics before filing an issue to copy backend, session, "
            "and feature details. Including that report makes it much easier "
            "to understand compositor, monitor, and environment-specific "
            "problems."
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
