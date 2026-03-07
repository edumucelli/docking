"""Integration-style tests for DockWindow event handlers."""

from __future__ import annotations

from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import docking.ui.dock_window as dock_window_mod
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import Rect


def _make_stub(item: DockItem | None = None):
    item = item or DockItem(desktop_id="firefox.desktop")
    frame = SimpleNamespace(
        cursor_rect=Rect(0, 0, 100, 100),
        item_at_point=MagicMock(return_value=item),
        geometry_for_item=MagicMock(return_value=None),
    )
    stub = SimpleNamespace()
    stub.config = SimpleNamespace(pos=Position.BOTTOM)
    stub.model = MagicMock()
    stub.model.visible_items.return_value = [item]
    stub.model.get_applet = MagicMock()
    stub.theme = SimpleNamespace(item_padding=8, h_padding=10, urgent_glow_time_ms=500)
    stub.window_tracker = MagicMock()
    stub._menu = MagicMock()
    stub._tooltip = MagicMock()
    stub._hover = MagicMock()
    stub._hover.hovered_item = item
    stub._hover.cancel = MagicMock()
    stub._preview = None
    stub._menu_popup_visible = False
    stub.autohide = None
    stub.cursor_x = 12.0
    stub.cursor_y = 6.0
    stub._click_x = 12.0
    stub._click_y = 6.0
    stub.local_cursor_main = MagicMock(return_value=-1e6)
    stub._main_axis_cursor = MagicMock(return_value=33.0)
    stub.hit_test = MagicMock(return_value=item)
    stub._update_dock_size = MagicMock()
    stub.drawing_area = MagicMock()
    stub._pointer_inside_input_rect = MagicMock(return_value=False)
    stub._get_geometry_frame = MagicMock(return_value=frame)
    stub._last_geometry_frame = frame
    stub._dock_hovered = True
    stub._on_effective_enter = MethodType(
        dock_window_mod.DockWindow._on_effective_enter, stub
    )
    stub._on_effective_leave = MethodType(
        dock_window_mod.DockWindow._on_effective_leave, stub
    )
    stub._current_input_rect = lambda: (
        None
        if getattr(stub, "_last_geometry_frame", None) is None
        else stub._last_geometry_frame.cursor_rect
    )
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
        stub._menu.show.assert_called_once_with(event, 33.0)

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
        stub._tooltip.update.assert_called_once_with(
            item, stub._get_geometry_frame.return_value
        )
        stub._hover.start_anim_pump.assert_called_once_with(350)

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
        stub._hover.start_anim_pump.assert_called_once_with(350)
        assert item.last_clicked == 1010
        assert item.last_launched == 0

    def test_middle_click_force_launches_running_app(self, monkeypatch):
        # Given
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
            "launch",
            lambda desktop_id: launch_calls.append(desktop_id),
        )

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        assert launch_calls == ["firefox.desktop"]
        assert item.last_launched == 2020
        stub._hover.start_anim_pump.assert_called_once_with(700)

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

    def test_left_click_folder_item_opens_item_menu(self, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 4040)

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        stub._menu.show_item.assert_called_once_with(event, item)

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
        stub._tooltip.update.assert_called_once_with(
            item, stub._get_geometry_frame.return_value
        )

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
        stub._last_geometry_frame = SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100))
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
        stub._hover.cancel.assert_not_called()

    def test_leave_clears_hover_and_resets_cursor_without_preview_or_autohide(self):
        # Given
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._last_geometry_frame = None
        stub._preview = MagicMock()
        stub._preview.get_visible.return_value = False
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
        assert stub._hover.hovered_item is None
        stub._hover.cancel.assert_called_once()
        stub._tooltip.hide.assert_called_once()
        stub._preview.schedule_hide.assert_not_called()
        assert stub.cursor_x == -1.0
        assert stub.cursor_y == -1.0
        stub._update_dock_size.assert_called_once()
        widget.queue_draw.assert_called_once()

    def test_leave_with_visible_preview_defers_autohide_until_preview_hides(self):
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._last_geometry_frame = None
        stub._preview = MagicMock()
        stub._preview.get_visible.return_value = True
        stub.autohide = SimpleNamespace(enabled=True, on_mouse_leave=MagicMock())
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)

        assert handled is True
        stub._preview.schedule_hide.assert_called_once()
        stub.autohide.on_mouse_leave.assert_not_called()
        assert stub._hover.hovered_item is not None
        assert stub.cursor_x == 12.0
        assert stub.cursor_y == 6.0

    def test_leave_with_autohide_keeps_hover_identity_until_hidden(self):
        stub, item = _make_stub()
        widget = MagicMock()
        stub._last_geometry_frame = None
        stub.autohide = SimpleNamespace(enabled=True, on_mouse_leave=MagicMock())
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)

        assert handled is True
        assert stub._hover.hovered_item is item
        assert stub.cursor_x == 12.0
        assert stub.cursor_y == 6.0
        stub.autohide.on_mouse_leave.assert_called_once()

    def test_enter_sets_cursor_and_notifies_autohide(self):
        # Given
        stub, _item = _make_stub()
        stub.autohide = MagicMock()
        stub._dock_hovered = False
        event = SimpleNamespace(x=44.0, y=11.0)
        # When
        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)
        # Then
        assert handled is True
        assert stub.cursor_x == 44.0
        assert stub.cursor_y == 11.0
        stub.autohide.on_mouse_enter.assert_called_once()

    def test_enter_does_not_trigger_effective_enter_when_outside_cursor_rect(self):
        stub, _item = _make_stub()
        stub.autohide = MagicMock()
        stub._dock_hovered = False
        outside_frame = SimpleNamespace(cursor_rect=Rect(292, 70, 1335, 148))
        stub._get_geometry_frame = MagicMock(return_value=outside_frame)
        event = SimpleNamespace(x=556.0, y=3.0)

        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)

        assert handled is True
        assert stub.cursor_x == 556.0
        assert stub.cursor_y == 3.0
        stub.autohide.on_mouse_enter.assert_not_called()
        assert stub._dock_hovered is False


