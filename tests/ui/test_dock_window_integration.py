"""Integration-style tests for DockWindow event handlers."""

from __future__ import annotations

from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import docking.ui.dock_window as dock_window_mod
import docking.ui.renderer as renderer_mod
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import Rect, build_geometry_frame
from docking.ui.interaction import DockInteractionCoordinator


def _autohide(*, enabled: bool = False, state: HideState = HideState.VISIBLE):
    return SimpleNamespace(
        enabled=enabled,
        state=state,
        hide_offset=0.0,
        zoom_progress=1.0 if enabled else 0.0,
        on_mouse_leave=MagicMock(),
        on_mouse_enter=MagicMock(),
        reconcile=MagicMock(),
        set_hovered=MagicMock(),
        set_disabled=MagicMock(),
        reset=MagicMock(),
    )


def _window_cache(
    *,
    current_geometry_frame=None,
    current_geometry_frame_signature=None,
    applied_input_frame=None,
    last_blur_region=None,
):
    cache = dock_window_mod._DockWindowCache.create()
    if current_geometry_frame is not None:
        cache.geometry_frame = dock_window_mod._GeometryFrameCacheEntry(
            frame=current_geometry_frame,
            signature=current_geometry_frame_signature,
        )
    cache.applied_input_frame = applied_input_frame
    cache.last_blur_region = last_blur_region
    return cache


def _bind_geometry_signature(stub):
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


def _make_stub(item: DockItem | None = None):
    item = item or DockItem(desktop_id="firefox.desktop")
    frame = SimpleNamespace(
        cursor_rect=Rect(0, 0, 100, 100),
        item_at_point=MagicMock(return_value=item),
        geometry_for_item=MagicMock(return_value=None),
    )
    stub = SimpleNamespace()
    stub.config = SimpleNamespace(
        pos=Position.BOTTOM,
        left_click_action="toggle",
        middle_click_action="new-window",
        icon_size=48,
    )
    stub.model = MagicMock()
    stub.model.visible_items.return_value = [item]
    stub.model.get_applet = MagicMock()
    stub.theme = SimpleNamespace(item_padding=8, h_padding=10, urgent_glow_time_ms=500)
    stub.window_tracker = MagicMock()
    stub._menu = MagicMock()
    stub._menu.open_folder_stack_item_id.return_value = None
    stub._menu.close_folder_stack = MagicMock()
    stub.tooltip = MagicMock()
    stub.hover = MagicMock()
    stub.hover.hovered_item = item
    stub.hover.cancel = MagicMock()
    stub.preview = None
    stub._menu_popup_visible = False
    stub.autohide = _autohide(enabled=False)
    stub.cursor_x = 12.0
    stub.cursor_y = 6.0
    stub._click_x = 12.0
    stub._click_y = 6.0
    stub.local_cursor_main = MagicMock(return_value=-1e6)
    stub.hit_test = MagicMock(return_value=item)
    stub.update_input_region = MagicMock()
    stub.drawing_area = MagicMock()
    stub.get_position = MagicMock(return_value=(100, 200))
    stub.get_size = MagicMock(return_value=(1920, 122))
    stub._test_geometry_frame = frame
    stub._cache = _window_cache(
        current_geometry_frame=frame,
        applied_input_frame=frame,
    )
    stub._redraw_source_id = None
    stub.geometry = SimpleNamespace(build_frame=lambda **_kwargs: frame)
    stub.dock_hovered = True
    stub.zoom_animator = SimpleNamespace(progress=1.0)
    stub.interaction = MagicMock()
    stub.interaction.on_effective_enter = MagicMock()
    stub.interaction.on_effective_leave = MagicMock()
    _bind_geometry_signature(stub)
    return stub, item


