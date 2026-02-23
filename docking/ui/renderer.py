"""Cairo renderer for the dock — background, icons, indicators, zoom visualization."""

from __future__ import annotations

import math
from docking.log import get_logger

log = get_logger("renderer")
from typing import TYPE_CHECKING

import cairo

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from docking.core.zoom import compute_layout, content_bounds
from docking.core.theme import RGB
from docking.ui.effects import (
    easing_bounce,
    average_icon_color,
    CLICK_DURATION_US,
    CLICK_DARKEN_MAX,
    LAUNCH_BOUNCE_DURATION_US,
    LAUNCH_BOUNCE_HEIGHT,
    URGENT_BOUNCE_DURATION_US,
    URGENT_BOUNCE_HEIGHT,
    HOVER_LIGHTEN_MAX,
    HOVER_FADE_FRAMES,
)
from docking.ui.shelf import draw_shelf_background, SHELF_HEIGHT_PX

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.model import DockModel, DockItem
    from docking.core.theme import Theme
    from docking.core.zoom import LayoutItem


# Linear interpolation (lerp) factor for shelf width smoothing.
#
# When icons zoom on hover, the total content width changes slightly
# each frame. If the shelf background tracked this width exactly, it
# would visibly jitter (wobble) as the cursor moves between icons.
#
# Instead, the shelf width is smoothed using lerp:
#   shelf_w += (target_w - shelf_w) * SHELF_SMOOTH_FACTOR
#
# A value of 0.3 means the shelf moves 30% of the remaining distance
# each frame. At 60fps this settles in ~200ms — fast enough to follow
# the icons but smooth enough to dampen frame-to-frame jitter.
# Lower values = smoother but slower. Higher values = more responsive
# but more visible wobble.
SHELF_SMOOTH_FACTOR = 0.3
SLIDE_MOVE_THRESHOLD = 2.0  # minimum px displacement to trigger slide animation
# Per-frame exponential decay factor for the slide reorder animation.
#
# When dock items are reordered via drag-and-drop, displaced items
# slide smoothly to their new positions instead of teleporting.
# Each frame, the remaining offset is multiplied by this factor:
#   offset *= SLIDE_DECAY_FACTOR  (e.g., 0.75)
#
# This creates a decelerating motion — items move fast initially
# then slow down as they approach their target, which feels natural.
# At 60fps with 0.75 decay, an offset halves roughly every 3 frames
# (~50ms), settling to imperceptible in about 200-300ms.
SLIDE_DECAY_FACTOR = 0.75
SLIDE_CLEAR_THRESHOLD = 0.5  # clear slide offset when below this px
MAX_INDICATOR_DOTS = 3
INDICATOR_SPACING_MULT = 3  # spacing = indicator_radius * this

SLIDE_DURATION_MS = 300
SLIDE_FRAME_MS = 16


