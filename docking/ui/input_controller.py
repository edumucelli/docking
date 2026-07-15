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

"""Raw dock input routing for the GTK dock shell."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.applets.identity import is_applet_desktop_id as is_applet
from docking.core.config import FolderStackUnfold, LeftClickAction, MiddleClickAction
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.core.position import is_horizontal
from docking.log import get_logger
from docking.platform.launcher import launch, launch_new_window, open_target
from docking.ui.autohide import HideState
from docking.ui.display import window_screen_position
from docking.ui.dnd import DnDHandler
from docking.ui.dock_interactions import DockInteractions, FolderStackAnchor
from docking.ui.geometry import current_input_rect
from docking.ui.renderer import RenderState

if TYPE_CHECKING:
    import cairo

    from docking.core.items import DockItem
    from docking.ui.dnd import DnDHandler
    from docking.ui.dock_window import DockWindow
    from docking.ui.geometry import DockGeometryFrame


log = get_logger(name="input_controller")

CLICK_DRAG_THRESHOLD = 10
REDRAW_FRAME_INTERVAL_MS = 16
SHORT_ANIMATION_PUMP_MS = 350
BOUNCE_ANIMATION_PUMP_MS = 700
MOUSE_LEFT = 1
MOUSE_MIDDLE = 2
MOUSE_RIGHT = 3


class DockInputController:
    """Own raw GTK event routing for a dock window."""

    def __init__(
        self,
        *,
        window: DockWindow,
        interactions: DockInteractions,
        dnd: DnDHandler,
    ) -> None:
        self._window = window
        self._interactions = interactions
        self._click_x: float = -1.0
        self._click_y: float = -1.0
        self._click_button: int = 0
        self.dnd = dnd
        self._started = False
        self._signal_handlers: list[tuple[object, int]] = []

    def set_theme(self, theme) -> None:
        """Update collaborators owned by this controller when the theme changes."""
        self.dnd.set_theme(theme)

    def start(self) -> None:
        """Start input routing and model-listener lifecycle."""
        if self._started:
            return
        self._started = True
        self._connect_signals()
        self._connect_model()
        self._interactions.prewarm_visible_folder_stacks(
            self._window.model.visible_items()
        )

    def stop(self) -> None:
        """Stop input routing and release signal/model listeners."""
        if not self._started:
            return
        self._disconnect_signals()
        self._disconnect_model()
        self._started = False

    def _connect_signals(self) -> None:
        drawing_area = self._window.drawing_area
        for obj, signal, callback in (
            (drawing_area, "draw", self._on_draw),
            (drawing_area, "motion-notify-event", self._on_motion),
            (drawing_area, "button-press-event", self._on_button_press),
            (drawing_area, "button-release-event", self._on_button_release),
            (drawing_area, "leave-notify-event", self._on_leave),
            (drawing_area, "enter-notify-event", self._on_enter),
            (drawing_area, "scroll-event", self._on_scroll),
            (self._window, "destroy", self._on_destroy),
        ):
            handler_id = obj.connect(signal, callback)
            self._signal_handlers.append((obj, handler_id))

    def _disconnect_signals(self) -> None:
        for obj, handler_id in self._signal_handlers:
            obj.disconnect(handler_id)
        self._signal_handlers = []

    def _connect_model(self) -> None:
        self._window.model.add_change_listener(self._on_model_changed)

    def _disconnect_model(self) -> None:
        self._window.model.remove_change_listener(self._on_model_changed)

    def _on_destroy(self, _window: Gtk.Window) -> None:
        self.stop()

    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        window = self._window
        window._clear_scheduled_redraw()
        hide_offset = window.autohide.hide_offset
        autohide_zoom = (
            window.autohide.zoom_progress if window.autohide.enabled else 1.0
        )
        zoom_progress = window.zoom_animator.progress * autohide_zoom
        drag_index = self.dnd.drag_index
        drop_insert = self.dnd.drop_insert_index
        drop_target = self.dnd.drop_target_id
        hovered_id = (
            window.hover.hovered_item.desktop_id
            if window.hover and window.hover.hovered_item
            else ""
        )
        current_autohide_state = None
        if window.autohide.enabled:
            current_autohide_state = window.autohide.state
            log.debug(
                (
                    "draw: state=%s hide_offset=%.3f zoom_progress=%.3f "
                    "hovered=%s cursor=(%.0f,%.0f)"
                ),
                window.autohide.state.value,
                hide_offset,
                zoom_progress,
                hovered_id or "-",
                window.cursor_x,
                window.cursor_y,
            )
        if window.model.tick_animations():
            window._schedule_redraw()

        frame = window._current_or_build_geometry_frame(drop_insert_index=drop_insert)
        if current_autohide_state is not None and log.isEnabledFor(logging.DEBUG):
            item_positions = [
                (
                    f"{geometry.item.desktop_id}@"
                    f"({geometry.draw_rect.x},{geometry.draw_rect.y},"
                    f"{geometry.draw_rect.w}x{geometry.draw_rect.h})"
                )
                for geometry in frame.item_geometries
            ]
            log.debug("draw items: %s", " | ".join(item_positions) or "<none>")
        window._sync_background_blur_hint(frame=frame)
        cursor_main_axis = (
            window.cursor_x if is_horizontal(pos=window.config.pos) else window.cursor_y
        )
        render_state = RenderState(
            hide_offset=hide_offset,
            drag_index=drag_index,
            drop_insert_index=drop_insert,
            hovered_id=hovered_id,
            drop_target_id=drop_target,
            cursor_main=cursor_main_axis,
        )
        window.renderer.draw(
            cr,
            widget,
            frame,
            window.config,
            window.theme,
            render_state,
        )
        window.update_input_region(frame=frame)

        if window.autohide.state == HideState.HIDDEN:
            window.cursor_x = -1.0
            window.cursor_y = -1.0
            window.hover.hovered_item = None
            window.dock_hovered = False
            window.tooltip.hide()
        elif (
            window._last_autohide_state == HideState.SHOWING
            and current_autohide_state == HideState.VISIBLE
            and window.dock_hovered
            and window.hover.hovered_item is not None
        ):
            cursor_main = (
                window.cursor_x
                if is_horizontal(pos=window.config.pos)
                else window.cursor_y
            )
            window.hover.update(cursor_main, frame=frame)
            if (
                window.config.folder_stack_unfold == FolderStackUnfold.HOVER.value
                and window.hover.hovered_item is not None
                and window.hover.hovered_item.kind == FOLDER_KIND
            ):
                self._show_folder_stack_for_item(
                    item=window.hover.hovered_item,
                    frame=frame,
                    fallback_x=window.cursor_x,
                    fallback_y=window.cursor_y,
                    toggle_if_same_item=False,
                )

        if window.renderer.has_active_urgent_glow(
            model=window.model,
            theme=window.theme,
            autohide_state=current_autohide_state,
            now_us=GLib.get_monotonic_time(),
        ):
            window._schedule_redraw()

        window._last_autohide_state = current_autohide_state
        return True

    def _on_motion(self, widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        window = self._window
        window.cursor_x = event.x
        window.cursor_y = event.y
        frame = window._build_and_store_geometry_frame()
        window.update_input_region(frame=frame)
        window._schedule_redraw()
        hovered_item = frame.item_at_point(event.x, event.y)
        self._interactions.close_folder_stack_unless_target(hovered_item)
        if frame.cursor_rect.contains(event.x, event.y):
            window.interaction.on_effective_enter()
            cursor_main = (
                window.cursor_x
                if is_horizontal(pos=window.config.pos)
                else window.cursor_y
            )
            window.hover.update(cursor_main, frame=frame)
            if hovered_item is not None and hovered_item.kind == FOLDER_KIND:
                self._interactions.prewarm_folder_stack(hovered_item)
            if (
                window.config.folder_stack_unfold == FolderStackUnfold.HOVER.value
                and hovered_item is not None
                and hovered_item.kind == FOLDER_KIND
                and (
                    not window.autohide.enabled
                    or window.autohide.state == HideState.VISIBLE
                )
            ):
                self._show_folder_stack_for_item(
                    item=hovered_item,
                    frame=frame,
                    fallback_x=event.x,
                    fallback_y=event.y,
                    toggle_if_same_item=False,
                )
        elif window.dock_hovered:
            window.interaction.on_effective_leave(widget)
        return False

    def _show_folder_stack_for_item(
        self,
        *,
        item: DockItem,
        frame: DockGeometryFrame,
        fallback_x: float,
        fallback_y: float,
        toggle_if_same_item: bool,
    ) -> None:
        window = self._window
        item_geometry = frame.geometry_for_item(item)
        if item_geometry is not None:
            window_pos = window_screen_position(window)
            win_x, win_y = window_pos.x, window_pos.y
            anchor_x, anchor_y = item_geometry.anchor_point(
                win_x=win_x,
                win_y=win_y,
                position=window.config.pos,
            )
            icon_w = int(item_geometry.draw_rect.w)
        else:
            window_pos = window_screen_position(window)
            win_x, win_y = window_pos.x, window_pos.y
            anchor_x = win_x + int(fallback_x)
            anchor_y = win_y + int(fallback_y)
            icon_w = int(window.config.icon_size)
        self._interactions.show_folder_stack(
            item=item,
            anchor=FolderStackAnchor(
                x=anchor_x,
                y=anchor_y,
                icon_w=icon_w,
                position=window.config.pos,
            ),
            toggle_if_same_item=toggle_if_same_item,
        )

    def _on_button_press(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        self._click_x = event.x
        self._click_y = event.y
        self._click_button = event.button
        return False

    def _on_button_release(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        window = self._window
        if is_horizontal(pos=window.config.pos):
            drag_delta = abs(event.x - self._click_x)
        else:
            drag_delta = abs(event.y - self._click_y)
        if drag_delta > CLICK_DRAG_THRESHOLD:
            return False

        if event.button == MOUSE_RIGHT:
            cursor_main = event.x if is_horizontal(pos=window.config.pos) else event.y
            force_background = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
            frame = window._build_and_store_geometry_frame(
                cursor_x=event.x,
                cursor_y=event.y,
            )
            self._interactions.show_context_menu(
                event=event,
                cursor_main=cursor_main,
                frame=frame,
                force_background=force_background,
            )
            return True

        if event.button in (MOUSE_LEFT, MOUSE_MIDDLE):
            frame = window._build_and_store_geometry_frame(
                cursor_x=event.x,
                cursor_y=event.y,
            )
            item = frame.item_at_point(event.x, event.y)
            if item is None:
                return True

            now = GLib.get_monotonic_time()
            item.last_clicked = now

            if is_applet(desktop_id=item.desktop_id):
                applet = window.model.get_applet(item.desktop_id)
                if applet:
                    applet.set_popup_anchor(window.popup_anchor_for_item(item, frame))
                    applet.on_clicked()
                    window.tooltip.update(item, frame)
                    window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
                    return True

            if item.kind == FOLDER_KIND:
                self._show_folder_stack_for_item(
                    item=item,
                    frame=frame,
                    fallback_x=event.x,
                    fallback_y=event.y,
                    toggle_if_same_item=(
                        window.config.folder_stack_unfold
                        != FolderStackUnfold.HOVER.value
                    ),
                )
                window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
                return True

            if item.kind == FILE_KIND:
                item.last_launched = now
                open_target(item.target)
                window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
                return True

            action = (
                window.config.middle_click_action
                if event.button == MOUSE_MIDDLE
                else window.config.left_click_action
            )
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                action = MiddleClickAction.NEW_WINDOW.value

            if action == MiddleClickAction.NEW_WINDOW.value or not item.is_running:
                item.last_launched = now
                if action == MiddleClickAction.NEW_WINDOW.value:
                    launch_new_window(desktop_id=item.desktop_id)
                else:
                    launch(desktop_id=item.desktop_id)
                window.hover.start_anim_pump(BOUNCE_ANIMATION_PUMP_MS)
            elif action == LeftClickAction.CYCLE.value:
                window.window_tracker.cycle(item.desktop_id)
                window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
            elif action == LeftClickAction.MOST_RECENT.value:
                window.window_tracker.activate_most_recent(item.desktop_id)
                window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
            elif action == MiddleClickAction.MINIMIZE.value:
                window.window_tracker.minimize_all(item.desktop_id)
                window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
            elif action == MiddleClickAction.CLOSE_FOCUSED.value:
                window.window_tracker.close_focused(item.desktop_id)
                window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
            else:
                window.window_tracker.toggle_focus(item.desktop_id)
                window.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)

        return True

    def _on_scroll(self, _widget: Gtk.DrawingArea, event: Gdk.EventScroll) -> bool:
        window = self._window
        frame = window._build_and_store_geometry_frame(
            cursor_x=event.x,
            cursor_y=event.y,
        )
        item = frame.item_at_point(event.x, event.y)
        if item and is_applet(desktop_id=item.desktop_id):
            applet = window.model.get_applet(item.desktop_id)
            if applet:
                direction_up = _scroll_direction_up(event=event)
                if direction_up is None:
                    return False
                applet.on_scroll(direction_up)
                window.tooltip.update(item, frame)
                return True
        return False

    def _on_leave(self, widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        window = self._window
        log.debug(
            "leave: detail=%s mode=%s x=%.0f y=%.0f",
            event.detail,
            event.mode,
            event.x,
            event.y,
        )
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False

        current_entry = window._cache.geometry_frame
        frame = (
            current_entry.frame if current_entry is not None else None
        ) or window._cache.applied_input_frame
        input_rect = current_input_rect(frame)
        if input_rect is not None and window.interaction.point_inside_event_frame(
            x=event.x, y=event.y
        ):
            return False

        if not window.dock_hovered:
            return False

        window.interaction.on_effective_leave(widget)
        return True

    def _on_enter(self, _widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        window = self._window
        window.cursor_x = event.x
        window.cursor_y = event.y
        frame = window._build_and_store_geometry_frame(
            cursor_x=event.x,
            cursor_y=event.y,
        )
        if frame.cursor_rect.contains(event.x, event.y):
            window.interaction.on_effective_enter()
        return True

    def _on_model_changed(self) -> None:
        window = self._window
        window._invalidate_current_geometry_frame()
        window.update_input_region()
        window.hover.on_model_changed()
        self._interactions.prewarm_visible_folder_stacks(window.model.visible_items())
        if window.hover.hovered_item is not None:
            cursor_main = (
                window.cursor_x
                if is_horizontal(pos=window.config.pos)
                else window.cursor_y
            )
            window.hover.update(cursor_main)
        window._schedule_redraw()


def _scroll_direction_up(*, event: Gdk.EventScroll) -> bool | None:
    """Normalize GTK discrete and smooth scroll events to one applet direction."""
    if event.direction == Gdk.ScrollDirection.UP:
        return True
    if event.direction == Gdk.ScrollDirection.DOWN:
        return False
    if event.direction == Gdk.ScrollDirection.SMOOTH:
        has_deltas, _dx, dy = event.get_scroll_deltas()
        if not has_deltas or dy == 0:
            return None
        return dy < 0
    return None