class TestButtonReleaseFlow:
    def test_right_click_opens_context_menu(self):
        # Given
        stub, _item = _make_stub()
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_RIGHT, state=0
        )

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        stub._menu.show.assert_called_once_with(event, 12.0)

    def test_left_click_on_applet_updates_tooltip_immediately(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="applet://quote")
        stub, _ = _make_stub(item=item)
        applet = MagicMock()
        stub.model.get_applet.return_value = applet
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(
            dock_window_mod,
            "is_applet",
            lambda desktop_id: desktop_id.startswith("applet://"),
        )
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 999)

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        applet.on_clicked.assert_called_once()
        stub.tooltip.update.assert_called_once_with(item, stub._test_geometry_frame)
        stub.hover.start_anim_pump.assert_called_once_with(350)

    def test_left_click_running_app_toggles_focus(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 1010)

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        stub.window_tracker.toggle_focus.assert_called_once_with("firefox.desktop")
        stub.hover.start_anim_pump.assert_called_once_with(350)
        assert item.last_clicked == 1010
        assert item.last_launched == 0

    def test_middle_click_new_window_launches_running_app(self, monkeypatch):
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_MIDDLE, state=0
        )
        launch_calls: list[str] = []
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 2020)
        monkeypatch.setattr(
            dock_window_mod,
            "launch_new_window",
            lambda desktop_id: launch_calls.append(desktop_id),
        )

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        assert launch_calls == ["firefox.desktop"]
        assert item.last_launched == 2020
        stub.hover.start_anim_pump.assert_called_once_with(700)

    def test_left_click_cycle_dispatches_cycle_action(self, monkeypatch):
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        stub.config.left_click_action = "cycle"
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 1111)

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        stub.window_tracker.cycle_windows.assert_called_once_with("firefox.desktop")
        stub.window_tracker.toggle_focus.assert_not_called()
        assert item.last_clicked == 1111
        assert item.last_launched == 0

    def test_middle_click_minimizes_when_configured(self, monkeypatch):
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        stub.config.middle_click_action = "minimize"
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_MIDDLE, state=0
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 2121)

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        stub.window_tracker.minimize_windows.assert_called_once_with("firefox.desktop")
        stub.window_tracker.toggle_focus.assert_not_called()
        assert item.last_launched == 0

    def test_middle_click_close_focused_when_configured(self, monkeypatch):
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        stub.config.middle_click_action = "close-focused"
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_MIDDLE, state=0
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 2222)

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        stub.window_tracker.close_focused.assert_called_once_with("firefox.desktop")
        stub.window_tracker.toggle_focus.assert_not_called()
        assert item.last_launched == 0

    def test_ctrl_click_still_force_launches(self, monkeypatch):
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        stub.config.left_click_action = "cycle"
        event = SimpleNamespace(
            x=12.0,
            y=6.0,
            button=dock_window_mod.MOUSE_LEFT,
            state=dock_window_mod.Gdk.ModifierType.CONTROL_MASK,
        )
        launch_calls: list[str] = []
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 2323)
        monkeypatch.setattr(
            dock_window_mod,
            "launch_new_window",
            lambda desktop_id: launch_calls.append(desktop_id),
        )

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        assert launch_calls == ["firefox.desktop"]
        stub.window_tracker.cycle_windows.assert_not_called()
        assert item.last_launched == 2323

    def test_left_click_file_item_opens_target(self, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/notes.txt",
            kind=FILE_KIND,
            target="file:///tmp/notes.txt",
        )
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 3030)
        opened: list[str] = []
        monkeypatch.setattr(
            dock_window_mod, "open_target", lambda target: opened.append(target)
        )

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        assert opened == ["file:///tmp/notes.txt"]
        assert item.last_launched == 3030

    def test_left_click_folder_item_opens_folder_stack(self, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        stub, _ = _make_stub(item=item)
        stub._test_geometry_frame.geometry_for_item.return_value = SimpleNamespace(
            draw_rect=SimpleNamespace(x=4, y=5, w=48, h=48)
        )
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 4040)

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        stub._menu.show_folder_stack.assert_called_once()
        kwargs = stub._menu.show_folder_stack.call_args.kwargs
        assert kwargs["item"] is item
        assert kwargs["anchor_x"] == 104
        assert kwargs["anchor_y"] == 205
        assert kwargs["icon_w"] == 48
        assert kwargs["position"] == Position.BOTTOM

    def test_drag_delta_above_threshold_is_ignored(self):
        # Given
        stub, _item = _make_stub()
        stub._click_x = 0.0
        event = SimpleNamespace(
            x=40.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is False
        stub._menu.show.assert_not_called()


class TestScrollAndHoverFlow:
    def test_scroll_on_applet_updates_tooltip(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="applet://volume")
        stub, _ = _make_stub(item=item)
        applet = MagicMock()
        stub.model.get_applet.return_value = applet
        event = SimpleNamespace(
            x=10.0,
            y=5.0,
            direction=dock_window_mod.Gdk.ScrollDirection.UP,
        )
        monkeypatch.setattr(
            dock_window_mod,
            "is_applet",
            lambda desktop_id: desktop_id.startswith("applet://"),
        )

        # When
        handled = dock_window_mod.DockWindow._on_scroll(stub, MagicMock(), event)
        # Then
        assert handled is True
        applet.on_scroll.assert_called_once_with(True)
        stub.tooltip.update.assert_called_once_with(item, stub._test_geometry_frame)

    def test_smooth_scroll_on_applet_uses_delta_direction(self, monkeypatch):
        item = DockItem(desktop_id="applet://separator")
        stub, _ = _make_stub(item=item)
        applet = MagicMock()
        stub.model.get_applet.return_value = applet
        event = SimpleNamespace(
            x=10.0,
            y=5.0,
            direction=dock_window_mod.Gdk.ScrollDirection.SMOOTH,
            get_scroll_deltas=lambda: (True, 0.0, -1.0),
        )
        monkeypatch.setattr(
            dock_window_mod,
            "is_applet",
            lambda desktop_id: desktop_id.startswith("applet://"),
        )

        handled = dock_window_mod.DockWindow._on_scroll(stub, MagicMock(), event)

        assert handled is True
        applet.on_scroll.assert_called_once_with(True)
        stub.tooltip.update.assert_called_once_with(item, stub._test_geometry_frame)

    def test_smooth_scroll_without_vertical_delta_is_ignored(self, monkeypatch):
        item = DockItem(desktop_id="applet://separator")
        stub, _ = _make_stub(item=item)
        applet = MagicMock()
        stub.model.get_applet.return_value = applet
        event = SimpleNamespace(
            x=10.0,
            y=5.0,
            direction=dock_window_mod.Gdk.ScrollDirection.SMOOTH,
            get_scroll_deltas=lambda: (True, 1.0, 0.0),
        )
        monkeypatch.setattr(
            dock_window_mod,
            "is_applet",
            lambda desktop_id: desktop_id.startswith("applet://"),
        )

        handled = dock_window_mod.DockWindow._on_scroll(stub, MagicMock(), event)

        assert handled is False
        applet.on_scroll.assert_not_called()
        stub.tooltip.update.assert_not_called()

    def test_scroll_on_non_applet_returns_false(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        event = SimpleNamespace(
            x=10.0,
            y=5.0,
            direction=dock_window_mod.Gdk.ScrollDirection.DOWN,
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)

        # When
        handled = dock_window_mod.DockWindow._on_scroll(stub, MagicMock(), event)
        # Then
        assert handled is False


class TestLeaveEnterFlow:
    def test_leave_ignores_inferior_notify(self):
        # Given
        stub, _item = _make_stub()
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.INFERIOR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=2.0,
            y=2.0,
        )
        # When
        handled = dock_window_mod.DockWindow._on_leave(stub, MagicMock(), event)
        # Then
        assert handled is False

    def test_leave_inside_input_rect_is_ignored(self):
        # Given
        stub, _item = _make_stub()
        stub._cache.geometry_frame.frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 100, 100)
        )
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=20.0,
            y=20.0,
        )

        # When
        handled = dock_window_mod.DockWindow._on_leave(stub, MagicMock(), event)
        # Then
        assert handled is False
        stub.interaction.point_inside_event_frame.assert_called_once_with(
            x=20.0, y=20.0
        )
        stub.hover.cancel.assert_not_called()

    def test_leave_clears_hover_and_resets_cursor_without_preview_or_autohide(self):
        # Given
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._cache.geometry_frame.frame = None
        stub._cache.applied_input_frame = None
        stub.preview = MagicMock()
        stub.preview.get_visible.return_value = False
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        # When
        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)
        # Then
        assert handled is True
        stub.interaction.on_effective_leave.assert_called_once_with(widget)

    def test_leave_with_visible_preview_defers_autohide_until_preview_hides(self):
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._cache.geometry_frame.frame = None
        stub._cache.applied_input_frame = None
        stub.preview = MagicMock()
        stub.preview.get_visible.return_value = True
        stub.autohide = _autohide(enabled=True)
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)

        assert handled is True
        stub.interaction.on_effective_leave.assert_called_once_with(widget)

    def test_leave_with_autohide_keeps_hover_identity_until_hidden(self):
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._cache.geometry_frame.frame = None
        stub._cache.applied_input_frame = None
        stub.autohide = _autohide(enabled=True)
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)

        assert handled is True
        stub.interaction.on_effective_leave.assert_called_once_with(widget)

    def test_enter_sets_cursor_and_notifies_autohide(self):
        # Given
        stub, _item = _make_stub()
        stub.autohide = _autohide(enabled=True)
        stub.dock_hovered = False
        event = SimpleNamespace(x=44.0, y=11.0)
        # When
        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)
        # Then
        assert handled is True
        assert stub.cursor_x == 44.0
        assert stub.cursor_y == 11.0
        stub.interaction.on_effective_enter.assert_called_once()

    def test_enter_does_not_trigger_effective_enter_when_outside_cursor_rect(self):
        stub, _item = _make_stub()
        stub.autohide = _autohide(enabled=True)
        stub.dock_hovered = False
        outside_frame = SimpleNamespace(cursor_rect=Rect(292, 70, 1335, 148))
        stub._test_geometry_frame = outside_frame
        event = SimpleNamespace(x=556.0, y=3.0)

        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)

        assert handled is True
        assert stub.cursor_x == 556.0
        assert stub.cursor_y == 3.0
        stub.interaction.on_effective_enter.assert_not_called()
        assert stub.dock_hovered is False

    def test_enter_treats_hidden_gap_trigger_as_inside_with_real_geometry(self):
        theme = SimpleNamespace(
            distance_from_edge=6,
            item_padding=8,
            h_padding=12,
            top_padding=0,
            bottom_padding=4,
            shelf_height=21,
            stroke_width=1.0,
        )
        frame = build_geometry_frame(
            items=[DockItem(desktop_id="firefox.desktop")],
            config=SimpleNamespace(
                pos=Position.BOTTOM,
                icon_size=48,
                zoom_percent=1.5,
                zoom_enabled=True,
            ),
            theme=theme,
            window_w=420,
            window_h=90,
            cursor_main=-1.0,
            autohide_state=HideState.HIDDEN,
            hide_offset=1.0,
        )
        stub, _item = _make_stub()
        stub._test_geometry_frame = frame
        stub.geometry = SimpleNamespace(build_frame=lambda **_kwargs: frame)
        stub.interaction = MagicMock()
        event = SimpleNamespace(
            x=float(frame.cursor_rect.x + frame.cursor_rect.w / 2),
            y=float(frame.cursor_rect.y + 1),
        )

        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)

        assert handled is True
        stub.interaction.on_effective_enter.assert_called_once()


