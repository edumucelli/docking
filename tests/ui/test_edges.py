"""High-fidelity outer-edge behavior regressions for DockWindow.

These tests drive the actual DockWindow motion/leave handlers with a real
geometry frame and a real HoverManager. The goal is to approximate the live
dock event flow closely enough that outer-edge hover/hide regressions can be
caught without manual testing. This module also keeps the lower-level sweep
checks for edge hover ownership so all outer-edge regressions live in one
place.
"""

from __future__ import annotations

from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import docking.ui.dock_window as dock_window_mod
import docking.ui.input_controller as input_controller_mod
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import (
    DockGeometryBuilder,
    DockGeometryFrame,
    build_geometry_frame,
)
from docking.ui.hover import HoverManager
from docking.ui.interaction import DockInteractionCoordinator


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        pos=Position.BOTTOM,
        icon_size=48,
        zoom_percent=1.5,
        zoom_enabled=True,
        additional_distance_from_edge=0,
    )


def _theme() -> SimpleNamespace:
    return SimpleNamespace(
        distance_from_edge=0,
        item_padding=8,
        horizontal_padding=12,
        top_padding=0,
        bottom_padding=4,
        shelf_height=21,
        stroke_width=1.0,
    )


def _items() -> list[DockItem]:
    return [
        DockItem(desktop_id="google-chrome.desktop", name="Google Chrome"),
        DockItem(desktop_id="firefox.desktop", name="Firefox"),
        DockItem(desktop_id="code.desktop", name="Code"),
        DockItem(desktop_id="terminator.desktop", name="Terminator"),
        DockItem(desktop_id="sublime_text.desktop", name="Sublime Text"),
        DockItem(desktop_id="applet://clock", name="Clock"),
        DockItem(desktop_id="applet://calendar", name="Calendar"),
    ]


def _frame(cursor_main: float) -> DockGeometryFrame:
    return build_geometry_frame(
        items=_items(),
        config=_config(),
        theme=_theme(),
        window_w=1920,
        window_h=122,
        cursor_main=cursor_main,
        autohide_state=None,
        zoom_progress=1.0,
        hide_offset=0.0,
    )


def _sweep_until_none(
    *,
    frame: DockGeometryFrame,
    start_x: int,
    stop_x: int,
    step: int,
    y: int,
) -> int:
    for x in range(start_x, stop_x, step):
        if frame.hover_item_at_point(x, y) is None:
            return x
    raise AssertionError("sweep never exited hover region")


class _Harness:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            pos=Position.BOTTOM,
            icon_size=48,
            zoom_percent=1.5,
            zoom_enabled=True,
            previews_enabled=False,
            stack_unfold="click",
            additional_distance_from_edge=0,
        )
        self.theme = SimpleNamespace(
            item_padding=8,
            horizontal_padding=12,
            top_padding=0,
            bottom_padding=4,
            shelf_height=21,
            stroke_width=1.0,
            distance_from_edge=0,
            urgent_glow_time_ms=500,
        )
        self.model = MagicMock()
        self.model.visible_items.return_value = [
            DockItem(desktop_id="google-chrome.desktop", name="Google Chrome"),
            DockItem(desktop_id="firefox.desktop", name="Firefox"),
            DockItem(desktop_id="code.desktop", name="Code"),
            DockItem(desktop_id="terminator.desktop", name="Terminator"),
            DockItem(desktop_id="sublime_text.desktop", name="Sublime Text"),
            DockItem(desktop_id="applet://clock", name="Clock"),
            DockItem(desktop_id="applet://calendar", name="Calendar"),
        ]
        self.cursor_x = -1.0
        self.cursor_y = -1.0
        self.dock_hovered = False
        self._cache = dock_window_mod._DockWindowCache.create()
        self._redraw_source_id = None
        self.preview = None
        self._interactions = MagicMock()
        self._click_x = 0.0
        self._click_y = 0.0
        self._click_button = 0
        self.tooltip = MagicMock()
        self.drawing_area = MagicMock()
        self.surface_service = MagicMock()
        self.autohide = SimpleNamespace(
            enabled=True,
            state=HideState.VISIBLE,
            zoom_progress=1.0,
            hide_offset=0.0,
            on_mouse_leave=MagicMock(),
            on_mouse_enter=MagicMock(),
        )
        self._gdk_window = MagicMock()
        self.zoom_animator = SimpleNamespace(
            progress=1.0, on_enter=lambda: None, on_leave=lambda: None
        )
        self.geometry = DockGeometryBuilder(self)
        self.hover = HoverManager(
            window=self,
            config=self.config,
            model=self.model,
            theme=self.theme,
            tooltip=self.tooltip,
            geometry_builder=self.geometry,
        )
        self.interaction = DockInteractionCoordinator(self)

    def get_size(self) -> tuple[int, int]:
        return (1920, 122)

    def get_window(self) -> MagicMock:
        return self._gdk_window

    def get_realized(self) -> bool:
        return True

    def get_position(self) -> tuple[int, int]:
        return (0, 958)


