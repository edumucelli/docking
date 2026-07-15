"""High-level dock pointer and drag/drop interaction scenarios.

These tests are intentionally broader than the unit-style integration tests.
They use one reusable dock harness and drive the real DockWindow motion/leave
handlers plus the real DnDHandler so we can validate user-shaped paths:

- moving into the dock and across icons,
- leaving and re-entering quickly,
- preview-visible leave policy,
- external drag/drop on the dock,
- external drag leave without drop,
- internal drag reorder while the dock stays active.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MethodType, SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, call

import pytest

import docking.ui.autohide as autohide_mod
import docking.ui.dnd as dnd_mod
import docking.ui.dock_window as dock_window_mod
import docking.ui.input_controller as input_controller_mod
import docking.ui.menu as menu_mod
import docking.ui.placement as placement_mod
from docking.core.config import PinnedEntry
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.autohide import AutoHideController, HideState
from docking.ui.dnd import DnDHandler
from docking.ui.folder.stack import FolderStackController
from docking.ui.geometry import DockGeometryBuilder
from docking.ui.hover import HoverManager
from docking.ui.interaction import DockInteractionCoordinator
from docking.ui.menu import MenuHandler
from docking.ui.runtime import DockRuntime


class _Seat:
    def __init__(self, harness: _ScenarioHarness) -> None:
        self._harness = harness

    def get_pointer(self):
        return self

    def get_position(self):
        return (None, *self._harness.screen_pointer)


class _Display:
    def __init__(self, harness: _ScenarioHarness) -> None:
        self._harness = harness

    def get_default_seat(self):
        return _Seat(self._harness)


class _ScenarioHarness:
    def __init__(self, *, pos: Position = Position.BOTTOM) -> None:
        self.config = SimpleNamespace(
            pos=pos,
            icon_size=48,
            zoom_percent=1.5,
            scaled_icon_size=72,
            zoom_enabled=True,
            previews_enabled=True,
            folder_stack_unfold="click",
            lock_icons=False,
            pinned=[],
            save=MagicMock(),
            hide_delay_ms=0,
            unhide_delay_ms=0,
            hide_time_ms=250,
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
        self.items = [
            DockItem(desktop_id="google-chrome.desktop", name="Google Chrome"),
            DockItem(desktop_id="firefox.desktop", name="Firefox"),
            DockItem(desktop_id="code.desktop", name="Code"),
            DockItem(desktop_id="terminator.desktop", name="Terminator"),
            DockItem(desktop_id="sublime_text.desktop", name="Sublime Text"),
            DockItem(desktop_id="applet://clock", name="Clock"),
            DockItem(desktop_id="applet://calendar", name="Calendar"),
        ]
        self.model = MagicMock()
        self.model.visible_items.return_value = self.items
        self.model.pinned_items = []
        self.model.find_by_desktop_id.return_value = None

        def insert_pinned_item(*, item: DockItem, index: int) -> bool:
            item.is_pinned = True
            self.model.pinned_items.insert(index, item)
            self.config.pinned.insert(
                index,
                PinnedEntry(kind=item.kind, target=item.target),
            )
            self.config.save()
            self.model.notify()
            return True

        self.model.insert_pinned_item.side_effect = insert_pinned_item
        self.cursor_x = -1.0
        self.cursor_y = -1.0
        self.screen_pointer = (0, 0)
        self.dock_hovered = False
        self._cache = dock_window_mod._DockWindowCache.create()
        self._redraw_source_id = None
        self._interactions = MagicMock()
        self._click_x = 0.0
        self._click_y = 0.0
        self._click_button = 0
        self.preview = None
        self.tooltip = MagicMock()
        self.drawing_area = MagicMock()
        self.surface_service = MagicMock()
        self.renderer = SimpleNamespace(slide_offsets={}, prev_positions={})
        self.autohide = SimpleNamespace(
            enabled=True,
            state=HideState.VISIBLE,
            zoom_progress=1.0,
            hide_offset=0.0,
            on_mouse_leave=MagicMock(),
            on_mouse_enter=MagicMock(),
            set_disabled=MagicMock(),
            set_hovered=MagicMock(),
        )
        self._gdk_window = MagicMock()
        self.zoom_animator = SimpleNamespace(
            progress=1.0, on_enter=lambda: None, on_leave=lambda: None
        )
        self.geometry = DockGeometryBuilder(cast(Any, self))
        self.hover = HoverManager(
            window=cast(Any, self),
            config=cast(Any, self.config),
            model=self.model,
            theme=cast(Any, self.theme),
            tooltip=self.tooltip,
            geometry_builder=self.geometry,
        )
        self.interaction = DockInteractionCoordinator(cast(Any, self))
        self.launcher = MagicMock()
        self.window_tracker = MagicMock()
        self.dnd = DnDHandler(
            drawing_area=self.drawing_area,
            window=cast(Any, self),
            model=self.model,
            config=cast(Any, self.config),
            renderer=cast(Any, self.renderer),
            theme=cast(Any, self.theme),
            launcher=self.launcher,
            geometry_builder=self.geometry,
            folder_stack=self._interactions,
        )
        self.update_input_region = MethodType(
            dock_window_mod.DockWindow.update_input_region, self
        )
        self.current_interaction_frame = MethodType(
            dock_window_mod.DockWindow.current_interaction_frame, self
        )
        self.is_pointer_inside_dock = MethodType(
            dock_window_mod.DockWindow.is_pointer_inside_dock, self
        )
        self._geometry_signature = MethodType(
            dock_window_mod.DockWindow._geometry_signature, self
        )
        self._build_and_store_geometry_frame = MethodType(
            dock_window_mod.DockWindow._build_and_store_geometry_frame, self
        )
        self._current_or_build_geometry_frame = MethodType(
            dock_window_mod.DockWindow._current_or_build_geometry_frame, self
        )
        self._clear_scheduled_redraw = MethodType(
            dock_window_mod.DockWindow._clear_scheduled_redraw, self
        )
        self._flush_scheduled_redraw = MethodType(
            dock_window_mod.DockWindow._flush_scheduled_redraw, self
        )
        self._schedule_redraw = MethodType(
            dock_window_mod.DockWindow._schedule_redraw, self
        )
        self._invalidate_current_geometry_frame = MethodType(
            dock_window_mod.DockWindow._invalidate_current_geometry_frame, self
        )

    def get_size(self) -> tuple[int, int]:
        if self.config.pos in (Position.LEFT, Position.RIGHT):
            return (122, 1920)
        return (1920, 122)

    def get_window(self):
        return self._gdk_window

    def get_realized(self) -> bool:
        return True

    def get_display(self):
        return _Display(self)

    def get_position(self) -> tuple[int, int]:
        return (0, 958)

    def queue_redraw(self) -> None:
        self.drawing_area.queue_draw()

    def local_to_screen(self, x: float, y: float) -> None:
        win_x, win_y = self.get_position()
        self.cursor_x = x
        self.cursor_y = y
        self.screen_pointer = (int(win_x + x), int(win_y + y))
        frame = self.geometry.build_frame(cursor_x=x, cursor_y=y)
        self._cache.store_geometry_frame(
            frame=frame,
            signature=self._geometry_signature(),
        )
        self._cache.applied_input_frame = frame

    def rest_frame(self):
        return self.geometry.build_frame(main_cursor=-1e6)

    def item_center(self, index: int) -> tuple[float, float]:
        frame = self.rest_frame()
        item_geo = frame.item_geometries[index]
        return (
            float(item_geo.hover_rect.x + item_geo.hover_rect.w // 2),
            float(item_geo.hover_rect.y + item_geo.hover_rect.h // 2),
        )

    def point_above_item(self, index: int, *, delta: int = 8) -> tuple[float, float]:
        x, _ = self.item_center(index)
        frame = self.geometry.build_frame(main_cursor=x)
        return (x, float(frame.cursor_rect.y - delta))

    def point_outside_dock(self, index: int, *, delta: int = 8) -> tuple[float, float]:
        x, y = self.item_center(index)
        main_cursor = x if self.config.pos in (Position.BOTTOM, Position.TOP) else y
        frame = self.geometry.build_frame(main_cursor=main_cursor)
        if self.config.pos == Position.BOTTOM:
            return (x, float(frame.cursor_rect.y - delta))
        if self.config.pos == Position.TOP:
            return (x, float(frame.cursor_rect.y + frame.cursor_rect.h + delta))
        if self.config.pos == Position.LEFT:
            return (float(frame.cursor_rect.x + frame.cursor_rect.w + delta), y)
        return (float(frame.cursor_rect.x - delta), y)

    def move_to(self, x: float, y: float):
        self.local_to_screen(x, y)
        return input_controller_mod.DockInputController._on_motion(
            _controller(self), self.drawing_area, SimpleNamespace(x=x, y=y)
        )

    def enter_at(self, x: float, y: float):
        self.local_to_screen(x, y)
        return input_controller_mod.DockInputController._on_enter(
            _controller(self), self.drawing_area, SimpleNamespace(x=x, y=y)
        )

    def leave_at(self, x: float, y: float):
        self.local_to_screen(x, y)
        return input_controller_mod.DockInputController._on_leave(
            _controller(self),
            self.drawing_area,
            SimpleNamespace(
                x=x,
                y=y,
                detail=dock_window_mod.Gdk.NotifyType.ANCESTOR,
                mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            ),
        )


def _controller(harness: _ScenarioHarness):
    return SimpleNamespace(
        _window=harness,
        _interactions=harness._interactions,
        _click_x=getattr(harness, "_click_x", -1.0),
        _click_y=getattr(harness, "_click_y", -1.0),
        _click_button=getattr(harness, "_click_button", 0),
        dnd=harness.dnd,
    )


class TestPointerScenarios:
    @pytest.mark.parametrize(
        ("pos", "index"),
        [
            (Position.BOTTOM, 3),
            (Position.TOP, 3),
            (Position.LEFT, 1),
            (Position.RIGHT, 5),
        ],
    )
    def test_pointer_exit_releases_hover_for_all_orientations(self, pos, index):
        harness = _ScenarioHarness(pos=pos)
        x, y = harness.item_center(index)
        out_x, out_y = harness.point_outside_dock(index)

        harness.enter_at(x, y)
        harness.move_to(x, y)
        assert harness.dock_hovered is True
        assert harness.hover.hovered_item is harness.items[index]

        harness.move_to(out_x, out_y)

        assert harness.dock_hovered is False
        harness.autohide.on_mouse_leave.assert_called_once()

    def test_pointer_moves_across_icons_then_leaves_top(self):
        harness = _ScenarioHarness()
        x0, y0 = harness.item_center(0)
        x3, y3 = harness.item_center(3)
        out_x, out_y = harness.point_above_item(3)

        harness.enter_at(x0, y0)
        harness.move_to(x0, y0)
        assert harness.hover.hovered_item is harness.items[0]
        assert harness.dock_hovered is True

        harness.move_to(x3, y3)
        assert harness.hover.hovered_item is harness.items[3]
        harness.autohide.on_mouse_leave.assert_not_called()

        harness.move_to(out_x, out_y)
        assert harness.dock_hovered is False
        harness.autohide.on_mouse_leave.assert_called_once()

    def test_fast_vertical_u_turn_leaves_then_reenters_cleanly(self):
        harness = _ScenarioHarness()
        x, y = harness.item_center(2)
        out_x, out_y = harness.point_above_item(2)

        harness.enter_at(x, y)
        harness.move_to(x, y)
        harness.move_to(out_x, out_y)
        harness.move_to(x, y)

        assert harness.dock_hovered is True
        assert harness.hover.hovered_item is harness.items[2]
        assert harness.autohide.on_mouse_leave.call_count == 1
        assert harness.autohide.on_mouse_enter.call_count == 2

    def test_preview_visible_leave_schedules_preview_hide_without_autohide_leave(self):
        harness = _ScenarioHarness()
        harness.preview = MagicMock()
        harness.preview.get_visible.return_value = True

        x, y = harness.item_center(1)
        out_x, out_y = harness.point_above_item(1)

        harness.enter_at(x, y)
        harness.move_to(x, y)
        harness.move_to(out_x, out_y)

        harness.preview.schedule_hide.assert_called_once()
        harness.autohide.on_mouse_leave.assert_not_called()
        assert harness.hover.hovered_item is harness.items[1]

    def test_tooltip_updates_across_adjacent_icons_and_hides_on_leave(self):
        harness = _ScenarioHarness()
        x0, y0 = harness.item_center(0)
        x1, y1 = harness.item_center(1)
        out_x, out_y = harness.point_outside_dock(1)

        harness.enter_at(x0, y0)
        harness.move_to(x0, y0)
        harness.move_to(x1, y1)
        harness.move_to(out_x, out_y)

        updated_items = [call.args[0] for call in harness.tooltip.update.call_args_list]
        assert harness.items[0] in updated_items
        assert harness.items[1] in updated_items
        harness.tooltip.hide.assert_called_once()


class _FakePopupMenu:
    def __init__(self) -> None:
        self._signals: dict[
            str, list[tuple[Callable[..., object], tuple[object, ...]]]
        ] = {}
        self.shown = False
        self.popup_event = None

    def get_children(self):
        return []

    def connect(
        self, signal: str, callback: Callable[..., object], *args: object
    ) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def emit(self, signal: str) -> None:
        for callback, args in self._signals.get(signal, []):
            callback(self, *args)

    def show_all(self) -> None:
        self.shown = True

    def popup_at_pointer(self, event) -> None:
        self.popup_event = event


class TestMenuLifecycleScenarios:
    def test_background_menu_open_and_hide_follow_real_popup_lifecycle(
        self, monkeypatch
    ):
        harness = _ScenarioHarness()
        harness.autohide = SimpleNamespace(
            enabled=True,
            state=HideState.VISIBLE,
            zoom_progress=1.0,
            hide_offset=0.0,
            on_mouse_leave=MagicMock(),
            on_mouse_enter=MagicMock(),
            set_disabled=MagicMock(),
            set_hovered=MagicMock(),
        )

        runtime = DockRuntime(
            cast(Any, harness),
            update_checker=MagicMock(),
        )
        folder_stack = FolderStackController(
            config=cast(Any, harness.config),
            runtime=runtime,
            launcher=harness.launcher,
        )
        handler = MenuHandler(
            about=MagicMock(),
            settings=MagicMock(),
            runtime=runtime,
            model=harness.model,
            config=cast(Any, harness.config),
            window_tracker=harness.window_tracker,
            preview_service=MagicMock(),
            folder_stack=folder_stack,
            diagnostics=MagicMock(),
            launcher=harness.launcher,
            dock_window=cast(Any, harness),
        )
        created: list[_FakePopupMenu] = []

        monkeypatch.setattr(
            menu_mod.Gtk,
            "Menu",
            lambda: created.append(_FakePopupMenu()) or created[-1],
        )
        monkeypatch.setattr(
            handler, "_build_dock_menu", lambda menu, insert_index: None
        )

        event = SimpleNamespace(x=10.0, y=5.0)
        frame = SimpleNamespace(
            item_at_point=lambda *_args: None,
            insertion_index_for_main=lambda *_args, **_kwargs: 0,
        )
        handler.show(event=event, cursor_main=10.0, frame=frame)
        menu = created[0]

        harness.autohide.set_disabled.assert_called_once_with(True, reason="menu-open")
        assert menu.shown is True
        assert menu.popup_event is event

        menu.emit("hide")

        assert harness.autohide.set_disabled.call_count == 2


class TestAutohideAnimationScenarios:
    def test_autohide_progresses_visible_to_hidden_and_back(self, monkeypatch):
        window = SimpleNamespace(queue_redraw=MagicMock())
        config = SimpleNamespace(
            hide_mode="autohide",
            hide_delay_ms=0,
            unhide_delay_ms=0,
            hide_time_ms=64,
        )
        ctrl = AutoHideController(cast(Any, window), cast(Any, config))
        scheduled: list[Callable[[], object]] = []
        monkeypatch.setattr(
            autohide_mod.GLib,
            "timeout_add",
            lambda _delay, cb: scheduled.append(cb) or 1,
        )
        monkeypatch.setattr(autohide_mod, "_clear_source", lambda source_id: 0)

        ctrl.on_mouse_enter()
        ctrl.on_mouse_leave()
        assert scheduled
        scheduled.pop(0)()
        assert ctrl.state == HideState.HIDING

        while ctrl.state != HideState.HIDDEN:
            ctrl._animation_tick()

        assert ctrl.hide_offset == 1.0
        assert ctrl.zoom_progress == 0.0

        ctrl.on_mouse_enter()
        assert ctrl.state == HideState.SHOWING

        while ctrl.state != HideState.VISIBLE:
            ctrl._animation_tick()

        assert ctrl.hide_offset == 0.0
        assert ctrl.zoom_progress == 1.0


class TestPlacementScenarios:
    def test_active_display_repositions_when_pointer_moves_between_monitors(
        self, monkeypatch
    ):
        primary = SimpleNamespace(
            name="primary",
            get_geometry=lambda: SimpleNamespace(x=0, y=0, width=1000, height=1080),
        )
        secondary = SimpleNamespace(
            name="secondary",
            get_geometry=lambda: SimpleNamespace(x=1000, y=0, width=1000, height=1080),
        )
        pointer = SimpleNamespace(get_position=lambda: (None, 200, 50))
        seat = SimpleNamespace(get_pointer=lambda: pointer)
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_monitor=lambda idx: primary if idx == 0 else secondary,
            get_default_seat=lambda: seat,
            get_monitor_at_point=lambda x, y: primary if x < 1000 else secondary,
        )
        window = SimpleNamespace(
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=True,
                zoom_percent=1.2,
                pos=Position.BOTTOM,
                folder_stack_unfold="click",
                active_display=True,
                hide_mode="none",
                monitor_index=-1,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.5,
                distance_from_edge=0,
            ),
            get_display=lambda: display,
            get_screen=MagicMock(),
            get_window=MagicMock(),
            get_realized=MagicMock(return_value=True),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
            drawing_area=SimpleNamespace(queue_draw=MagicMock()),
            update_input_region=MagicMock(),
            surface_service=MagicMock(),
            additional_distance_from_edge=0,
        )
        controller = placement_mod.DockPlacementController(
            cast(Any, window),
            surface_service=window.surface_service,
        )
        reposition = MagicMock()
        monkeypatch.setattr(controller, "reposition", reposition)

        assert controller._poll_active_display() is True
        assert controller._active_monitor is primary
        reposition.assert_called_once()

        controller._poll_active_display()
        reposition.assert_called_once()

        pointer.get_position = lambda: (None, 1600, 50)
        controller._poll_active_display()
        assert controller._active_monitor is secondary
        assert reposition.call_count == 2


class TestDragDropScenarios:
    def test_external_drop_inside_keeps_dock_visible(self, monkeypatch):
        harness = _ScenarioHarness()
        x, y = harness.item_center(2)
        harness.local_to_screen(x, y)
        selection = MagicMock()
        selection.get_uris.return_value = [
            "file:///usr/share/applications/firefox.desktop"
        ]
        resolved = SimpleNamespace(name="Firefox", icon_name="firefox", wm_class="ff")
        harness.launcher.resolve.return_value = resolved
        harness.launcher.load_icon.return_value = object()
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_args, **_kwargs: None)

        harness.dnd._on_drag_motion(
            harness.drawing_area, MagicMock(), int(x), int(y), 1
        )
        harness.dnd._on_drag_leave(harness.drawing_area, MagicMock(), 1)
        harness.dnd._drop_committed = True
        harness.dnd._on_drag_data_received(
            harness.drawing_area,
            MagicMock(),
            int(x),
            int(y),
            selection,
            1,
            77,
        )

        assert harness.model.pinned_items
        assert any(
            c == call(False, reason="drag-data-received-inside")
            for c in harness.autohide.set_disabled.call_args_list
        )
        assert any(c == call(True) for c in harness.autohide.set_hovered.call_args_list)
        harness.autohide.on_mouse_leave.assert_not_called()
        finish.assert_called_once()

    def test_external_drag_leave_without_drop_releases_after_timeout(self, monkeypatch):
        harness = _ScenarioHarness()
        x, y = harness.item_center(4)
        out_x, out_y = harness.point_above_item(4, delta=20)
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_args, **_kwargs: None)

        harness.local_to_screen(x, y)
        harness.dnd._on_drag_motion(
            harness.drawing_area, MagicMock(), int(x), int(y), 1
        )
        harness.dnd._on_drag_leave(harness.drawing_area, MagicMock(), 1)

        harness.local_to_screen(out_x, out_y)
        assert harness.dnd._deferred_clear_drop_gap(harness.drawing_area) is False

        assert harness.dnd.drop_insert_index == -1
        assert any(
            c == call(False, reason="drag-leave-outside")
            for c in harness.autohide.set_disabled.call_args_list
        )
        harness.autohide.on_mouse_leave.assert_called_once()

    def test_internal_drag_reorder_keeps_dock_visible_while_dragging(self, monkeypatch):
        harness = _ScenarioHarness()
        x0, y0 = harness.item_center(0)
        x3, y3 = harness.item_center(3)
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_args, **_kwargs: None)

        harness.local_to_screen(x0, y0)
        harness.cursor_x = x0
        harness.cursor_y = y0
        harness.dnd._on_drag_begin(harness.drawing_area, MagicMock())
        handled = harness.dnd._on_drag_motion(
            harness.drawing_area, MagicMock(), int(x3), int(y3), 1
        )

        assert handled is True
        harness.model.reorder_visible.assert_called_once()
        assert any(
            c == call(True, reason="drag-begin")
            for c in harness.autohide.set_disabled.call_args_list
        )
        assert any(
            c == call(True, reason="drag-motion")
            for c in harness.autohide.set_disabled.call_args_list
        )
        harness.autohide.on_mouse_enter.assert_called()
