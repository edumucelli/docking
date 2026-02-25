"""Data model for dock items -- merges pinned and running applications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING, Callable

from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.launcher import Launcher
    from docking.docklets.base import Docklet

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402


@dataclass
class DockItem:
    """A single item in the dock."""

    desktop_id: str
    name: str = ""
    icon_name: str = "application-x-executable"
    wm_class: str = ""
    is_pinned: bool = False
    is_running: bool = False
    is_active: bool = False
    is_urgent: bool = False
    instance_count: int = 0
    icon: GdkPixbuf.Pixbuf | None = None
    # Timestamps for animations (monotonic microseconds, 0 = inactive)
    last_clicked: int = 0
    last_launched: int = 0
    last_urgent: int = 0


class DockModel:
    """Ordered collection of dock items, merging pinned and running apps."""

    def __init__(self, config: Config, launcher: Launcher) -> None:
        self._config = config
        self._launcher = launcher
        self.pinned_items: list[DockItem] = []
        self._transient: list[DockItem] = []
        self._docklets: dict[str, Docklet] = {}
        self.on_change: Callable[[], None] | None = None

        self._load_pinned()

    def _load_pinned(self) -> None:
        """Load pinned items from config and resolve their desktop info."""
        from docking.docklets.base import is_docklet, docklet_id_from
        from docking.docklets import get_registry

        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        registry = get_registry()

        log = get_logger("model")
        for desktop_id in self._config.pinned:
            if is_docklet(desktop_id):
                did = docklet_id_from(desktop_id)
                cls = registry.get(did)
                if cls:
                    try:
                        docklet = cls(icon_size, config=self._config)
                        self._docklets[desktop_id] = docklet
                        self.pinned_items.append(docklet.item)
                        log.info("Loaded docklet %s (icon=%s)", did, docklet.item.icon)
                    except Exception:
                        log.exception("Failed to create docklet %s", did)
                else:
                    log.warning("Unknown docklet id: %s", did)
                continue

            info = self._launcher.resolve(desktop_id)
            if info is None:
                continue
            icon = self._launcher.load_icon(info.icon_name, icon_size)
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

    def get_docklet(self, desktop_id: str) -> Docklet | None:
        """Look up active docklet by desktop_id."""
        return self._docklets.get(desktop_id)

    def add_docklet(self, docklet_id: str) -> None:
        """Instantiate a docklet and add to the dock."""
        from docking.docklets import get_registry

        desktop_id = f"docklet://{docklet_id}"
        if desktop_id in self._docklets:
            return
        cls = get_registry().get(docklet_id)
        if not cls:
            return
        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        try:
            docklet = cls(icon_size, config=self._config)
        except Exception:
            get_logger("model").exception("Failed to create docklet %s", docklet_id)
            return
        self._docklets[desktop_id] = docklet
        self.pinned_items.append(docklet.item)
        docklet.start(self.notify)
        self.sync_pinned_to_config()
        self.notify()

    def remove_docklet(self, desktop_id: str) -> None:
        """Stop and remove a docklet from the dock."""
        docklet = self._docklets.pop(desktop_id, None)
        if docklet:
            docklet.stop()
            if docklet.item in self.pinned_items:
                self.pinned_items.remove(docklet.item)
            self.sync_pinned_to_config()
            self.notify()

    def start_docklets(self) -> None:
        """Start all active docklets (call after dock is ready)."""
        for docklet in self._docklets.values():
            docklet.start(self.notify)

    def stop_docklets(self) -> None:
        """Stop all active docklets (call on shutdown)."""
        for docklet in self._docklets.values():
            docklet.stop()

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
                    resolved = self._launcher.resolve(desktop_id)
                    icon_size = int(self._config.icon_size * self._config.zoom_percent)
                    icon = self._launcher.load_icon(
                        resolved.icon_name if resolved else "application-x-executable",
                        icon_size,
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
        """Unpin an item. If running, becomes transient; otherwise removed."""
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

        # Auto-pin if transient
        if not item.is_pinned:
            if item in self._transient:
                self._transient.remove(item)
            item.is_pinned = True
            self.pinned_items.append(item)

        # Now reorder within _pinned using the item's pinned index
        pinned_from = self.pinned_items.index(item)

        # Target: find what pinned index to_index maps to
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
        """Update config.pinned to reflect current pinned order."""
        self._config.pinned = [item.desktop_id for item in self.pinned_items]

    def notify(self) -> None:
        if self.on_change:
            self.on_change()