class TestMenuPopupFlow:
    def test_menu_close_triggers_autohide_when_pointer_outside(self):
        # Given
        stub, _item = _make_stub()
        stub._menu_popup_visible = True
        stub.autohide = SimpleNamespace(enabled=True, on_mouse_leave=MagicMock())
        stub._preview = MagicMock()
        stub._preview.get_visible.return_value = False

        # When
        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        # Then
        assert stub._menu_popup_visible is False
        stub._hover.cancel.assert_called_once()
        stub._tooltip.hide.assert_called_once()
        stub._preview.schedule_hide.assert_not_called()
        stub._update_dock_size.assert_called_once()
        stub.drawing_area.queue_draw.assert_called_once()
        stub.autohide.on_mouse_leave.assert_called_once()

    def test_menu_close_with_visible_preview_defers_autohide(self):
        stub, _item = _make_stub()
        stub._menu_popup_visible = True
        stub.autohide = SimpleNamespace(enabled=True, on_mouse_leave=MagicMock())
        stub._preview = MagicMock()
        stub._preview.get_visible.return_value = True

        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        assert stub._menu_popup_visible is False
        stub._preview.schedule_hide.assert_called_once()
        stub.autohide.on_mouse_leave.assert_not_called()

    def test_menu_close_does_not_hide_when_pointer_is_back_on_dock(self):
        # Given
        stub, _item = _make_stub()
        stub._menu_popup_visible = True
        stub.autohide = SimpleNamespace(enabled=True, on_mouse_leave=MagicMock())
        stub._pointer_inside_input_rect.return_value = True

        # When
        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        # Then
        assert stub._menu_popup_visible is False
        stub.autohide.on_mouse_leave.assert_not_called()
        stub._hover.cancel.assert_not_called()

    def test_menu_close_is_noop_when_no_popup_is_tracked(self):
        # Given
        stub, _item = _make_stub()
        stub.autohide = SimpleNamespace(enabled=True, on_mouse_leave=MagicMock())

        # When
        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        # Then
        stub.autohide.on_mouse_leave.assert_not_called()


class TestUrgentGlow:
    def test_has_active_urgent_glow_only_when_hidden_and_recent(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        urgent = DockItem(desktop_id="urgent.desktop", last_urgent=1500)
        old = DockItem(desktop_id="old.desktop", last_urgent=1)
        stub.model.visible_items.return_value = [urgent, old]
        stub.autohide = SimpleNamespace(enabled=True, state=HideState.HIDDEN)
        stub.theme = SimpleNamespace(urgent_glow_time_ms=2)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 3000)

        # Then
        # When
        assert dock_window_mod.DockWindow._has_active_urgent_glow(stub) is True

        stub.autohide.state = HideState.VISIBLE
        assert dock_window_mod.DockWindow._has_active_urgent_glow(stub) is False

    def test_urgent_glow_tick_requests_redraw_once(self):
        # Given
        stub, _item = _make_stub()
        stub.drawing_area = MagicMock()
        # Then
        # When
        assert dock_window_mod.DockWindow._urgent_glow_tick(stub) is False
        stub.drawing_area.queue_draw.assert_called_once()


