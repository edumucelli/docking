# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Filesystem adapter for the reusable curved item-stack popup."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo

from docking.applets.popup import PopupAnchor
from docking.core.items import FOLDER_KIND
from docking.core.position import Position
from docking.i18n import _
from docking.log import get_logger
from docking.platform.targets import TargetService
from docking.ui.folder._browser import (
    FOLDER_SMALL_ICON_PX,
    FOLDER_SORT_OPTIONS,
    FolderBrowser,
    FolderPrefs,
    FolderRow,
)
from docking.ui.stack import (
    FOLDER_STACK_ACTION_ARROW_GAP_PX,
    FOLDER_STACK_ACTION_ARROW_SIZE_PX,
    FOLDER_STACK_ACTION_MAX_WIDTH_PX,
    FOLDER_STACK_LABEL_MAX_WIDTH_PX,
    FOLDER_STACK_LABEL_TEXT_MARGIN_PX,
    FOLDER_STACK_MAX_VISIBLE_ROWS,
    StackAction,
    StackCard,
    StackCardGeometry,
    StackContent,
    StackContentProvider,
    StackEntry,
    StackLayout,
    StackLayoutCache,
    StackPopupController,
    _measure_stack_text_px,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.ui.runtime import DockRuntime


FOLDER_STACK_REFRESH_DEBOUNCE_MS = 120
FOLDER_STACK_CONTENT_CACHE_MAX_ENTRIES = 32

FolderStackCard = StackCard
FolderStackCardGeometry = StackCardGeometry
FolderStackLayout = StackLayout

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

log = get_logger("folder.stack")

__all__ = [
    "FOLDER_STACK_ACTION_ARROW_GAP_PX",
    "FOLDER_STACK_ACTION_ARROW_SIZE_PX",
    "FOLDER_STACK_ACTION_MAX_WIDTH_PX",
    "FOLDER_STACK_LABEL_MAX_WIDTH_PX",
    "FOLDER_STACK_LABEL_TEXT_MARGIN_PX",
    "FolderStackCard",
    "FolderStackCardGeometry",
    "FolderStackController",
    "FolderStackLayout",
    "Gdk",
    "Gtk",
    "Pango",
    "PangoCairo",
    "_measure_stack_text_px",
]


class FolderStackCache(StackLayoutCache):
    """Generic layout cache plus the folder prewarm queue."""

    def __init__(self, target_service: TargetService) -> None:
        super().__init__()
        self._target_service = target_service
        self.prewarm_queue: list[DockItem] = []
        self.prewarm_targets: set[str] = set()
        self.prewarm_source: int = 0

    def queue_prewarm(self, item: DockItem, *, uri: str) -> bool:
        if uri in self.prewarm_targets:
            return False
        self.prewarm_targets.add(uri)
        self.prewarm_queue.append(item)
        return True

    def pop_next_prewarm(self) -> DockItem | None:
        if not self.prewarm_queue:
            return None
        item = self.prewarm_queue.pop(0)
        uri = self._target_service.normalize_file_target(item.target)
        if uri is not None:
            self.prewarm_targets.discard(uri)
        return item

    def invalidate_target(self, *, uri: str) -> None:
        self.invalidate_owner(uri)
        self.prewarm_targets.discard(uri)
        self.prewarm_queue = [
            item
            for item in self.prewarm_queue
            if self._target_service.normalize_file_target(item.target) != uri
        ]


class FolderStackController(StackPopupController):
    """Adapt folders, preferences, monitoring, and launch actions to a stack."""

    def __init__(
        self,
        *,
        config: Config,
        runtime: DockRuntime,
        dock_window: Gtk.Window,
        target_service: TargetService,
    ) -> None:
        super().__init__(
            config=config,
            runtime=runtime,
            dock_window=dock_window,
        )
        self._target_service = target_service
        self._icon_loader = target_service.icon_loader
        self._browser = FolderBrowser(
            target_service=target_service,
        )
        self._folder_stack_cache = FolderStackCache(target_service)
        self._folder_content_cache: dict[tuple[object, ...], StackContent] = {}
        self._folder_stack_item: DockItem | None = None
        self._folder_stack_monitor: Gio.FileMonitor | None = None
        self._folder_stack_refresh_source: int = 0

    def show(
        self,
        *,
        item: DockItem,
        anchor_x: int,
        anchor_y: int,
        icon_w: int,
        position: Any,
        toggle_if_same_item: bool = True,
    ) -> None:
        """Show the selected folder through the reusable stack controller."""
        was_open_for_item = self.open_owner_id() == item.desktop_id
        pos = Position(position)
        centered_anchor = PopupAnchor(
            x=(
                int(anchor_x + icon_w / 2)
                if pos in (Position.BOTTOM, Position.TOP)
                else int(anchor_x)
            ),
            y=(
                int(anchor_y + icon_w / 2)
                if pos in (Position.LEFT, Position.RIGHT)
                else int(anchor_y)
            ),
            position=pos,
        )
        shown = super().show_stack(
            owner_id=item.desktop_id,
            provider=lambda icon_px: self._stack_content_for_item(
                item=item,
                icon_px=icon_px,
            ),
            anchor=centered_anchor,
            toggle_if_same_owner=toggle_if_same_item,
            on_closed=self._cleanup_folder_state,
        )
        if shown and self.open_owner_id() == item.desktop_id:
            self._folder_stack_item = item
            if not was_open_for_item:
                self._track_folder_stack(target=item.target)

    def show_applet_stack(
        self,
        *,
        owner_id: str,
        provider: StackContentProvider,
        anchor_x: int,
        anchor_y: int,
        icon_w: int,
        position: Position,
        parent: Gtk.Window | None,
        toggle_if_same_owner: bool = True,
    ) -> bool:
        """Expose the generic stack surface to declarative applets."""
        pos = Position(position)
        centered_anchor = PopupAnchor(
            x=(
                int(anchor_x + icon_w / 2)
                if pos in (Position.BOTTOM, Position.TOP)
                else anchor_x
            ),
            y=(
                int(anchor_y + icon_w / 2)
                if pos in (Position.LEFT, Position.RIGHT)
                else anchor_y
            ),
            position=pos,
            parent=parent,
        )
        return super().show_stack(
            owner_id=owner_id,
            provider=provider,
            anchor=centered_anchor,
            toggle_if_same_owner=toggle_if_same_owner,
        )

    def open_item_id(self) -> str | None:
        item = self._folder_stack_item
        if item is None or self.open_owner_id() != item.desktop_id:
            return None
        return item.desktop_id

    def schedule_prewarm(self, item: DockItem) -> None:
        """Queue a folder data and layout warm-up during idle time."""
        if item.kind != FOLDER_KIND:
            return
        uri = self._target_service.normalize_file_target(item.target)
        if uri is None:
            return
        if not self._folder_stack_cache.queue_prewarm(item, uri=uri):
            return
        if self._folder_stack_cache.prewarm_source == 0:
            self._folder_stack_cache.prewarm_source = GLib.idle_add(
                self._drain_folder_stack_prewarm
            )

    def schedule_visible_prewarm(self, items: Sequence[DockItem]) -> None:
        for item in items:
            self.schedule_prewarm(item)

    def invalidate_target(self, target: str) -> None:
        self._browser.invalidate_target(target)
        uri = self._target_service.normalize_file_target(target)
        if uri is not None:
            self._folder_stack_cache.invalidate_target(uri=uri)
            for key in [key for key in self._folder_content_cache if key[0] == uri]:
                self._folder_content_cache.pop(key, None)

    def folder_prefs(self, item: DockItem) -> dict[str, Any]:
        return self._folder_prefs_for_item(item).to_dict()

    def sort_options(self) -> Sequence[tuple[str, str]]:
        return FOLDER_SORT_OPTIONS

    def list_directory(
        self,
        *,
        folder_item: DockItem,
        target: str,
        icon_px: int | None = None,
    ) -> list[dict[str, Any]]:
        return [
            row.as_dict()
            for row in self._list_directory_rows(
                folder_item=folder_item,
                target=target,
                icon_px=icon_px,
            )
        ]

    def icon_px(self, folder_item: DockItem) -> int:
        _ = folder_item
        return FOLDER_SMALL_ICON_PX

    def update_folder_pref(self, item: DockItem, key: str, value: Any) -> None:
        if key not in {"sort", "show_hidden"}:
            return
        prefs = self.folder_prefs(item)
        prefs[key] = value
        self._config.item_prefs[item.prefs_key or item.target] = prefs
        self._config.save()
        self._runtime.queue_draw()
        self.invalidate_target(item.target)
        self.schedule_prewarm(item)

    def _folder_prefs_for_item(self, item: DockItem) -> FolderPrefs:
        stored = dict(self._config.item_prefs.get(item.prefs_key or item.target, {}))
        return FolderPrefs.from_mapping(stored)

    def _list_directory_rows(
        self,
        *,
        folder_item: DockItem,
        target: str,
        icon_px: int | None = None,
    ) -> list[FolderRow]:
        return self._browser.list_directory(
            target=target,
            prefs=self._folder_prefs_for_item(folder_item),
            icon_px=icon_px,
        )

    def _stack_content_for_item(
        self,
        *,
        item: DockItem,
        icon_px: int,
    ) -> StackContent:
        if self._browser.target_state(item.target) == "missing":
            return StackContent(empty_label=_("Folder not found"))

        prefs = self._folder_prefs_for_item(item)
        uri = self._target_service.normalize_file_target(item.target) or item.target
        app_name = self._target_service.default_directory_app_name()
        cache_key = (
            uri,
            self._browser.cache_stamp(item.target),
            prefs.sort,
            prefs.show_hidden,
            icon_px,
            app_name,
        )
        cached = self._folder_stack_cache._get_lru(
            self._folder_content_cache,
            cache_key,
        )
        if cached is not None:
            return cached

        rows = self._list_directory_rows(
            folder_item=item,
            target=item.target,
            icon_px=icon_px,
        )
        if not rows:
            return StackContent(empty_label=_("Folder is empty"))

        visible_rows = rows[:FOLDER_STACK_MAX_VISIBLE_ROWS]
        hidden_count = max(len(rows) - len(visible_rows), 0)
        action_label = self._folder_stack_action_label(
            hidden_count=hidden_count,
            app_name=app_name,
        )
        entries = tuple(
            StackEntry(
                key=str(row["target"]),
                label=str(row["name"]),
                icon=row["icon"],
                activate=lambda target=str(row["target"]): (
                    self._open_folder_stack_target(target)
                ),
            )
            for row in visible_rows
        )
        content = StackContent(
            entries=entries,
            action=StackAction(
                key=item.target,
                label=action_label,
                activate=lambda: self._open_folder_stack_target(item.target),
            ),
            empty_label=_("Folder is empty"),
        )
        self._folder_stack_cache._put_lru(
            self._folder_content_cache,
            cache_key,
            content,
            max_entries=FOLDER_STACK_CONTENT_CACHE_MAX_ENTRIES,
        )
        return content

    def _folder_stack_action_label(
        self, *, hidden_count: int, app_name: str | None = None
    ) -> str:
        if app_name is None:
            app_name = self._target_service.default_directory_app_name()
        if app_name:
            return (
                _("Open in %s") % app_name
                if hidden_count == 0
                else _("%d More in %s") % (hidden_count, app_name)
            )
        return (
            _("Open Folder")
            if hidden_count == 0
            else _("%d More in Folder") % hidden_count
        )

    def _folder_stack_cards_for_item(
        self, item: DockItem
    ) -> tuple[list[FolderStackCard], int, int]:
        content = self._stack_content_for_item(
            item=item,
            icon_px=max(int(self._config.icon_size), 1),
        )
        previous_owner = self._stack_owner_id
        self._stack_owner_id = item.desktop_id
        try:
            return self._stack_cards_for_content(content)
        finally:
            self._stack_owner_id = previous_owner

    def _folder_stack_layout_for_item(self, item: DockItem) -> FolderStackLayout:
        content = self._stack_content_for_item(
            item=item,
            icon_px=max(int(self._config.icon_size), 1),
        )
        return self._stack_layout(owner_id=item.desktop_id, content=content)

    def _replace_folder_stack_content(
        self,
        item: DockItem | None = None,
        *,
        content: StackContent | None = None,
    ) -> None:
        if content is None:
            if item is None:
                return
            content = self._stack_content_for_item(
                item=item,
                icon_px=max(int(self._config.icon_size), 1),
            )
        self._stack_content = content
        super()._replace_stack_content(content=content)

    def _drain_folder_stack_prewarm(self) -> bool:
        item = self._folder_stack_cache.pop_next_prewarm()
        if item is None:
            self._folder_stack_cache.prewarm_source = 0
            return False
        self._folder_stack_layout_for_item(item)
        if not self._folder_stack_cache.prewarm_queue:
            self._folder_stack_cache.prewarm_source = 0
            return False
        return True

    def _track_folder_stack(self, target: str) -> None:
        uri = self._target_service.normalize_file_target(target)
        if uri is None:
            return
        if self._folder_stack_monitor is not None:
            self._folder_stack_monitor.cancel()
            self._folder_stack_monitor = None
        try:
            folder = Gio.File.new_for_uri(uri)
            monitor = folder.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self._on_folder_stack_changed)
            self._folder_stack_monitor = monitor
        except GLib.Error as exc:
            log.warning("Failed to monitor folder stack target %s: %s", target, exc)

    def _on_folder_stack_changed(
        self,
        _monitor: Gio.FileMonitor,
        _file: Gio.File,
        _other_file: Gio.File | None,
        _event_type: Gio.FileMonitorEvent,
    ) -> None:
        if self._folder_stack_item is not None:
            self.invalidate_target(self._folder_stack_item.target)
        if self._folder_stack_refresh_source:
            GLib.source_remove(self._folder_stack_refresh_source)
        self._folder_stack_refresh_source = GLib.timeout_add(
            FOLDER_STACK_REFRESH_DEBOUNCE_MS,
            self._refresh_folder_stack,
        )

    def _refresh_folder_stack(self) -> bool:
        self._folder_stack_refresh_source = 0
        item = self._folder_stack_item
        if item is None:
            return False
        if self._stack_provider is None:
            window = self._folder_stack_window
            if window is None:
                return False
            self._replace_folder_stack_content(item)
            self._restart_stack_animation()
            self._position_stack_window()
            window.show_all()
            return False
        self.refresh(owner_id=item.desktop_id)
        return False

    def _cleanup_folder_state(self) -> None:
        if self._folder_stack_refresh_source:
            GLib.source_remove(self._folder_stack_refresh_source)
            self._folder_stack_refresh_source = 0
        if self._folder_stack_monitor is not None:
            self._folder_stack_monitor.cancel()
            self._folder_stack_monitor = None
        self._folder_stack_item = None

    def _open_folder_stack_target(self, target: str) -> None:
        self._target_service.open_target(target)
        self._close_stack()

    def _activate_stack_key(self, key: str) -> None:
        if self._stack_content is None:
            self._open_folder_stack_target(key)
            self._close_stack()
            return
        super()._activate_stack_key(key)
