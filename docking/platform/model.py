"""DockModel: canonical dock state, ordering, and applet ownership.

This module is the source of truth for "what icons currently exist in the dock
and in what order." Renderers, input handlers, menus, hover logic, and window
tracking all consume model state, but only DockModel owns mutation rules.

What data does the model own?

DockModel composes two logical lists:

1. pinned items: persistent entries from config (apps, applets, separators),
2. transient items: non-pinned apps that are currently running.

The visual dock list is ``pinned + transient`` (via ``visible_items()``).
Pinned order is user-controlled and persisted. Transient entries appear only
while running and disappear when no windows remain.

Why this layer exists

Without a dedicated model, each subsystem would mutate partial state:
WindowTracker might toggle running flags, menu actions might reorder entries,
and applet code might update labels independently. That creates drift and race
conditions. DockModel centralizes those writes so each operation has a single
place to enforce invariants.

Identity model used by DockModel

Each visible entry is a ``DockItem`` with a ``desktop_id`` key:

- regular apps use desktop entry IDs (``firefox.desktop``),
- applets use ``applet://<id>`` (for example ``applet://clock``),
- multi-instance applets include instance suffixes
  (for example ``applet://separator#2``).

This uniform keying lets the renderer and input logic treat apps and applets
with the same container type while preserving applet-specific lifecycle hooks.

Ownership boundaries across modules

- Launcher: resolves desktop metadata and loads theme icons.
- WindowTracker: reports running/active/urgent window aggregates.
- DockModel: applies those aggregates to DockItem flags and counts.
- UI modules: render and interact with DockItems; they do not own state.

Lifecycle and persistence responsibilities

DockModel is responsible for:

- constructing applets from registry IDs and managing their lifetime,
- inserting/removing separator instances with stable instance numbering,
- starting/stopping applets with dock notify callbacks,
- synchronizing pinned order back to config and saving.

Core invariant

After every mutating operation, ``visible_items()`` must remain renderer-safe:
stable order, coherent running/active/urgent flags, and applet object registry
in sync with corresponding DockItem entries. If this invariant holds, all UI
paths can assume consistent model semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import docking.applets as applets
from docking.applets.identity import (
    APPLET_PREFIX,
    AppletId,
    applet_desktop_id,
    applet_id_from,
    is_applet_desktop_id,
)
from docking.core.items import DockItem
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.applets.base import Applet
    from docking.core.config import Config
    from docking.platform.launcher import Launcher

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

_log = with_context(get_logger(name="model"))


class DockModel:
    """Ordered collection of dock items, merging pinned and running apps."""

    def __init__(self, config: Config, launcher: Launcher) -> None:
        self._config = config
        self._launcher = launcher
        self.pinned_items: list[DockItem] = []
        self._transient: list[DockItem] = []
        self._applets: dict[str, Applet] = {}
        self.on_change: Callable[[], None] | None = None

        self._load_pinned()

    def _load_pinned(self) -> None:
        """Load pinned items from config and resolve their desktop info."""
        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        registry = applets.get_registry()

        for desktop_id in self._config.pinned:
            if is_applet_desktop_id(desktop_id=desktop_id):
                did = applet_id_from(desktop_id=desktop_id)
                cls = registry.get(did)
                if cls:
                    try:
                        applet = cls(icon_size=icon_size, config=self._config)
                        applet.item.desktop_id = desktop_id
                        applet.apply_prefs()
                        self._applets[desktop_id] = applet
                        self.pinned_items.append(applet.item)
                        _log.bind(applet_id=str(did), action="load_applet").info(
                            f"Loaded applet {did} (icon={applet.item.icon})"
                        )
                    except Exception:
                        _log.bind(applet_id=str(did), action="load_applet").exception(
                            f"Failed to create applet {did}"
                        )
                else:
                    _log.bind(applet_id=str(did), action="load_applet").warning(
                        f"Unknown applet id: {did}"
                    )
                continue

            info = self._launcher.resolve(desktop_id=desktop_id)
            if info is None:
                continue
            icon = self._launcher.load_icon(icon_name=info.icon_name, size=icon_size)
            self.pinned_items.append(
                DockItem(
                    desktop_id=desktop_id,
                    name=info.name,
                    icon_name=info.icon_name,
                    wm_class=info.wm_class,
                    is_pinned=True,
                    icon=icon,
                )
            )

    def get_applet(self, desktop_id: str) -> Applet | None:
        """Look up active applet by desktop_id."""
        return self._applets.get(desktop_id)

    def add_applet(self, applet_id: str) -> None:
        """Instantiate a applet and add to the dock."""
        try:
            did = AppletId(applet_id)
        except ValueError:
            _log.bind(applet_id=applet_id, action="add_applet").warning(
                f"Invalid applet id: {applet_id}"
            )
            return

        desktop_id = applet_desktop_id(applet_id=did)
        if desktop_id in self._applets:
            _log.bind(applet_id=str(did), action="add_applet").warning(
                f"Applet already present: {did}"
            )
            return
        cls = applets.get_registry().get(did)
        if not cls:
            _log.bind(applet_id=str(did), action="add_applet").warning(
                f"No class registered for applet: {did}"
            )
            return
        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        try:
            applet = cls(icon_size=icon_size, config=self._config)
        except Exception:
            _log.bind(applet_id=str(did), action="add_applet").exception(
                f"Failed to create applet {did}"
            )
            return
        self._applets[desktop_id] = applet
        self.pinned_items.append(applet.item)
        applet.start(notify=self.notify)
        self.sync_pinned_to_config()
        self._config.save()
        self.notify()

    def add_separator(self, index: int = -1) -> None:
        """Add a separator instance at the given pinned index (-1 = end)."""
        cls = applets.get_registry().get(AppletId.SEPARATOR)
        if not cls:
            return

        # Find next unused instance number
        prefix = f"{APPLET_PREFIX}{AppletId.SEPARATOR}#"
        nums = [int(k[len(prefix) :]) for k in self._applets if k.startswith(prefix)]
        n = max(nums, default=-1) + 1
        desktop_id = applet_desktop_id(applet_id=AppletId.SEPARATOR, instance=n)

        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        try:
            applet = cls(icon_size=icon_size, config=self._config)
        except Exception:
            _log.bind(applet_id="separator", action="add_separator").exception(
                "Failed to create separator",
            )
            return
        applet.item.desktop_id = desktop_id
        applet.apply_prefs()
        self._applets[desktop_id] = applet
        if index < 0 or index >= len(self.pinned_items):
            self.pinned_items.append(applet.item)
        else:
            self.pinned_items.insert(index, applet.item)
        applet.start(notify=self.notify)
        self.sync_pinned_to_config()
        self._config.save()
        self.notify()

    def remove_applet(self, desktop_id: str) -> None:
        """Stop and remove a applet from the dock."""
        applet = self._applets.pop(desktop_id, None)
        if applet:
            applet.stop()
            if applet.item in self.pinned_items:
                self.pinned_items.remove(applet.item)
            self.sync_pinned_to_config()
            self._config.save()
            self.notify()

    def start_applets(self) -> None:
        """Start all active applets (call after dock is ready)."""
        for applet in self._applets.values():
            applet.start(notify=self.notify)

    def stop_applets(self) -> None:
        """Stop all active applets (call on shutdown)."""
        for applet in self._applets.values():
            applet.stop()

    def visible_items(self) -> list[DockItem]:
        """All items to display: pinned first, then transient running apps."""
        return self.pinned_items + self._transient

    def find_by_desktop_id(self, desktop_id: str) -> DockItem | None:
        for item in self.pinned_items + self._transient:
            if item.desktop_id == desktop_id:
                return item
        return None

    def find_by_wm_class(self, wm_class: str) -> DockItem | None:
        wm_lower = wm_class.lower()
        for item in self.pinned_items + self._transient:
            if item.wm_class.lower() == wm_lower:
                return item
        return None

    def update_running(self, running: dict[str, dict[str, Any]]) -> None:
        """Update running state from WindowTracker data.

        Args:
            running: {desktop_id: {"count": int, "active": bool}}
        """
        # Reset running state (preserve is_urgent for transition detection)
        for item in self.pinned_items:
            item.is_running = False
            item.is_active = False
            item.instance_count = 0

        # Update pinned items that are running
        matched_ids = set()
        for item in self.pinned_items:
            if item.desktop_id not in running:
                item.is_urgent = False
                continue
            info = running[item.desktop_id]
            item.is_running = True
            item.is_active = info.get("active", False)
            item.instance_count = info.get("count", 1)
            # Set urgent timestamp only on false->true transition
            urgent = info.get("urgent", False)
            if urgent and not item.is_urgent:
                item.last_urgent = GLib.get_monotonic_time()
            item.is_urgent = urgent
            matched_ids.add(item.desktop_id)

        # Add transient items for running apps not in pinned
        new_transient: list[DockItem] = []
        for desktop_id, info in running.items():
            if desktop_id not in matched_ids:
                existing = next(
                    (t for t in self._transient if t.desktop_id == desktop_id), None
                )
                if existing:
                    existing.is_running = True
                    existing.is_active = info.get("active", False)
                    existing.instance_count = info.get("count", 1)
                    new_transient.append(existing)
                else:
                    resolved = self._launcher.resolve(desktop_id=desktop_id)
                    icon_size = int(self._config.icon_size * self._config.zoom_percent)
                    icon = self._launcher.load_icon(
                        icon_name=(
                            resolved.icon_name
                            if resolved
                            else "application-x-executable"
                        ),
                        size=icon_size,
                    )
                    new_transient.append(
                        DockItem(
                            desktop_id=desktop_id,
                            name=resolved.name if resolved else desktop_id,
                            icon_name=(
                                resolved.icon_name
                                if resolved
                                else "application-x-executable"
                            ),
                            wm_class=resolved.wm_class if resolved else "",
                            is_pinned=False,
                            is_running=True,
                            is_active=info.get("active", False),
                            instance_count=info.get("count", 1),
                            icon=icon,
                        )
                    )

        self._transient = new_transient
        self.notify()

    def pin_item(self, desktop_id: str) -> None:
        """Pin a transient item to the dock."""
        item = next((t for t in self._transient if t.desktop_id == desktop_id), None)
        if item:
            self._transient.remove(item)
            item.is_pinned = True
            self.pinned_items.append(item)
            self.sync_pinned_to_config()
            self.notify()

    def unpin_item(self, desktop_id: str) -> None:
        """Unpin an item. If running, becomes transient; otherwise removed.

        Applets are fully removed (stop + cleanup) since they can't be transient.
        """
        if is_applet_desktop_id(desktop_id=desktop_id):
            self.remove_applet(desktop_id=desktop_id)
            return
        item = next((p for p in self.pinned_items if p.desktop_id == desktop_id), None)
        if item:
            self.pinned_items.remove(item)
            item.is_pinned = False
            if item.is_running:
                self._transient.append(item)
            self.sync_pinned_to_config()
            self.notify()

    def reorder(self, from_index: int, to_index: int) -> None:
        """Move a pinned item from one position to another."""
        items = self.pinned_items
        if 0 <= from_index < len(items) and 0 <= to_index < len(items):
            item = items.pop(from_index)
            items.insert(to_index, item)
            self.sync_pinned_to_config()
            self.notify()

    def reorder_visible(self, from_index: int, to_index: int) -> None:
        """Move any visible item, auto-pinning transients as needed.

        Indices are based on visible_items() ordering.
        """
        items = self.visible_items()
        if not (0 <= from_index < len(items) and 0 <= to_index < len(items)):
            return

        item = items[from_index]

        # Auto-pin transient items so they can be reordered among pinned items
        if not item.is_pinned:
            if item in self._transient:
                self._transient.remove(item)
            item.is_pinned = True
            self.pinned_items.append(item)

        # Map visible index -> pinned index for the source item
        pinned_from = self.pinned_items.index(item)

        # Map visible target index -> pinned index (auto-pin target if transient)
        target_item = items[to_index] if to_index < len(items) else None
        if target_item and not target_item.is_pinned:
            if target_item in self._transient:
                self._transient.remove(target_item)
            target_item.is_pinned = True
            self.pinned_items.append(target_item)

        if target_item and target_item in self.pinned_items:
            pinned_to = self.pinned_items.index(target_item)
        else:
            pinned_to = len(self.pinned_items) - 1

        if pinned_from != pinned_to:
            self.pinned_items.pop(pinned_from)
            self.pinned_items.insert(pinned_to, item)

        self.sync_pinned_to_config()
        self.notify()

    def sync_pinned_to_config(self) -> None:
        """Write current pinned_items order back to config (does not save to disk)."""
        self._config.pinned = [item.desktop_id for item in self.pinned_items]

    def notify(self) -> None:
        """Fire on_change callback to trigger a dock redraw."""
        if self.on_change:
            self.on_change()
