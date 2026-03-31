"""Deterministic dock interaction harness shared by behave scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docking.ui.autohide as autohide_mod
import docking.ui.dnd as dnd_mod
import docking.ui.dock_window as dock_window_mod
from docking.core.items import FOLDER_KIND, DockItem
from docking.core.position import Position
from docking.ui.autohide import AutoHideController, HideState
from docking.ui.geometry import Rect
from docking.ui.interaction import DockInteractionCoordinator


@dataclass
class _ScheduledCallback:
    source_id: int
    callback: object
    args: tuple[object, ...]


class _TimerScheduler:
    """Simple deterministic GLib timer scheduler for autohide scenarios."""

    def __init__(self) -> None:
        self._next_source_id = 1
        self._callbacks: dict[int, _ScheduledCallback] = {}

    def timeout_add(self, _delay: int, callback, *args) -> int:
        source_id = self._next_source_id
        self._next_source_id += 1
        self._callbacks[source_id] = _ScheduledCallback(
            source_id=source_id,
            callback=callback,
            args=args,
        )
        return source_id

    def source_remove(self, source_id: int) -> bool:
        self._callbacks.pop(source_id, None)
        return True

    def source_exists(self, source_id: int) -> bool:
        return source_id in self._callbacks

    def advance(self) -> None:
        while self._callbacks:
            source_id = min(self._callbacks)
            scheduled = self._callbacks[source_id]
            keep = bool(scheduled.callback(*scheduled.args))
            if keep:
                continue
            self._callbacks.pop(source_id, None)


def _dnd_frame(*, item_index: int = -1, insert_index: int = 0, count: int = 1):
    item_geometries = [
        SimpleNamespace(
            draw_rect=SimpleNamespace(x=index * 70, y=0, w=48, h=48),
        )
        for index in range(count)
    ]
    return SimpleNamespace(
        item_geometries=item_geometries,
        item_index_at_point=MagicMock(return_value=item_index),
        item_at_point=MagicMock(return_value=None),
        insertion_index_for_main=MagicMock(return_value=insert_index),
    )


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
            geometry_for_item=MagicMock(
                return_value=SimpleNamespace(
                    draw_rect=SimpleNamespace(x=4, y=5, w=48, h=48)
                )
            ),
        )
        self._folder_stub = SimpleNamespace(
            cursor_x=12.0,
            cursor_y=6.0,
            dock_hovered=True,
            config=SimpleNamespace(pos=Position.BOTTOM),
            _test_geometry_frame=self._folder_frame,
            update_input_region=MagicMock(),
            hover=SimpleNamespace(update=MagicMock(), start_anim_pump=MagicMock()),
            _menu=self._folder_menu,
            autohide=SimpleNamespace(
                enabled=False,
                set_disabled=MagicMock(),
                set_hovered=MagicMock(),
                on_mouse_enter=MagicMock(),
                on_mouse_leave=MagicMock(),
            ),
            zoom_animator=MagicMock(),
            geometry=SimpleNamespace(
                build_frame=lambda **_kwargs: self._folder_frame,
            ),
            interaction=None,
            _click_x=12.0,
            _click_y=6.0,
            hit_test=MagicMock(return_value=self._folder_item),
            model=MagicMock(),
            theme=SimpleNamespace(item_padding=8, h_padding=10),
            window_tracker=MagicMock(),
            tooltip=MagicMock(),
            preview=None,
            current_geometry_frame=self._folder_frame,
            applied_input_frame=self._folder_frame,
            local_cursor_main=MagicMock(return_value=-1e6),
            get_position=MagicMock(return_value=(100, 200)),
        )
        self._folder_stub.interaction = DockInteractionCoordinator(self._folder_stub)

        self._dnd_frame = _dnd_frame()
        self._drag_reorder_called = False
        self._external_pinned_targets: list[str] = []

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

    def advance_time(self, _milliseconds: int) -> None:
        self._scheduler.advance()

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
        target = self._folder_item if desktop_id == self._folder_item.desktop_id else self._other_item
        self._folder_frame.item_at_point.return_value = target
        event = SimpleNamespace(x=12.0, y=9.0)
        dock_window_mod.DockWindow._on_motion(self._folder_stub, MagicMock(), event)

    @property
    def folder_stack_open_for(self) -> str | None:
        return self._folder_stack_open_for

    def begin_drag(self, desktop_id: str) -> None:
        if desktop_id != "a.desktop":
            raise AssertionError(f"Unsupported drag source {desktop_id}")
        self._drag_handler._model.visible_items.return_value = [
            DockItem(desktop_id="a.desktop", name="A", icon=MagicMock()),
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

    @property
    def drag_reordered(self) -> bool:
        return self._drag_reorder_called

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

    def _build_drag_handler(self) -> None:
        drawing_area = MagicMock()
        config = SimpleNamespace(
            lock_icons=False,
            pos=Position.BOTTOM,
            icon_size=48,
            zoom_percent=2.0,
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
            get_display=MagicMock(return_value=display),
            get_position=MagicMock(return_value=(0, 0)),
            get_size=MagicMock(return_value=(400, 60)),
        )
        self._drag_handler = dnd_mod.DnDHandler(
            drawing_area,
            window,
            model,
            config,
            renderer,
            theme,
            launcher,
            geometry_builder=SimpleNamespace(build_frame=lambda **_kwargs: self._dnd_frame),
        )

    def _mark_drag_reorder(self, *_args, **_kwargs) -> None:
        self._drag_reorder_called = True

    def _open_folder_stack(self, *, item: DockItem, **_kwargs) -> None:
        self._folder_stack_open_for = item.desktop_id
        self._folder_menu.open_folder_stack_item_id.return_value = item.desktop_id

    def _close_folder_stack(self) -> None:
        self._folder_stack_open_for = None
        self._folder_menu.open_folder_stack_item_id.return_value = None
