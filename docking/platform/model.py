"""Canonical dock state: visible item order, persistence, and applet ownership.

Why this module exists

The dock has many consumers of item state:

- the renderer needs a stable ordered list of visible items,
- hover/menu/dnd need consistent item identity,
- window tracking needs somewhere to publish running/active/urgent status,
- applets need lifecycle ownership,
- configuration needs pinned order persisted back to disk.

If each of those subsystems mutated its own partial state, the dock would drift.
The renderer might still think an applet exists after it was removed, or window
tracking might mark an app active without the pinned order reflecting the same
item set.

This module is the source of truth that prevents that.

What the model owns

The model owns "what items exist in the dock" and "in what order they appear".
It combines two logical populations:

1. pinned items
   Persistent entries loaded from config:
   - applications
   - applets
   - files/folders
   - separators

2. transient items
   Non-pinned running applications that should appear only while active.

Visual order is:

    visible_items = pinned_items + transient_items

That rule is simple, but it is the basis for almost every dock interaction.

ASCII view:

    pinned:    [ Files ][ Firefox ][ Clock ][ Separator ]
    transient: [ Slack ][ Discord ]

    visible:   [ Files ][ Firefox ][ Clock ][ Separator ][ Slack ][ Discord ]

Pinned order is user-owned and persisted. Transient order is runtime-owned and
disappears when the underlying windows are gone.

Uniform item identity

Every visible entry is represented as a `DockItem` with a stable `desktop_id`.
That identity model unifies several different kinds of entries:

- application launcher:
  `firefox.desktop`

- applet:
  `applet://clock`

- multi-instance applet/separator:
  `applet://separator#2`

- file/folder:
  stable file URI in the pinned entry target

The important point is not the exact string format. The important point is that
all UI layers can talk about "the same item" through one stable identity key.

What this module does not own

DockModel does not resolve desktop files or icons itself. It relies on:

- Launcher
  desktop metadata, icon loading, file target resolution

- WindowTracker
  aggregation of real runtime windows into running/active/urgent state

- UI layer
  drawing, hover, menus, drag/drop, popup behavior

The model applies and preserves state; it does not interpret window manager or
GTK behavior directly.

Applet ownership

Applets are more than rows in a list. They are live objects with lifecycle:

- create applet instance from registry,
- own the applet object while its DockItem exists,
- start it with dock notify callbacks,
- stop and remove it when the model removes that entry,
- keep item registry and applet registry in sync.

That is why applet ownership belongs in the model instead of being spread across
menus, config, and UI code.

Persistence responsibilities

The model is also the place where user intent becomes persisted order:

    menu / dnd / user action
      |
      +--> mutate pinned_items
      |
      +--> rebuild persisted PinnedEntry list
      |
      +--> config.save()

Keeping that flow in one place ensures the dock that the user sees is the dock
that will return next launch.

Core invariants

After every mutation, these must remain true:

1. `visible_items()` is safe to render immediately.
2. Pinned order matches persisted config order.
3. Applet registry and DockItem list agree on which applets exist.
4. Running/active/urgent fields on items are coherent with the latest
   WindowTracker aggregate.
5. Stable identifiers continue to point to the same conceptual item.

If these invariants hold, the rest of the dock can remain much simpler.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import gi

import docking.applets as applets
from docking.applets.identity import (
    APPLET_PREFIX,
    applet_desktop_id,
    applet_id_from,
    is_applet_desktop_id,
)
from docking.applets.separator import meta as _separator_meta
from docking.core.config import PinnedEntry, normalize_pinned_entries
from docking.core.items import (
    APP_KIND,
    APPLET_KIND,
    FILE_KIND,
    FOLDER_KIND,
    DockItem,
)
from docking.log import get_logger, with_context

gi.require_version("Gtk", "3.0")
from gi.repository import GLib

if TYPE_CHECKING:
    from docking.applets.base import Applet
    from docking.core.config import Config
    from docking.platform.launcher import Launcher

log = with_context(get_logger(name="model"))


class DockModel:
    """Ordered collection of dock items, merging pinned and running apps."""

    def __init__(self, config: Config, launcher: Launcher) -> None:
        self._config = config
        self._launcher = launcher
        self.pinned_items: list[DockItem] = []
        self._transient: list[DockItem] = []
        self._applets: dict[str, Applet] = {}
        self._animating_out: list[DockItem] = []
        self._change_listeners: list[Callable[[], None]] = []
        raw_pinned = self._config.pinned
        if raw_pinned and not isinstance(raw_pinned[0], PinnedEntry):
            self._config.pinned = normalize_pinned_entries(list(raw_pinned))

        self._load_pinned()

    def add_change_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback fired whenever model-visible state changes.

        Callbacks fire in registration order.
        """
        if callback not in self._change_listeners:
            self._change_listeners.append(callback)

    def remove_change_listener(self, callback: Callable[[], None]) -> None:
        """Remove a previously registered change callback."""
        try:
            self._change_listeners.remove(callback)
        except ValueError as exc:
            log.debug("Tried to remove unknown change listener: %s", exc)
            return

    def _load_pinned(self) -> None:
        """Load pinned items from config and resolve their desktop info."""
        for entry in self._config.pinned:
            item = self._build_pinned_item(entry=entry)
            if item is not None:
                self.pinned_items.append(item)

    def _build_pinned_item(self, entry: PinnedEntry) -> DockItem | None:
        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        if entry.kind == APPLET_KIND:
            did = applet_id_from(desktop_id=entry.target)
            cls = applets.load_applet_class(did)
            if cls:
                try:
                    applet = cls(icon_size=icon_size, config=self._config)
                    applet.item.desktop_id = entry.id
                    applet.item.kind = APPLET_KIND
                    applet.item.target = entry.target
                    applet.item.prefs_key = entry.target
                    applet.apply_prefs()
                    self._applets[entry.id] = applet
                    log.bind(applet_id=str(did), action="load_applet").info(
                        f"Loaded applet {did} (icon={applet.item.icon})"
                    )
                    return applet.item
                except Exception:
                    log.bind(applet_id=str(did), action="load_applet").exception(
                        f"Failed to create applet {did}"
                    )
            else:
                log.bind(applet_id=str(did), action="load_applet").warning(
                    f"Unknown applet id: {did}"
                )
            return None

        if entry.kind == APP_KIND:
            info = self._launcher.resolve(desktop_id=entry.target)
            if info is None:
                return None
            icon = self._launcher.load_icon(icon_name=info.icon_name, size=icon_size)
            return DockItem(
                desktop_id=entry.id,
                kind=APP_KIND,
                target=entry.target,
                name=info.name,
                icon_name=info.icon_name,
                wm_class=info.wm_class,
                is_pinned=True,
                icon=icon,
            )

        info = self._launcher.resolve_file(target=entry.target, size=icon_size)
        if info is None:
            return None
        return DockItem(
            desktop_id=entry.id,
            kind=entry.kind,
            target=entry.target,
            name=info.name,
            icon_name=info.icon_name,
            is_pinned=True,
            icon=info.icon,
            prefs_key=entry.target,
        )

    def get_applet(self, desktop_id: str) -> Applet | None:
        """Look up active applet by desktop_id."""
        return self._applets.get(desktop_id)

    def add_applet(self, applet_id: str) -> None:
        """Instantiate a applet and add to the dock."""
        did = applet_id
        if did not in applets.get_applet_catalog():
            log.bind(applet_id=applet_id, action="add_applet").warning(
                f"Invalid applet id: {applet_id}"
            )
            return

        desktop_id = applet_desktop_id(applet_id=did)
        if desktop_id in self._applets:
            log.bind(applet_id=str(did), action="add_applet").warning(
                f"Applet already present: {did}"
            )
            return
        cls = applets.load_applet_class(did)
        if not cls:
            log.bind(applet_id=str(did), action="add_applet").warning(
                f"No class registered for applet: {did}"
            )
            return
        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        try:
            applet = cls(icon_size=icon_size, config=self._config)
        except Exception:
            log.bind(applet_id=str(did), action="add_applet").exception(
                f"Failed to create applet {did}"
            )
            return
        self._applets[desktop_id] = applet
        applet.item.kind = APPLET_KIND
        applet.item.target = desktop_id
        applet.item.prefs_key = desktop_id
        applet.item.insert_factor = 0.0
        self.pinned_items.append(applet.item)
        applet.start(notify=self.notify)
        self.sync_pinned_to_config()
        self._config.save()
        self.notify()

    def add_separator(self, index: int = -1) -> None:
        """Add a separator instance at the given pinned index (-1 = end)."""
        cls = applets.load_applet_class(_separator_meta.id)
        if not cls:
            return

        # Find next unused instance number
        prefix = f"{APPLET_PREFIX}{_separator_meta.id}#"
        nums = [int(k[len(prefix) :]) for k in self._applets if k.startswith(prefix)]
        n = max(nums, default=-1) + 1
        desktop_id = applet_desktop_id(applet_id=_separator_meta.id, instance=n)

        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        try:
            applet = cls(icon_size=icon_size, config=self._config)
        except Exception:
            log.bind(applet_id="separator", action="add_separator").exception(
                "Failed to create separator",
            )
            return
        applet.item.desktop_id = desktop_id
        applet.item.kind = APPLET_KIND
        applet.item.target = desktop_id
        applet.item.prefs_key = desktop_id
        applet.item.insert_factor = 0.0
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
        """Stop and remove a applet from the dock (animated)."""
        applet = self._applets.pop(desktop_id, None)
        if applet:
            applet.stop()
            if applet.item in self.pinned_items:
                applet.item.removal_index = self.visible_items().index(applet.item)
                self.pinned_items.remove(applet.item)
                self._animating_out.append(applet.item)
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
        """All items to display with optional independent anchoring rules."""
        items = self.pinned_items + self._transient
        anchor_applets = self._config.anchor_applets
        anchor_files = self._config.anchor_files
        if not anchor_applets and not anchor_files:
            ordered = list(items)
        else:
            # Rebuild the visible order with the optional "anchor applets/files
            # to the end" policy applied. This is a presentation rule only; the
            # underlying pinned/transient ownership does not change here.
            regular: list[DockItem] = []
            files: list[DockItem] = []
            applet_items: list[DockItem] = []
            for item in items:
                if anchor_applets and item.kind == APPLET_KIND:
                    applet_items.append(item)
                elif anchor_files and item.kind in {FILE_KIND, FOLDER_KIND}:
                    files.append(item)
                else:
                    regular.append(item)
            ordered = regular + files + applet_items

        if not self._animating_out:
            return ordered

        result = list(ordered)
        # Removed items keep animating in their former visible slot instead of
        # being appended at the end. Appending them would make the shelf grow on
        # the trailing edge while the icon shrinks, which looks like the wrong
        # item is being removed.
        for item in sorted(
            self._animating_out,
            key=lambda outgoing: (
                outgoing.removal_index
                if outgoing.removal_index >= 0
                else len(ordered) + len(self._animating_out)
            ),
        ):
            if item.removal_index < 0 or item.removal_index > len(result):
                result.append(item)
            else:
                result.insert(item.removal_index, item)
        return result

    def find_by_desktop_id(self, desktop_id: str) -> DockItem | None:
        for item in self.pinned_items + self._transient:
            if item.desktop_id == desktop_id:
                return item
        return None

    def update_running(self, running: dict[str, dict[str, Any]]) -> None:
        """Update running state from WindowTracker data.

        Args:
            running: {desktop_id: {"count": int, "active": bool}}
        """
        # Reset running state (preserve is_urgent for transition detection)
        for item in self.pinned_items:
            if item.kind != APP_KIND:
                continue
            item.is_running = False
            item.is_active = False
            item.instance_count = 0

        # Update pinned items that are running
        matched_ids = set()
        for item in self.pinned_items:
            if item.kind != APP_KIND:
                continue
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
                            kind=APP_KIND,
                            target=desktop_id,
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
            self._persist_pinned_changes()
            self.notify()

    def unpin_item(self, desktop_id: str) -> None:
        """Unpin an item. If running, becomes transient; otherwise animated out.

        Applets are fully removed (stop + cleanup) since they can't be transient.
        """
        if is_applet_desktop_id(desktop_id=desktop_id):
            self.remove_applet(desktop_id=desktop_id)
            return
        item = next((p for p in self.pinned_items if p.desktop_id == desktop_id), None)
        if item:
            visible_index = self.visible_items().index(item)
            self.pinned_items.remove(item)
            item.is_pinned = False
            if item.kind == APP_KIND and item.is_running:
                item.removal_index = -1
                self._transient.append(item)
            else:
                item.removal_index = visible_index
                self._animating_out.append(item)
            self._persist_pinned_changes()
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

        self._persist_pinned_changes()
        self.notify()

    def sync_pinned_to_config(self) -> None:
        """Write current pinned_items order back to config (does not save to disk)."""
        self._config.pinned = [
            self._entry_from_item(item=item) for item in self.pinned_items
        ]

    def _persist_pinned_changes(self) -> None:
        """Sync current pinned order to config and flush it to disk."""
        self.sync_pinned_to_config()
        self._config.save()

    @staticmethod
    def _entry_from_item(item: DockItem) -> PinnedEntry:
        if item.kind == APPLET_KIND:
            return PinnedEntry(kind=APPLET_KIND, target=item.desktop_id)
        if item.kind == APP_KIND:
            return PinnedEntry(kind=APP_KIND, target=item.desktop_id)
        if item.kind == FOLDER_KIND:
            return PinnedEntry(kind=FOLDER_KIND, target=item.target)
        return PinnedEntry(kind=FILE_KIND, target=item.target)

    def notify(self) -> None:
        """Fire change callbacks to trigger redraws and side effects.

        Iterate over a shallow copy so listeners can unsubscribe themselves
        safely while notifications are in flight.
        """
        for callback in list(self._change_listeners):
            callback()

    def tick_animations(self) -> bool:
        """Advance insert/remove animations. Returns True if any are active."""
        speed = 0.12
        active = False

        # Grow newly inserted items
        for item in self.pinned_items + self._transient:
            if item.insert_factor < 1.0:
                item.insert_factor = min(1.0, item.insert_factor + speed)
                active = True

        # Shrink items being removed
        done = []
        for item in self._animating_out:
            item.insert_factor = max(0.0, item.insert_factor - speed)
            if item.insert_factor <= 0.0:
                done.append(item)
            else:
                active = True
        for item in done:
            self._animating_out.remove(item)
            item.removal_index = -1

        return active