class TestModelChangedFlow:
    def test_model_changed_refreshes_hover_and_redraw(self):
        # Given
        stub, _item = _make_stub()
        stub.drawing_area = MagicMock()

        # When
        dock_window_mod.DockWindow._on_model_changed(stub)

        # Then
        stub._update_dock_size.assert_called_once()
        stub._hover.on_model_changed.assert_called_once()
        stub._hover.update.assert_called_once_with(33.0)
        stub.drawing_area.queue_draw.assert_called_once()

    def test_model_changed_skips_hover_refresh_when_not_hovering(self):
        # Given
        stub, _item = _make_stub()
        stub.drawing_area = MagicMock()
        stub._hover.hovered_item = None

        # When
        dock_window_mod.DockWindow._on_model_changed(stub)

        # Then
        stub._update_dock_size.assert_called_once()
        stub._hover.on_model_changed.assert_called_once()
        stub._hover.update.assert_not_called()
        stub.drawing_area.queue_draw.assert_called_once()


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
            set_visual=MagicMock(),
            connect=MagicMock(),
            _on_realize=MagicMock(),
            _on_screen_changed=MagicMock(),
            _on_scale_factor_changed=MagicMock(),
            _on_destroy=MagicMock(),
            _on_screen_metrics_changed=MagicMock(),
            _screen_signal_handlers=[],
        )

        # When
        dock_window_mod.DockWindow._setup_window(stub)

        # Then
        stub.set_title.assert_called_once_with("Docking")
        stub.set_visual.assert_called_once_with("sys-visual")
        assert stub.connect.call_count == 5
        screen.connect.assert_any_call(
            "monitors-changed", stub._on_screen_metrics_changed
        )
        screen.connect.assert_any_call("size-changed", stub._on_screen_metrics_changed)

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
            _last_geometry_frame="sentinel",
        )

        # When
        dock_window_mod.DockWindow._setup_drawing_area(stub)

        # Then
        assert isinstance(stub.drawing_area, FakeDrawingArea)
        assert stub.drawing_area.double_buffered is False
        assert "draw" in stub.drawing_area.connected
        assert "scroll-event" in stub.drawing_area.connected
        assert stub._last_geometry_frame == "sentinel"

    def test_connect_model_sets_on_change_handler(self):
        # Given
        stub = SimpleNamespace(
            model=SimpleNamespace(on_change=None), _on_model_changed=lambda: None
        )

        # When
        dock_window_mod.DockWindow._connect_model(stub)

        # Then
        assert stub.model.on_change == stub._on_model_changed

    def test_on_realize_calls_position_struts_and_input_update(self):
        # Given
        screen = SimpleNamespace(
            connect=MagicMock(side_effect=[21, 22]), disconnect=MagicMock()
        )
        stub = SimpleNamespace(
            get_display=lambda: None,
            get_screen=lambda: screen,
            config=SimpleNamespace(active_display=False),
            _position_dock=MagicMock(),
            _set_struts=MagicMock(),
            _update_input_region=MagicMock(),
            _on_screen_metrics_changed=MagicMock(),
            _screen_signal_handlers=[],
        )

        # When
        dock_window_mod.DockWindow._on_realize(stub, MagicMock())

        # Then
        stub._position_dock.assert_called_once()
        stub._set_struts.assert_called_once()
        stub._update_input_region.assert_called_once()
        assert len(stub._screen_signal_handlers) == 2

    def test_on_screen_changed_reattaches_and_schedules_reposition(self):
        screen = SimpleNamespace(
            connect=MagicMock(side_effect=[31, 32]), disconnect=MagicMock()
        )
        stub = SimpleNamespace(
            get_screen=lambda: screen,
            get_realized=lambda: True,
            _on_screen_metrics_changed=MagicMock(),
            _screen_signal_handlers=[],
            _geometry_refresh_source=0,
        )

        idle_calls: list[tuple[object, tuple[object, ...]]] = []
        dock_window_mod.GLib.idle_add = MagicMock(
            side_effect=lambda cb, *args: idle_calls.append((cb, args)) or 77
        )

        dock_window_mod.DockWindow._on_screen_changed(stub, MagicMock(), None)

        assert len(stub._screen_signal_handlers) == 2
        assert stub._geometry_refresh_source == 77
        assert (
            idle_calls[0][0] == dock_window_mod.DockWindow._apply_scheduled_reposition
        )
        assert idle_calls[0][1] == (stub,)

    def test_schedule_reposition_coalesces_until_idle_runs(self, monkeypatch):
        stub = SimpleNamespace(
            get_realized=lambda: True,
            _geometry_refresh_source=0,
            reposition=MagicMock(),
        )
        idle_calls: list[tuple[object, tuple[object, ...]]] = []
        monkeypatch.setattr(
            dock_window_mod.GLib,
            "idle_add",
            lambda cb, *args: idle_calls.append((cb, args)) or 88,
        )

        dock_window_mod.DockWindow._schedule_reposition(stub)
        dock_window_mod.DockWindow._schedule_reposition(stub)

        assert stub._geometry_refresh_source == 88
        assert len(idle_calls) == 1

        result = dock_window_mod.DockWindow._apply_scheduled_reposition(stub)

        assert result is False
        assert stub._geometry_refresh_source == 0
        stub.reposition.assert_called_once()

    def test_on_destroy_cleans_geometry_refresh_and_screen_handlers(self, monkeypatch):
        screen = SimpleNamespace(disconnect=MagicMock())
        removed: list[int] = []
        stub = SimpleNamespace(
            _geometry_refresh_source=91,
            _screen_signal_handlers=[(screen, 4), (screen, 5)],
        )
        monkeypatch.setattr(
            dock_window_mod.GLib, "source_remove", lambda source: removed.append(source)
        )

        dock_window_mod.DockWindow._on_destroy(stub, MagicMock())

        assert removed == [91]
        assert stub._geometry_refresh_source == 0
        assert stub._screen_signal_handlers == []
        screen.disconnect.assert_any_call(4)
        screen.disconnect.assert_any_call(5)

    def test_position_dock_horizontal_bottom(self):
        # Given
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        stub = SimpleNamespace(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=True,
                zoom_percent=1.2,
                pos=Position.BOTTOM,
                active_display=False,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.5,
                distance_from_edge=0,
            ),
            _active_monitor=None,
            _update_barrier=MagicMock(),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._position_dock(stub)

        # Then
        stub.set_size_request.assert_called_once()
        stub.resize.assert_called_once()
        stub.move.assert_called_once()

    def test_position_dock_bottom_keeps_window_on_screen_edge_with_theme_gap(self):
        # Given
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        stub = SimpleNamespace(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=False,
                zoom_percent=1.0,
                pos=Position.BOTTOM,
                active_display=False,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.0,
                distance_from_edge=6,
            ),
            _active_monitor=None,
            _update_barrier=MagicMock(),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._position_dock(stub)

        # Then
        stub.move.assert_called_once_with(0, 1014)

    def test_position_dock_right_keeps_window_on_screen_edge_with_theme_gap(self):
        # Given
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1000)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        stub = SimpleNamespace(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=False,
                zoom_percent=1.0,
                pos=Position.RIGHT,
                active_display=False,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.0,
                distance_from_edge=6,
            ),
            _active_monitor=None,
            _update_barrier=MagicMock(),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._position_dock(stub)

        # Then
        stub.move.assert_called_once_with(1854, 24)

    def test_position_dock_vertical_right(self):
        # Given
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1000)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        stub = SimpleNamespace(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=False,
                zoom_percent=1.0,
                pos=Position.RIGHT,
                active_display=False,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.5,
                distance_from_edge=0,
            ),
            _active_monitor=None,
            _update_barrier=MagicMock(),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._position_dock(stub)

        # Then
        stub.move.assert_called_once()

    def test_position_dock_uses_selected_monitor_index(self):
        # Given
        geom_primary = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work_primary = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        geom_secondary = SimpleNamespace(x=1920, y=0, width=1280, height=1024)
        work_secondary = SimpleNamespace(x=1920, y=24, width=1280, height=1000)
        primary = SimpleNamespace(
            get_geometry=lambda: geom_primary, get_workarea=lambda: work_primary
        )
        secondary = SimpleNamespace(
            get_geometry=lambda: geom_secondary, get_workarea=lambda: work_secondary
        )
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: primary,
            get_monitor=lambda idx: secondary if idx == 1 else primary,
        )
        stub = SimpleNamespace(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=True,
                zoom_percent=1.2,
                pos=Position.BOTTOM,
                monitor_index=1,
                active_display=False,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.5,
                distance_from_edge=0,
            ),
            _active_monitor=None,
            _update_barrier=MagicMock(),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._position_dock(stub)

        # Then
        assert stub.move.call_args[0][0] >= 1920

    def test_get_monitor_menu_choices_only_for_multiple_monitors(self):
        # Given
        geom = SimpleNamespace(width=1920, height=1080)
        primary = SimpleNamespace(get_geometry=lambda: geom)
        display = SimpleNamespace(
            get_n_monitors=lambda: 1,
            get_primary_monitor=lambda: primary,
            get_monitor=lambda _idx: primary,
        )
        stub = SimpleNamespace(get_display=lambda: display)

        # Then
        assert dock_window_mod.DockWindow.get_monitor_menu_choices(stub) == []

    def test_get_monitor_menu_choices_does_not_duplicate_primary(self):
        # Given
        geom1 = SimpleNamespace(width=1920, height=1080)
        geom2 = SimpleNamespace(width=2560, height=1440)
        mon1 = SimpleNamespace(get_geometry=lambda: geom1)
        mon2 = SimpleNamespace(get_geometry=lambda: geom2)
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: mon1,
            get_monitor=lambda idx: mon1 if idx == 0 else mon2,
        )
        stub = SimpleNamespace(get_display=lambda: display)

        # When
        choices = dock_window_mod.DockWindow.get_monitor_menu_choices(stub)

        # Then
        labels = [label for label, _ in choices]
        assert labels == ["Display 1: 1920x1080 (Primary)", "Display 2: 2560x1440"]