def _attach_runtime_methods(harness: _Harness) -> None:
    harness.current_interaction_frame = MethodType(
        dock_window_mod.DockWindow.current_interaction_frame, harness
    )
    harness.update_input_region = MethodType(
        dock_window_mod.DockWindow.update_input_region, harness
    )
    harness._geometry_signature = MethodType(
        dock_window_mod.DockWindow._geometry_signature, harness
    )
    harness._build_and_store_geometry_frame = MethodType(
        dock_window_mod.DockWindow._build_and_store_geometry_frame, harness
    )
    harness._current_or_build_geometry_frame = MethodType(
        dock_window_mod.DockWindow._current_or_build_geometry_frame, harness
    )
    harness._clear_scheduled_redraw = MethodType(
        dock_window_mod.DockWindow._clear_scheduled_redraw, harness
    )
    harness._flush_scheduled_redraw = MethodType(
        dock_window_mod.DockWindow._flush_scheduled_redraw, harness
    )
    harness._schedule_redraw = MethodType(
        dock_window_mod.DockWindow._schedule_redraw, harness
    )
    harness._invalidate_current_geometry_frame = MethodType(
        dock_window_mod.DockWindow._invalidate_current_geometry_frame, harness
    )


def _controller(harness: _Harness):
    return SimpleNamespace(
        _window=harness,
        _interactions=harness._interactions,
        _click_x=getattr(harness, "_click_x", -1.0),
        _click_y=getattr(harness, "_click_y", -1.0),
        _click_button=getattr(harness, "_click_button", 0),
        dnd=SimpleNamespace(drag_index=-1, drop_insert_index=-1, drop_target_id=""),
    )


def _motion_event(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y)


