"""Deterministic dock interaction harness shared by behave scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

import docking.ui.autohide as autohide_mod
import docking.ui.dnd as dnd_mod
import docking.ui.dock_window as dock_window_mod
import docking.ui.hover as hover_mod
import docking.ui.preview as preview_mod
from docking.core.items import FOLDER_KIND, DockItem
from docking.core.position import Position
from docking.ui.autohide import AutoHideController, HideState
from docking.ui.geometry import Rect
from docking.ui.hover import HoverManager
from docking.ui.interaction import DockInteractionCoordinator


@dataclass
class _ScheduledCallback:
    source_id: int
    delay_ms: int
    due_ms: int
    callback: object
    args: tuple[object, ...]


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
        self._folder_menu.open_folder_stack_item_id.return_value = None
        self._folder_menu.close_folder_stack = MagicMock(
            side_effect=self._close_folder_stack
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
                config=SimpleNamespace(pos=Position.BOTTOM),
                _test_geometry_frame=self._folder_frame,
                update_input_region=MagicMock(),
                drawing_area=MagicMock(),
                hover=SimpleNamespace(update=MagicMock(), start_anim_pump=MagicMock()),
                _menu=self._folder_menu,
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
                theme=SimpleNamespace(item_padding=8, h_padding=10),
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
        self._build_hover_harness()

    def start(self) -> None:
        self._patchers = [
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
        for patcher in self._patchers:
            patcher.start()
        self._build_drag_handler()

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
        dock_window_mod.DockWindow._on_button_release(
            self._folder_stub,
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
        dock_window_mod.DockWindow._on_motion(self._folder_stub, MagicMock(), event)

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
        self._drag_handler._model.sync_pinned_to_config = MagicMock()
        self._drag_handler._model.notify = MagicMock()
        self._drag_handler._config.pinned = []
        self._drag_handler._config.save = MagicMock()
        self._drag_handler._launcher.resolve.return_value = SimpleNamespace(
            name="Firefox",
            icon_name="firefox",
            wm_class="firefox",
        )
        self._drag_handler._launcher.load_icon.return_value = object()
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
        theme = SimpleNamespace(item_padding=8, h_padding=10)
        launcher = MagicMock()
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
            close_open_folder_stack_for_item=MagicMock(),
            get_display=MagicMock(return_value=display),
            get_position=MagicMock(return_value=(0, 0)),
            get_size=MagicMock(return_value=(400, 60)),
        )
        self._drag_pointer = pointer
        self._drag_window = window
        self._drag_handler = dnd_mod.DnDHandler(
            drawing_area,
            window,
            model,
            config,
            renderer,
            theme,
            launcher,
            geometry_builder=SimpleNamespace(
                build_frame=lambda **_kwargs: self._dnd_frame
            ),
        )

    def _mark_drag_reorder(self, *_args, **_kwargs) -> None:
        self._drag_reorder_called = True

    def _mark_drag_removed(self, desktop_id: str) -> None:
        self._drag_removed_desktop_id = desktop_id

    def _open_folder_stack(self, *, item: DockItem, **_kwargs) -> None:
        self._folder_stack_open_for = item.desktop_id
        self._folder_menu.open_folder_stack_item_id.return_value = item.desktop_id

    def _close_folder_stack(self) -> None:
        self._folder_stack_open_for = None
        self._folder_menu.open_folder_stack_item_id.return_value = None

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
            h_padding=10,
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