class TestDockWindowStrutsAndRegion:
    def test_set_struts_clears_when_autohide_enabled(self):
        # Given
        stub = SimpleNamespace(
            config=SimpleNamespace(autohide=True),
            _clear_struts=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._set_struts(stub)

        # Then
        stub._clear_struts.assert_called_once()

    def test_set_struts_returns_when_no_window(self):
        # Given
        stub = SimpleNamespace(
            config=SimpleNamespace(autohide=False),
            get_window=lambda: None,
        )

        # When / Then
        dock_window_mod.DockWindow._set_struts(stub)

    def test_set_struts_calls_platform_helper_for_x11(self, monkeypatch):
        # Given
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            dock_window_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        set_struts = MagicMock()
        monkeypatch.setattr(dock_window_mod, "set_dock_struts", set_struts)
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        gdk_window = FakeX11Window()
        stub = SimpleNamespace(
            config=SimpleNamespace(
                autohide=False,
                icon_size=48,
                pos=Position.BOTTOM,
                active_display=False,
            ),
            theme=SimpleNamespace(bottom_padding=8, distance_from_edge=0),
            _active_monitor=None,
            get_window=lambda: gdk_window,
            get_display=lambda: display,
            get_screen=lambda: MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._set_struts(stub)

        # Then
        set_struts.assert_called_once()

    def test_clear_struts_calls_helper_for_x11(self, monkeypatch):
        # Given
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            dock_window_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        clear = MagicMock()
        monkeypatch.setattr(dock_window_mod, "clear_struts", clear)
        gdk_window = FakeX11Window()
        stub = SimpleNamespace(get_window=lambda: gdk_window)

        # When
        dock_window_mod.DockWindow._clear_struts(stub)

        # Then
        clear.assert_called_once_with(gdk_window=gdk_window)

    def test_update_input_region_applies_shape_and_caches_rect(self, monkeypatch):
        # Given
        gdk_window = MagicMock()
        frame = SimpleNamespace(cursor_rect=Rect(140, 36, 120, 54))
        stub = SimpleNamespace(
            get_window=lambda: gdk_window,
            _get_geometry_frame=MagicMock(return_value=frame),
            _last_geometry_frame=None,
            _current_input_rect=lambda: (
                None
                if getattr(stub, "_last_geometry_frame", None) is None
                else stub._last_geometry_frame.cursor_rect
            ),
        )

        # When
        dock_window_mod.DockWindow._update_input_region(stub)
        first_rect = stub._last_geometry_frame.cursor_rect
        dock_window_mod.DockWindow._update_input_region(stub)

        # Then
        assert first_rect is not None
        gdk_window.input_shape_combine_region.assert_called_once()


class TestDockWindowDrawAndHelpers:
    def test_on_draw_invokes_renderer_and_updates_region(self):
        # Given
        stub = SimpleNamespace(
            autohide=None,
            _last_autohide_state=None,
            _dock_hovered=False,
            _dnd=None,
            _hover=SimpleNamespace(hovered_item=None),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            _tooltip=MagicMock(),
            _main_axis_cursor=lambda: 12.0,
            _update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=1.0,
            cursor_y=2.0,
        )

        # When
        result = dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert result is True
        stub.renderer.draw.assert_called_once()
        stub._update_input_region.assert_called_once()

    def test_on_draw_resets_cursor_when_hidden(self):
        # Given
        hovered = DockItem(desktop_id="hovered.desktop")
        stub = SimpleNamespace(
            autohide=SimpleNamespace(
                enabled=True, state=HideState.HIDDEN, hide_offset=0.0, zoom_progress=0.0
            ),
            _dnd=None,
            _hover=SimpleNamespace(hovered_item=hovered),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            _tooltip=MagicMock(),
            _main_axis_cursor=lambda: -1.0,
            _update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=25.0,
            cursor_y=33.0,
        )

        # When
        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert stub.cursor_x == -1.0
        assert stub.cursor_y == -1.0
        assert stub._hover.hovered_item is None
        stub._tooltip.hide.assert_called_once()

    def test_on_draw_refreshes_tooltip_once_when_showing_finishes(self):
        hovered = DockItem(desktop_id="hovered.desktop")
        frame = SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100))
        stub = SimpleNamespace(
            autohide=SimpleNamespace(
                enabled=True,
                state=HideState.VISIBLE,
                hide_offset=0.0,
                zoom_progress=1.0,
            ),
            _last_autohide_state=HideState.SHOWING,
            _dock_hovered=True,
            _dnd=None,
            _hover=SimpleNamespace(hovered_item=hovered, update=MagicMock()),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            _tooltip=MagicMock(),
            _main_axis_cursor=lambda: 12.0,
            _get_geometry_frame=MagicMock(return_value=frame),
            _update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=25.0,
            cursor_y=33.0,
        )

        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        stub._hover.update.assert_called_once_with(12.0, frame=frame)
        assert stub._last_autohide_state == HideState.VISIBLE

    def test_on_motion_updates_cursor_and_hover(self):
        # Given
        widget = MagicMock()
        stub = SimpleNamespace(
            cursor_x=-1.0,
            cursor_y=-1.0,
            _dock_hovered=False,
            _get_geometry_frame=MagicMock(
                return_value=SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100))
            ),
            _update_dock_size=MagicMock(),
            _hover=SimpleNamespace(update=MagicMock()),
            _main_axis_cursor=lambda: 12.0,
            autohide=None,
        )
        stub._on_effective_enter = MethodType(
            dock_window_mod.DockWindow._on_effective_enter, stub
        )
        stub._on_effective_leave = MethodType(
            dock_window_mod.DockWindow._on_effective_leave, stub
        )
        event = SimpleNamespace(x=7.0, y=9.0)

        # When
        handled = dock_window_mod.DockWindow._on_motion(stub, widget, event)

        # Then
        assert handled is False
        assert stub.cursor_x == 7.0
        assert stub.cursor_y == 9.0
        assert stub._dock_hovered is True
        widget.queue_draw.assert_called_once()

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

    def test_main_axis_cursor_uses_position_axis(self):
        # Given
        item = DockItem(desktop_id="a.desktop")
        stub, _ = _make_stub(item=item)
        stub.cursor_x = 40.0
        stub.cursor_y = 20.0

        # Then
        assert dock_window_mod.DockWindow._main_axis_cursor(stub) == 40.0
        stub.config.pos = Position.LEFT
        assert dock_window_mod.DockWindow._main_axis_cursor(stub) == 20.0

    def test_reposition_and_queue_redraw(self):
        # Given
        drawing_area = MagicMock()
        stub = SimpleNamespace(
            _position_dock=MagicMock(),
            _set_struts=MagicMock(),
            _update_input_region=MagicMock(),
            drawing_area=drawing_area,
        )

        # When
        dock_window_mod.DockWindow.reposition(stub)
        dock_window_mod.DockWindow.queue_redraw(stub)

        # Then
        stub._position_dock.assert_called_once()
        stub._set_struts.assert_called_once()
        stub._update_input_region.assert_called_once()
        assert drawing_area.queue_draw.call_count == 2