class DockRenderer:
    """Dock renderer with per-item slide animation for reordering."""

    def __init__(self) -> None:
        # Per-item X offset for slide animation: {desktop_id: offset_px}
        self.slide_offsets: dict[str, float] = {}
        self.prev_positions: dict[str, float] = {}  # {desktop_id: last_x}
        self.smooth_shelf_w: float = 0.0
        # Per-item lighten value for hover effect: {desktop_id: 0.0-HOVER_LIGHTEN_MAX}
        self._hover_lighten: dict[str, float] = {}
        self._hovered_id: str = ""
        # Cached average icon colors for active glow: {desktop_id: (r, g, b)}
        self._icon_colors: dict[str, RGB] = {}

    @staticmethod
    def compute_dock_size(
        model: DockModel,
        config: Config,
        theme: Theme,
    ) -> tuple[int, int]:
        """Compute base dock dimensions (no zoom)."""
        items = model.visible_items()
        num_items = len(items)
        icon_size = config.icon_size
        width = int(
            theme.h_padding * 2
            + num_items * icon_size
            + max(0, num_items - 1) * theme.item_padding
        )
        height = int(icon_size + theme.top_padding + theme.bottom_padding)
        return max(width, 1), max(height, 1)

    def draw(
        self,
        cr: cairo.Context,
        widget: Gtk.DrawingArea,
        model: DockModel,
        config: Config,
        theme: Theme,
        cursor_x: float,
        hide_offset: float = 0.0,
        drag_index: int = -1,
        drop_insert_index: int = -1,
        zoom_progress: float = 1.0,
        hovered_id: str = "",
    ) -> None:
        """Main draw entry point — called on every 'draw' signal."""
        alloc = widget.get_allocation()
        width, height = alloc.width, alloc.height

        # Clear to transparent
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Cascade hide animation: shelf and icons slide at different rates.
        #
        # When the dock hides, instead of everything moving uniformly,
        # the shelf background slides down faster than the icons. This
        # creates a layered "cascade" effect that feels more polished:
        #
        #   Frame 0:  [icons]          Frame 3:     [icons]
        #             ═══shelf═══                 ═══shelf═══
        #             ───screen───                ───screen───
        #
        #   Frame 6:        [icons]    Frame 9:          (hidden)
        #                ═══shelf═══               ═══shelf═══
        #             ───screen───                ───screen───
        #
        # The shelf leads with a 1.3x multiplier on hide_offset,
        # meaning it reaches the screen edge ~30% sooner than icons.
        # bg_hide is clamped to 1.0 so it doesn't overshoot.
        if hide_offset > 0:
            bg_hide = min(hide_offset * 1.3, 1.0)  # background leads
            icon_hide = hide_offset  # icons follow
        else:
            bg_hide = 0.0
            icon_hide = 0.0

        items = model.visible_items()
        if not items:
            return

        num_items = len(items)

        # --- Coordinate Systems ---
        #
        # The dock uses two coordinate spaces:
        #
        # WINDOW-SPACE: pixel positions within the GTK window.
        #   The window spans the full monitor width (e.g., 1920px) to
        #   prevent resize wobble during zoom. Position 0 = left edge
        #   of the monitor, position 1920 = right edge.
        #
        # CONTENT-SPACE: positions relative to the dock icon layout.
        #   Position 0 = left edge of the first icon's horizontal padding.
        #   The layout is computed entirely in this space.
        #
        # Conversion between them:
        #
        #   |<----------- monitor (1920px) ------------>|
        #   |            |<-- content (478px) -->|      |
        #   |            | [A] [B] [C] [D] [E]  |      |
        #   |<--offset-->|                       |      |
        #   0          721                     1199   1920
        #
        #   content_x = window_x - base_offset
        #   window_x  = content_x + base_offset
        #
        # Mouse events arrive in window-space. The zoom formula and
        # layout computation work in content-space. We convert the
        # cursor position before passing it to compute_layout().
        base_w = (
            theme.h_padding * 2
            + num_items * config.icon_size
            + max(0, num_items - 1) * theme.item_padding
        )
        base_offset = (width - base_w) / 2

        local_cursor = cursor_x - base_offset if cursor_x >= 0 else -1.0
        layout = compute_layout(
            items,
            config,
            local_cursor,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
        )

        # Smooth zoom decay during hide animation.
        #
        # When the mouse leaves the dock and autohide triggers, we want
        # the icon zoom to fade out smoothly IN PARALLEL with the slide
        # animation, not snap to unzoomed before the slide starts.
        #
        # zoom_progress is a value from 0.0 (no zoom) to 1.0 (full zoom),
        # animated by the AutoHideController. During hide, it decays from
        # its current value toward 0.0 alongside hide_offset.
        #
        # The formula: effective_scale = 1.0 + (computed_scale - 1.0) * zoom_progress
        # At zoom_progress=1.0: full zoom (normal hover behavior)
        # At zoom_progress=0.5: half zoom (mid-hide)
        # At zoom_progress=0.0: no zoom (fully hidden)
        #
        # Without this, icons would snap to 1.0x scale on the frame the
        # mouse leaves, creating a jarring visual pop before the smooth
        # slide animation begins.
        if zoom_progress < 1.0:
            for li in layout:
                li.scale = 1.0 + (li.scale - 1.0) * zoom_progress

        # Content bounds and centering offset.
        #
        # The parabolic zoom displacement pushes icons AWAY from the cursor.
        # When hovering the right side, left icons shift further left —
        # potentially past x=0 in content-space. Similarly, hovering the
        # left side pushes right icons past the normal right boundary.
        #
        # content_bounds() returns the actual (left_edge, right_edge) of
        # all icons including these displacements. The left_edge may be
        # negative when left icons are pushed past the origin.
        #
        # icon_offset positions the content so it's centered in the
        # full-width window: icon_offset = (window_w - zoomed_w) / 2 - left_edge
        # The "- left_edge" term compensates for negative left displacement,
        # ensuring the visual center stays at the monitor center.
        left_edge, right_edge = content_bounds(
            layout, config.icon_size, theme.h_padding
        )
        zoomed_w = right_edge - left_edge
        icon_offset = (width - zoomed_w) / 2 - left_edge

        # Shelf width smoothing with snap-on-first-draw.
        #
        # smooth_shelf_w tracks the rendered shelf width, smoothed via
        # lerp to prevent wobble (see SHELF_SMOOTH_FACTOR above).
        #
        # On the very first frame, smooth_shelf_w is 0.0 (initialized in
        # __init__). If we lerped from 0, the shelf would start tiny and
        # gradually expand — a visible glitch on startup. Instead, we
        # snap to the target width on the first frame, then lerp normally
        # on all subsequent frames.
        target_shelf_w = zoomed_w
        if self.smooth_shelf_w == 0.0:
            self.smooth_shelf_w = target_shelf_w
        else:
            self.smooth_shelf_w += (
                target_shelf_w - self.smooth_shelf_w
            ) * SHELF_SMOOTH_FACTOR
        shelf_w = self.smooth_shelf_w
        shelf_x = (width - shelf_w) / 2

        # Plank Yaru-light: shelf = 21px for 48px icons (not additive with bottom_padding)
        bg_height = SHELF_HEIGHT_PX
        bg_y = height - bg_height + bg_hide * height
        draw_shelf_background(cr, shelf_x, bg_y, shelf_w, bg_height, theme)

        # Active window glow: color-matched highlight on the shelf
        # behind the focused application's icon.
        #
        # The glow pipeline:
        # 1. Extract the icon's dominant color via average_icon_color()
        #    (saturation-weighted average — see that function's comments).
        #    The result is cached in _icon_colors so we don't re-scan
        #    the pixbuf every frame.
        #
        # 2. Create a vertical linear gradient using that color:
        #    - Top of shelf:    icon_color at alpha=0.0 (transparent)
        #    - Bottom of shelf: icon_color at alpha=0.6 (visible)
        #    This makes the glow appear to emanate upward from the
        #    screen edge, fading as it rises into the shelf.
        #
        # 3. Draw a rectangle slightly wider than the icon (15% padding
        #    on each side) to give the glow a soft spread.
        #
        # 4. Clip the glow rectangle to the shelf bounds. Without
        #    clipping, icons near the shelf edge would have their glow
        #    bleed past the rounded corners of the shelf background.
        #
        #   Shelf background:
        #   ╔════════════════════════════════╗
        #   ║          ▓▓▓▓▓▓▓▓              ║ ← glow clipped to shelf
        #   ║         ▓▓▓▓▓▓▓▓▓▓             ║
        #   ╚════════════════════════════════╝
        #              ↑ glow uses icon's own color
        #
        # Using the icon's own color (instead of a fixed white/blue)
        # makes each app's active state visually distinct — Firefox
        # gets an orange glow, Terminal gets a dark glow, etc.
        for i, (item, li) in enumerate(zip(items, layout)):
            if item.is_active:
                glow_x = li.x + icon_offset
                glow_w = config.icon_size * li.scale
                glow_pad = glow_w * 0.15
                # Compute and cache the icon's average color
                if item.desktop_id not in self._icon_colors:
                    self._icon_colors[item.desktop_id] = average_icon_color(item.icon)
                glow_red, glow_green, glow_blue = self._icon_colors[item.desktop_id]
                pat = cairo.LinearGradient(0, bg_y, 0, bg_y + bg_height)
                pat.add_color_stop_rgba(0, glow_red, glow_green, glow_blue, 0.0)
                pat.add_color_stop_rgba(1, glow_red, glow_green, glow_blue, 0.6)
                # Clip glow to shelf bounds so it doesn't bleed past edges
                gx_left = max(glow_x - glow_pad, shelf_x)
                gx_right = min(glow_x + glow_w + glow_pad, shelf_x + shelf_w)
                if gx_right > gx_left:
                    cr.rectangle(gx_left, bg_y, gx_right - gx_left, bg_height)
                    cr.set_source(pat)
                    cr.fill()

        # Update slide animation offsets (detect items that moved)
        self._update_slide_offsets(items, layout, icon_offset)

        # Gap for external drop insertion
        gap = config.icon_size + theme.item_padding if drop_insert_index >= 0 else 0

        # Update hover lighten values (fade in/out per icon)
        self._update_hover_lighten(items, hovered_id)

        # Draw icons with all effects: slide, drop gap, cascade hide,
        # hover lighten, click darken, launch/urgent bounce
        icon_size = config.icon_size
        icon_y_off = icon_hide * height
        now = GLib.get_monotonic_time()
        for i, (item, li) in enumerate(zip(items, layout)):
            if i == drag_index:
                continue
            slide = self.slide_offsets.get(item.desktop_id, 0.0)
            drop_shift = gap if drop_insert_index >= 0 and i >= drop_insert_index else 0
            lighten = self._hover_lighten.get(item.desktop_id, 0.0)

            # Click darken animation: brief sine pulse
            darken = 0.0
            if item.last_clicked > 0:
                ct = now - item.last_clicked
                if ct < CLICK_DURATION_US:
                    darken = (
                        math.sin(math.pi * ct / CLICK_DURATION_US) * CLICK_DARKEN_MAX
                    )

            # Launch bounce: icon bounces up when launching
            bounce_y = 0.0
            if item.last_launched > 0:
                lt = now - item.last_launched
                bounce_y -= (
                    easing_bounce(lt, LAUNCH_BOUNCE_DURATION_US, 2)
                    * icon_size
                    * LAUNCH_BOUNCE_HEIGHT
                )

            # Urgent bounce: icon bounces when app demands attention
            if item.last_urgent > 0:
                ut = now - item.last_urgent
                bounce_y -= (
                    easing_bounce(ut, URGENT_BOUNCE_DURATION_US, 1)
                    * icon_size
                    * URGENT_BOUNCE_HEIGHT
                )

            self._draw_icon(
                cr,
                item,
                li,
                icon_size,
                height,
                theme,
                icon_offset + slide + drop_shift,
                icon_y_off + bounce_y,
                lighten,
                darken,
            )

        # Draw indicators with slide offset + drop gap + cascade hide
        for i, (item, li) in enumerate(zip(items, layout)):
            if item.is_running:
                slide = self.slide_offsets.get(item.desktop_id, 0.0)
                drop_shift = (
                    gap if drop_insert_index >= 0 and i >= drop_insert_index else 0
                )
                self._draw_indicator(
                    cr,
                    item,
                    li,
                    icon_size,
                    height,
                    theme,
                    icon_offset + slide + drop_shift,
                    icon_y_off,
                )

    def _update_hover_lighten(self, items: list[DockItem], hovered_id: str) -> None:
        """Update per-icon lighten values for hover highlight effect.

        Icons fade in to HOVER_LIGHTEN_MAX when hovered, and fade out
        when the cursor moves to a different icon. The fade uses a fixed
        step per frame for a linear ~150ms transition.
        """
        step = HOVER_LIGHTEN_MAX / HOVER_FADE_FRAMES
        active_ids = {item.desktop_id for item in items}

        for item in items:
            did = item.desktop_id
            current = self._hover_lighten.get(did, 0.0)
            if did == hovered_id:
                # Fade in
                self._hover_lighten[did] = min(current + step, HOVER_LIGHTEN_MAX)
            elif current > 0:
                # Fade out
                new_val = max(current - step, 0.0)
                if new_val > 0:
                    self._hover_lighten[did] = new_val
                else:
                    self._hover_lighten.pop(did, None)

        # Clean up removed items
        for did in list(self._hover_lighten):
            if did not in active_ids:
                del self._hover_lighten[did]

    def _update_slide_offsets(
        self, items: list[DockItem], layout: list[LayoutItem], icon_offset: float
    ) -> None:
        """Detect items that changed position and set slide animation offsets."""
        new_positions: dict[str, float] = {}
        for item, li in zip(items, layout):
            new_positions[item.desktop_id] = li.x + icon_offset

        for desktop_id, new_x in new_positions.items():
            old_x = self.prev_positions.get(desktop_id)
            if old_x is not None and abs(old_x - new_x) > SLIDE_MOVE_THRESHOLD:
                # Item moved — set offset so it appears at old position, then animates
                current_slide = self.slide_offsets.get(desktop_id, 0.0)
                self.slide_offsets[desktop_id] = current_slide + (old_x - new_x)

        # Decay all offsets toward 0 (lerp)
        decay = SLIDE_DECAY_FACTOR
        dead = []
        for desktop_id in self.slide_offsets:
            self.slide_offsets[desktop_id] *= decay
            if abs(self.slide_offsets[desktop_id]) < SLIDE_CLEAR_THRESHOLD:
                dead.append(desktop_id)
        for d in dead:
            del self.slide_offsets[d]

        self.prev_positions = new_positions

    @staticmethod
    def _draw_icon(
        cr: cairo.Context,
        item: DockItem,
        li: LayoutItem,
        base_size: int,
        dock_height: float,
        theme: Theme,
        x_offset: float = 0.0,
        y_offset: float = 0.0,
        lighten: float = 0.0,
        darken: float = 0.0,
    ) -> None:
        """Draw a single dock icon with hover lighten and click darken effects.

        lighten: additive brightness (Cairo ADD operator, 0.0-1.0)
        darken: subtractive darkness (black overlay with ATOP operator, 0.0-1.0)
        """
        if item.icon is None:
            return

        scaled_size = base_size * li.scale
        y = dock_height - theme.bottom_padding - scaled_size + y_offset

        icon_width = item.icon.get_width()
        icon_height = item.icon.get_height()

        # Render icon + effects to an intermediate ImageSurface, then
        # composite the result onto the main Cairo context.
        #
        # Why not apply effects directly on the main context?
        # Cairo compositing operators (ADD, ATOP) operate on ALL
        # pixels in the destination surface — not just the icon's
        # pixels. If we used OPERATOR_ADD directly on the main
        # context, the additive white overlay would brighten the
        # shelf background behind the icon, not just the icon itself.
        # Similarly, OPERATOR_ATOP would interact with previously
        # drawn shelf/glow pixels in unpredictable ways.
        #
        # The intermediate surface isolates the icon:
        #
        #   Step 1: Paint icon onto empty ARGB32 surface
        #   Step 2: Apply ADD (lighten) — only icon pixels affected
        #           because the rest of the surface is transparent
        #   Step 3: Apply ATOP (darken) — ATOP only paints where
        #           the destination has alpha > 0 (the icon shape)
        #   Step 4: Composite finished icon onto main context with
        #           OPERATOR_OVER (normal alpha blending)
        #
        # This is the standard Cairo pattern for per-object effects.
        icon_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, icon_width, icon_height)
        icon_cr = cairo.Context(icon_surface)

        Gdk.cairo_set_source_pixbuf(icon_cr, item.icon, 0, 0)
        icon_cr.paint()

        # Hover lighten: additive blending brightens the icon
        if lighten > 0:
            icon_cr.set_operator(cairo.OPERATOR_ADD)
            icon_cr.paint_with_alpha(lighten)

        # Click darken: black overlay dims only the icon's opaque pixels
        if darken > 0:
            icon_cr.set_operator(cairo.OPERATOR_ATOP)
            icon_cr.set_source_rgba(0, 0, 0, darken)
            icon_cr.paint()

        # Composite the icon surface onto the main context at scaled size
        cr.save()
        cr.translate(li.x + x_offset, y)
        scale_x = scaled_size / icon_width
        scale_y = scaled_size / icon_height
        cr.scale(scale_x, scale_y)
        cr.set_source_surface(icon_surface, 0, 0)
        cr.paint()
        cr.restore()

    @staticmethod
    def _draw_indicator(
        cr: cairo.Context,
        item: DockItem,
        li: LayoutItem,
        base_size: int,
        dock_height: float,
        theme: Theme,
        x_offset: float = 0.0,
        y_offset: float = 0.0,
    ) -> None:
        """Draw running indicator dot(s) below an icon."""
        scaled_size = base_size * li.scale
        center_x = li.x + x_offset + scaled_size / 2
        y = dock_height - theme.bottom_padding / 2 + y_offset

        color = (
            theme.active_indicator_color if item.is_active else theme.indicator_color
        )
        cr.set_source_rgba(*color)

        count = min(item.instance_count, MAX_INDICATOR_DOTS)
        spacing = theme.indicator_radius * INDICATOR_SPACING_MULT
        start_x = center_x - (count - 1) * spacing / 2

        for j in range(count):
            dot_x = start_x + j * spacing
            cr.arc(dot_x, y, theme.indicator_radius, 0, 2 * math.pi)
            cr.fill()