class TestUrgentGlow:
    def test_has_active_urgent_glow_only_when_hidden_and_recent(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        urgent = DockItem(desktop_id="urgent.desktop", last_urgent=1500)
        old = DockItem(desktop_id="old.desktop", last_urgent=1)
        stub.model.visible_items.return_value = [urgent, old]
        state = HideState.HIDDEN
        stub.theme = SimpleNamespace(urgent_glow_time_ms=2)
        assert (
            renderer_mod.has_active_urgent_glow(
                model=stub.model, theme=stub.theme, autohide_state=state, now_us=3000
            )
            is True
        )

        assert (
            renderer_mod.has_active_urgent_glow(
                model=stub.model,
                theme=stub.theme,
                autohide_state=HideState.VISIBLE,
                now_us=3000,
            )
            is False
        )

    def test_flush_scheduled_redraw_requests_widget_draw_once(self):
        # Given
        drawing_area = MagicMock()
        stub = SimpleNamespace(drawing_area=drawing_area, _redraw_source_id=123)

        # When / Then
        assert dock_window_mod.DockWindow._flush_scheduled_redraw(stub) is False
        assert stub._redraw_source_id is None
        drawing_area.queue_draw.assert_called_once()


class TestModelChangedFlow:
    def test_model_changed_refresheshover_and_redraw(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        timeout_add = MagicMock(return_value=55)
        monkeypatch.setattr(dock_window_mod.GLib, "timeout_add", timeout_add)

        # When
        dock_window_mod.DockWindow._on_model_changed(stub)

        # Then
        stub.update_input_region.assert_called_once()
        stub.hover.on_model_changed.assert_called_once()
        stub.hover.update.assert_called_once_with(12.0)
        assert stub._redraw_source_id == 55
        timeout_add.assert_called_once()

    def test_model_changed_skipshover_refresh_when_nothovering(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        stub.hover.hovered_item = None
        timeout_add = MagicMock(return_value=66)
        monkeypatch.setattr(dock_window_mod.GLib, "timeout_add", timeout_add)

        # When
        dock_window_mod.DockWindow._on_model_changed(stub)

        # Then
        stub.update_input_region.assert_called_once()
        stub.hover.on_model_changed.assert_called_once()
        stub.hover.update.assert_not_called()
        assert stub._redraw_source_id == 66
        timeout_add.assert_called_once()


class TestDockWindowSetupAndGeometry:
    def test_setup_window_applies_hints_and_connects(self):
        # Given
        screen = SimpleNamespace(
            get_rgba_visual=lambda: None,
            get_system_visual=lambda: "sys-visual",
            connect=MagicMock(side_effect=[11, 12]),
            disconnect=MagicMock(),
        )
        stub = SimpleNamespace(
            set_title=MagicMock(),
            set_decorated=MagicMock(),
            set_skip_taskbar_hint=MagicMock(),
            set_skip_pager_hint=MagicMock(),
            stick=MagicMock(),
            set_keep_above=MagicMock(),
            set_type_hint=MagicMock(),
            set_app_paintable=MagicMock(),
            set_resizable=MagicMock(),
            get_screen=MagicMock(return_value=screen),
            get_display=MagicMock(return_value=SimpleNamespace()),
            set_visual=MagicMock(),
            connect=MagicMock(),
            _on_destroy=MagicMock(),
            placement=SimpleNamespace(
                attach_screen_signals=MagicMock(),
                on_realize=MagicMock(),
                on_screen_changed=MagicMock(),
                on_scale_factor_changed=MagicMock(),
                on_destroy=MagicMock(),
            ),
        )

        # When
        dock_window_mod.DockWindow._setup_window(stub)

        # Then
        stub.set_title.assert_called_once_with("Docking")
        stub.set_visual.assert_called_once_with("sys-visual")
        assert stub.connect.call_count == 6
        stub.placement.attach_screen_signals.assert_called_once_with(screen)

    def test_setup_drawing_area_initializes_events(self, monkeypatch):
        # Given
        class FakeDrawingArea:
            def __init__(self):
                self.double_buffered = None
                self.events = None
                self.connected: list[str] = []

            def set_double_buffered(self, value: bool):
                self.double_buffered = value

            def set_events(self, events: int):
                self.events = events

            def connect(self, signal: str, _callback):
                self.connected.append(signal)

        monkeypatch.setattr(
            dock_window_mod.Gtk,
            "DrawingArea",
            FakeDrawingArea,
            raising=False,
        )
        stub = SimpleNamespace(
            add=MagicMock(),
            _on_draw=MagicMock(),
            _on_motion=MagicMock(),
            _on_button_press=MagicMock(),
            _on_button_release=MagicMock(),
            _on_leave=MagicMock(),
            _on_enter=MagicMock(),
            _on_scroll=MagicMock(),
            _cache=_window_cache(
                current_geometry_frame="sentinel-current",
                applied_input_frame="sentinel-applied",
            ),
        )

        # When
        dock_window_mod.DockWindow._setup_drawing_area(stub)

        # Then
        assert isinstance(stub.drawing_area, FakeDrawingArea)
        assert stub.drawing_area.double_buffered is False
        assert "draw" in stub.drawing_area.connected
        assert "scroll-event" in stub.drawing_area.connected
        assert stub._cache.geometry_frame.frame == "sentinel-current"
        assert stub._cache.applied_input_frame == "sentinel-applied"

    def test_connect_model_registers_change_listener(self):
        # Given
        add_change_listener = MagicMock()
        stub = SimpleNamespace(
            model=SimpleNamespace(add_change_listener=add_change_listener),
            _on_model_changed=lambda: None,
        )

        # When
        dock_window_mod.DockWindow._connect_model(stub)

        # Then
        add_change_listener.assert_called_once_with(stub._on_model_changed)

    def test_on_destroy_disconnects_model_listener(self):
        stub = SimpleNamespace(
            _disconnect_model=MagicMock(),
        )

        dock_window_mod.DockWindow._on_destroy(stub, MagicMock())

        stub._disconnect_model.assert_called_once_with()

    def test_disconnect_model_unregisters_change_listener(self):
        remove_change_listener = MagicMock()
        stub = SimpleNamespace(
            model=SimpleNamespace(remove_change_listener=remove_change_listener),
            _on_model_changed=lambda: None,
        )

        dock_window_mod.DockWindow._disconnect_model(stub)

        remove_change_listener.assert_called_once_with(stub._on_model_changed)


class TestDockWindowStrutsAndRegion:
    def testupdate_input_region_applies_shape_and_caches_rect(self, monkeypatch):
        # Given
        gdk_window = MagicMock()
        frame = SimpleNamespace(cursor_rect=Rect(140, 36, 120, 54))
        stub = _bind_geometry_signature(
            SimpleNamespace(
                get_window=lambda: gdk_window,
                get_size=MagicMock(return_value=(1920, 122)),
                cursor_x=-1.0,
                cursor_y=-1.0,
                autohide=_autohide(enabled=False),
                zoom_animator=SimpleNamespace(progress=1.0),
                _test_geometry_frame=frame,
                _cache=_window_cache(),
                geometry=SimpleNamespace(build_frame=lambda **_kwargs: frame),
            )
        )

        # When
        dock_window_mod.DockWindow.update_input_region(stub)
        first_rect = stub._cache.geometry_frame.frame.cursor_rect
        dock_window_mod.DockWindow.update_input_region(stub)

        # Then
        assert first_rect is not None
        assert stub._cache.applied_input_frame.cursor_rect == first_rect
        gdk_window.input_shape_combine_region.assert_called_once()

    def test_update_input_region_uses_hidden_gap_trigger_from_real_geometry(self):
        theme = SimpleNamespace(
            distance_from_edge=6,
            item_padding=8,
            h_padding=12,
            top_padding=0,
            bottom_padding=4,
            shelf_height=21,
            stroke_width=1.0,
        )
        config = SimpleNamespace(
            pos=Position.BOTTOM,
            icon_size=48,
            zoom_percent=1.5,
            zoom_enabled=True,
        )
        frame = build_geometry_frame(
            items=[DockItem(desktop_id="firefox.desktop")],
            config=config,
            theme=theme,
            window_w=420,
            window_h=90,
            cursor_main=-1.0,
            autohide_state=HideState.HIDDEN,
            hide_offset=1.0,
        )
        gdk_window = MagicMock()
        stub = _bind_geometry_signature(
            SimpleNamespace(
                get_window=lambda: gdk_window,
                get_size=MagicMock(return_value=(420, 90)),
                cursor_x=-1.0,
                cursor_y=-1.0,
                autohide=_autohide(enabled=True, state=HideState.HIDDEN),
                zoom_animator=SimpleNamespace(progress=1.0),
                _test_geometry_frame=frame,
                _cache=_window_cache(),
                geometry=SimpleNamespace(build_frame=lambda **_kwargs: frame),
            )
        )

        dock_window_mod.DockWindow.update_input_region(stub)

        region = gdk_window.input_shape_combine_region.call_args.args[0]
        extents = region.get_extents()
        assert extents.height == frame.cursor_rect.h
        assert extents.y == frame.cursor_rect.y
        assert stub._cache.applied_input_frame.cursor_rect == frame.cursor_rect


class TestDockWindowDrawAndHelpers:
    def test_on_hide_mode_changed_resets_autohide_when_disabled(self):
        stub = SimpleNamespace(
            autohide=_autohide(enabled=False),
            placement=SimpleNamespace(update_struts=MagicMock()),
            update_input_region=MagicMock(),
            queue_redraw=MagicMock(),
            dodge_monitor=MagicMock(),
            is_pointer_inside_dock=MagicMock(return_value=False),
        )

        dock_window_mod.DockWindow.on_hide_mode_changed(stub)

        stub.autohide.reset.assert_called_once_with()
        stub.autohide.set_hovered.assert_not_called()
        stub.placement.update_struts.assert_called_once_with()
        stub.update_input_region.assert_called_once_with()
        stub.queue_redraw.assert_called_once_with()
        stub.dodge_monitor.evaluate_now.assert_called_once_with()

    def test_on_hide_mode_changed_reconciles_pointer_inside_when_enabled(self):
        stub = SimpleNamespace(
            autohide=_autohide(enabled=True),
            placement=SimpleNamespace(update_struts=MagicMock()),
            update_input_region=MagicMock(),
            queue_redraw=MagicMock(),
            dodge_monitor=MagicMock(),
            is_pointer_inside_dock=MagicMock(return_value=True),
        )

        dock_window_mod.DockWindow.on_hide_mode_changed(stub)

        stub.autohide.reset.assert_not_called()
        stub.autohide.set_hovered.assert_called_once_with(True)
        stub.autohide.reconcile.assert_called_once_with()
        stub.dodge_monitor.evaluate_now.assert_called_once_with()

    def test_on_hide_mode_changed_is_safe_without_dodge_monitor(self):
        stub = SimpleNamespace(
            autohide=_autohide(enabled=True),
            placement=SimpleNamespace(update_struts=MagicMock()),
            update_input_region=MagicMock(),
            queue_redraw=MagicMock(),
            dodge_monitor=None,
            is_pointer_inside_dock=MagicMock(return_value=False),
        )

        dock_window_mod.DockWindow.on_hide_mode_changed(stub)

        stub.autohide.set_hovered.assert_called_once_with(False)
        stub.autohide.reconcile.assert_called_once_with()
        stub.placement.update_struts.assert_called_once_with()

    def test_on_draw_invokes_renderer_and_updates_region(self):
        # Given
        renderer = renderer_mod.DockRenderer()
        renderer.draw = MagicMock()
        geometry = SimpleNamespace()
        geometry.build_frame = MagicMock()
        stub = _bind_geometry_signature(
            SimpleNamespace(
                autohide=_autohide(enabled=False),
                _last_autohide_state=None,
                dock_hovered=False,
                dnd=SimpleNamespace(
                    drag_index=-1, drop_insert_index=-1, drop_target_id=""
                ),
                hover=SimpleNamespace(hovered_item=None),
                model=MagicMock(),
                config=SimpleNamespace(pos=Position.BOTTOM),
                theme=MagicMock(),
                tooltip=MagicMock(),
                _test_geometry_frame=SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100)),
                update_input_region=MagicMock(),
                renderer=renderer,
                cursor_x=1.0,
                cursor_y=2.0,
                get_size=MagicMock(return_value=(1920, 122)),
                _sync_background_blur_hint=MagicMock(),
                zoom_animator=SimpleNamespace(progress=1.0),
                geometry=geometry,
                _cache=_window_cache(
                    current_geometry_frame=SimpleNamespace(
                        cursor_rect=Rect(0, 0, 100, 100)
                    ),
                    current_geometry_frame_signature=(
                        1920,
                        122,
                        1.0,
                        2.0,
                        -1,
                        None,
                        1.0,
                        0.0,
                    ),
                ),
            )
        )
        geometry.build_frame.side_effect = lambda **_kwargs: stub._test_geometry_frame

        # When
        result = dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert result is True
        stub.renderer.draw.assert_called_once()
        stub.update_input_region.assert_called_once()
        stub._sync_background_blur_hint.assert_called_once_with(
            frame=stub._cache.geometry_frame.frame
        )
        geometry.build_frame.assert_not_called()

    def test_on_draw_rebuilds_geometry_when_signature_changes(self):
        renderer = renderer_mod.DockRenderer()
        renderer.draw = MagicMock()
        frame = SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100), item_geometries=())
        geometry = SimpleNamespace(build_frame=MagicMock(return_value=frame))
        stub = _bind_geometry_signature(
            SimpleNamespace(
                autohide=_autohide(enabled=False),
                _last_autohide_state=None,
                dock_hovered=False,
                dnd=SimpleNamespace(
                    drag_index=-1, drop_insert_index=3, drop_target_id=""
                ),
                hover=SimpleNamespace(hovered_item=None),
                model=MagicMock(),
                config=SimpleNamespace(pos=Position.BOTTOM),
                theme=MagicMock(),
                tooltip=MagicMock(),
                update_input_region=MagicMock(),
                renderer=renderer,
                cursor_x=1.0,
                cursor_y=2.0,
                get_size=MagicMock(return_value=(1920, 122)),
                _sync_background_blur_hint=MagicMock(),
                zoom_animator=SimpleNamespace(progress=1.0),
                geometry=geometry,
                _cache=_window_cache(
                    current_geometry_frame=SimpleNamespace(
                        cursor_rect=Rect(0, 0, 50, 50)
                    ),
                    current_geometry_frame_signature=(
                        1920,
                        122,
                        1.0,
                        2.0,
                        -1,
                        None,
                        1.0,
                        0.0,
                    ),
                ),
            )
        )

        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        geometry.build_frame.assert_called_once_with(
            drop_insert_index=3,
            cursor_x=None,
            cursor_y=None,
        )
        assert stub._cache.geometry_frame.frame is frame

    def test_current_or_build_geometry_frame_returns_cached_frame(self):
        frame = SimpleNamespace()
        stub = _bind_geometry_signature(
            SimpleNamespace(
                _cache=_window_cache(
                    current_geometry_frame=frame,
                    current_geometry_frame_signature=(
                        1920,
                        122,
                        1.0,
                        2.0,
                        -1,
                        None,
                        1.0,
                        0.0,
                    ),
                ),
                cursor_x=1.0,
                cursor_y=2.0,
                get_size=MagicMock(return_value=(1920, 122)),
                autohide=_autohide(enabled=False),
                zoom_animator=SimpleNamespace(progress=1.0),
            )
        )

        result = dock_window_mod.DockWindow._current_or_build_geometry_frame(stub)

        assert result is frame

    def test_current_or_build_geometry_frame_builds_on_cache_miss(self):
        frame = SimpleNamespace()
        geometry = SimpleNamespace(build_frame=MagicMock(return_value=frame))
        stub = _bind_geometry_signature(
            SimpleNamespace(
                _cache=_window_cache(),
                cursor_x=1.0,
                cursor_y=2.0,
                get_size=MagicMock(return_value=(1920, 122)),
                autohide=_autohide(enabled=False),
                zoom_animator=SimpleNamespace(progress=1.0),
                geometry=geometry,
            )
        )

        result = dock_window_mod.DockWindow._current_or_build_geometry_frame(stub)

        assert result is frame
        geometry.build_frame.assert_called_once()

    def test_on_draw_works_with_real_dock_renderer_instance(self):
        renderer = renderer_mod.DockRenderer()
        renderer.draw = MagicMock()
        stub = _bind_geometry_signature(
            SimpleNamespace(
                autohide=_autohide(enabled=True, state=HideState.HIDDEN),
                _last_autohide_state=HideState.HIDDEN,
                dock_hovered=False,
                dnd=SimpleNamespace(
                    drag_index=-1, drop_insert_index=-1, drop_target_id=""
                ),
                hover=SimpleNamespace(hovered_item=None),
                model=MagicMock(),
                config=SimpleNamespace(pos=Position.BOTTOM),
                theme=SimpleNamespace(urgent_glow_time_ms=500),
                tooltip=MagicMock(),
                _test_geometry_frame=SimpleNamespace(
                    cursor_rect=Rect(0, 0, 100, 100),
                    item_geometries=(),
                ),
                update_input_region=MagicMock(),
                renderer=renderer,
                cursor_x=-1.0,
                cursor_y=-1.0,
                get_size=MagicMock(return_value=(1920, 122)),
                _sync_background_blur_hint=MagicMock(),
                zoom_animator=SimpleNamespace(progress=0.0),
                geometry=SimpleNamespace(
                    build_frame=lambda **_kwargs: stub._test_geometry_frame
                ),
                drawing_area=MagicMock(),
                _cache=_window_cache(),
            )
        )
        stub.model.visible_items.return_value = []

        result = dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        assert result is True
        stub.renderer.draw.assert_called_once()

    def test_on_draw_resets_cursor_when_hidden(self):
        # Given
        hovered = DockItem(desktop_id="hovered.desktop")
        stub = _bind_geometry_signature(
            SimpleNamespace(
                autohide=_autohide(enabled=True, state=HideState.HIDDEN),
                dnd=SimpleNamespace(
                    drag_index=-1, drop_insert_index=-1, drop_target_id=""
                ),
                hover=SimpleNamespace(hovered_item=hovered),
                renderer=SimpleNamespace(
                    draw=MagicMock(),
                    has_active_urgent_glow=lambda **_kwargs: False,
                ),
                model=MagicMock(),
                config=SimpleNamespace(pos=Position.BOTTOM),
                theme=MagicMock(),
                tooltip=MagicMock(),
                _test_geometry_frame=SimpleNamespace(
                    cursor_rect=Rect(0, 0, 100, 100),
                    item_geometries=(),
                ),
                update_input_region=MagicMock(),
                cursor_x=25.0,
                cursor_y=33.0,
                get_size=MagicMock(return_value=(1920, 122)),
                _sync_background_blur_hint=MagicMock(),
                zoom_animator=SimpleNamespace(progress=0.0),
                geometry=SimpleNamespace(
                    build_frame=lambda **_kwargs: stub._test_geometry_frame
                ),
                _cache=_window_cache(),
            )
        )

        # When
        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert stub.cursor_x == -1.0
        assert stub.cursor_y == -1.0
        assert stub.hover.hovered_item is None
        stub.tooltip.hide.assert_called_once()

    def test_on_draw_refreshes_tooltip_once_when_showing_finishes(self):
        hovered = DockItem(desktop_id="hovered.desktop")
        frame = SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100), item_geometries=())
        stub = _bind_geometry_signature(
            SimpleNamespace(
                autohide=_autohide(enabled=True, state=HideState.VISIBLE),
                _last_autohide_state=HideState.SHOWING,
                dock_hovered=True,
                dnd=SimpleNamespace(
                    drag_index=-1, drop_insert_index=-1, drop_target_id=""
                ),
                hover=SimpleNamespace(hovered_item=hovered, update=MagicMock()),
                renderer=SimpleNamespace(
                    draw=MagicMock(),
                    has_active_urgent_glow=lambda **_kwargs: False,
                ),
                model=MagicMock(),
                config=SimpleNamespace(pos=Position.BOTTOM),
                theme=MagicMock(),
                tooltip=MagicMock(),
                _test_geometry_frame=frame,
                update_input_region=MagicMock(),
                cursor_x=25.0,
                cursor_y=33.0,
                get_size=MagicMock(return_value=(1920, 122)),
                _sync_background_blur_hint=MagicMock(),
                zoom_animator=SimpleNamespace(progress=1.0),
                geometry=SimpleNamespace(build_frame=lambda **_kwargs: frame),
                _cache=_window_cache(),
            )
        )

        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        stub.hover.update.assert_called_once_with(25.0, frame=frame)
        assert stub._last_autohide_state == HideState.VISIBLE

    def test_on_motion_updates_cursor_and_hover(self, monkeypatch):
        # Given
        widget = MagicMock()
        timeout_add = MagicMock(return_value=77)
        monkeypatch.setattr(dock_window_mod.GLib, "timeout_add", timeout_add)
        menu = MagicMock()
        menu.open_folder_stack_item_id.return_value = None
        stub = _bind_geometry_signature(
            SimpleNamespace(
                cursor_x=-1.0,
                cursor_y=-1.0,
                get_size=MagicMock(return_value=(1920, 122)),
                dock_hovered=False,
                config=SimpleNamespace(pos=Position.BOTTOM),
                _test_geometry_frame=SimpleNamespace(
                    cursor_rect=Rect(0, 0, 100, 100),
                    item_at_point=MagicMock(return_value=None),
                ),
                update_input_region=MagicMock(),
                hover=SimpleNamespace(update=MagicMock()),
                _menu=menu,
                autohide=_autohide(enabled=False),
                zoom_animator=MagicMock(),
                geometry=SimpleNamespace(
                    build_frame=lambda **_kwargs: stub._test_geometry_frame
                ),
                _cache=_window_cache(),
                _redraw_source_id=None,
            )
        )
        stub.interaction = DockInteractionCoordinator(stub)
        event = SimpleNamespace(x=7.0, y=9.0)

        # When
        handled = dock_window_mod.DockWindow._on_motion(stub, widget, event)

        # Then
        assert handled is False
        assert stub.cursor_x == 7.0
        assert stub.cursor_y == 9.0
        assert stub.dock_hovered is True
        stub.update_input_region.assert_called_once()
        stub.hover.update.assert_called_once_with(7.0, frame=stub._test_geometry_frame)
        assert stub._redraw_source_id == 77
        widget.queue_draw.assert_not_called()
        timeout_add.assert_called_once()

    def test_on_motion_closes_folder_stack_after_leaving_source_folder(self):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        other_item = DockItem(desktop_id="firefox.desktop")
        widget = MagicMock()
        menu = MagicMock()
        menu.open_folder_stack_item_id.return_value = item.desktop_id
        frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 100, 100),
            item_at_point=MagicMock(return_value=other_item),
        )
        stub = _bind_geometry_signature(
            SimpleNamespace(
                cursor_x=-1.0,
                cursor_y=-1.0,
                get_size=MagicMock(return_value=(1920, 122)),
                dock_hovered=True,
                config=SimpleNamespace(pos=Position.BOTTOM),
                _test_geometry_frame=frame,
                update_input_region=MagicMock(),
                hover=SimpleNamespace(update=MagicMock()),
                _menu=menu,
                autohide=_autohide(enabled=False),
                zoom_animator=MagicMock(),
                geometry=SimpleNamespace(build_frame=lambda **_kwargs: frame),
                _cache=_window_cache(),
                _redraw_source_id=None,
            )
        )
        stub.interaction = DockInteractionCoordinator(stub)
        event = SimpleNamespace(x=12.0, y=9.0)

        handled = dock_window_mod.DockWindow._on_motion(stub, widget, event)

        assert handled is False
        menu.close_folder_stack.assert_called_once_with()

    def test_on_button_press_records_click_state(self):
        # Given
        stub = SimpleNamespace(_click_x=0.0, _click_y=0.0, _click_button=0)
        event = SimpleNamespace(x=11.0, y=22.0, button=3)

        # When
        handled = dock_window_mod.DockWindow._on_button_press(stub, MagicMock(), event)

        # Then
        assert handled is False
        assert stub._click_x == 11.0
        assert stub._click_y == 22.0
        assert stub._click_button == 3

    def test_queue_redraw(self):
        timeout_add = MagicMock(return_value=99)
        drawing_area = MagicMock()
        stub = _bind_geometry_signature(
            SimpleNamespace(
                drawing_area=drawing_area,
                _cache=_window_cache(),
                _redraw_source_id=None,
            )
        )
        dock_window_mod.GLib.timeout_add = timeout_add

        dock_window_mod.DockWindow.queue_redraw(stub)

        assert stub._redraw_source_id == 99
        drawing_area.queue_draw.assert_not_called()
        timeout_add.assert_called_once()

    def test_schedule_redraw_coalesces_multiple_requests(self):
        timeout_add = MagicMock(return_value=77)
        stub = _bind_geometry_signature(SimpleNamespace(_redraw_source_id=None))
        dock_window_mod.GLib.timeout_add = timeout_add

        dock_window_mod.DockWindow._schedule_redraw(stub)
        dock_window_mod.DockWindow._schedule_redraw(stub)

        assert stub._redraw_source_id == 77
        timeout_add.assert_called_once()


