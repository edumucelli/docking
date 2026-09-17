"""Deterministic dock interaction harness shared by behave scenarios."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

import cairo

import docking.ui.autohide as autohide_mod
import docking.ui.dnd as dnd_mod
import docking.ui.dock_window as dock_window_mod
import docking.ui.hover as hover_mod
import docking.ui.input_controller as input_controller_mod
import docking.ui.placement as placement_mod
import docking.ui.preview as preview_mod
import docking.ui.renderer as renderer_mod
from docking.core.config import PinnedEntry
from docking.core.items import APP_KIND, FOLDER_KIND, DockItem
from docking.core.position import Position
from docking.core.theme import Theme
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.model import DockModel
from docking.ui.autohide import AutoHideController, HideState
from docking.ui.geometry import Rect, build_geometry_frame, compute_dock_cross_metrics
from docking.ui.hover import HoverManager
from docking.ui.interaction import DockInteractionCoordinator
from docking.ui.renderer import RenderState


@dataclass
class _ScheduledCallback:
    source_id: int
    delay_ms: int
    due_ms: int
    callback: object
    args: tuple[object, ...]


@dataclass(frozen=True)
class _EdgeGapProbe:
    painted_matches_target: bool
    phantom_gap_is_clear: bool
    painted_shelf_gap: int


@dataclass(frozen=True)
class _BounceProbe:
    icon_within_surface: bool


class _TimerScheduler:
    """Simple deterministic GLib timer scheduler for autohide scenarios."""

    def __init__(self) -> None:
        self._next_source_id = 1
        self._now_ms = 0
        self._callbacks: dict[int, _ScheduledCallback] = {}

    def timeout_add(self, delay: int, callback, *args) -> int:
        source_id = self._next_source_id
        self._next_source_id += 1
        self._callbacks[source_id] = _ScheduledCallback(
            source_id=source_id,
            delay_ms=max(int(delay), 0),
            due_ms=self._now_ms + max(int(delay), 0),
            callback=callback,
            args=args,
        )
        return source_id

    def source_remove(self, source_id: int) -> bool:
        self._callbacks.pop(source_id, None)
        return True

    def source_exists(self, source_id: int) -> bool:
        return source_id in self._callbacks

    def get_monotonic_time(self) -> int:
        return self._now_ms * 1000

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, milliseconds: int) -> None:
        target_ms = self._now_ms + max(int(milliseconds), 0)
        while True:
            due = [
                scheduled
                for scheduled in self._callbacks.values()
                if scheduled.due_ms <= target_ms
            ]
            if not due:
                break
            scheduled = min(due, key=lambda item: (item.due_ms, item.source_id))
            self._now_ms = scheduled.due_ms
            keep = bool(scheduled.callback(*scheduled.args))
            if not keep:
                self._callbacks.pop(scheduled.source_id, None)
                continue
            if scheduled.source_id not in self._callbacks:
                continue
            self._callbacks[scheduled.source_id] = _ScheduledCallback(
                source_id=scheduled.source_id,
                delay_ms=scheduled.delay_ms,
                due_ms=self._now_ms + scheduled.delay_ms,
                callback=scheduled.callback,
                args=scheduled.args,
            )
        self._now_ms = target_ms


def _dnd_frame(*, item_index: int = -1, insert_index: int = 0, count: int = 1):
    item_geometries = [
        SimpleNamespace(
            item=SimpleNamespace(kind=FOLDER_KIND),
            draw_rect=SimpleNamespace(x=index * 70, y=0, w=48, h=48),
        )
        for index in range(count)
    ]
    return SimpleNamespace(
        cursor_rect=Rect(0, 0, 400, 60),
        item_geometries=item_geometries,
        item_index_at_point=MagicMock(return_value=item_index),
        item_at_point=MagicMock(return_value=None),
        insertion_index_for_main=MagicMock(return_value=insert_index),
    )


def _item_geometry(*, x: int, y: int, w: int = 48, h: int = 48) -> SimpleNamespace:
    draw_rect = SimpleNamespace(x=x, y=y, w=w, h=h)
    return SimpleNamespace(
        draw_rect=draw_rect,
        anchor_point=lambda *, win_x, win_y, position: (win_x + x, win_y + y),
    )


def _bind_dock_window_helpers(stub) -> SimpleNamespace:
    stub._geometry_signature = MethodType(
        dock_window_mod.DockWindow._geometry_signature, stub
    )
    stub._build_and_store_geometry_frame = MethodType(
        dock_window_mod.DockWindow._build_and_store_geometry_frame, stub
    )
    stub._current_or_build_geometry_frame = MethodType(
        dock_window_mod.DockWindow._current_or_build_geometry_frame, stub
    )
    stub._clear_scheduled_redraw = MethodType(
        dock_window_mod.DockWindow._clear_scheduled_redraw, stub
    )
    stub._flush_scheduled_redraw = MethodType(
        dock_window_mod.DockWindow._flush_scheduled_redraw, stub
    )
    stub._schedule_redraw = MethodType(
        dock_window_mod.DockWindow._schedule_redraw, stub
    )
    stub._invalidate_current_geometry_frame = MethodType(
        dock_window_mod.DockWindow._invalidate_current_geometry_frame, stub
    )
    return stub


def _controller(stub) -> SimpleNamespace:
    controller = SimpleNamespace(
        _window=stub,
        _interactions=stub._interactions,
        _application_launcher=getattr(stub, "_application_launcher", MagicMock()),
        _target_service=getattr(stub, "_target_service", MagicMock()),
        _click_x=getattr(stub, "_click_x", -1.0),
        _click_y=getattr(stub, "_click_y", -1.0),
        _click_button=getattr(stub, "_click_button", 0),
        dnd=getattr(
            stub,
            "dnd",
            SimpleNamespace(drag_index=-1, drop_insert_index=-1, drop_target_id=""),
        ),
    )
    controller._show_folder_stack_for_item = MethodType(
        input_controller_mod.DockInputController._show_folder_stack_for_item,
        controller,
    )
    controller._stack_anchor_for_item = MethodType(
        input_controller_mod.DockInputController._stack_anchor_for_item,
        controller,
    )
    return controller


class DockHarness:
    """High-level user-facing harness for behave interaction scenarios."""

    def __init__(self) -> None:
        self._patchers: list[object] = []
        self._scheduler = _TimerScheduler()
        self._autohide_window = MagicMock()
        self._autohide_config = MagicMock()
        self._autohide_config.hide_mode = "autohide"
        self._autohide_config.hide_delay_ms = 0
        self._autohide_config.unhide_delay_ms = 0
        self._autohide_config.hide_time_ms = 32
        self.autohide = AutoHideController(
            self._autohide_window,
            self._autohide_config,
        )

        self._folder_item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        self._other_item = DockItem(desktop_id="firefox.desktop")
        self._folder_stack_open_for: str | None = None
        self._folder_menu = MagicMock()
        self._folder_menu.folder_stack_item_id.return_value = None
        self._folder_menu.close_folder_stack_for_item = MagicMock(
            side_effect=lambda _desktop_id: self._close_folder_stack()
        )
        self._folder_menu.close_stack_unless_target = MagicMock(
            side_effect=self._close_stack_unless_target
        )
        self._folder_menu.show_folder_stack = MagicMock(
            side_effect=self._open_folder_stack
        )
        self._folder_frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 100, 100),
            item_at_point=MagicMock(return_value=None),
            geometry_for_item=MagicMock(return_value=_item_geometry(x=4, y=5)),
        )
        self._folder_stub = _bind_dock_window_helpers(
            SimpleNamespace(
                cursor_x=12.0,
                cursor_y=6.0,
                dock_hovered=True,
                config=SimpleNamespace(
                    pos=Position.BOTTOM,
                    stack_unfold="click",
                ),
                _test_geometry_frame=self._folder_frame,
                update_input_region=MagicMock(),
                drawing_area=MagicMock(),
                hover=SimpleNamespace(update=MagicMock(), start_anim_pump=MagicMock()),
                _interactions=self._folder_menu,
                autohide=SimpleNamespace(
                    enabled=False,
                    state=HideState.VISIBLE,
                    zoom_progress=1.0,
                    hide_offset=0.0,
                    set_disabled=MagicMock(),
                    set_hovered=MagicMock(),
                    on_mouse_enter=MagicMock(),
                    on_mouse_leave=MagicMock(),
                ),
                zoom_animator=SimpleNamespace(progress=1.0),
                geometry=SimpleNamespace(
                    build_frame=lambda **_kwargs: self._folder_frame,
                ),
                interaction=None,
                _click_x=12.0,
                _click_y=6.0,
                _redraw_source_id=None,
                hit_test=MagicMock(return_value=self._folder_item),
                model=MagicMock(),
                theme=SimpleNamespace(item_padding=8, horizontal_padding=10),
                window_tracker=MagicMock(),
                tooltip=MagicMock(),
                preview=None,
                _cache=dock_window_mod._DockWindowCache.create(),
                local_cursor_main=MagicMock(return_value=-1e6),
                get_position=MagicMock(return_value=(100, 200)),
                get_size=MagicMock(return_value=(1920, 122)),
            )
        )
        self._folder_stub._cache.applied_input_frame = self._folder_frame
        self._folder_stub.interaction = DockInteractionCoordinator(self._folder_stub)

        self._dnd_frame = _dnd_frame()
        self._drag_reorder_called = False
        self._drag_removed_desktop_id: str | None = None
        self._external_pinned_targets: list[str] = []
        self._geometry_position = Position.BOTTOM
        self._geometry_gap = 0
        self._edge_gap_probe: _EdgeGapProbe | None = None
        self._bounce_probe: _BounceProbe | None = None
        self._position_change_aligned = False
        self._left_edge_input_owned = False
        self._left_edge_window = None
        self._animation_model: DockModel | None = None
        self._animation_item: DockItem | None = None
        self._animation_frame_delay_ms = 0
        self._insertion_elapsed_ms = 0
        self._insertion_widths: list[int] = []
        self._removal_elapsed_ms = 0
        self._removal_final_ids: list[str] = []
        self._build_hover_harness()

    def start(self) -> None:
        patchers = [
            patch.object(
                autohide_mod.GLib,
                "get_monotonic_time",
                side_effect=self._scheduler.get_monotonic_time,
            ),
            patch.object(
                autohide_mod.GLib,
                "timeout_add",
                side_effect=self._scheduler.timeout_add,
            ),
            patch.object(
                autohide_mod.GLib,
                "source_remove",
                side_effect=self._scheduler.source_remove,
            ),
            patch.object(
                autohide_mod,
                "_source_exists",
                side_effect=self._scheduler.source_exists,
            ),
            patch.object(dnd_mod.Gdk, "drag_status", lambda *_args, **_kwargs: None),
            patch.object(dnd_mod.Gtk, "drag_set_icon_pixbuf", lambda *_args: None),
            patch.object(dnd_mod.Gtk, "drag_finish", lambda *_args: None),
            patch.object(dnd_mod, "show_poof", MagicMock()),
        ]
        self._patchers = []
        try:
            for patcher in patchers:
                patcher.start()
                self._patchers.append(patcher)
            self._build_drag_handler()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._patchers.clear()

    def set_hide_mode(self, hide_mode: str) -> None:
        self._autohide_config.hide_mode = hide_mode
        self.autohide.reconcile()

    def move_pointer_to_dock(self) -> None:
        self.autohide.on_mouse_enter()

    def move_pointer_off_dock(self) -> None:
        self.autohide.on_mouse_leave()

    def advance_time(self, milliseconds: int) -> None:
        self._scheduler.advance(milliseconds)

    def configure_geometry(self, *, position: str, gap: int) -> None:
        self._geometry_position = Position(position)
        self._geometry_gap = gap

    def begin_item_insertion(self) -> None:
        model = DockModel.__new__(DockModel)
        steady = DockItem(desktop_id="firefox.desktop")
        animated = DockItem(desktop_id="applet://clock", insert_factor=0.0)
        model.pinned_items = [steady, animated]
        model._transient = []
        model._animating_out = []
        model._animation_last_update_us = {}
        model._start_item_animation(animated)
        self._animation_model = model
        self._animation_item = animated

    def run_delayed_insertion_frames(self, *, frame_delay_ms: int) -> None:
        assert self._animation_model is not None
        assert self._animation_item is not None
        self._animation_frame_delay_ms = frame_delay_ms
        started_ms = self._scheduler.now_ms
        tick = None
        for _ in range(10):
            self._scheduler.advance(frame_delay_ms)
            tick = self._animation_model.tick_animations()
            frame = self._item_animation_frame(
                self._animation_model.pinned_items
                + self._animation_model._animating_out
            )
            geometry = frame.geometry_for_item(self._animation_item)
            assert geometry is not None
            self._insertion_widths.append(geometry.layout_item.width)
            if not tick.active:
                break
        assert tick is not None and not tick.active
        self._insertion_elapsed_ms = self._scheduler.now_ms - started_ms

    def run_delayed_removal_frames(self) -> None:
        assert self._animation_model is not None
        assert self._animation_item is not None
        item = self._animation_item
        item.removal_index = self._animation_model.pinned_items.index(item)
        self._animation_model._reverse_item_animation(item, target=1.0)
        self._animation_model.pinned_items.remove(item)
        self._animation_model._animating_out.append(item)
        started_ms = self._scheduler.now_ms
        tick = None
        frame = None
        for _ in range(10):
            self._scheduler.advance(self._animation_frame_delay_ms)
            tick = self._animation_model.tick_animations()
            frame = self._item_animation_frame(
                self._animation_model.pinned_items
                + self._animation_model._animating_out
            )
            if not tick.active:
                break
        assert tick is not None and not tick.active
        assert frame is not None
        self._removal_elapsed_ms = self._scheduler.now_ms - started_ms
        self._removal_final_ids = [
            geometry.item.desktop_id for geometry in frame.item_geometries
        ]

    def _item_animation_frame(self, items: list[DockItem]):
        theme = Theme.load("default", 48)
        return build_geometry_frame(
            items=items,
            config=self._geometry_config(pos=Position.BOTTOM, zoom=1.0),
            theme=theme,
            window_w=1280,
            window_h=187,
            cursor_main=-1.0,
            autohide_state=None,
        )

    @property
    def insertion_is_bounded_and_progressive(self) -> bool:
        return (
            0 < self._insertion_widths[0] < 48
            and self._insertion_widths[-1] == 48
            and self._insertion_elapsed_ms <= self._animation_frame_delay_ms * 2
        )

    @property
    def removal_is_bounded_without_ghost(self) -> bool:
        return (
            self._removal_elapsed_ms <= self._animation_frame_delay_ms * 2
            and "applet://clock" not in self._removal_final_ids
        )

    def render_resting_geometry(self) -> None:
        pos = self._geometry_position
        gap = self._geometry_gap
        theme = replace(Theme.load("default", 48), distance_from_edge=gap)
        config = self._geometry_config(pos=pos, zoom=1.0)
        item = DockItem(desktop_id="firefox.desktop")
        metrics = compute_dock_cross_metrics(icon_size=48, zoom=1.0, theme=theme)
        window_w, window_h = self._geometry_window_size(
            pos=pos,
            cross_extent=metrics.surface_extent + gap,
        )
        frame = build_geometry_frame(
            items=[item],
            config=config,
            theme=theme,
            window_w=window_w,
            window_h=window_h,
            cursor_main=-1.0,
            autohide_state=None,
        )
        painted_icons: list[tuple[float, float]] = []
        painted_shelves: list[Rect] = []

        def capture_icon(**kwargs) -> None:
            painted_icons.append(kwargs["cr"].user_to_device(kwargs["x"], kwargs["y"]))

        def capture_shelf(**kwargs) -> None:
            cr = kwargs["cr"]
            x, y, width, height = (
                kwargs["x"],
                kwargs["y"],
                kwargs["w"],
                kwargs["h"],
            )
            corners = [
                cr.user_to_device(px, py)
                for px, py in (
                    (x, y),
                    (x + width, y),
                    (x, y + height),
                    (x + width, y + height),
                )
            ]
            left = round(min(point[0] for point in corners))
            top = round(min(point[1] for point in corners))
            right = round(max(point[0] for point in corners))
            bottom = round(max(point[1] for point in corners))
            painted_shelves.append(Rect(left, top, right - left, bottom - top))

        renderer = renderer_mod.DockRenderer()
        renderer._draw_icon = MagicMock(side_effect=capture_icon)
        with (
            patch.object(renderer_mod, "draw_shelf_background", capture_shelf),
            patch.object(
                renderer_mod.GLib, "get_monotonic_time", return_value=1_000_000
            ),
        ):
            renderer._draw_content(
                cr=cairo.Context(
                    cairo.ImageSurface(
                        cairo.FORMAT_ARGB32,
                        window_w,
                        window_h,
                    )
                ),
                frame=frame,
                config=config,
                theme=theme,
                state=RenderState(),
            )

        draw_rect = frame.item_geometries[0].draw_rect
        painted_x, painted_y = painted_icons[0]
        painted_center = (
            painted_x + draw_rect.w / 2,
            painted_y + draw_rect.h / 2,
        )
        phantom_point = self._gap_midpoint(
            pos=pos,
            gap=gap,
            window_w=window_w,
            window_h=window_h,
            draw_rect=draw_rect,
        )
        shelf = painted_shelves[0]
        self._edge_gap_probe = _EdgeGapProbe(
            painted_matches_target=(
                int(painted_x) == draw_rect.x
                and int(painted_y) == draw_rect.y
                and frame.item_at_point(*painted_center) is item
            ),
            phantom_gap_is_clear=frame.item_at_point(*phantom_point) is None,
            painted_shelf_gap=self._shelf_edge_gap(
                pos=pos,
                shelf=shelf,
                window_w=window_w,
                window_h=window_h,
            ),
        )

    def render_peak_bounce(self) -> None:
        pos = self._geometry_position
        gap = self._geometry_gap
        zoom = 1.5
        theme = replace(Theme.load("default", 48), distance_from_edge=gap)
        config = self._geometry_config(pos=pos, zoom=zoom)
        now_us = 1_000_000
        item = DockItem(
            desktop_id="firefox.desktop",
            last_launched=now_us - theme.launch_bounce_time_ms * 1000 // 4,
            last_urgent=now_us - theme.urgent_bounce_time_ms * 1000 // 2,
        )
        metrics = compute_dock_cross_metrics(icon_size=48, zoom=zoom, theme=theme)
        window_w, window_h = self._geometry_window_size(
            pos=pos,
            cross_extent=metrics.surface_extent + gap,
        )
        main_size = window_w if pos in (Position.BOTTOM, Position.TOP) else window_h
        base_width = 2 * (theme.horizontal_padding + theme.item_padding / 2) + 48
        cursor_main = theme.horizontal_padding + 24 + (main_size - base_width) / 2
        frame = build_geometry_frame(
            items=[item],
            config=config,
            theme=theme,
            window_w=window_w,
            window_h=window_h,
            cursor_main=cursor_main,
            autohide_state=None,
        )
        painted_icons: list[tuple[float, float, float]] = []

        def capture_icon(**kwargs) -> None:
            x, y = kwargs["cr"].user_to_device(kwargs["x"], kwargs["y"])
            painted_icons.append((x, y, kwargs["base_size"] * kwargs["li"].scale))

        renderer = renderer_mod.DockRenderer()
        renderer._draw_icon = MagicMock(side_effect=capture_icon)
        with (
            patch.object(renderer_mod, "draw_shelf_background", lambda **_kwargs: None),
            patch.object(renderer_mod.GLib, "get_monotonic_time", return_value=now_us),
        ):
            renderer._draw_content(
                cr=cairo.Context(
                    cairo.ImageSurface(
                        cairo.FORMAT_ARGB32,
                        window_w,
                        window_h,
                    )
                ),
                frame=frame,
                config=config,
                theme=theme,
                state=RenderState(),
            )

        x, y, size = painted_icons[0]
        self._bounce_probe = _BounceProbe(
            icon_within_surface=(
                x >= 0.0 and y >= 0.0 and x + size <= window_w and y + size <= window_h
            )
        )

    def render_position_change(self, *, old_position: str, new_position: str) -> None:
        theme = Theme.load("default", 48)
        metrics = compute_dock_cross_metrics(icon_size=48, zoom=1.0, theme=theme)
        item = DockItem(desktop_id="firefox.desktop")
        renderer = renderer_mod.DockRenderer()
        painted_icons: list[tuple[float, float]] = []
        renderer._draw_icon = MagicMock(
            side_effect=lambda **kwargs: painted_icons.append(
                kwargs["cr"].user_to_device(kwargs["x"], kwargs["y"])
            )
        )

        def dimensions(pos: Position) -> tuple[int, int]:
            if pos in (Position.BOTTOM, Position.TOP):
                return 1280, metrics.surface_extent
            return metrics.surface_extent, 800

        def render(pos: Position, width: int, height: int):
            config = self._geometry_config(pos=pos, zoom=1.0)
            frame = build_geometry_frame(
                items=[item],
                config=config,
                theme=theme,
                window_w=width,
                window_h=height,
                cursor_main=-1.0,
                autohide_state=None,
            )
            renderer._draw_content(
                cr=cairo.Context(
                    cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
                ),
                frame=frame,
                config=config,
                theme=theme,
                state=RenderState(),
            )
            return frame

        old_pos = Position(old_position)
        new_pos = Position(new_position)
        old_width, old_height = dimensions(old_pos)
        new_width, new_height = dimensions(new_pos)
        with (
            patch.object(renderer_mod, "draw_shelf_background", lambda **_kwargs: None),
            patch.object(
                renderer_mod.GLib,
                "get_monotonic_time",
                return_value=1_000_000,
            ),
        ):
            render(old_pos, old_width, old_height)
            render(new_pos, old_width, old_height)
            final_frame = render(new_pos, new_width, new_height)

        final_rect = final_frame.item_geometries[0].draw_rect
        painted_x, painted_y = painted_icons[-1]
        self._position_change_aligned = (
            int(painted_x) == final_rect.x
            and int(painted_y) == final_rect.y
            and renderer.slide_offsets == {}
        )
        if old_pos == Position.RIGHT and new_pos == Position.LEFT:
            self._probe_left_edge_input_after_side_change(
                theme=theme,
                item=item,
                width=new_width,
                height=new_height,
            )

    def _probe_left_edge_input_after_side_change(
        self,
        *,
        theme: Theme,
        item: DockItem,
        width: int,
        height: int,
    ) -> None:
        config = self._geometry_config(pos=Position.RIGHT, zoom=1.0)

        def build_live_frame(**_kwargs):
            return build_geometry_frame(
                items=[item],
                config=config,
                theme=theme,
                window_w=width,
                window_h=height,
                cursor_main=-1.0,
                autohide_state=None,
            )

        old_frame = build_live_frame()
        surface_service = MagicMock()
        window = _bind_dock_window_helpers(
            SimpleNamespace(
                config=config,
                cursor_x=-1.0,
                cursor_y=-1.0,
                get_size=MagicMock(return_value=(width, height)),
                autohide=SimpleNamespace(enabled=False),
                zoom_animator=SimpleNamespace(progress=1.0),
                geometry=SimpleNamespace(build_frame=build_live_frame),
                surface_service=surface_service,
                drawing_area=SimpleNamespace(queue_draw=MagicMock()),
                _cache=dock_window_mod._DockWindowCache.create(),
            )
        )
        window.update_input_region = MethodType(
            dock_window_mod.DockWindow.update_input_region,
            window,
        )
        window._cache.store_geometry_frame(
            frame=old_frame,
            signature=window._geometry_signature(),
        )
        window._cache.applied_input_frame = old_frame

        config.pos = Position.LEFT
        placement = placement_mod.DockPlacementController(
            window,
            surface_service=surface_service,
        )
        placement.position_dock = MagicMock()
        placement.set_struts = MagicMock()
        placement.reposition()

        applied = window._cache.applied_input_frame
        center_y = applied.cursor_rect.y + applied.cursor_rect.h / 2
        self._left_edge_input_owned = (
            applied.cursor_rect.contains(0, center_y)
            and surface_service.update_input_region.call_count == 1
        )

    def hold_pointer_at_left_edge(self) -> None:
        frame = SimpleNamespace(cursor_rect=Rect(0, 0, 53, 420))
        interaction = MagicMock()
        interaction.point_inside_event_frame.return_value = False
        interaction.is_pointer_inside_dock.return_value = True
        self._left_edge_window = SimpleNamespace(
            _cache=dock_window_mod._DockWindowCache.create(),
            interaction=interaction,
            dock_hovered=True,
        )
        self._left_edge_window._cache.store_geometry_frame(
            frame=frame,
            signature=(),
        )

    def report_left_edge_shape_leave(self) -> None:
        assert self._left_edge_window is not None
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.ANCESTOR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=-1.0,
            y=210.0,
        )
        input_controller_mod.DockInputController._on_leave(
            SimpleNamespace(_window=self._left_edge_window),
            MagicMock(),
            event,
        )

    @property
    def painted_geometry_matches_target(self) -> bool:
        assert self._edge_gap_probe is not None
        return self._edge_gap_probe.painted_matches_target

    @property
    def floating_gap_is_clear(self) -> bool:
        assert self._edge_gap_probe is not None
        return self._edge_gap_probe.phantom_gap_is_clear

    @property
    def painted_shelf_gap(self) -> int:
        assert self._edge_gap_probe is not None
        return self._edge_gap_probe.painted_shelf_gap

    @property
    def bounced_icon_within_surface(self) -> bool:
        assert self._bounce_probe is not None
        return self._bounce_probe.icon_within_surface

    @property
    def position_change_aligned(self) -> bool:
        return self._position_change_aligned

    @property
    def left_edge_input_owned(self) -> bool:
        return self._left_edge_input_owned

    @property
    def left_edge_hover_retained(self) -> bool:
        assert self._left_edge_window is not None
        return not self._left_edge_window.interaction.on_effective_leave.called

    @property
    def dock_hidden(self) -> bool:
        return self.autohide.state == HideState.HIDDEN

    @property
    def dock_visible(self) -> bool:
        return self.autohide.state == HideState.VISIBLE

    def left_click_item(self, desktop_id: str) -> None:
        if desktop_id != self._folder_item.desktop_id:
            raise AssertionError(f"Unsupported click target {desktop_id}")
        self._folder_frame.item_at_point.return_value = self._folder_item
        event = SimpleNamespace(
            x=12.0,
            y=6.0,
            button=dock_window_mod.MOUSE_LEFT,
            state=0,
        )
        input_controller_mod.DockInputController._on_button_release(
            _controller(self._folder_stub),
            MagicMock(),
            event,
        )

    def move_pointer_to_item(self, desktop_id: str) -> None:
        target = (
            self._folder_item
            if desktop_id == self._folder_item.desktop_id
            else self._other_item
        )
        self._folder_frame.item_at_point.return_value = target
        event = SimpleNamespace(x=12.0, y=9.0)
        input_controller_mod.DockInputController._on_motion(
            _controller(self._folder_stub), MagicMock(), event
        )

    @property
    def folder_stack_open_for(self) -> str | None:
        return self._folder_stack_open_for

    def hover_running_item_long_enough(self, desktop_id: str) -> None:
        item = self._hover_item_by_desktop_id(desktop_id)
        self._hover_frame.hover_item_at_point.return_value = item
        self._hover_window.cursor_x = 20.0
        self._hover_window.cursor_y = 10.0
        self._hover_window.dock_hovered = True
        self._hover_manager.update(cursor_main=20.0)
        self._scheduler.advance(hover_mod.PREVIEW_SHOW_DELAY_MS)

    def leave_dock_with_preview_visible(self) -> None:
        self._hover_interaction.on_effective_leave(MagicMock())

    def finish_preview_hide(self) -> None:
        self._scheduler.advance(preview_mod.PREVIEW_HIDE_DELAY_MS)

    def set_dock_showing(self) -> None:
        self._hover_window.autohide.state = HideState.SHOWING
        self._hover_window.autohide.enabled = True
        self._hover_window.dock_hovered = True

    def hover_item(self, desktop_id: str) -> None:
        item = self._hover_item_by_desktop_id(desktop_id)
        self._hover_frame.hover_item_at_point.return_value = item
        self._hover_window.cursor_x = 20.0
        self._hover_window.cursor_y = 10.0
        self._hover_window.dock_hovered = True
        self._hover_manager.update(cursor_main=20.0)

    def begin_drag(self, desktop_id: str) -> None:
        if desktop_id != "a.desktop":
            raise AssertionError(f"Unsupported drag source {desktop_id}")
        self._drag_handler._model.visible_items.return_value = [
            DockItem(
                desktop_id="a.desktop",
                name="A",
                icon=MagicMock(),
                is_pinned=True,
            ),
            DockItem(desktop_id="b.desktop", name="B", icon=MagicMock()),
        ]
        self._dnd_frame = _dnd_frame(item_index=0, count=2)
        self._drag_handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: self._dnd_frame
        )
        self._drag_handler._model.reorder_visible = MagicMock(
            side_effect=self._mark_drag_reorder
        )
        self._drag_handler._on_drag_begin(self._drag_handler._drawing_area, MagicMock())

    def drag_to_index(self, index: int) -> None:
        self._dnd_frame = _dnd_frame(insert_index=index, count=2)
        self._drag_handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: self._dnd_frame
        )
        self._drag_handler._on_drag_motion(
            self._drag_handler._drawing_area,
            MagicMock(),
            200,
            5,
            1,
        )

    def drag_outside_and_release(self, desktop_id: str) -> None:
        self.begin_drag(desktop_id)
        self._drag_handler._model.unpin_item = MagicMock(
            side_effect=self._mark_drag_removed
        )
        # Simulate GTK drag-leave having fired during the drag so the
        # _internal_drag_left_dock gate in _on_drag_end passes.
        self._drag_handler._internal_drag_left_dock = True
        self._drag_folder_stack.open_item_id.return_value = desktop_id
        self._drag_pointer.get_position.return_value = (None, 200, 50)
        self._drag_window.get_position.return_value = (100, 200)
        self._drag_window.get_size.return_value = (400, 60)
        self._drag_handler._on_drag_end(self._drag_handler._drawing_area, MagicMock())

    @property
    def drag_reordered(self) -> bool:
        return self._drag_reorder_called

    @property
    def drag_removed_desktop_id(self) -> str | None:
        return self._drag_removed_desktop_id

    def drop_external_uri(self, uri: str, target_index: int) -> None:
        self._drag_handler._drag_from = -1
        self._drag_handler._drop_committed = True
        self._drag_handler.drop_insert_index = target_index
        self._drag_handler._model.pinned_items = []
        self._drag_handler._model.find_by_desktop_id.return_value = None
        self._drag_handler._model.notify = MagicMock()
        self._drag_handler._config.pinned = []
        self._drag_handler._config.save = MagicMock()

        def insert_pinned_item(*, item: DockItem, index: int) -> bool:
            item.is_pinned = True
            self._drag_handler._model.pinned_items.insert(index, item)
            self._drag_handler._config.pinned.insert(
                index,
                PinnedEntry(kind=item.kind, target=item.target),
            )
            self._drag_handler._config.save()
            self._drag_handler._model.notify()
            return True

        self._drag_handler._model.insert_pinned_item.side_effect = insert_pinned_item
        self._drag_handler._model.insert_pinned_application.side_effect = (
            lambda *, desktop_id, index: insert_pinned_item(
                item=DockItem(
                    desktop_id=desktop_id,
                    kind=APP_KIND,
                    target=desktop_id,
                    is_pinned=True,
                ),
                index=index,
            )
        )
        self._drag_handler._application_registry.get.return_value = ApplicationInfo(
            desktop_id="firefox.desktop",
            name="Firefox",
            declared_icon="firefox",
            wm_class="firefox",
            exec_line="/usr/bin/firefox",
            origin=ApplicationOrigin.INSTALLED,
            location=ApplicationLocation.SANDBOX,
            desktop_file=None,
            executable_path=None,
            aliases=(),
            visible=True,
            has_gio_source=True,
        )
        self._drag_handler._icon_loader.load_desktop_icon.return_value = object()
        selection = MagicMock()
        selection.get_uris.return_value = [uri]
        self._drag_handler._on_drag_data_received(
            self._drag_handler._drawing_area,
            MagicMock(),
            0,
            0,
            selection,
            1,
            77,
        )
        self._external_pinned_targets = [
            entry.target for entry in self._drag_handler._config.pinned
        ]

    @property
    def external_pinned_targets(self) -> list[str]:
        return list(self._external_pinned_targets)

    @property
    def preview_visible(self) -> bool:
        return self._preview_visible

    @property
    def preview_hide_scheduled(self) -> bool:
        return self._preview_popup._hide_timer_id != 0

    @property
    def autohide_leave_released(self) -> bool:
        return self._hover_window.autohide.on_mouse_leave.called

    @property
    def tooltip_suppressed(self) -> bool:
        return bool(self._tooltip_hidden) and not self._tooltip_updated

    @staticmethod
    def _geometry_config(*, pos: Position, zoom: float) -> SimpleNamespace:
        return SimpleNamespace(
            pos=pos,
            icon_size=48,
            zoom_enabled=zoom > 1.0,
            zoom_percent=zoom,
            additional_distance_from_edge=0,
            show_window_count_numbers=False,
            show_launcher_badges=False,
            show_launcher_progress=False,
        )

    @staticmethod
    def _geometry_window_size(*, pos: Position, cross_extent: int) -> tuple[int, int]:
        if pos in (Position.BOTTOM, Position.TOP):
            return 420, cross_extent
        return cross_extent, 420

    @staticmethod
    def _gap_midpoint(
        *,
        pos: Position,
        gap: int,
        window_w: int,
        window_h: int,
        draw_rect: Rect,
    ) -> tuple[float, float]:
        if pos == Position.TOP:
            return draw_rect.x + draw_rect.w / 2, gap / 2
        if pos == Position.BOTTOM:
            return draw_rect.x + draw_rect.w / 2, window_h - gap / 2
        if pos == Position.LEFT:
            return gap / 2, draw_rect.y + draw_rect.h / 2
        return window_w - gap / 2, draw_rect.y + draw_rect.h / 2

    @staticmethod
    def _shelf_edge_gap(
        *,
        pos: Position,
        shelf: Rect,
        window_w: int,
        window_h: int,
    ) -> int:
        if pos == Position.TOP:
            return shelf.y
        if pos == Position.BOTTOM:
            return window_h - shelf.y - shelf.h
        if pos == Position.LEFT:
            return shelf.x
        return window_w - shelf.x - shelf.w

    def _build_drag_handler(self) -> None:
        drawing_area = MagicMock()
        config = SimpleNamespace(
            lock_icons=False,
            pos=Position.BOTTOM,
            icon_size=48,
            zoom_percent=2.0,
            scaled_icon_size=96,
            pinned=[],
            save=MagicMock(),
        )
        model = MagicMock()
        renderer = SimpleNamespace(slide_offsets={}, prev_positions={})
        theme = SimpleNamespace(item_padding=8, horizontal_padding=10)
        application_registry = MagicMock()
        application_registry.get.return_value = None
        application_registry.resolve_by_desktop_file.return_value = None
        application_launcher = MagicMock()
        icon_loader = MagicMock()
        target_service = MagicMock()
        target_service.resolve_file.return_value = None
        pointer = MagicMock()
        pointer.get_position.return_value = (None, 0, 0)
        seat = MagicMock()
        seat.get_pointer.return_value = pointer
        display = MagicMock()
        display.get_default_seat.return_value = seat
        autohide = SimpleNamespace(
            enabled=True,
            set_disabled=MagicMock(),
            set_hovered=MagicMock(),
            on_mouse_enter=MagicMock(),
            on_mouse_leave=MagicMock(),
        )
        window = SimpleNamespace(
            cursor_x=20.0,
            cursor_y=8.0,
            autohide=autohide,
            is_pointer_inside_dock=MagicMock(return_value=False),
            get_display=MagicMock(return_value=display),
            get_position=MagicMock(return_value=(0, 0)),
            get_size=MagicMock(return_value=(400, 60)),
        )
        folder_stack = MagicMock()
        folder_stack.open_item_id.return_value = None
        self._drag_pointer = pointer
        self._drag_window = window
        self._drag_folder_stack = folder_stack
        self._drag_handler = dnd_mod.DnDHandler(
            drawing_area,
            window,
            model,
            config,
            renderer,
            theme,
            geometry_builder=SimpleNamespace(
                build_frame=lambda **_kwargs: self._dnd_frame
            ),
            folder_stack=folder_stack,
            application_registry=application_registry,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
        )

    def _mark_drag_reorder(self, *_args, **_kwargs) -> None:
        self._drag_reorder_called = True

    def _mark_drag_removed(self, desktop_id: str) -> None:
        self._drag_removed_desktop_id = desktop_id

    def _open_folder_stack(self, *, item: DockItem, **_kwargs) -> None:
        self._folder_stack_open_for = item.desktop_id
        self._folder_menu.folder_stack_item_id.return_value = item.desktop_id

    def _close_folder_stack(self) -> None:
        self._folder_stack_open_for = None
        self._folder_menu.folder_stack_item_id.return_value = None

    def _close_stack_unless_target(self, hovered_item: DockItem | None) -> None:
        if (
            hovered_item is not None
            and hovered_item.desktop_id == self._folder_stack_open_for
        ):
            return
        self._close_folder_stack()

    def _build_hover_harness(self) -> None:
        self._tooltip_updated = False
        self._tooltip_hidden = False
        self._preview_visible = False
        self._hover_items = {
            "firefox.desktop": DockItem(
                desktop_id="firefox.desktop",
                name="Firefox",
                is_running=True,
                instance_count=1,
            ),
            "code.desktop": DockItem(
                desktop_id="code.desktop",
                name="Code",
                is_running=False,
                instance_count=0,
            ),
        }
        self._hover_frame = SimpleNamespace(
            hover_item_at_point=MagicMock(return_value=None),
            geometry_for_item=MagicMock(return_value=_item_geometry(x=15, y=4)),
            cursor_rect=SimpleNamespace(contains=lambda *_args, **_kwargs: True),
        )
        self._hover_tooltip = SimpleNamespace(
            update=MagicMock(side_effect=self._mark_tooltip_updated),
            hide=MagicMock(side_effect=self._mark_tooltip_hidden),
        )
        self._hover_window = SimpleNamespace(
            get_realized=MagicMock(return_value=True),
            get_position=MagicMock(return_value=(100, 200)),
            dock_hovered=True,
            drawing_area=MagicMock(),
            cursor_x=20.0,
            cursor_y=10.0,
            autohide=SimpleNamespace(
                enabled=True,
                state=HideState.VISIBLE,
                on_mouse_enter=MagicMock(),
                on_mouse_leave=MagicMock(),
                set_disabled=MagicMock(),
                set_hovered=MagicMock(),
            ),
            preview=None,
            tooltip=self._hover_tooltip,
            update_input_region=MagicMock(),
            zoom_animator=SimpleNamespace(on_leave=MagicMock(), on_enter=MagicMock()),
            hover=None,
        )
        self._hover_config = SimpleNamespace(
            previews_enabled=True,
            icon_size=48,
            pos=Position.BOTTOM,
            tooltips_enabled=True,
        )
        self._hover_theme = SimpleNamespace(
            item_padding=8,
            horizontal_padding=10,
            bottom_padding=12,
            launch_bounce_height=0.5,
        )
        self._hover_model = MagicMock()
        self._hover_model.visible_items.return_value = list(self._hover_items.values())
        self._hover_manager = HoverManager(
            self._hover_window,
            self._hover_config,
            self._hover_model,
            self._hover_theme,
            self._hover_tooltip,
            geometry_builder=SimpleNamespace(
                build_frame=lambda **_kwargs: self._hover_frame
            ),
        )
        self._preview_popup = preview_mod.PreviewPopup.__new__(preview_mod.PreviewPopup)
        self._preview_popup._tracker = MagicMock()
        self._preview_popup._autohide = self._hover_window.autohide
        self._preview_popup._pointer_inside_dock = lambda: (
            self._hover_window.dock_hovered
        )
        self._preview_popup._hide_timer_id = 0
        self._preview_popup._current_desktop_id = ""
        self._preview_popup.hide = MagicMock(side_effect=self._hide_preview_popup)
        self._preview_popup.show_for_item = MagicMock(
            side_effect=self._show_preview_popup
        )
        self._preview_popup.get_visible = MagicMock(
            side_effect=lambda: self._preview_visible
        )
        self._hover_manager.set_preview(self._preview_popup)
        self._hover_window.preview = self._preview_popup
        self._hover_window.hover = self._hover_manager
        self._hover_interaction = DockInteractionCoordinator(self._hover_window)

    def _hover_item_by_desktop_id(self, desktop_id: str) -> DockItem:
        try:
            return self._hover_items[desktop_id]
        except KeyError as exc:
            raise AssertionError(f"Unsupported hover item {desktop_id}") from exc

    def _show_preview_popup(
        self,
        desktop_id: str,
        _anchor_x: float,
        _icon_w: float,
        _anchor_y: float,
        _position: Position,
    ) -> None:
        self._preview_visible = True
        self._preview_popup._current_desktop_id = desktop_id

    def _hide_preview_popup(self) -> None:
        self._preview_visible = False

    def _mark_tooltip_updated(self, _item, _frame) -> None:
        self._tooltip_updated = True

    def _mark_tooltip_hidden(self) -> None:
        self._tooltip_hidden = True