def _leave_event(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(
        x=x,
        y=y,
        detail=dock_window_mod.Gdk.NotifyType.ANCESTOR,
        mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
    )


def hover_first_item(harness: _Harness, widget: MagicMock) -> tuple[float, float]:
    frame = harness.geometry.build_frame(main_cursor=330.0)
    first = frame.item_geometries[0]
    x = float(first.draw_rect.x + first.draw_rect.w // 2)
    y = float(first.draw_rect.y + first.draw_rect.h - 1)
    input_controller_mod.DockInputController._on_motion(
        _controller(harness), widget, _motion_event(x=x, y=y)
    )
    assert harness.hover.hovered_item is first.item
    return x, y


def hover_last_item(harness: _Harness, widget: MagicMock) -> tuple[float, float]:
    frame = harness.geometry.build_frame(main_cursor=1590.0)
    last = frame.item_geometries[-1]
    x = float(last.draw_rect.x + last.draw_rect.w // 2)
    y = float(last.draw_rect.y + last.draw_rect.h - 1)
    input_controller_mod.DockInputController._on_motion(
        _controller(harness), widget, _motion_event(x=x, y=y)
    )
    assert harness.hover.hovered_item is last.item
    return x, y


class TestDockOuterEdgeBehavior:
    def test_first_item_loseshover_exactly_after_left_background_edge(self):
        frame = _frame(cursor_main=330.0)
        first = frame.item_geometries[0]
        y = first.draw_rect.y + first.draw_rect.h - 1

        exit_x = _sweep_until_none(
            frame=frame,
            start_x=int(first.anchor_x),
            stop_x=frame.background_rect.x - 8,
            step=-1,
            y=y,
        )

        assert exit_x == frame.background_rect.x - 1

    def test_last_item_loseshover_exactly_at_right_background_edge(self):
        frame = _frame(cursor_main=1590.0)
        last = frame.item_geometries[-1]
        y = last.draw_rect.y + last.draw_rect.h - 1

        exit_x = _sweep_until_none(
            frame=frame,
            start_x=int(last.anchor_x),
            stop_x=frame.background_rect.x + frame.background_rect.w + 8,
            step=1,
            y=y,
        )

        assert exit_x == frame.background_rect.x + frame.background_rect.w

    def test_cursor_band_has_no_main_axis_gaps_between_background_edges(self):
        frame = _frame(cursor_main=960.0)
        y = frame.static_dock_rect.y + frame.static_dock_rect.h - 1
        first_gap = None

        for x in range(
            frame.background_rect.x,
            frame.background_rect.x + frame.background_rect.w,
        ):
            if frame.hover_item_at_point(x, y) is None:
                first_gap = x
                break

        assert first_gap is None

    def test_motion_past_left_background_edge_releases_hover_before_gtk_leave(self):
        harness = _Harness()
        _attach_runtime_methods(harness)
        widget = MagicMock()
        hover_first_item(harness, widget)

        frame = harness.current_interaction_frame()
        assert frame is not None
        exit_x = frame.background_rect.x - 1
        y = (
            frame.item_geometries[0].draw_rect.y
            + frame.item_geometries[0].draw_rect.h
            - 1
        )

        input_controller_mod.DockInputController._on_motion(
            _controller(harness),
            widget,
            _motion_event(x=float(exit_x), y=float(y)),
        )

        harness.autohide.on_mouse_leave.assert_called_once()
        assert harness.dock_hovered is False

    def test_gtk_leave_after_left_geometry_exit_is_ignored(self):
        harness = _Harness()
        _attach_runtime_methods(harness)
        widget = MagicMock()
        hover_first_item(harness, widget)

        frame = harness.current_interaction_frame()
        assert frame is not None
        exit_x = frame.background_rect.x - 1
        y = (
            frame.item_geometries[0].draw_rect.y
            + frame.item_geometries[0].draw_rect.h
            - 1
        )

        input_controller_mod.DockInputController._on_motion(
            _controller(harness),
            widget,
            _motion_event(x=float(exit_x), y=float(y)),
        )

        assert harness.autohide.on_mouse_leave.call_count == 1

        handled = input_controller_mod.DockInputController._on_leave(
            _controller(harness),
            widget,
            _leave_event(x=float(exit_x), y=float(y)),
        )

        assert handled is False
        assert harness.autohide.on_mouse_leave.call_count == 1

    def test_gtk_leave_after_right_geometry_exit_is_ignored(self):
        harness = _Harness()
        _attach_runtime_methods(harness)
        widget = MagicMock()
        hover_last_item(harness, widget)

        frame = harness.current_interaction_frame()
        assert frame is not None
        exit_x = frame.background_rect.x + frame.background_rect.w
        y = (
            frame.item_geometries[-1].draw_rect.y
            + frame.item_geometries[-1].draw_rect.h
            - 1
        )

        input_controller_mod.DockInputController._on_motion(
            _controller(harness),
            widget,
            _motion_event(x=float(exit_x), y=float(y)),
        )

        assert harness.autohide.on_mouse_leave.call_count == 1

        handled = input_controller_mod.DockInputController._on_leave(
            _controller(harness),
            widget,
            _leave_event(x=float(exit_x), y=float(y)),
        )

        assert handled is False
        assert harness.autohide.on_mouse_leave.call_count == 1
