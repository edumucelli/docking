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

"""Reusable curved item-stack popup, layout, drawing, and interaction."""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo

from docking.applets.popup import PopupAnchor
from docking.core.position import Position
from docking.i18n import _
from docking.log import get_logger
from docking.platform.backends.base import Rect, Size
from docking.ui.display import (
    clamp_popup,
    popup_workarea,
    scrolled_popup_size,
    window_surface_service,
)
from docking.ui.shelf import rounded_rect

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.ui.runtime import DockRuntime


FOLDER_STACK_MAX_VISIBLE_ROWS = 9
FOLDER_STACK_GAP_PX = 8
FOLDER_STACK_POPUP_SIDE_PADDING_PX = 14
FOLDER_STACK_TOP_PADDING_PX = 6
K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
FOLDER_STACK_ACTION_GAP_PX = 18
FOLDER_STACK_ICON_GAP_PX = 10
FOLDER_STACK_LABEL_HEIGHT_PX = 24
FOLDER_STACK_LABEL_MAX_WIDTH_PX = 148
FOLDER_STACK_ACTION_MAX_WIDTH_PX = 240
FOLDER_STACK_ROW_STEP_PX = 54
FOLDER_STACK_CURVE_X_PX = 40
FOLDER_STACK_CURVE_MIN_ROWS = 4
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
STACK_THUMBNAIL_RADIUS_FACTOR = 3.0 / 48
STACK_THUMBNAIL_SHADOW_OFFSET_FACTOR = 1.5 / 48
STACK_THUMBNAIL_SHADOW_ALPHA = 0.10

log = get_logger("stack")


def stack_available_size(
    bounds: Rect, anchor_x: int, anchor_y: int, position: Position
) -> Size:
    """Space facing away from the dock, within the anchor monitor's workarea."""
    width, height = bounds.width, bounds.height
    if position == Position.BOTTOM:
        height = min(height, anchor_y - FOLDER_STACK_GAP_PX - bounds.y)
    elif position == Position.TOP:
        height = min(height, bounds.bottom - anchor_y - FOLDER_STACK_GAP_PX)
    elif position == Position.LEFT:
        width = min(width, bounds.right - anchor_x - FOLDER_STACK_GAP_PX)
    else:
        width = min(width, anchor_x - FOLDER_STACK_GAP_PX - bounds.x)
    return Size(max(1, width), max(1, height))


@dataclass(frozen=True)
class StackEntry:
    """One presentation-ready entry in a reusable item stack."""

    key: str
    label: str
    icon: GdkPixbuf.Pixbuf | None
    activate: Callable[[], None]
    thumbnail_style: bool = False


@dataclass(frozen=True)
class StackAction:
    """Optional action chip displayed above the item stack."""

    key: str
    label: str
    activate: Callable[[], None]


@dataclass(frozen=True)
class StackContent:
    """Current snapshot supplied by a folder or applet stack provider."""

    entries: tuple[StackEntry, ...] = ()
    action: StackAction | None = None
    empty_label: str = _("No items")


StackContentProvider = Callable[[int], StackContent | None]


@dataclass(frozen=True)
class StackCard:
    target: str | None
    label: str
    icon: GdkPixbuf.Pixbuf | None
    icon_x: int
    icon_y: int
    icon_size: int
    label_x: int
    label_y: int
    label_w: int
    label_h: int
    centered: bool = False
    action: bool = False
    stack_progress: float = 0.0
    arc_span: float = 0.0
    thumbnail_style: bool = False

    @property
    def key(self) -> str | None:
        """Stable interaction key (legacy cards called this target)."""
        return self.target


@dataclass(frozen=True)
class StackCardGeometry:
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
class StackLayout:
    cards: tuple[StackCard, ...]
    popup_w: int
    popup_h: int
    fold_center_x: int


