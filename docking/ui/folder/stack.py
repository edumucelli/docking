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

"""Folder stack popup, layout, drawing, and interaction."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo

import docking.platform.launcher as launcher_mod
from docking.core.items import FOLDER_KIND
from docking.core.position import Position
from docking.i18n import _
from docking.log import get_logger
from docking.ui.display import clamp_to_screen
from docking.ui.folder._browser import (
    FOLDER_SMALL_ICON_PX,
    FOLDER_SORT_OPTIONS,
    FolderBrowser,
    FolderPrefs,
    FolderRow,
)
from docking.ui.shelf import rounded_rect

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.platform.launcher import Launcher
    from docking.ui.runtime import DockRuntime


FOLDER_STACK_MAX_VISIBLE_ROWS = 9
FOLDER_STACK_GAP_PX = 8
FOLDER_STACK_POPUP_SIDE_PADDING_PX = 14
FOLDER_STACK_TOP_PADDING_PX = 6
FOLDER_STACK_ACTION_GAP_PX = 18
FOLDER_STACK_ICON_GAP_PX = 10
FOLDER_STACK_LABEL_HEIGHT_PX = 24
FOLDER_STACK_LABEL_MAX_WIDTH_PX = 148
FOLDER_STACK_ACTION_MAX_WIDTH_PX = 240
FOLDER_STACK_ROW_STEP_PX = 54
FOLDER_STACK_CURVE_X_PX = 40
FOLDER_STACK_ARC_BASE_SHIFT_PX = 8
FOLDER_STACK_ARC_RADIUS_FACTOR = 2.45
FOLDER_STACK_ARC_LINEAR_BLEND = 0.34
FOLDER_STACK_RIGHT_BLEED_PX = 24
FOLDER_STACK_LABEL_RADIUS_PX = 6
FOLDER_STACK_LABEL_TEXT_MARGIN_PX = 8
FOLDER_STACK_ACTION_ARROW_GAP_PX = 7
FOLDER_STACK_ACTION_ARROW_SIZE_PX = 7
FOLDER_STACK_ROTATION_MAX_DEG = 5.5
FOLDER_STACK_REVEAL_DURATION_MS = 160
FOLDER_STACK_REVEAL_STAGGER_MS = 28
FOLDER_STACK_ANIM_FRAME_MS = 16
FOLDER_STACK_HOVER_SCALE = 1.14
FOLDER_STACK_HOVER_EASE = 0.35
FOLDER_STACK_LAYOUT_CACHE_MAX_ENTRIES = 32
FOLDER_STACK_REFRESH_DEBOUNCE_MS = 120

log = get_logger("folder.stack")


@dataclass(frozen=True)
class FolderStackCard:
    label: str
    target: str | None
    icon: GdkPixbuf.Pixbuf | None
    icon_x: int
    icon_y: int
    icon_size: int
    label_x: int
    label_y: int
    label_w: int
    label_h: int
    centered: bool = False
    stack_progress: float = 0.0
    arc_span: float = 0.0


@dataclass(frozen=True)
class FolderStackCardGeometry:
    reveal: float
    hover_value: float
    rotation_radians: float
    icon_x: float
    icon_y: float
    icon_size: float
    icon_center_x: float
    icon_center_y: float
    label_x: float
    label_y: float


@dataclass(frozen=True)
class FolderStackLayout:
    cards: tuple[FolderStackCard, ...]
    popup_w: int
    popup_h: int
    fold_center_x: int


class FolderStackCache:
    """Bounded cache state for folder stack layouts and prewarm queue."""

    def __init__(self) -> None:
        self.layouts: dict[
            tuple[str, int, str, bool, int, str | None], FolderStackLayout
        ] = {}
        self.prewarm_queue: list[DockItem] = []
        self.prewarm_targets: set[str] = set()
        self.prewarm_source: int = 0

    @staticmethod
    def _get_lru(cache: dict[Any, Any], key: Any) -> Any | None:
        cached = cache.pop(key, None)
        if cached is not None:
            cache[key] = cached
        return cached

    @staticmethod
    def _put_lru(
        cache: dict[Any, Any], key: Any, value: Any, *, max_entries: int
    ) -> None:
        cache[key] = value
        while len(cache) > max_entries:
            cache.pop(next(iter(cache)))

    def get_layout(
        self, key: tuple[str, int, str, bool, int, str | None]
    ) -> FolderStackLayout | None:
        return self._get_lru(self.layouts, key)

    def put_layout(
        self,
        key: tuple[str, int, str, bool, int, str | None],
        layout: FolderStackLayout,
    ) -> None:
        self._put_lru(
            self.layouts,
            key,
            layout,
            max_entries=FOLDER_STACK_LAYOUT_CACHE_MAX_ENTRIES,
        )

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
        uri = launcher_mod.normalize_file_target(item.target)
        if uri is not None:
            self.prewarm_targets.discard(uri)
        return item

    def invalidate_target(self, *, uri: str) -> None:
        for key in [key for key in self.layouts if key[0] == uri]:
            self.layouts.pop(key, None)
        self.prewarm_targets.discard(uri)
        self.prewarm_queue = [
            item
            for item in self.prewarm_queue
            if launcher_mod.normalize_file_target(item.target) != uri
        ]


def _is_folder_stack_action_card(card: FolderStackCard) -> bool:
    return card.centered and card.target is not None and card.icon is None


def _ease_out_cubic(value: float) -> float:
    value = min(max(value, 0.0), 1.0)
    return 1.0 - (1.0 - value) ** 3


def _folder_stack_arc_offset(progress: float, span: float) -> float:
    progress = min(max(progress, 0.0), 1.0)
    max_offset = max(FOLDER_STACK_CURVE_X_PX, span * 0.08)
    linear = max_offset * progress
    if span <= 0:
        curved = linear
    else:
        radius = max(span * FOLDER_STACK_ARC_RADIUS_FACTOR, span + 1.0)
        y = progress * span
        curved = radius - math.sqrt(max(radius * radius - y * y, 0.0))
        max_curved = radius - math.sqrt(max(radius * radius - span * span, 0.0))
        curved = max_offset * (curved / max_curved) if max_curved > 0 else linear
    offset = (
        FOLDER_STACK_ARC_LINEAR_BLEND * linear
        + (1.0 - FOLDER_STACK_ARC_LINEAR_BLEND) * curved
    )
    return FOLDER_STACK_ARC_BASE_SHIFT_PX + offset


def _folder_stack_rotation(progress: float, position: Any, span: float) -> float:
    progress = min(max(progress, 0.0), 1.0)
    direction = 1.0 if position in {"bottom", "left"} else -1.0
    degrees = min(
        (0.2 + 0.8 * progress) * FOLDER_STACK_ROTATION_MAX_DEG,
        FOLDER_STACK_ROTATION_MAX_DEG,
    )
    return math.radians(degrees * direction)


def _measure_stack_text_px(text: str) -> int:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    cr = cairo.Context(surface)
    layout = PangoCairo.create_layout(cr)
    layout.set_text(text, -1)
    desc = Pango.FontDescription()
    desc.set_family("Sans")
    desc.set_size(10 * Pango.SCALE)
    layout.set_font_description(desc)
    _ink, logical = layout.get_pixel_extents()
    return max(int(logical.width), 0)


class FolderStackController:
    """Owns the left-click folder stack popup and visual interaction."""

    def __init__(
        self,
        *,
        config: Config,
        runtime: DockRuntime,
        launcher: Launcher | None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._launcher = launcher
        self._browser = FolderBrowser(launcher=launcher)
        self._folder_stack_cache = FolderStackCache()
        self._folder_stack_window: Gtk.Window | None = None
        self._folder_stack_revealer: Gtk.Revealer | None = None
        self._folder_stack_item: DockItem | None = None
        self._folder_stack_anchor_x: int = 0
        self._folder_stack_anchor_y: int = 0
        self._folder_stack_icon_w: int = 0
        self._folder_stack_fold_center_x: int = 0
        self._folder_stack_position_value = self._config.pos
        self._folder_stack_area: Gtk.DrawingArea | None = None
        self._folder_stack_cards: list[FolderStackCard] = []
        self._folder_stack_monitor: Gio.FileMonitor | None = None
        self._folder_stack_refresh_source: int = 0
        self._folder_stack_anim_source: int = 0
        self._folder_stack_show_started_us: int = 0
        self._folder_stack_hover_target: str | None = None
        self._folder_stack_hover_values: dict[str, float] = {}
        self._folder_stack_pressed_target: str | None = None

    def schedule_prewarm(self, item: DockItem) -> None:
        """Queue a folder stack warm-up during idle time."""
        if item.kind != FOLDER_KIND:
            return
        uri = launcher_mod.normalize_file_target(item.target)
        if uri is None:
            return
        if not self._folder_stack_cache.queue_prewarm(item, uri=uri):
            return
        if self._folder_stack_cache.prewarm_source == 0:
            self._folder_stack_cache.prewarm_source = GLib.idle_add(
                self._drain_folder_stack_prewarm
            )

    def schedule_visible_prewarm(self, items: Sequence[DockItem]) -> None:
        """Warm visible folder stacks so hover-open can render from cache."""
        for item in items:
            self.schedule_prewarm(item)

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
        """Show a folder stack popup, or optionally toggle it closed."""
        if (
            self._folder_stack_window is not None
            and self._folder_stack_window.get_visible()
            and self._folder_stack_item is not None
            and self._folder_stack_item.desktop_id == item.desktop_id
        ):
            if toggle_if_same_item:
                self._close_folder_stack()
            return

        self._close_folder_stack()
        self._runtime.hide_hover_ui()
        self._runtime.menu_popup_opened()

        window = self._ensure_folder_stack_window()
        revealer = self._folder_stack_revealer
        assert revealer is not None

        self._replace_folder_stack_content(item=item)

        self._folder_stack_anchor_x = int(anchor_x)
        self._folder_stack_anchor_y = int(anchor_y)
        self._folder_stack_icon_w = max(int(icon_w), 1)
        self._folder_stack_position_value = position
        self._folder_stack_item = item
        self._track_folder_stack(target=item.target)
        self._restart_folder_stack_animation()
        self._position_folder_stack_window()
        revealer.set_reveal_child(True)
        window.show_all()

    def close(self) -> None:
        self._close_folder_stack()

    def open_item_id(self) -> str | None:
        window = self._folder_stack_window
        if (
            window is None
            or not window.get_visible()
            or self._folder_stack_item is None
        ):
            return None
        return self._folder_stack_item.desktop_id

    def invalidate_target(self, target: str) -> None:
        self._browser.invalidate_target(target)
        uri = launcher_mod.normalize_file_target(target)
        if uri is not None:
            self._folder_stack_cache.invalidate_target(uri=uri)

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

    def _close_folder_stack(self) -> None:
        window = self._folder_stack_window
        if window is None or not window.get_visible():
            return
        revealer = self._folder_stack_revealer
        if revealer is not None:
            revealer.set_reveal_child(False)
        window.hide()
        self._cleanup_folder_stack()
        self._runtime.menu_popup_closed()

    def _cleanup_folder_stack(self) -> None:
        if self._folder_stack_refresh_source:
            GLib.source_remove(self._folder_stack_refresh_source)
            self._folder_stack_refresh_source = 0
        if self._folder_stack_anim_source:
            GLib.source_remove(self._folder_stack_anim_source)
            self._folder_stack_anim_source = 0
        if self._folder_stack_monitor is not None:
            self._folder_stack_monitor.cancel()
            self._folder_stack_monitor = None
        self._folder_stack_area = None
        self._folder_stack_item = None
        self._folder_stack_anchor_x = 0
        self._folder_stack_anchor_y = 0
        self._folder_stack_icon_w = 0
        self._folder_stack_fold_center_x = 0
        self._folder_stack_show_started_us = 0
        self._folder_stack_hover_target = None
        self._folder_stack_hover_values.clear()
        self._folder_stack_pressed_target = None

    def _ensure_folder_stack_window(self) -> Gtk.Window:
        if self._folder_stack_window is not None:
            return self._folder_stack_window

        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_decorated(False)
        window.set_skip_taskbar_hint(True)
        window.set_resizable(False)
        window.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        window.set_app_paintable(True)

        screen = window.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            window.set_visual(visual)

        revealer = Gtk.Revealer()
        revealer.set_transition_type(self._folder_stack_transition_type())
        revealer.set_transition_duration(140)
        revealer.set_reveal_child(False)
        window.add(revealer)

        self._folder_stack_window = window
        self._folder_stack_revealer = revealer
        return window

    def _folder_stack_transition_type(self):
        # Resolved lazily so test stubs of ``Gtk`` (monkeypatched at module
        # scope) are visible. A class-level dict would freeze the real
        # ``Gtk`` reference at import time.
        transitions = {
            Position.BOTTOM: Gtk.RevealerTransitionType.SLIDE_UP,
            Position.TOP: Gtk.RevealerTransitionType.SLIDE_DOWN,
            Position.LEFT: Gtk.RevealerTransitionType.SLIDE_RIGHT,
            Position.RIGHT: Gtk.RevealerTransitionType.SLIDE_LEFT,
        }
        return transitions[Position(self._config.pos)]

    def _replace_folder_stack_content(self, item: DockItem) -> None:
        revealer = self._folder_stack_revealer
        if revealer is None:
            return
        child = revealer.get_child()
        if child is not None:
            revealer.remove(child)
        content = self._build_folder_stack_content(item=item)
        revealer.add(content)
        content.show_all()

    def _position_folder_stack_window(self) -> None:
        window = self._folder_stack_window
        revealer = self._folder_stack_revealer
        if window is None or revealer is None:
            return
        child = revealer.get_child()
        if child is None:
            return

        preferred = child.get_preferred_size()[1]
        popup_w = max(int(preferred.width), 1)
        popup_h = max(int(preferred.height), 1)
        anchor_x = self._folder_stack_anchor_x
        anchor_y = self._folder_stack_anchor_y
        icon_w = max(self._folder_stack_icon_w, 1)
        pos = self._folder_stack_position_value
        local_icon_center_x = max(self._folder_stack_fold_center_x, 1)

        pos_enum = Position(pos)
        if pos_enum in (Position.BOTTOM, Position.TOP):
            popup_x = int(anchor_x + icon_w / 2 - local_icon_center_x)
            popup_y = (
                int(anchor_y - popup_h - FOLDER_STACK_GAP_PX)
                if pos_enum is Position.BOTTOM
                else int(anchor_y + FOLDER_STACK_GAP_PX)
            )
        else:
            popup_y = int(anchor_y + icon_w / 2 - popup_h / 2)
            popup_x = (
                int(anchor_x + FOLDER_STACK_GAP_PX)
                if pos_enum is Position.LEFT
                else int(anchor_x - popup_w - FOLDER_STACK_GAP_PX)
            )

        screen = window.get_screen()
        popup_pos = clamp_to_screen(
            popup_x,
            popup_y,
            popup_w,
            popup_h,
            screen.get_width(),
            screen.get_height(),
        )
        window.move(popup_pos.x, popup_pos.y)

    def _track_folder_stack(self, target: str) -> None:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return
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
        window = self._folder_stack_window
        item = self._folder_stack_item
        if window is None or item is None:
            return False
        self._replace_folder_stack_content(item=item)
        self._restart_folder_stack_animation()
        self._position_folder_stack_window()
        window.show_all()
        return False

    def _build_folder_stack_content(self, item: DockItem) -> Gtk.Widget:
        cards, popup_w, popup_h = self._folder_stack_cards_for_item(item)
        self._folder_stack_cards = cards

        area = Gtk.DrawingArea()
        area.set_size_request(popup_w, popup_h)
        area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        area.connect("draw", self._on_folder_stack_draw)
        area.connect("button-press-event", self._on_folder_stack_button_press)
        area.connect("button-release-event", self._on_folder_stack_button_release)
        area.connect("motion-notify-event", self._on_folder_stack_motion_notify)
        area.connect("leave-notify-event", self._on_folder_stack_leave_notify)
        self._folder_stack_area = area
        return area

    def _folder_stack_cards_for_item(
        self, item: DockItem
    ) -> tuple[list[FolderStackCard], int, int]:
        layout = self._folder_stack_layout_for_item(item)
        self._folder_stack_fold_center_x = layout.fold_center_x
        return list(layout.cards), layout.popup_w, layout.popup_h

    def _folder_stack_layout_for_item(self, item: DockItem) -> FolderStackLayout:
        prefs = self._folder_prefs_for_item(item)
        icon_px = max(int(self._config.icon_size), 1)
        app_name = (
            self._launcher.default_directory_app_name()
            if self._launcher is not None
            else None
        )
        uri = launcher_mod.normalize_file_target(item.target) or item.target
        state = self._browser.target_state(item.target)
        if state == "missing":
            return self._compute_folder_stack_layout(
                item=item,
                icon_px=icon_px,
                app_name=app_name,
                state=state,
            )
        cache_key = (
            uri,
            self._browser.cache_stamp(item.target),
            str(prefs.sort),
            bool(prefs.show_hidden),
            icon_px,
            app_name,
        )
        cached = self._folder_stack_cache.get_layout(cache_key)
        if cached is not None:
            return cached

        layout = self._compute_folder_stack_layout(
            item=item,
            icon_px=icon_px,
            app_name=app_name,
            state=state,
        )
        self._folder_stack_cache.put_layout(cache_key, layout)
        return layout

    def _compute_folder_stack_layout(
        self,
        *,
        item: DockItem,
        icon_px: int,
        app_name: str | None,
        state: str,
    ) -> FolderStackLayout:
        cards: list[FolderStackCard] = []
        label_h = FOLDER_STACK_LABEL_HEIGHT_PX
        row_step = max(FOLDER_STACK_ROW_STEP_PX, round(icon_px * 1.08))
        curve_extent = max(FOLDER_STACK_CURVE_X_PX, round(icon_px * 0.65))
        right_bleed = max(
            FOLDER_STACK_RIGHT_BLEED_PX,
            round(curve_extent + icon_px * FOLDER_STACK_HOVER_SCALE * 0.35),
        )
        fold_center_x = int(
            FOLDER_STACK_POPUP_SIDE_PADDING_PX
            + FOLDER_STACK_LABEL_MAX_WIDTH_PX
            + FOLDER_STACK_ICON_GAP_PX
            + icon_px / 2
        )

        if state == "missing":
            return self._centered_layout(
                label=_("Folder not found"),
                label_h=label_h,
                fold_center_x=fold_center_x,
                icon_px=icon_px,
                right_bleed=right_bleed,
            )

        rows = self._list_directory_rows(
            folder_item=item,
            target=item.target,
            icon_px=icon_px,
        )
        if not rows:
            return self._centered_layout(
                label=_("Folder is empty"),
                label_h=label_h,
                fold_center_x=fold_center_x,
                icon_px=icon_px,
                right_bleed=right_bleed,
            )

        visible_rows = list(rows)[:FOLDER_STACK_MAX_VISIBLE_ROWS]
        hidden_count = max(len(rows) - len(visible_rows), 0)
        action_label = self._folder_stack_action_label(
            hidden_count=hidden_count,
            app_name=app_name,
        )
        chip_w = self._folder_stack_action_width(label=action_label)
        chip_h = label_h
        total_rows = len(visible_rows)
        top_progress = 1.0 if total_rows > 0 else 0.0
        total_span = (total_rows - 1) * row_step
        top_center_x = round(
            fold_center_x + _folder_stack_arc_offset(top_progress, total_span)
        )
        chip_x = max(
            FOLDER_STACK_POPUP_SIDE_PADDING_PX,
            int(top_center_x - chip_w / 2 + curve_extent * 0.1),
        )
        chip_y = FOLDER_STACK_TOP_PADDING_PX
        cards.append(
            FolderStackCard(
                label=action_label,
                target=item.target,
                icon=None,
                icon_x=0,
                icon_y=0,
                icon_size=0,
                label_x=chip_x,
                label_y=chip_y,
                label_w=chip_w,
                label_h=chip_h,
                centered=True,
                stack_progress=1.0,
                arc_span=float(total_span),
            )
        )

        stack_top = chip_y + chip_h + FOLDER_STACK_ACTION_GAP_PX
        max_right = chip_x + chip_w
        bottom_center_y = (
            stack_top + (total_rows - 1) * row_step + icon_px / 2 if total_rows else 0
        )
        for index, child in enumerate(visible_rows):
            raw_progress = (
                (total_rows - 1 - index) / max(total_rows - 1, 1)
                if total_rows > 1
                else 1.0
            )
            arc_progress = raw_progress
            icon_center_x = fold_center_x + _folder_stack_arc_offset(
                arc_progress,
                total_span,
            )
            icon_center_y = bottom_center_y - total_span * raw_progress
            icon_x = round(icon_center_x - icon_px / 2)
            icon_y = round(icon_center_y - icon_px / 2)
            name = str(child["name"])
            label_w = self._folder_stack_label_width(label=name)
            label_pull = round(arc_progress * 10)
            label_x = max(
                FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                icon_x - FOLDER_STACK_ICON_GAP_PX - label_w - label_pull,
            )
            cards.append(
                FolderStackCard(
                    label=name,
                    target=str(child["target"]),
                    icon=child["icon"],
                    icon_x=icon_x,
                    icon_y=icon_y,
                    icon_size=icon_px,
                    label_x=label_x,
                    label_y=icon_y + max(int((icon_px - label_h) / 2), 0),
                    label_w=label_w,
                    label_h=label_h,
                    centered=False,
                    stack_progress=arc_progress,
                    arc_span=float(total_span),
                )
            )
            max_right = max(max_right, icon_x + icon_px)

        popup_w = int(
            max(
                max_right + right_bleed,
                fold_center_x
                + _folder_stack_arc_offset(1.0, total_span)
                + icon_px / 2
                + right_bleed,
            )
        )
        popup_h = (
            stack_top
            + (total_rows - 1) * row_step
            + icon_px
            + FOLDER_STACK_TOP_PADDING_PX
        )
        return FolderStackLayout(
            cards=tuple(cards),
            popup_w=popup_w,
            popup_h=popup_h,
            fold_center_x=fold_center_x,
        )

    def _centered_layout(
        self,
        *,
        label: str,
        label_h: int,
        fold_center_x: int,
        icon_px: int,
        right_bleed: int,
    ) -> FolderStackLayout:
        label_w = 190
        card = FolderStackCard(
            label=label,
            target=None,
            icon=None,
            icon_x=0,
            icon_y=0,
            icon_size=0,
            label_x=max(
                FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                int(fold_center_x - label_w / 2),
            ),
            label_y=FOLDER_STACK_TOP_PADDING_PX,
            label_w=label_w,
            label_h=label_h,
            centered=True,
        )
        popup_w = int(
            max(
                fold_center_x + label_w / 2 + FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                fold_center_x + icon_px / 2 + right_bleed,
            )
        )
        popup_h = label_h + 2 * FOLDER_STACK_TOP_PADDING_PX
        return FolderStackLayout(
            cards=(card,),
            popup_w=popup_w,
            popup_h=popup_h,
            fold_center_x=fold_center_x,
        )

    def _on_folder_stack_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        now_us = GLib.get_monotonic_time()
        total_cards = len(self._folder_stack_cards)
        for draw_index, card in enumerate(self._folder_stack_cards):
            self._draw_folder_stack_card(
                cr=cr,
                card=card,
                sequence_index=total_cards - 1 - draw_index,
                now_us=now_us,
            )
        return False

    def _folder_stack_card_geometry(
        self,
        *,
        card: FolderStackCard,
        sequence_index: int,
        now_us: int,
    ) -> FolderStackCardGeometry | None:
        reveal = self._folder_stack_reveal_progress(
            sequence_index=sequence_index,
            now_us=now_us,
        )
        if reveal <= 0:
            return None

        hover_value = (
            self._folder_stack_hover_values.get(card.target, 0.0)
            if card.target is not None and not card.centered
            else 0.0
        )
        y_offset = (1.0 - reveal) * 18.0
        rotation_radians = (
            _folder_stack_rotation(
                card.stack_progress,
                self._folder_stack_position_value,
                card.arc_span,
            )
            * reveal
        )
        open_label_center_x = card.label_x + card.label_w / 2
        label_center_x = (
            self._folder_stack_fold_center_x
            + (open_label_center_x - self._folder_stack_fold_center_x) * reveal
        )
        label_x = label_center_x - card.label_w / 2
        label_y = card.label_y + y_offset

        icon_size = 0.0
        icon_x = 0.0
        icon_y = 0.0
        icon_center_x = 0.0
        icon_center_y = 0.0
        if card.icon is not None and card.icon_size > 0:
            icon_size = max(
                (
                    card.icon_size
                    * (0.82 + 0.18 * reveal)
                    * (1.0 + hover_value * (FOLDER_STACK_HOVER_SCALE - 1.0))
                ),
                1.0,
            )
            open_icon_center_x = card.icon_x + card.icon_size / 2
            icon_center_x = (
                self._folder_stack_fold_center_x
                + (open_icon_center_x - self._folder_stack_fold_center_x) * reveal
            )
            icon_center_y = (
                card.icon_y + card.icon_size / 2 + y_offset - hover_value * 4.0
            )
            icon_x = icon_center_x - icon_size / 2
            icon_y = icon_center_y - icon_size / 2

        return FolderStackCardGeometry(
            reveal=reveal,
            hover_value=hover_value,
            rotation_radians=rotation_radians,
            icon_x=icon_x,
            icon_y=icon_y,
            icon_size=icon_size,
            icon_center_x=icon_center_x,
            icon_center_y=icon_center_y,
            label_x=label_x,
            label_y=label_y,
        )

    def _draw_folder_stack_card(
        self,
        *,
        cr: cairo.Context,
        card: FolderStackCard,
        sequence_index: int,
        now_us: int,
    ) -> None:
        geometry = self._folder_stack_card_geometry(
            card=card,
            sequence_index=sequence_index,
            now_us=now_us,
        )
        if geometry is None:
            return
        is_action_card = _is_folder_stack_action_card(card)

        if card.icon is not None and card.icon_size > 0:
            pixbuf = card.icon
            draw_icon_size = max(round(geometry.icon_size), 1)
            if (
                pixbuf.get_width() != draw_icon_size
                or pixbuf.get_height() != draw_icon_size
            ):
                scaled = pixbuf.scale_simple(
                    draw_icon_size,
                    draw_icon_size,
                    GdkPixbuf.InterpType.BILINEAR,
                )
                if scaled is not None:
                    pixbuf = scaled

            cr.save()
            cr.translate(geometry.icon_center_x + 2, geometry.icon_center_y + 2)
            cr.rotate(geometry.rotation_radians)
            Gdk.cairo_set_source_pixbuf(
                cr,
                pixbuf,
                -draw_icon_size / 2,
                -draw_icon_size / 2,
            )
            cr.paint_with_alpha(0.16 * geometry.reveal)
            cr.restore()

            cr.save()
            cr.translate(geometry.icon_center_x, geometry.icon_center_y)
            cr.rotate(geometry.rotation_radians)
            Gdk.cairo_set_source_pixbuf(
                cr,
                pixbuf,
                -draw_icon_size / 2,
                -draw_icon_size / 2,
            )
            cr.paint_with_alpha(0.55 + 0.45 * geometry.reveal)
            cr.restore()

        radius = FOLDER_STACK_LABEL_RADIUS_PX
        label_center_x = geometry.label_x + card.label_w / 2
        label_center_y = geometry.label_y + card.label_h / 2
        cr.save()
        cr.translate(label_center_x, label_center_y + 1)
        cr.rotate(geometry.rotation_radians * 0.85)
        rounded_rect(
            cr,
            -card.label_w / 2,
            -card.label_h / 2,
            card.label_w,
            card.label_h,
            radius,
        )
        cr.set_source_rgba(0, 0, 0, 0.08 * geometry.reveal)
        cr.fill()
        cr.restore()

        cr.save()
        cr.translate(label_center_x, label_center_y)
        cr.rotate(geometry.rotation_radians * 0.85)
        rounded_rect(
            cr,
            -card.label_w / 2,
            -card.label_h / 2,
            card.label_w,
            card.label_h,
            radius,
        )
        cr.set_source_rgba(0.98, 0.98, 0.98, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.08)
        cr.set_line_width(1.0)
        cr.stroke()
        cr.restore()

        cr.save()
        layout = PangoCairo.create_layout(cr)
        layout.set_text(card.label, -1)
        desc = Pango.FontDescription()
        desc.set_family("Sans")
        desc.set_size(10 * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        arrow_reserve = (
            FOLDER_STACK_ACTION_ARROW_GAP_PX + FOLDER_STACK_ACTION_ARROW_SIZE_PX
            if is_action_card
            else 0
        )
        available_text_w = max(
            int(card.label_w - 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX - arrow_reserve),
            1,
        )
        layout.set_width(available_text_w * Pango.SCALE)
        layout.set_alignment(Pango.Alignment.CENTER)
        _, logical = layout.get_pixel_extents()
        text_y = int(-card.label_h / 2 + (card.label_h - logical.height) / 2)
        text_x = -card.label_w / 2 + FOLDER_STACK_LABEL_TEXT_MARGIN_PX
        cr.set_source_rgba(0.16, 0.2, 0.26, 1.0)
        cr.translate(label_center_x, label_center_y)
        cr.rotate(geometry.rotation_radians * 0.85)
        cr.move_to(text_x, text_y)
        PangoCairo.show_layout(cr, layout)
        if is_action_card:
            arrow_center_x = (
                card.label_w / 2
                - FOLDER_STACK_LABEL_TEXT_MARGIN_PX
                - FOLDER_STACK_ACTION_ARROW_SIZE_PX / 2
            )
            half = FOLDER_STACK_ACTION_ARROW_SIZE_PX / 2
            cr.set_line_width(1.4)
            cr.set_line_cap(cairo.LineCap.ROUND)
            cr.set_line_join(cairo.LineJoin.ROUND)
            cr.move_to(arrow_center_x - half, -half)
            cr.line_to(arrow_center_x, 0.0)
            cr.line_to(arrow_center_x - half, half)
            cr.stroke()
        cr.restore()

    def _folder_stack_card_at(self, x: float, y: float) -> FolderStackCard | None:
        now_us = GLib.get_monotonic_time()
        total_cards = len(self._folder_stack_cards)
        for index in range(total_cards - 1, -1, -1):
            card = self._folder_stack_cards[index]
            geometry = self._folder_stack_card_geometry(
                card=card,
                sequence_index=total_cards - 1 - index,
                now_us=now_us,
            )
            if geometry is None:
                continue
            within_label = (
                geometry.label_x <= x <= geometry.label_x + card.label_w
                and geometry.label_y <= y <= geometry.label_y + card.label_h
            )
            within_icon = (
                geometry.icon_size > 0
                and geometry.icon_x <= x <= geometry.icon_x + geometry.icon_size
                and geometry.icon_y <= y <= geometry.icon_y + geometry.icon_size
            )
            if within_label or within_icon:
                return card
        return None

    def _on_folder_stack_button_press(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        if int(event.button) != 1:
            self._folder_stack_pressed_target = None
            return False
        card = self._folder_stack_card_at(event.x, event.y)
        self._folder_stack_pressed_target = (
            card.target if card is not None and card.target is not None else None
        )
        return self._folder_stack_pressed_target is not None

    def _on_folder_stack_button_release(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        if int(event.button) != 1:
            self._folder_stack_pressed_target = None
            return False
        card = self._folder_stack_card_at(event.x, event.y)
        target = card.target if card is not None and card.target is not None else None
        pressed_target = self._folder_stack_pressed_target
        self._folder_stack_pressed_target = None
        if target is not None and (pressed_target is None or pressed_target == target):
            self._open_folder_stack_target(target)
            return True
        return False

    def _on_folder_stack_motion_notify(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventMotion
    ) -> bool:
        card = self._folder_stack_card_at(event.x, event.y)
        target = (
            card.target
            if card is not None and card.target is not None and not card.centered
            else None
        )
        if target != self._folder_stack_hover_target:
            self._folder_stack_hover_target = target
            self._ensure_folder_stack_animating()
        return False

    def _on_folder_stack_leave_notify(
        self, _widget: Gtk.DrawingArea, _event: Gdk.EventCrossing
    ) -> bool:
        if self._folder_stack_hover_target is not None:
            self._folder_stack_hover_target = None
            self._ensure_folder_stack_animating()
        self._folder_stack_pressed_target = None
        return False

    def _folder_stack_action_label(
        self, *, hidden_count: int, app_name: str | None = None
    ) -> str:
        if app_name is None and self._launcher is not None:
            app_name = self._launcher.default_directory_app_name()
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

    def _folder_stack_action_width(self, *, label: str) -> int:
        return min(
            FOLDER_STACK_ACTION_MAX_WIDTH_PX,
            _measure_stack_text_px(label)
            + 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX
            + FOLDER_STACK_ACTION_ARROW_GAP_PX
            + FOLDER_STACK_ACTION_ARROW_SIZE_PX
            + 10,
        )

    def _folder_stack_label_width(self, *, label: str) -> int:
        return min(
            FOLDER_STACK_LABEL_MAX_WIDTH_PX,
            max(
                24,
                _measure_stack_text_px(label)
                + 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX
                + 10,
            ),
        )

    def _restart_folder_stack_animation(self) -> None:
        self._folder_stack_show_started_us = GLib.get_monotonic_time()
        self._folder_stack_hover_target = None
        self._folder_stack_hover_values.clear()
        self._ensure_folder_stack_animating()

    def _ensure_folder_stack_animating(self) -> None:
        area = self._folder_stack_area
        if area is None:
            return
        area.queue_draw()
        if self._folder_stack_anim_source == 0:
            self._folder_stack_anim_source = GLib.timeout_add(
                FOLDER_STACK_ANIM_FRAME_MS,
                self._on_folder_stack_animation_frame,
            )

    def _on_folder_stack_animation_frame(self) -> bool:
        area = self._folder_stack_area
        window = self._folder_stack_window
        if area is None or window is None or not window.get_visible():
            self._folder_stack_anim_source = 0
            return False

        active = False
        now_us = GLib.get_monotonic_time()
        elapsed_ms = max((now_us - self._folder_stack_show_started_us) / 1000.0, 0.0)
        reveal_budget_ms = (
            FOLDER_STACK_REVEAL_DURATION_MS
            + max(
                len(self._folder_stack_cards) - 1,
                0,
            )
            * FOLDER_STACK_REVEAL_STAGGER_MS
        )
        if elapsed_ms < reveal_budget_ms:
            active = True

        for card in self._folder_stack_cards:
            if card.target is None or card.centered:
                continue
            current = self._folder_stack_hover_values.get(card.target, 0.0)
            target = 1.0 if self._folder_stack_hover_target == card.target else 0.0
            updated = current + (target - current) * FOLDER_STACK_HOVER_EASE
            if abs(updated - target) < 0.02:
                updated = target
            if updated <= 0.0 and target == 0.0:
                self._folder_stack_hover_values.pop(card.target, None)
            else:
                self._folder_stack_hover_values[card.target] = updated
            if updated != target:
                active = True

        area.queue_draw()
        if not active:
            self._folder_stack_anim_source = 0
            return False
        return True

    def _folder_stack_reveal_progress(
        self, *, sequence_index: int, now_us: int
    ) -> float:
        if self._folder_stack_show_started_us <= 0:
            return 1.0
        elapsed_ms = max((now_us - self._folder_stack_show_started_us) / 1000.0, 0.0)
        elapsed_ms -= sequence_index * FOLDER_STACK_REVEAL_STAGGER_MS
        if elapsed_ms <= 0:
            return 0.0
        return _ease_out_cubic(elapsed_ms / FOLDER_STACK_REVEAL_DURATION_MS)

    def _open_folder_stack_target(self, target: str) -> None:
        launcher_mod.open_target(target)
        self._close_folder_stack()