class TestBlurHintSync:
    def test_sync_background_blur_hint_updates_once_and_caches(self, monkeypatch):
        class FakeX11Window:
            def get_scale_factor(self):
                return 2

        monkeypatch.setattr(dock_window_mod.GdkX11, "X11Window", FakeX11Window)
        set_mock = MagicMock()
        monkeypatch.setattr(dock_window_mod, "set_blur_region", set_mock)
        window = FakeX11Window()

        frame = SimpleNamespace(background_rect=Rect(10, 20, 100, 30))
        stub = SimpleNamespace(
            get_window=lambda: window,
            autohide=_autohide(enabled=False),
            theme=SimpleNamespace(roundness=4.0, round_bottom=True),
            config=SimpleNamespace(pos=Position.BOTTOM),
            _cache=_window_cache(),
        )

        dock_window_mod.DockWindow._sync_background_blur_hint(stub, frame=frame)
        dock_window_mod.DockWindow._sync_background_blur_hint(stub, frame=frame)

        set_mock.assert_called_once_with(
            gdk_window=window,
            blur_region=[20, 40, 200, 60, 8, 8, 8, 8],
        )
        assert stub._cache.last_blur_region == (20, 40, 200, 60, 8, 8, 8, 8)

    def test_sync_background_blur_hint_clears_when_hidden(self, monkeypatch):
        class FakeX11Window:
            def get_scale_factor(self):
                return 1

        monkeypatch.setattr(dock_window_mod.GdkX11, "X11Window", FakeX11Window)
        clear_mock = MagicMock()
        monkeypatch.setattr(dock_window_mod, "clear_blur_region", clear_mock)
        window = FakeX11Window()

        stub = SimpleNamespace(
            get_window=lambda: window,
            autohide=_autohide(enabled=True, state=HideState.HIDDEN),
            theme=SimpleNamespace(roundness=4.0, round_bottom=True),
            config=SimpleNamespace(pos=Position.BOTTOM),
            _cache=_window_cache(last_blur_region=(1, 2, 3, 4, 5, 6, 7, 8)),
        )

        dock_window_mod.DockWindow._sync_background_blur_hint(
            stub,
            frame=SimpleNamespace(background_rect=Rect(10, 20, 100, 30)),
        )

        clear_mock.assert_called_once_with(gdk_window=window)
        assert stub._cache.last_blur_region is None