def _draw_rounded_stack_thumbnail(
    cr: cairo.Context,
    pixbuf: GdkPixbuf.Pixbuf,
    size: int,
    geometry: StackCardGeometry,
) -> None:
    """Rotate a rounded image and its alpha-masked shadow as one unit."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    image_cr = cairo.Context(surface)
    rounded_rect(image_cr, 0, 0, size, size, size * STACK_THUMBNAIL_RADIUS_FACTOR)
    image_cr.clip()
    Gdk.cairo_set_source_pixbuf(image_cr, pixbuf, 0, 0)
    image_cr.paint()

    half = size / 2
    offset = size * STACK_THUMBNAIL_SHADOW_OFFSET_FACTOR
    cr.save()
    cr.translate(geometry.icon_center_x, geometry.icon_center_y)
    cr.rotate(geometry.rotation_radians)
    cr.set_source_rgba(0, 0, 0, STACK_THUMBNAIL_SHADOW_ALPHA * geometry.reveal)
    cr.mask_surface(surface, -half + offset, -half + offset)
    cr.set_source_surface(surface, -half, -half)
    cr.paint_with_alpha(0.55 + 0.45 * geometry.reveal)
    cr.restore()


class StackLayoutCache:
    """Bounded cache for reusable stack layouts."""

    def __init__(self) -> None:
        self.layouts: dict[tuple[object, ...], StackLayout] = {}

    @staticmethod
    def _get_lru(cache: dict[K, V], key: K) -> V | None:
        cached = cache.pop(key, None)
        if cached is not None:
            cache[key] = cached
        return cached

    @staticmethod
    def _put_lru(cache: dict[K, V], key: K, value: V, *, max_entries: int) -> None:
        cache[key] = value
        while len(cache) > max_entries:
            cache.pop(next(iter(cache)))

    def get_layout(self, key: tuple[object, ...]) -> StackLayout | None:
        return self._get_lru(self.layouts, key)

    def put_layout(
        self,
        key: tuple[object, ...],
        layout: StackLayout,
    ) -> None:
        self._put_lru(
            self.layouts,
            key,
            layout,
            max_entries=FOLDER_STACK_LAYOUT_CACHE_MAX_ENTRIES,
        )

    def invalidate_owner(self, owner_id: str) -> None:
        for key in [key for key in self.layouts if key[0] == owner_id]:
            self.layouts.pop(key, None)


def _is_stack_action_card(card: StackCard) -> bool:
    return card.action or (
        card.centered and card.target is not None and card.icon is None
    )


def _ease_out_cubic(value: float) -> float:
    value = min(max(value, 0.0), 1.0)
    return 1.0 - (1.0 - value) ** 3


def _stack_arc_offset(progress: float, span: float) -> float:
    """Return the horizontal distance from the stack's fold center.

    The stack is laid out vertically, but its rows drift to the right as they
    rise away from the dock icon. A purely linear drift would look like a
    diagonal list, while an unmodified circular arc would stay almost vertical
    near the fold and turn too sharply near the top. This function blends the
    two shapes so the stack reads as a smooth fan:

    1. ``progress`` is the row's normalized vertical position on the arc.
       ``0`` is the bottom row next to the fold and ``1`` is the top of a full
       arc. Values are clamped because reveal and hover animation code may
       briefly produce values outside that range.
    2. ``span`` is the vertical height represented by the complete curve. It
       is deliberately not always the visible content height; short stacks use
       the span of a four-row reference curve so adjacent rows remain adjacent
       samples of the same shape.
    3. ``max_offset`` is the horizontal travel of a complete arc. It is at
       least :data:`FOLDER_STACK_CURVE_X_PX`, but grows slightly for unusually
       tall stacks so their upper rows do not look almost vertical.
    4. The linear candidate is simply ``max_offset * progress``.
    5. The curved candidate is taken from the right-hand displacement of a
       circle. For radius ``r`` and vertical coordinate ``y``::

           y = progress * span
           circular_x = r - sqrt(r**2 - y**2)

       ``circular_x`` is divided by its value at ``y == span`` and multiplied
       by ``max_offset``. Normalization is important: changing the radius then
       changes the *shape* of the curve without changing its final horizontal
       extent.
    6. The final shape mixes the linear and circular candidates. The linear
       contribution prevents the lower rows from collapsing onto one another;
       the circular contribution supplies the visibly increasing bend toward
       the top.
    7. :data:`FOLDER_STACK_ARC_BASE_SHIFT_PX` is added last. Even the bottom
       row therefore sits slightly to the right of the source icon's fold
       center instead of being drawn directly on top of it.

    The radius is proportional to ``span``, so the normalized curve has the
    same character at different row counts and icon sizes. Layout decides
    which progress samples are visible; this function only converts one such
    sample into an x offset.
    """
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


def _stack_rotation(progress: float, position: Any, span: float) -> float:
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


class StackPopupController:
    """Own the reusable curved stack popup and visual interaction."""

    def __init__(
        self,
        *,
        config: Config,
        runtime: DockRuntime,
        dock_window: Gtk.Window,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._dock_window = dock_window
        self._folder_stack_cache = StackLayoutCache()
        self._folder_stack_window: Gtk.Window | None = None
        self._folder_stack_revealer: Gtk.Revealer | None = None
        self._stack_owner_id: str | None = None
        self._stack_provider: StackContentProvider | None = None
        self._stack_content: StackContent | None = None
        self._stack_closed: Callable[[], None] | None = None
        self._folder_stack_anchor_x: int = 0
        self._folder_stack_anchor_y: int = 0
        self._folder_stack_icon_w: int = 0
        self._folder_stack_fold_center_x: int = 0
        self._folder_stack_position_value = self._config.pos
        self._folder_stack_area: Gtk.DrawingArea | None = None
        self._folder_stack_popup_size: Size | None = None
        self._folder_stack_constraint_source: int = 0
        self._folder_stack_constraint_retries: int = 0
        self._folder_stack_cards: list[StackCard] = []
        self._folder_stack_anim_source: int = 0
        self._folder_stack_show_started_us: int = 0
        self._folder_stack_hover_target: str | None = None
        self._folder_stack_hover_values: dict[str, float] = {}
        self._folder_stack_pressed_target: str | None = None

    def show_stack(
        self,
        *,
        owner_id: str,
        provider: StackContentProvider,
        anchor: PopupAnchor,
        toggle_if_same_owner: bool = True,
        on_closed: Callable[[], None] | None = None,
    ) -> bool:
        """Show a provider-backed stack, or optionally toggle it closed."""
        if (
            self._folder_stack_window is not None
            and self._folder_stack_window.get_visible()
            and self._stack_owner_id == owner_id
        ):
            if toggle_if_same_owner:
                self._close_stack()
            return True

        content = provider(max(int(self._config.icon_size), 1))
        if content is None:
            return False

        self._close_stack()
        self._runtime.hide_hover_ui()
        self._runtime.menu_popup_opened()

        window = self._ensure_stack_window()
        revealer = self._folder_stack_revealer
        assert revealer is not None

        self._stack_owner_id = owner_id
        self._stack_provider = provider
        self._stack_content = content
        self._stack_closed = on_closed
        self._folder_stack_anchor_x = int(anchor.x)
        self._folder_stack_anchor_y = int(anchor.y)
        self._folder_stack_icon_w = 0
        self._folder_stack_position_value = anchor.position
        self._replace_stack_content(content=content)
        self._restart_stack_animation()
        self._position_stack_window()
        revealer.set_reveal_child(True)
        window.show_all()
        return True

    def close(self) -> None:
        self._close_stack()

    def open_owner_id(self) -> str | None:
        window = self._folder_stack_window
        if window is None or not window.get_visible() or self._stack_owner_id is None:
            return None
        return self._stack_owner_id

    def invalidate_owner(self, owner_id: str) -> None:
        self._folder_stack_cache.invalidate_owner(owner_id)

    def prewarm(self, *, owner_id: str, provider: StackContentProvider) -> None:
        content = provider(max(int(self._config.icon_size), 1))
        if content is not None:
            self._stack_layout(owner_id=owner_id, content=content)

    def refresh(self, owner_id: str | None = None) -> bool:
        if owner_id is not None and owner_id != self._stack_owner_id:
            return False
        provider = self._stack_provider
        window = self._folder_stack_window
        if provider is None or window is None or not window.get_visible():
            return False
        content = provider(max(int(self._config.icon_size), 1))
        if content is None:
            self.close()
            return False
        if self._stack_content is not None and self._stack_content_signature(
            content
        ) == self._stack_content_signature(self._stack_content):
            self._stack_content = content
            return True
        if self._stack_owner_id is not None:
            self.invalidate_owner(self._stack_owner_id)
        self._stack_content = content
        self._replace_stack_content(content=content)
        self._restart_stack_animation()
        self._position_stack_window()
        window.show_all()
        return True

    @staticmethod
    def _stack_content_signature(content: StackContent) -> tuple[object, ...]:
        """Return the visible structure while excluding replaceable callbacks."""
        entries = content.entries[:FOLDER_STACK_MAX_VISIBLE_ROWS]
        return (
            tuple(
                (entry.key, entry.label, id(entry.icon), entry.thumbnail_style)
                for entry in entries
            ),
            (
                (content.action.key, content.action.label)
                if content.action is not None
                else None
            ),
            content.empty_label,
        )

    def _close_stack(self) -> None:
        window = self._folder_stack_window
        if window is None or not window.get_visible():
            return
        revealer = self._folder_stack_revealer
        if revealer is not None:
            revealer.set_reveal_child(False)
        window.hide()
        self._cleanup_stack()
        self._runtime.menu_popup_closed()

    def _cleanup_stack(self) -> None:
        self._cancel_stack_constraint()
        if self._folder_stack_anim_source:
            GLib.source_remove(self._folder_stack_anim_source)
            self._folder_stack_anim_source = 0
        on_closed = self._stack_closed
        self._folder_stack_area = None
        self._folder_stack_popup_size = None
        self._stack_owner_id = None
        self._stack_provider = None
        self._stack_content = None
        self._stack_closed = None
        self._folder_stack_anchor_x = 0
        self._folder_stack_anchor_y = 0
        self._folder_stack_icon_w = 0
        self._folder_stack_fold_center_x = 0
        self._folder_stack_show_started_us = 0
        self._folder_stack_hover_target = None
        self._folder_stack_hover_values.clear()
        self._folder_stack_pressed_target = None
        if on_closed is not None:
            on_closed()

    def _ensure_stack_window(self) -> Gtk.Window:
        if self._folder_stack_window is not None:
            return self._folder_stack_window

        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_decorated(False)
        window.set_skip_taskbar_hint(True)
        window.set_resizable(False)
        window.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        window.set_app_paintable(True)
        window.connect("configure-event", self._on_stack_configure)

        # On Wayland popups need a transient parent so the compositor
        # positions them relative to the dock rather than at (0,0).
        window.set_transient_for(self._dock_window)

        screen = window.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            window.set_visual(visual)

        revealer = Gtk.Revealer()
        revealer.set_transition_type(self._stack_transition_type())
        revealer.set_transition_duration(140)
        revealer.set_reveal_child(False)
        window.add(revealer)

        self._folder_stack_window = window
        self._folder_stack_revealer = revealer
        return window

    def _stack_transition_type(self):
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

    def _replace_stack_content(self, content: StackContent) -> None:
        revealer = self._folder_stack_revealer
        if revealer is None:
            return
        self._cancel_stack_constraint()
        self._folder_stack_constraint_retries = 0
        # Reused GTK popups otherwise retain their previous large allocation.
        if self._folder_stack_window is not None:
            self._folder_stack_window.hide()
        child = revealer.get_child()
        if child is not None:
            child.destroy()
        widget = self._build_stack_content(content=content)
        revealer.add(widget)
        widget.show_all()

    def _position_stack_window(self) -> None:
        window = self._folder_stack_window
        revealer = self._folder_stack_revealer
        if window is None or revealer is None:
            return
        child = revealer.get_child()
        if child is None:
            return

        preferred = child.get_preferred_size()[1]
        size = self._folder_stack_popup_size or Size(
            max(int(preferred.width), 1), max(int(preferred.height), 1)
        )
        popup_w, popup_h = size.width, size.height
        anchor_x = self._folder_stack_anchor_x
        anchor_y = self._folder_stack_anchor_y
        icon_w = max(self._folder_stack_icon_w, 0)
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

        bounds = popup_workarea(window, anchor_x, anchor_y)
        # Legacy GTK layer-shell popups can retain off-screen requested origins.
        # Bound the rectangle before converting to parent-relative coordinates.
        popup_x = max(bounds.x, min(popup_x, bounds.right - popup_w))
        popup_y = max(bounds.y, min(popup_y, bounds.bottom - popup_h))
        popup_pos = clamp_popup(
            window, popup_x, popup_y, popup_w, popup_h, bounds=bounds
        )
        window.resize(popup_w, popup_h)
        if self._stack_uses_native_placement():
            # The compositor knows the parent's actual origin, including panel
            # offsets that are not available through GDK's monitor workarea.
            # Preserve the dock-facing edge when the viewport must shrink.
            window.set_resizable(True)
            window.realize()
            rect = Gdk.Rectangle()
            rect.x, rect.y = popup_pos
            rect.width = rect.height = 1
            gravity = Gdk.Gravity.NORTH_WEST
            if pos_enum is Position.BOTTOM:
                rect.y += popup_h
                gravity = Gdk.Gravity.SOUTH_WEST
            elif pos_enum is Position.RIGHT:
                rect.x += popup_w
                gravity = Gdk.Gravity.NORTH_EAST
            window.get_window().move_to_rect(
                rect,
                Gdk.Gravity.NORTH_WEST,
                gravity,
                Gdk.AnchorHints.RESIZE_X | Gdk.AnchorHints.RESIZE_Y,
                0,
                0,
            )
        else:
            window.move(popup_pos.x, popup_pos.y)
        log.debug(
            "stack shown: edge=%s bounds=%s size=%sx%s at=%s",
            pos,
            bounds,
            popup_w,
            popup_h,
            popup_pos,
        )

    def _build_stack_content(self, content: StackContent) -> Gtk.Widget:
        cards, popup_w, popup_h = self._stack_cards_for_content(content)
        self._folder_stack_cards = cards
        self._folder_stack_popup_size = Size(popup_w, popup_h)

        area = Gtk.DrawingArea()
        area.set_size_request(popup_w, popup_h)
        area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        area.connect("draw", self._on_stack_draw)
        area.connect("button-press-event", self._on_stack_button_press)
        area.connect("button-release-event", self._on_stack_button_release)
        area.connect("motion-notify-event", self._on_stack_motion_notify)
        area.connect("leave-notify-event", self._on_stack_leave_notify)
        self._folder_stack_area = area
        window = self._folder_stack_window
        if window is None:
            return area
        bounds = popup_workarea(
            window, self._folder_stack_anchor_x, self._folder_stack_anchor_y
        )
        available = stack_available_size(
            bounds,
            self._folder_stack_anchor_x,
            self._folder_stack_anchor_y,
            Position(self._folder_stack_position_value),
        )
        native_placement = self._stack_uses_native_placement()
        if (
            not native_placement
            and popup_w <= available.width
            and popup_h <= available.height
        ):
            return area

        # Keep the existing fan and its content-local input coordinates. GTK's
        # viewport scrolls paint and events together, without scaling the labels.
        scroller = Gtk.ScrolledWindow()
        scroller.set_shadow_type(Gtk.ShadowType.NONE)
        scroller.set_overlay_scrolling(False)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_width(1)
        scroller.set_min_content_height(1)
        viewport = Gtk.Viewport()
        viewport.set_shadow_type(Gtk.ShadowType.NONE)
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"scrolledwindow, viewport { background-color: transparent; border: none; }"
            b"undershoot, overshoot { background: none; box-shadow: none; }"
        )
        for widget in (scroller, viewport):
            widget.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        viewport.add(area)
        scroller.add(viewport)
        for adjustment in (scroller.get_hadjustment(), scroller.get_vadjustment()):
            adjustment.connect("value-changed", self._on_stack_scrolled)
        size = scrolled_popup_size(
            Size(popup_w, popup_h),
            available,
            Size(
                scroller.get_vscrollbar().get_preferred_width()[1],
                scroller.get_hscrollbar().get_preferred_height()[1],
            ),
        )
        self._folder_stack_popup_size = size
        # A Wayland popup that initially fits may be constrained by an external
        # panel. Its content must be scrollable and its GTK minimum releasable.
        if not native_placement:
            scroller.set_size_request(size.width, size.height)
        return scroller

    def _stack_uses_native_placement(self) -> bool:
        surface = window_surface_service(self._dock_window)
        return surface is not None and surface.popups_use_parent_relative_coordinates

    def _cancel_stack_constraint(self) -> None:
        if self._folder_stack_constraint_source:
            GLib.source_remove(self._folder_stack_constraint_source)
            self._folder_stack_constraint_source = 0

    def _on_stack_configure(
        self, window: Gtk.Window, event: Gdk.EventConfigure
    ) -> bool:
        size = self._folder_stack_popup_size
        if (
            size is not None
            and window.get_visible()
            and self._stack_uses_native_placement()
            and (event.width > size.width or event.height > size.height)
            and self._folder_stack_constraint_source == 0
            and self._folder_stack_constraint_retries < 2
        ):
            # Older compositors can grow a popup in response to a resize
            # constraint. Coalesce configure events, then recheck the actual
            # allocation so queued events from a previous mapping are harmless.
            self._folder_stack_constraint_source = GLib.idle_add(
                self._constrain_stack_size
            )
        return False

    def _constrain_stack_size(self) -> bool:
        self._folder_stack_constraint_source = 0
        window = self._folder_stack_window
        size = self._folder_stack_popup_size
        if window is None or size is None or not window.get_visible():
            return GLib.SOURCE_REMOVE
        width, height = window.get_size()
        if width <= size.width and height <= size.height:
            return GLib.SOURCE_REMOVE
        fitted = Size(
            max(1, size.width - max(0, width - size.width)),
            max(1, size.height - max(0, height - size.height)),
        )
        log.debug(
            "retry stack constraint: requested=%s granted=%sx%s retry=%s",
            size,
            width,
            height,
            fitted,
        )
        self._folder_stack_popup_size = fitted
        self._folder_stack_constraint_retries += 1
        # GTK3 cannot reposition a mapped xdg_popup on all supported protocol
        # versions. Remap this popup while preserving its content and hold-open.
        window.hide()
        self._position_stack_window()
        window.show_all()
        return GLib.SOURCE_REMOVE

    def _on_stack_scrolled(self, _adjustment: Gtk.Adjustment) -> None:
        self._folder_stack_pressed_target = None
        if self._folder_stack_hover_target is not None:
            self._folder_stack_hover_target = None
            self._ensure_stack_animating()

    def _stack_cards_for_content(
        self, content: StackContent
    ) -> tuple[list[StackCard], int, int]:
        owner_id = self._stack_owner_id or ""
        layout = self._stack_layout(owner_id=owner_id, content=content)
        self._folder_stack_fold_center_x = layout.fold_center_x
        return list(layout.cards), layout.popup_w, layout.popup_h

    def _stack_layout(self, *, owner_id: str, content: StackContent) -> StackLayout:
        icon_px = max(int(self._config.icon_size), 1)
        entries = tuple(content.entries[:FOLDER_STACK_MAX_VISIBLE_ROWS])
        if len(content.entries) > FOLDER_STACK_MAX_VISIBLE_ROWS:
            log.warning(
                "Stack %s supplied %d entries; displaying the first %d",
                owner_id,
                len(content.entries),
                FOLDER_STACK_MAX_VISIBLE_ROWS,
            )
        cache_key = (
            owner_id,
            icon_px,
            *self._stack_content_signature(content),
        )
        cached = self._folder_stack_cache.get_layout(cache_key)
        if cached is not None:
            return cached

        layout = self._compute_stack_layout(
            entries=entries,
            action=content.action,
            empty_label=content.empty_label,
            icon_px=icon_px,
        )
        self._folder_stack_cache.put_layout(cache_key, layout)
        return layout

    def _compute_stack_layout(
        self,
        *,
        entries: Sequence[StackEntry],
        action: StackAction | None,
        empty_label: str,
        icon_px: int,
    ) -> StackLayout:
        """Lay out stack cards along a stable, bottom-anchored curve.

        The important detail is that a short stack must not normalize its own
        top row to ``progress == 1``. Doing that makes a two-row stack sample
        only the two endpoints of the curve: the bottom row receives the base
        offset and the top row immediately receives the full horizontal
        offset. The result is a large diagonal jump rather than two neighboring
        points on a continuous arc.

        Four rows are the visual reference because that is the smallest stack
        for which the full curve reads clearly. For one, two, or three rows we
        therefore retain a four-row ``arc_span`` and show only its bottom-most
        samples. Vertical spacing is not padded and no invisible rows are
        created; only the progress denominator uses the reference span.

        With the normal 48px icon size and 54px row step, the fold center is at
        x=196. The 8px base shift places the bottom icon at x=180 after
        subtracting half its width. The resulting top-to-bottom
        ``(icon_x, stack_progress)`` values are::

            {
                1: [(180, 0.0)],
                2: [(187, 0.333), (180, 0.0)],
                3: [(201, 0.667), (187, 0.333), (180, 0.0)],
                4: [
                    (220, 1.0),
                    (201, 0.667),
                    (187, 0.333),
                    (180, 0.0),
                ],
                5: [
                    (220, 1.0),
                    (205, 0.75),
                    (193, 0.5),
                    (185, 0.25),
                    (180, 0.0),
                ],
            }

        This table demonstrates two related invariants:

        * A two-row stack is exactly the last two samples of the four-row
          reference, and a three-row stack is exactly its last three samples.
          Adding an item extends the existing curve instead of repositioning
          the lower items.
        * Four or more rows span their real content height. Five rows therefore
          use progress quarters rather than thirds, still reaching the same
          bottom and top endpoints while gaining a new intermediate sample.

        ``content_span`` remains the actual distance between the first and last
        visible entries and controls their y coordinates. ``arc_span`` is the
        possibly extended reference distance used only for curve progress and
        rotation. ``top_progress`` records how much of that reference curve is
        occupied by visible content. The optional action chip uses the same
        progress as the top entry, and popup width is calculated from that same
        endpoint, so neither introduces an artificial jump or reserves space
        for the unused upper portion of a short curve.

        The numeric x positions above document the current 48px regression
        case, not a fixed public geometry contract. Larger icon sizes may move
        the fold center and row step, but they retain the sampling invariants.
        """
        cards: list[StackCard] = []
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

        if not entries:
            return self._centered_layout(
                label=empty_label,
                label_h=label_h,
                fold_center_x=fold_center_x,
                icon_px=icon_px,
                right_bleed=right_bleed,
            )

        total_rows = len(entries)
        content_span = (total_rows - 1) * row_step
        arc_span = (max(total_rows, FOLDER_STACK_CURVE_MIN_ROWS) - 1) * row_step
        top_progress = content_span / arc_span if arc_span > 0 else 0.0
        stack_top = FOLDER_STACK_TOP_PADDING_PX
        max_right = fold_center_x
        if action is not None:
            chip_w = self._stack_action_width(label=action.label)
            top_center_x = round(
                fold_center_x + _stack_arc_offset(top_progress, arc_span)
            )
            chip_x = max(
                FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                int(top_center_x - chip_w / 2 + curve_extent * 0.1),
            )
            chip_y = FOLDER_STACK_TOP_PADDING_PX
            cards.append(
                StackCard(
                    target=action.key,
                    label=action.label,
                    icon=None,
                    icon_x=0,
                    icon_y=0,
                    icon_size=0,
                    label_x=chip_x,
                    label_y=chip_y,
                    label_w=chip_w,
                    label_h=label_h,
                    centered=True,
                    action=True,
                    stack_progress=top_progress,
                    arc_span=float(arc_span),
                )
            )
            stack_top = chip_y + label_h + FOLDER_STACK_ACTION_GAP_PX
            max_right = chip_x + chip_w

        bottom_center_y = (
            stack_top + (total_rows - 1) * row_step + icon_px / 2 if total_rows else 0
        )
        for index, entry in enumerate(entries):
            distance_from_bottom = (total_rows - 1 - index) * row_step
            arc_progress = distance_from_bottom / arc_span if arc_span > 0 else 0.0
            icon_center_x = fold_center_x + _stack_arc_offset(
                arc_progress,
                arc_span,
            )
            icon_center_y = bottom_center_y - distance_from_bottom
            icon_x = round(icon_center_x - icon_px / 2)
            icon_y = round(icon_center_y - icon_px / 2)
            name = entry.label
            label_w = self._stack_label_width(label=name)
            label_pull = round(arc_progress * 10)
            label_x = max(
                FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                icon_x - FOLDER_STACK_ICON_GAP_PX - label_w - label_pull,
            )
            cards.append(
                StackCard(
                    target=entry.key,
                    label=name,
                    icon=entry.icon,
                    icon_x=icon_x,
                    icon_y=icon_y,
                    icon_size=icon_px,
                    label_x=label_x,
                    label_y=icon_y + max(int((icon_px - label_h) / 2), 0),
                    label_w=label_w,
                    label_h=label_h,
                    centered=False,
                    stack_progress=arc_progress,
                    arc_span=float(arc_span),
                    thumbnail_style=entry.thumbnail_style,
                )
            )
            max_right = max(max_right, icon_x + icon_px)

        popup_w = int(
            max(
                max_right + right_bleed,
                fold_center_x
                + _stack_arc_offset(top_progress, arc_span)
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
        return StackLayout(
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
    ) -> StackLayout:
        label_w = 190
        card = StackCard(
            target=None,
            label=label,
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
        return StackLayout(
            cards=(card,),
            popup_w=popup_w,
            popup_h=popup_h,
            fold_center_x=fold_center_x,
        )

    def _on_stack_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        now_us = GLib.get_monotonic_time()
        total_cards = len(self._folder_stack_cards)
        for draw_index, card in enumerate(self._folder_stack_cards):
            self._draw_stack_card(
                cr=cr,
                card=card,
                sequence_index=total_cards - 1 - draw_index,
                now_us=now_us,
            )
        return False

    def _stack_card_geometry(
        self,
        *,
        card: StackCard,
        sequence_index: int,
        now_us: int,
    ) -> StackCardGeometry | None:
        reveal = self._stack_reveal_progress(
            sequence_index=sequence_index,
            now_us=now_us,
        )
        if reveal <= 0:
            return None

        hover_value = (
            self._folder_stack_hover_values.get(card.key, 0.0)
            if card.key is not None and not card.centered
            else 0.0
        )
        y_offset = (1.0 - reveal) * 18.0
        rotation_radians = (
            _stack_rotation(
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

        return StackCardGeometry(
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

    def _draw_stack_card(
        self,
        *,
        cr: cairo.Context,
        card: StackCard,
        sequence_index: int,
        now_us: int,
    ) -> None:
        geometry = self._stack_card_geometry(
            card=card,
            sequence_index=sequence_index,
            now_us=now_us,
        )
        if geometry is None:
            return
        is_action_card = _is_stack_action_card(card)

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

            if card.thumbnail_style:
                _draw_rounded_stack_thumbnail(cr, pixbuf, draw_icon_size, geometry)
            else:
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

    def _stack_card_at(self, x: float, y: float) -> StackCard | None:
        now_us = GLib.get_monotonic_time()
        total_cards = len(self._folder_stack_cards)
        for index in range(total_cards - 1, -1, -1):
            card = self._folder_stack_cards[index]
            geometry = self._stack_card_geometry(
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

    def _on_stack_button_press(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        if int(event.button) != 1:
            self._folder_stack_pressed_target = None
            return False
        card = self._stack_card_at(event.x, event.y)
        self._folder_stack_pressed_target = (
            card.key if card is not None and card.key is not None else None
        )
        return self._folder_stack_pressed_target is not None

    def _on_stack_button_release(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        if int(event.button) != 1:
            self._folder_stack_pressed_target = None
            return False
        card = self._stack_card_at(event.x, event.y)
        target = card.key if card is not None and card.key is not None else None
        pressed_target = self._folder_stack_pressed_target
        self._folder_stack_pressed_target = None
        if target is not None and pressed_target == target:
            self._activate_stack_key(target)
            return True
        return False

    def _on_stack_motion_notify(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventMotion
    ) -> bool:
        card = self._stack_card_at(event.x, event.y)
        target = (
            card.key
            if card is not None and card.key is not None and not card.centered
            else None
        )
        if target != self._folder_stack_hover_target:
            self._folder_stack_hover_target = target
            self._ensure_stack_animating()
        return False

    def _on_stack_leave_notify(
        self, _widget: Gtk.DrawingArea, _event: Gdk.EventCrossing
    ) -> bool:
        if self._folder_stack_hover_target is not None:
            self._folder_stack_hover_target = None
            self._ensure_stack_animating()
        self._folder_stack_pressed_target = None
        return False

    def _stack_action_width(self, *, label: str) -> int:
        return min(
            FOLDER_STACK_ACTION_MAX_WIDTH_PX,
            _measure_stack_text_px(label)
            + 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX
            + FOLDER_STACK_ACTION_ARROW_GAP_PX
            + FOLDER_STACK_ACTION_ARROW_SIZE_PX
            + 10,
        )

    def _stack_label_width(self, *, label: str) -> int:
        return min(
            FOLDER_STACK_LABEL_MAX_WIDTH_PX,
            max(
                24,
                _measure_stack_text_px(label)
                + 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX
                + 10,
            ),
        )

    def _restart_stack_animation(self) -> None:
        self._folder_stack_show_started_us = GLib.get_monotonic_time()
        self._folder_stack_hover_target = None
        self._folder_stack_hover_values.clear()
        self._ensure_stack_animating()

    def _ensure_stack_animating(self) -> None:
        area = self._folder_stack_area
        if area is None:
            return
        area.queue_draw()
        if self._folder_stack_anim_source == 0:
            self._folder_stack_anim_source = GLib.timeout_add(
                FOLDER_STACK_ANIM_FRAME_MS,
                self._on_stack_animation_frame,
            )

    def _on_stack_animation_frame(self) -> bool:
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
            if card.key is None or card.centered:
                continue
            current = self._folder_stack_hover_values.get(card.key, 0.0)
            target = 1.0 if self._folder_stack_hover_target == card.key else 0.0
            updated = current + (target - current) * FOLDER_STACK_HOVER_EASE
            if abs(updated - target) < 0.02:
                updated = target
            if updated <= 0.0 and target == 0.0:
                self._folder_stack_hover_values.pop(card.key, None)
            else:
                self._folder_stack_hover_values[card.key] = updated
            if updated != target:
                active = True

        area.queue_draw()
        if not active:
            self._folder_stack_anim_source = 0
            return False
        return True

    def _stack_reveal_progress(self, *, sequence_index: int, now_us: int) -> float:
        if self._folder_stack_show_started_us <= 0:
            return 1.0
        elapsed_ms = max((now_us - self._folder_stack_show_started_us) / 1000.0, 0.0)
        elapsed_ms -= sequence_index * FOLDER_STACK_REVEAL_STAGGER_MS
        if elapsed_ms <= 0:
            return 0.0
        return _ease_out_cubic(elapsed_ms / FOLDER_STACK_REVEAL_DURATION_MS)

    def _activate_stack_key(self, key: str) -> None:
        content = self._stack_content
        callback: Callable[[], None] | None = None
        if content is not None:
            if content.action is not None and content.action.key == key:
                callback = content.action.activate
            else:
                callback = next(
                    (entry.activate for entry in content.entries if entry.key == key),
                    None,
                )
        if callback is None:
            return
        try:
            callback()
        except Exception:
            log.exception("Failed to activate stack entry %s", key)
        finally:
            self._close_stack()
